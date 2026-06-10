import pytest
import json
from unittest.mock import MagicMock, AsyncMock, patch
from app.services.ai.tools.knowledge_tool import search_knowledge_base, normalize_dataset_ids


def test_normalize_dataset_ids_plain_and_comma():
    rid_a = "4525d66cec7111f0a3d00242ac120006"
    rid_b = "abcd1234abcd1234abcd1234abcd1234"
    assert normalize_dataset_ids(rid_a) == [rid_a]
    assert normalize_dataset_ids(f"{rid_a},{rid_b}") == [rid_a, rid_b]
    assert normalize_dataset_ids("id1,id2") == []


def test_normalize_dataset_ids_json_and_python_list_strings():
    rid = "4525d66cec7111f0a3d00242ac120006"
    assert normalize_dataset_ids(f'["{rid}"]') == [rid]
    assert normalize_dataset_ids(f"['{rid}']") == [rid]
    assert normalize_dataset_ids([rid]) == [rid]


@pytest.mark.asyncio
async def test_search_knowledge_base_strips_json_array_argument():
    """LLM 传入 JSON 数组字符串时应解析为合法 ID"""
    rid = "4525d66cec7111f0a3d00242ac120006"
    with patch("app.services.ai.ragflow_client.RagFlowClient.retrieve", new_callable=AsyncMock) as mock_retrieve, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config:
        mock_retrieve.return_value = []
        mock_config.return_value = None

        await search_knowledge_base.ainvoke({"query": "换电", "dataset_ids": f'["{rid}"]'})

        args, _ = mock_retrieve.call_args
        assert args[1] == [rid]

@pytest.mark.asyncio
async def test_search_knowledge_base_explicit_ids():
    """测试传递显式数据集 ID"""
    with patch("app.services.ai.ragflow_client.RagFlowClient.retrieve", new_callable=AsyncMock) as mock_retrieve, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config:
        
        mock_retrieve.return_value = [{"doc_name": "test.pdf", "content": "text"}]
        mock_config.return_value = None
        
        rid_a = "11111111111111111111111111111111"
        rid_b = "22222222222222222222222222222222"
        result = await search_knowledge_base.ainvoke(
            {"query": "hello", "dataset_ids": f"{rid_a},{rid_b}"}
        )

        args, kwargs = mock_retrieve.call_args
        assert args[0] == "hello"
        assert args[1] == [rid_a, rid_b]
        assert "test.pdf" in result

@pytest.mark.asyncio
async def test_search_knowledge_base_context_ids():
    """测试从 Agent 上下文获取数据集 ID"""
    with patch("app.services.ai.ragflow_client.RagFlowClient.retrieve", new_callable=AsyncMock) as mock_retrieve, \
         patch("app.services.ai.tools.knowledge_tool.get_current_agent_config") as mock_ctx_config, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config:
        
        mock_retrieve.return_value = []
        # 模拟上下文中有 dataset_ids
        mock_ctx_config.side_effect = (
            lambda k: ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"] if k == "dataset_ids" else None
        )
        mock_config.return_value = None
        
        await search_knowledge_base.ainvoke({"query": "hi"})
        
        args, kwargs = mock_retrieve.call_args
        assert args[1] == ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]

@pytest.mark.asyncio
async def test_search_knowledge_base_parameter_priority():
    """测试参数（阈值、权重）的优先级逻辑"""
    with patch("app.services.ai.ragflow_client.RagFlowClient.retrieve", new_callable=AsyncMock) as mock_retrieve, \
         patch("app.services.ai.tools.knowledge_tool.get_current_agent_config") as mock_ctx_config, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config:
        
        mock_retrieve.return_value = []
        
        # 1. 系统配置：0.5
        # 2. 上下文配置：0.8 (优先级更高)
        default_ds = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        mock_config.side_effect = (
            lambda k: "0.5" if k == "ragflow_similarity_threshold" else default_ds
        )
        mock_ctx_config.side_effect = (
            lambda k: 0.8
            if k == "ragflow_threshold"
            else ([default_ds] if k == "dataset_ids" else None)
        )
        
        await search_knowledge_base.ainvoke({"query": "param test"})
        
        args, kwargs = mock_retrieve.call_args
        assert kwargs["similarity_threshold"] == 0.8 # 应当采用上下文覆盖的值


@pytest.mark.asyncio
async def test_search_knowledge_base_reports_service_unavailable_on_502():
    """RAGFlow 502 时应返回明确的「知识库服务不可用」提示，而非泛化 Tool Error。"""
    rid = "4525d66cec7111f0a3d00242ac120006"
    with patch("app.services.ai.ragflow_client.RagFlowClient.retrieve", new_callable=AsyncMock) as mock_retrieve, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config:
        mock_retrieve.side_effect = Exception("RAGFlow Retrieve failed: HTTP 502 Bad Gateway")
        mock_config.return_value = None

        result = await search_knowledge_base.ainvoke(
            {"query": "换电流程", "dataset_ids": rid}
        )

    assert "知识库服务不可用" in result
    assert "Failed to search knowledge base" not in result
    assert mock_retrieve.call_count == 1