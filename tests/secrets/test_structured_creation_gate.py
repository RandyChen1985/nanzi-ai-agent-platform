"""新建开关不影响存量凭据；使用项目现有数据库隔离 fixture 和合成值。"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import insert, select, update

from app.core.config import settings
from app.models.credential_type import StructuredCredential
from app.models.script_tool import SysScriptTool
from app.models.secret import SecretBinding, UserSecret
from app.models.tool import SysApiTool
from app.services.config_service import _SYSTEM_CONFIGS_TABLE
from app.services.secrets import center, field_binding, mcp_profiles, payload_config, structured_payload, type_catalog
from app.services.secrets.payload_crypto import PayloadCryptoError
from app.services.secrets.policy import CONSUMER_MCP
from tests.secrets.test_structured_secret_lifecycle import credential_db, create_imap, mcp_fixture  # noqa: F401
from tests.temporary_database import init_infrastructure  # noqa: F401


async def close_creation(db):
    await db.execute(insert(_SYSTEM_CONFIGS_TABLE).values(
        key="structured_credentials_enabled", value="false", category="security", is_secret=False,
    ))
    await db.commit()


@pytest.mark.asyncio
async def test_disabled_creation_preserves_cipher_rotation_and_metadata(credential_db):
    db = credential_db
    row = await create_imap(db)
    secret_id = row.id
    await close_creation(db)
    with pytest.raises(center.SecretCredentialError) as blocked:
        await create_imap(db, key="second", account="second@example.test")
    assert blocked.value.code == "STRUCTURED_CREDENTIALS_DISABLED"
    await db.rollback()
    await center.rotate_secret(db, secret_id=secret_id, rotated_by="member-a",
                               field_changes={"password": "rotated-fixture"}, expected_value_revision=1)
    payload = await db.get(StructuredCredential, secret_id)
    cipher = (await payload_config.load_payload_config(db)).cipher()
    assert structured_payload.decrypt_fields(payload, cipher)["password"] == "rotated-fixture"
    assert payload.value_revision == 2
    assert len((await db.scalars(select(UserSecret))).all()) == 1
    edited = await center.update_secret(db, secret_id=secret_id, actor_id="member-a", name="更新合成名称")
    assert edited.name == "更新合成名称"
    with pytest.raises(center.SecretPermissionError):
        await center.rotate_secret(db, secret_id=secret_id, rotated_by="other",
                                   field_changes={"password": "other-fixture"}, expected_value_revision=2)
    with pytest.raises(center.SecretCredentialError) as conflict:
        await center.rotate_secret(db, secret_id=secret_id, rotated_by="member-a",
                                   field_changes={"password": "stale-fixture"}, expected_value_revision=1)
    assert conflict.value.code == "CREDENTIAL_REVISION_CONFLICT"


@pytest.mark.asyncio
async def test_disabled_creation_keeps_type_management_schema_and_legacy_creation(credential_db):
    from app.api.portal.endpoints import credential_types

    db = credential_db
    await close_creation(db)
    admin = {"user_id": "999", "role": "admin"}
    schema = {"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]}
    created = await credential_types.create_type(credential_types.CreateType(
        type_key="custom_gate", name="合成类型", schema=schema,
    ), admin=admin, db=db)
    assert created["data"]["enabled"] and not created["data"]["can_create"]
    assert created["data"]["creation_disabled_reason"]
    published = await credential_types.publish("custom_gate", credential_types.PublishSchema(
        schema=schema, expected_revision=1,
    ), admin=admin, db=db)
    assert published["data"]["version"] == 2
    detail = (await credential_types.read_version("custom_gate", 1, user=admin, db=db))["data"]
    assert detail["schema"]["properties"]["token"] and not detail["can_create"]
    listing = (await credential_types.list_types(include_disabled=True, user=admin, db=db))["data"]
    assert all(item["can_create"] for item in listing if item["type_key"] in {"scalar", "variable"})
    for kind in ("credential", "variable"):
        legacy = await center.create_secret(db, owner_id="101", secret_key=kind, name=kind,
                                            secret_type=kind, value="synthetic-legacy")
        assert legacy.credential_format == "legacy"
    await db.execute(update(_SYSTEM_CONFIGS_TABLE).values(value="true"))
    await db.commit()
    await type_catalog.set_enabled(db, "imap_password", enabled=False, expected_revision=1, actor_id="999")
    with pytest.raises(center.SecretCredentialError) as blocked:
        await create_imap(db)
    assert blocked.value.code == "CREDENTIAL_TYPE_DISABLED"
    await db.rollback()
    await type_catalog.set_enabled(db, "imap_password", enabled=True, expected_revision=2, actor_id="999")
    assert (await create_imap(db)).credential_format == "structured_v1"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fixed", "infer", "ask"])
async def test_disabled_creation_keeps_profiles_and_existing_mcp_injection(mcp_fixture, mode):
    db, server, tool, row, profile = mcp_fixture
    await close_creation(db)
    profile = await mcp_profiles.save_profile(
        db, tool, server, type_key="imap_password", type_version=1, selection_mode=mode,
        mappings={name: name for name in ("username", "password", "host", "port")},
        expected_revision=profile.revision, expected_profile_id=profile.profile_id, actor_id="999",
    )
    if mode == "fixed":
        db.add(SecretBinding(id="fixed", secret_id=row.id, consumer_type=CONSUMER_MCP, consumer_id=server.id,
                             slot=f"group:{tool.id}", bound_by_user_id="101"))
        await db.commit()
    ref = {"tool_id": tool.id, "revision": profile.revision, "profile_id": profile.profile_id}
    assert (await mcp_profiles.pending_candidates(db, ref, "101"))[0][0].id == row.id
    call = mcp_profiles.McpCredentialInvocation(
        tool.id, profile.revision, profile_id=profile.profile_id,
        selected_secret_id=row.id if mode == "ask" else None,
    )
    outgoing = await mcp_profiles.prepare_mcp_arguments(
        db, server, "list_mail", {"credential_account": "demo@example.test", "limit": 2}, call,
        user_info={"user_id": "101"}, trace_id=None, conversation_id=None,
    )
    assert outgoing.arguments == {"username": "fixture-user", "password": "synthetic-mail-only",
                                  "host": "imap.example.test", "port": 993, "limit": 2}
    for change in ({"status": "inactive"}, {"status": "active", "expires_at": datetime.utcnow() - timedelta(seconds=1)},
                   {"expires_at": None, "owner_id": "102"}):
        for key, value in change.items():
            setattr(row, key, value)
        await db.commit()
        assert await mcp_profiles.pending_candidates(db, ref, "101") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["script_tool", "api_tool", CONSUMER_MCP])
async def test_disabled_creation_keeps_field_binding_and_resolution(credential_db, kind):
    from app.models.mcp import McpServer

    db = credential_db
    if kind == CONSUMER_MCP:
        row = await center.create_secret(db, owner_id="101", secret_key="token", name="token",
            structured=structured_payload.StructuredInput("mcp_token", 1, "server-account", {"token": "fixture-token"}))
        consumer = McpServer(id="consumer", server_name="fixture", sse_url="https://mcp.example.test/rpc", scope="global")
        slot, field, expected = "header:Authorization", "token", "fixture-token"
    else:
        row = await create_imap(db, owner="101")
        if kind == "script_tool":
            consumer = SysScriptTool(id="consumer", name="fixture_script", display_name="fixture", code="pass",
                                     parameter_schema="{}", requirements="[]", is_active=True, source="custom")
            slot, field, expected = "env:MAIL_PORT", "port", 993
        else:
            consumer = SysApiTool(id="consumer", name="fixture_api", url_template="https://api.example.test/rpc",
                                  method="POST", headers='{"Authorization":""}', is_active=True)
            slot, field, expected = "header:Authorization", "password", "synthetic-mail-only"
    db.add(consumer)
    await db.commit()
    await close_creation(db)
    binding = await center.bind_secret(db, secret_id=row.id, consumer_type=kind, consumer_id=consumer.id,
                                       slot=slot, value_field=field, bound_by_user_id="101", actor_id="101")
    selected = await field_binding.resolve_field(db, row, binding, user_id="101", expected_target=binding.target_digest)
    assert selected.reveal() == expected
    with pytest.raises(ValueError):
        await field_binding.resolve_field(db, row, binding, user_id="102", expected_target=binding.target_digest)
    with pytest.raises(ValueError):
        await field_binding.resolve_field(db, row, binding, user_id="101", expected_target="changed-target")


@pytest.mark.asyncio
@pytest.mark.parametrize("scheduled", [False, True], ids=["chat", "scheduled-task"])
async def test_disabled_creation_native_options_keep_schema_candidates_and_reject_stale_post(mcp_fixture, monkeypatch, scheduled):
    from app.api.v1.endpoints import chat
    from app.services.ai.runtime.agentscope.confirmations import pending_agentscope_confirmations
    from app.services.secrets.credential_approval import MCP_CREDENTIAL_REF
    from app.services.secrets import task_credential_wait as waits
    from app.models.task import AgentScheduledTask
    import time

    db, server, tool, row, profile = mcp_fixture
    ref = {"tool_id": tool.id, "revision": profile.revision, "profile_id": profile.profile_id}
    pending = SimpleNamespace(request_id="request", user_id="101", snapshot=SimpleNamespace(
        conversation_id="conversation",
        runner_context={MCP_CREDENTIAL_REF: ref}, tool_call={"input": {"credential_account": "new@example.test"}},
    ), state={"user_query": "读取邮件"})
    if scheduled:
        task = AgentScheduledTask(id=17, name="合成任务", user_id=101, agent_id="agent-fixture",
                                  conversation_id="conversation", cron_expr="0 * * * *", prompt="测试", config={})
        db.add(task)
        await db.commit()
        waits.add_wait(db, task, event={"request_id": "request", "expires_at": int(time.time()) + 120},
                       trace_id="trace", conversation_id="conversation")
        ref["scheduled_task_id"] = task.id
        await db.commit()
    monkeypatch.setattr(pending_agentscope_confirmations, "peek_async", AsyncMock(return_value=pending))
    await close_creation(db)
    options = await chat.get_tool_credential_options("request", {"user_id": "101"})
    assert options["creation"]["can_create"] is False
    assert options["creation"]["creation_disabled_reason"]
    assert "password" in options["creation"]["schema"]["properties"]
    assert [item["id"] for item in options["data"]] == [row.id]
    with pytest.raises(HTTPException) as rejected:
        await chat.create_tool_credential("request", chat.ToolCredentialCreateRequest(
            account="new@example.test", fields={"host": "imap.example.test", "username": "new", "password": "fixture"},
        ), {"user_id": "101"})
    assert rejected.value.detail["code"] == "STRUCTURED_CREDENTIALS_DISABLED"
    assert len((await db.scalars(select(UserSecret))).all()) == 1
    await type_catalog.set_enabled(db, profile.type_key, enabled=False, expected_revision=1, actor_id="999")
    disabled_type_options = await chat.get_tool_credential_options("request", {"user_id": "101"})
    assert not disabled_type_options["creation"]["can_create"]
    assert disabled_type_options["creation"]["schema"] == options["creation"]["schema"]


@pytest.mark.asyncio
async def test_closed_creation_still_requires_valid_keyring_and_preserves_type_disabled_schema(mcp_fixture, monkeypatch):
    from app.api.portal.endpoints import credential_types

    db, server, tool, row, profile = mcp_fixture
    await close_creation(db)
    await type_catalog.set_enabled(db, profile.type_key, enabled=False, expected_revision=1, actor_id="999")
    detail = (await credential_types.read_version(profile.type_key, 1, user={"user_id": "101"}, db=db))["data"]
    assert not detail["enabled"] and not detail["can_create"]
    assert detail["creation_disabled_reason"] and detail["schema"]["properties"]["password"]
    monkeypatch.setattr(settings, "CREDENTIAL_PAYLOAD_KEYRING_JSON", SecretStr("{}"))
    config = await payload_config.load_payload_config(db)
    with pytest.raises(PayloadCryptoError):
        config.cipher()
    with pytest.raises(mcp_profiles.CredentialInvocationError) as invalid:
        await mcp_profiles.pending_target(db, {"tool_id": tool.id, "revision": profile.revision,
                                              "profile_id": profile.profile_id}, "101")
    assert invalid.value.code == "CREDENTIAL_ENCRYPTION_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["infer", "ask"])
async def test_disabled_creation_keeps_dynamic_tool_relations(mcp_fixture, monkeypatch, mode):
    from app.services.secrets import tool_relations

    db, server, tool, row, profile = mcp_fixture
    profile.selection_mode = mode
    await db.commit()
    permissions = SimpleNamespace(roles=[], permissions=SimpleNamespace(forbidden_tools=[]))
    monkeypatch.setattr(tool_relations.PermissionService, "get_user_permissions", AsyncMock(return_value=permissions))
    await close_creation(db)
    linked = await tool_relations.list_tool_relations(db, row, user_id="101", dept_code=None, now=datetime.utcnow())
    assert [(item.id, item.selection_mode) for item in linked["items"]] == [(tool.id, mode)]
    permissions.permissions.forbidden_tools = [tool.tool_name]
    assert not (await tool_relations.list_tool_relations(db, row, user_id="101", dept_code=None,
                                                       now=datetime.utcnow()))["items"]
    permissions.permissions.forbidden_tools = []
    monkeypatch.setattr(settings, "CREDENTIAL_PAYLOAD_KEYRING_JSON", SecretStr("{}"))
    assert not (await tool_relations.list_tool_relations(db, row, user_id="101", dept_code=None,
                                                       now=datetime.utcnow()))["items"]
