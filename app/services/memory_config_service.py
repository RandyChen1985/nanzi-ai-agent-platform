"""
记忆管理中心专用配置：读写 memory_service_configs 表。
与 system_configs / 系统设置页完全分离。
"""
import logging
import time
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm import AsyncSessionLocal
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

CACHE_PREFIX = "memory_config:"
CACHE_TTL = 300


class MemoryConfigService:
    _cache: Optional[Dict[str, dict]] = None
    _cache_at: float = 0
    _CACHE_TTL = 60.0

    @staticmethod
    async def get_all() -> List[Dict[str, Any]]:
        """返回全部配置项（供记忆管理中心 UI）。"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text(
                    "SELECT `key`, `value`, `description`, `is_secret` "
                    "FROM memory_service_configs ORDER BY `key`"
                )
            )
            rows = result.fetchall()
        return [
            {
                "key": row[0],
                "value": row[1] or "",
                "description": row[2] or "",
                "is_secret": bool(row[3]),
            }
            for row in rows
        ]

    @staticmethod
    async def _load_cache() -> Dict[str, dict]:
        if MemoryConfigService._cache and (
            time.time() - MemoryConfigService._cache_at < MemoryConfigService._CACHE_TTL
        ):
            return MemoryConfigService._cache
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("SELECT `key`, `value`, `description`, `is_secret` FROM memory_service_configs")
            )
            rows = result.fetchall()
        configs: Dict[str, dict] = {}
        for row in rows:
            configs[row[0]] = {
                "value": row[1],
                "description": row[2],
                "is_secret": bool(row[3]),
            }
        MemoryConfigService._cache = configs
        MemoryConfigService._cache_at = time.time()
        return configs

    @staticmethod
    async def get(key: str, default: Optional[str] = None) -> Optional[str]:
        redis = await get_redis()
        cache_key = f"{CACHE_PREFIX}{key}"
        if redis:
            cached = await redis.get(cache_key)
            if cached is not None:
                return cached

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("SELECT `value` FROM memory_service_configs WHERE `key` = :key"),
                {"key": key},
            )
            row = result.fetchone()
        if row is None:
            return default
        val = row[0]
        if redis and val is not None:
            await redis.set(cache_key, val, ex=CACHE_TTL)
        return val if val is not None else default

    @staticmethod
    async def get_bool(key: str, default: bool = False) -> bool:
        raw = await MemoryConfigService.get(key)
        if raw is None:
            return default
        return str(raw).strip().lower() in ("1", "true", "yes", "on")

    @staticmethod
    async def get_int(key: str, default: int) -> int:
        raw = await MemoryConfigService.get(key)
        if raw is None:
            return default
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    @staticmethod
    async def get_float(key: str, default: float) -> float:
        raw = await MemoryConfigService.get(key)
        if raw is None:
            return default
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def invalidate_cache():
        MemoryConfigService._cache = None
        MemoryConfigService._cache_at = 0

    @staticmethod
    async def bulk_update(updates: List[Dict[str, Any]], changed_by: str = "admin") -> None:
        if not updates:
            return
        async with AsyncSessionLocal() as session:
            for item in updates:
                key = item["key"]
                value = item.get("value", "")
                await session.execute(
                    text(
                        "UPDATE memory_service_configs SET `value` = :value, "
                        "`updated_at` = CURRENT_TIMESTAMP WHERE `key` = :key"
                    ),
                    {"key": key, "value": value},
                )
            await session.commit()

        MemoryConfigService.invalidate_cache()
        redis = await get_redis()
        if redis:
            for item in updates:
                await redis.delete(f"{CACHE_PREFIX}{item['key']}")
        logger.info("[MemoryConfig] Updated %s keys by %s", len(updates), changed_by)
