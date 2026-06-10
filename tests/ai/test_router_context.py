import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from app.services.ai.router_service import RouterService, RouteResult


pytestmark = pytest.mark.no_infrastructure


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
            await router.route_query(user_input, history=history)
            
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
            {"id": "1", "name": "chat-bi", "description": "Query database", "capabilities": ["data_query"]}
        ]
        
        mock_llm_instance = MagicMock()
        mock_chat = AsyncMock()
        mock_chat.generate_text.return_value = '{"thought": "Standard Routing", "agent_name": "chat-bi", "confidence": 0.9}'
        mock_get_llm.return_value = mock_llm_instance
        mock_chat_factory.return_value = mock_chat
        
        # This input previously triggered heuristic
        user_input = "查一下机房温度" 
        
        result = await router.route_query(user_input, history=[{"role": "user", "content": "hello"}])
        
        assert result is not None
        assert result.agent_id == "1"
        assert result.confidence == 0.9
