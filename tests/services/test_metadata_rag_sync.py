import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.metadata_rag_service import MetadataRagService
from app.models.metadata import MetaDataset, MetaTable, MetaColumn
from app.services.ai.ragflow_client import RagFlowClient

@pytest.mark.asyncio
async def test_yaml_generation():
    """验证生成的 YAML 格式是否符合要求"""
    dataset = MetaDataset(name="test_ds", display_name="测试数据集", data_source="mysql_test")
    table = MetaTable(physical_name="t_orders", term="订单表", description="订单描述")
    col1 = MetaColumn(physical_name="id", type="Int64", term="主键", description="唯一标识")
    table.columns = [col1]
    
    content = MetadataRagService.generate_table_content(dataset, table)
    
    assert "table_name: t_orders" in content
    assert "table_desc: 订单表" in content
    assert "data_source: mysql_test" in content
    assert "dataset: test_ds" in content
    assert "dataset: test_ds" in content
    assert "meta_name: 测试数据集" in content
    assert "- name: id" in content
    assert "type: Int64" in content


def test_metrics_yaml_generation():
    """验证指标块 YAML 包含 dataset / meta_name / data_source。"""
    from app.models.metadata import MetaMetric

    dataset = MetaDataset(name="sales_detail", display_name="销售明细", data_source="default_clickhouse")
    dataset.metrics = [
        MetaMetric(
            name="daily_orders",
            display_name="日订单量",
            description="当日订单总数",
            unit="单",
            calculation_logic="SELECT count(*) FROM fact_orders WHERE dt = today()",
        )
    ]

    content = MetadataRagService.generate_metrics_content(dataset)

    assert "dataset: sales_detail" in content
    assert "meta_name: 销售明细" in content
    assert "data_source: default_clickhouse" in content
    assert "metrics_scope: 销售明细" in content
    assert "name: daily_orders" in content


@pytest.mark.asyncio
async def test_sync_dataset_flow(db_session: AsyncSession):
    """模拟同步全流程"""
    import uuid
    uid = uuid.uuid4().hex[:6]
    # 1. Prepare Data
    dataset = MetaDataset(name=f"sync_test_{uid}", display_name="Sync Test", status=1)
    db_session.add(dataset)
    await db_session.flush()
    
    table = MetaTable(dataset_id=dataset.id, physical_name=f"t_test_{uid}", term="测试表")
    db_session.add(table)
    await db_session.flush()
    await db_session.commit()
    
    # 2. Mock RagFlowClient
    with patch("app.services.metadata_rag_service.RagFlowClient") as MockClient:
        mock_instance = MockClient.return_value
        mock_instance.list_datasets = AsyncMock(return_value=[])
        mock_instance.create_dataset = AsyncMock(return_value={"id": "rag_kb_123"})
        mock_instance.list_documents = AsyncMock(return_value=[])
        mock_instance.upload_document = AsyncMock(return_value={"id": "doc_456"})
        mock_instance.parse_documents = AsyncMock()
        
        # 3. Execute Sync
        await MetadataRagService.sync_dataset(db_session, dataset.id)
        
        # 4. Verify DB updated
        await db_session.refresh(dataset)
        assert dataset.rag_dataset_id == "rag_kb_123"
        assert dataset.rag_sync_status == 2
        assert dataset.rag_synced_at is not None
        
        # 5. Verify Client calls
        mock_instance.create_dataset.assert_called_once()
        mock_instance.upload_document.assert_called()

@pytest.mark.asyncio
async def test_sync_dataset_failure(db_session: AsyncSession):
    """验证同步失败时的状态更新"""
    import uuid
    uid = uuid.uuid4().hex[:6]
    dataset = MetaDataset(name=f"fail_test_{uid}", display_name="Fail Test", status=1)
    db_session.add(dataset)
    await db_session.commit()
    
    # Mock Client to raise exception
    with patch("app.services.metadata_rag_service.RagFlowClient") as MockClient:
        mock_instance = MockClient.return_value
        mock_instance.list_datasets = AsyncMock(side_effect=Exception("RAGFlow Down"))
        
        await MetadataRagService.sync_dataset(db_session, dataset.id)
        
        await db_session.refresh(dataset)
        assert dataset.rag_sync_status == -1 # Should be marked as failed

