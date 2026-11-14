"""参数设置的保存、重开和执行使用同一结果可见性快照，无外部服务。"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.portal.endpoints import mcp, mcp_credential_profiles
from app.models.credential_type import McpCredentialProfile
from app.models.mcp import McpServer, McpToolCache
from app.services.secrets import mcp_profiles
from app.services.secrets.mcp_model_result import project_model_result

pytestmark = pytest.mark.no_infrastructure
OWNER = {"user_id": 7, "role": "admin"}


@pytest.fixture
def config(monkeypatch):
    tool = McpToolCache(
        id="test-tool", server_id="test-server", tool_name="fixture:health",
        credential_params=None, is_available=True, is_published=True,
        parameter_schema=json.dumps({
            "type": "object", "properties": {"ping": {"type": "string"}}, "required": ["ping"],
        }),
    )
    server = McpServer(
        id="test-server", server_name="fixture", sse_url="https://mcp.example.test/rpc",
        scope="global", credential_mode="static", user_assertion_enabled=False, enabled_status=1,
    )

    class MemorySession:
        profile = None
        commits = 0

        async def get(self, model, _id, **kwargs):
            return {McpServer: server, McpToolCache: tool, McpCredentialProfile: self.profile}.get(model)

        async def scalar(self, stmt):
            entity = stmt.column_descriptions[0]["entity"]
            if entity is McpToolCache:
                return server.id if stmt.column_descriptions[0]["name"] == "server_id" else tool
            return server

        async def execute(self, stmt):
            if stmt.is_update:
                values = stmt.compile().params
                tool.credential_params = values["credential_params"]
                return None
            entity = stmt.column_descriptions[0]["entity"]
            return SimpleNamespace(scalar_one_or_none=lambda: tool if entity is McpToolCache else server)

        def add(self, row):
            pass  # 审计落库不属于此测试；配置写入与读取仍使用实际对象。

        async def commit(self):
            self.commits += 1

    monkeypatch.setattr(mcp, "_clear_runtime_tool_cache", lambda: None)
    monkeypatch.setattr(mcp_profiles, "apply_mcp_arg_bindings", AsyncMock(return_value={"ping": "fixture-secret"}))
    return MemorySession(), tool, server


async def read_policy(db, tool):
    result = await mcp_credential_profiles.get_profile(tool.id, user=OWNER, db=db)
    return result["data"]["model_result_policy"]


async def save(db, tool, policy, fields):
    return await mcp.set_tool_credential_params(
        tool.id, mcp.ToolCredentialParamsRequest(
            params=["ping"], model_result_fields=fields,
            expected_revision=policy["revision"], expected_target=policy["target"],
        ), db=db, user=OWNER,
    )


async def prepare(db, tool, server):
    return await mcp_profiles.prepare_mcp_arguments(
        db, server, "health", {}, mcp_profiles.McpCredentialInvocation(tool.id, None),
        user_info=OWNER, trace_id=None, conversation_id=None,
    )


async def test_root_saved_with_new_credential_param_survives_reopen_and_execution(config):
    db, tool, server = config
    response = await save(db, tool, await read_policy(db, tool), ["$"])
    reopened = await read_policy(db, tool)
    assert reopened["model_result_fields"] == ["$"]
    assert reopened["stale"] is False
    assert response["model_result_policy"] == reopened
    assert db.commits == 1
    prepared = await prepare(db, tool, server)
    assert prepared.arguments == {"ping": "fixture-secret"}
    assert prepared.result_policy_stale is False
    result = project_model_result('{"status":"ok"}', prepared.model_result_fields,
                                  protected_values=prepared.protected_values)
    assert result == {"status": "success", "result_visibility": "projected", "data": {"status": "ok"}}


@pytest.mark.parametrize("fields", [[], ["$["], None])
async def test_empty_or_invalid_edit_replaces_previous_visible_policy(config, fields):
    db, tool, _ = config
    await save(db, tool, await read_policy(db, tool), ["$"])
    response = await save(db, tool, await read_policy(db, tool), fields)
    assert (await read_policy(db, tool))["model_result_fields"] == []
    assert response["model_result_policy"]["model_result_fields"] == []
    if fields:
        assert response["warnings"][0]["code"] == "MCP_RESULT_FIELDS_INVALID"


@pytest.mark.parametrize("change", ["target", "revision", "profile"])
async def test_conflicting_save_changes_neither_params_nor_policy(config, change):
    db, tool, server = config
    original = await read_policy(db, tool)
    if change == "target":
        server.sse_url = "https://mcp.example.test/changed"
    elif change == "revision":
        tool.model_result_policy = {"revision": 1, "fields": [], "target": original["target"]}
    else:
        db.profile = SimpleNamespace()
    policy_before = tool.model_result_policy
    with pytest.raises(HTTPException) as caught:
        await save(db, tool, original, ["$"])
    assert caught.value.status_code == 409
    assert tool.credential_params is None
    assert tool.model_result_policy == policy_before
    assert db.commits == 0


async def test_equivalent_legacy_param_save_does_not_invalidate_result_policy(config):
    db, tool, server = config
    tool.credential_params = '[ "ping" ]'
    await mcp_profiles.save_tool_result_policy(
        db, tool, server, paths=["$"], expected_revision=0,
        expected_target=mcp_profiles.tool_result_target(tool, server), actor_id="7",
    )
    await mcp.set_tool_credential_params(
        tool.id, mcp.ToolCredentialParamsRequest(params=["ping"]), db=db, user=OWNER,
    )
    assert (await read_policy(db, tool))["stale"] is False


@pytest.mark.parametrize("change", ["endpoint", "schema", "params"])
async def test_stale_policy_keeps_editable_paths_but_execution_remains_restricted(config, change):
    db, tool, server = config
    await mcp_profiles.save_tool_result_policy(
        db, tool, server, paths=["$"], expected_revision=0,
        expected_target=mcp_profiles.tool_result_target(tool, server), actor_id="7",
    )
    if change == "endpoint":
        server.sse_url = "https://mcp.example.test/changed"
    elif change == "schema":
        tool.parameter_schema = '{"type":"object","properties":{}}'
    else:
        tool.credential_params = '["ping"]'
    reopened = await read_policy(db, tool)
    assert reopened["stale"] is True
    assert reopened["model_result_fields"] == ["$"]
    prepared = await prepare(db, tool, server)
    assert prepared.result_policy_stale is True
    assert prepared.model_result_fields == ()


async def test_root_selection_returns_the_injected_value_when_selected(config):
    db, tool, server = config
    await save(db, tool, await read_policy(db, tool), ["$"])
    prepared = await prepare(db, tool, server)
    result = project_model_result('{"status":"ok","pong":"fixture-secret"}', prepared.model_result_fields,
                                  protected_values=prepared.protected_values)
    assert result == {
        "status": "success", "result_visibility": "projected",
        "data": {"status": "ok", "pong": "fixture-secret"},
    }


@pytest.mark.parametrize("kwargs", [
    {}, {"expected_revision": 0}, {"expected_target": "0" * 64},
])
async def test_result_edit_requires_complete_snapshot_before_updating_params(config, kwargs):
    db, tool, _ = config
    with pytest.raises(HTTPException) as caught:
        await mcp.set_tool_credential_params(
            tool.id, mcp.ToolCredentialParamsRequest(params=["ping"], model_result_fields=["$"], **kwargs),
            db=db, user=OWNER,
        )
    assert caught.value.status_code == 422
    assert tool.credential_params is None
    assert tool.model_result_policy is None
    assert db.commits == 0


async def test_param_only_change_does_not_reapprove_existing_result_policy(config):
    db, tool, _ = config
    await save(db, tool, await read_policy(db, tool), ["$"])
    await mcp.set_tool_credential_params(
        tool.id, mcp.ToolCredentialParamsRequest(params=[]), db=db, user=OWNER,
    )
    assert (await read_policy(db, tool))["stale"] is True


async def test_nonowner_cannot_save_personal_tool_result_policy(config):
    db, tool, server = config
    policy = await read_policy(db, tool)
    server.scope, server.user_id = "personal", 8
    with pytest.raises(HTTPException) as caught:
        await mcp.set_tool_credential_params(
            tool.id, mcp.ToolCredentialParamsRequest(
                params=["ping"], model_result_fields=["$"],
                expected_revision=policy["revision"], expected_target=policy["target"],
            ), db=db, user={"user_id": 7, "role": "user"},
        )
    assert caught.value.status_code == 403
    assert db.commits == 0
