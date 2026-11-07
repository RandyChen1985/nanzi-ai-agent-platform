import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from app.services.ai.router_service import RouterService
from app.services.ai.knowledge_catalog import AuthorizedKnowledgeCatalog, KnowledgeBaseCatalogItem


pytestmark = pytest.mark.no_infrastructure


def test_router_prompt_documents_general_and_knowledge_boundaries():
    prompt = RouterService.DEFAULT_SYSTEM_PROMPT
    assert "天气" in prompt
    assert "代码/API/编程" in prompt
    assert "内部 SOP/流程/规范/手册" in prompt
    assert "即使带有「查询/查一下」" in prompt
    assert "难以区分" in prompt
    assert "优先选择" in prompt
    assert '"intent":' in prompt
    assert '"intent_confidence":' in prompt
    assert '"intent_reasoning":' in prompt
    assert "chatbi_business_data" in prompt
    assert "local_file" in prompt
    assert "thought 不超过 40 个汉字" in prompt
    assert "accessible_resources_context" in prompt
    assert "明确语义关联" in prompt


@pytest.mark.asyncio
async def test_router_injects_authorized_resource_catalog_before_semantic_routing():
    router = RouterService()
    agents = [
        {
            "id": "data-agent",
            "name": "chat-bi",
            "description": "结构化业务数据查询",
            "capabilities": ["data_query"],
        },
        {
            "id": "knowledge-agent",
            "name": "knowledge-base",
            "description": "内部知识库和文档问答",
            "capabilities": ["knowledge_base"],
        },
        {
            "id": "general-agent",
            "name": "general-chat",
            "description": "通用问答",
            "capabilities": ["chat"],
        },
    ]
    mock_chat = AsyncMock()
    mock_chat.generate_structured_dict.return_value = None
    mock_chat.generate_text.return_value = (
        '{"agent_name":"knowledge-base","confidence":0.93,'
        '"secondary_agents":[],"intent":"KNOWLEDGE_BASE",'
        '"domain":"internal_docs","intent_confidence":0.91,'
        '"intent_reasoning":"匹配用户可访问的车辆手册","thought":"命中内部知识库"}'
    )
    resource_catalog = (
        "## 当前用户可访问的内部资源摘要\n"
        "### 知识库\n"
        "- 蔚来汽车手册：车辆功能、辅助驾驶和使用说明"
    )

    with patch.object(router, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
        patch.object(router, "_filter_agents_for_user", new_callable=AsyncMock) as mock_filter, \
        patch(
            "app.services.ai.router_service.build_accessible_resource_catalog",
            new_callable=AsyncMock,
        ) as mock_catalog, \
        patch(
            "app.services.ai.router_service.load_authorized_knowledge_catalog",
            new_callable=AsyncMock,
        ) as mock_knowledge_catalog, \
        patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
        patch("app.services.ai.router_service.chat_client_from_handle") as mock_factory:
        mock_fetch.return_value = agents
        mock_filter.return_value = agents
        mock_catalog.return_value = resource_catalog
        mock_knowledge_catalog.return_value = AuthorizedKnowledgeCatalog(
            status="available",
            items=(
                KnowledgeBaseCatalogItem(
                    ragflow_dataset_id="kb-ev",
                    name="蔚来汽车手册",
                    description="车辆功能、辅助驾驶和使用说明",
                ),
            ),
        )
        mock_get_llm.return_value = object()
        mock_factory.return_value = mock_chat

        result = await router.route_query(
            "如何开启辅助驾驶功能",
            user_id=7,
            is_admin=False,
        )

    assert result is not None
    assert result.agent_id == "knowledge-agent"
    mock_catalog.assert_awaited_once()
    assert mock_catalog.await_args.kwargs["user_id"] == 7
    assert mock_catalog.await_args.kwargs["user_name"] is None
    assert mock_catalog.await_args.kwargs["is_admin"] is False
    assert mock_catalog.await_args.kwargs["knowledge_catalog"] is mock_knowledge_catalog.return_value
    system_prompt = mock_chat.generate_text.call_args.args[0][0].content[0].text
    assert "当前用户可访问的内部资源摘要" in system_prompt
    assert "蔚来汽车手册" in system_prompt


@pytest.mark.asyncio
async def test_router_emits_safe_progress_stages_without_raw_reasoning():
    router = RouterService()
    events = []
    agents = [
        {
            "id": "general-agent",
            "name": "general-chat",
            "description": "通用问答",
            "capabilities": ["chat"],
        },
        {
            "id": "data-agent",
            "name": "chat-bi",
            "description": "业务数据查询",
            "capabilities": ["data_query"],
        },
    ]

    mock_chat = AsyncMock()
    mock_chat.generate_structured_dict.return_value = {
        "agent_name": "general-chat",
        "confidence": 0.9,
        "secondary_agents": [],
        "intent": "GENERAL",
        "domain": "general",
        "thought": "内部路由理由不应进入进度事件",
    }

    async def on_progress(event):
        events.append(event)

    with patch.object(router, "_fetch_agents_from_db", new_callable=AsyncMock, return_value=agents), \
        patch.object(router, "_filter_agents_for_user", new_callable=AsyncMock, return_value=agents), \
        patch(
            "app.services.ai.router_service.build_accessible_resource_catalog",
            new_callable=AsyncMock,
            return_value="",
        ), \
        patch(
            "app.services.ai.router_service.load_authorized_knowledge_catalog",
            new_callable=AsyncMock,
            return_value=None,
        ), \
        patch(
            "app.services.ai.router_service.get_llm_async",
            new_callable=AsyncMock,
            return_value=object(),
        ), \
        patch("app.services.ai.router_service.chat_client_from_handle", return_value=mock_chat):
        result = await router.route_query("帮我写一段说明", on_progress=on_progress, is_admin=True)

    assert result is not None
    assert [event["id"] for event in events] == [
        "route:candidate_catalog",
        "route:candidate_catalog",
        "route:knowledge_catalog",
        "route:knowledge_catalog",
        "route:router_model",
        "route:router_model",
    ]
    assert events[0]["status"] == "pending"
    assert events[1]["status"] == "success"
    assert events[2]["status"] == "pending"
    assert events[3]["status"] == "success"
    assert events[4]["status"] == "pending"
    assert events[5]["status"] == "success"
    assert all("thought" not in event for event in events)
    assert "thought" not in events[3].get("details", "")


@pytest.mark.asyncio
async def test_router_does_not_select_knowledge_agent_when_catalog_has_no_match():
    router = RouterService()
    agents = [
        {
            "id": "knowledge-agent",
            "name": "knowledge-base",
            "description": "内部知识库和文档问答",
            "capabilities": ["knowledge_base"],
        },
        {
            "id": "general-agent",
            "name": "general-chat",
            "description": "通用问答",
            "capabilities": ["chat"],
        },
    ]
    mock_chat = AsyncMock()
    mock_chat.generate_structured_dict.return_value = None
    mock_chat.generate_text.return_value = (
        '{"agent_name":"knowledge-base","confidence":0.93,'
        '"secondary_agents":[],"intent":"KNOWLEDGE_BASE",'
        '"domain":"internal_docs","intent_confidence":0.91,'
        '"thought":"命中知识库"}'
    )

    with patch.object(router, "_fetch_agents_from_db", new_callable=AsyncMock) as mock_fetch, \
        patch.object(router, "_filter_agents_for_user", new_callable=AsyncMock) as mock_filter, \
        patch(
            "app.services.ai.router_service.load_authorized_knowledge_catalog",
            new_callable=AsyncMock,
        ) as mock_knowledge_catalog, \
        patch(
            "app.services.ai.router_service.build_accessible_resource_catalog",
            new_callable=AsyncMock,
            return_value="",
        ), \
        patch("app.services.ai.router_service.get_llm_async", new_callable=AsyncMock) as mock_get_llm, \
        patch("app.services.ai.router_service.chat_client_from_handle") as mock_factory:
        mock_fetch.return_value = agents
        mock_filter.return_value = agents
        mock_knowledge_catalog.return_value = AuthorizedKnowledgeCatalog(
            status="available",
            items=(
                KnowledgeBaseCatalogItem(
                    ragflow_dataset_id="kb-ev",
                    name="蔚来汽车手册",
                    description="车辆功能与换电操作说明",
                ),
            ),
        )
        mock_get_llm.return_value = object()
        mock_factory.return_value = mock_chat

        result = await router.route_query(
            "查看春秋航空9C6475航班的准点率和退改签政策",
            user_id=7,
            is_admin=False,
        )

    assert result is not None
    assert result.agent_id == "general-agent"
    assert result.agent_name == "general-chat"
    assert result.turn_kind == "general"
    assert result.should_delegate is False
    assert result.knowledge_fallback_allowed is True


@pytest.mark.asyncio
async def test_router_context_awareness():
    """
    Test that the router can use history to understand ambiguous queries.
    """
    router = RouterService()
    
    # Mock dependencies
    with patch.object(router, '_fetch_agents_from_db', new_callable=AsyncMock) as mock_fetch, \
         patch('app.services.ai.intent_service.intent_service.identify_intent', new_callable=AsyncMock) as mock_intent, \
         patch('app.services.config_service.ConfigService.get', new_callable=AsyncMock) as mock_config:
        
        mock_fetch.return_value = [
            {"id": "1", "name": "chat-bi", "description": "Query database for metrics", "capabilities": ["data_query"]},
            {"id": "2", "name": "knowledge-base", "description": "Answer questions from documents", "capabilities": ["knowledge_base"]},
            {"id": "3", "name": "general-chat", "description": "General assistant", "capabilities": []}
        ]
        
        from app.services.ai.intent_service import IntentResponse, IntentType
        mock_intent.return_value = IntentResponse(intent=IntentType.GENERAL, confidence=0.8, reasoning="mock")
        mock_config.return_value = RouterService.DEFAULT_SYSTEM_PROMPT
        
        # Mock LLM
        with patch('app.services.ai.router_service.get_llm_async') as mock_get_llm, \
             patch('app.services.ai.router_service.chat_client_from_handle') as mock_chat_factory:
            mock_llm_instance = MagicMock()
            mock_chat = AsyncMock()
            mock_chat.generate_structured_dict.return_value = None
            mock_chat.generate_text.return_value = '{"thought": "test", "agent_name": "chat-bi", "confidence": 0.9}'
            mock_get_llm.return_value = mock_llm_instance
            mock_chat_factory.return_value = mock_chat
            
            # Scenario: User asked about room temperatures (chat-bi), then asked "how about there?"
            history = [
                {"role": "user", "content": "What's the temperature in IDC Room 1?"},
                {"role": "assistant", "content": "The temperature in IDC Room 1 is 22°C."}
            ]
            user_input = "Show me the trend for it." # 'it' refers to IDC Room 1 temperature
            
            # We want to see if the system prompt contains the history
            await router.route_query(user_input, history=history, is_admin=True)
            
            # Check the call to LLM
            called_messages = mock_chat.generate_text.call_args[0][0]
            system_msg = called_messages[0].content[0].text
            human_msg = called_messages[1].content[0].text
            
            assert "Conversation History" in system_msg
            assert "IDC Room 1" in system_msg
            assert "Latest User Query: Show me the trend for it." in human_msg

@pytest.mark.asyncio
async def test_router_heuristic_bypass_history():
    """
    Test that routing works even if history is provided.
    (Heuristics were simplified, now relying on unified LLM routing)
    """
    router = RouterService()
    
    with patch.object(router, '_fetch_agents_from_db', new_callable=AsyncMock) as mock_fetch, \
         patch('app.services.ai.router_service.get_llm_async') as mock_get_llm, \
         patch('app.services.ai.router_service.chat_client_from_handle') as mock_chat_factory:
        
        mock_fetch.return_value = [
            {"id": "1", "name": "chat-bi", "description": "Query database", "capabilities": ["data_query"]},
            {"id": "2", "name": "general-chat", "description": "General assistant", "capabilities": []},
        ]
        
        mock_llm_instance = MagicMock()
        mock_chat = AsyncMock()
        mock_chat.generate_structured_dict.return_value = None
        mock_chat.generate_text.return_value = (
            '{"thought": "Standard Routing", "agent_name": "chat-bi", "confidence": 0.9,'
            ' "intent": "DATA_QUERY", "domain": "chatbi_business_data"}'
        )
        mock_get_llm.return_value = mock_llm_instance
        mock_chat_factory.return_value = mock_chat
        
        # This input previously triggered heuristic
        user_input = "查一下机房温度" 
        
        result = await router.route_query(user_input, history=[{"role": "user", "content": "hello"}], is_admin=True)
        
        assert result is not None
        assert result.agent_id == "1"
        assert result.confidence == 0.9
