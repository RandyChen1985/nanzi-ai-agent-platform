from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.ai import context_manager
from app.services.ai.data_query_turn_classifier import (
    DataQueryTurnType,
    DataQueryTurnClassification,
)
from app.services.ai.quick_result_context import normalize_quick_result_context
from app.services.ai.runners.data_agent_runner import DataAgentRunner
from app.services.ai.turn_decision import TurnDecision
from app.schemas.agent import ChatConfig


pytestmark = pytest.mark.no_infrastructure


def test_normalize_quick_result_context_accepts_only_fresh_chatbi_result_reference():
    context = normalize_quick_result_context(
        {
            "source": "chatbi_result",
            "result_id": "result-123",
            "requires_fresh_data": True,
        }
    )

    assert context is not None
    assert context.source == "chatbi_result"
    assert context.result_id == "result-123"
    assert context.requires_fresh_data is True


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {"source": "chatbi_result", "requires_fresh_data": False},
        {"source": "assistant_text", "requires_fresh_data": True},
        {"source": "chatbi_result", "result_id": "x" * 129, "requires_fresh_data": True},
    ],
)
def test_normalize_quick_result_context_rejects_untrusted_metadata(raw):
    assert normalize_quick_result_context(raw) is None


def test_quick_result_context_is_not_treated_as_reusable_result():
    from app.services.ai.reusable_result import (
        quick_result_reuse_decision,
        should_attempt_reusable_reuse,
    )

    # —— 行为断言：quick-result（新查询）上下文必须产出硬性「不复用」决策 ——
    decision = quick_result_reuse_decision()
    assert decision.mode == "none"
    assert decision.reason == "quick_context_requires_fresh_data"

    # quick_result_followup 时，即使存在候选结果类型白名单，也禁止复用尝试
    assert (
        should_attempt_reusable_reuse(
            quick_result_followup=True,
            allowed_reusable_result_types={"data"},
        )
        is False
    )
    assert (
        should_attempt_reusable_reuse(
            quick_result_followup=True,
            allowed_reusable_result_types=None,
        )
        is False
    )

    # 仅当「非 quick-result」且「存在候选类型白名单」时才允许复用尝试
    assert (
        should_attempt_reusable_reuse(
            quick_result_followup=False,
            allowed_reusable_result_types={"data"},
        )
        is True
    )
    assert (
        should_attempt_reusable_reuse(
            quick_result_followup=False,
            allowed_reusable_result_types=None,
        )
        is False
    )


def test_quick_result_missing_data_agent_prompts_for_retry():
    # 用户可见的降级文案契约：快捷分析需实时数据但无数据查询智能体时提示重试。
    source = Path("app/services/ai/pipeline/steps/route_step.py").read_text(
        encoding="utf-8"
    )
    assert "没有可用的数据查询智能体" in source


def test_quick_result_followup_overrides_result_analysis_to_fresh_query():
    runner = DataAgentRunner(
        config=ChatConfig(
            agent_id="data-agent-1",
            agent_name="chat-bi",
            model_name="test",
            temperature=0,
            system_prompt="test",
            tools=[],
            capabilities=["data_query"],
        ),
        trace_id="quick-result-test",
        trace_buffer=[],
        turn_decision=TurnDecision(
            route_status="resolved",
            turn_kind="data_query",
            source="internal_structured_data",
            capability="data_query",
            allows_data_route=True,
            quick_result_followup=True,
        ),
    )
    classification = DataQueryTurnClassification(
        turn_type=DataQueryTurnType.RESULT_ANALYSIS,
        reasoning="旧结果分析",
        requires_fresh_data=False,
        requires_few_shot=False,
        requires_sql_query=False,
    )

    result = runner._apply_route_semantics_to_turn_classification(classification)

    assert result.turn_type == DataQueryTurnType.NEW_DATA_QUERY
    assert result.requires_fresh_data is True
    assert result.requires_sql_query is True


@pytest.mark.asyncio
async def test_quick_result_followup_resolves_data_agent_instead_of_default_main(monkeypatch):
    class SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    data_agent = SimpleNamespace(
        id="data-agent-1",
        is_enabled=True,
        capabilities=["data_query"],
    )
    data_config = SimpleNamespace(
        agent_id="data-agent-1",
        agent_name="chat-bi",
        agent_display_name="数据分析",
        capabilities=["data_query"],
    )

    monkeypatch.setattr(context_manager, "AsyncSessionLocal", lambda: SessionContext())
    monkeypatch.setattr(
        context_manager.AgentManagerService,
        "list_allowed_agents",
        staticmethod(lambda *_args, **_kwargs: _async_return([data_agent])),
    )
    monkeypatch.setattr(
        context_manager.AgentManagerService,
        "get_active_agent_config",
        staticmethod(lambda *_args, **_kwargs: _async_return(data_config)),
    )

    config, route = await context_manager.AgentContextManager.resolve_agent_config(
        [],
        user_info={"user_id": 1},
        quick_result_followup=True,
    )

    assert config is data_config
    assert route.turn_kind == "data_query"
    assert route.provenance == "direct_agent_selection"


async def _async_return(value):
    return value
