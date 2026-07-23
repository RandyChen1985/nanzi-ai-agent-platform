import pytest
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch
from app.services.ai.router_service import RouterService, RouteResult
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
         patch("app.services.ai.router_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify:
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
        "confidence": 0.95
    })
    
    mock_llm = object()
    mock_chat = _mock_chat_client(llm_resp_content)
    
    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
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
        
        assert isinstance(result, RouteResult)
        assert result.agent_id == "agent-chatbi"
        assert result.confidence == 0.95
        assert result.reasoning == "Query asks for data table."
        assert result.turn_labels == []
        assert result.relation_to_previous == "unknown"
        assert result.user_action_type == "unknown"


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
         patch("app.services.ai.router_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
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
         patch("app.services.ai.router_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
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
    }))
    evidence = IntentResponse(
        intent=IntentType.DATA_QUERY,
        confidence=0.95,
        reasoning="模型仅因统计动作词产生了数据意图",
        entities=["机器文件数"],
        domain="local_file",
        operation="aggregate",
    )

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:
        mock_fetch.return_value = mock_agents_metadata
        mock_identify.return_value = evidence
        mock_get_llm.return_value = object()
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query("统计一下我机器的文件数")

    assert result.agent_id == "agent-general"
    assert result.chatbi_mode == "deny"
    assert result.request_should_delegate is False
    assert result.request_capability == "data_query"


@pytest.mark.asyncio
async def test_route_query_business_load_metric_still_uses_llm(mock_agents_metadata):
    """业务/机房负载指标不命中本机诊断捷径，仍由 LLM 路由给 ChatBI。"""
    service = RouterService()
    llm_resp_content = json.dumps({
        "thought": "Query asks for IDC load trend metrics.",
        "agent_name": "ChatBI",
        "confidence": 0.91
    })

    mock_llm = object()
    mock_chat = _mock_chat_client(llm_resp_content)

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
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
        permissions=SimpleNamespace(agents=["agent-rag"])
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
        "confidence": 0.95
    })

    mock_llm = object()
    mock_chat = _mock_chat_client(llm_resp_content)

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
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
    """高置信结构化数据意图只在具备数据能力的候选中路由，不依赖业务对象词。"""
    service = RouterService()
    evidence = IntentResponse(
        intent=IntentType.DATA_QUERY,
        confidence=0.91,
        reasoning="请求系统结构化记录",
        entities=["任意业务对象"],
        domain="chatbi_business_data",
    )
    router_response = json.dumps({
        "thought": "在数据候选中选择 SQL 分析专家",
        "agent_name": "ChatBI",
        "confidence": 0.9,
    })
    mock_chat = _mock_chat_client(router_response)

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.resolve_authorized_dataset_candidates", new_callable=AsyncMock) as mock_candidates, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:
        mock_fetch.return_value = mock_agents_metadata
        mock_identify.return_value = evidence
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
    assert result.intent_info == evidence
    routed_messages = mock_chat.generate_text.call_args.args[0]
    system_prompt = routed_messages[0].content[0].text
    assert "ID: ChatBI" in system_prompt
    assert "ID: general-chat" not in system_prompt


@pytest.mark.asyncio
async def test_route_query_public_company_lookup_not_forced_to_data_agent(mock_agents_metadata):
    """公网公司资料查询即使被语义模型误判为 DATA_QUERY，也不能收缩或落到 ChatBI。"""
    service = RouterService()
    evidence = IntentResponse(
        intent=IntentType.DATA_QUERY,
        confidence=0.93,
        reasoning="误判为查询业务数据",
        entities=["有孚网络"],
    )
    mock_chat = _mock_chat_client(json.dumps({
        "thought": "错误地选择数据查询智能体",
        "agent_name": "ChatBI",
        "confidence": 0.88,
    }))

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:
        mock_fetch.return_value = mock_agents_metadata
        mock_identify.return_value = evidence
        mock_get_llm.return_value = object()
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query("查一下有孚网络公司信息")

    assert result.agent_id == "agent-general"
    assert result.intent_info == evidence
    assert "without internal structured-data source" in result.reasoning

    routed_messages = mock_chat.generate_text.call_args.args[0]
    system_prompt = routed_messages[0].content[0].text
    assert "ID: ChatBI" in system_prompt
    assert "ID: general-chat" in system_prompt


@pytest.mark.asyncio
async def test_route_query_data_evidence_survives_invalid_constrained_route(mock_agents_metadata):
    """受约束候选的路由模型失败时，应回落 Main 并保留语义供其委派。"""
    service = RouterService()
    evidence = IntentResponse(
        intent=IntentType.DATA_QUERY,
        confidence=0.91,
        reasoning="请求系统结构化记录",
        entities=[],
    )
    mock_chat = _mock_chat_client(json.dumps({
        "thought": "错误返回不存在的候选",
        "agent_name": "not-in-candidates",
        "confidence": 0.9,
    }))

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
         patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
         patch("app.services.ai.router_service.chat_client_from_handle") as mock_chat_factory:
        mock_fetch.return_value = mock_agents_metadata
        mock_identify.return_value = evidence
        mock_get_llm.return_value = object()
        mock_chat_factory.return_value = mock_chat

        result = await service.route_query("列出某个系统中的全部记录")

    assert result.agent_id == "agent-general"
    assert result.intent_info == evidence


@pytest.mark.asyncio
async def test_route_query_greeting_shortcut_skips_llm(mock_agents_metadata):
    """纯问候应短路至通用助手，不调用路由 LLM。"""
    service = RouterService()
    mock_chat = _mock_chat_client("{}")

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
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
         patch("app.services.ai.router_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
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
    })
    mock_chat = _mock_chat_client(llm_resp_content)

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
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
         patch("app.services.ai.router_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
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
async def test_route_query_data_followup_after_chatbi_still_uses_llm(mock_agents_metadata):
    """上一轮 ChatBI 后，纯数据结果追问仍应走路由 LLM（保留粘性，不误切通用助手）。"""
    service = RouterService()
    llm_resp_content = json.dumps({
        "thought": "Follow-up visualization on previous query result.",
        "agent_name": "ChatBI",
        "confidence": 0.92,
    })
    mock_chat = _mock_chat_client(llm_resp_content)

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
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
    mock_get_llm.assert_called_once()
    mock_chat.generate_text.assert_called_once()


@pytest.mark.asyncio
async def test_route_query_greeting_compound_still_calls_llm(mock_agents_metadata):
    """问候 + 业务诉求的复合句不应走路由短路。"""
    service = RouterService()
    llm_resp_content = json.dumps({
        "thought": "User wants room list.",
        "agent_name": "ChatBI",
        "confidence": 0.9,
    })
    mock_chat = _mock_chat_client(llm_resp_content)

    with patch.object(service, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
         patch("app.services.ai.router_service.intent_service.identify_intent", new_callable=AsyncMock) as mock_identify, \
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
