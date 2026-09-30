import pytest
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch
from app.services.ai.router_service import RouterService
from app.services.ai.turn_decision import TurnDecision
from app.services.ai.chatbi_qualification import DatasetCandidate
from app.services.ai.intent_service import (
    DataSessionAffinity,
    IntentResponse,
    IntentType,
    resolve_data_agent_session_affinity,
    should_inherit_data_agent_session,
)

# --- Mocks ---

@pytest.fixture(scope="function", autouse=True)
async def init_infrastructure():
    """Override infrastructure initialization to avoid real DB connections."""
    with patch("app.core.database.init_db", new_callable=AsyncMock), \
         patch("app.core.database.close_db", new_callable=AsyncMock), \
         patch("app.core.redis.init_redis", new_callable=AsyncMock), \
         patch("app.core.redis.close_redis", new_callable=AsyncMock), \
         patch("app.core.orm.AsyncSessionLocal", new_callable=MagicMock), \
         patch("app.services.ai.intent_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify:
        mock_identify.return_value = IntentResponse(
            intent=IntentType.GENERAL,
            confidence=0.95,
            reasoning="测试默认通用语义",
            entities=[],
        )
        yield

@pytest.fixture
def mock_agents_metadata():
    return [
        {
            "id": "agent-chatbi",
            "name": "ChatBI",
            "description": "SQL Data Analyst",
            "capabilities": ["text-to-sql", "chart"]
        },
        {
            "id": "agent-rag",
            "name": "KnowledgeBase",
            "description": "Document Retrieval",
            "capabilities": ["rag", "qa"]
        },
        {
            "id": "agent-general",
            "name": "general-chat",
            "description": "General Assistant",
            "capabilities": ["chat"]
        }
    ]

class MockLLMResponse:
    def __init__(self, content):
        self.content = content


def _mock_chat_client(content: str):
    mock_client = AsyncMock()
    mock_client.generate_structured_dict.return_value = None
    mock_client.generate_text.return_value = content
    return mock_client


def test_should_inherit_data_agent_session_generalized():
    assert should_inherit_data_agent_session("把上面的结果画成柱状图") is True
    assert should_inherit_data_agent_session("查一下所有机房的列表") is True
    assert should_inherit_data_agent_session("看看我开源项目，小星星情况") is False
    assert should_inherit_data_agent_session("今天北京天气怎么样") is False
    assert should_inherit_data_agent_session("姓名呢") is True
    assert should_inherit_data_agent_session("那手机号呢") is True
    assert should_inherit_data_agent_session("还有创建时间呢") is True
    assert should_inherit_data_agent_session("PUE呢？") is True
    assert should_inherit_data_agent_session("那它呢") is False
    assert should_inherit_data_agent_session("为什么呢") is False
    assert should_inherit_data_agent_session("看看") is False


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("把上面的结果画成柱状图", DataSessionAffinity.KEEP),
        ("保存这个结果", DataSessionAffinity.KEEP),
        ("查一下所有机房的列表", DataSessionAffinity.KEEP),
        ("你好", DataSessionAffinity.BREAK),
        ("联网查一下今天的行业新闻", DataSessionAffinity.BREAK),
        ("帮我看看这个", DataSessionAffinity.UNCERTAIN),
        ("看看我开源项目，小星星情况", DataSessionAffinity.UNCERTAIN),
    ],
)
def test_resolve_data_agent_session_affinity_is_tristate(query, expected):
    assert resolve_data_agent_session_affinity(query) == expected

# --- Tests ---

@pytest.mark.asyncio
async def test_router_service_fetch_agents_mock(mock_agents_metadata):
    """测试从 DB 获取 Agents 列表的 Mock 逻辑"""
    service = RouterService()
    
    # Mock _fetch_agents_from_db directly to isolate from SQL logic in this unit test
    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_agents_metadata
        
        agents = await service._fetch_agents_from_db()
        assert len(agents) == 3
        assert agents[0]["name"] == "ChatBI"

@pytest.mark.asyncio
async def test_route_query_high_confidence(mock_agents_metadata):
    """测试高置信度路由: 明确匹配 ChatBI"""
    service = RouterService()
    
    # Mock JSON response from LLM using the new 'thought' field
    llm_resp_content = json.dumps({
        "thought": "Query asks for data table.",
        "agent_name": "ChatBI",
        "confidence": 0.95,
        "intent": "DATA_QUERY",
        "domain": "chatbi_business_data",
        "operation": "lookup",
    })
    
    mock_llm = object()
    mock_chat = _mock_chat_client(llm_resp_content)
    
    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.intent_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config:
         
        mock_fetch.return_value = mock_agents_metadata
        mock_identify.return_value = IntentResponse(
            intent=IntentType.DATA_QUERY,
            confidence=0.92,
            reasoning="用户请求内部结构化数据计数",
            entities=["user count"],
        )
        mock_get_llm.return_value = mock_llm
        mock_chat_factory.return_value = mock_chat
        mock_config.return_value = "System Prompt"
        
        result = await service.route_query("Show me user count")
        
        assert isinstance(result, TurnDecision)
        assert result.agent_id == "agent-chatbi"
        assert result.confidence == 0.95
        assert result.reasoning == "Query asks for data table."
        assert result.turn_labels == []
        assert result.relation_to_previous == "unknown"
        assert result.user_action_type == "unknown"
        assert result.semantic_intent == IntentType.DATA_QUERY.value
        assert result.semantic_domain == "chatbi_business_data"
        assert result.semantic_confidence != result.confidence
        assert result.semantic_reasoning != result.reasoning
        mock_identify.assert_not_called()
        assert mock_get_llm.call_count == 1
        assert mock_get_llm.call_args.kwargs["ignore_session_reasoning_overrides"] is True


