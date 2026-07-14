import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from app.services.ai.tools.mcp_client import McpClientService, McpSseSession
from httpx import Response

pytestmark = pytest.mark.no_infrastructure

# Mock server info
SERVER_ID = "test-server"
SSE_URL = "http://mock-mcp-server/mcp"
AUTH_HEADERS = {"Authorization": "Bearer test-token"}

@pytest.fixture
def mock_session():
    session = McpSseSession(SERVER_ID, SSE_URL, AUTH_HEADERS)
    session.mcp_session_id = "old-session-id"
    session.is_direct_http = True
    return session

@pytest.mark.asyncio
async def test_session_expired_retry(mock_session):
    # Setup mock for httpx.AsyncClient
    with patch("httpx.AsyncClient") as MockClient:
        # Mock context manager
        mock_client_instance = AsyncMock()
        MockClient.return_value.__aenter__.return_value = mock_client_instance
        
        # Define responses: 
        # 1. 401 SessionExpired
        # 2. 200 Success
        
        expired_response = Response(401, json={
            "RequestId": "req-1",
            "Code": "SessionExpired",
            "Message": "Session is expired"
        })
        
        success_response = Response(200, json={
            "result": {"tools": []}
        })
        
        mock_client_instance.post.side_effect = [expired_response, success_response]

        # Call the method
        result = await McpClientService._direct_http_rpc(mock_session, "tools/list", {})

        # Verify retry happened (called twice)
        assert mock_client_instance.post.call_count == 2
        
        # Verify first call had session ID
        call_args_list = mock_client_instance.post.call_args_list
        first_call_headers = call_args_list[0].kwargs["headers"]
        assert first_call_headers.get("mcp-session-id") == "old-session-id"
        
        # Verify second call did NOT have session ID (cleared)
        second_call_headers = call_args_list[1].kwargs["headers"]
        assert "mcp-session-id" not in second_call_headers
        
        # Verify result
        assert result == {"tools": []}

@pytest.mark.asyncio
async def test_session_expired_retry_fail(mock_session):
    # Test that it doesn't loop infinitely
    with patch("httpx.AsyncClient") as MockClient:
        mock_client_instance = AsyncMock()
        MockClient.return_value.__aenter__.return_value = mock_client_instance
        
        expired_response = Response(401, json={
            "RequestId": "req-2",
            "Code": "SessionExpired",
            "Message": "Session is expired"
        })
        
        mock_client_instance.post.return_value = expired_response

        # Expect exception after retries exhausted
        with pytest.raises(Exception) as excinfo:
            await McpClientService._direct_http_rpc(mock_session, "tools/list", {})
        
        # Should call 2 times (initial + 1 retry)
        assert mock_client_instance.post.call_count == 2
        assert "HTTP 401" in str(excinfo.value)
