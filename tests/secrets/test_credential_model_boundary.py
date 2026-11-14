import json

import pytest

from app.models.mcp import McpToolCache
from app.services.ai.tools import mcp_factory
from app.services.ai.tools.mcp_client import McpClientService

pytestmark = pytest.mark.no_infrastructure


def _tool():
    return McpToolCache(
        server_id="synthetic-server",
        tool_name="probe:mail",
        credential_params='["password"]',
        parameter_schema=json.dumps({
            "type": "object",
            "properties": {"password": {"type": "string"}, "folder": {"type": "string"}},
            "required": ["password"],
        }),
    )


async def test_credentialed_tool_exposes_only_platform_status_to_model(monkeypatch):
    async def inject(_tool, args, **kwargs):
        return {**args, "password": "synthetic-only"}

    async def remote(cls, *, arguments, progress_callback=None, **kwargs):
        assert arguments["password"] == "synthetic-only"
        if progress_callback:
            await progress_callback(1.0, 2.0, "synthetic-only")
        return {"remote": "c3ludGhldGljLW9ubHk="}

    monkeypatch.setattr(mcp_factory, "apply_mcp_arg_bindings", inject)
    monkeypatch.setattr(McpClientService, "call_remote_tool", classmethod(remote))
    tool = mcp_factory.McpToolFactory.create_tool(_tool())
    assert "password" not in tool.mcp_input_schema["properties"]
    stream = await tool.ainvoke({"folder": "inbox"})
    assert [part async for part in stream] == [{"status": "success", "result_visibility": "restricted"}]


@pytest.mark.parametrize("mode", ["structured", "json", "list", "empty", "plain", "echo", "oversize"])
def test_projection_only_exposes_configured_business_fields(mode):
    from app.services.secrets.mcp_model_result import project_model_result

    fields = ["messages[].subject", "messages[].sender.address"]
    fixture = {
        "subject": "Monthly report",
        "sender": {"address": "sender@example.test", "internal": "unselected"},
        "password": "fixture-only-password",
        "attachment": {"name": "private.pdf"},
    }
    expected = {"messages": [{"subject": "Monthly report", "sender": {"address": "sender@example.test"}}]}
    if mode == "structured":
        result = {
            "structured_content": {"messages": [fixture]},
            "content": "separate text must never be forwarded",
        }
    elif mode == "json":
        result = json.dumps({"messages": [fixture]})
    elif mode == "list":
        result, fields = json.dumps([fixture]), [field.replace("messages", "items") for field in fields]
        expected = {"items": expected["messages"]}
    elif mode == "empty":
        result, expected = json.dumps({"messages": []}), {"messages": []}
    elif mode == "plain":
        result, expected = "Subject: Monthly report", None
    elif mode == "echo":
        result = json.dumps({"messages": [{**fixture, "subject": "fixture-only-password"}]})
        expected["messages"][0]["subject"] = "fixture-only-password"
    else:
        result, expected = json.dumps({"messages": [{**fixture, "subject": "x" * 200_000}]}), None

    actual = project_model_result(result, fields, protected_values=("fixture-only-password",))
    if expected is None:
        assert actual == {"status": "success", "result_visibility": "restricted"}
    else:
        assert actual == {"status": "success", "result_visibility": "projected", "data": expected}


@pytest.mark.parametrize(("paths", "expected"), [
    (["$.account", "$.messages[*][1]"], {"account": "fixture", "messages": [[None, "First"], [None, "Second"]]}),
    (["$['messages'][*][0]", "$.messages[*][1]"], {"messages": [["09:12", "First"], ["14:03", "Second"]]}),
    (["$.messages[1][1]"], {"messages": [None, [None, "Second"]]}),
    (["$.messages[*][3]"], None),
    (["$.messages[*][2]"], {"messages": [[None, None, "fixture-only-password"], [None, None, "Fixture body"]]}),
])
def test_jsonpath_selects_only_the_approved_columns_of_mail_rows(paths, expected):
    from app.services.secrets.mcp_model_result import project_model_result

    result = {"structured_content": {
        "account": "fixture",
        "messages": [["09:12", "First", "fixture-only-password"], ["14:03", "Second", "Fixture body"]],
    }}
    actual = project_model_result(result, paths, protected_values=("fixture-only-password",))

    if expected is None:
        assert actual["result_visibility"] == "restricted"
    else:
        assert actual["result_visibility"] == "projected"
        assert actual["data"] == expected


