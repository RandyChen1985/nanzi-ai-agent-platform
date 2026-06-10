import pytest
import json
from unittest.mock import AsyncMock, patch
from app.services.ai.router_service import RouterService


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_router_single_intent():
    router = RouterService()
    
    # Mock LLM Response for single intent
    mock_llm_response = {
        "thought": "User wants to query data.",
        "agent_name": "chat-bi",
        "secondary_agents": [],
        "confidence": 0.99
    }
    
    # Mock agents from DB
    mock_agents = [
        {"id": "agent-1", "name": "chat-bi", "description": "Data Expert", "capabilities": ["data"]},
        {"id": "agent-2", "name": "knowledge-base", "description": "Doc Expert", "capabilities": ["knowledge"]}
    ]
    
    mock_chat = AsyncMock()
    mock_chat.generate_text.return_value = json.dumps(mock_llm_response)
    with patch.object(RouterService, '_fetch_agents_from_db', return_value=mock_agents):
        with patch('app.services.ai.router_service.get_llm_async', AsyncMock(return_value=object())), \
             patch('app.services.ai.router_service.chat_client_from_handle', return_value=mock_chat):
            result = await router.route_query("查询能耗", enable_multi_agent=True)
            
            assert result is not None
            assert result.agent_id == "agent-1"
            assert result.secondary_agents == []
            assert result.confidence == 0.99

@pytest.mark.asyncio
async def test_router_multi_intent():
    router = RouterService()
    
    # Mock LLM Response for multi intent
    mock_llm_response = {
        "thought": "User wants data and knowledge.",
        "agent_name": "chat-bi",
        "secondary_agents": ["knowledge-base"],
        "confidence": 0.95
    }
    
    # Mock agents from DB
    mock_agents = [
        {"id": "agent-1", "name": "chat-bi", "description": "Data Expert", "capabilities": ["data"]},
        {"id": "agent-2", "name": "knowledge-base", "description": "Doc Expert", "capabilities": ["knowledge"]}
    ]
    
    mock_chat = AsyncMock()
    mock_chat.generate_text.return_value = json.dumps(mock_llm_response)
    with patch.object(RouterService, '_fetch_agents_from_db', return_value=mock_agents):
        with patch('app.services.ai.router_service.get_llm_async', AsyncMock(return_value=object())), \
             patch('app.services.ai.router_service.chat_client_from_handle', return_value=mock_chat):
            # Case 1: Multi-agent enabled
            result = await router.route_query("查询能耗并看看制度", enable_multi_agent=True)
            assert result.agent_id == "agent-1"
            assert result.secondary_agents == ["agent-2"]
            
            # Case 2: Multi-agent disabled (should ignore secondaries)
            result_single = await router.route_query("查询能耗并看看制度", enable_multi_agent=False)
            assert result_single.agent_id == "agent-1"
            assert result_single.secondary_agents == []

@pytest.mark.asyncio
async def test_router_fallback_on_low_confidence():
    router = RouterService()
    
    mock_llm_response = {
        "thought": "Not sure.",
        "agent_name": "chat-bi",
        "secondary_agents": ["knowledge-base"],
        "confidence": 0.4
    }
    
    mock_agents = [
        {"id": "agent-1", "name": "chat-bi", "description": "Data Expert", "capabilities": ["data"]},
        {"id": "agent-3", "name": "general-chat", "description": "Chat", "capabilities": []}
    ]
    
    mock_chat = AsyncMock()
    mock_chat.generate_text.return_value = json.dumps(mock_llm_response)
    with patch.object(RouterService, '_fetch_agents_from_db', return_value=mock_agents):
        with patch('app.services.ai.router_service.get_llm_async', AsyncMock(return_value=object())), \
             patch('app.services.ai.router_service.chat_client_from_handle', return_value=mock_chat):
            result = await router.route_query("模糊问题", enable_multi_agent=True)
            
            assert result.agent_id == "agent-3" # Fallback to general-chat
            assert result.secondary_agents == []
