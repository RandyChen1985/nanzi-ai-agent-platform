"""工具异常「回显给模型」的最后一公里脱敏 + 试跑占位符错误收敛。

两条实锤缺陷：

1. **MCP 异常只堵了 HTTP 层**。``_direct_http_rpc`` 会把错误换成脱敏文本，但
   ``call_remote_tool`` 外层仍是 ``call_error = redact_value(...)`` 之后裸 ``raise``，
   原始异常一路冒到 ``RuntimeToolError(f"Tool '{name}' failed: {exc}")``——那句话会
   进工具结果、模型上下文和轨迹表。流式调用更麻烦：凭据是登记在子 Task 的
   Context 里的，要等父级 finally 才回收，那时再脱敏已经抓不到原值。

2. **试跑缺用户变量直接 500**。``prepare_params`` 抛的是
   ``UserPlaceholderError(ValueError)``，而试跑端点只 ``except ScriptToolError``；
   两个异常没有继承关系，于是「用户没填邮箱」变成 500。

覆盖点：
- ``AgentScopeRuntimeError`` 基类构造统一脱敏（所有 RuntimeToolError 子类受益）；
- 工具结果里那条纯文本错误片（不走异常类，单独脱敏）；
- ``ScriptToolRunner.prepare_params`` 把占位符缺值收敛成 ``ScriptToolError``，
  且变量齐全时行为不变。
"""
from __future__ import annotations

import pytest

from app.services.secrets.redact import register_injected, reset_injected

SECRET = "sk-live-9f3a7c1e5b2d"


@pytest.fixture(autouse=True)
def _registered_secret():
    reset_injected()
    register_injected(SECRET)
    try:
        yield
    finally:
        reset_injected()


def _script_config():
    from app.services.ai.tools.script_tool_runtime import ScriptToolConfig

    return ScriptToolConfig(
        name="demo_script",
        code="print('hi')",
        parameter_schema={
            "type": "object",
            "properties": {
                "applicant": {"type": "string", "default": "{user.email}"},
            },
        },
    )


def _runner(user_info: dict):
    from app.services.ai.tools.script_tool_runtime import ScriptToolRunner

    return ScriptToolRunner(_script_config(), workdir="/tmp/preview-test", user_info=user_info)


def test_runtime_tool_error_message_is_redacted():
    from app.services.ai.runtime.agentscope.errors import RuntimeToolError

    exc = RuntimeToolError(f"Tool 'mcp_demo' failed: HTTP 401 invalid token: {SECRET}")

    assert SECRET not in str(exc)
    assert "***" in str(exc)
    # 非敏感部分要保留，否则排查时看不出是哪个工具、什么错
    assert "mcp_demo" in str(exc) and "401" in str(exc)


def test_runtime_tool_error_keeps_message_when_nothing_to_redact():
    from app.services.ai.runtime.agentscope.errors import RuntimeToolError

    exc = RuntimeToolError("Tool 'x' failed: connection reset")

    assert str(exc) == "Tool 'x' failed: connection reset"
    assert exc.kind == "tool"


def test_tool_error_text_helper_redacts_plain_result_text():
    from app.services.ai.runtime.agentscope.tools import _redact_tool_error_text

    text = _redact_tool_error_text(Exception(f"url?access_token={SECRET}"))

    assert SECRET not in text
    assert "access_token=" in text


def test_prepare_params_converts_missing_user_variable_to_script_tool_error():
    from app.services.ai.tools.script_tool_runtime import ScriptToolError
    from app.services.ai.tools.user_placeholders import UserPlaceholderError

    runner = _runner({"user_id": 7, "user_name": "tester", "email": ""})

    with pytest.raises(ScriptToolError) as excinfo:
        runner.prepare_params({})

    # 收敛成 ScriptToolError 才算修好：试跑端点 / 工具调用层都只认它
    assert not isinstance(excinfo.value, UserPlaceholderError)
    assert "{user.email}" in str(excinfo.value)
    # 原异常要留在链上，方便定位
    assert isinstance(excinfo.value.__cause__, UserPlaceholderError)


def test_prepare_params_still_substitutes_when_variable_present():
    runner = _runner(
        {"user_id": 7, "user_name": "tester", "email": "tester@example.com"}
    )

    prepared = runner.prepare_params({})

    # 补齐 email 后要正常替换，别把「收敛异常」做成「一律报错」
    assert prepared["applicant"] == "tester@example.com"
