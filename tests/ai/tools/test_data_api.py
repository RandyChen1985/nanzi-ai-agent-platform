import pytest
import json
import httpx
from unittest.mock import ANY, MagicMock, AsyncMock, patch
from app.services.ai.tools.data_api import (
    validate_sql, 
    call_external_sql_api, 
    call_ragflow_api,
    get_dataset_schema,
    execute_sql_query
)

# --- SQL Validation Tests ---

def test_validate_sql_success():
    """测试合法的 SELECT 语句"""
    sql = "SELECT id, name FROM users WHERE age > 18"
    assert validate_sql(sql, "clickhouse") is None
    assert validate_sql(sql, "mysql") is None

def test_validate_sql_syntax_error():
    """测试语法错误的 SQL"""
    sql = "SELECT * FROM users WHERE"
    error = validate_sql(sql)
    assert "Syntax Error" in error

def test_validate_sql_not_select():
    """测试非 SELECT 语句（禁止）"""
    sql = "UPDATE users SET age = 20"
    error = validate_sql(sql)
    assert "Only read-only queries (SELECT, EXPLAIN, SHOW, DESCRIBE) are allowed" in error

def test_validate_sql_multi_statement():
    """测试多条语句（禁止）"""
    sql = "SELECT 1; SELECT 2;"
    error = validate_sql(sql)
    assert "Multi-statement queries are prohibited" in error

# --- External API Tests ---

@pytest.mark.asyncio
async def test_call_external_sql_api_success():
    """测试外部 SQL API 调用成功"""
    mock_resp_data = {
        "code": 200,
        "data": {
            "columns": ["id"],
            "items": [{"id": 1}]
        }
    }
    
    with patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:

        mock_config.side_effect = lambda k, **kwargs: {
            "external_sql_api_url": "http://api/sql",
            "external_sql_api_key": "key123",
            "external_sql_data_source": "default",
            "data_api_timeout_seconds": "60"
        }.get(k, kwargs.get("default"))
        
        mock_post.return_value = httpx.Response(200, json=mock_resp_data)
        
        result = await call_external_sql_api("SELECT 1")
        parsed = json.loads(result)
        assert parsed["items"][0]["id"] == 1

@pytest.mark.asyncio
async def test_call_external_sql_api_error():
    """测试外部 SQL API 返回错误"""
    with patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        
        mock_config.side_effect = lambda k, **kwargs: {
            "external_sql_api_url": "http://api/sql",
            "external_sql_api_key": "key123",
            "external_sql_data_source": "default",
            "data_api_timeout_seconds": "60"
        }.get(k, kwargs.get("default"))
        
        mock_post.return_value = httpx.Response(400, content="Bad Request")
        
        result = await call_external_sql_api("SELECT 1")
        assert "[TOOL_ERROR]" in result
        assert "400" in result

# --- RAGFlow API Tests ---

@pytest.mark.asyncio
async def test_call_ragflow_api_success():
    """测试 RAGFlow API 调用成功"""
    mock_resp = {
        "code": 0,
        "data": {
            "chunks": [
                {"content": "Chunk 1", "similarity": 0.9},
                {"content": "Chunk 2", "similarity": 0.8}
            ]
        }
    }
    
    with patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        
        mock_config.side_effect = lambda k: {
            "ragflow_api_url": "http://rag",
            "ragflow_api_key": "key"
        }.get(k)
        
        mock_post.return_value = httpx.Response(200, json=mock_resp)
        
        result = await call_ragflow_api("test query", ["ds1"])
        assert "Chunk 1" in result
        assert "[置信度: 0.90]" in result
# --- Tool Integration Tests ---

