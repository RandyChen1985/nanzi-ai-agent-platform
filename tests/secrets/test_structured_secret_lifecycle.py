import base64
import json
import os
import time
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import func, insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.core import orm
from app.models.credential_type import (
    CredentialType, CredentialTypeVersion, McpCredentialProfile, McpCredentialUseGrant, StructuredCredential, TaskCredentialWait,
)
from app.models.mcp import McpServer, McpToolCache
from app.models.user import User
from app.models.secret import SecretAuditLog, SecretBinding, SecretGrant, SecretUsageLog, SecretUserPref, UserSecret
from app.models.task import AgentScheduledTask
from app.models.tool import SysApiTool
from app.models.script_tool import SysScriptTool
from app.services.config_service import _SYSTEM_CONFIGS_TABLE
from app.services.secrets import center, mcp_profiles, payload_config, resolver, structured_payload, type_catalog
from app.services.secrets.credential_approval import (
    CredentialApproval, approved_arguments_digest, claim_approval, current_credential_approval,
)
from app.services.secrets.resolver import SecretResolver
from tests.temporary_database import init_infrastructure, temporary_database  # noqa: F401
from app.services.secrets.credential_schema import BUILTIN_SCHEMAS, schema_digest


pytestmark = pytest.mark.no_infrastructure


@pytest_asyncio.fixture
async def credential_db(monkeypatch):
    models = (
        CredentialType, CredentialTypeVersion, StructuredCredential,
        UserSecret, SecretAuditLog, SecretBinding, SecretGrant, SecretUsageLog,
        McpServer, McpToolCache, McpCredentialProfile, McpCredentialUseGrant, User,
        SecretUserPref, SysApiTool, SysScriptTool, AgentScheduledTask, TaskCredentialWait,
    )
    async with temporary_database(*(model.__table__ for model in models), _SYSTEM_CONFIGS_TABLE) as engine:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        # 审计和解析器可以单独开会话，仍需指向本用例的临时库。
        monkeypatch.setattr(orm, "AsyncSessionLocal", session_factory)
        monkeypatch.setattr(resolver, "AsyncSessionLocal", session_factory)
        monkeypatch.setattr(
            settings, "CREDENTIAL_PAYLOAD_KEYRING_JSON",
            SecretStr(json.dumps({"synthetic": base64.urlsafe_b64encode(b"A" * 32).decode()})),
        )
        monkeypatch.setattr(settings, "CREDENTIAL_PAYLOAD_ACTIVE_KEY_ID", "synthetic")
        async with session_factory() as db:
            for key, (name, schema) in BUILTIN_SCHEMAS.items():
                db.add(CredentialType(type_key=key, name=name, source="builtin"))
                db.add(CredentialTypeVersion(type_key=key, version=1, schema_json=schema, schema_hash=schema_digest(schema)))
            await db.commit()
            yield db


async def create_imap(db, *, owner="member-a", account="demo@example.test", key="mail", scope="user"):
    return await center.create_secret(
        db, owner_id=owner, owner_scope=scope, secret_key=key, name="测试邮箱",
        allowed_scenarios=["mcp"], allowed_hosts=["mcp.example.test"],
        structured=structured_payload.StructuredInput(
            "imap_password", 1, account,
            {"host": "imap.example.test", "username": "fixture-user", "password": "synthetic-mail-only"},
        ),
    )


@pytest.mark.asyncio
async def test_credential_db_uses_a_disposable_platform_database(credential_db):
    url = credential_db.bind.url
    configured = make_url(os.environ["TEST_DATABASE_URL"])
    assert url.get_backend_name() == configured.get_backend_name()
    assert (url.host, url.port) == (configured.host, configured.port)
    current_database = func.database() if url.get_backend_name() == "mysql" else func.current_database()
    assert await credential_db.scalar(select(current_database)) == url.database
    assert url.database.startswith("nanzi_test_")
    assert url.database != configured.database


@pytest.mark.asyncio
async def test_temporary_database_is_removed_when_the_test_raises(credential_db):
    if credential_db.bind.url.get_backend_name() == "mysql":
        lookup = text("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = :name")
    else:
        lookup = text("SELECT COUNT(*) FROM pg_database WHERE datname = :name")

    with pytest.raises(RuntimeError, match="synthetic test failure"):
        async with temporary_database(CredentialType.__table__) as engine:
            params = {"name": engine.url.database}
            assert await credential_db.scalar(lookup, params) == 1
            raise RuntimeError("synthetic test failure")

    await credential_db.rollback()
    assert await credential_db.scalar(lookup, params) == 0


