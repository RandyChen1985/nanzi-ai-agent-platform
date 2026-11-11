"""MCP 工具的「模型挑一条」通道：模型真的看得见那个编号字段吗。

这里钉的是一个曾经静默失效的接线错误，而不是逻辑错误：

``McpToolFactory.create_tool`` 生成了含 ``secret_choice_<参数名>`` 的
``args_schema``，但同一函数末尾又把**远端原始 schema** 挂到
``tool.mcp_input_schema``。而真正送给模型的两条路径
（``_schema_from_legacy_tool``、``legacy_tools_to_openai_schemas``）都**优先读
mcp_input_schema**。于是：

* 平台侧逻辑全对，注入层也确实会在未命中时采纳编号；
* 但模型拿到的参数表里既没有 ``mailkey``、也没有 ``secret_choice_mailkey``，
  它只会回答「凭据参数我看不到，也猜不出来」；
* ``on_miss=model`` 一次都没生效过，而配置页看起来一切正常。

所以断言必须落在**模型实际看到的那份 schema** 上，不能只断言 args_schema。
"""
import json
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.no_infrastructure

REMOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "username": {"type": "string", "title": "Username"},
        "mailkey": {"type": "string", "title": "Mailkey"},
        # 嵌套/枚举这类结构 args_schema 表达不了，是保留 mcp_input_schema 的理由
        "subject": {"type": "string", "default": ""},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["username", "mailkey"],
    "title": "send_mailArguments",
}


def _record(schema: dict | None = None):
    return SimpleNamespace(
        tool_name="echo:send_mail",
        tool_description="发邮件（验收用）",
        parameter_schema=json.dumps(schema or REMOTE_SCHEMA, ensure_ascii=False),
        credential_params=json.dumps(["mailkey"]),
        server_id="server-1",
    )


@pytest.fixture(scope="module")
def tool():
    from app.services.ai.tools.mcp_factory import McpToolFactory

    return McpToolFactory.create_tool(_record())


def test_mcp_input_schema_exposes_choice_field(tool):
    """模型能看到的参数表里必须有编号字段——否则「让模型挑一条」是句空话。"""
    schema = tool.mcp_input_schema
    assert "secret_choice_mailkey" in schema["properties"]
    assert schema["properties"]["secret_choice_mailkey"]["type"] == "string"
    assert "list_my_credentials" in schema["properties"]["secret_choice_mailkey"]["description"]


def test_mcp_input_schema_hides_the_credential_param(tool):
    """凭据参数本身仍然不可见：值只能由平台注入。"""
    schema = tool.mcp_input_schema
    assert "mailkey" not in schema["properties"]
    # required 里也不能留着它，否则等于「必填但没有定义」
    assert "mailkey" not in schema.get("required", [])


def test_args_schema_still_has_the_field(tool):
    """本地调用校验用的是 args_schema，两边必须一致。"""
    properties = tool.args_schema.model_json_schema()["properties"]
    assert "secret_choice_mailkey" in properties
    assert "mailkey" not in properties


def test_what_the_model_actually_receives_has_the_choice_field(tool):
    """闭环断言：走真实渲染路径，看模型最终收到的 parameters。"""
    from app.services.ai.runtime.agentscope.chat import legacy_tools_to_openai_schemas

    schemas = legacy_tools_to_openai_schemas([tool])
    assert len(schemas) == 1
    parameters = schemas[0]["function"]["parameters"]
    assert "secret_choice_mailkey" in parameters["properties"]
    assert "mailkey" not in parameters["properties"]


def test_runtime_spec_carries_the_choice_field(tool):
    """AgentScope 运行时 entrypoint 走的是 ``_schema_from_legacy_tool``。"""
    from app.services.ai.runtime.agentscope.tools import runtime_tool_spec_from_legacy_tool

    spec = runtime_tool_spec_from_legacy_tool(tool, source_type="mcp")
    assert "secret_choice_mailkey" in spec.parameters_schema["properties"]


def test_flat_schema_form_is_handled_too():
    """扁平形态（顶层就是参数表）同样不能把凭据参数留给模型。"""
    from app.services.ai.tools.mcp_factory import McpToolFactory

    flat = {
        "username": {"type": "string"},
        "mailkey": {"type": "string"},
    }
    created = McpToolFactory.create_tool(_record(flat))
    schema = created.mcp_input_schema
    assert "mailkey" not in schema
    assert "secret_choice_mailkey" in schema