@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_get_dataset_schema_tool():
    """local 模式优先走本地向量检索，而不是返回全量授权 YAML。"""
    mock_ds = MagicMock()
    mock_ds.id = 1
    mock_ds.display_name = "User Stats"
    mock_ds.name = "user_stats"

    with patch("app.core.orm.AsyncSessionLocal", new_callable=MagicMock), \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config, \
         patch("app.services.metadata_service.MetadataService.search_datasets", new_callable=AsyncMock) as mock_search, \
         patch("app.services.metadata_service.MetadataService.export_dataset_yaml", new_callable=AsyncMock) as mock_export, \
         patch("app.services.ai.embedding_client.EmbeddingClient.embed_text", new_callable=AsyncMock) as mock_embed, \
         patch("app.services.ai.metadata_index_service.MetadataIndexService.search_knn", new_callable=AsyncMock) as mock_search_knn:

        async def config_get(key, default=None):
            return {
                "metadata_provider": "local",
                "ragflow_similarity_threshold": "0.2",
                "ragflow_metadata_top_k": "5",
            }.get(key, default)

        mock_config.side_effect = config_get
        mock_search.return_value = [mock_ds]
        mock_export.return_value = "tables: [users]"
        mock_embed.return_value = [0.1] * 1536
        mock_search_knn.return_value = [
            {
                "dataset_id": "1",
                "doc_name": "user_stats.users.txt",
                "content": "table_name: users\ndataset: user_stats\n",
                "similarity": 0.86,
            }
        ]
        
        result = await get_dataset_schema.ainvoke({"keywords": "user_stats"})

        assert "--- [Schema:1]" in result
        assert "score=0.86" in result
        assert "table=user_stats" in result or "table=users" in result
        assert "table_name: users" in result
        mock_embed.assert_awaited_once_with("user_stats", use_global=True)
        mock_search_knn.assert_awaited_once_with(
            query_embedding=[0.1] * 1536,
            authorized_dataset_ids=[1],
            top_k=5,
        )
        mock_export.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_get_dataset_schema_tool_local_falls_back_to_mysql_like_when_vector_fails():
    """local 向量检索异常时，降级为 MySQL LIKE 命中数据集后导出 YAML。"""
    authorized_ds = MagicMock()
    authorized_ds.id = 1
    authorized_ds.display_name = "User Stats"
    authorized_ds.name = "user_stats"
    matched_ds = MagicMock()
    matched_ds.id = 2
    matched_ds.display_name = "Order Stats"
    matched_ds.name = "order_stats"

    with patch("app.core.orm.AsyncSessionLocal", new_callable=MagicMock), \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config, \
         patch("app.services.metadata_service.MetadataService.search_datasets", new_callable=AsyncMock) as mock_search, \
         patch("app.services.metadata_service.MetadataService.build_dataset_schema_chunk_contents", new_callable=AsyncMock) as mock_build_chunks, \
         patch("app.services.ai.embedding_client.EmbeddingClient.embed_text", new_callable=AsyncMock) as mock_embed, \
         patch("app.services.ai.metadata_index_service.MetadataIndexService.search_knn", new_callable=AsyncMock) as mock_search_knn:

        async def config_get(key, default=None):
            return {
                "metadata_provider": "local",
                "ragflow_similarity_threshold": "0.2",
                "ragflow_metadata_top_k": "5",
            }.get(key, default)

        mock_config.side_effect = config_get
        mock_search.side_effect = [[authorized_ds], [matched_ds]]
        mock_embed.return_value = [0.1] * 1536
        mock_search_knn.side_effect = RuntimeError("redis unavailable")
        mock_build_chunks.return_value = ["table_name: orders\ndataset: order_stats\n"]

        result = await get_dataset_schema.ainvoke({"keywords": "order"})

        assert "--- [Schema:1]" in result
        assert "table_name: orders" in result
        assert mock_search.await_args_list[0].kwargs["query"] is None
        assert mock_search.await_args_list[1].kwargs["query"] == "order"
        mock_build_chunks.assert_awaited_once_with(ANY, 2)


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_get_dataset_schema_tool_passes_configured_metadata_dataset_ids():
    """工具高级配置中的元数据集 ID 应传给 Schema 检索核心逻辑。"""
    with patch("app.core.orm.AsyncSessionLocal", new_callable=MagicMock), \
         patch("app.services.chatbi_dataset_schema_service.fetch_dataset_schema_core", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = "schema scoped"

        result = await get_dataset_schema.ainvoke({
            "keywords": "销售",
            "metadata_dataset_ids": ["1", "3"],
        })

        assert result == "schema scoped"
        mock_fetch.assert_awaited_once()
        assert mock_fetch.await_args.kwargs["keywords"] == "销售"
        assert mock_fetch.await_args.kwargs["authorized_dataset_ids"] == [1, 3]

@pytest.mark.asyncio
async def test_execute_sql_query_tool_dry_run():
    """测试执行 SQL 工具的干跑模式 (使用 .invoke)"""
    mock_ds = MagicMock()
    mock_ds.data_source = "clickhouse_prod"
    
    # 模拟管理员上下文以绕过权限拦截
    mock_ctx = MagicMock()
    mock_ctx.user_id = 1
    mock_ctx.is_admin = True
    
    with patch("app.core.orm.AsyncSessionLocal", new_callable=MagicMock), \
         patch("app.services.metadata_service.MetadataService.get_dataset_by_name", new_callable=AsyncMock) as mock_get_name, \
         patch("app.core.context.get_current_agent_context", return_value=mock_ctx), \
         patch("app.core.context.get_debug_option", return_value=True):
        
        mock_get_name.return_value = mock_ds
        
        # 使用 ainvoke 调用工具
        result = await execute_sql_query.ainvoke({"sql": "SELECT 1", "data_source": "clickhouse_prod", "dataset_name": "user_stats"})
        assert "[DRY_RUN]" in result
        assert "SELECT 1" in result
        assert "clickhouse_prod" in result

@pytest.mark.asyncio
async def test_execute_sql_query_tool_validation_fail():
    """测试执行 SQL 工具的验证失败 (使用 .invoke)"""
    mock_ds = MagicMock()
    mock_ds.id = 1
    mock_ds.data_source = "mysql_prod"
    
    # 模拟管理员上下文
    mock_ctx = MagicMock()
    mock_ctx.user_id = 1
    mock_ctx.is_admin = True
    
    with patch("app.core.orm.AsyncSessionLocal", new_callable=MagicMock), \
         patch("app.services.metadata_service.MetadataService.get_dataset_by_name", new_callable=AsyncMock) as mock_get_name, \
         patch("app.core.context.get_current_agent_context", return_value=mock_ctx):
        
        mock_get_name.return_value = mock_ds
        
        # 语法错误
        result = await execute_sql_query.ainvoke({"sql": "SELECT * FROM", "data_source": "user_stats", "dataset_name": "user_stats"})
        assert "[Validation Failed]" in result