@pytest.mark.asyncio
async def test_member_can_browse_and_create_enabled_credential_types(credential_db):
    from fastapi import HTTPException
    from app.api.portal.endpoints import credential_types

    db = credential_db
    member = {"role": "user", "user_id": 101}
    admin = {"role": "admin", "user_id": 999}
    await type_catalog.set_enabled(db, "database", enabled=False, expected_revision=1, actor_id="999")

    response = await credential_types.list_types(include_disabled=False, user=member, db=db)
    types = {item["type_key"]: item for item in response["data"]}
    assert set(types) == set(BUILTIN_SCHEMAS) - {"database"}
    assert types["scalar"]["can_create"]
    assert types["imap_password"]["can_create"]
    detail = await credential_types.read_version("imap_password", 1, user=member, db=db)
    assert detail["data"]["schema"]["properties"]["password"]["writeOnly"]
    assert detail["data"]["can_create"]
    row = await create_imap(db, owner="101")
    encrypted = await db.get(StructuredCredential, row.id)
    assert encrypted.key_id == "synthetic"
    assert "synthetic-mail-only" not in encrypted.payload_encrypted
    cipher = (await payload_config.load_payload_config(db)).cipher()
    assert structured_payload.decrypt_fields(encrypted, cipher)["password"] == "synthetic-mail-only"

    with pytest.raises(HTTPException) as denied:
        await credential_types.list_types(include_disabled=True, user=member, db=db)
    assert denied.value.status_code == 403
    response = await credential_types.list_types(include_disabled=True, user=admin, db=db)
    disabled = next(item for item in response["data"] if item["type_key"] == "database")
    assert not disabled["can_create"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("keyring", "active_id"), [('{}', ''), ('{"invalid":"test"}', 'invalid')])
async def test_missing_or_invalid_keyring_never_writes_credential(credential_db, monkeypatch, keyring, active_id):
    await credential_db.execute(insert(_SYSTEM_CONFIGS_TABLE).values(
        key="structured_credentials_enabled", value="true", category="security", is_secret=False,
    ))
    monkeypatch.setattr(settings, "CREDENTIAL_PAYLOAD_KEYRING_JSON", SecretStr(keyring))
    monkeypatch.setattr(settings, "CREDENTIAL_PAYLOAD_ACTIVE_KEY_ID", active_id)
    with pytest.raises(center.SecretValidationError) as rejected:
        await create_imap(credential_db)
    assert rejected.value.code == "CREDENTIAL_ENCRYPTION_UNAVAILABLE"
    assert "synthetic-mail-only" not in str(rejected.value)
    await credential_db.rollback()
    assert (await credential_db.scalars(select(UserSecret))).all() == []
    assert (await credential_db.scalars(select(StructuredCredential))).all() == []


@pytest.mark.asyncio
async def test_disabled_type_rejects_legacy_and_structured_creation(credential_db):
    db = credential_db
    for type_key in ("scalar", "variable", "imap_password"):
        await type_catalog.set_enabled(db, type_key, enabled=False, expected_revision=1, actor_id="admin")
    for legacy_type in ("credential", "variable"):
        with pytest.raises(center.SecretValidationError) as rejected:
            await center.create_secret(db, owner_id="member-a", secret_key="old", name="", value="synthetic", secret_type=legacy_type)
        assert rejected.value.code == "CREDENTIAL_TYPE_DISABLED"
        await db.rollback()
    for scope in ("user", "system"):
        with pytest.raises(center.SecretValidationError) as rejected:
            await create_imap(db, scope=scope)
        assert rejected.value.code == "CREDENTIAL_TYPE_DISABLED"
        await db.rollback()
    assert (await db.scalars(select(UserSecret))).all() == []
    await type_catalog.set_enabled(db, "imap_password", enabled=True, expected_revision=2, actor_id="admin")
    assert (await create_imap(db)).credential_format == "structured_v1"


@pytest.mark.asyncio
async def test_rotation_and_detail_keep_values_internal(credential_db):
    db = credential_db
    row = await create_imap(db)
    secret_id = row.id
    await type_catalog.set_enabled(db, "imap_password", enabled=False, expected_revision=1, actor_id="admin")
    assert not row.value_encrypted and not row.value_preview
    payload = await db.get(StructuredCredential, secret_id)
    summary = (await structured_payload.describe_payloads(db, [secret_id]))[secret_id]
    serialized = json.dumps(summary)
    for protected in ("synthetic-mail-only", "fixture-user", "imap.example.test", payload.payload_encrypted):
        assert protected not in serialized
    with pytest.raises(center.SecretPermissionError):
        await center.rotate_secret(db, secret_id=secret_id, rotated_by="member-b",
                                   field_changes={"password": "replacement-fixture"}, expected_value_revision=1)
    await db.rollback()
    await center.rotate_secret(db, secret_id=secret_id, rotated_by="member-a",
                               field_changes={"password": "replacement-fixture"}, expected_value_revision=1)
    payload = await db.get(StructuredCredential, secret_id)
    cipher = (await payload_config.load_payload_config(db)).cipher()
    assert structured_payload.decrypt_fields(payload, cipher)["password"] == "replacement-fixture"
    assert structured_payload.decrypt_fields(payload, cipher)["username"] == "fixture-user"
    with pytest.raises(center.SecretValidationError) as stale:
        await center.rotate_secret(db, secret_id=secret_id, rotated_by="member-a",
                                   field_changes={"password": "stale-fixture"}, expected_value_revision=1)
    assert stale.value.code == "CREDENTIAL_REVISION_CONFLICT"
    await db.rollback()
    logs = (await db.scalars(select(SecretAuditLog.detail))).all()
    assert "replacement-fixture" not in str(logs)
    assert "synthetic-mail-only" not in str(logs)