@pytest.mark.parametrize(("result", "paths", "expected"), [
    ('{"messages":[{"subject":"First"}, {}, {"subject":"Third"}]}',
     ["$.messages[*].subject"], {"messages": [{"subject": "First"}, None, {"subject": "Third"}]}),
    ('{"messages":[{"from":"Sender"}]}', ["$.messages[*].subject"], None),
    ('{"messages":[["09:12"], []]}', ["$.messages[*][1]"], None),
    ('{"messages":[]}', ["$.messages[*].subject"], None),
    ('{"messages":[]}', ["$.messages[*]"], None),
    ('{"messages":[]}', ["$.messages"], {"messages": []}),
    ('{"messages":[{"subject":null}]}', ["$.messages[*].subject"], {"messages": [{"subject": None}]}),
    ('{"messages":[{"subject":"First"}, "other row"]}', ["$.messages[*].subject"],
     {"messages": [{"subject": "First"}]}),
    ('{"messages":[{"subject":"First","rating":NaN}]}', ["$.messages[*].subject"], None),
    ('{"messages":[{"subject":"First",}]}', ["$.messages[*].subject"], None),
    ('{"messages":[{"subject":"First"}]}', ["$.messages[*].subject", "$.messages["], None),
    ('{"messages":[{"subject":"First"}]}', [], None),
])
def test_jsonpath_visibility_requires_valid_json_and_matching_paths(result, paths, expected):
    from app.services.secrets.mcp_model_result import project_model_result

    actual = project_model_result(result, paths, protected_values=())
    assert actual.get("data") == expected


def test_jsonpath_supports_a_root_array():
    from app.services.secrets.mcp_model_result import project_model_result

    result = json.dumps([["09:12", "First"], ["14:03", "Second"]])
    actual = project_model_result(result, ["$[*][1]"], protected_values=())

    assert actual["data"] == {"items": [[None, "First"], [None, "Second"]]}


@pytest.mark.parametrize(("paths", "expected"), [
    (["$"], {"status": "ok", "messages": [["09:12", "First"], ["14:03", "Second"]]}),
    (["$.messages[*]"], {"messages": [["09:12", "First"], ["14:03", "Second"]]}),
    (["$.messages[-1]", "$.messages[:1]"], {"messages": [["09:12", "First"], ["14:03", "Second"]]}),
    (["$.messages[*]", "$.messages[0][0]"], {"messages": [["09:12", "First"], ["14:03", "Second"]]}),
    (["$['status', 'messages']"], {"status": "ok", "messages": [["09:12", "First"], ["14:03", "Second"]]}),
    (["$..messages[?(@[1] == 'Second' && length(@) == 2)][1]"], {"messages": [None, [None, "Second"]]}),
    (["$.messages[?match(@[1], 'F.*') || search(@[1], 'con')][0]"], {"messages": [["09:12"], ["14:03"]]}),
])
def test_standard_jsonpath_selects_business_json(paths, expected):
    from app.services.secrets.mcp_model_result import project_model_result

    source = {"status": "ok", "messages": [["09:12", "First"], ["14:03", "Second"]]}
    result = project_model_result({"structured_content": source}, paths, protected_values=())
    assert result["data"] == expected


def test_jsonpath_root_supports_nested_json_and_primitives():
    from app.services.secrets.mcp_model_result import project_model_result

    for source in ([{"邮件": "合成内容"}], "fixture", 0, False, None):
        result = project_model_result(json.dumps(source), ["$"], protected_values=())
        assert result["data"] == source


