"""API 工具参数中的用户变量占位符。

元数据行级权限已有 ``{user.user_name}`` 一类写法；工具参数复用同一套变量口径，
但必须保证：敏感扩展字段不暴露、integer 参数保留数字类型、取不到值时明确报错
而不是发空字符串。
"""
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.context import AgentContext, agent_context
from app.models.tool import SysApiTool
from app.services.ai.tools.generic_api import GenericApiToolFactory
from app.services.ai.tools.user_placeholders import (
    UserPlaceholderError,
    current_user_variables,
    replace_headers_user_placeholders,
    replace_user_placeholders,
    resolve_user_variables,
)
from app.utils.context import current_user_info

pytestmark = pytest.mark.no_infrastructure


USER_INFO = {
    "user_id": "42",
    "user_name": "alice",
    "real_name": "Alice Chen",
    "role": "user",
    "dept_code": "D001",
    "org_path": "yovole/sh/dc1",
    "extra_data": json.dumps(
        {"region": "east", "password": "secret", "api_token": "token-value"}
    ),
}


def test_resolve_user_variables_flattens_extra_data_and_drops_sensitive_keys():
    variables = resolve_user_variables(USER_INFO)

    assert variables["id"] == 42
    assert variables["user_id"] == 42
    assert variables["user_name"] == "alice"
    assert variables["dept_code"] == "D001"
    assert variables["region"] == "east"
    assert "password" not in variables
    assert "api_token" not in variables


def test_replace_keeps_native_type_for_whole_placeholder():
    variables = resolve_user_variables(USER_INFO)

    assert replace_user_placeholders(
        "{user.id}", variables, expected_type="integer"
    ) == 42
    assert replace_user_placeholders(
        "{user.id}", variables, expected_type="string"
    ) == "42"
    assert replace_user_placeholders("prefix-{user.user_name}", variables) == "prefix-alice"


def test_replace_handles_nested_values_and_headers():
    variables = resolve_user_variables(USER_INFO)

    value = {
        "applicant": "{user.real_name}",
        "filters": [{"dept": "{user.dept_code}"}],
    }
    assert replace_user_placeholders(value, variables) == {
        "applicant": "Alice Chen",
        "filters": [{"dept": "D001"}],
    }
    assert replace_headers_user_placeholders(
        {"X-User": "{user.user_name}", "X-Trace": "fixed"}, variables
    ) == {"X-User": "alice", "X-Trace": "fixed"}


def test_missing_variable_raises_instead_of_sending_empty_value():
    with pytest.raises(UserPlaceholderError) as exc:
        replace_user_placeholders("{user.missing}", resolve_user_variables(USER_INFO))

    assert "{user.missing}" in str(exc.value)


def test_current_user_variables_prefers_agent_context_dimensions():
    token = agent_context.set(
        AgentContext(
            agent_id="agent-1",
            agent_name="Agent",
            user_dimensions={
                "id": 7,
                "user_name": "bob",
                "user_id": None,
                "region": "west",
                "api_key": "should-be-dropped",
            },
        )
    )
    try:
        variables = current_user_variables()
    finally:
        agent_context.reset(token)

    assert variables["id"] == 7
    assert variables["user_id"] == 7
    assert variables["user_name"] == "bob"
    assert variables["region"] == "west"
    assert "api_key" not in variables


def test_create_tool_hides_placeholder_default_from_model_schema():
    config = SysApiTool(
        name="refund",
        method="POST",
        url_template="http://api.example.com/refund",
        parameter_schema={
            "properties": {
                "reason": {
                    "type": "string",
                    "required": False,
                    "default": "{user.real_name}",
                    "description": "退款原因",
                },
                "amount": {"type": "integer", "required": False, "default": 100},
                "meta": {
                    "type": "object",
                    "required": False,
                    "default": {"applicant": "{user.user_name}"},
                },
            }
        },
    )

    tool = GenericApiToolFactory.create_tool(config)
    fields = tool.args_schema.model_fields

    assert fields["reason"].default is None
    assert "自动填充" in fields["reason"].description
    assert fields["amount"].default == 100
    assert fields["meta"].default is None
    # 模型看到的 schema 里不能出现 {user.real_name}，否则容易照抄
    assert "{user.real_name}" not in json.dumps(
        tool.args_schema.model_json_schema(), ensure_ascii=False
    )