@pytest.mark.asyncio
async def test_type_revisions_are_immutable(credential_db):
    db = credential_db
    schema = {"type": "object", "properties": {"password": {"type": "string"}}, "required": ["password"]}
    row, v1 = await type_catalog.create_type(db, "custom_demo", "测试类型", schema, enabled=False, actor_id="admin")
    edited = {**schema, "properties": {**schema["properties"], "account_label": {"type": "string"}}}
    row, v2 = await type_catalog.publish_version(db, row.type_key, edited, expected_revision=1, actor_id="admin")
    assert not row.enabled and (v1.version, v2.version) == (1, 2)
    assert "account_label" not in v1.schema_json["properties"]
    assert v1.schema_json["properties"]["password"]["writeOnly"]
    with pytest.raises(type_catalog.CredentialTypeError) as stale:
        await type_catalog.set_enabled(db, "custom_demo", enabled=True, expected_revision=1, actor_id="admin")
    assert stale.value.code == "CREDENTIAL_REVISION_CONFLICT"



@pytest_asyncio.fixture
async def mcp_fixture(credential_db, monkeypatch):
    db = credential_db
    monkeypatch.setattr(SecretResolver, "_platform_flags", staticmethod(_fixture_platform_flags))
    db.add_all([User(id=101, user_name="fixture-a"), User(id=102, user_name="fixture-b")])
    server = McpServer(
        id="server-fixture", server_name="fixture-mail", sse_url="https://mcp.example.test/rpc",
        enabled_status=1, scope="global",
    )
    tool = McpToolCache(
        id="tool-fixture", server_id=server.id, tool_name="fixture-mail:list_mail",
        is_published=True, is_available=True,
        parameter_schema=json.dumps({
            "type": "object",
            "properties": {name: {"type": kind} for name, kind in
                           {"username": "string", "password": "string", "host": "string",
                            "port": "integer", "limit": "integer"}.items()},
            "required": ["username", "password", "host", "port"],
        }),
    )
    tool.server = server
    db.add_all([server, tool])
    await db.commit()
    row = await create_imap(db, owner="101")
    profile = await mcp_profiles.save_profile(
        db, tool, server, type_key="imap_password", type_version=1, selection_mode="infer",
        mappings={name: name for name in ("username", "password", "host", "port")},
        expected_revision=0, actor_id="999",
    )
    return db, server, tool, row, profile


