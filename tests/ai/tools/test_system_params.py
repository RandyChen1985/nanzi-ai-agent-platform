"""系统级参数：模型不可见，运行时强制注入。"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.models.tool import SysApiTool
from app.services.ai.tools.generic_api import GenericApiToolFactory
from app.services.ai.tools.system_params import (
    SystemParamConfigError,
    apply_system_params,
    parse_system_params,
    validate_system_params,
)
from app.services.ai.tools.user_placeholders import UserPlaceholderError, resolve_user_variables

pytestmark = pytest.mark.no_infrastructure

USER_INFO = {"user_id": "42", "user_name": "alice", "dept_code": "D001"}


def _schema():
    return {
        "properties": {"amount": {"type": "number"}},
        "x-nanzi-system-params": [
            {
                "name": "applicant",
                "type": "string",
                "value": "{user.user_name}",
                "description": "申请人",
            }
        ],
    }


def _tool(schema, *, method="POST", url="https://api.test/refund"):
    return SysApiTool(
        name="refund",
        method=method,
        url_template=url,
        headers=json.dumps({}),
        parameter_schema=schema,
    )


def test_parse_system_params_reads_valid_entries():
    specs = parse_system_params(_schema())

    assert [(s.name, s.type, s.value) for s in specs] == [
        ("applicant", "string", "{user.user_name}")
    ]
    assert specs[0].description == "申请人"


def test_validate_rejects_duplicate_and_collision_with_normal_param():
    duplicate = {
        "x-nanzi-system-params": [
            {"name": "applicant", "value": "{user.user_name}"},
            {"name": "applicant", "value": "{user.real_name}"},
        ]
    }
    with pytest.raises(SystemParamConfigError, match="重复"):
        validate_system_params(duplicate)

    collision = {
        "properties": {"applicant": {"type": "string"}},
        "x-nanzi-system-params": [
            {"name": "applicant", "value": "{user.user_name}"}
        ],
    }
    with pytest.raises(SystemParamConfigError, match="重名"):
        validate_system_params(collision)


@pytest.mark.parametrize(
    "entry, message",
    [
        ({"name": "", "value": "x"}, "缺少名称"),
        ({"name": "1bad", "value": "x"}, "不合法"),
        ({"name": "applicant", "value": ""}, "缺少固定值"),
        ({"name": "applicant", "value": "x", "type": "object"}, "类型不支持"),
    ],
)
def test_validate_rejects_invalid_entries(entry, message):
    with pytest.raises(SystemParamConfigError, match=message):
        validate_system_params({"x-nanzi-system-params": [entry]})


def test_apply_replaces_user_variable_and_overrides_model_value():
    variables = resolve_user_variables(USER_INFO)

    prepared = apply_system_params(
        _schema(),
        {"amount": 100, "applicant": "mallory"},
        variables,
    )

    assert prepared == {"amount": 100, "applicant": "alice"}


def test_apply_raises_for_missing_variable():
    with pytest.raises(UserPlaceholderError):
        apply_system_params(
            _schema(),
            {"amount": 100},
            resolve_user_variables({"user_id": "42"}),
        )


def test_apply_coerces_literal_types():
    schema = {
        "x-nanzi-system-params": [
            {"name": "applicant", "type": "string", "value": "fixed"},
            {"name": "tenant_id", "type": "integer", "value": "7"},
            {"name": "score", "type": "number", "value": "1.5"},
            {"name": "active", "type": "boolean", "value": "true"},
        ]
    }

    prepared = apply_system_params(schema, {}, {})

    assert prepared == {
        "applicant": "fixed",
        "tenant_id": 7,
        "score": 1.5,
        "active": True,
    }


def test_system_params_are_not_in_model_schema():
    config = _tool(_schema())
    tool = GenericApiToolFactory.create_tool(config)

    assert "applicant" not in tool.args_schema.model_fields
    assert "applicant" not in json.dumps(
        tool.args_schema.model_json_schema(), ensure_ascii=False
    )


@pytest.mark.asyncio
async def test_perform_request_injects_system_param_into_json_body():
    config = _tool(_schema())
    capture = {}
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as request:
        request.return_value = httpx.Response(200, json={"ok": True})

        await GenericApiToolFactory._perform_request(
            config,
            {"amount": 100, "applicant": "mallory"},
            user_info=USER_INFO,
            capture=capture,
        )

        args, kwargs = request.call_args

    assert args[0] == "POST"
    assert kwargs["json"] == {"amount": 100, "applicant": "alice"}
    assert capture["method"] == "POST"
    assert capture["url"] == "https://api.test/refund"
    assert capture["body"] == {"amount": 100, "applicant": "alice"}
    assert capture["query"] == {}


@pytest.mark.asyncio
async def test_perform_request_injects_system_param_into_path():
    schema = {
        "properties": {},
        "x-nanzi-system-params": [
            {"name": "user_id", "type": "integer", "value": "{user.id}"}
        ],
    }
    config = _tool(
        schema,
        method="GET",
        url="https://api.test/users/{user_id}",
    )
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as get:
        get.return_value = httpx.Response(200, json={"ok": True})

        await GenericApiToolFactory._perform_request(
            config,
            {},
            user_info=USER_INFO,
        )

        args, kwargs = get.call_args

    assert args[0] == "https://api.test/users/42"
    assert kwargs["params"] == {}


@pytest.mark.asyncio
async def test_perform_request_injects_system_param_into_query():
    config = _tool(_schema(), method="GET", url="https://api.test/refund")
    capture = {}
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as get:
        get.return_value = httpx.Response(200, json={"ok": True})

        await GenericApiToolFactory._perform_request(
            config,
            {"amount": 100},
            user_info=USER_INFO,
            capture=capture,
        )

        args, kwargs = get.call_args

    assert args[0] == "https://api.test/refund"
    assert kwargs["params"] == {"amount": 100, "applicant": "alice"}
    assert capture["query"] == {"amount": 100, "applicant": "alice"}
    assert "applicant=alice" in capture["url"]


@pytest.mark.asyncio
async def test_flat_schema_extension_is_not_exposed_as_normal_param():
    schema = {
        "amount": {"type": "number"},
        "x-nanzi-system-params": [
            {"name": "applicant", "value": "{user.user_name}"}
        ],
    }
    tool = GenericApiToolFactory.create_tool(_tool(schema))

    assert "x-nanzi-system-params" not in tool.args_schema.model_fields
    assert "amount" in tool.args_schema.model_fields
