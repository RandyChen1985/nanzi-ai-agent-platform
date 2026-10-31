"""轻量级 Redis 计数限流。

为什么需要它：嵌入组件是**对第三方站点开放**的入口（`/embed/*` 允许被任意站点
iframe 引用），而 `POST /embed/tickets`、`/embed/miniprogram/session`（按 openid
自动开户）此前完全没有频率约束——一旦宿主后端的 API Key 泄露，或小程序侧被刷，
既会灌爆 Redis/DB，也会留下大量垃圾账号。

设计取舍：

- **fail-open**：Redis 不可用时不阻断业务，只记 warning。限流是防护性能力，
  不能因为它自己故障把正常对话打挂。
- 固定窗口（INCR + 首次 EXPIRE），不追求精确滑动窗口：这里防的是「脚本刷」，
  不是计费。
- 返回布尔值（是否超限）而不是抛异常，把「怎么表达」留给端点——聊天接口返回
  429，签发接口也可以选择只告警。
"""

from __future__ import annotations

import logging

from app.core.redis import get_redis

logger = logging.getLogger(__name__)

# 一批端点共用的默认窗口
RATE_LIMIT_WINDOW_SECONDS = 60


async def hit_rate_limit(key: str, limit: int, window_seconds: int = RATE_LIMIT_WINDOW_SECONDS) -> bool:
    """记一次命中；返回 True 表示**已超限**。

    key 必须已经带上业务维度（如 `embed:ticket:{user_id}`），本函数只负责计数。
    """
    if not key or limit <= 0:
        return False
    redis = await get_redis()
    if not redis:
        logger.warning("[RateLimit] Redis 不可用，跳过限流: key=%s", key)
        return False
    try:
        ttl_seconds = max(1, int(window_seconds))
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, ttl_seconds)
        elif int(count) > int(limit) and await _ttl_missing(redis, key):
            # 自愈：INCR 成功但 EXPIRE 失败（连接抖动/进程被杀）会留下一个没有 TTL 的计数键，
            # 之后**永久** 429。只在「已经超限」的冷路径上补一次 TTL，不影响热路径开销。
            await redis.expire(key, ttl_seconds)
        return int(count) > int(limit)
    except Exception as exc:  # noqa: BLE001 - 限流不得打断业务
        logger.warning("[RateLimit] 计数失败，按未超限处理: key=%s err=%s", key, exc)
        return False


async def _ttl_missing(redis, key: str) -> bool:
    """TTL 是否缺失（-1=永久，-2=键不存在）。取不到 TTL 时按「缺失」处理。"""
    try:
        ttl = await redis.ttl(key)
        return int(ttl) < 0
    except Exception:
        return True


async def reset_rate_limit(key: str) -> None:
    """清掉某个计数（仅供测试/运维使用）。"""
    redis = await get_redis()
    if not redis:
        return
    try:
        await redis.delete(key)
    except Exception:
        pass