async def _fixture_platform_flags():
    return False, True, 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("composite", "projected", "direct", "bare_json"), [
        (False, False, True, False), (True, False, True, False),
        (False, True, True, False), (False, True, False, False),
        (False, True, True, True),
    ],
    ids=["scalar-fields", "nested-object", "projected-direct", "projected-session", "projected-bare-json"],
)
async def test_mcp_injects_authorized_credentials_and_only_returns_approved_data(
    mcp_fixture, monkeypatch, composite, projected, direct, bare_json,
):
    import asyncio

    import httpx

    from app.core import orm
    from app.api.portal.endpoints import mcp_credential_profiles
    from app.services.ai.runtime.agentscope import tools as runtime_tools
    from app.services.ai.tools import mcp_client, mcp_factory
    from app.services.secrets import injection

    db, server, tool, _, profile = mcp_fixture
    observed = []
    audits = []
    expected = {
        "username": "fixture-user", "password": "synthetic-mail-only", "host": "imap.example.test",
        "port": 993, "limit": 2,
    }
    if composite:
        nested = {
            "type": "object", "properties": {"service_token": {"type": "string"}}, "required": ["service_token"],
        }
        imap = BUILTIN_SCHEMAS["imap_password"][1]
        schema = {**imap, "properties": {**imap["properties"], "connection": nested}}
        row, _ = await type_catalog.create_type(db, "composite_mail", "测试复合类型", schema, enabled=True, actor_id="999")
        remote = json.loads(tool.parameter_schema)
        remote["properties"]["connection"] = nested
        tool.parameter_schema = json.dumps(remote)
        await db.commit()
        profile = await mcp_profiles.save_profile(
            db, tool, server, type_key=row.type_key, type_version=1, selection_mode="infer",
            mappings={**profile.mappings, "connection": "connection"},
            expected_revision=profile.revision, expected_profile_id=profile.profile_id, actor_id="999",
        )
        expected["connection"] = {"service_token": "synthetic-service-only"}
        await center.create_secret(
            db, owner_id="101", secret_key="composite_mail", name="测试复合凭据",
            allowed_scenarios=["mcp"], allowed_hosts=["mcp.example.test"],
            structured=structured_payload.StructuredInput(
                row.type_key, 1, "demo@example.test", {key: expected[key] for key in profile.mappings},
            ),
        )
        assert "connection" not in mcp_profiles.model_schema(remote, profile)["properties"]
    if projected:
        admin = {"user_id": 999, "role": "admin"}
        fields = ["$.messages[*][1]", "$.content", "$.result"] if bare_json else [
            "messages[].subject", "messages[].body",
        ]
        response = await mcp_credential_profiles.put_profile(
            tool.id, mcp_credential_profiles.ProfileRequest(
                type_key=profile.type_key, type_version=profile.type_version, selection_mode=profile.selection_mode,
                mappings=profile.mappings, expected_revision=profile.revision, expected_profile_id=profile.profile_id,
                model_result_fields=fields,
            ), user=admin, db=db,
        )
        assert response["data"]["model_result_fields"] == fields
        response = await mcp_credential_profiles.get_profile(tool.id, user=admin, db=db)
        assert response["data"]["profile"]["model_result_fields"] == fields

    @asynccontextmanager
    async def fixture_session():
        yield db

    def response_for(name, arguments):
        observed.append((name, dict(arguments)))
        if bare_json:
            return {
                "status": "ok", "account": "fixture",
                "content": "Mailbox response", "result": "found",
                "messages": [["09:12", "Fixture report", "Quarterly totals"]],
            }
        return {
            "messages": [{"subject": "Fixture report", "body": "Quarterly totals", "debug": arguments}],
            "echo": arguments,
        }

    class Transport:
        is_direct_http = direct
        auth_headers = {}
        sse_url = server.sse_url
        server_id = server.id
        mcp_session_id = None

        def __init__(self):
            self._active_requests_changed = asyncio.Event()

        def next_rpc_id(self):
            return 1

        def get_http_client(self):
            return self

        async def post(self, url, *, json, **kwargs):
            assert json["method"] == "tools/call"
            payload = response_for(json["params"]["name"], json["params"]["arguments"])
            if not bare_json:
                payload = {"jsonrpc": "2.0", "id": json["id"], "result": {"structuredContent": payload}}
            return httpx.Response(200, json=payload)

        async def close(self):
            pass

        @property
        def session(self):
            return self

        async def call_tool(self, name, arguments, **kwargs):
            assert "progress_callback" not in kwargs
            return SimpleNamespace(structuredContent=response_for(name, arguments), content=[])

    async def load_server(_id):
        return server

    async def connect(_id, **kwargs):
        assert kwargs["server_snapshot"] is server
        return Transport()

    async def initialized(_session):
        pass

    async def audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(mcp_client, "AsyncSessionLocal", fixture_session)
    monkeypatch.setattr(orm, "AsyncSessionLocal", fixture_session)
    monkeypatch.setattr(injection, "_bindings_unavailable_until", 0.0)
    monkeypatch.setattr(mcp_client.McpClientService, "_load_server", load_server)
    monkeypatch.setattr(mcp_client.McpClientService, "get_session", connect)
    monkeypatch.setattr(mcp_client.McpClientService, "_ensure_direct_http_initialized", initialized)
    monkeypatch.setattr(mcp_client, "record_outbound_audit_log", audit)
    call = mcp_profiles.McpCredentialInvocation(tool.id, profile.revision, user_query="请用 demo@example.test 查邮件", profile_id=profile.profile_id)

    async def invoke(user_id, arguments):
        return await mcp_client.McpClientService.call_remote_tool(
            server.id, "list_mail", arguments, user_info={"user_id": user_id},
            credential_call=call, for_model=True,
        )

    args = {"credential_account": "demo@example.test", "password": "model-fixture", "limit": 2}
    if composite:
        args["connection"] = {"service_token": "model-fixture"}
    with pytest.raises(mcp_profiles.CredentialInvocationError) as denied:
        await invoke("102", args)
    assert denied.value.code == "CREDENTIAL_MISSING"
    assert not observed

    if projected:
        # 走实际的运行时工具包装，确保投影没有在生成器层再次被丢弃。
        monkeypatch.setattr(mcp_factory, "current_mcp_agent_identity", lambda: ({"user_id": "101"}, {}))
        monkeypatch.setattr(mcp_factory, "get_current_agent_context", lambda: SimpleNamespace(
            trace_id=None, conversation_id=None, credential_user_query=call.user_query,
        ))
        wrapped = mcp_factory.McpToolFactory.create_tool(tool, credential_profile=profile, verify_profile=True)
        spec = runtime_tools.runtime_tool_spec_from_legacy_tool(wrapped, "mcp")
        runtime_tool = runtime_tools.AgentScopeRuntimeTool(spec)
        stream = await runtime_tool(credential_account="demo@example.test", limit=2)
        chunks = [part async for part in stream]
        assert len(chunks) == 1 and chunks[0].is_last
        result = json.loads(chunks[0].content[0].text)
        assert result["status"] == "success" and result["result_visibility"] == "projected"
        expected_data = (
            {"messages": [[None, "Fixture report"]], "content": "Mailbox response", "result": "found"} if bare_json
            else {"messages": [{"subject": "Fixture report", "body": "Quarterly totals"}]}
        )
        assert result["data"] == expected_data
        assert "synthetic-mail-only" not in chunks[0].content[0].text
    else:
        result = await invoke("101", args)
        assert result == {"status": "success", "result_visibility": "restricted"}
    assert observed == [("list_mail", expected)]
    await asyncio.sleep(0)
    assert audits and audits[0]["arguments"] == {} and audits[0]["tool_output"] is None

    server.sse_url = "https://changed.example.test/rpc"
    with pytest.raises(mcp_profiles.CredentialInvocationError) as stale:
        await invoke("101", args)
    assert stale.value.code == "CREDENTIAL_PROFILE_CHANGED"
    assert len(observed) == 1

    server.sse_url = "https://mcp.example.test/rpc"
    await db.execute(insert(_SYSTEM_CONFIGS_TABLE).values(
        key="structured_credentials_enabled", value="false", category="security", is_secret=False,
    ))
    after_close = await invoke("101", args)
    assert after_close["status"] == "success"
    assert observed == [("list_mail", expected), ("list_mail", expected)]