@pytest.mark.asyncio
async def test_perform_request_fills_placeholder_default_and_headers():
    config = SysApiTool(
        name="refund",
        method="POST",
        url_template="http://api.example.com/orders/{order_id}/refund",
        headers=json.dumps({"X-User": "{user.user_name}"}),
        parameter_schema={
            "properties": {
                "order_id": {
                    "type": "string",
                    "required": False,
                    "default": "{user.id}",
                },
                "amount": {"type": "integer", "required": False, "default": 100},
                "reason": {
                    "type": "string",
                    "required": False,
                    "default": "{user.real_name}",
                },
                "meta": {
                    "type": "object",
                    "required": False,
                    "default": {"applicant": "{user.user_name}"},
                },
            }
        },
    )

    with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = httpx.Response(200, json={"ok": True})

        await GenericApiToolFactory._perform_request(
            config,
            # 空白串按「没填」处理，回落占位符默认值；amount 显式传值应原样保留
            {"amount": 100, "reason": "   "},
            user_info=USER_INFO,
        )

        args, kwargs = mock_request.call_args

    assert args[0] == "POST"
    assert args[1] == "http://api.example.com/orders/42/refund"
    assert kwargs["headers"]["X-User"] == "alice"
    assert kwargs["json"]["reason"] == "Alice Chen"
    assert kwargs["json"]["amount"] == 100
    assert kwargs["json"]["meta"] == {"applicant": "alice"}


@pytest.mark.asyncio
async def test_perform_request_raises_for_unknown_variable():
    config = SysApiTool(
        name="search",
        method="GET",
        url_template="http://api.example.com/search",
        parameter_schema={"properties": {"q": {"type": "string"}}},
    )

    with pytest.raises(UserPlaceholderError):
        await GenericApiToolFactory._perform_request(
            config,
            {"q": "{user.unknown}"},
            user_info=USER_INFO,
        )


def test_current_user_variables_falls_back_to_current_user_info_without_agent_context():
    token_ctx = agent_context.set(None)
    token_user = current_user_info.set(USER_INFO)
    try:
        variables = current_user_variables()
    finally:
        current_user_info.reset(token_user)
        agent_context.reset(token_ctx)

    assert variables["user_name"] == "alice"
    assert variables["region"] == "east"


@pytest.mark.asyncio
async def test_tool_ainvoke_fills_placeholder_default_from_current_user():
    config = SysApiTool(
        name="whoami",
        method="GET",
        url_template="http://api.example.com/users/{user_id}",
        parameter_schema={
            "properties": {
                "user_id": {
                    "type": "integer",
                    "required": False,
                    "default": "{user.id}",
                },
                "name": {
                    "type": "string",
                    "required": False,
                    "default": "{user.user_name}",
                },
            }
        },
    )
    tool = GenericApiToolFactory.create_tool(config)

    token_ctx = agent_context.set(None)
    token_user = current_user_info.set(USER_INFO)
    try:
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = httpx.Response(200, json={"ok": True})
            await tool.ainvoke({})
            args, kwargs = mock_get.call_args
    finally:
        current_user_info.reset(token_user)
        agent_context.reset(token_ctx)

    assert args[0] == "http://api.example.com/users/42"
    assert kwargs["params"] == {"name": "alice"}


@pytest.mark.asyncio
async def test_execute_request_returns_explicit_error_for_unknown_variable():
    config = SysApiTool(
        name="search",
        method="GET",
        url_template="http://api.example.com/search",
        parameter_schema={"properties": {"q": {"type": "string"}}},
    )

    result = await GenericApiToolFactory._execute_request(
        config,
        {"q": "{user.unknown}"},
    )

    assert result.startswith("[Execution Error]")
    assert "用户变量" in result
