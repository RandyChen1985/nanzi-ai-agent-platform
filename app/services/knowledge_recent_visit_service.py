"""知识库「最近访问」。

按人维护一个 ZSET（member = RAGFlow Dataset ID，score = 访问时间戳），
只保留 30 天：写的时候裁一次、读的时候再裁一次，避免「只写不读」的库永远不消失。

放在 Redis 而不是 MySQL，是因为它天然是一个带过期的排行榜：
不需要按库反查、不需要历史记录，过期即丢，也不该让主库多一条会无限增长的表。
Redis 不可用时全部降级为空，不能让「最近访问」把知识库首页拖挂。
"""

import logging
import time
from typing import Any, Dict, List

from app.core.redis import get_redis

logger = logging.getLogger(__name__)


class KnowledgeRecentVisitService:
    """最近访问记录（Redis ZSET，30 天过期）。"""

    RETENTION_SECONDS = 30 * 24 * 60 * 60
    DEFAULT_LIMIT = 20

    @classmethod
    def _key(cls, user_id: Any) -> str:
        return f"knowledge:recent:{int(user_id)}"

    @classmethod
    async def record(cls, user_id: Any, dataset_id: str) -> None:
        """记一次访问。同一库重复访问只更新时间戳，不会产生多条记录。"""
        dataset_id = str(dataset_id or "").strip()
        if not dataset_id:
            return
        try:
            redis = await get_redis()
        except Exception as exc:  # noqa: BLE001 - Redis 故障不该影响主流程
            logger.warning("[Knowledge] 最近访问写入跳过（Redis 不可用）: %s", exc)
            return
        if not redis:
            return

        key = cls._key(user_id)
        now = time.time()
        try:
            async with redis.pipeline(transaction=True) as pipe:
                pipe.zadd(key, {dataset_id: now})
                # 顺手清掉超过保留期的成员：访问越活跃的库越靠前，旧的自然沉底被裁掉
                pipe.zremrangebyscore(key, 0, now - cls.RETENTION_SECONDS)
                pipe.expire(key, cls.RETENTION_SECONDS)
                await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Knowledge] 最近访问写入失败: %s", exc)

    @classmethod
    async def list_ids(cls, user_id: Any, limit: int = DEFAULT_LIMIT) -> List[str]:
        """按最近访问倒序返回 dataset_id。"""
        try:
            redis = await get_redis()
        except Exception:
            return []
        if not redis:
            return []

        key = cls._key(user_id)
        now = time.time()
        safe_limit = max(1, min(int(limit or cls.DEFAULT_LIMIT), 100))
        try:
            await redis.zremrangebyscore(key, 0, now - cls.RETENTION_SECONDS)
            rows = await redis.zrevrange(key, 0, safe_limit - 1)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Knowledge] 最近访问读取失败: %s", exc)
            return []
        return [str(row) for row in rows or []]

    @classmethod
    async def snapshot(cls, user_id: Any, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
        """给接口用的形状：ids 供列表排序，total 供左栏角标。"""
        ids = await cls.list_ids(user_id, limit=limit)
        return {"dataset_ids": ids, "total": len(ids)}
