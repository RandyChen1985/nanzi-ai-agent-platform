import pytest
from unittest.mock import AsyncMock, MagicMock

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_sandbox_degraded_set_get_clear(monkeypatch):
    from app.services.ai.runtime import sandbox_degradation as sd

    store: dict[str, str] = {}
    fake_redis = MagicMock()

    async def _set(key, value, ex=None):
        store[key] = value

    async def _delete(key):
        store.pop(key, None)

    async def _get(key):
        return store.get(key)

    fake_redis.set = AsyncMock(side_effect=_set)
    fake_redis.delete = AsyncMock(side_effect=_delete)
    fake_redis.get = AsyncMock(side_effect=_get)
    monkeypatch.setattr("app.core.redis.get_redis", AsyncMock(return_value=fake_redis))

    assert await sd.get_sandbox_degraded("conv-1") is None

    await sd.set_sandbox_degraded("conv-1", "沙箱不可用，已降级为本地执行。")
    assert await sd.get_sandbox_degraded("conv-1") == "沙箱不可用，已降级为本地执行。"

    await sd.set_sandbox_degraded("conv-2", "another")
    assert await sd.get_sandbox_degraded("conv-2") == "another"
    # 会话独立
    assert await sd.get_sandbox_degraded("conv-1") == "沙箱不可用，已降级为本地执行。"

    await sd.clear_sandbox_degraded("conv-1")
    assert await sd.get_sandbox_degraded("conv-1") is None
    # 另一会话不受影响
    assert await sd.get_sandbox_degraded("conv-2") == "another"


@pytest.mark.asyncio
async def test_sandbox_degraded_redis_unavailable_is_noop(monkeypatch):
    from app.services.ai.runtime import sandbox_degradation as sd

    async def _redis():
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.core.redis.get_redis", _redis)
    # 应静默记录、不抛异常
    await sd.set_sandbox_degraded("conv-1", "msg")
    assert await sd.get_sandbox_degraded("conv-1") is None
    await sd.clear_sandbox_degraded("conv-1")
