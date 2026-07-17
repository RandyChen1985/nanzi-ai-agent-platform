"""Unit tests for global Embedding configuration, fallback strategies, and connection testing."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.ai.embedding_client import EmbeddingClient
from app.api.portal.endpoints.system import test_connection, EmbedConnectionTestPayload

pytestmark = pytest.mark.no_infrastructure

@pytest.mark.asyncio
async def test_resolve_credentials_global_configured():
    # Test case: Global config is fully provided
    async def mock_config_get(key, default=None):
        configs = {
            "embed_api_url": "https://global-embed.yovole.com/v1",
            "embed_api_key": "global-key",
            "embed_model_name": "global-model-v1"
        }
        return configs.get(key, default)

    with patch("app.services.ai.embedding_client.ConfigService.get", side_effect=mock_config_get):
        url, key, model = await EmbeddingClient._resolve_credentials(use_global=True)
        assert url == "https://global-embed.yovole.com"
        assert key == "global-key"
        assert model == "global-model-v1"

@pytest.mark.asyncio
async def test_resolve_credentials_global_fallback_memory():
    # Test case: Global config is empty, fallback to memory config
    async def mock_config_get(key, default=None):
        return None  # empty global configs

    async def mock_memory_get(key, default=None):
        configs = {
            "memory_embedding_base_url": "https://memory-embed.yovole.com/v1",
            "memory_embedding_api_key": "memory-key",
            "memory_embedding_model": "memory-model-v2"
        }
        return configs.get(key, default)

    with patch("app.services.ai.embedding_client.ConfigService.get", side_effect=mock_config_get), \
         patch("app.services.ai.embedding_client.MemoryConfigService.get", side_effect=mock_memory_get):
        url, key, model = await EmbeddingClient._resolve_credentials(use_global=True)
        assert url == "https://memory-embed.yovole.com"
        assert key == "memory-key"
        assert model == "bge-m3"  # global default is bge-m3

@pytest.mark.asyncio
async def test_resolve_credentials_global_fallback_llm():
    # Test case: Global config and memory config are empty, fallback to LLM config
    async def mock_config_get(key, default=None):
        configs = {
            "llm_base_url": "https://llm-base.yovole.com/v1",
            "llm_api_key": "llm-key",
            "embed_model_name": "fallback-global-model"
        }
        return configs.get(key, default)

    async def mock_memory_get(key, default=None):
        return None

    with patch("app.services.ai.embedding_client.ConfigService.get", side_effect=mock_config_get), \
         patch("app.services.ai.embedding_client.MemoryConfigService.get", side_effect=mock_memory_get):
        url, key, model = await EmbeddingClient._resolve_credentials(use_global=True)
        assert url == "https://llm-base.yovole.com"
        assert key == "llm-key"
        assert model == "fallback-global-model"

@pytest.mark.asyncio
async def test_get_dimensions_global():
    # Test case: Global dimensions configured
    async def mock_config_get(key, default=None):
        if key == "embed_dimensions":
            return "512"
        return None

    with patch("app.services.ai.embedding_client.ConfigService.get", side_effect=mock_config_get):
        dim = await EmbeddingClient.get_dimensions(use_global=True)
        assert dim == 512

@pytest.mark.asyncio
async def test_get_dimensions_global_fallback_memory():
    # Test case: Global dimensions not configured, fallback to memory
    async def mock_config_get(key, default=None):
        return None

    async def mock_memory_get_int(key, default=0):
        if key == "memory_embedding_dimensions":
            return 768
        return default

    with patch("app.services.ai.embedding_client.ConfigService.get", side_effect=mock_config_get), \
         patch("app.services.ai.embedding_client.MemoryConfigService.get_int", side_effect=mock_memory_get_int):
        dim = await EmbeddingClient.get_dimensions(use_global=True)
        assert dim == 768

@pytest.mark.asyncio
async def test_get_dimensions_global_fallback_default():
    # Test case: No configs, fallback to 1024
    async def mock_config_get(key, default=None):
        return None

    async def mock_memory_get_int(key, default=0):
        return default

    with patch("app.services.ai.embedding_client.ConfigService.get", side_effect=mock_config_get), \
         patch("app.services.ai.embedding_client.MemoryConfigService.get_int", side_effect=mock_memory_get_int):
        dim = await EmbeddingClient.get_dimensions(use_global=True)
        assert dim == 1024

@pytest.mark.asyncio
async def test_global_embed_connection_api():
    # Test connection endpoint logic for global_embed
    payload = EmbedConnectionTestPayload(
        embed_api_url="https://test-conn-embed.yovole.com",
        embed_api_key="test-conn-key",
        embed_model_name="test-conn-model"
    )

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value={
        "data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]
    })

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    # Use AsyncContextManager mock for httpx.AsyncClient
    class MockClientContext:
        async def __aenter__(self):
            return mock_client
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("app.api.portal.endpoints.system.httpx.AsyncClient", return_value=MockClientContext()), \
         patch("app.api.portal.endpoints.system.require_permission", return_value=lambda x: None):
        res = await test_connection(
            component="global_embed",
            payload=payload,
            user={"username": "admin"}
        )
        assert res.status == "success"
        assert "Embedding connection test successful" in res.message
        assert any("length: 4" in log for log in res.logs)

@pytest.mark.asyncio
async def test_rebuild_vector_indexes_api():
    from app.api.portal.endpoints.system import rebuild_vector_indexes
    
    mock_redis = AsyncMock()
    mock_redis.execute_command = AsyncMock()
    
    with patch("app.api.portal.endpoints.system.redis.get_redis", return_value=mock_redis), \
         patch("app.services.ai.metadata_index_service.MetadataIndexService.ensure_index", return_value=True), \
         patch("app.services.ai.example_index_service.ExampleIndexService.ensure_index", return_value=True), \
         patch("app.services.ai.metadata_index_service.MetadataIndexService.sync_all_datasets", return_value=None), \
         patch("app.services.ai.example_index_service.ExampleIndexService.sync_all_examples", return_value=None), \
         patch("app.api.portal.endpoints.system.require_permission", return_value=lambda x: None):
         
        res = await rebuild_vector_indexes(user={"username": "admin"})
        assert res["status"] == "success"
        assert "已成功重构本地向量索引。" in res["message"]
        mock_redis.execute_command.assert_any_call("FT.DROPINDEX", "nanzi:idx:metadata:dataset", "DD")
        mock_redis.execute_command.assert_any_call("FT.DROPINDEX", "nanzi:idx:example:dataset", "DD")

@pytest.mark.asyncio
async def test_search_examples_top_k_resolution():
    from app.services.chatbi_example_service import ExampleService
    
    mock_embed = [0.1, 0.2]
    
    async def mock_config_get(key, default=None):
        if key == "chatbi_sample_top_k":
            return "8"
        if key == "metadata_provider":
            return "local"
        return default

    with patch("app.services.chatbi_example_service.ConfigService.get", side_effect=mock_config_get), \
         patch("app.services.chatbi_example_service.EmbeddingClient.embed_text", return_value=mock_embed), \
         patch("app.services.ai.example_index_service.ExampleIndexService.search_knn", return_value=[]) as mock_search, \
         patch("app.services.chatbi_example_service.ExampleService._search_mysql_fallback", return_value=[]) as mock_fallback:
         
        await ExampleService.search_examples(query="hello", dataset_id=1, top_k=None)
        mock_search.assert_called_once_with(
            query_embedding=mock_embed,
            authorized_dataset_ids=[1],
            top_k=8
        )