def test_intent_from_router_payload_does_not_reuse_route_confidence_or_thought():
    """路由 confidence/thought 不是意图置信度与理由。"""
    copied = RouterService._intent_from_router_payload({
        "agent_name": "ChatBI",
        "confidence": 0.95,
        "thought": "Query asks for data table.",
        "intent": "DATA_QUERY",
        "domain": "chatbi_business_data",
        "operation": "lookup",
    })
    assert copied is not None
    assert copied.intent == IntentType.DATA_QUERY
    assert copied.domain == "chatbi_business_data"
    assert copied.confidence == 0.0
    assert copied.reasoning != "Query asks for data table."

    explicit = RouterService._intent_from_router_payload({
        "agent_name": "ChatBI",
        "confidence": 0.95,
        "thought": "选数据智能体",
        "intent": "DATA_QUERY",
        "domain": "chatbi_business_data",
        "operation": "lookup",
        "intent_confidence": 0.82,
        "intent_reasoning": "内部业务记录查询",
    })
    assert explicit is not None
    assert explicit.confidence == pytest.approx(0.82)
    assert explicit.reasoning == "内部业务记录查询"


def test_intent_from_router_payload_ignores_placeholder_general_unknown():
    """重试示例里的 GENERAL/unknown 不能当成真实语义证据。"""
    assert RouterService._intent_from_router_payload({
        "agent_name": "ChatBI",
        "confidence": 0.9,
        "intent": "GENERAL",
        "domain": "unknown",
        "thought": "短理由",
    }) is None
    assert RouterService._intent_from_router_payload({
        "agent_name": "ChatBI",
        "confidence": 0.9,
        "intent": "UNKNOWN",
        "domain": "unknown",
    }) is None


@pytest.mark.asyncio
async def test_route_query_retry_recovers_business_data_intent(mock_agents_metadata):
    """首次 JSON 失败后，第二次应按当前问题重判意图并路由到 ChatBI。"""
    service = RouterService()
    retry_payload = json.dumps({
        "agent_name": "ChatBI",
        "confidence": 0.91,
        "secondary_agents": [],
        "intent": "DATA_QUERY",
        "domain": "chatbi_business_data",
        "operation": "lookup",
        "intent_confidence": 0.88,
        "intent_reasoning": "系统结构化记录查询",
        "thought": "业务查数",
    })
    mock_chat = AsyncMock()
    mock_chat.generate_structured_dict.return_value = None
    mock_chat.generate_text.side_effect = [
        "not-json {{{",
        retry_payload,
    ]

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.intent_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:
        mock_fetch.return_value = mock_agents_metadata
        mock_get_llm.return_value = object()
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query("列出某个系统中的全部记录")

    assert result.agent_id == "agent-chatbi"
    assert result.semantic_intent == IntentType.DATA_QUERY.value
    assert result.semantic_domain == "chatbi_business_data"
    assert result.semantic_confidence == pytest.approx(0.88)
    assert result.semantic_reasoning == "系统结构化记录查询"
    mock_identify.assert_not_called()
    assert mock_chat.generate_text.await_count == 2
    retry_prompt = mock_chat.generate_text.await_args_list[1].args[0][0].content[0].text
    assert '"intent":"GENERAL"' not in retry_prompt.replace(" ", "")
    assert "根据当前问题" in retry_prompt or "重新判断" in retry_prompt


@pytest.mark.asyncio
async def test_route_query_retry_placeholder_general_does_not_block_chatbi(mock_agents_metadata):
    """第二次若照抄 GENERAL/unknown，应丢弃该占位意图，保留 agent_name 查数选择。"""
    service = RouterService()
    retry_payload = json.dumps({
        "agent_name": "ChatBI",
        "confidence": 0.9,
        "intent": "GENERAL",
        "domain": "unknown",
        "thought": "短理由",
    })
    mock_chat = AsyncMock()
    mock_chat.generate_structured_dict.return_value = None
    mock_chat.generate_text.side_effect = [
        "not-json {{{",
        retry_payload,
    ]

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.intent_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:
        mock_fetch.return_value = mock_agents_metadata
        mock_get_llm.return_value = object()
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query("列出某个系统中的全部记录")

    assert result.agent_id == "agent-chatbi"
    assert result.semantic_intent is None
    mock_identify.assert_not_called()


@pytest.mark.asyncio
async def test_route_query_placeholder_general_fails_closed_for_ambiguous_query(mock_agents_metadata):
    """未知/占位意图不能让模糊请求放行到 ChatBI。"""
    service = RouterService()
    retry_payload = json.dumps({
        "agent_name": "ChatBI",
        "confidence": 0.9,
        "intent": "GENERAL",
        "domain": "unknown",
        "thought": "短理由",
    })
    mock_chat = AsyncMock()
    mock_chat.generate_structured_dict.return_value = None
    mock_chat.generate_text.side_effect = [
        "not-json {{{",
        retry_payload,
    ]

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.intent_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:
        mock_fetch.return_value = mock_agents_metadata
        mock_get_llm.return_value = object()
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query("帮我看看这个")

    assert result.agent_id == "agent-general"
    assert result.semantic_intent is None
    mock_identify.assert_not_called()