@pytest.mark.asyncio
async def test_pending_card_creates_only_for_owner_and_pins_account_and_target(mcp_fixture, monkeypatch):
    from fastapi import HTTPException
    from app.api.v1.endpoints import chat
    from app.core import orm
    from app.services.ai.runtime.agentscope.confirmations import pending_agentscope_confirmations
    from app.services.secrets.credential_approval import MCP_CREDENTIAL_REF

    db, server, tool, _, profile = mcp_fixture
    ref = {"tool_id": tool.id, "revision": profile.revision, "profile_id": profile.profile_id}
    account = "new-fixture@example.test"
    pending = SimpleNamespace(
        user_id="101",
        snapshot=SimpleNamespace(
            runner_context={MCP_CREDENTIAL_REF: ref},
            tool_call={"input": {"credential_account": account}},
        ),
        state={"user_query": f"读取 {account} 的邮件"},
    )

    @asynccontextmanager
    async def session():
        yield db

    async def peek(*args, **kwargs):
        return pending

    monkeypatch.setattr(orm, "AsyncSessionLocal", session)
    monkeypatch.setattr(pending_agentscope_confirmations, "peek_async", peek)
    actor = {"user_id": 101}
    data = {"host": "imap.example.test", "username": "fixture-login", "password": "synthetic-only"}
    payload = chat.ToolCredentialCreateRequest(account=account, fields=data)
    options = await chat.get_tool_credential_options("req-fixture", actor)
    assert options["creation"]["account_hint"] == account
    assert options["creation"]["host"] == "mcp.example.test"

    with pytest.raises(HTTPException) as wrong_owner:
        await chat.create_tool_credential("req-fixture", payload, {"user_id": 102})
    assert wrong_owner.value.status_code == 404
    with pytest.raises(HTTPException) as wrong_account:
        await chat.create_tool_credential("req-fixture", payload.model_copy(update={"account": "other"}), actor)
    assert wrong_account.value.status_code == 409

    response = await chat.create_tool_credential("req-fixture", payload, actor)
    row = await db.get(UserSecret, response["data"]["id"])
    assert row.owner_id == "101"
    assert row.allowed_hosts == ["mcp.example.test"] and row.allowed_scenarios == ["mcp"]
    assert "synthetic-only" not in repr(response)

    await type_catalog.set_enabled(db, profile.type_key, enabled=False, expected_revision=1, actor_id="admin")
    options = await chat.get_tool_credential_options("req-fixture", actor)
    assert options["creation"] is None and row.id in {item["id"] for item in options["data"]}
    with pytest.raises(HTTPException) as disabled:
        await chat.create_tool_credential("req-fixture", payload, actor)
    assert disabled.value.status_code == 409
    assert disabled.value.detail["code"] == "CREDENTIAL_SELECTION_REQUIRED"


@pytest.mark.asyncio
async def test_approved_call_is_consumed_once_even_when_approval_recreated(mcp_fixture):
    db, server, tool, row, profile = mcp_fixture
    profile.selection_mode = "ask"
    await db.commit()
    args = {"limit": 2}
    fields = dict(
        request_id="fixture-request-id", user_id="101", conversation_id="fixture-conversation",
        tool_id=tool.id, profile_id=profile.profile_id, profile_revision=profile.revision,
        arguments_digest=approved_arguments_digest(args), secret_id=row.id, expires_at=time.time() + 60,
    )
    call = mcp_profiles.McpCredentialInvocation(tool.id, profile.revision, profile_id=profile.profile_id)
    scope = current_credential_approval.set(CredentialApproval(**fields))
    try:
        with pytest.raises(mcp_profiles.CredentialInvocationError):
            await mcp_profiles.prepare_mcp_arguments(
                db, server, "list_mail", {"limit": 7}, call,
                user_info={"user_id": "101"}, trace_id=None, conversation_id="fixture-conversation",
            )
        prepared = await mcp_profiles.prepare_mcp_arguments(
            db, server, "list_mail", args, call,
            user_info={"user_id": "101"}, trace_id=None, conversation_id="fixture-conversation",
        )
        assert prepared.protect_payload and prepared.arguments["password"] == "synthetic-mail-only"
        assert not await claim_approval(db, CredentialApproval(**fields))
    finally:
        current_credential_approval.reset(scope)