@pytest.mark.asyncio
async def test_cascade_delete(db_session: AsyncSession):
    """验证级联删除逻辑"""
    import uuid
    uid = uuid.uuid4().hex[:6]
    dataset = MetaDataset(name=f"del_test_{uid}", rag_dataset_id="rag_id_to_delete")
    db_session.add(dataset)
    await db_session.commit()
    ds_id = dataset.id
    
    # Mock Service delete_rag_dataset to avoid actual API call and verify it's called
    with patch("app.services.metadata_rag_service.MetadataRagService.delete_rag_dataset", new_callable=AsyncMock) as mock_delete_rag, \
         patch("app.services.ai.config.AgentConfigProvider.refresh_dataset_menu", AsyncMock()):
        
        from app.services.metadata_service import MetadataService
        await MetadataService.delete_dataset(db_session, ds_id)
        
        # Verify dataset is gone
        result = await db_session.get(MetaDataset, ds_id)
        assert result is None
        
        # Verify cascade call
        import asyncio
        await asyncio.sleep(0.1)
        mock_delete_rag.assert_called_with("rag_id_to_delete")

@pytest.mark.asyncio
async def test_rag_client_methods():
    """验证 RagFlowClient 方法签名与 HTTP 调用"""
    client = RagFlowClient()
    client.base_url = "http://mock-api"
    client.api_key = "mock-key"
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        
        # Mock responses
        post_resp = MagicMock()
        post_resp.status_code = 200
        post_resp.json.return_value = {"code": 0, "data": {"id": "123"}}
        mock_post.return_value = post_resp
        
        get_resp = MagicMock()
        get_resp.status_code = 200
        get_resp.json.return_value = {"code": 0, "data": []}
        mock_get.return_value = get_resp
        
        # Test Create
        await client.create_dataset("test_kb")
        mock_post.assert_called()
        assert mock_post.call_args[1]['json']['name'] == "test_kb"
        
        # Test Upload (Multipart)
        await client.upload_document("ds_1", "file.md", b"content")
        mock_post.assert_called()
        assert "files" in mock_post.call_args[1]

@pytest.mark.asyncio
async def test_schema_gateway_routing(db_session: AsyncSession):
    """验证 Schema 网关路由逻辑"""
    from app.api.v1.endpoints.schema import get_database_schema, SchemaRequest
    
    request = SchemaRequest(query="find something")
    
    # Mock Config based on key
    async def mock_config_get(key, default=None):
        mapping = {
            "metadata_provider": "ragflow",
            "ragflow_api_url": "http://mock",
            "ragflow_similarity_threshold": "0.2",
            "ragflow_vector_weight": "0.3"
        }
        return mapping.get(key, default)

    # Simplified patches with cleaner syntax
    p1 = patch("app.services.config_service.ConfigService.get", side_effect=mock_config_get)
    p2 = patch("app.services.metadata_service.MetadataService.search_datasets", AsyncMock(return_value=[MagicMock(rag_dataset_id="rag_id_1")]))
    p3 = patch("app.services.ai.ragflow_client.RagFlowClient.retrieve", AsyncMock(return_value=[{"doc_name": "test.md", "similarity": 0.9, "content": "retrieved content"}]))
    
    with p1, p2, p3:
        mock_user = {"user_id": 1, "role": "admin"}
        response = await get_database_schema(request, db_session, current_user=mock_user)
        
        assert response.data.provider == "ragflow"
        assert "retrieved content" in response.data.schema_context
        assert any("RAGFlow Endpoint" in log for log in response.data.logs)

