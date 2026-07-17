import pytest
import json
import httpx
from unittest.mock import MagicMock, AsyncMock, patch
from app.services.ai.ragflow_client import RagFlowClient

# --- Fixtures ---

@pytest.fixture
def ragflow_client():
    return RagFlowClient()

@pytest.fixture
def mock_config():
    """Mock ConfigService to return fake API info"""
    with patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock:
        mock.side_effect = lambda k: {
            "ragflow_api_url": "http://mock-ragflow",
            "ragflow_api_key": "mock-key"
        }.get(k)
        yield mock

# --- Tests ---

@pytest.mark.asyncio
async def test_retrieve_success(ragflow_client, mock_config):
    """测试 RAG 检索成功逻辑"""
    mock_response_data = {
        "code": 0,
        "data": [
            {
                "doc_name": "test.pdf",
                "content_with_weight": "这是匹配到的知识内容",
                "similarity": 0.95,
                "chunk_id": "chunk_123"
            }
        ]
    }
    
    # Mock httpx.AsyncClient.post
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = httpx.Response(200, json=mock_response_data)
        
        results = await ragflow_client.retrieve(
            query="什么是南孜？",
            dataset_ids=["ds_001"],
            top_k=1
        )
        
        assert len(results) == 1
        assert results[0]["doc_name"] == "test.pdf"
        assert results[0]["content"] == "这是匹配到的知识内容"
        assert results[0]["similarity"] == 0.95
        
        # 验证调用参数
        args, kwargs = mock_post.call_args
        assert kwargs["json"]["question"] == "什么是南孜？"
        assert kwargs["json"]["dataset_ids"] == ["ds_001"]

@pytest.mark.asyncio
async def test_chat_stream_success(ragflow_client, mock_config):
    """测试 RAGFlow 流式对话 Mock 逻辑 (OpenAI 兼容协议)"""
    
    # 模拟 RAGFlow 返回的 SSE 数据流
    stream_lines = [
        'data: {"choices": [{"delta": {"content": "你好"}}]}',
        'data: {"choices": [{"delta": {"content": "，我是"}}]}',
        'data: {"choices": [{"delta": {"content": " AI助手。"}}]}',
        'data: [DONE]'
    ]
    
    async def mock_aiter_lines():
        for line in stream_lines:
            yield line

    mock_stream_response = AsyncMock()
    mock_stream_response.status_code = 200
    mock_stream_response.aiter_lines = mock_aiter_lines
    mock_stream_response.__aenter__.return_value = mock_stream_response

    # Mock httpx.AsyncClient.stream 容器
    with patch("httpx.AsyncClient.stream", return_value=mock_stream_response):
        chunks = []
        async for chunk in ragflow_client.chat_stream(
            query="你好",
            conversation_id="conv_123",
            config={"app_id": "rag_app_001"}
        ):
            if chunk.get("type") == "answer":
                chunks.append(chunk["content"])
        
        full_response = "".join(chunks)
        assert full_response == "你好，我是 AI助手。"

@pytest.mark.asyncio
async def test_chat_stream_error(ragflow_client, mock_config):
    """测试 RAGFlow 接口报错处理"""
    
    mock_error_response = AsyncMock()
    mock_error_response.status_code = 500
    mock_error_response.read = AsyncMock(return_value=b"Internal Server Error")
    mock_error_response.__aenter__.return_value = mock_error_response

    with patch("httpx.AsyncClient.stream", return_value=mock_error_response):
        chunks = []
        async for chunk in ragflow_client.chat_stream(
            query="你好",
            conversation_id="conv_123",
            config={"app_id": "rag_app_001"}
        ):
            if chunk.get("type") == "error":
                chunks.append(chunk["content"])
        
        assert len(chunks) > 0
        assert "500" in chunks[0]
        assert "Internal Server Error" in chunks[0]

@pytest.mark.asyncio
async def test_ensure_config_missing(ragflow_client):
    """测试配置缺失时的异常抛出"""
    with patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None # 配置不存在
        
        with pytest.raises(ValueError, match="RAGFlow configuration .* is missing"):
            await ragflow_client.retrieve("test", ["ds1"])


@pytest.mark.asyncio
async def test_handle_response_includes_status_when_error_body_empty(ragflow_client):
    response = httpx.Response(502, content=b"")

    with pytest.raises(Exception, match="HTTP 502"):
        await ragflow_client._handle_response(response, "List Datasets")


@pytest.mark.asyncio
async def test_handle_response_error_translation_permission_denied(ragflow_client):
    """测试 _handle_response 正确翻译 RAGFlow 的缺乏权限错误"""
    response = httpx.Response(200, json={
        "code": 100,
        "message": "User '3e7c726dead811f0bbc70242ac120006' lacks permission for datasets: '188b62a2eb7611f093130242ac120006'"
    })

    with pytest.raises(Exception) as excinfo:
        await ragflow_client._handle_response(response, "Delete Datasets")
    
    assert "RAGFlow 权限拒绝" in str(excinfo.value)
    assert "无权操作该知识库" in str(excinfo.value)


@pytest.mark.asyncio
async def test_handle_response_error_translation_not_found(ragflow_client):
    """测试 _handle_response 正确翻译 RAGFlow 的数据集未找到错误"""
    response = httpx.Response(200, json={
        "code": 404,
        "message": "Dataset not found: 'ds_123'"
    })

    with pytest.raises(Exception) as excinfo:
        await ragflow_client._handle_response(response, "Delete Datasets")
    
    assert "RAGFlow 侧未找到该知识库" in str(excinfo.value)