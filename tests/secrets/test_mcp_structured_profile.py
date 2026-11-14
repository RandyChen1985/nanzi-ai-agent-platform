import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core import orm
from app.services.ai.tools.mcp_factory import McpToolFactory
from app.services.secrets import mcp_profiles
from app.services.secrets.credential_schema import BUILTIN_SCHEMAS, account_key
from app.services.secrets.mcp_profiles import (
    CredentialInvocationError,
    compile_mappings,
    model_schema,
)

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "folder": {"type": "string", "description": "Business folder", "default": "synthetic-default"},
        "username": {"type": "string", "default": "synthetic-username"},
        "password": {"type": "string", "writeOnly": True, "default": "synthetic-password"},
        "host": {"type": "string"},
        "port": {"type": "integer"},
    },
    "required": ["username", "password", "host", "port"],
}
MAPPINGS = {key: key for key in ("username", "password", "host", "port")}


def test_schema_exposes_only_safe_arguments_and_optional_selector():
    mappings = compile_mappings(TOOL_SCHEMA, BUILTIN_SCHEMAS["imap_password"][1], MAPPINGS)
    profile = SimpleNamespace(mappings=mappings, selection_mode="infer", type_key="imap_password", type_version=1)
    projected = model_schema(TOOL_SCHEMA, profile)
    assert set(projected["properties"]) == {"folder", "credential_account"}
    assert "credential_account" not in projected.get("required", [])
    assert "synthetic-" not in repr(projected)
    assert "enum" not in projected["properties"]["credential_account"]
    description = projected["properties"]["credential_account"]["description"]
    assert "凭据中心" in description
    assert "imap_password" in description
    assert "v1" in description


@pytest.mark.parametrize("mappings", [
    {"username": "username"},
    {**MAPPINGS, "port": "password"},
    {**MAPPINGS, "credential_account": "username"},
])
def test_rejects_unbound_sensitive_and_invalid_mappings(mappings):
    with pytest.raises(CredentialInvocationError):
        compile_mappings(TOOL_SCHEMA, BUILTIN_SCHEMAS["imap_password"][1], mappings)



def test_fixed_tool_retains_nested_business_structure_without_selector():
    schema = json.loads(json.dumps(TOOL_SCHEMA))
    schema["description"] = "synthetic-root-description mentions password"
    schema["properties"]["mailbox_filter"] = {"type": "array", "items": {
        "type": "object", "properties": {
            "folder": {"type": "string", "description": "Mailbox folder", "enum": ["inbox", "archive"]},
            "limit": {"type": "integer", "minimum": 1},
        }, "required": ["folder"],
    }}
    tool = McpToolFactory.create_tool(
        SimpleNamespace(
            id="fixture-tool", server_id="fixture-server", tool_name="mail:list_mail",
            tool_description="synthetic-upstream-description", parameter_schema=json.dumps(schema),
        ),
        credential_profile=SimpleNamespace(
            profile_id="fixture-profile", revision=1, mappings=MAPPINGS,
            selection_mode="fixed", type_key="imap_password", type_version=1,
        ),
        verify_profile=True,
    )

    properties = tool.mcp_input_schema["properties"]
    assert set(properties) == {"folder", "mailbox_filter"}
    assert properties["mailbox_filter"] == {"type": "array", "items": {
        "type": "object", "properties": {
            "folder": {"type": "string", "description": "Mailbox folder", "enum": ["inbox", "archive"]},
            "limit": {"type": "integer", "minimum": 1},
        },
        "required": ["folder"], "additionalProperties": False,
    }}
    assert not tool.mcp_input_schema.get("required")
    assert "synthetic-" not in repr(tool.mcp_input_schema)


def test_unmapped_nested_authentication_requires_mapping_parent_object():
    schema = json.loads(json.dumps(TOOL_SCHEMA))
    schema["properties"]["connection"] = {
        "type": "array",
        "items": {"type": "object", "properties": {"accessToken": {"type": "string"}}},
    }

    with pytest.raises(CredentialInvocationError) as blocked:
        compile_mappings(schema, BUILTIN_SCHEMAS["imap_password"][1], MAPPINGS)

    assert blocked.value.code == "CREDENTIAL_MAPPING_REQUIRED"