@pytest.mark.asyncio
async def test_schema_gateway_retry(db_session: AsyncSession):
    """验证 RAGFlow 检索的自动重试与坏ID剔除逻辑"""
    from app.api.v1.endpoints.schema import get_database_schema, SchemaRequest
    
    request = SchemaRequest(query="retry test")
    
    # Mock Config
    async def mock_config_get(key, default=None):
        mapping = {
            "metadata_provider": "ragflow",
            "ragflow_api_url": "http://mock",
            "ragflow_similarity_threshold": "0.2",
            "ragflow_vector_weight": "0.3"
        }
        return mapping.get(key, default)

    # Setup Mocks
    mock_bad_ds = MagicMock(rag_dataset_id="bad_id")
    mock_good_ds = MagicMock(rag_dataset_id="good_id")
    
    p1 = patch("app.services.config_service.ConfigService.get", side_effect=mock_config_get)
    p2 = patch("app.services.metadata_service.MetadataService.search_datasets", AsyncMock(return_value=[mock_bad_ds, mock_good_ds]))
    p3 = patch("app.services.ai.ragflow_client.RagFlowClient.retrieve", new_callable=AsyncMock)
    
    with p1, p2, p3 as mock_retrieve:
        def retrieve_side_effect(query, ids, **kwargs):
            if "bad_id" in ids:
                raise Exception("You don't own the dataset bad_id")
            return [{"doc_name": "good.md", "content": "success", "similarity": 0.9}]
            
        mock_retrieve.side_effect = retrieve_side_effect
        
        mock_user = {"user_id": 1, "role": "admin"}
        response = await get_database_schema(request, db_session, current_user=mock_user)
        
        assert response.data.provider == "ragflow"
        assert "success" in response.data.schema_context
        logs = response.data.logs
        assert any("Attempt 1 failed" in log for log in logs)
        assert any("Excluding bad ID: bad_id" in log for log in logs)
        assert mock_retrieve.call_count == 2


@pytest.mark.asyncio
async def test_retrieve_aborts_on_service_unavailable():
    """服务级故障（502）应立即终止、不重试，并抛出 MetadataServiceUnavailableError。"""
    from app.services.metadata_rag_service import MetadataServiceUnavailableError

    client = MagicMock()
    client.retrieve = AsyncMock(side_effect=Exception("RAGFlow HTTP Error 502: "))

    with pytest.raises(MetadataServiceUnavailableError):
        await MetadataRagService.retrieve_with_retry(
            client, "智能体用户表", ["id_a", "id_b"], max_retries=2
        )

    # 关键：只调用一次，不因 502 反复重试
    assert client.retrieve.call_count == 1


@pytest.mark.asyncio
async def test_get_dataset_schema_tool_reports_service_unavailable():
    """get_dataset_schema 工具在元数据服务 502 时应返回明确的「不可用」提示，而非「未找到」。"""
    from app.services.ai.tools.data_api import get_dataset_schema

    async def mock_config_get(key, default=None):
        mapping = {
            "metadata_provider": "ragflow",
            "ragflow_similarity_threshold": "0.2",
            "ragflow_vector_weight": "0.3",
            "ragflow_metadata_top_k": "5",
        }
        return mapping.get(key, default)

    mock_ds = MagicMock(rag_dataset_id="rid_1", display_name="测试集", name="ds", data_source="clickhouse", description="")

    p1 = patch("app.services.config_service.ConfigService.get", side_effect=mock_config_get)
    p2 = patch("app.services.metadata_service.MetadataService.search_datasets", AsyncMock(return_value=[mock_ds]))
    p3 = patch("app.services.ai.ragflow_client.RagFlowClient.retrieve", new_callable=AsyncMock)
    p4 = patch("app.core.context.get_current_agent_context", return_value=MagicMock(user_id=1, is_admin=True, api_key=None))

    with p1, p2, p3 as mock_retrieve, p4:
        mock_retrieve.side_effect = Exception("RAGFlow HTTP Error 502: ")
        result = await get_dataset_schema.ainvoke({"keywords": "智能体用户表"})

    assert "元数据服务不可用" in result
    assert "No relevant schema info found" not in result
    assert mock_retrieve.call_count == 1
