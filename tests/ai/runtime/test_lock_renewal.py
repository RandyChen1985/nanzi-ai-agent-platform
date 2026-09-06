import asyncio

import pytest

from app.services.ai.runtime.lock_renewal import (
    _renew_once,
    renew_lock_during_hold,
)

pytestmark = pytest.mark.no_infrastructure


class FakeRedis:
    """模拟 Redis，支持可带过期时间的 set 与两种 Lua 脚本语义。

    ``eval`` 依据脚本内容分派：解锁脚本(RENEW_SCRIPT 之外)校验 token 后 del；
    续约脚本校验 token 后刷新 pexpire，返回 1/0。
    """

    def __init__(self):
        self.store: dict[str, str] = {}
        self.expiry_ms: dict[str, int] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return False
        self.store[key] = value
        self.expiry_ms[key] = ex * 1000 if ex is not None else -1
        return True

    async def eval(self, script, numkeys, *args):
        key, token = args[0], args[1]
        if numkeys != 1:
            raise ValueError("fake redis expects 1 key for tests")
        if "pexpire" in script:
            # 续约脚本：token 匹配才刷新过期时间
            if self.store.get(key) == token:
                ttl_ms = int(args[2])
                self.expiry_ms[key] = ttl_ms
                return 1
            return 0
        # 解锁脚本：校验 token 后删除
        if self.store.get(key) == token:
            self.store.pop(key, None)
            self.expiry_ms.pop(key, None)
            return 1
        return 0

    async def delete(self, key):
        if key in self.store:
            del self.store[key]
            self.expiry_ms.pop(key, None)
            return 1
        return 0


@pytest.mark.asyncio
async def test_renew_once_renews_when_token_matches(monkeypatch):
    fake = FakeRedis()
    await fake.set("lock:u1:c1", "tok-a", ex=120)
    initial = fake.expiry_ms["lock:u1:c1"]

    async def _redis():
        return fake

    monkeypatch.setattr("app.core.redis.get_redis", _redis)

    await _renew_once(fake, "lock:u1:c1", "tok-a", 120_000)

    # token 仍匹配：过期时间被刷新回完整 ttl
    assert fake.store["lock:u1:c1"] == "tok-a"
    assert fake.expiry_ms["lock:u1:c1"] == 120_000
    assert fake.expiry_ms["lock:u1:c1"] >= initial


@pytest.mark.asyncio
async def test_renew_once_skips_when_token_changed(monkeypatch):
    fake = FakeRedis()
    # 锁已被他人(new-token)重新抢到，旧持有者(old-token)的续约必须被忽略
    await fake.set("lock:u1:c1", "new-token", ex=60)
    fake.expiry_ms["lock:u1:c1"] = 15_000
    before = fake.expiry_ms["lock:u1:c1"]

    async def _redis():
        return fake

    monkeypatch.setattr("app.core.redis.get_redis", _redis)

    # 旧持有者尝试续约，但锁内 token 已变：不得刷新过期时间
    await _renew_once(fake, "lock:u1:c1", "old-token", 120_000)

    assert fake.store["lock:u1:c1"] == "new-token"
    assert fake.expiry_ms["lock:u1:c1"] == before


@pytest.mark.asyncio
async def test_renew_lock_during_hold_cancels_cleanly(monkeypatch):
    # 完整续约循环：可取消，cancel 后任务结束、不对外抛错。
    real_sleep = asyncio.sleep  # 在打补丁前保存真实 sleep
    fake = FakeRedis()
    await fake.set("lock:u1:c1", "tok", ex=120)

    async def _redis():
        return fake

    calls = {"count": 0}

    async def _spy_renew(redis, key, token, ttl_ms):
        calls["count"] += 1

    monkeypatch.setattr("app.core.redis.get_redis", _redis)
    monkeypatch.setattr(
        "app.services.ai.runtime.lock_renewal._renew_once",
        _spy_renew,
    )
    # 用可控的假 sleep 加速循环，让续约真正发生并被 cancel 中断；
    # 假 sleep 内部仍调用真实 sleep(0) 让出事件循环，避免自递归
    async def _fast_sleep(seconds):
        await real_sleep(0)

    monkeypatch.setattr(
        "app.services.ai.runtime.lock_renewal.asyncio.sleep",
        _fast_sleep,
    )

    task = asyncio.create_task(
        renew_lock_during_hold(key="lock:u1:c1", token="tok", ttl_seconds=3)
    )
    await real_sleep(0.05)
    assert calls["count"] >= 1  # 循环已真实续约过
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task