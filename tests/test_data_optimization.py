
import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from app.services.ai.tools.data_api import call_external_sql_api

@pytest.mark.asyncio
async def test_call_external_sql_api_optimization():
    # Mock Dependencies
    with patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config_get, \
         patch("app.core.http_client.GlobalHttpClient.get_client", new_callable=AsyncMock) as mock_get_client, \
         patch("app.core.redis.get_redis", new_callable=AsyncMock) as mock_get_redis:

        # 1. Setup Configuration Mocks
        mock_config_get.side_effect = lambda key, default=None: {
            "external_sql_api_url": "http://mock-api.com/query",
            "external_sql_api_key": "mock-key",
            "external_sql_data_source": "default_ch",
            "data_api_timeout_seconds": "60"
        }.get(key, default)

        # 2. Setup Redis Mock
        mock_redis = AsyncMock()
        mock_get_redis.return_value = mock_redis
        
        # Scenario A: Cache MISS
        mock_redis.get.return_value = None # No cache
        
        # Setup HTTP Client Mock
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.is_error = False
        mock_response.json.return_value = {"code": 200, "data": [{"col": "val"}]}
        mock_client.post.return_value = mock_response
        mock_get_client.return_value = mock_client

        # Execute
        result = await call_external_sql_api("SELECT * FROM test", "default_ch")
        
        # Verifications A
        assert result == '[{"col": "val"}]'
        # Verify Cache was checked
        mock_redis.get.assert_called_once()
        # Verify Cache was set
        mock_redis.set.assert_called_once()
        # Verify Global Client was used
        mock_get_client.assert_called_once()
        mock_client.post.assert_called_once()

        print("✅ Scenario A (Cache Miss) Passed: Client Used, Cache Set.")

        # Scenario B: Cache HIT
        mock_redis.get.reset_mock()
        mock_redis.set.reset_mock()
        mock_client.post.reset_mock()
        
        mock_redis.get.return_value = '{"cached": "true"}'
        
        result_cached = await call_external_sql_api("SELECT * FROM test", "default_ch")
        
        # Verifications B
        assert result_cached == '{"cached": "true"}'
        # Verify Cache was checked
        mock_redis.get.assert_called_once()
        # Verify Cache was NOT set (already there)
        mock_redis.set.assert_not_called()
        # Verify Client was NOT used
        mock_client.post.assert_not_called()
        
        print("✅ Scenario B (Cache Hit) Passed: Client Skipped.")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(test_call_external_sql_api_optimization())
