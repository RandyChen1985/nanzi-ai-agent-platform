from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# 续约必须是「校验持有者 token 仍未变」才刷新过期时间，否则并发场景下可能
# 把他人（cancel 后新抢到锁的 run）的锁误续期，掩盖真正的锁让渡。
_RENEW_SCRIPT = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end"
)


async def _renew_once(redis, key: str, token: str, ttl_ms: int) -> None:
    try:
        await redis.eval(_RENEW_SCRIPT, 1, key, token, ttl_ms)
    except asyncio.CancelledError:  # pragma: no cover - cancellation may hit during eval
        raise
    except Exception as exc:
        logger.warning("[LockRenewal] renew failed key=%s: %s", key, exc)


async def renew_lock_during_hold(
    *,
    key: str,
    token: str,
    ttl_seconds: int,
) -> None:
    """在锁持有期间定期续约，直到 task 被取消或抛出异常。

    通常由 ``asyncio.create_task`` 启动，并在 ``hold`` 退出时 cancel：
    每次间隔 = ttl/3（但不低于 1s），把过期时间刷新回完整 ttl。
    Redis 单例通过 ``get_redis`` 获取，与两个锁的 acquire/release 保持一致。
    """
    from app.core.redis import get_redis

    redis = await get_redis()
    if redis is None:
        logger.warning("[LockRenewal] redis unavailable; skipping renewal key=%s", key)
        return

    ttl_ms = max(int(ttl_seconds * 1000), 1000)
    interval = max(ttl_seconds / 3, 1.0)
    try:
        while True:
            await asyncio.sleep(interval)
            await _renew_once(redis, key, token, ttl_ms)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # pragma: no cover - defensive settle
        logger.warning("[LockRenewal] renewal loop ended key=%s: %s", key, exc)