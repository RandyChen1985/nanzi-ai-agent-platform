"""Unit tests for local Redis vector search and ExampleIndexService."""
import pytest
import json
from unittest.mock import AsyncMock, patch
from app.services.ai.example_index_service import ExampleIndexService
from app.services.chatbi_example_service import ExampleService

pytestmark = pytest.mark.no_infrastructure

@pytest.mark.asyncio
async def test_example_index_ensure_index():
    mock_redis = AsyncMock()
    mock_redis.execute_command = AsyncMock(side_effect=Exception("Index does not exist"))
    
    with patch("app.services.ai.example_index_service.get_redis", return_value=mock_redis), \
         patch("app.services.ai.example_index_service.EmbeddingClient.get_dimensions", return_value=1536):
        
        res = await ExampleIndexService.ensure_index()
        assert res is True
        mock_redis.execute_command.assert_any_call(
            "FT.CREATE",
            "yunshu:idx:example:local",
            "ON",
            "HASH",
            "PREFIX",
            "1",
            "yunshu:example:",
            "SCHEMA",
            "id",
            "TAG",
            "dataset_id",
            "TAG",
            "dataset_name",
            "TEXT",
            "question",
            "TEXT",
            "raw_query",
            "TEXT",
            "context_summary",
            "TEXT",
            "sql",
            "TEXT",
            "trace_id",
            "TAG",
            "agent_id",
            "TAG",
            "sql_metadata",
            "TEXT",
            "embedding",
            "VECTOR",
            "HNSW",
            "6",
            "TYPE",
            "FLOAT32",
            "DIM",
            "1536",
            "DISTANCE_METRIC",
            "COSINE",
        )

@pytest.mark.asyncio
async def test_example_index_upsert_vector():
    mock_redis = AsyncMock()
    
    with patch("app.services.ai.example_index_service.get_redis", return_value=mock_redis), \
         patch("app.services.ai.example_index_service.ExampleIndexService.ensure_index", return_value=True):
        
        await ExampleIndexService.upsert_vector(
            example_id=12,
            dataset_id=42,
            dataset_name="Test DS",
            question="What is the total sales?",
            raw_query="total sales",
            context_summary="User asked for sales",
            sql_text="SELECT sum(sales) FROM orders",
            trace_id="t123",
            agent_id="a456",
            sql_metadata={"tables": ["orders"]},
            embedding=[0.1, 0.2, 0.3]
        )
        
        mock_redis.hset.assert_called_once()
        call_args = mock_redis.hset.call_args[0]
        assert call_args[0] == "yunshu:example:12"
        mapping = mock_redis.hset.call_args[1]["mapping"]
        assert mapping["id"] == "12"
        assert mapping["dataset_id"] == "42"
        assert mapping["dataset_name"] == "Test DS"
        assert mapping["question"] == "What is the total sales?"
        assert mapping["sql"] == "SELECT sum(sales) FROM orders"
        assert mapping["sql_metadata"] == json.dumps({"tables": ["orders"]}, ensure_ascii=False)
        assert "embedding" in mapping

@pytest.mark.asyncio
async def test_example_index_delete_vector():
    mock_redis = AsyncMock()
    
    with patch("app.services.ai.example_index_service.get_redis", return_value=mock_redis):
        await ExampleIndexService.delete_vector(12)
        mock_redis.delete.assert_called_once_with("yunshu:example:12")

