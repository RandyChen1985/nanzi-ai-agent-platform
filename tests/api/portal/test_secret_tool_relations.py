"""凭据详情的工具关系统计，以临时库中的真实配置和使用流水为准。"""

import json
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.portal.endpoints import secrets
from app.core import orm
from app.core.config import settings
from app.core.dependencies import require_api_key
from app.models.credential_type import (
    CredentialType, CredentialTypeVersion, McpCredentialProfile, McpCredentialUseGrant, StructuredCredential,
)
from app.models.mcp import McpServer, McpToolCache
from app.models.menu import AgentMenu
from app.models.permission import ResourcePermission, Role, UserRoleRelation
from app.models.secret import SecretAuditLog, SecretBinding, SecretGrant, SecretUsageLog, UserSecret
from app.models.tool import SysApiTool
from app.models.user import User
from app.services import config_service, dataset_scope_service, menu_service, permission_service
from app.services.config_service import ConfigService, _SYSTEM_CONFIGS_TABLE
from app.services.secrets import center, mcp_profiles, structured_payload
from app.services.secrets.credential_schema import BUILTIN_SCHEMAS, schema_digest
from tests.temporary_database import init_infrastructure, temporary_database  # noqa: F401

pytestmark = pytest.mark.no_infrastructure

USER_ID = "101"
PREFIX = "/api/portal/secrets"
TOOL_SCHEMA = {"type": "object", "properties": {"token": {"type": "string"}}}
ALLOWED_URL = "https://mcp.example.test/mcp"
TOKEN = "synthetic-token-only"


