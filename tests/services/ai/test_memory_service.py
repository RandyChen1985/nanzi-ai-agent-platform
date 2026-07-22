import pytest
import json
from unittest.mock import MagicMock, AsyncMock, patch
from app.services.ai.memory_service import LongTermMemoryService, MemoryService

# --- Mocks ---

@pytest.fixture(scope="function", autouse=True)
async def init_infrastructure():
    """Override infrastructure initialization."""
    with patch("app.core.database.init_db", new_callable=AsyncMock), \
         patch("app.core.database.close_db", new_callable=AsyncMock), \
         patch("app.core.redis.init_redis", new_callable=AsyncMock), \
         patch("app.core.redis.close_redis", new_callable=AsyncMock):
        yield

@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    # Mock Pipeline: redis.pipeline() 返回一个异步上下文管理器
    mock_pipe = AsyncMock()
    mock_pipe.rpush = AsyncMock(return_value=1)
    mock_pipe.ltrim = AsyncMock(return_value=True)
    mock_pipe.expire = AsyncMock(return_value=True)
    mock_pipe.execute = AsyncMock(return_value=[1, True, True])
    # 让 pipeline() 作为异步上下文管理器使用
    mock_pipe.__aenter__ = AsyncMock(return_value=mock_pipe)
    mock_pipe.__aexit__ = AsyncMock(return_value=False)
    redis.pipeline = MagicMock(return_value=mock_pipe)
    # 保留其他方法 Mock（供其他测试用）
    redis.lrange = AsyncMock(return_value=[])
    redis.delete = AsyncMock(return_value=1)
    redis._mock_pipe = mock_pipe  # 暴露 pipe 引用供测试断言
    return redis

# --- Tests ---

def test_memory_service_get_key():
    """测试 Redis Key 生成逻辑"""
    service = MemoryService()
    assert service._get_key("user123", "conv456") == "conversation:user123:conv456:history"
    assert service._get_data_result_key("user123", "conv456") == "conversation:user123:conv456:last_data_result"
    assert service._get_data_result_stack_key("user123", "conv456") == "conversation:user123:conv456:data_result_stack_v1"
    assert service._get_session_tool_artifact_key("user123", "conv456") == "conversation:user123:conv456:session_tool_artifact_v1"
    assert service._get_active_conversation_key("user123") == "conversation:user123:active"
    assert LongTermMemoryService()._get_key("user123") == "nanzi:agent:ltm:user123"


@pytest.mark.parametrize("user_id", [None, "", "   "])
def test_memory_service_rejects_missing_user_id(user_id):
    """缺少用户身份时，所有用户私有 Redis Key 都必须拒绝生成。"""
    service = MemoryService()
    key_builders = [
        lambda: service._get_key(user_id, "conv456"),
        lambda: service._get_data_result_key(user_id, "conv456"),
        lambda: service._get_data_result_stack_key(user_id, "conv456"),
        lambda: service._get_session_tool_artifact_key(user_id, "conv456"),
        lambda: service._get_active_conversation_key(user_id),
        lambda: LongTermMemoryService()._get_key(user_id),
    ]

    for build_key in key_builders:
        with pytest.raises(ValueError, match="缺少有效 user_id"):
            build_key()

@pytest.mark.asyncio
async def test_memory_service_add_message(mock_redis):
    """测试添加消息（使用 Pipeline）"""
    service = MemoryService()
    
    with patch("app.services.ai.memory_service.get_redis", new_callable=AsyncMock) as mock_get_redis:
        mock_get_redis.return_value = mock_redis
        
        await service.add_message("u1", "c1", "user", "Hello Redis")
        
        pipe = mock_redis._mock_pipe
        
        # 验证 Pipeline 内 RPUSH 被调用且参数正确
        assert pipe.rpush.called
        args, _ = pipe.rpush.call_args
        key, val = args
        assert key == "conversation:u1:c1:history"
        msg_data = json.loads(val)
        assert msg_data["role"] == "user"
        assert msg_data["content"] == "Hello Redis"
        
        # 验证 LTRIM 和 EXPIRE 也在 Pipeline 中被调用
        pipe.ltrim.assert_called_once()
        pipe.expire.assert_called_once()
        # 验证 execute 被调用（提交 Pipeline）
        pipe.execute.assert_called_once()

@pytest.mark.asyncio
async def test_memory_service_get_history(mock_redis):
    """测试获取历史记录及其限额过滤"""
    service = MemoryService(max_history_turns=2) # Max 4 messages
    
    # Mock data in Redis (5 items)
    mock_data = [
        json.dumps({"role": "user", "content": f"msg {i}"})
        for i in range(5)
    ]
    mock_redis.lrange.return_value = mock_data
    
    with patch("app.services.ai.memory_service.get_redis", new_callable=AsyncMock) as mock_get_redis:
        mock_get_redis.return_value = mock_redis
        
        # 1. Fetch with service default limit (4)
        history = await service.get_history("u1", "c1")
        assert len(history) == 4
        assert history[-1]["content"] == "msg 4"
        assert history[0]["content"] == "msg 1"
        
        # 2. Fetch with custom limit (2)
        history_limited = await service.get_history("u1", "c1", limit=2)
        assert len(history_limited) == 2
        assert history_limited[0]["content"] == "msg 3"

@pytest.mark.asyncio
async def test_memory_service_clear_history(mock_redis):
    """测试清理历史记录"""
    service = MemoryService()
    
    with patch("app.services.ai.memory_service.get_redis", new_callable=AsyncMock) as mock_get_redis:
        mock_get_redis.return_value = mock_redis
        
        await service.clear_history("u1", "c1")
        assert mock_redis.delete.call_count == 4
        mock_redis.delete.assert_any_call("conversation:u1:c1:history")
        mock_redis.delete.assert_any_call("conversation:u1:c1:last_data_result")
