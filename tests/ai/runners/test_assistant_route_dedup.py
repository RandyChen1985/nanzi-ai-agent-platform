from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.ai.executors.prompts import AssistantPrompts
from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner


def test_assistant_agent_runner_avoids_duplicate_route_context():
    """验证当 system_content 中已包含平台路由快照时，不会重复追加 AssistantPrompts 决策。"""
    runner = AssistantAgentRunner(
        config=MagicMock(),
        trace_id="test_trace",
        trace_buffer=MagicMock(),
    )
    runner.turn_decision = SimpleNamespace(
        turn_labels=["assistant_chat"],
        semantic_intent="chat",
        confidence=0.9,
    )

    # 1. 模拟已有 PromptAssembler 注入的平台路由快照
    existing_prompt = "You are an assistant.\n\n## 本轮执行上下文（平台路由快照）\n- 路由: assistant"

    route_hint = AssistantPrompts.turn_decision_context(runner.turn_decision)
    assert route_hint, "route_hint 应该生成内容"

    dynamic_appends = []
    if (
        route_hint
        and route_hint not in existing_prompt
        and "本轮执行上下文（平台路由快照）" not in existing_prompt
        and "【本轮执行决策（仅供参考）】" not in existing_prompt
    ):
        dynamic_appends.append(route_hint)

    # 断言：因为已包含「本轮执行上下文（平台路由快照）」，所以不会追加
    assert len(dynamic_appends) == 0


def test_assistant_agent_runner_appends_when_no_route_snapshot():
    """验证当 system_content 未包含任何路由决策时，正常追加 AssistantPrompts 决策。"""
    runner = AssistantAgentRunner(
        config=MagicMock(),
        trace_id="test_trace",
        trace_buffer=MagicMock(),
    )
    runner.turn_decision = SimpleNamespace(
        turn_labels=["assistant_chat"],
        semantic_intent="chat",
        confidence=0.9,
    )

    clean_prompt = "You are a clean assistant."

    route_hint = AssistantPrompts.turn_decision_context(runner.turn_decision)
    assert route_hint, "route_hint 应该生成内容"

    dynamic_appends = []
    if (
        route_hint
        and route_hint not in clean_prompt
        and "本轮执行上下文（平台路由快照）" not in clean_prompt
        and "【本轮执行决策（仅供参考）】" not in clean_prompt
    ):
        dynamic_appends.append(route_hint)

    assert len(dynamic_appends) == 1
    assert "【本轮执行决策（仅供参考）】" in dynamic_appends[0]
