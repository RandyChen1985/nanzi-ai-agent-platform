import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core import orm
from app.services.ai.tools import mcp_factory
from app.services.ai.tools.mcp_factory import McpToolFactory
from app.services.ai.tools.model_tool_name import build_model_tool_name
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
        }, "required": ["folder"], "additionalProperties": False,
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
    {"type": ["object", "null"], "properties": {"limit": {"type": "integer"}}, "additionalProperties": False},
    {"anyOf": [{"type": "object", "properties": {"limit": {"type": "integer"}}, "additionalProperties": False},
               {"type": "null"}]},
    {"oneOf": [{"type": "object", "properties": {"limit": {"type": "integer"}}, "additionalProperties": False},
               {"type": "null"}]},
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
        {"type": "object", "properties": {"folder": {"type": "string"}}, "additionalProperties": False},
        {"type": "object", "properties": {"limit": {"type": "integer"}}, "additionalProperties": False},
    ]}
    schema = {**TOOL_SCHEMA, "properties": {**TOOL_SCHEMA["properties"], "filter": filter_schema}}

    assert mcp_profiles._public_mcp_arguments(
        {"filter": {"limit": 2, "undeclared": "unused"}}, model_schema(schema, profile),
    ) == {"filter": {"limit": 2}}


@pytest.mark.parametrize(("source", "target", "result"), [
    ("password", {"type": "string"}, True),
    ("password", {"type": ["string", "null"], "writeOnly": True}, True),
    ("password", {"type": ["null", "string"]}, True),
    ("password", {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]}, True),
    ("password", {"oneOf": [{"type": "null"}, {"type": "string"}]}, True),
    ("port", {"type": ["null", "number"]}, True),
    ("password", {"type": ["string", "integer", "null"]}, False),
    ("password", {"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]}, False),
    ("password", {"oneOf": [{"type": "string"}, {"type": "integer"}]}, False),
    ("password", {"type": ["null"]}, False),
    ("password", {"anyOf": [{"type": "null"}, {"type": "string"}, {}]}, False),
    ("password", {"type": "integer", "anyOf": [{"type": "string"}, {"type": "null"}]}, False),
])
def test_mapping_accepts_only_one_non_null_type(source, target, result):
    schema = {"type": "object", "properties": {"value": target}}
    source_schema = BUILTIN_SCHEMAS["imap_password"][1]
    if result:
        assert compile_mappings(schema, source_schema, {"value": source}) == {"value": source}
    else:
        with pytest.raises(CredentialInvocationError) as error:
            compile_mappings(schema, source_schema, {"value": source})
        assert error.value.code == "CREDENTIAL_MAPPING_INVALID"


@pytest.mark.asyncio
async def test_profile_configuration_returns_normalized_types_without_exposing_remote_schema(monkeypatch):
    from app.api.portal.endpoints import mcp_credential_profiles

    tool = SimpleNamespace(
        id="fixture-tool", tool_name="mail:configure",
        parameter_schema=json.dumps({"$defs": {"Proof": {"type": "string", "writeOnly": True}}, "properties": {
            "proof": {"$ref": "#/$defs/Proof"},
            "nullable_array": {"type": ["null", "string"]},
            "nullable_any": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": "fixture-only"},
            "nullable_one": {"oneOf": [{"type": "null"}, {"type": "number"}]},
            "incompatible": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
        }}),
    )
    server = SimpleNamespace(id="fixture-server", sse_url="https://mcp.example.test/rpc")
    monkeypatch.setattr(mcp_credential_profiles, "_resource", AsyncMock(return_value=(tool, server)))
    db = SimpleNamespace(get=AsyncMock(return_value=None))
    response = await mcp_credential_profiles.get_profile(tool.id, user={"user_id": 101}, db=db)
    assert {item["name"]: (item["type"], item["mapping_type"]) for item in response["data"]["parameters"]} == {
        "proof": (None, "string"),
        "nullable_array": (["null", "string"], "string"),
        "nullable_any": (None, "string"),
        "nullable_one": (None, "number"),
        "incompatible": (None, None),
    }
    proof = next(item for item in response["data"]["parameters"] if item["name"] == "proof")
    assert proof["sensitive"] and proof["group_binding_required"]
    assert "fixture-only" not in repr(response)


@pytest.mark.parametrize(("field_schema", "payload"), [
    ({"type": "object"}, {"subject": "Fixture subject", "labels": [{"added": True}, None]}),
    ({"type": "object", "additionalProperties": True}, {"subject": "Fixture subject"}),
    ({"type": "object", "additionalProperties": {"type": "array", "items": {"type": "object",
        "properties": {"host": {"type": "string"}, "port": {"type": "integer"}},
        "required": ["host"]}}}, {"primary": [{"host": "example.test", "port": 443}]}),
])
def test_profile_projection_preserves_allowed_dynamic_business_fields(field_schema, payload):
    profile = SimpleNamespace(selection_mode="fixed", mappings=MAPPINGS)
    schema = {**TOOL_SCHEMA, "properties": {**TOOL_SCHEMA["properties"], "payload": field_schema}}
    projected = model_schema(schema, profile)
    assert projected["properties"]["payload"]["additionalProperties"] is not False
    assert mcp_profiles._public_mcp_arguments({"payload": payload}, projected) == {"payload": payload}


def test_profile_projection_validates_dynamic_property_types():
    profile = SimpleNamespace(selection_mode="fixed", mappings=MAPPINGS)
    schema = {**TOOL_SCHEMA, "properties": {**TOOL_SCHEMA["properties"], "labels": {
        "type": "object", "additionalProperties": {"type": "array", "items": {"type": "integer"}},
    }}}
    with pytest.raises(CredentialInvocationError) as error:
        mcp_profiles._public_mcp_arguments({"labels": {"a": ["one"]}}, model_schema(schema, profile))
    assert error.value.code == "MCP_ARGUMENTS_INVALID"


def test_profile_projection_rejects_unsupported_object_schema_instead_of_emptying_it():
    profile = SimpleNamespace(selection_mode="fixed", mappings=MAPPINGS)
    schema = {**TOOL_SCHEMA, "properties": {**TOOL_SCHEMA["properties"], "payload": {
        "type": "object", "patternProperties": {"^label": {"type": "integer"}}, "additionalProperties": False,
    }}}
    with pytest.raises(CredentialInvocationError) as error:
        mcp_profiles._public_mcp_arguments({"payload": {"label1": 1}}, model_schema(schema, profile))
    assert error.value.code == "MCP_ARGUMENTS_INVALID"


def test_open_business_schema_does_not_forward_nested_authentication_fields():
    profile = SimpleNamespace(selection_mode="fixed", mappings=MAPPINGS)
    schema = {**TOOL_SCHEMA, "properties": {**TOOL_SCHEMA["properties"], "payload": {"type": "object"}}}
    projected = model_schema(schema, profile)
    value = {"payload": {"entries": [{"subject": "Fixture", "accessToken": "forged", "credential_account": "x"}]}}
    assert mcp_profiles._public_mcp_arguments(value, projected) == {"payload": {"entries": [{"subject": "Fixture"}]}}


def test_profile_projection_retains_page_cursor_and_business_connection_fields():
    schema = {**TOOL_SCHEMA, "properties": {**TOOL_SCHEMA["properties"],
        "page_token": {"type": "string", "description": "Previous page cursor"},
        "payload": {"type": "object", "properties": {"url": {"type": "string"}}},
    }, "required": [*TOOL_SCHEMA["required"], "page_token"]}
    mappings = compile_mappings(schema, BUILTIN_SCHEMAS["imap_password"][1], MAPPINGS)
    for mode in ("fixed", "infer"):
        profile = SimpleNamespace(selection_mode=mode, type_key="imap_password", type_version=1, mappings=mappings)
        projected = model_schema(schema, profile)
        assert projected["required"] == ["page_token"]
        assert "host" not in projected["properties"] and "password" not in projected["properties"]
        assert ("credential_account" in projected["properties"]) == (mode == "infer")

        arguments = {"page_token": "next", "payload": {"url": "https://example.test/items"},
                     "host": "model-supplied.test", "password": "model-forgery", "credential_account": "fixture"}
        assert mcp_profiles._public_mcp_arguments(arguments, projected) == {
            "page_token": "next", "payload": {"url": "https://example.test/items"},
        }


@pytest.mark.asyncio
async def test_outbound_preparation_rechecks_explicit_nested_authentication():
    from app.models.mcp import McpToolCache

    schema = {"type": "object", "properties": {"payload": {
        "type": "object", "properties": {"authentication": {
            "type": ["null", "string"], "writeOnly": True,
        }},
    }}}
    record = _cached_tool(schema)
    server = SimpleNamespace(id=record.server_id, sse_url="https://mcp.example.test/rpc")
    session = SimpleNamespace(get=AsyncMock(side_effect=lambda model, _: record if model is McpToolCache else None))
    with pytest.raises(CredentialInvocationError) as error:
        await mcp_profiles.prepare_mcp_arguments(
            session, server, "list_items", {"payload": {}},
            mcp_profiles.McpCredentialInvocation(record.id, None),
            user_info={"user_id": "101"}, trace_id=None, conversation_id=None,
        )
    assert error.value.code == "CREDENTIAL_MAPPING_REQUIRED"


def _cached_tool(schema, *, bound=False):
    return SimpleNamespace(
        id="fixture-tool", server_id="fixture-server", tool_name="catalog:list_items",
        tool_description="List catalog items across pages." if not bound else "Remote description mentions password.",
        parameter_schema=json.dumps(schema),
        credential_params='["password"]' if bound else None,
        is_available=True, is_published=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("required", "bound"), [(False, False), (True, False), (False, True), (True, True)])
async def test_page_token_remains_usable_through_mcp_invocation(monkeypatch, required, bound):
    from app.models.mcp import McpToolCache
    from app.services.secrets.mcp_model_result import restricted_model_result

    schema = {"type": "object", "properties": {
        "page_token": {"type": "string", "description": "Cursor returned on the prior page."},
        "customer-id": {"type": "string", "description": "Customer identifier", "minLength": 1},
        "url": {"type": "string", "description": "URL to search in the catalog"},
        "payload": {"type": "array", "items": {"type": "object", "properties": {
            "priority": {"type": "integer", "description": "Prioritize lower values", "minimum": 0},
            "host": {"type": "string"},
        }, "required": ["priority"]}},
    }, "required": ["page_token"] if required else [], "additionalProperties": False}
    schema["properties"]["payload"]["items"]["properties"]["labels"] = {"type": "object"}
    if bound:
        schema["properties"]["password"] = {"type": "string", "writeOnly": True, "default": "fixture-only"}
        schema["properties"]["legacy_hint"] = {"type": "string", "description": "The password configuration"}
        schema["required"].append("password")
    record = _cached_tool(schema, bound=bound)
    server = SimpleNamespace(id=record.server_id, sse_url="https://mcp.example.test/rpc")
    session = SimpleNamespace(get=AsyncMock(side_effect=lambda model, _: record if model is McpToolCache else None))
    transported = []

    async def inject(tool, arguments, **kwargs):
        assert tool is record
        assert "password" not in arguments
        return {**arguments, "password": "injected-value"}

    async def transport(*, tool_name, arguments, credential_call, user_info, **kwargs):
        assert tool_name == "list_items"
        prepared = await mcp_profiles.prepare_mcp_arguments(
            session, server, tool_name, arguments, credential_call, user_info=user_info,
            trace_id=None, conversation_id=None,
        )
        transported.append((prepared.arguments, prepared.protect_payload))
        return restricted_model_result("no_matching_fields") if bound else {"status": "success"}

    monkeypatch.setattr(mcp_factory, "current_mcp_agent_identity", lambda: ({"user_id": "101"}, {}))
    monkeypatch.setattr(mcp_factory, "resolve_mcp_tool_timeout", AsyncMock(return_value=1.0))
    monkeypatch.setattr(mcp_factory.McpClientService, "call_remote_tool", transport)
    monkeypatch.setattr(mcp_profiles, "apply_mcp_arg_bindings", inject)
    tool = McpToolFactory.create_tool(record, verify_profile=True, parameter_binding_modes={"password": "fixed"})

    properties = tool.mcp_input_schema["properties"]
    expected_properties = {"page_token", "customer-id", "url", "payload"}
    if bound:
        expected_properties.add("legacy_hint")
    assert set(properties) == expected_properties
    assert tool.args_schema.model_validate({"customer-id": "fixture", **({"page_token": "next"} if required else {})})
    assert properties["customer-id"] == {"type": "string", "description": "Customer identifier", "minLength": 1}
    assert properties["payload"]["items"]["properties"]["priority"] == {
        "type": "integer", "description": "Prioritize lower values", "minimum": 0,
    }
    assert properties["page_token"]["description"] == "Cursor returned on the prior page."
    assert tool.name == build_model_tool_name("catalog:list_items")
    assert ("page_token" in tool.mcp_input_schema.get("required", [])) is required
    if bound:
        assert "Remote description" not in tool.description
        assert "fixture-only" not in repr(tool.mcp_input_schema)
        assert "description" not in properties["legacy_hint"]
    else:
        assert tool.description == "List catalog items across pages."

    business = {"customer-id": "fixture", "url": "https://example.test/catalog",
                "payload": [{"priority": 0, "host": "example.test", "labels": {"sort": ["first"], "other": 1}}]}
    for cursor in ({"page_token": "next"},) if required else ({}, {"page_token": "next"}):
        arguments = {**business, **cursor, "undeclared": "skip"}
        if bound:
            arguments["password"] = "model-forgery"
        summaries = [item async for item in await tool.ainvoke(arguments)]
        assert summaries[-1]["status"] == "success"
        expected = {**business, **cursor}
        if bound:
            expected["password"] = "injected-value"
        assert transported[-1] == (expected, bound)


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "spec"), [
    ("page_token", {"type": "string", "writeOnly": True}),
    ("page_token", {"type": "string", "format": "password"}),
    ("accessToken", {"type": "string"}),
    ("payload", {"type": "array", "items": {"type": "object",
        "properties": {"authorization": {"type": "string"}}}}),
])
async def test_unbound_explicit_authentication_stays_hidden_and_blocks_invocation(monkeypatch, name, spec):
    record = _cached_tool({"properties": {name: spec, "query": {"type": "string"}}})
    record.tool_description = "Remote description with fixture-only credentials"
    tool = McpToolFactory.create_tool(record, verify_profile=True)
    assert set(tool.mcp_input_schema["properties"]) == {"query"}
    assert "fixture-only" not in tool.description

    async def unexpected_peer(**kwargs):
        pytest.fail("未配置的明确认证参数不能触达上游")

    monkeypatch.setattr(mcp_factory.McpClientService, "call_remote_tool", unexpected_peer)
    monkeypatch.setattr(mcp_factory, "resolve_mcp_tool_timeout", AsyncMock(return_value=1.0))
    assert [item async for item in await tool.ainvoke({"query": "fixture"})] == [
        {"status": "error", "code": "CREDENTIAL_MAPPING_REQUIRED"},
    ]