@pytest.mark.asyncio
async def test_route_query_filters_data_secondary_for_public_web_request(mock_agents_metadata):
    """公网请求即使被 LLM 放入 secondary，也不能携带 ChatBI。"""
    service = RouterService()
    mock_chat = _mock_chat_client(json.dumps({
        "agent_name": "general-chat",
        "confidence": 0.9,
        "secondary_agents": ["ChatBI"],
        "intent": "GENERAL",
        "domain": "public_web",
        "operation": "lookup",
        "intent_confidence": 0.9,
        "intent_reasoning": "公网信息查询",
        "thought": "交给通用助手联网查询",
    }))

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:
        mock_fetch.return_value = mock_agents_metadata
        mock_get_llm.return_value = object()
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query("查一下有孚网络公司信息")

    assert result.agent_id == "agent-general"
    assert result.secondary_agents == []


@pytest.mark.asyncio
async def test_route_query_returns_generic_turn_hints(mock_agents_metadata):
    """路由可输出通用会话标签，但它们只是 executor 可选择使用的 hint。"""
    service = RouterService()

    llm_resp_content = json.dumps({
        "thought": "This is a follow-up to the previous data answer.",
        "agent_name": "ChatBI",
        "secondary_agents": [],
        "confidence": 0.92,
        "turn_labels": ["continuation_followup", "business_related", "same_topic", "unknown_label"],
        "relation_to_previous": "followup",
        "user_action_type": "transform_context"
    })

    mock_llm = object()
    mock_chat = _mock_chat_client(llm_resp_content)

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.intent_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:

        mock_fetch.return_value = mock_agents_metadata
        mock_identify.return_value = IntentResponse(
            intent=IntentType.DATA_QUERY,
            confidence=0.93,
            reasoning="用户查询内部机房基础数据列表",
            entities=["机房列表"],
        )
        mock_get_llm.return_value = mock_llm
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query("把上面的结果画成柱状图", last_agent_name="ChatBI")

    assert result.agent_id == "agent-chatbi"
    assert result.turn_labels == ["continuation_followup", "business_related", "same_topic"]
    assert result.relation_to_previous == "followup"
    assert result.user_action_type == "transform_context"


@pytest.mark.asyncio
async def test_route_prompt_guides_local_machine_load_to_general(mock_agents_metadata):
    """路由提示词应区分本机诊断与业务指标查询，避免把本机负载当作 ChatBI 查数。"""
    service = RouterService()
    llm_resp_content = json.dumps({
        "thought": "This asks for the current machine runtime status, not historical business metrics.",
        "agent_name": "general-chat",
        "confidence": 0.93
    })

    mock_llm = object()
    mock_chat = _mock_chat_client(llm_resp_content)

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.intent_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:

        mock_fetch.return_value = mock_agents_metadata
        mock_identify.return_value = IntentResponse(
            intent=IntentType.DATA_QUERY,
            confidence=0.91,
            reasoning="用户查询业务机房负载趋势指标",
            entities=["上海机房", "负载趋势"],
        )
        mock_get_llm.return_value = mock_llm
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query("看看我机器的负载情况")

    assert result.agent_id == "agent-general"
    assert result.confidence == 0.93
    routed_messages = mock_chat.generate_text.call_args[0][0]
    system_prompt = routed_messages[0].content[0].text
    assert "当前系统/本机/这台机器/服务器运行状态" in system_prompt
    assert "不要因为出现\"负载/利用率/CPU/内存\"等词就直接判为数据查询" in system_prompt


@pytest.mark.asyncio
async def test_route_query_local_file_domain_blocks_chatbi_even_when_llm_selects_it(mock_agents_metadata):
    """本机文件统计中的“统计”不能把请求升级为 ChatBI。"""
    service = RouterService()
    mock_chat = _mock_chat_client(json.dumps({
        "thought": "错误地把本机文件统计选成了数据智能体",
        "agent_name": "ChatBI",
        "confidence": 0.95,
        "intent": "DATA_QUERY",
        "domain": "local_file",
        "operation": "aggregate",
        "fact_kind": "file_count",
        "freshness_requirement": "dynamic",
        "reference_mode": "new_query",
        "needs_fresh_data": True,
    }))

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.intent_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:
        mock_fetch.return_value = mock_agents_metadata
        mock_get_llm.return_value = object()
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query("统计一下我机器的文件数")

    assert result.agent_id == "agent-general"
    assert result.chatbi_mode == "deny"
    assert result.should_delegate is False
    assert result.capability == "answer"
    assert result.semantic_domain == "local_file"
    assert result.reference_mode == "new_query"
    assert result.needs_fresh_data is True
    mock_identify.assert_not_called()


@pytest.mark.asyncio
async def test_route_query_business_load_metric_still_uses_llm(mock_agents_metadata):
    """业务/机房负载指标不命中本机诊断捷径，仍由 LLM 路由给 ChatBI。"""
    service = RouterService()
    llm_resp_content = json.dumps({
        "thought": "Query asks for IDC load trend metrics.",
        "agent_name": "ChatBI",
        "confidence": 0.91,
        "intent": "DATA_QUERY",
        "domain": "chatbi_business_data",
        "operation": "lookup",
    })

    mock_llm = object()
    mock_chat = _mock_chat_client(llm_resp_content)

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.intent_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:

        mock_fetch.return_value = mock_agents_metadata
        mock_identify.return_value = IntentResponse(
            intent=IntentType.DATA_QUERY,
            confidence=0.93,
            reasoning="用户查询内部机房基础数据列表",
            entities=["机房列表"],
        )
        mock_get_llm.return_value = mock_llm
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query("查询上海机房负载趋势")

    assert result.agent_id == "agent-chatbi"
    assert result.confidence == 0.91
    mock_get_llm.assert_called_once()