@pytest.mark.asyncio
async def test_api_field_binding_keeps_types_and_rechecks_destination(credential_db, monkeypatch):
    from app.services.secrets.field_binding import target_digest

    db = credential_db
    monkeypatch.setattr(SecretResolver, "_platform_flags", staticmethod(_fixture_platform_flags))

    async def ignore_audit(**kwargs):
        pass

    @asynccontextmanager
    async def fixture_session():
        yield db

    monkeypatch.setattr(center, "_audit", ignore_audit)
    monkeypatch.setattr(resolver, "AsyncSessionLocal", fixture_session)
    tool = SysApiTool(
        id="fixture-api", name="fixture-api", method="POST", url_template="https://api.example.test/fixed",
        parameter_schema=json.dumps({
            "type": "object", "properties": {},
            "x-nanzi-system-params": [{"name": "port", "type": "integer", "source": "credential"}],
        }),
    )
    db.add(tool)
    await db.commit()
    secret = await center.create_secret(
        db, owner_id="101", secret_key="test.mail", name="测试账号",
        allowed_scenarios=["api_tool"], allowed_hosts=["api.example.test"],
        structured=structured_payload.StructuredInput("imap_password", 1, "alias-fixture", {
            "username": "test-user", "password": "synthetic-pass", "host": "mail.example.test",
        }),
    )
    await center.bind_secret(
        db, secret_id=secret.id, consumer_type="api_tool", consumer_id=tool.id,
        slot="body:port", bound_by_user_id="101", value_field="port", actor_id="101",
    )
    params = dict(user_id="101", scene="api_tool", consumer_type="api_tool", consumer_id=tool.id,
                  slot="body:port", target_host="api.example.test")
    result = await resolver.secret_resolver.resolve(**params, target_binding_digest=target_digest("api_tool", tool, "body:port"))
    assert result.ok and result.value.reveal() == 993
    from app.services.secrets.field_binding import validate_binding

    with pytest.raises(ValueError, match="仅支持请求头和请求体"):
        await validate_binding(db, secret, "api_tool", tool.id, "query:port", "port", "101")
    tool.url_template = "https://api.example.test/other"
    denied = await resolver.secret_resolver.resolve(**params, target_binding_digest=target_digest("api_tool", tool, "body:port"))
    assert not denied.ok and denied.value is None

    await db.execute(insert(_SYSTEM_CONFIGS_TABLE).values(
        key="structured_credentials_enabled", value="false", category="security", is_secret=False,
    ))
    tool.url_template = "https://api.example.test/fixed"
    resolved = await resolver.secret_resolver.resolve(**params, target_binding_digest=target_digest("api_tool", tool, "body:port"))
    assert resolved.ok and resolved.value.reveal() == 993


@pytest.mark.asyncio
async def test_task_lease_recovers_unclaimed_and_terminates_claimed(credential_db):
    from app.services.secrets import task_credential_wait as waits
    from app.services.secrets.credential_approval import approval_claim_id

    db = credential_db
    task = AgentScheduledTask(
        id=17, name="合成任务", user_id=101, agent_id="agent-fixture", conversation_id="fixture-task",
        cron_expr="0 * * * *", prompt="测试", config={},
    )
    db.add(task)
    await db.commit()
    row = waits.add_wait(db, task, event={"request_id": "request-fixture", "expires_at": int(time.time()) + 120},
                         trace_id="trace-fixture", conversation_id="same-conversation")
    row.status = "resuming"
    row.resume_lease_until = datetime.utcnow() - timedelta(seconds=1)
    await db.commit()
    await waits.expire_waits(db, task.id)
    assert row.status == "pending"
    assert (await waits.latest_wait(db, task.id, active_only=True)).request_id == row.request_id
    row.status = "resuming"
    row.resume_lease_until = datetime.utcnow() - timedelta(seconds=1)
    db.add(SecretAuditLog(id=approval_claim_id(row.request_id), action="mcp_approval_claim"))
    await db.commit()
    await waits.expire_waits(db, task.id)
    assert row.status == "failed"
    assert await waits.latest_wait(db, task.id, active_only=True) is None