@pytest.mark.asyncio
async def test_rule_choice_does_not_overwrite_business_parameter_with_same_name(monkeypatch):
    record = _cached_tool({"properties": {
        "credential_choice_1": {"type": "string", "description": "Catalog preference"},
        "password": {"type": "string", "writeOnly": True},
    }}, bound=True)
    transported = []

    async def transport(*, arguments, credential_call, **kwargs):
        transported.append((arguments, credential_call.parameter_choices))
        from app.services.secrets.mcp_model_result import restricted_model_result
        return restricted_model_result("no_matching_fields")

    monkeypatch.setattr(mcp_factory, "resolve_mcp_tool_timeout", AsyncMock(return_value=1.0))
    monkeypatch.setattr(mcp_factory.McpClientService, "call_remote_tool", transport)
    tool = McpToolFactory.create_tool(record, verify_profile=True, parameter_binding_modes={"password": "rule"})

    assert tool.mcp_input_schema["properties"]["credential_choice_1"]["description"] == "Catalog preference"
    assert set(tool.mcp_input_schema["properties"]) == {"credential_choice_1", "credential_choice_2"}
    values = {"credential_choice_1": "promotional", "credential_choice_2": "c2"}
    assert [item async for item in await tool.ainvoke(values)][-1]["status"] == "success"
    assert transported == [({"credential_choice_1": "promotional"}, (("password", "c2"),))]