@pytest.mark.asyncio
async def test_route_query_low_confidence_fallback(mock_agents_metadata):
    """测试低置信度回退: 路由给 general-chat"""
    service = RouterService()
    
    # Confidence 0.4 < 0.6 threshold
    llm_resp_content = json.dumps({
        "thought": "Not sure.",
        "agent_name": "ChatBI",
        "confidence": 0.4
    })
    
    mock_llm = object()
    mock_chat = _mock_chat_client(llm_resp_content)
    
    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config:
         
        mock_fetch.return_value = mock_agents_metadata
        mock_get_llm.return_value = mock_llm
        mock_chat_factory.return_value = mock_chat
        mock_config.return_value = "System Prompt"
        
        result = await service.route_query("嗯，随便聊聊")
        
        # Should fallback to general-chat
        assert result.agent_id == "agent-general"
        assert "Low confidence" in result.reasoning
        assert "Not sure." in result.reasoning

@pytest.mark.asyncio
@pytest.mark.parametrize("fallback_name,expected_id", [
    ("assistant", "agent-assistant"),
    ("main", "agent-main"),
    ("general-chat", "agent-general"),
])
async def test_fallback_matches_multiple_general_agent_slugs(fallback_name, expected_id):
    """兜底逻辑应支持 assistant / main / general-chat 多种 slug。"""
    service = RouterService()
    agents = [
        {"id": "agent-chatbi", "name": "ChatBI", "description": "SQL", "capabilities": []},
        {"id": expected_id, "name": fallback_name, "description": "General", "capabilities": ["chat"]},
    ]

    llm_resp_content = json.dumps({
        "thought": "Not sure.",
        "agent_name": "ChatBI",
        "confidence": 0.4,
    })

    mock_llm = object()
    mock_chat = _mock_chat_client(llm_resp_content)

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:

        mock_fetch.return_value = agents
        mock_get_llm.return_value = mock_llm
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query("嗯，随便聊聊")

        assert result.agent_id == expected_id
    assert "Low confidence" in result.reasoning


@pytest.mark.asyncio
async def test_fallback_prefers_assistant_over_general_chat():
    """多个兜底 slug 同时存在时，按 FALLBACK_AGENT_NAMES 优先级取首个。"""
    service = RouterService()
    agents = [
        {"id": "agent-general", "name": "general-chat", "description": "Legacy", "capabilities": []},
        {"id": "agent-assistant", "name": "assistant", "description": "New", "capabilities": []},
    ]

    llm_resp_content = json.dumps({
        "thought": "Unknown.",
        "agent_name": "missing-agent",
        "confidence": 0.99,
    })

    mock_chat = _mock_chat_client(llm_resp_content)

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:

        mock_fetch.return_value = agents
        mock_get_llm.return_value = object()
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query("Hi")

    assert result.agent_id == "agent-assistant"


@pytest.mark.asyncio
async def test_route_query_unknown_agent_fallback(mock_agents_metadata):
    """测试 LLM 返回未知 Agent 名称时的回退"""
    service = RouterService()
    
    llm_resp_content = json.dumps({
        "thought": "I made this up.",
        "agent_name": "SuperAgent", # Does not exist
        "confidence": 0.99
    })
    
    mock_llm = object()
    mock_chat = _mock_chat_client(llm_resp_content)
    
    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config:
         
        mock_fetch.return_value = mock_agents_metadata
        mock_get_llm.return_value = mock_llm
        mock_chat_factory.return_value = mock_chat
        mock_config.return_value = "System Prompt"
        
        result = await service.route_query("Do magic")
        
        assert result.agent_id == "agent-general"
        assert "unknown agent" in result.reasoning

@pytest.mark.asyncio
async def test_router_caching(mock_agents_metadata):
    """测试 Agent 列表的缓存机制"""
    service = RouterService()
    service._cache_ttl = 60
    
    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config:
        
        mock_fetch.return_value = mock_agents_metadata
        mock_llm_instance = object()
        mock_chat_factory.return_value = _mock_chat_client('{"thought": "test", "agent_name": "ChatBI", "confidence": 1.0}')
        mock_get_llm.return_value = mock_llm_instance
        
        mock_config.return_value = ""

        # First call: fetch from DB
        await service.route_query("Q1")
        assert mock_fetch.call_count == 1
        
        # Second call (immediate): use cache
        await service.route_query("Q2")
        assert mock_fetch.call_count == 1 # Should not increment
        
        # Invalidate cache
        service.invalidate_cache()
        
        # Third call: fetch again
        await service.route_query("Q3")
        assert mock_fetch.call_count == 2


