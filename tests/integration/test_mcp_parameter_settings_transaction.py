"""逐参数设置在真实 Session 中提交，不连接业务库或 Redis。"""

import json
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.portal.endpoints import mcp
from app.models.credential_type import McpCredentialProfile
from app.models.mcp import McpServer, McpToolCache
from app.models.secret import SecretAuditLog, SecretBinding
from app.services.secrets import mcp_profiles
from tests.temporary_database import init_infrastructure, temporary_database  # noqa: F401

OWNER = {"user_id": 42, "role": "user"}
ADMIN = {"user_id": 1, "role": "admin"}
RULE = {"key_source": "arg:region"}


@pytest.fixture
async def session_factory(monkeypatch):
    monkeypatch.setattr(mcp, "_clear_runtime_tool_cache", AsyncMock())
    async with temporary_database(*(
        model.__table__ for model in (McpServer, McpToolCache, McpCredentialProfile, SecretBinding, SecretAuditLog)
    )) as engine:
        session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with session_factory.begin() as db:
            server = McpServer(
                id="s1", server_name="fixture", sse_url="https://example.test/rpc",
                user_id=42, scope="personal",
            )
            tool = McpToolCache(
                id="t1", server_id="s1", tool_name="fixture:find", is_published=True,
                parameter_schema=json.dumps({"type": "object", "properties": {
                    name: {"type": "string"} for name in ("auth", "secondary", "region")
                }}),
            )
            db.add_all([server, tool])
            await db.flush()
            snapshot = mcp_profiles.tool_result_target(tool, server)
        yield session_factory, snapshot


async def _save(session_factory, snapshot, changes, *, user=OWNER, revision=0, params=None, fail_commit=False):
    request = mcp.ToolCredentialParamsRequest(
        params=["auth"] if params is None else params,
        model_result_fields=["$"], expected_revision=revision, expected_target=snapshot,
        binding_changes=changes,
    )
    async with session_factory.begin() as db:
        if fail_commit:
            @event.listens_for(db.sync_session, "before_commit", once=True)
            def fail(session):
                session.flush()
                assert session.scalars(select(SecretBinding)).one().slot == "arg:find:auth"
                raise RuntimeError("fixture policy failure")
        return await mcp.set_tool_credential_params("t1", request, db, user)


def _upsert(parameter="auth", **extra):
    return {"parameter": parameter, "operation": "upsert_rule", "rule": RULE, **extra}


async def test_parameter_sources_and_rule_add_delete_commit_together(session_factory):
    factory, snapshot = session_factory
    result = await _save(factory, snapshot, [_upsert()])
    async with factory() as db:
        tool = await db.get(McpToolCache, "t1")
        assert json.loads(tool.credential_params) == ["auth"]
        assert tool.model_result_policy["fields"] == ["$"]
        rule = (await db.scalars(select(SecretBinding))).one()
        assert (rule.slot, rule.bound_by_user_id, rule.strategy) == ("arg:find:auth", "42", "rule")
        assert json.loads(rule.rule_json)["key_source"] == "arg:region"
        assert {a.action for a in await db.scalars(select(SecretAuditLog))} == {"bind_rule", "mcp_result_fields_save"}
    snapshot = result["model_result_policy"]["target"]
    await _save(factory, snapshot, [
        {"parameter": "auth", "operation": "delete_rule"},
    ], revision=1, params=[])
    async with factory() as db:
        assert not (await db.scalars(select(SecretBinding))).all()
        assert (await db.get(McpToolCache, "t1")).credential_params is None


async def test_policy_conflict_does_not_write_rule(session_factory):
    factory, snapshot = session_factory
    with pytest.raises(HTTPException) as error:
        await _save(factory, snapshot, [_upsert()], revision=1)
    assert error.value.status_code == 409
    async with factory() as db:
        assert not (await db.scalars(select(SecretBinding))).all()


async def test_current_profile_conflicts_even_when_save_transaction_has_an_older_snapshot(session_factory):
    factory, snapshot = session_factory
    async with factory.begin() as old_reader:
        # MySQL 的 REPEATABLE READ 会保留这一普通查询的快照。
        assert await old_reader.get(McpCredentialProfile, "t1") is None
        async with factory.begin() as other:
            other.add(McpCredentialProfile(
                tool_id="t1", profile_id="p1", server_id="s1", tool_name="fixture:find",
                type_key="api_key", type_version=1, selection_mode="fixed",
                mappings={"auth": "api_key"}, schema_digest="fixture", endpoint_digest="fixture",
                revision=1, model_result_fields=[],
            ))
        with pytest.raises(HTTPException) as error:
            await mcp.set_tool_credential_params("t1", mcp.ToolCredentialParamsRequest(
                params=["auth"], model_result_fields=["$"], expected_revision=0,
                expected_target=snapshot, binding_changes=[_upsert()],
            ), old_reader, OWNER)
        assert error.value.status_code == 409
        assert error.value.detail["code"] == "CREDENTIAL_PROFILE_CHANGED"
    async with factory() as db:
        assert not (await db.scalars(select(SecretBinding))).all()


async def test_database_error_rolls_back_staged_rule_and_policy(session_factory):
    factory, snapshot = session_factory
    # 在实际数据库事务已暂存规则之后，模拟持久化失败。
    with pytest.raises(RuntimeError, match="fixture policy failure"):
        await _save(factory, snapshot, [_upsert()], fail_commit=True)
    async with factory() as db:
        assert not (await db.scalars(select(SecretBinding))).all()
        assert not (await db.scalars(select(SecretAuditLog))).all()
        assert (await db.get(McpToolCache, "t1")).model_result_policy is None


async def test_personal_manager_cannot_set_global_rule(session_factory):
    factory, snapshot = session_factory
    with pytest.raises(HTTPException) as error:
        await _save(factory, snapshot, [_upsert(is_global=True)])
    assert error.value.status_code == 403
    async with factory() as db:
        assert not (await db.scalars(select(SecretBinding))).all()


async def test_global_rule_is_stored_in_existing_global_scope(session_factory):
    factory, snapshot = session_factory
    await _save(factory, snapshot, [_upsert(is_global=True)], user=ADMIN)
    async with factory() as db:
        row = (await db.scalars(select(SecretBinding))).one()
        assert row.bound_by_user_id == ""


async def test_marking_a_rule_input_as_credential_requires_updating_the_rule(session_factory):
    factory, snapshot = session_factory
    result = await _save(factory, snapshot, [_upsert()])
    target = result["model_result_policy"]["target"]
    with pytest.raises(HTTPException) as error:
        await _save(factory, target, [], revision=1, params=["auth", "region"])
    assert error.value.status_code == 400
    assert "规则" in error.value.detail
    async with factory() as db:
        assert json.loads((await db.get(McpToolCache, "t1")).credential_params) == ["auth"]
        assert len((await db.scalars(select(SecretBinding))).all()) == 1
    result = await _save(factory, target, [
        {"parameter": "auth", "operation": "delete_rule"},
    ], revision=1, params=["region"])
    assert result["credential_params"] == ["region"]
