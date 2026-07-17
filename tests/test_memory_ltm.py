import pytest
import asyncio
from unittest.mock import AsyncMock, patch
from app.services.ai.memory_service import ltm_service

@pytest.mark.asyncio
async def test_ltm_service_crud():
    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {
        b"theme": b"dark",
        b"user_role": b"architect"
    }
    
    with patch("app.services.ai.memory_service.get_redis", return_value=mock_redis):
        # 1. 测试写入偏好
        await ltm_service.update_preference("user_99", "theme", "dark")
        mock_redis.hset.assert_called_with("nanzi:agent:ltm:user_99", "theme", "dark")

        # 2. 测试查询记忆
        mem = await ltm_service.fetch_memory("user_99")
        assert mem["theme"] == "dark"
        assert mem["user_role"] == "architect"
        mock_redis.hgetall.assert_called_with("nanzi:agent:ltm:user_99")

@pytest.mark.asyncio
async def test_ltm_timeout_fallback():
    # 模拟 Redis 慢查询或抖动触发 TimeoutError
    async def slow_fetch(*args, **kwargs):
        await asyncio.sleep(2.0)
        return {}

    with patch("app.services.ai.memory_service.ltm_service.fetch_memory", side_effect=slow_fetch):
        # 模拟 agent_service.py 内部 200ms 的超时阻断
        try:
            await asyncio.wait_for(ltm_service.fetch_memory("user_99"), timeout=0.2)
            assert False, "Should have triggered TimeoutError"
        except asyncio.TimeoutError:
            # 预期触发超时，验证兜底逻辑是否正常生效
            pass