@pytest.mark.asyncio
async def test_route_query_filters_candidates_by_user_permission(mock_agents_metadata):
    """路由候选应先按用户可访问智能体过滤，避免选到随后被拒绝的 agent。"""
    service = RouterService()
    llm_resp_content = json.dumps({
        "thought": "Only allowed agent is suitable.",
        "agent_name": "KnowledgeBase",
        "confidence": 0.95
    })

    mock_llm = object()
    mock_chat = _mock_chat_client(llm_resp_content)
    permission_response = SimpleNamespace(
        roles=["user"],
        permissions=SimpleNamespace(agents=["agent-rag", "agent-general"])
    )

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config, \
         patch("app.services.permission_service.PermissionService.get_user_permissions", new_callable=AsyncMock) as mock_perms:

        mock_fetch.return_value = mock_agents_metadata
        mock_get_llm.return_value = mock_llm
        mock_chat_factory.return_value = mock_chat
        mock_config.return_value = RouterService.DEFAULT_SYSTEM_PROMPT
        mock_perms.return_value = permission_response

        result = await service.route_query("查文档", user_id=1001, is_admin=False)

    assert result.agent_id == "agent-rag"
    routed_messages = mock_chat.generate_text.call_args[0][0]
    system_prompt = routed_messages[0].content[0].text
    assert "ID: KnowledgeBase" in system_prompt
    assert "UUID: agent-rag" in system_prompt
    assert "UUID: agent-chatbi" not in system_prompt


@pytest.mark.asyncio
async def test_route_query_datacenter_list_uses_chatbi(mock_agents_metadata):
    """查询机房列表或基础数据列表，应路由给 ChatBI 智能体进行 SQL 数据查询，而不是误判为知识库检索。"""
    service = RouterService()
    llm_resp_content = json.dumps({
        "thought": "Query asks for a list of server rooms (physical data list), which requires SQL query.",
        "agent_name": "ChatBI",
        "confidence": 0.95,
        "intent": "DATA_QUERY",
        "domain": "chatbi_business_data",
        "operation": "lookup",
    })

    mock_llm = object()
    mock_chat = _mock_chat_client(llm_resp_content)

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.intent_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:

        mock_fetch.return_value = mock_agents_metadata
        mock_identify.return_value = IntentResponse(
            intent=IntentType.DATA_QUERY,
            confidence=0.93,
            reasoning="用户查询内部机房基础数据列表",
            entities=["机房列表"],
        )
        mock_get_llm.return_value = mock_llm
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query("查一下所有机房的列表")

    assert result.agent_id == "agent-chatbi"
    assert result.confidence == 0.95

    routed_messages = mock_chat.generate_text.call_args[0][0]
    system_prompt = routed_messages[0].content[0].text
    assert "客户/员工/产品/工单/审批等业务记录或数据列表" in system_prompt


@pytest.mark.asyncio
async def test_route_query_constrains_candidates_for_high_confidence_data_intent(mock_agents_metadata):
    """合并后的路由 JSON 仍能选出 ChatBI；候选清单不再被前置意图 LLM 收缩。"""
    service = RouterService()
    router_response = json.dumps({
        "thought": "在数据候选中选择 SQL 分析专家",
        "agent_name": "ChatBI",
        "confidence": 0.9,
        "intent": "DATA_QUERY",
        "domain": "chatbi_business_data",
        "operation": "lookup",
    })
    mock_chat = _mock_chat_client(router_response)

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.intent_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.resolve_authorized_dataset_candidates", new_callable=AsyncMock) as mock_candidates, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:
        mock_fetch.return_value = mock_agents_metadata
        mock_candidates.return_value = [
            DatasetCandidate(
                dataset_id=7,
                display_name="系统记录",
                similarity=0.84,
                content="系统中的业务记录",
            )
        ]
        mock_get_llm.return_value = object()
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query("列出某个系统中的全部记录")

    assert result.agent_id == "agent-chatbi"
    assert result.semantic_intent == IntentType.DATA_QUERY.value
    assert result.semantic_domain == "chatbi_business_data"
    mock_identify.assert_not_called()
    routed_messages = mock_chat.generate_text.call_args.args[0]
    system_prompt = routed_messages[0].content[0].text
    assert "ID: ChatBI" in system_prompt
    assert "ID: general-chat" in system_prompt


@pytest.mark.asyncio
async def test_route_query_public_company_lookup_not_forced_to_data_agent(mock_agents_metadata):
    """公网公司资料查询即使路由模型误选 ChatBI，启发式资格规则仍应否决。"""
    service = RouterService()
    mock_chat = _mock_chat_client(json.dumps({
        "thought": "错误地选择数据查询智能体",
        "agent_name": "ChatBI",
        "confidence": 0.88,
        "intent": "DATA_QUERY",
        "domain": "public_web",
        "operation": "lookup",
    }))

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.intent_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:
        mock_fetch.return_value = mock_agents_metadata
        mock_get_llm.return_value = object()
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query("查一下有孚网络公司信息")

    assert result.agent_id == "agent-general"
    assert result.semantic_intent == IntentType.DATA_QUERY.value
    assert result.semantic_domain == "public_web"
    assert "without internal structured-data source" in result.reasoning
    mock_identify.assert_not_called()

    routed_messages = mock_chat.generate_text.call_args.args[0]
    system_prompt = routed_messages[0].content[0].text
    assert "ID: ChatBI" in system_prompt
    assert "ID: general-chat" in system_prompt


@pytest.mark.asyncio
async def test_route_query_data_evidence_survives_invalid_constrained_route(mock_agents_metadata):
    """路由模型返回非法候选时，应回落 Main 并保留同一次 JSON 里的语义供其委派。"""
    service = RouterService()
    mock_chat = _mock_chat_client(json.dumps({
        "thought": "错误返回不存在的候选",
        "agent_name": "not-in-candidates",
        "confidence": 0.9,
        "intent": "DATA_QUERY",
        "domain": "chatbi_business_data",
        "operation": "lookup",
    }))

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.intent_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:
        mock_fetch.return_value = mock_agents_metadata
        mock_get_llm.return_value = object()
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query("列出某个系统中的全部记录")

    assert result.agent_id == "agent-general"
    assert result.semantic_intent == IntentType.DATA_QUERY.value
    assert result.semantic_domain == "chatbi_business_data"
    mock_identify.assert_not_called()


