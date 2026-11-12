"""模型身份口径统一：真实下发模型、工具卡片与审计轨迹必须是同一个名字。

用户截图为证提问：「外面输入框选了 gpt-oss-120b，为什么执行链路里还是
DeepSeek-V4-Flash」。与 temperature.py 里记录的历史陷阱同构：真实下发给模型的
是会话级覆盖模型（`execution_options["model"]`），卡片和审计轨迹读的却是智能体
版本配置的模型，于是排查时看到的名字和实际生效的名字对不上。
"""

from types import SimpleNamespace

import pytest

from app.core.context import set_execution_options_context
from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture(autouse=True)
def _clean_execution_options_context():
    set_execution_options_context({})
    yield
    set_execution_options_context({})


def _runner(model_name: str = "deepseek-v4-flash") -> AssistantAgentRunner:
    runner = object.__new__(AssistantAgentRunner)
    runner.config = SimpleNamespace(
        agent_name="Assistant",
        model_name=model_name,
        temperature=0.0,
    )
    runner.step_counter = 1
    return runner


def _observe(runner: AssistantAgentRunner, target_tool=None):
    return runner._build_tool_observation(
        tool_id="bash-1",
        tool_name="Bash",
        tool_args={"command": "ping -c 4 baidu.com"},
        tool_output="4 packets transmitted",
        duration_tool=12,
        target_tool=target_tool,
        tool_index=0,
        tool_result_state="success",
    )


def test_resolve_effective_model_name_priority():
    from app.services.ai.model_override import resolve_effective_model_name

    assert resolve_effective_model_name(
        tool_model="tool-model",
        recorded_model="recorded-model",
        session_model="session-model",
        config_model="config-model",
        global_model="global-model",
    ) == "tool-model"
    assert resolve_effective_model_name(
        recorded_model="recorded-model",
        session_model="session-model",
        config_model="config-model",
    ) == "recorded-model"
    assert resolve_effective_model_name(
        session_model="session-model",
        config_model="config-model",
    ) == "session-model"
    assert resolve_effective_model_name(config_model="config-model") == "config-model"
    assert resolve_effective_model_name(global_model="global-model") == "global-model"
    assert resolve_effective_model_name() == ""


def test_blank_values_do_not_shadow_fallbacks():
    """空串/纯空白不是有效模型名，不能盖掉后面的候选值。"""
    from app.services.ai.model_override import resolve_effective_model_name

    assert resolve_effective_model_name(
        tool_model="   ",
        session_model="",
        config_model="config-model",
    ) == "config-model"


def test_tool_observation_reports_session_override_model():
    set_execution_options_context({"model": "gpt-oss-120b"})

    result = _observe(_runner(model_name="deepseek-v4-flash"))

    assert result["log"]["model"] == "gpt-oss-120b"
    assert result["trace"].model == "gpt-oss-120b"


def test_tool_observation_prefers_recorded_effective_model():
    """真实下发模型一旦记录（注册表别名解析后的 id），就该盖过会话别名与版本模型。"""

    runner = _runner(model_name="deepseek-v4-flash")
    runner._session_effective_model = "gpt-oss-120b@v1"
    set_execution_options_context({"model": "gpt-oss-120b"})

    result = _observe(runner)

    assert result["log"]["model"] == "gpt-oss-120b@v1"


def test_tool_observation_ignores_legacy_tool_runtime_model():
    """工具级模型覆盖已下架：AgentScope 无按工具换模型的落点，卡片不应再报它。"""
    set_execution_options_context({"model": "gpt-oss-120b"})
    tool = SimpleNamespace(
        _runtime_config=SimpleNamespace(temperature=0.9, model_name="tool-model")
    )

    result = _observe(_runner(model_name="deepseek-v4-flash"), target_tool=tool)

    assert result["log"]["model"] == "gpt-oss-120b"


def test_tool_observation_falls_back_to_agent_version_model():
    result = _observe(_runner(model_name="deepseek-v4-flash"))

    assert result["log"]["model"] == "deepseek-v4-flash"


def test_record_effective_model_reads_handle_model_name():
    runner = _runner()
    handle = SimpleNamespace(model_name=" gpt-oss-120b ", temperature=0.2)

    runner._record_effective_model(handle)

    assert runner._session_effective_model == "gpt-oss-120b"


def test_record_effective_model_ignores_handle_without_model():
    runner = _runner()

    runner._record_effective_model(SimpleNamespace(temperature=0.2))

    assert getattr(runner, "_session_effective_model", None) is None