@pytest.mark.parametrize("bad_fields", [
    ["$.messages[*][1]", "$.messages["],
    ["x" * 257],
    [f"$.field{index}" for index in range(21)],
    ["$.account", None],
    "$.messages[*][1]",
])
async def test_invalid_result_fields_replace_a_previously_visible_configuration(mcp_fixture, bad_fields):
    from app.api.portal.endpoints import mcp_credential_profiles

    db, _, tool, _, profile = mcp_fixture
    admin = {"user_id": 999, "role": "admin"}

    async def save(fields):
        return await mcp_credential_profiles.put_profile(
            tool.id, mcp_credential_profiles.ProfileRequest(
                type_key=profile.type_key, type_version=profile.type_version, selection_mode=profile.selection_mode,
                mappings=profile.mappings, expected_revision=profile.revision, expected_profile_id=profile.profile_id,
                model_result_fields=fields,
            ), user=admin, db=db,
        )

    good = await save(["$.messages[*][1]"])
    assert good["data"]["model_result_fields"] == ["$.messages[*][1]"]
    invalid = await save(bad_fields)
    assert invalid["data"]["model_result_fields"] == []
    assert invalid["warnings"][0]["code"] == "MCP_RESULT_FIELDS_INVALID"
    saved = await mcp_credential_profiles.get_profile(tool.id, user=admin, db=db)
    assert saved["data"]["profile"]["model_result_fields"] == []

    await save(["$.messages[*][1]"])
    cleared = await save([])
    assert cleared["data"]["model_result_fields"] == []
    assert "warnings" not in cleared


async def test_header_authenticated_tool_projects_saved_fields_without_a_credential_profile(
    mcp_fixture, monkeypatch,
):
    from app.api.portal.endpoints import mcp_credential_profiles
    from app.services.ai.tools import mcp_client, mcp_factory

    db, server, _, _, _ = mcp_fixture
    server.scope = "personal"
    server.user_id = 101
    tool = McpToolCache(
        id="header-fixture", server_id=server.id, tool_name="fixture-mail:header_mail",
        is_published=True, is_available=True,
        parameter_schema='{"type":"object","properties":{}}',
    )
    db.add(tool)
    await db.commit()

    owner = {"user_id": 101, "role": "user"}
    original = (await mcp_credential_profiles.get_profile(tool.id, user=owner, db=db))["data"]
    request = mcp_credential_profiles.ModelResultFieldsRequest(
        model_result_fields=["$.messages[*][1]"], expected_revision=0,
        expected_target=original["model_result_policy"]["target"],
    )
    with pytest.raises(mcp_credential_profiles.HTTPException) as denied:
        await mcp_credential_profiles.put_model_result_fields(
            tool.id, request, user={"user_id": 102, "role": "user"}, db=db,
        )
    assert denied.value.status_code == 403
    saved = await mcp_credential_profiles.put_model_result_fields(tool.id, request, user=owner, db=db)
    assert saved["data"]["model_result_fields"] == ["$.messages[*][1]"]
    assert (await mcp_credential_profiles.get_profile(tool.id, user=owner, db=db))["data"]["profile"] is None

    @asynccontextmanager
    async def fixture_session():
        yield db

    async def no_bound_headers(_server, headers, **kwargs):
        return headers

    async def load_server(_server_id):
        return server

    sent = []
    audits = []

    class Transport:
        is_direct_http = False
        auth_headers = {"Authorization": "Bearer fixture-header-token"}

        @property
        def session(self):
            return self

        async def call_tool(self, name, arguments, **kwargs):
            assert "progress_callback" not in kwargs
            sent.append((name, arguments))
            return SimpleNamespace(
                content=[SimpleNamespace(text=json.dumps({
                    "messages": [["09:12", "Fixture report", "Fixture body"]],
                    "debug": "fixture-header-token",
                }))],
            )

        async def close(self):
            pass

    async def connect(_server_id, **kwargs):
        assert kwargs["auth_headers"] == Transport.auth_headers
        assert kwargs["server_snapshot"] is server
        return Transport()

    async def audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(mcp_client, "AsyncSessionLocal", fixture_session)
    monkeypatch.setattr(mcp_client, "apply_mcp_bindings", no_bound_headers)
    monkeypatch.setattr(mcp_client, "build_mcp_headers", lambda *args, **kwargs: Transport.auth_headers)
    monkeypatch.setattr(mcp_client.McpClientService, "_load_server", load_server)
    monkeypatch.setattr(mcp_client.McpClientService, "get_session", connect)
    monkeypatch.setattr(mcp_client, "record_outbound_audit_log", audit)
    monkeypatch.setattr(mcp_factory, "current_mcp_agent_identity", lambda: (owner, {}))

    wrapped = mcp_factory.McpToolFactory.create_tool(tool, verify_profile=True)

    async def invoke():
        stream = await wrapped.ainvoke({})
        return [part async for part in stream]

    assert await invoke() == [{
        "status": "success", "result_visibility": "projected",
        "data": {"messages": [[None, "Fixture report"]]},
    }]
    assert sent == [("header_mail", {})]
    assert audits[0]["tool_output"] is None and audits[0]["arguments"] == {}

    server.sse_url = "https://mcp.example.test/new-endpoint"
    assert await invoke() == [{"status": "success", "result_visibility": "restricted"}]
    config = (await mcp_credential_profiles.get_profile(tool.id, user=owner, db=db))["data"]
    assert config["model_result_policy"]["stale"]
    stale_save = mcp_credential_profiles.ModelResultFieldsRequest(
        model_result_fields=["$.messages[*][1]"], expected_revision=saved["data"]["revision"],
        expected_target=saved["data"]["target"],
    )
    with pytest.raises(mcp_credential_profiles.HTTPException) as changed:
        await mcp_credential_profiles.put_model_result_fields(tool.id, stale_save, user=owner, db=db)
    assert changed.value.detail["code"] == "MCP_RESULT_FIELDS_CHANGED"
    server.sse_url = "https://mcp.example.test/rpc"

    cleared = await mcp_credential_profiles.put_model_result_fields(
        tool.id, mcp_credential_profiles.ModelResultFieldsRequest(
            model_result_fields=[], expected_revision=saved["data"]["revision"],
            expected_target=saved["data"]["target"],
        ), user=owner, db=db,
    )
    assert cleared["data"]["model_result_fields"] == []
    Transport.auth_headers = {}
    assert await invoke() == [{"status": "success", "result_visibility": "restricted"}]


