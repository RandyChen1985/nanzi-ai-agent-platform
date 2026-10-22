"""MCP 管理类出站操作的轻量限流。

`/verify`、`/sync` 会主动连接用户指定的地址，是最容易被用来做端口探测/打满连接池的
入口。这里用 Redis 做一分钟固定窗口限流；Redis 不可用时 fail-open，保持可用性优先。
"""
from __future__ import annotations

import logging
import time

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# 管理操作都是低频动作；阈值只拦异常刷接口，正常人工操作不会触发。
DEFAULT_LIMITS = {
    "verify": (20, 60),
    "sync": (10, 60),
    "execute": (60, 60),
}


async def enforce_mcp_management_rate_limit(
    user_id: str | int | None,
    action: str,
) -> None:
    limit, window_seconds = DEFAULT_LIMITS.get(action, (0, 60))
    if not user_id or limit <= 0:
        return
    try:
        from app.core.redis import get_redis

        redis = await get_redis()
        if redis is None:
            return
        window = int(time.time() // window_seconds)
        key = f"mcp_mgmt_rate_limit:{action}:{user_id}:{window}"
        current = await redis.incr(key)
        if int(current) == 1:
            await redis.expire(key, window_seconds + 10)
        if int(current) > limit:
            raise HTTPException(
                status_code=429,
                detail="MCP 操作过于频繁，请稍后再试",
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - 限流不可用时不能阻断主流程
        logger.warning("MCP management rate limit check failed: %s", exc)
        return