@pytest.mark.asyncio
async def test_example_index_search_knn_admin():
    mock_redis = AsyncMock()
    raw_response = [
        1,
        "yunshu:example:12",
        [
            b"id", b"12",
            b"dataset_id", b"42",
            b"dataset_name", b"Test DS",
            b"question", b"What is the total sales?",
            b"raw_query", b"total sales",
            b"context_summary", b"User asked for sales",
            b"sql", b"SELECT sum(sales) FROM orders",
            b"trace_id", b"t123",
            b"agent_id", b"a456",
            b"sql_metadata", b'{"tables": ["orders"]}',
            b"score", b"0.15"
        ]
    ]
    mock_redis.execute_command = AsyncMock(return_value=raw_response)
    
    with patch("app.services.ai.example_index_service.get_redis_binary", return_value=mock_redis), \
         patch("app.services.ai.example_index_service.ExampleIndexService.ensure_index", return_value=True):
        
        results = await ExampleIndexService.search_knn(
            query_embedding=[0.1] * 1536,
            authorized_dataset_ids=None, # Admin
            top_k=5
        )
        
        assert len(results) == 1
        assert results[0]["id"] == 12
        assert results[0]["question"] == "What is the total sales?"
        assert results[0]["sql"] == "SELECT sum(sales) FROM orders"
        assert results[0]["similarity"] == pytest.approx(0.85) # 1.0 - 0.15
        
        query_expr = mock_redis.execute_command.call_args[0][2]
        assert query_expr == "*=>[KNN 5 @embedding $vec AS score]"

@pytest.mark.asyncio
async def test_example_index_search_knn_normal_user():
    mock_redis = AsyncMock()
    mock_redis.execute_command = AsyncMock(return_value=[])
    
    with patch("app.services.ai.example_index_service.get_redis_binary", return_value=mock_redis), \
         patch("app.services.ai.example_index_service.ExampleIndexService.ensure_index", return_value=True):
        
        await ExampleIndexService.search_knn(
            query_embedding=[0.1] * 1536,
            authorized_dataset_ids=[1, 3], # Normal user
            top_k=3
        )
        
        query_expr = mock_redis.execute_command.call_args[0][2]
        assert query_expr == "(@dataset_id:{1|3})=>[KNN 3 @embedding $vec AS score]"

@pytest.mark.asyncio
async def test_chatbi_example_search_local_split():
    redis_results = [
        {
            "id": 12,
            "question": "What is the total sales?",
            "sql": "SELECT sum(sales) FROM orders",
            "context_summary": "User asked for sales",
            "dataset_name": "Test DS",
            "trace_id": "t123",
            "sql_metadata": {"tables": ["orders"]},
            "similarity": 0.85
        }
    ]
    
    with patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config, \
         patch("app.services.ai.embedding_client.EmbeddingClient.embed_text", new_callable=AsyncMock, return_value=[0.1]*1536), \
         patch("app.services.ai.example_index_service.ExampleIndexService.search_knn", new_callable=AsyncMock, return_value=redis_results) as mock_local_search:
        
        mock_config.side_effect = lambda key, default=None: {
            "metadata_provider": "local"
        }.get(key, default)
        
        results = await ExampleService.search_examples(
            query="total sales",
            dataset_id=42,
            top_k=5
        )
        
        assert len(results) == 1
        assert results[0]["id"] == 12
        assert results[0]["question"] == "What is the total sales?"
        assert results[0]["sql"] == "SELECT sum(sales) FROM orders"
        assert results[0]["similarity"] == 0.85
        
        mock_local_search.assert_called_once()
        call_args = mock_local_search.call_args[1]
        assert call_args["authorized_dataset_ids"] == [42]
        assert call_args["top_k"] == 5

@pytest.mark.asyncio
async def test_chatbi_example_search_local_fallback_mysql():
    with patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config, \
         patch("app.services.ai.embedding_client.EmbeddingClient.embed_text", new_callable=AsyncMock, return_value=[0.1]*1536), \
         patch("app.services.ai.example_index_service.ExampleIndexService.search_knn", new_callable=AsyncMock, side_effect=Exception("Redis connection error")), \
         patch("app.services.chatbi_example_service.ExampleService._search_mysql_fallback", new_callable=AsyncMock, return_value=[{"id": 99, "question": "mysql fallback", "sql": "SELECT 1"}]) as mock_mysql_search:
        
        mock_config.side_effect = lambda key, default=None: {
            "metadata_provider": "local"
        }.get(key, default)
        
        results = await ExampleService.search_examples(
            query="total sales",
            dataset_id=42,
            top_k=5
        )
        
        assert len(results) == 1
        assert results[0]["id"] == 99
        assert results[0]["question"] == "mysql fallback"
        mock_mysql_search.assert_called_once_with("total sales", 42, 5)