def test_profile_selector_and_public_schema_do_not_reopen_hidden_nonstandard_mapping():
    schema = {"type": "object", "properties": {
        "opaque": {"type": "string"},
        "page_token": {"type": "string"},
    }, "required": ["opaque"]}
    profile = SimpleNamespace(selection_mode="infer", type_key="fixture", type_version=1, mappings={"opaque": "value"})
    projected = model_schema(schema, profile)

    values = {
        "credential_account": "fixture", "opaque": "forged",
        "page_token": "next", "extra": {"opaque": "also-forged", "note": "fixture"},
    }
    assert mcp_profiles._public_mcp_arguments(values, projected) == {
        "page_token": "next", "extra": {"note": "fixture"},
    }
    assert "opaque" not in repr(projected)


def test_infer_schema_can_be_constructed_with_unsupported_root():
    schema = {"type": "object", "properties": {"password": {"type": "string"}}, "allOf": [{"type": "object"}]}
    profile = SimpleNamespace(selection_mode="infer", type_key="fixture", type_version=1, mappings=MAPPINGS)
    projected = model_schema(schema, profile)
    with pytest.raises(CredentialInvocationError) as error:
        mcp_profiles._public_mcp_arguments({}, projected)
    assert error.value.code == "MCP_ARGUMENTS_INVALID"


