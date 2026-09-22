"""Unit tests for global Embedding configuration, fallback strategies, and connection testing."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.ai.embedding_client import EmbeddingClient
from app.api.portal.endpoints.system import test_connection as system_test_connection, EmbedConnectionTestPayload

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
        assert url == "https://global-embed.yovole.com/v1"
        assert key == "global-key"
        assert model == "global-model-v1"

@pytest.mark.asyncio
async def test_resolve_credentials_memory_path_uses_global():
    # Memory callers (historically use_global=False) now also read system embed_*
    async def mock_config_get(key, default=None):
        configs = {
            "embed_api_url": "https://global-embed.yovole.com/v1",
            "embed_api_key": "global-key",
            "embed_model_name": "global-model-v1",
            "llm_base_url": "https://should-not-use.example/v1",
            "llm_api_key": "llm-key",
        }
        return configs.get(key, default)

    with patch("app.services.ai.embedding_client.ConfigService.get", side_effect=mock_config_get):
        url, key, model = await EmbeddingClient._resolve_credentials(use_global=False)
        assert url == "https://global-embed.yovole.com/v1"
        assert key == "global-key"
        assert model == "global-model-v1"

@pytest.mark.asyncio
async def test_resolve_credentials_ignores_memory_embedding_keys():
    # Even if memory_embedding_* exist, they must not be used
    async def mock_config_get(key, default=None):
        configs = {
            "llm_base_url": "https://llm-base.yovole.com/v1",
            "llm_api_key": "llm-key",
            "embed_model_name": "fallback-global-model",
        }
        return configs.get(key, default)

    with patch("app.services.ai.embedding_client.ConfigService.get", side_effect=mock_config_get):
        # MemoryConfigService import was removed; ensure resolve still works via LLM fallback
        url, key, model = await EmbeddingClient._resolve_credentials(use_global=True)
        assert url == "https://llm-base.yovole.com/v1"
        assert key == "llm-key"
        assert model == "fallback-global-model"

@pytest.mark.asyncio
async def test_resolve_credentials_global_fallback_llm():
    # Test case: Global URL/Key empty, fallback to LLM config
    async def mock_config_get(key, default=None):
        configs = {
            "llm_base_url": "https://llm-base.yovole.com/v1",
            "llm_api_key": "llm-key",
            "embed_model_name": "fallback-global-model"
        }
        return configs.get(key, default)

    with patch("app.services.ai.embedding_client.ConfigService.get", side_effect=mock_config_get):
        url, key, model = await EmbeddingClient._resolve_credentials(use_global=True)
        assert url == "https://llm-base.yovole.com/v1"
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
async def test_get_dimensions_memory_path_uses_global():
    async def mock_config_get(key, default=None):
        if key == "embed_dimensions":
            return "1024"
        return None

    with patch("app.services.ai.embedding_client.ConfigService.get", side_effect=mock_config_get):
        dim = await EmbeddingClient.get_dimensions(use_global=False)
        assert dim == 1024

@pytest.mark.asyncio
async def test_get_dimensions_global_fallback_default():
    # Test case: No configs, fallback to 1024
    async def mock_config_get(key, default=None):
        return None

    with patch("app.services.ai.embedding_client.ConfigService.get", side_effect=mock_config_get):
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

    with patch("httpx.AsyncClient", return_value=MockClientContext()), \
         patch("app.api.portal.endpoints.system.require_permission", return_value=lambda x: None):
        res = await system_test_connection(
            component="global_embed",
            payload=payload,
            user={"username": "admin"}
        )
        assert res.status == "success"
        assert "Embedding connection test successful" in res.message
        assert any("length: 4" in log for log in res.logs)


@pytest.mark.asyncio
async def test_global_embed_connection_allows_empty_api_key():
    payload = EmbedConnectionTestPayload(
        embed_api_url="http://ollama.local/v1",
        embed_api_key="",
        embed_model_name="nomic-embed-text",
    )

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value={
        "data": [{"embedding": [0.1, 0.2]}]
    })
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    class MockClientContext:
        async def __aenter__(self):
            return mock_client
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    async def mock_config_get(key, default=None):
        return {
            "embed_api_url": "http://ollama.local/v1",
            "embed_api_key": "",
            "embed_model_name": "nomic-embed-text",
            "llm_base_url": "",
            "llm_api_key": "",
        }.get(key, default)

    with patch("httpx.AsyncClient", return_value=MockClientContext()), \
         patch("app.api.portal.endpoints.system.ConfigService.get", side_effect=mock_config_get):
        res = await system_test_connection(
            component="global_embed",
            payload=payload,
            user={"username": "admin"},
        )

    assert res.status == "success"
    assert mock_client.post.call_args.kwargs["headers"] == {
        "Content-Type": "application/json",
    }

@pytest.mark.asyncio
async def test_rebuild_vector_indexes_api():
    """端点现在只负责启动任务并返回 task_id，进度改由 SSE 推送。"""
    from app.api.portal.endpoints.system import rebuild_vector_indexes

    with patch(
        "app.services.ai.local_vector_rebuild.start_local_vector_rebuild",
        new_callable=AsyncMock,
        return_value="task_abc",
    ) as mock_start, \
         patch("app.api.portal.endpoints.system.require_permission", return_value=lambda x: None):

        res = await rebuild_vector_indexes(user={"username": "admin"})
        assert res["status"] == "success"
        assert res["task_id"] == "task_abc"
        assert "已启动" in res["message"]
        mock_start.assert_awaited_once_with(trigger="manual")


@pytest.mark.asyncio
async def test_rebuild_vector_indexes_api_conflicts_when_task_running():
    """已有重构任务在跑时必须返回 409，并带上正在运行的任务 ID。"""
    from fastapi import HTTPException
    from app.api.portal.endpoints.system import rebuild_vector_indexes

    with patch(
        "app.services.ai.local_vector_rebuild.start_local_vector_rebuild",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "app.services.ai.local_vector_rebuild.get_active_rebuild_task_id",
        new_callable=AsyncMock,
        return_value="task_running",
    ), patch(
        "app.api.portal.endpoints.system.require_permission", return_value=lambda x: None
    ):
        with pytest.raises(HTTPException) as exc:
            await rebuild_vector_indexes(user={"username": "admin"})
        assert exc.value.status_code == 409
        assert "task_running" in exc.value.detail
        # 锁带 TTL，提示里要说清会自动释放，避免用户以为被永久锁死
        assert "TTL" in exc.value.detail


@pytest.mark.asyncio
async def test_rebuild_vector_indexes_conflict_message_does_not_fake_a_task():
    """残留锁的持有者不是 task_id 时，不能提示成一个可以在抽屉里看的任务。"""
    from fastapi import HTTPException
    from app.api.portal.endpoints.system import rebuild_vector_indexes

    with patch(
        "app.services.ai.local_vector_rebuild.start_local_vector_rebuild",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "app.services.ai.local_vector_rebuild.get_active_rebuild_task_id",
        new_callable=AsyncMock,
        return_value="startup",
    ), patch(
        "app.api.portal.endpoints.system.require_permission", return_value=lambda x: None
    ):
        with pytest.raises(HTTPException) as exc:
            await rebuild_vector_indexes(user={"username": "admin"})
        assert exc.value.status_code == 409
        assert "task_id=startup" not in exc.value.detail
        assert "非任务持有者" in exc.value.detail

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
         patch("app.services.ai.embedding_client.EmbeddingClient.embed_text", return_value=mock_embed), \
         patch("app.services.ai.example_index_service.ExampleIndexService.search_knn", return_value=[]) as mock_search, \
         patch("app.services.chatbi_example_service.ExampleService._search_mysql_fallback", return_value=[]) as mock_fallback:
         
        await ExampleService.search_examples(query="hello", dataset_id=1, top_k=None)
        mock_search.assert_called_once_with(
            query_embedding=mock_embed,
            authorized_dataset_ids=[1],
            top_k=8
        )
