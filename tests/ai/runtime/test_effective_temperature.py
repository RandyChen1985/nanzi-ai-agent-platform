"""采样温度口径统一：真实下发值、工具卡片与审计轨迹必须是同一个数字。

用户截图为证提问：「输入框把温度调到 0.3，工具卡片为什么还显示温度 0，这个 0 是真的吗」。
根因是卡片读的是智能体版本温度，而真实下发给模型的是会话级覆盖温度，
于是排查时看到的数字和实际生效的数字对不上。
"""

from types import SimpleNamespace

import pytest

from app.core.context import set_debug_context
from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture(autouse=True)
def _clean_debug_context():
    set_debug_context({})
    yield
    set_debug_context({})


def _runner(temperature: float = 0.0) -> AssistantAgentRunner:
    runner = object.__new__(AssistantAgentRunner)
    runner.config = SimpleNamespace(
        agent_name="Assistant",
        model_name="test-model",
        temperature=temperature,
    )
    runner.step_counter = 1
    return runner


def _observe(runner: AssistantAgentRunner, target_tool=None):
    return runner._build_tool_observation(
        tool_id="bash-1",
        tool_name="Bash",
        tool_args={"command": "uptime"},
        tool_output="up 1 day",
        duration_tool=12,
        target_tool=target_tool,
        tool_index=0,
        tool_result_state="success",
    )


def test_resolve_effective_temperature_priority():
    from app.services.ai.temperature import resolve_effective_temperature

    assert resolve_effective_temperature(
        tool_temperature=0.9,
        session_temperature=0.3,
        config_temperature=0.1,
        global_temperature=0.5,
    ) == 0.9
    assert resolve_effective_temperature(
        session_temperature=0.3,
        config_temperature=0.1,
        global_temperature=0.5,
    ) == 0.3
    assert resolve_effective_temperature(
        config_temperature=0.1,
        global_temperature=0.5,
    ) == 0.1
    assert resolve_effective_temperature(global_temperature=0.5) == 0.5
    assert resolve_effective_temperature() == 0.0


def test_zero_temperature_is_a_real_value_not_an_absent_one():
    from app.services.ai.temperature import resolve_effective_temperature

    assert resolve_effective_temperature(
        session_temperature=0.0,
        config_temperature=0.7,
    ) == 0.0
    assert resolve_effective_temperature(
        tool_temperature=0.0,
        session_temperature=0.7,
    ) == 0.0


def test_tool_observation_reports_session_override_as_effective_temperature():
    set_debug_context({"temperature": 0.3})

    result = _observe(_runner(temperature=0.0))

    assert result["log"]["temperature"] == 0.3
    assert result["trace"].temperature == 0.3


def test_tool_observation_keeps_tool_runtime_temperature_over_session_override():
    set_debug_context({"temperature": 0.3})
    tool = SimpleNamespace(
        _runtime_config=SimpleNamespace(temperature=0.9, model_name="tool-model")
    )

    result = _observe(_runner(temperature=0.0), target_tool=tool)

    assert result["log"]["temperature"] == 0.9
    assert result["log"]["model"] == "tool-model"


def test_tool_observation_falls_back_to_agent_version_temperature():
    result = _observe(_runner(temperature=0.25))

    assert result["log"]["temperature"] == 0.25


def test_tool_observation_prefers_recorded_effective_temperature():
    """主模型真实下发温度（可能来自全局兜底）一旦记录，就该盖过版本温度。"""

    runner = _runner(temperature=0.0)
    runner._session_effective_temperature = 0.55

    result = _observe(runner)

    assert result["log"]["temperature"] == 0.55