@pytest.mark.asyncio
async def test_verified_choice_keeps_business_key_colliding_with_internal_choice(monkeypatch):
    from app.models.mcp import McpToolCache
    from app.services.secrets.mcp_model_result import restricted_model_result

    record = _cached_tool({"properties": {
        "secret_choice_password": {"type": "string", "description": "Business preference code"},
        "password": {"type": "string", "writeOnly": True},
    }}, bound=True)
    server = SimpleNamespace(id=record.server_id, sse_url="https://mcp.example.test/rpc")
    session = SimpleNamespace(get=AsyncMock(side_effect=lambda model, _: record if model is McpToolCache else None))
    transported = []

    async def inject(_, arguments, *, model_choices=None, **kwargs):
        assert model_choices == {"password": "c2"}
        assert arguments == {"secret_choice_password": "business"}
        return {**arguments, "password": "injected-value"}

    async def transport(*, tool_name, arguments, credential_call, user_info, **kwargs):
        prepared = await mcp_profiles.prepare_mcp_arguments(
            session, server, tool_name, arguments, credential_call, user_info=user_info,
            trace_id=None, conversation_id=None,
        )
        transported.append(prepared.arguments)
        return restricted_model_result("no_matching_fields")

    monkeypatch.setattr(mcp_factory, "current_mcp_agent_identity", lambda: ({"user_id": "101"}, {}))
    monkeypatch.setattr(mcp_factory, "resolve_mcp_tool_timeout", AsyncMock(return_value=1.0))
    monkeypatch.setattr(mcp_factory.McpClientService, "call_remote_tool", transport)
    monkeypatch.setattr(mcp_profiles, "apply_mcp_arg_bindings", inject)
    tool = McpToolFactory.create_tool(record, verify_profile=True, parameter_binding_modes={"password": "rule"})

    assert tool.mcp_input_schema["properties"]["secret_choice_password"]["description"] == "Business preference code"
    values = {"secret_choice_password": "business", "credential_choice_1": "c2"}
    assert [item async for item in await tool.ainvoke(values)][-1]["status"] == "success"
    assert transported == [{"secret_choice_password": "business", "password": "injected-value"}]