@pytest.mark.asyncio
async def test_route_query_greeting_shortcut_skips_llm(mock_agents_metadata):
    """纯问候应短路至通用助手，不调用路由 LLM。"""
    service = RouterService()
    mock_chat = _mock_chat_client("{}")

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.intent_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:

        mock_fetch.return_value = mock_agents_metadata
        mock_identify.return_value = IntentResponse(
            intent=IntentType.DATA_QUERY,
            confidence=0.93,
            reasoning="问候后提出内部机房列表查询",
            entities=["机房列表"],
        )
        mock_get_llm.return_value = object()
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query("你好")

    assert result is not None
    assert result.agent_id == "agent-general"
    assert result.confidence >= 0.9
    assert "问候" in result.reasoning or "寒暄" in result.reasoning
    assert result.turn_labels == ["general_chat"]
    assert result.user_action_type == "chat"
    mock_get_llm.assert_not_called()
    mock_chat.generate_text.assert_not_called()


@pytest.mark.parametrize(
    "query",
    [
        "【业务确认】用户已确定\nconfirmation_id: bc_1\n请根据以下已确认字段继续执行",
        "【业务确认】用户已取消\nconfirmation_id: bc_1\n请立即终止本次录入/变更",
    ],
)
@pytest.mark.asyncio
async def test_route_query_business_confirmation_receipt_sticky_skips_llm(
    mock_agents_metadata,
    query,
):
    """业务确认确定/取消回执应粘滞上一轮智能体，跳过路由 LLM。"""
    service = RouterService()
    mock_chat = _mock_chat_client("{}")

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.intent_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:

        mock_fetch.return_value = mock_agents_metadata
        mock_identify.return_value = IntentResponse(
            intent=IntentType.GENERAL,
            confidence=0.5,
            reasoning="should not be used",
            entities=[],
        )
        mock_get_llm.return_value = object()
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query(query, last_agent_name="general-chat")

    assert result is not None
    assert result.agent_id == "agent-general"
    assert result.confidence >= 0.99
    assert "业务确认回执" in result.reasoning
    assert "business_confirmation_receipt" in result.turn_labels
    assert result.relation_to_previous == "follow_up"
    mock_get_llm.assert_not_called()
    mock_identify.assert_not_called()
    mock_chat.generate_text.assert_not_called()


@pytest.mark.asyncio
async def test_route_query_user_question_receipt_sticky_skips_llm(mock_agents_metadata):
    """AI 提问回执沿用上一轮智能体，避免回答被重新分到通用/联网路径。"""
    service = RouterService()
    mock_chat = _mock_chat_client("{}")

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.intent_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:
        mock_fetch.return_value = mock_agents_metadata
        mock_identify.return_value = IntentResponse(
            intent=IntentType.GENERAL,
            confidence=0.5,
            reasoning="should not be used",
            entities=[],
        )
        mock_get_llm.return_value = object()
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query(
            "【用户回答】\ninteraction_type: question\nquestion_id: uq_1\n"
            'selected_option_ids: ["monthly"]\ncustom_input: 排除退款',
            last_agent_name="ChatBI",
        )

    assert result is not None
    assert result.agent_id == "agent-chatbi"
    assert "AI 提问回执" in result.reasoning
    assert "user_question_receipt" in result.turn_labels
    assert result.relation_to_previous == "follow_up"
    mock_get_llm.assert_not_called()
    mock_identify.assert_not_called()
    mock_chat.generate_text.assert_not_called()


@pytest.mark.asyncio
async def test_route_query_business_confirmation_receipt_without_last_agent_still_routes(
    mock_agents_metadata,
):
    """无上一轮智能体时，确认回执不短路，仍走正常路由。"""
    service = RouterService()
    mock_chat = _mock_chat_client(
        json.dumps(
            {
                "thought": "无粘性，走通用助手",
                "agent_name": "general-chat",
                "confidence": 0.9,
                "secondary_agents": [],
                "turn_labels": ["general_chat"],
                "relation_to_previous": "standalone",
                "user_action_type": "chat",
            },
            ensure_ascii=False,
        )
    )

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:

        mock_fetch.return_value = mock_agents_metadata
        mock_get_llm.return_value = object()
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query(
            "【业务确认】用户已确定\nconfirmation_id: bc_1",
            last_agent_name=None,
        )

    assert result is not None
    assert result.agent_id == "agent-general"
    mock_get_llm.assert_called()


@pytest.mark.asyncio
async def test_route_query_sole_candidate_after_permission_skips_all_llms():
    """权限过滤后仅 1 个候选时，跳过意图与路由 LLM。"""
    service = RouterService()
    sole = [
        {
            "id": "agent-general",
            "name": "general-chat",
            "description": "General Assistant",
            "capabilities": ["chat"],
        }
    ]
    mock_chat = _mock_chat_client("{}")

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.intent_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:

        mock_fetch.return_value = sole
        mock_get_llm.return_value = object()
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query("供应商名称:测试 我要录入")

    assert result is not None
    assert result.agent_id == "agent-general"
    assert "唯一可路由智能体" in result.reasoning
    assert result.turn_labels == ["sole_candidate"]
    mock_identify.assert_not_called()
    mock_get_llm.assert_not_called()
    mock_chat.generate_text.assert_not_called()