@pytest.fixture
async def relations_fixture(monkeypatch):
    models = (
        CredentialType, CredentialTypeVersion, StructuredCredential, McpCredentialProfile, McpCredentialUseGrant,
        UserSecret, SecretGrant, SecretBinding, SecretUsageLog, SecretAuditLog,
        McpServer, McpToolCache, SysApiTool, User, Role, ResourcePermission, AgentMenu,
    )
    async with temporary_database(*(model.__table__ for model in models), _SYSTEM_CONFIGS_TABLE) as engine:
        async with engine.begin() as connection:
            if engine.dialect.name == "mysql":
                # MySQL 的 V27 角色关系表没有外键。ORM 定义的 BIGINT→INT 外键建表会失败。
                await connection.execute(text(
                    "CREATE TABLE ai_agent_user_role_relations ("
                    "id BIGINT PRIMARY KEY AUTO_INCREMENT, user_id BIGINT NOT NULL, "
                    "role_id BIGINT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
                ))
            else:
                await connection.run_sync(UserRoleRelation.__table__.create)
            # ORM 为兼容历史库有意省略此列。全局禁用不应读到智能体级配置。
            await connection.execute(text(
                "ALTER TABLE ai_agent_resource_permissions ADD COLUMN agent_id VARCHAR(36)"
            ))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(orm, "AsyncSessionLocal", sessions)
        monkeypatch.setattr(config_service, "AsyncSessionLocal", sessions)
        monkeypatch.setattr(menu_service, "AsyncSessionLocal", sessions)
        monkeypatch.setattr(menu_service, "_MENU_TABLE_STATE", None)
        monkeypatch.setattr(menu_service, "_MENU_TABLE_PROBED_AT", 0.0)
        monkeypatch.setattr(permission_service, "_AGENT_SCOPE_COLUMN_STATE", None)
        monkeypatch.setattr(permission_service, "_AGENT_SCOPE_PROBE_FAILED_AT", 0.0)
        monkeypatch.setattr(dataset_scope_service, "_column_cache", None)
        monkeypatch.setattr(ConfigService, "_all_configs_cache", None)
        monkeypatch.setattr(ConfigService, "_all_configs_last_fetched", 0)
        monkeypatch.setattr(settings, "REDIS_ENABLE", False)
        monkeypatch.setattr(settings, "CREDENTIAL_PAYLOAD_ACTIVE_KEY_ID", "synthetic")
        monkeypatch.setattr(
            settings, "CREDENTIAL_PAYLOAD_KEYRING_JSON",
            SecretStr(json.dumps({"synthetic": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="})),
        )

        async def fixture_session():
            async with sessions() as session:
                yield session

        # 使用真实路由和请求解析，鉴权主体由本用例提供。
        api = FastAPI()
        api.include_router(secrets.router, prefix=PREFIX)
        api.dependency_overrides[require_api_key] = lambda: {
            "user_id": USER_ID, "user_name": "member", "dept_code": "dept-a", "role": "user",
        }
        api.dependency_overrides[orm.get_db_session] = fixture_session

        async with sessions() as db, AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
            db.add_all([
                User(id=101, user_name="member", dept_code="dept-a"),
                User(id=202, user_name="other", dept_code="dept-b"),
                CredentialType(type_key="mcp_token", name="MCP Token", source="builtin", latest_version=2),
            ])
            schema = BUILTIN_SCHEMAS["mcp_token"][1]
            db.add_all([
                CredentialTypeVersion(type_key="mcp_token", version=version, schema_json=schema, schema_hash=schema_digest(schema))
                for version in (1, 2)
            ])
            await db.commit()
            yield db, client


async def _credential(db, *, owner_id=USER_ID, owner_scope="user", secret_key="mcp.fixture"):
    return await center.create_secret(
        db, owner_id=owner_id, owner_scope=owner_scope, secret_key=secret_key, name="凭据名称",
        structured=structured_payload.StructuredInput("mcp_token", 1, "fixture-account", {"token": TOKEN}),
        allowed_scenarios=["mcp"], allowed_hosts=["mcp.example.test"],
    )


def _server(db, server_id, *, owner_id=None, url=ALLOWED_URL, enabled=1):
    row = McpServer(
        id=server_id, server_name=server_id, sse_url=url, enabled_status=enabled,
        scope="global" if owner_id is None else "personal", user_id=owner_id,
        auth_headers="synthetic-auth-header",
    )
    db.add(row)
    return row


async def _tool(db, server, tool_id, mode=None, *, version=1, published=True, available=True):
    tool = McpToolCache(
        id=tool_id, server_id=server.id, tool_name=f"{server.server_name}:{tool_id}",
        is_published=published, is_available=available, parameter_schema=json.dumps(TOOL_SCHEMA),
        credential_params='["token"]',
    )
    db.add(tool)
    await db.flush()
    if mode is None:
        return tool, None
    profile = await mcp_profiles.save_profile(
        db, tool, server, type_key="mcp_token", type_version=version,
        selection_mode=mode, mappings={"token": "token"}, expected_revision=0, actor_id=USER_ID,
    )
    return tool, profile


async def _invoke(db, server, tool, profile):
    await mcp_profiles.prepare_mcp_arguments(
        db, server, tool.tool_name.split(":", 1)[1], {"credential_account": "fixture-account"},
        mcp_profiles.McpCredentialInvocation(
            tool.id, profile.revision, profile_id=profile.profile_id, user_query="用 fixture-account",
        ),
        user_info={"user_id": USER_ID}, trace_id=None, conversation_id=None,
    )


def _usage(secret, consumer_id, slot, *, user_id=USER_ID, result="success", days_ago=30, kind="mcp_server"):
    return SecretUsageLog(
        id=str(uuid4()), secret_id=secret.id, secret_key_snapshot=secret.secret_key,
        user_id=user_id, owner_scope=secret.owner_scope, scene="mcp", consumer_type=kind,
        consumer_id=consumer_id, slot=slot, result=result, created_at=datetime.utcnow() - timedelta(days=days_ago),
    )


async def _relations(client, secret):
    response = await client.get(f"{PREFIX}/{secret.id}/tool-relations")
    assert response.status_code == 200, response.text
    assert TOKEN not in response.text
    assert "fixture-account" not in response.text
    assert "synthetic-auth-header" not in response.text
    return response.json()["data"]


async def test_tool_relations_unite_type_binding_and_usage_by_tool_id(relations_fixture):
    db, client = relations_fixture
    credential = await _credential(db)
    same_key = await _credential(db, owner_id="202")
    server = _server(db, "mail-server", owner_id=101)
    infer_tool, infer_profile = await _tool(db, server, "infer", "infer")
    await _tool(db, server, "ask", "ask")
    await _tool(db, server, "fixed", "fixed")
    await _tool(db, server, "fixed-without-binding", "fixed")
    await _tool(db, server, "history")
    await _tool(db, server, "denied")
    await _tool(db, server, "other-credential")
    await _invoke(db, server, infer_tool, infer_profile)
    await _invoke(db, server, infer_tool, infer_profile)
    db.add_all([
        SecretBinding(
            id="own-fixed-binding", secret_id=credential.id, bound_by_user_id=USER_ID,
            consumer_type="mcp_server", consumer_id=server.id, slot="group:fixed",
        ),
        _usage(credential, server.id, "arg:infer:token"),
        _usage(credential, server.id, "group:fixed"),
        _usage(credential, server.id, "group:history"),
        _usage(credential, server.id, "group:denied", result="denied_by_host", days_ago=0),
        _usage(same_key, server.id, "group:other-credential"),
    ])
    await db.commit()

    data = await _relations(client, credential)
    assert len(data["items"]) == 4
    items = {row["id"]: row for row in data["items"]}
    assert {
        item_id: (row["kind"], row["selection_mode"], row["used"], [binding["id"] for binding in row["bindings"]])
        for item_id, row in items.items()
    } == {
        "infer": ("mcp_tool", "infer", True, []),
        "ask": ("mcp_tool", "ask", False, []),
        "fixed": ("mcp_tool", None, True, ["own-fixed-binding"]),
        "history": ("mcp_tool", None, True, []),
    }
    assert items["infer"]["name"] == "mail-server:infer"
    assert items["infer"]["server_name"] == "mail-server"
    assert not data["unresolved_usage"]
    listed = (await client.get(PREFIX)).json()["data"]["items"]
    assert len(listed) == 1
    assert (listed[0]["binding_count"], listed[0]["usage_7d"], listed[0]["failed_7d"]) == (1, 3, 1)

    credential.status = "disabled"
    await db.commit()
    after_disable = {row["id"]: row for row in (await _relations(client, credential))["items"]}
    assert set(after_disable) == {"infer", "fixed", "history"}
    assert after_disable["infer"]["used"]
    assert after_disable["infer"]["selection_mode"] is None


async def test_dynamic_candidates_require_current_visible_tool_and_allowed_target(relations_fixture):
    db, client = relations_fixture
    credential = await _credential(db)
    server = _server(db, "public")
    await _tool(db, server, "eligible", "infer")
    await _tool(db, server, "wrong-version", "infer", version=2)
    await _tool(db, server, "unpublished", "infer", published=False)
    await _tool(db, server, "removed-remote", "infer", available=False)
    await _tool(db, _server(db, "not-mine", owner_id=202), "private-tool", "infer")
    await _tool(db, _server(db, "disabled-service", enabled=0), "disabled-tool", "infer")
    await _tool(db, _server(db, "disallowed-host", url="https://elsewhere.example.test/mcp"), "wrong-host", "infer")
    await _tool(db, server, "globally-forbidden", "infer")
    await _tool(db, server, "blocked-in-one-agent", "infer")
    stale_tool, _ = await _tool(db, server, "stale", "infer")
    stale_tool.parameter_schema = "{}"
    db.add_all([
        ResourcePermission(id=1, user_id=101, resource_type="forbidden_tool", resource_id="public:globally-forbidden"),
        ResourcePermission(id=2, user_id=101, resource_type="forbidden_tool", resource_id="public:blocked-in-one-agent"),
    ])
    await db.flush()
    await db.execute(text("UPDATE ai_agent_resource_permissions SET agent_id = 'one-agent' WHERE id = 2"))
    await db.commit()

    data = await _relations(client, credential)
    assert [row["id"] for row in data["items"]] == ["blocked-in-one-agent", "eligible"]

    credential.status = "disabled"
    await db.commit()
    assert (await _relations(client, credential))["items"] == []
    credential.status = "active"
    credential.expires_at = datetime.utcnow() - timedelta(seconds=1)
    await db.commit()
    assert (await _relations(client, credential))["items"] == []


async def test_system_candidates_use_center_sharing_and_ignore_historical_tool_grants(relations_fixture):
    db, client = relations_fixture
    credential = await _credential(db, owner_id="", owner_scope="system")
    public = _server(db, "public")
    infer_tool, profile = await _tool(db, public, "later-revoked", "infer")
    await _tool(db, public, "no-tool-grant", "infer")
    await _tool(db, public, "expired-tool-grant", "infer")
    await _tool(db, public, "wrong-dept-grant", "infer")
    await _tool(db, public, "by-dept", "ask")
    private = _server(db, "other-private-service", owner_id=202)
    private_tool, _ = await _tool(db, private, "other-private-tool", "infer")
    tool_grant = McpCredentialUseGrant(
        tool_id=infer_tool.id, secret_id=credential.id, grantee_type="user", grantee_id=USER_ID,
    )
    db.add_all([
        tool_grant,
        McpCredentialUseGrant(
            tool_id="expired-tool-grant", secret_id=credential.id, grantee_type="all", grantee_id="",
            expires_at=datetime.utcnow() - timedelta(seconds=1),
        ),
        McpCredentialUseGrant(
            tool_id="wrong-dept-grant", secret_id=credential.id, grantee_type="dept", grantee_id="dept-b",
        ),
        McpCredentialUseGrant(
            tool_id="by-dept", secret_id=credential.id, grantee_type="dept", grantee_id="dept-a",
        ),
        SecretBinding(
            id="other-private-binding", secret_id=credential.id, bound_by_user_id="202",
            consumer_type="mcp_server", consumer_id=private.id, slot=f"group:{private_tool.id}",
        ),
        _usage(credential, private.id, f"group:{private_tool.id}", user_id="202", days_ago=0),
    ])
    await db.commit()
    assert (await client.get(f"{PREFIX}/{credential.id}/tool-relations")).status_code == 404

    secret_grant = SecretGrant(
        id="shared-with-dept", secret_id=credential.id, grantee_type="dept", grantee_id="dept-a",
    )
    db.add(secret_grant)
    await db.commit()
    data = await _relations(client, credential)
    expected_tools = {"later-revoked", "by-dept", "no-tool-grant", "expired-tool-grant", "wrong-dept-grant"}
    assert {row["id"] for row in data["items"]} == expected_tools
    assert not data["unresolved_usage"]
    assert not data["unresolved_bindings"]
    listed = (await client.get(PREFIX)).json()["data"]["items"]
    assert len(listed) == 1
    assert (listed[0]["binding_count"], listed[0]["usage_7d"], listed[0]["failed_7d"]) == (0, 1, 0)

    await _invoke(db, public, infer_tool, profile)
    await db.delete(tool_grant)
    db.add(_usage(credential, private.id, f"group:{private_tool.id}"))
    await db.commit()

    data = await _relations(client, credential)
    items = {row["id"]: row for row in data["items"]}
    assert set(items) == expected_tools
    assert items["later-revoked"]["used"]
    assert items["later-revoked"]["selection_mode"] == "infer"
    assert items["later-revoked"]["bindings"] == []
    assert data["unresolved_usage"]
    assert "other-private" not in json.dumps(data)

    infer_tool.is_published = False
    await db.commit()
    data = await _relations(client, credential)
    assert {row["id"] for row in data["items"]} == expected_tools - {"later-revoked"}
    assert "later-revoked" not in json.dumps(data)

    await db.delete(secret_grant)
    await db.commit()
    assert (await client.get(f"{PREFIX}/{credential.id}/tool-relations")).status_code == 404


async def test_connection_scope_and_deleted_targets_do_not_invent_tool_identity(relations_fixture):
    db, client = relations_fixture
    credential = await _credential(db)
    private_credential = await _credential(db, owner_id="202")
    server = _server(db, "connection")
    await _tool(db, server, "another-tool")
    db.add(SysApiTool(id=server.id, name="api-tool", url_template=ALLOWED_URL))
    db.add_all([
        SecretBinding(
            id="old-own-binding", secret_id=credential.id, bound_by_user_id=USER_ID,
            consumer_type="mcp_server", consumer_id="removed-service", slot="arg:old-private-tool:token",
        ),
        SecretBinding(
            id="other-binding", secret_id=credential.id, bound_by_user_id="202",
            consumer_type="mcp_server", consumer_id=server.id, slot="header:X-Other",
        ),
        SecretBinding(
            id="connection-fixed", secret_id=credential.id, bound_by_user_id=USER_ID,
            consumer_type="mcp_server", consumer_id=server.id, slot="header:Authorization",
        ),
        _usage(credential, server.id, "header:Authorization"),
        _usage(credential, server.id, "header:X-Token"),
        _usage(credential, server.id, "group:deleted-tool"),
        _usage(credential, server.id, "body:token", kind="api_tool", result="success_fallback"),
        _usage(credential, server.id, "header:X-Token", kind="api_tool"),
    ])
    await db.commit()

    assert (await client.get(f"{PREFIX}/{private_credential.id}/tool-relations")).status_code == 404
    data = await _relations(client, credential)
    assert {(row["id"], row["kind"], row["used"]) for row in data["items"]} == {
        ("connection", "mcp_server", True), ("connection", "api_tool", True),
    }
    assert data["unresolved_usage"]
    assert data["unresolved_bindings"] == [{"id": "old-own-binding", "slot": None, "bound_by_me": True}]
    assert "old-private-tool" not in json.dumps(data)
    assert "removed-service" not in json.dumps(data)
    assert "other-binding" not in json.dumps(data)

    assert (await client.delete(f"{PREFIX}/bindings/other-binding")).status_code == 404
    assert (await client.delete(f"{PREFIX}/bindings/old-own-binding")).status_code == 200
    assert (await _relations(client, credential))["unresolved_bindings"] == []