def test_root_branch_cannot_reintroduce_mapped_argument_into_model_schema():
    schema = {"type": "object", "properties": {"opaque": {"type": "string"}}, "anyOf": [
        {"properties": {"opaque": {"type": "string", "description": "fixture-only"}, "query": {"type": "string"}},
         "required": ["opaque", "query"]},
        {"properties": {"limit": {"type": "integer"}}, "required": ["limit"]},
    ]}
    profile = SimpleNamespace(selection_mode="infer", type_key="fixture", type_version=1, mappings={"opaque": "value"})
    projected = model_schema(schema, profile)
    assert "opaque" not in repr(projected) and "fixture-only" not in repr(projected)
    assert mcp_profiles._public_mcp_arguments({"query": "x", "opaque": "model-forgery"}, projected) == {"query": "x"}
    assert mcp_profiles._public_mcp_arguments({"limit": 2}, projected) == {"limit": 2}


def test_root_branch_detects_explicit_authentication_fields():
    schema = {"type": "object", "properties": {}, "oneOf": [
        {"properties": {"credential_value": {"type": "string", "writeOnly": True, "default": "fixture-only"}}},
        {"properties": {"query": {"type": "string"}}},
    ]}
    assert mcp_profiles.unbound_sensitive_args(schema, []) == {"credential_value"}
    public = model_schema(schema, SimpleNamespace(selection_mode="fixed", mappings={"credential_value": "value"}))
    assert "credential_value" not in repr(public) and "fixture-only" not in repr(public)


def _fastmcp_nested_schema(*, authenticated: bool) -> dict:
    from mcp.server.mcpserver.utilities.func_metadata import func_metadata
    from pydantic import BaseModel, Field

    class Filter(BaseModel):
        priority: int = Field(description="Sort priority")

    class AuthenticatedFilter(Filter):
        auth_value: str = Field(json_schema_extra={"writeOnly": True})

    def list_items(customer_id: str, payload: list[Filter]) -> None:
        pass

    if authenticated:
        list_items.__annotations__["payload"] = list[AuthenticatedFilter]
    schema = func_metadata(list_items).arg_model.model_json_schema()
    assert schema["properties"]["payload"]["items"]["$ref"].startswith("#/$defs/")
    return schema