@pytest.mark.asyncio
async def test_route_query_sole_candidate_after_constrain_skips_route_llm(mock_agents_metadata):
    """意图约束后仅剩 1 个候选时，跳过路由 LLM。"""
    service = RouterService()
    mock_chat = _mock_chat_client("{}")
    sole = [mock_agents_metadata[0]]  # ChatBI only

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch.object(service, "_constrain_candidates_by_intent", return_value=sole) as mock_constrain, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:

        mock_fetch.return_value = mock_agents_metadata
        mock_get_llm.return_value = object()
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query("列出某个系统中的全部记录")

    assert result is not None
    assert result.agent_id == "agent-chatbi"
    assert "唯一可路由智能体" in result.reasoning
    assert "sole_candidate" in result.turn_labels
    mock_constrain.assert_called()
    mock_get_llm.assert_not_called()
    mock_chat.generate_text.assert_not_called()


@pytest.mark.parametrize(
    "query",
    [
        "我有哪些知识库权限",
        "我们有哪些知识库权限",
        "我们有哪些知识库",
        "我能访问哪些数据集",
        "列出我的数据集",
        "我有哪些知识库",
        "知识库列表",
        "我能看哪些知识库权限",
    ],
)
@pytest.mark.asyncio
async def test_route_query_resource_catalog_shortcut_skips_llm(mock_agents_metadata, query):
    """权限内资源目录询问应短路至通用助手，跳过意图与路由 LLM。"""
    service = RouterService()
    mock_chat = _mock_chat_client("{}")

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.intent_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:

        mock_fetch.return_value = mock_agents_metadata
        mock_get_llm.return_value = object()
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query(query)

    assert result is not None
    assert result.agent_id == "agent-general"
    assert result.confidence >= 0.9
    assert "数据集" in result.reasoning or "知识库" in result.reasoning or "资源目录" in result.reasoning
    mock_get_llm.assert_not_called()
    mock_identify.assert_not_called()
    mock_chat.generate_text.assert_not_called()


@pytest.mark.parametrize(
    "query",
    [
        "数据集里有哪些订单表",
        "统计客户订单有哪些",
        "查一下机房数据集的负载趋势",
    ],
)
@pytest.mark.asyncio
async def test_route_query_resource_catalog_shortcut_does_not_steal_data_queries(
    mock_agents_metadata,
    query,
):
    """真实查数句不应被资源目录短路误伤。"""
    service = RouterService()
    llm_resp_content = json.dumps({
        "thought": "Business data query.",
        "agent_name": "ChatBI",
        "confidence": 0.91,
        "intent": "DATA_QUERY",
        "domain": "chatbi_business_data",
        "operation": "lookup",
    })
    mock_chat = _mock_chat_client(llm_resp_content)

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.intent_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:

        mock_fetch.return_value = mock_agents_metadata
        mock_identify.return_value = IntentResponse(
            intent=IntentType.DATA_QUERY,
            confidence=0.93,
            reasoning="业务查数",
            entities=[],
        )
        mock_get_llm.return_value = object()
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query(query)

    assert result.agent_id == "agent-chatbi"
    mock_get_llm.assert_called()


def test_parse_router_json_recovers_agent_name_from_truncated_payload():
    """thought 被截断时，只要 agent_name 已写出就应恢复路由。"""
    truncated = (
        '{\n  "agent_name": "general-chat",\n  "confidence": 0.88,\n'
        '  "thought": "1. 指代消解：用户询问权限清单。\\n2. 语义匹配：'
        'chat-bi 专注于数据查询、SQL '
    )
    parsed = RouterService._parse_router_json(truncated)
    assert parsed is not None
    assert parsed["agent_name"] == "general-chat"
    assert float(parsed.get("confidence", 0)) == 0.88


def test_parse_router_json_returns_none_when_agent_name_missing():
    truncated = (
        '{\n  "thought": "1. 指代消解：用户询问“我有哪些数据集权限”，意图明确。\\n'
        '3. 语义匹配：清单中的 `chat-bi` 专注于“数据查询、SQL '
    )
    assert RouterService._parse_router_json(truncated) is None


@pytest.mark.asyncio
async def test_route_query_uncertain_topic_after_chatbi_reaches_semantic_router(mock_agents_metadata):
    """上一轮 ChatBI 后的灰区话题应重新分诊，不能由粘性规则直接判给通用助手。"""
    service = RouterService()
    mock_chat = _mock_chat_client(json.dumps({
        "thought": "This is an external project status question.",
        "agent_name": "general-chat",
        "confidence": 0.94,
    }))

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.intent_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:

        mock_fetch.return_value = mock_agents_metadata
        mock_identify.return_value = IntentResponse(
            intent=IntentType.DATA_QUERY,
            confidence=0.93,
            reasoning="问候后提出内部机房列表查询",
            entities=["机房列表"],
        )
        mock_get_llm.return_value = object()
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query(
            "看看我开源项目，小星星情况",
            last_agent_name="ChatBI",
        )

    assert result is not None
    assert result.agent_id == "agent-general"
    assert result.reasoning != (
        "上一轮虽由数据智能体处理，但本轮不再具备内部业务库查数或数据追问信号，"
        "按话题切换重新分诊至通用助手"
    )
    mock_get_llm.assert_called_once()
    mock_chat.generate_text.assert_called_once()