async def test_saving_without_result_fields_hides_previously_visible_data(mcp_fixture):
    from app.api.portal.endpoints import mcp_credential_profiles

    db, _, tool, _, profile = mcp_fixture
    admin = {"user_id": 999, "role": "admin"}

    async def save(**changes):
        return await mcp_credential_profiles.put_profile(
            tool.id, mcp_credential_profiles.ProfileRequest(
                type_key=profile.type_key, type_version=profile.type_version, selection_mode=profile.selection_mode,
                mappings=profile.mappings, expected_revision=profile.revision, expected_profile_id=profile.profile_id,
                **changes,
            ), user=admin, db=db,
        )

    for empty in ({}, {"model_result_fields": None}):
        await save(model_result_fields=["$.account"])
        cleared = await save(**empty)
        assert cleared["data"]["model_result_fields"] == []
        assert "warnings" not in cleared


@pytest.mark.asyncio
async def test_old_profile_generation_does_not_authorize_recreated_mapping(mcp_fixture):
    from sqlalchemy import delete

    db, server, tool, _, profile = mcp_fixture
    call = mcp_profiles.McpCredentialInvocation(
        tool.id, profile.revision, user_query="请用 demo@example.test 查邮件", profile_id=profile.profile_id,
    )
    await db.execute(delete(McpCredentialProfile).where(McpCredentialProfile.tool_id == tool.id))
    await db.commit()
    replacement = await mcp_profiles.save_profile(
        db, tool, server, type_key="imap_password", type_version=1, selection_mode="infer",
        mappings={name: name for name in ("username", "password", "host", "port")},
        expected_revision=0, actor_id="999",
    )
    assert replacement.revision == call.profile_revision and replacement.profile_id != call.profile_id
    with pytest.raises(mcp_profiles.CredentialInvocationError) as error:
        await mcp_profiles.prepare_mcp_arguments(
            db, server, "list_mail", {"credential_account": "demo@example.test"}, call,
            user_info={"user_id": "101"}, trace_id=None, conversation_id=None,
        )
    assert error.value.code == "CREDENTIAL_PROFILE_CHANGED"


@pytest.mark.asyncio
async def test_custom_type_validation_and_member_creation_with_composite_values(credential_db):
    from app.api.portal.endpoints import credential_types

    db = credential_db
    admin = {"role": "admin", "user_id": 999}
    member = {"role": "user", "user_id": 101}
    schema = {
        "type": "object",
        "x-icon": "☸️",
        "properties": {
            "labels": {"type": "array", "items": {"type": "string"}},
            "cluster": {
                "type": "object",
                "properties": {"token": {"type": "string"}},
                "required": ["token"],
            },
        },
        "required": ["cluster"],
    }
    preview = await credential_types.check_schema(credential_types.SchemaDefinition(schema=schema), admin=admin)
    assert preview["data"]["properties"]["cluster"]["properties"]["token"]["writeOnly"] is True
    assert await db.get(CredentialType, "custom_cluster") is None
    payload = credential_types.CreateType(type_key="custom_cluster", name="测试集群", schema=schema)
    result = await credential_types.create_type(payload, admin=admin, db=db)
    assert result["data"]["schema"] == preview["data"]
    member_types = await credential_types.list_types(include_disabled=False, user=member, db=db)
    assert any(item["type_key"] == "custom_cluster" and item["can_create"] for item in member_types["data"])

    values = {"cluster": {"token": "synthetic-cluster-only"}, "labels": ["internal-fixture"]}
    row = await center.create_secret(
        db, owner_id="101", secret_key="cluster", name="测试集群",
        allowed_scenarios=["mcp"], allowed_hosts=["mcp.example.test"],
        structured=structured_payload.StructuredInput("custom_cluster", 1, "fixture-cluster", values),
    )
    stored = await db.get(StructuredCredential, row.id)
    assert stored.public_projection == {}
    cipher = (await payload_config.load_payload_config(db)).cipher()
    assert structured_payload.decrypt_fields(stored, cipher) == values
    summary = (await structured_payload.describe_payloads(db, [row.id]))[row.id]
    assert summary["secret_fields_set"] == ["cluster"]
    assert "synthetic-cluster-only" not in json.dumps(summary)
    assert "internal-fixture" not in json.dumps(summary)