@pytest.mark.parametrize(("shape", "bound"), [
    ("object", False), ("anyOf", False), ("oneOf", False), ("closed_parent", False), ("object", True),
    ("fastmcp", False), ("fastmcp_auth", False), ("external_ref", False), ("recursive_ref", False),
])
@pytest.mark.asyncio
async def test_mcp_call_preserves_schema_allowed_extension_keys(monkeypatch, shape, bound):
    from app.core.context import AgentContext, agent_context
    from app.models.mcp import McpToolCache
    from app.services.ai.tools import mcp_client

    schema = {"type": "object", "properties": {
        "page_token": {"type": "string"},
        "customer-id": {"type": "string"},
        "payload": {"type": "array", "items": {"type": "object", "properties": {
            "priority": {"type": "integer"},
        }}},
        "fixed_shape": {"type": "object", "properties": {"room": {"type": "string"}},
                        "additionalProperties": False},
    }, "required": ["customer-id", "payload"]}
    if bound:
        schema["properties"]["api-key"] = {"type": "string", "writeOnly": True}
        schema["required"].append("api-key")
    if shape == "closed_parent":
        schema["additionalProperties"] = False
        schema["properties"]["query"] = {"type": "string"}
    if shape in {"anyOf", "oneOf", "closed_parent"}:
        schema["anyOf" if shape == "closed_parent" else shape] = [
            {"properties": {"query": {"type": "string"}, "payload": {"type": "array"}}, "required": ["query"],
             **({"additionalProperties": True} if shape == "closed_parent" else {})},
            {"properties": {"limit": {"type": "integer"}}, "required": ["limit"]},
        ]
    if shape in {"fastmcp", "fastmcp_auth"}:
        schema = _fastmcp_nested_schema(authenticated=shape == "fastmcp_auth")
    elif shape == "external_ref":
        schema["properties"]["payload"] = {"$ref": "https://example.test/schema.json"}
    elif shape == "recursive_ref":
        schema["$defs"] = {"Loop": {"type": "object", "properties": {"again": {"$ref": "#/$defs/Loop"}}}}
        schema["properties"]["payload"] = {"$ref": "#/$defs/Loop"}
    record = _cached_tool(schema)
    if bound:
        record.credential_params = '["api-key"]'

        async def inject(tool_record, business, **kwargs):
            assert tool_record is record
            assert "api-key" not in business and "api_key" not in business
            return {**business, "api-key": "synthetic-injected"}

        monkeypatch.setattr(mcp_profiles, "apply_mcp_arg_bindings", inject)
    server = SimpleNamespace(id=record.server_id, sse_url="https://mcp.example.test/rpc", enabled_status=1)
    session = SimpleNamespace(get=AsyncMock(side_effect=lambda model, _: record if model is McpToolCache else None))

    @asynccontextmanager
    async def sessions():
        yield session

    monkeypatch.setattr(mcp_client, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(mcp_client.McpClientService, "_load_server", AsyncMock(return_value=server))
    monkeypatch.setattr(mcp_client, "build_mcp_headers", lambda *args, **kwargs: {})
    monkeypatch.setattr(mcp_client, "apply_mcp_bindings", AsyncMock(return_value={}))
    monkeypatch.setattr(mcp_client, "record_outbound_audit_log", AsyncMock())
    manager = SimpleNamespace(is_direct_http=True, auth_headers={}, close=AsyncMock())
    monkeypatch.setattr(mcp_client.McpClientService, "get_session", AsyncMock(return_value=manager))
    monkeypatch.setattr(mcp_factory, "resolve_mcp_tool_timeout", AsyncMock(return_value=1.0))
    received = []

    async def remote(_manager, label, arguments, **kwargs):
        received.append((label, arguments))
        return {"items": ["fixture"]}

    monkeypatch.setattr(mcp_client.McpClientService, "_call_direct_http_tool", remote)
    tool = McpToolFactory.create_tool(record, verify_profile=True, parameter_binding_modes={"api-key": "fixed"})
    base = {"page_token": "next", "customer-id": "primary", "customer_id": "extension",
            "payload": [{"priority": 1, "new_filter": {"label": "fixture"}}],
            "fixed_shape": {"room": "b"}, "region": {"groups": ["north", None]}}
    supplied = {**base, "access_token": "fake-value", "credential_account": "fake-account",
                "payload": [{**base["payload"][0], "apiKey": "fake-value"}],
                "fixed_shape": {**base["fixed_shape"], "undeclared": "prune"}}
    if shape in {"anyOf", "oneOf", "closed_parent"}:
        supplied["query"] = base["query"] = "priority"
    if shape == "fastmcp":
        base = {"customer_id": "fixture", "payload": [{"priority": 1, "new_filter": {"label": "fixture"}}]}
        supplied = {**base, "payload": [{**base["payload"][0], "apiKey": "fixture-forgery"}]}
    if shape == "closed_parent":
        # 根明确封闭，子分支的 true 不扩大其范围。
        base.pop("region")
        base.pop("customer_id")
    if bound:
        assert "api-key" not in tool.mcp_input_schema["properties"]
        supplied.update({"api-key": "synthetic-forgery", "api_key": "synthetic-forgery"})
        base["api-key"] = "synthetic-injected"

    token = agent_context.set(AgentContext(agent_id="fixture", agent_name="Fixture", user_id=101))
    try:
        result = [item async for item in await tool.ainvoke(supplied)]
    finally:
        agent_context.reset(token)
        if mcp_client._AUDIT_TASKS:
            await asyncio.gather(*list(mcp_client._AUDIT_TASKS))

    if shape in {"fastmcp_auth", "external_ref", "recursive_ref"}:
        code = "CREDENTIAL_MAPPING_REQUIRED" if shape == "fastmcp_auth" else "MCP_ARGUMENTS_INVALID"
        assert result == [{"status": "error", "code": code}]
        assert received == []
        assert "auth_value" not in repr(tool.mcp_input_schema)
        return
    if bound:
        assert result == [{"status": "success", "result_visibility": "restricted", "reason": "fields_not_configured"}]
    else:
        assert result == [{"items": ["fixture"]}]
    assert received == [("list_items", base)]
    if shape == "fastmcp":
        nested = tool.mcp_input_schema["properties"]["payload"]["items"]
        assert nested["properties"]["priority"]["description"] == "Sort priority"
        assert nested["required"] == ["priority"]
        assert "$defs" not in tool.mcp_input_schema
    if shape in {"anyOf", "oneOf", "closed_parent"}:
        assert tool.mcp_input_schema["properties"]["page_token"] == {"type": "string"}
        assert "payload" in tool.mcp_input_schema["required"]
    expected_open = shape != "closed_parent"
    assert tool.mcp_input_schema.get("additionalProperties", True) is expected_open
    if shape in {"anyOf", "oneOf", "closed_parent"}:
        with pytest.raises(CredentialInvocationError) as error:
            mcp_profiles._public_mcp_arguments(
                {"query": "priority", "customer-id": "primary", "payload": [{"priority": "should-be-integer"}]},
                tool.mcp_input_schema,
            )
        assert error.value.code == "MCP_ARGUMENTS_INVALID"


@pytest.mark.parametrize(("schema_ref", "key"), [
    ("#/$defs/A~1B~0C", "A/B~C"),
    ("#/$defs/space%20name", "space name"),
])
def test_local_mcp_reference_uses_json_pointer_and_preserves_original_schema(schema_ref, key):
    schema = {"type": "object", "$defs": {key: {"type": "string"}}, "properties": {
        "query": {"$ref": schema_ref, "description": "Fixture business query"},
    }}
    original = json.dumps(schema)
    projected = mcp_profiles.business_mcp_schema(schema, set())
    assert projected["properties"]["query"] == {"type": "string", "description": "Fixture business query"}
    assert json.dumps(schema) == original


def test_mcp_profile_mapping_resolves_local_reference_without_exposing_the_mapped_object():
    schema = _fastmcp_nested_schema(authenticated=True)
    type_schema = {"properties": {"filters": {"type": "array"}}}
    mappings = compile_mappings(schema, type_schema, {"payload": "filters"})
    assert mappings == {"payload": "filters"}
    projected = model_schema(schema, SimpleNamespace(selection_mode="fixed", mappings=mappings))
    assert set(projected["properties"]) == {"customer_id"}
    assert "auth_value" not in repr(projected)


def test_unsupported_local_reference_siblings_cannot_replace_a_canonical_target():
    schema = {"type": "object", "$defs": {"Filter": {"type": "integer"}}, "properties": {
        "priority": {"$ref": "#/$defs/Filter", "type": "string"},
    }}
    with pytest.raises(CredentialInvocationError) as error:
        mcp_profiles._public_mcp_arguments({"priority": "fake"}, mcp_profiles.business_mcp_schema(schema, set()))
    assert error.value.code == "MCP_ARGUMENTS_INVALID"


def test_mcp_ref_cannot_fall_back_to_an_open_alternate_branch():
    schema = {"type": "object", "properties": {"payload": {"anyOf": [
        {"$ref": "https://example.test/protected.json"}, {"type": "object"},
    ]}}}
    with pytest.raises(CredentialInvocationError) as error:
        mcp_profiles._public_mcp_arguments({"payload": {}}, mcp_profiles.business_mcp_schema(schema, set()))
    assert error.value.code == "MCP_ARGUMENTS_INVALID"


def test_mcp_ref_siblings_cannot_hide_credential_markers_in_target():
    schema = {"type": "object", "$defs": {"Filter": {"type": "object", "properties": {
        "auth_value": {"type": "string", "writeOnly": True},
    }}}, "properties": {"payload": {"$ref": "#/$defs/Filter", "type": "object"}}}
    assert mcp_profiles.unbound_sensitive_args(schema, []) == {"payload"}


def test_unsupported_ref_with_changed_schema_identifier_is_not_resolved_as_root_relative():
    schema = {"type": "object", "$defs": {"Filter": {"type": "integer"}}, "properties": {
        "priority": {"$id": "https://example.test/new.json", "$ref": "#/$defs/Filter"},
    }}
    with pytest.raises(CredentialInvocationError) as error:
        mcp_profiles._public_mcp_arguments({"priority": 1}, mcp_profiles.business_mcp_schema(schema, set()))
    assert error.value.code == "MCP_ARGUMENTS_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize("mark", [{"writeOnly": True}, {"format": "password"}])
async def test_dynamic_authentication_schema_blocks_normal_mcp_preparation(mark):
    from app.models.mcp import McpToolCache

    schema = {"type": "object", "properties": {"query": {"type": "string"}},
              "additionalProperties": {"type": "string", **mark}}
    record = _cached_tool(schema)
    server = SimpleNamespace(id=record.server_id, sse_url="https://mcp.example.test/rpc")
    session = SimpleNamespace(get=AsyncMock(side_effect=lambda model, _: record if model is McpToolCache else None))
    tool = McpToolFactory.create_tool(record, verify_profile=True)
    assert "writeOnly" not in repr(tool.mcp_input_schema) and "password" not in repr(tool.mcp_input_schema)
    assert tool.mcp_input_schema.get("additionalProperties") != {"type": "string"}

    with pytest.raises(CredentialInvocationError) as error:
        await mcp_profiles.prepare_mcp_arguments(
            session, server, "list_items", {"query": "fixture", "ordinary_extension": "synthetic-value"},
            mcp_profiles.McpCredentialInvocation(record.id, None),
            user_info={"user_id": "101"}, trace_id=None, conversation_id=None,
        )
    assert error.value.code == "CREDENTIAL_MAPPING_REQUIRED"


@pytest.mark.asyncio
@pytest.mark.parametrize("declared_in_branch", [False, True])
async def test_legacy_mcp_choice_uses_only_names_not_declared_as_business_fields(monkeypatch, declared_in_branch):
    from app.models.mcp import McpToolCache
    from app.services.secrets import injection
    from app.services.secrets.locator import choice_field

    choice_key = choice_field("password")
    fallback_key = choice_field("mailkey")
    schema = {"type": "object", "properties": {
        "password": {"type": "string", "writeOnly": True},
        "mailkey": {"type": "string", "writeOnly": True},
    }}
    if declared_in_branch:
        schema["anyOf"] = [{"properties": {choice_key: {"type": "string"}}, "required": [choice_key]}]
    else:
        schema["properties"][choice_key] = {"type": "string"}
    record = _cached_tool(schema)
    record.credential_params = '["password", "mailkey"]'
    server = SimpleNamespace(id=record.server_id, sse_url="https://mcp.example.test/rpc")
    session = SimpleNamespace(get=AsyncMock(side_effect=lambda model, _: record if model is McpToolCache else None))
    choices_seen = []

    async def choose(choices, **kwargs):
        choices_seen.append(choices)
        return {name: "fixture-reference" for name in choices}

    async def resolve(**kwargs):
        return SimpleNamespace(ok=True, value=SimpleNamespace(reveal=lambda: "fixture-server-value"))

    monkeypatch.setattr(injection, "_resolve_choices", choose)
    monkeypatch.setattr(injection, "_max_inject_per_call", AsyncMock(return_value=2))
    monkeypatch.setattr(injection.secret_resolver, "resolve", resolve)
    monkeypatch.setattr(injection, "register_injected", lambda value: None)
    prepared = await mcp_profiles.prepare_mcp_arguments(
        session, server, "list_items", {choice_key: "c1", fallback_key: "c2"},
        mcp_profiles.McpCredentialInvocation(record.id, None),
        user_info={"user_id": "101"}, trace_id=None, conversation_id=None,
    )
    assert choices_seen == [{"mailkey": "c2"}]
    assert prepared.protect_payload
    assert prepared.arguments == {
        choice_key: "c1", "password": "fixture-server-value", "mailkey": "fixture-server-value",
    }


def test_mcp_one_of_retains_unique_branch_with_nested_business_extension():
    open_object = {"type": "object", "properties": {"known": {"type": "string"}}}
    closed_object = {**open_object, "additionalProperties": False}
    schema = {"type": "object", "properties": {"filter": {"oneOf": [
        {"type": "object", "properties": {"options": closed_object}, "required": ["options"]},
        {"type": "object", "properties": {"options": open_object}, "required": ["options"]},
    ]}}}
    projected = mcp_profiles.business_mcp_schema(schema, set())
    nested = {"filter": {"options": {"known": "kept", "dynamic": [{"value": 1, "api_key": "omit"}]}}}
    assert mcp_profiles._public_mcp_arguments(nested, projected) == {
        "filter": {"options": {"known": "kept", "dynamic": [{"value": 1}]}},
    }
    with pytest.raises(CredentialInvocationError) as error:
        mcp_profiles._public_mcp_arguments({"filter": {"options": {"known": "kept"}}}, projected)
    assert error.value.code == "MCP_ARGUMENTS_INVALID"


@pytest.mark.parametrize(("branches", "accepted", "rejected"), [
    ([
        {"type": "integer", "minimum": 0, "maximum": 9},
        {"type": "integer", "minimum": 10, "maximum": 99},
    ], [0, 9, 10, 12, 99], [-1, 100]),
    ([
        {"type": "number", "minimum": -10, "exclusiveMaximum": 0},
        {"type": "number", "exclusiveMinimum": 0, "maximum": 10},
    ], [-10, -1.5, 2.5, 10], [-11, 0, 11]),
    ([
        {"type": "string", "minLength": 1, "maxLength": 2},
        {"type": "string", "minLength": 3, "maxLength": 5},
    ], ["甲", "🤖甲", "甲乙丙", "甲乙丙丁戊"], ["", "甲乙丙丁戊己"]),
    ([
        {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 2},
        {"type": "array", "items": {"type": "integer"}, "minItems": 3, "maxItems": 4},
    ], [[1], [1, 2, 3], [1, 2, 3, 4]], [[], [1, 2, 3, 4, 5]]),
])
def test_mcp_one_of_matches_projected_numeric_and_length_constraints(branches, accepted, rejected):
    schema = {"type": "object", "properties": {
        "payload": {"type": "array", "items": {"type": "object", "properties": {
            "filter": {"oneOf": branches},
        }}},
    }}
    public = mcp_profiles.business_mcp_schema(schema, set())
    assert public["properties"]["payload"]["items"]["properties"]["filter"]["oneOf"] == branches

    for value in accepted:
        supplied = {"payload": [{"filter": value, "access_token": "synthetic-forgery"}]}
        assert mcp_profiles._public_mcp_arguments(supplied, public) == {"payload": [{"filter": value}]}
    for value in rejected:
        with pytest.raises(CredentialInvocationError) as error:
            mcp_profiles._public_mcp_arguments({"payload": [{"filter": value}]}, public)
        assert error.value.code == "MCP_ARGUMENTS_INVALID"


def test_mcp_one_of_length_checks_projected_array_and_retains_true_ambiguity():
    schema = {"type": "object", "properties": {
        "values": {"oneOf": [
            {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 2},
            {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 3},
        ]},
    }}
    public = mcp_profiles.business_mcp_schema(schema, set())
    assert mcp_profiles._public_mcp_arguments({"values": [1, 2, 3]}, public) == {"values": [1, 2, 3]}
    with pytest.raises(CredentialInvocationError) as error:
        mcp_profiles._public_mcp_arguments({"values": [1, 2]}, public)
    assert error.value.code == "MCP_ARGUMENTS_INVALID"
