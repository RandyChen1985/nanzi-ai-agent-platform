"""挂起快照 runner_context 的旧字段名兼容。

升级前写入 pending store 的快照用 ``debug_options`` 作为 key；字段更名后
恢复执行必须继续读到旧的模型/资源范围/超时覆盖，否则恢复出来的执行会
退化成智能体默认模型 + 全量资源范围。
"""

from app.schemas.agent import ChatConfig
from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
from app.services.ai.runners.data_agent_runner import DataAgentRunner
from app.services.ai.turn_decision import TurnDecision

LEGACY_OPTIONS = {"model": "legacy-model", "resource_scope": {"datasets": [{"id": "legacy"}]}}
NEW_OPTIONS = {"model": "new-model"}


def _config() -> ChatConfig:
    return ChatConfig(
        agent_id="agent-1",
        agent_name="测试智能体",
        model_name="agent-model",
        temperature=0.2,
        system_prompt="sys",
        tools=[],
    )


def _runner_context(*, execution_options=None, legacy_options=None) -> dict:
    config = _config()
    context = {
        "runner_type": "assistant",
        "config": config.model_dump(),
        "permission_options": {},
        "system_content": "sys",
        "max_steps": 5,
        "turn_decision": TurnDecision.for_direct_agent_selection(config).model_dump(mode="json"),
        "current_user_query": "hi",
    }
    if execution_options is not None:
        context["execution_options"] = execution_options
    if legacy_options is not None:
        context["debug_options"] = legacy_options
    return context


def test_assistant_runner_context_reads_legacy_debug_options_key():
    runner = AssistantAgentRunner.from_runner_context(
        runner_context=_runner_context(legacy_options=LEGACY_OPTIONS),
        trace_id="trace-1",
    )

    assert runner.execution_options == LEGACY_OPTIONS


def test_assistant_runner_context_prefers_execution_options_over_legacy_key():
    runner = AssistantAgentRunner.from_runner_context(
        runner_context=_runner_context(
            execution_options=NEW_OPTIONS,
            legacy_options=LEGACY_OPTIONS,
        ),
        trace_id="trace-1",
    )

    assert runner.execution_options == NEW_OPTIONS


def test_data_runner_context_reads_legacy_debug_options_key():
    runner = DataAgentRunner.from_runner_context(
        runner_context={
            **_runner_context(legacy_options=LEGACY_OPTIONS),
            "runner_type": "data",
        },
        trace_id="trace-1",
    )

    assert runner.execution_options == LEGACY_OPTIONS


def test_data_runner_context_prefers_execution_options_over_legacy_key():
    runner = DataAgentRunner.from_runner_context(
        runner_context={
            **_runner_context(
                execution_options=NEW_OPTIONS,
                legacy_options=LEGACY_OPTIONS,
            ),
            "runner_type": "data",
        },
        trace_id="trace-1",
    )

    assert runner.execution_options == NEW_OPTIONS