@pytest.fixture
def profile_lookup(monkeypatch):
    session = object()
    profile = SimpleNamespace(tool_id="fixture-tool", profile_id="fixture-profile", revision=1, selection_mode="infer")
    server = SimpleNamespace(id="fixture-server")
    user = SimpleNamespace(dept_code="fixture-dept")
    ref = {"tool_id": profile.tool_id, "profile_id": profile.profile_id, "revision": 1, "selection_mode": "infer"}
    found = AsyncMock(return_value=[SimpleNamespace(account="alice@example.test")])

    @asynccontextmanager
    async def sessions():
        yield session

    monkeypatch.setattr(orm, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(mcp_profiles, "pending_target", AsyncMock(return_value=(profile, server, user)))
    monkeypatch.setattr(mcp_profiles, "list_candidates", found)
    return session, profile, server, ref, found


@pytest.mark.asyncio
async def test_authorized_account_hint_can_come_from_previous_context(profile_lookup):
    session, profile, server, ref, found = profile_lookup
    account = "alice@example.test"
    assert not await mcp_profiles.requires_interactive_selection(
        ref, {"credential_account": account}, "101", "请接着读取那封邮件",
    )
    found.assert_awaited_once_with(
        session, profile, server, user_id="101", dept_code="fixture-dept",
        secret_id=None, digest=account_key(account)[1],
    )


@pytest.mark.asyncio
async def test_database_lookup_failure_does_not_open_creation_form(profile_lookup):
    _, _, _, ref, found = profile_lookup
    found.side_effect = RuntimeError("synthetic-database-internal-error")

    with pytest.raises(CredentialInvocationError) as failure:
        await mcp_profiles.requires_interactive_selection(
            ref, {"credential_account": "alice@example.test"}, "101", "继续",
        )

    assert failure.value.code == "CREDENTIAL_QUERY_UNAVAILABLE"
    assert "synthetic" not in str(failure.value)


@pytest.mark.asyncio
async def test_fixed_binding_only_waits_when_unavailable(monkeypatch, profile_lookup):
    _, profile, _, ref, found = profile_lookup
    profile.selection_mode = ref["selection_mode"] = "fixed"
    binding = AsyncMock(return_value=SimpleNamespace(secret_id="fixture-fixed"))
    monkeypatch.setattr(mcp_profiles.SecretResolver, "_pick_binding", binding)

    assert not await mcp_profiles.requires_interactive_selection(ref, {}, "101", "")
    assert found.await_args.kwargs["secret_id"] == "fixture-fixed"

    found.return_value = []
    assert await mcp_profiles.requires_interactive_selection(ref, {}, "101", "")

    binding.return_value = None
    assert await mcp_profiles.requires_interactive_selection(ref, {}, "101", "")


@pytest.mark.parametrize("field_schema", [
    {"type": ["object", "null"], "properties": {"limit": {"type": "integer"}}},
    {"anyOf": [{"type": "object", "properties": {"limit": {"type": "integer"}}}, {"type": "null"}]},
    {"oneOf": [{"type": "object", "properties": {"limit": {"type": "integer"}}}, {"type": "null"}]},
])
def test_nullable_business_object_keeps_structure_without_unknown_nested_fields(field_schema):
    profile = SimpleNamespace(selection_mode="fixed", mappings=MAPPINGS)
    schema = {**TOOL_SCHEMA, "properties": {**TOOL_SCHEMA["properties"], "filter": field_schema}}
    public = model_schema(schema, profile)

    assert public["properties"]["filter"]
    value = {"filter": {"limit": 2, "undeclared": "unused"}, "password": "never-outbound"}
    assert mcp_profiles._public_mcp_arguments(value, public) == {"filter": {"limit": 2}}
    assert mcp_profiles._public_mcp_arguments({"filter": None}, public) == {"filter": None}
    assert value == {"filter": {"limit": 2, "undeclared": "unused"}, "password": "never-outbound"}


@pytest.mark.asyncio
async def test_task_default_is_used_only_when_infer_has_no_account(monkeypatch, profile_lookup):
    from app.services.secrets.task_credential_wait import TaskCredentialRef, current_task_credential_refs

    session, profile, server, ref, found = profile_lookup
    token = current_task_credential_refs.set({
        profile.tool_id: TaskCredentialRef(7, "101", profile.profile_id, profile.revision, "fixture-alice"),
    })
    try:
        account = "bob@example.test"
        found.return_value = [SimpleNamespace(account=account)]
        assert await mcp_profiles.credential_selection_reason(ref, {"credential_account": account}, "101") is None
        found.assert_awaited_once_with(
            session, profile, server, user_id="101", dept_code="fixture-dept",
            secret_id=None, digest=account_key(account)[1],
        )

        found.reset_mock()
        found.return_value = [SimpleNamespace(account="alice@example.test")]
        assert await mcp_profiles.credential_selection_reason(ref, {}, "101") is None
        found.assert_awaited_once_with(
            session, profile, server, user_id="101", dept_code="fixture-dept",
            secret_id="fixture-alice", digest=None,
        )
    finally:
        current_task_credential_refs.reset(token)


@pytest.mark.asyncio
async def test_invalid_keyring_is_a_configuration_error_before_selection(monkeypatch):
    from app.services.secrets.payload_config import PayloadConfig

    monkeypatch.setattr(mcp_profiles, "load_payload_config", AsyncMock(return_value=PayloadConfig(True, "{}")))
    with pytest.raises(CredentialInvocationError) as error:
        await mcp_profiles.pending_target(object(), {}, "101")
    assert error.value.code == "CREDENTIAL_ENCRYPTION_UNAVAILABLE"


@pytest.mark.parametrize("keyword", ["anyOf", "oneOf"])
def test_composite_business_branch_preserves_the_declared_input_fields(keyword):
    profile = SimpleNamespace(selection_mode="fixed", mappings=MAPPINGS)
    filter_schema = {keyword: [
        {"type": "object", "properties": {"folder": {"type": "string"}}},
        {"type": "object", "properties": {"limit": {"type": "integer"}}},
    ]}
    schema = {**TOOL_SCHEMA, "properties": {**TOOL_SCHEMA["properties"], "filter": filter_schema}}

    assert mcp_profiles._public_mcp_arguments(
        {"filter": {"limit": 2, "undeclared": "unused"}}, model_schema(schema, profile),
    ) == {"filter": {"limit": 2}}