def test_jsonpath_root_and_wildcard_return_selected_credential_values():
    from app.services.secrets.mcp_model_result import project_model_result

    secret = "fixture-secret-only"
    value = {"messages": [{"subject": "Fixture", "metadata": {"password": secret}}]}
    for path in ("$", "$.messages[*]", "$..*"):
        result = project_model_result({"structured_content": value}, [path], protected_values=(secret,))
        assert result["result_visibility"] == "projected"
        assert result["data"] == value

    value = {"messages": [{"subject": "Fixture", "body": secret}]}
    result = project_model_result(
        {"structured_content": value}, ["$.messages[?@.body == 'fixture-secret-only'].subject"],
        protected_values=(secret,),
    )
    assert result["data"] == {"messages": [{"subject": "Fixture"}]}



@pytest.mark.parametrize("path", ["$", "$.*", "$..*", "$['prefix-fixture-secret-only-suffix']"])
def test_jsonpath_preserves_selected_keys_even_when_they_match_credentials(path):
    from app.services.secrets.mcp_model_result import project_model_result

    result = project_model_result(
        {"structured_content": {"prefix-fixture-secret-only-suffix": "Fixture"}}, [path],
        protected_values=("fixture-secret-only",),
    )
    assert result["data"] == {"prefix-fixture-secret-only-suffix": "Fixture"}


@pytest.mark.parametrize(("predicate", "matches"), [
    ("@.candidate == @.password", True),
    ("!match(@.candidate, '.*')", False),
    ("@.candidate != 'other'", True),
    ("!@.variants[?@ == 'fixture-secret-only']", False),
])
def test_jsonpath_predicates_evaluate_original_values(predicate, matches):
    from app.services.secrets.mcp_model_result import project_model_result

    secret = "fixture-secret-only"
    source = {"messages": [{
        "subject": "Fixture", "candidate": secret, "password": secret, "variants": [secret],
    }]}
    result = project_model_result(
        {"structured_content": source}, [f"$.messages[?{predicate}].subject"],
        protected_values=(secret,),
    )
    if matches:
        assert result["data"] == {"messages": [{"subject": "Fixture"}]}
    else:
        assert result.reason == "no_matching_fields"


@pytest.mark.parametrize(("headers", "subject"), [
    ({"Authorization": "Bearer fixture-bearer-token"}, "Mail from fixture"),
    ({"Authorization": "Bearer fixture-bearer-token"}, "fixture-bearer-token"),
    ({"Cookie": "session=fixture-session-token; session=fixture-rotated-token"}, "fixture-session-token"),
    ({"X-Auth": "custom value"}, "Mail from fixture"),
])
def test_transport_credentials_do_not_filter_selected_results(headers, subject):
    from app.services.secrets.mcp_model_result import project_model_result

    result = project_model_result(
        {"structured_content": {"subject": subject}}, ["subject"],
        protected_values=(), auth_headers=headers,
    )
    assert result["result_visibility"] == "projected"
    assert result["data"] == {"subject": subject}


@pytest.mark.parametrize("fields", [
    ["*"], ["messages[]", "messages[].subject"],
    ["messages.subject", "messages[].subject"],
    ["$.messages["], ["$.messages[0,]"],
    ["$.messages[?unknown(@)]"], ["$.messages[?count(@.subject) > ]"],
])
def test_result_configuration_requires_valid_unambiguous_paths(fields):
    from app.services.secrets.mcp_model_result import compile_result_fields

    with pytest.raises(ValueError):
        compile_result_fields(fields)


async def test_credentialed_tool_does_not_pass_upstream_exception_to_model(monkeypatch):
    async def inject(_tool, args, **kwargs):
        return {**args, "password": "synthetic-only"}

    async def remote(cls, **kwargs):
        raise RuntimeError("synthetic-only in remote error")

    monkeypatch.setattr(mcp_factory, "apply_mcp_arg_bindings", inject)
    monkeypatch.setattr(McpClientService, "call_remote_tool", classmethod(remote))
    tool = mcp_factory.McpToolFactory.create_tool(_tool())
    stream = await tool.ainvoke({})
    assert [part async for part in stream] == [
        {"status": "error", "code": "CREDENTIAL_TOOL_FAILED"}
    ]