@pytest.mark.asyncio
async def test_route_query_data_followup_after_chatbi_reuses_sticky_agent_without_llm(mock_agents_metadata):
    """上一轮 ChatBI 后，纯结果追问应复用会话智能体并跳过两次路由模型调用。"""
    service = RouterService()
    llm_resp_content = json.dumps({
        "thought": "Follow-up visualization on previous query result.",
        "agent_name": "ChatBI",
        "confidence": 0.92,
    })
    mock_chat = _mock_chat_client(llm_resp_content)

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.intent_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:

        mock_fetch.return_value = mock_agents_metadata
        mock_identify.return_value = IntentResponse(
            intent=IntentType.DATA_QUERY,
            confidence=0.93,
            reasoning="问候后提出内部机房列表查询",
            entities=["机房列表"],
        )
        mock_get_llm.return_value = object()
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query(
            "把上面的结果画成柱状图",
            last_agent_name="ChatBI",
        )

    assert result.agent_id == "agent-chatbi"
    assert result.relation_to_previous == "followup"
    assert result.user_action_type == "transform_context"
    mock_identify.assert_not_awaited()
    mock_get_llm.assert_not_awaited()
    mock_chat.generate_text.assert_not_called()


@pytest.mark.asyncio
async def test_route_query_greeting_compound_still_calls_llm(mock_agents_metadata):
    """问候 + 业务诉求的复合句不应走路由短路。"""
    service = RouterService()
    llm_resp_content = json.dumps({
        "thought": "User wants room list.",
        "agent_name": "ChatBI",
        "confidence": 0.9,
        "intent": "DATA_QUERY",
        "domain": "chatbi_business_data",
        "operation": "lookup",
    })
    mock_chat = _mock_chat_client(llm_resp_content)

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.intent_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:

        mock_fetch.return_value = mock_agents_metadata
        mock_identify.return_value = IntentResponse(
            intent=IntentType.DATA_QUERY,
            confidence=0.93,
            reasoning="问候后提出内部机房列表查询",
            entities=["机房列表"],
        )
        mock_get_llm.return_value = object()
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query("你好，查一下所有机房的列表")

    assert result.agent_id == "agent-chatbi"
    mock_chat.generate_text.assert_called_once()


@pytest.mark.asyncio
async def test_route_query_uses_bounded_output_and_compact_router_prompt(mock_agents_metadata):
    """普通路由保留关键边界，但使用精简提示词和受限输出长度。"""
    service = RouterService()
    mock_chat = AsyncMock()
    mock_chat.generate_structured_dict.return_value = {
        "agent_name": "general-chat",
        "confidence": 0.91,
        "secondary_agents": [],
        "intent": "GENERAL",
        "domain": "general",
        "operation": "explain",
    }

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.build_accessible_resource_catalog", new_callable=AsyncMock, return_value=""), \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle", return_value=mock_chat):
        mock_fetch.return_value = mock_agents_metadata
        mock_get_llm.return_value = object()

        result = await service.route_query("请解释自动路由和指定专家的执行差异")

    assert result is not None
    mock_get_llm.assert_awaited_once_with(
        temperature=0.0,
        max_output_tokens=512,
        ignore_session_reasoning_overrides=True,
        source="router",
    )
    prompt = mock_chat.generate_structured_dict.call_args.args[0][0].content[0].text
    assert len(prompt) < len(RouterService.DEFAULT_SYSTEM_PROMPT)
    assert "当前系统/本机/这台机器/服务器运行状态" in prompt
    assert "不要因为出现\"负载/利用率/CPU/内存\"等词就直接判为数据查询" in prompt
    assert "内部 SOP/流程/规范/手册" in prompt
    assert '"agent_name"' in prompt


@pytest.mark.asyncio
async def test_route_query_does_not_add_text_call_when_structured_output_fails(
    mock_agents_metadata,
):
    """原生结构化能力已存在但失败时，不再追加一次普通文本模型调用。"""
    service = RouterService()

    class StructuredFailureClient:
        last_structured_output_status = "error"

        def __init__(self):
            self.structured_calls = 0
            self.text_calls = 0

        async def generate_structured_dict(self, messages, structured_model):
            self.structured_calls += 1
            return None

        async def generate_text(self, messages):
            self.text_calls += 1
            raise AssertionError("structured output failure must not trigger text fallback")

    client = StructuredFailureClient()

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.build_accessible_resource_catalog", new_callable=AsyncMock, return_value=""), \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle", return_value=client):
        mock_fetch.return_value = mock_agents_metadata
        mock_get_llm.return_value = object()

        result = await service.route_query("请解释自动路由的执行逻辑")

    assert result is not None
    assert client.text_calls == 0
    assert client.structured_calls == 2
    assert mock_get_llm.await_count == 2


@pytest.mark.asyncio
async def test_route_query_timeout_falls_back_to_general(mock_agents_metadata):
    """路由大模型超时时，重试后应平滑安全降级至通用助手，不挂死异常。"""
    service = RouterService()
    mock_chat = AsyncMock()
    mock_chat.generate_structured_dict.side_effect = TimeoutError("Simulated timeout")

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.build_accessible_resource_catalog", new_callable=AsyncMock, return_value=""), \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle", return_value=mock_chat):
        mock_fetch.return_value = mock_agents_metadata
        mock_get_llm.return_value = object()

        result = await service.route_query("请统计本周各智能体的Token消耗总量并排名")

    assert result is not None
    assert result.agent_id == "agent-general"
    assert "Routing exception" in result.reasoning

