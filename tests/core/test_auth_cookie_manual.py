import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.config import settings

@pytest.mark.asyncio
async def test_require_api_key_cookie():
    """Test that require_api_key dependency works with cookies."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Login to get cookie (Simulate admin login)
        # Assuming we can mock or use a known user. 
        # For simplicity, we can inject a valid API key into a cookie manually if we know one,
        # but better to assume we don't know a valid key and rely on integration test flow or mocking.
        #
        # Let's try to access a protected endpoint that uses `require_api_key`.
        # /api/portal/auth/user_apikey is the one EmbedChat uses.
        
        # Test Case 1: No auth -> 401
        resp = await client.get("/api/portal/auth/user_apikey")
        assert resp.status_code == 401, "Should fail without any auth"

        # Test Case 2: Header Auth -> 200 (Existing behavior verification)
        # We need a valid API key. If we can't easily get one, we might need to mock AuthService.verify_api_key.
        # However, checking if 401 is returned when valid key is missing is a good negative test.
        # To test positive case properly, we'd need to mock DB or have a fixture.
        pass

# Since I don't have a full test DB setup fixture handy in this context, 
# I will create a test that specifically mocks the dependency or AuthService to verify logic.

from unittest.mock import patch, MagicMock
from app.services.auth_service import AuthService
from app.core.dependencies import require_api_key
from fastapi import Request, HTTPException

@pytest.mark.asyncio
async def test_require_api_key_logic():
    # Mock request with cookie
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}
    mock_request.cookies = {"admin_token": "valid_cookie_token"}
    mock_request.state = MagicMock()

    # Mock DB
    mock_db = MagicMock()
    mock_response = MagicMock()

    # Mock AuthService
    with patch("app.services.auth_service.AuthService.verify_api_key", return_value={"user_id": 1}) as mock_verify:
        # Call dependency
        user = await require_api_key(mock_request, mock_response, api_key_header=None, authorization=None, db=mock_db)
        
        # Assertions
        assert user.get("user_id") == 1
        mock_verify.assert_called_with("valid_cookie_token", mock_db)
        # 凭据来自 Cookie，应重新下发以顺延 max_age（与会话滑动续期对齐）
        assert mock_response.set_cookie.called, "经 Cookie 认证时应续期会话 Cookie"
        
    # Mock request without cookie or header
    mock_request_empty = MagicMock(spec=Request)
    mock_request_empty.headers = {}
    mock_request_empty.cookies = {}
    
    try:
        await require_api_key(mock_request_empty, MagicMock(), api_key_header=None, authorization=None, db=mock_db)
        assert False, "Should have raised HTTPException"
    except HTTPException as e:
        assert e.status_code == 401
