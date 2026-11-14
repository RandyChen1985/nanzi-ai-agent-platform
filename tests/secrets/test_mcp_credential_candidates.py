"""按当前工具发现已授权的结构化候选，复用平台隔离数据库及合成数据。"""
import json
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import event

from app.core import orm
from app.models.credential_type import McpCredentialUseGrant, StructuredCredential
from app.models.secret import SecretGrant, UserSecret
from app.services.ai.tools import session_status
from app.services.config_service import ConfigService
from app.services.secrets import mcp_profiles
from app.services.secrets.credential_approval import MCP_CREDENTIAL_REF
from app.services.secrets.credential_schema import account_key
from tests.secrets.test_structured_secret_lifecycle import credential_db  # noqa: F401
from tests.temporary_database import init_infrastructure  # noqa: F401

pytestmark = pytest.mark.no_infrastructure
TOOL_NAME = "mcp_0123456789abcdef0123"
REF = {"tool_id": "fixture-tool", "profile_id": "fixture-profile", "revision": 1, "selection_mode": "infer"}


@pytest_asyncio.fixture
async def candidate_context(credential_db, monkeypatch):
    session = credential_db
    server = SimpleNamespace(sse_url="https://mcp.example.test/rpc")
    profile = SimpleNamespace(
        type_key="fixture_mail", type_version=2, tool_id=REF["tool_id"], profile_id=REF["profile_id"],
        revision=REF["revision"], selection_mode=REF["selection_mode"],
    )
    context = SimpleNamespace(user_id=101, runtime_tool_capabilities=[{
        "name": TOOL_NAME, "source_type": "mcp", MCP_CREDENTIAL_REF: REF,
    }])

    @asynccontextmanager
    async def sessions():
        yield session

    monkeypatch.setattr(orm, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(ConfigService, "get_all_from_db", AsyncMock(return_value={
        "secret_policy_allow_catalog": {"value": "true"},
    }))
    monkeypatch.setattr(session_status, "get_current_agent_context", lambda: context)
    target = AsyncMock(return_value=(profile, server, SimpleNamespace(dept_code="fixture-dept")))
    monkeypatch.setattr(mcp_profiles, "pending_target", target)
    monkeypatch.setattr(mcp_profiles.type_catalog, "get_type", AsyncMock(return_value=SimpleNamespace(name="测试邮箱")))
    monkeypatch.setattr(mcp_profiles.SecretResolver, "_platform_flags", AsyncMock(return_value=(False, True, 3)))

    yield session, context, target


def add_candidate(session, key, name, account, *, scope="user", owner="101", version=2,
                  status="active", host="mcp.example.test", expires_at=None):
    session.add_all([
        UserSecret(
            id=key, owner_scope=scope, owner_id=owner, secret_key=key, name=name, status=status,
            allowed_hosts=[host], allowed_scenarios=["mcp"], credential_format="structured_v1",
            expires_at=expires_at,
        ),
        StructuredCredential(
            secret_id=key, owner_scope=scope, owner_id=owner, account=account, account_digest=account_key(account)[1],
            type_key="fixture_mail", type_version=version, key_id="fixture-key", payload_encrypted="never-model-payload",
            public_projection={"username": "never-model-login"}, secret_fields_set=["never-model-password"], extra_schema={},
        ),
    ])


def grant(session, secret_id, *, expires_at=None):
    session.add(SecretGrant(id=f"grant-{secret_id}", secret_id=secret_id, grantee_type="dept",
                            grantee_id="fixture-dept", expires_at=expires_at))


async def invoke(**arguments):
    from app.services.ai.tools.mcp_credential_candidates import list_mcp_credential_candidates

    return json.loads(await list_mcp_credential_candidates.ainvoke({"tool_name": TOOL_NAME, **arguments}))


@contextmanager
def candidate_reads(session):
    """记录真实数据库查询各自装载的凭据元数据，不替换查询实现。"""
    batches = []
    sync_session = session.sync_session

    def query_started(_state):
        batches.append([])

    def row_loaded(_session, instance):
        if isinstance(instance, UserSecret):
            batches[-1].append(instance.id)

    event.listen(sync_session, "do_orm_execute", query_started)
    event.listen(sync_session, "loaded_as_persistent", row_loaded)
    try:
        yield batches
    finally:
        event.remove(sync_session, "do_orm_execute", query_started)
        event.remove(sync_session, "loaded_as_persistent", row_loaded)


async def query_candidates(target, **options):
    profile, server, user = target.return_value
    async with orm.AsyncSessionLocal() as db:
        return await mcp_profiles.list_candidates(
            db, profile, server, user_id="101", dept_code=user.dept_code, **options,
        )


@pytest.mark.asyncio
async def test_discovery_lists_only_metadata_for_eligible_personal_and_shared_candidates(candidate_context):
    session, _, target = candidate_context
    add_candidate(session, "own-private-id", "Alice primary", "alice@example.test")
    add_candidate(session, "other-id", "Other owner", "other@example.test", owner="102")
    add_candidate(session, "old-id", "Wrong version", "old@example.test", version=1)
    add_candidate(session, "disabled-id", "Disabled", "disabled@example.test", status="disabled")
    past = datetime.utcnow() - timedelta(days=1)
    add_candidate(session, "expired-id", "Expired", "expired@example.test", expires_at=past)
    add_candidate(session, "host-id", "Wrong host", "host@example.test", host="other.example.test")
    add_candidate(session, "shared-private-id", "Shared support", "alice-support@example.test", scope="system", owner="")
    grant(session, "shared-private-id")
    add_candidate(session, "ungranted-id", "No grant", "ungranted@example.test", scope="system", owner="")
    add_candidate(session, "legacy-tool-id", "Legacy tool grant", "legacy-tool@example.test", scope="system", owner="")
    grant(session, "legacy-tool-id")
    # 旧逐工具授权既不能收窄已共享凭据，也不能授予未共享凭据使用权。
    session.add_all([
        McpCredentialUseGrant(tool_id="other-tool", secret_id="legacy-tool-id", grantee_type="user", grantee_id="101"),
        McpCredentialUseGrant(tool_id=REF["tool_id"], secret_id="ungranted-id", grantee_type="user", grantee_id="101"),
    ])
    add_candidate(session, "expired-grant-id", "Expired grant", "expired-grant@example.test", scope="system", owner="")
    grant(session, "expired-grant-id", expires_at=past)
    await session.commit()

    result = await invoke(keyword="alice")
    assert result["status"] == "success"
    assert result["credential_type"] == {"name": "测试邮箱", "type_key": "fixture_mail", "version": 2}
    # 名称资格与安全投影不依赖数据库的地区排序规则。
    assert sorted(result["items"], key=lambda item: item["account"]) == [
        {"name": "Shared support", "account": "alice-support@example.test", "owner_scope": "system"},
        {"name": "Alice primary", "account": "alice@example.test", "owner_scope": "user"},
    ]
    assert result["truncated"] is False
    assert await invoke(account="alice@example.test") == {
        **result, "items": [{"name": "Alice primary", "account": "alice@example.test", "owner_scope": "user"}],
    }
    assert sorted((await invoke())["items"], key=lambda item: item["account"]) == [
        {"name": "Shared support", "account": "alice-support@example.test", "owner_scope": "system"},
        {"name": "Alice primary", "account": "alice@example.test", "owner_scope": "user"},
        {"name": "Legacy tool grant", "account": "legacy-tool@example.test", "owner_scope": "system"},
    ]
    assert all(value not in repr(result) for value in ("private-id", "never-model-", "fixture-key"))
    assert target.await_args.args[1:] == (REF, "101")


@pytest.mark.asyncio
async def test_discovery_distinguishes_empty_unmounted_and_query_failed(candidate_context):
    _, context, target = candidate_context
    result = await invoke(account="missing@example.test")
    assert result["status"] == "success" and result["items"] == [] and result["truncated"] is False

    target.side_effect = RuntimeError("synthetic-database-internal-error")
    result = await invoke()
    assert result["status"] == "unavailable" and result["code"] == "CREDENTIAL_QUERY_UNAVAILABLE"
    assert "items" not in result and "synthetic" not in repr(result)

    context.runtime_tool_capabilities = []
    target.reset_mock()
    result = await invoke()
    assert result["status"] == "unavailable" and result["code"] == "CREDENTIAL_TOOL_NOT_AVAILABLE"
    target.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_discovery_separates_unavailable_from_missing(candidate_context):
    session, _, _ = candidate_context
    add_candidate(session, "inactive-id", "Stopped", "inactive@example.test", status="disabled")
    add_candidate(session, "expired-id", "Expired", "expired@example.test", expires_at=datetime.utcnow() - timedelta(days=1))
    add_candidate(session, "private-id", "Other owner", "private@example.test", owner="102")
    await session.commit()

    for account in ("inactive@example.test", "expired@example.test"):
        result = await invoke(account=account)
        assert result["status"] == "unavailable"
        assert result["code"] == "CREDENTIAL_SELECTION_INVALID"
        assert account not in repr(result)
        assert await mcp_profiles.credential_selection_reason(REF, {"credential_account": account}, "101") == "credential_selection_invalid"

    assert (await invoke(account="private@example.test"))["items"] == []
    assert await mcp_profiles.credential_selection_reason(
        REF, {"credential_account": "private@example.test"}, "101",
    ) == "credential_missing"


@pytest.mark.asyncio
async def test_runtime_capability_uses_internal_profile_ref_without_publishing_it(candidate_context):
    _, context, _ = candidate_context
    context.runtime_tool_capabilities = [session_status.runtime_tool_capability_from_spec(SimpleNamespace(
        name=TOOL_NAME, source_type="mcp", metadata={MCP_CREDENTIAL_REF: REF}, description="Mail",
    ))]
    assert session_status.mounted_mcp_credential_ref(TOOL_NAME) == ("101", REF)
    payload = json.loads(await session_status.get_runtime_capabilities.ainvoke({}))
    assert payload["tools"][0]["name"] == TOOL_NAME
    assert MCP_CREDENTIAL_REF not in payload["tools"][0]
    assert REF["profile_id"] not in repr(payload)


@pytest.mark.asyncio
async def test_candidate_truncation_counts_only_eligible_matches(candidate_context):
    session, _, _ = candidate_context
    for index in range(24):
        add_candidate(session, f"wrong-{index}", "Wrong host", f"a{index:02d}@example.test", host="other.example.test")
        add_candidate(session, f"own-{index}", "Available", f"b{index:02d}@example.test")
    await session.commit()

    result = await invoke()
    assert result["truncated"] is True
    assert len(result["items"]) == 20
    assert [item["account"] for item in result["items"]] == [f"b{index:02d}@example.test" for index in range(20)]


@pytest.mark.asyncio
async def test_limited_candidate_query_stops_loading_after_enough_matches(candidate_context):
    session, _, target = candidate_context
    for index in range(80):
        add_candidate(session, f"own-{index:02d}", "Available", f"a{index:02d}@example.test")
    await session.commit()

    with candidate_reads(session) as batches:
        rows = await query_candidates(target, limit=3)

    assert [row[0].id for row in rows] == ["own-00", "own-01", "own-02"]
    assert len(batches) == 1  # 第一批已经足够，不能继续查询后续候选。
    assert len(batches[0]) < 80  # 返回上限必须同时避免装载未使用的全量记录。


@pytest.mark.asyncio
async def test_limited_candidate_query_crosses_filtered_pages(candidate_context):
    session, _, target = candidate_context
    for index in range(100):
        add_candidate(
            session, f"own-{index:02d}", "Available", f"a{index:02d}@example.test",
            host="mcp.example.test" if index in {40, 42, 44} or index >= 50 else "other.example.test",
        )
    await session.commit()

    with candidate_reads(session) as batches:
        rows = await query_candidates(target, limit=3)

    assert [row[0].id for row in rows] == ["own-40", "own-42", "own-44"]
    assert len(batches) > 1
    assert max(map(len, batches)) < 40
    assert len([key for batch in batches for key in batch]) < 100


@pytest.mark.asyncio
async def test_limited_candidate_query_batches_long_runs_of_ineligible_rows(candidate_context):
    session, _, target = candidate_context
    for index in range(200):
        add_candidate(
            session, f"wrong-{index:03d}", "Wrong host", f"a{index:03d}@example.test", host="other.example.test",
        )
    await session.commit()

    with candidate_reads(session) as batches:
        rows = await query_candidates(target, limit=1)

    assert rows == []
    assert len(batches) <= 10  # 单条存在性检查不能退化为每条不适用凭据一次查询。
    assert max(map(len, batches)) < 200


@pytest.mark.asyncio
async def test_limited_candidate_query_keeps_same_account_across_pages(candidate_context):
    session, _, target = candidate_context
    add_candidate(session, "same-00", "Wrong host", "same@example.test", host="other.example.test")
    add_candidate(session, "same-01", "Shared", "same@example.test", scope="system", owner="")
    grant(session, "same-01")
    await session.commit()

    with candidate_reads(session) as batches:
        rows = await query_candidates(target, limit=1)

    assert [row[0].id for row in rows] == ["same-01"]
    assert batches == [["same-00"], ["same-01"]]


@pytest.mark.asyncio
async def test_unlimited_candidate_query_keeps_complete_single_query(candidate_context):
    session, _, target = candidate_context
    for index in range(60):
        add_candidate(
            session, f"own-{index:02d}", "Available", f"a{index:02d}@example.test",
            host="mcp.example.test" if index % 2 else "other.example.test",
        )
    await session.commit()

    with candidate_reads(session) as batches:
        rows = await query_candidates(target)

    assert [row[0].id for row in rows] == [f"own-{index:02d}" for index in range(1, 60, 2)]
    assert len(batches) == 1 and len(batches[0]) == 60


@pytest.mark.asyncio
async def test_limited_visibility_query_still_includes_unusable_candidates(candidate_context):
    session, _, target = candidate_context
    add_candidate(session, "inactive", "Inactive", "a@example.test", status="disabled", host="other.example.test")
    add_candidate(session, "active", "Active", "b@example.test")
    await session.commit()

    rows = await query_candidates(target, only_usable=False, limit=1)

    assert [row[0].id for row in rows] == ["inactive"]


@pytest.mark.asyncio
async def test_catalog_configuration_lookup_failure_releases_no_metadata(candidate_context, monkeypatch):
    _, _, target = candidate_context
    monkeypatch.setattr(
        ConfigService, "get_all_from_db", AsyncMock(side_effect=RuntimeError("synthetic-internal-detail")),
    )
    result = await invoke()
    assert result["code"] == "CREDENTIAL_QUERY_UNAVAILABLE"
    assert "synthetic" not in repr(result)
    target.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fixed", "rule", "mixed", "parameter_bindings", "ask", "none"])
async def test_mounted_non_inferred_tools_do_not_report_unavailable(candidate_context, monkeypatch, mode):
    _, context, target = candidate_context
    context.runtime_tool_capabilities[0] = {
        "name": TOOL_NAME, "source_type": "mcp",
        "mcp_credential": {"mode": mode, "candidate_lookup_required": False},
        **({MCP_CREDENTIAL_REF: {**REF, "selection_mode": mode}} if mode in {"fixed", "ask"} else {}),
    }
    # 非推导模式不依赖账号目录，目录关闭也不能把它误报成工具不可用。
    monkeypatch.setattr(ConfigService, "get_all_from_db", AsyncMock(return_value={
        "secret_policy_allow_catalog": {"value": "false"},
    }))

    result = await invoke(account="unused@example.test")

    assert result["status"] == "success"
    assert result["candidate_lookup_required"] is False
    assert result["credential_mode"] == mode
    assert result["code"] == "CREDENTIAL_CANDIDATES_NOT_REQUIRED"
    assert "直接调用" in result["note"]
    assert "items" not in result  # 无需查询不等于候选为零，也不能据此判断凭据缺失。
    assert "unused@example.test" not in repr(result)
    target.assert_not_awaited()


@pytest.mark.asyncio
async def test_mounted_tool_without_mode_is_distinct_from_an_unmounted_tool(candidate_context):
    _, context, target = candidate_context
    context.runtime_tool_capabilities[0].pop(MCP_CREDENTIAL_REF)

    result = await invoke()

    assert result["status"] == "success"
    assert result["credential_mode"] == "unknown"
    assert result["candidate_lookup_required"] is False
    assert "未配置凭据" not in result["note"]
    target.assert_not_awaited()


@pytest.mark.asyncio
async def test_inferred_tool_with_missing_internal_reference_reports_configuration_issue(candidate_context):
    _, context, target = candidate_context
    capability = context.runtime_tool_capabilities[0]
    capability.pop(MCP_CREDENTIAL_REF)
    capability["mcp_credential"] = {"mode": "infer", "candidate_lookup_required": True}

    result = await invoke()

    assert result["status"] == "unavailable"
    assert result["code"] == "CREDENTIAL_PROFILE_CHANGED"
    target.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_capability_publishes_mode_without_binding_fields_or_references(candidate_context):
    _, context, _ = candidate_context
    context.runtime_tool_capabilities = [session_status.runtime_tool_capability_from_spec(SimpleNamespace(
        name=TOOL_NAME, source_type="mcp", description="服务健康检查", metadata={
            MCP_CREDENTIAL_REF: {**REF, "selection_mode": "fixed"},
            "mcp_credential": {
                "mode": "fixed", "candidate_lookup_required": False,
                "secret_id": "never-publish-binding", "mappings": {"ping": "never-publish-field"},
                "credential_type": {"name": "测试类型", "type_key": "fixture", "version": 1,
                                    "payload": "never-publish-payload"},
            },
        },
    ))]

    payload = json.loads(await session_status.get_runtime_capabilities.ainvoke({}))

    assert payload["tools"][0]["mcp_credential"] == {
        "mode": "fixed", "candidate_lookup_required": False,
        "credential_type": {"name": "测试类型", "type_key": "fixture", "version": 1},
    }
    assert "never-publish" not in repr(payload)
    assert REF["profile_id"] not in repr(payload)
    assert "ping" not in repr(payload)
