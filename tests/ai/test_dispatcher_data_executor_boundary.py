import pytest

from app.schemas.agent import ChatConfig
from app.services.ai.dispatcher import AgentDispatcher
from app.services.ai.executors.data_executor import DataQueryExecutor
from app.services.ai.executors.knowledge_executor import KnowledgeExecutor
from app.services.ai.intent_service import IntentType
from app.services.ai.turn_classifier import TurnClassification, TurnType


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_dispatcher_routes_data_capable_agent_to_data_executor_for_non_knowledge_turn():
    """非知识库轮次时，带 data_query 能力的 agent 仍进入 DataQueryExecutor。"""
    config = ChatConfig(
        agent_id="sys-agent-chatbi",
        agent_name="chat-bi",
        agent_version=None,
        model_name="test-model",
        temperature=0.0,
        system_prompt="ChatBI",
        tools=["get_dataset_schema", "execute_sql_query"],
        capabilities=["data_query"],
        engine_type="LOCAL",
    )
    shared_turn = (
        TurnClassification(
            turn_type=TurnType.GENERAL,
            reasoning="外部粗分类不应决定 ChatBI 内部执行器",
            intent=IntentType.GENERAL,
        ),
        None,
        0.0,
    )

    executor = await AgentDispatcher.dispatch(
        config,
        user_query="分析一下",
        messages=[{"role": "user", "content": "分析一下"}],
        trace_id="trace-dispatch-boundary",
        trace_buffer=[],
        shared_turn=shared_turn,
    )

    assert isinstance(executor, DataQueryExecutor)
    assert not hasattr(executor, "turn_classification")


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_dispatcher_routes_knowledge_turn_to_knowledge_executor_even_for_data_capable_agent():
    """知识库轮次优先于 ChatBI，即使 agent 具备 data_query 能力。"""
    config = ChatConfig(
        agent_id="sys-agent-chatbi",
        agent_name="chat-bi",
        agent_version=None,
        model_name="test-model",
        temperature=0.0,
        system_prompt="ChatBI",
        tools=["get_dataset_schema", "execute_sql_query"],
        capabilities=["data_query"],
        engine_type="LOCAL",
    )
    shared_turn = (
        TurnClassification(
            turn_type=TurnType.KNOWLEDGE,
            reasoning="SOP 问答",
            requires_knowledge_search=True,
            intent=IntentType.KNOWLEDGE_BASE,
        ),
        None,
        0.0,
    )

    executor = await AgentDispatcher.dispatch(
        config,
        user_query="高温告警处理流程是什么",
        messages=[{"role": "user", "content": "高温告警处理流程是什么"}],
        trace_id="trace-dispatch-knowledge",
        trace_buffer=[],
        shared_turn=shared_turn,
    )

    assert isinstance(executor, KnowledgeExecutor)
    assert executor.turn_classification.turn_type == TurnType.KNOWLEDGE
