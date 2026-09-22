"""Shared local Redis vector index rebuild (metadata + ChatBI examples)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.core import redis
from app.core.config import settings

logger = logging.getLogger(__name__)

REBUILD_LOCK_KEY = "nanzi:lock:rebuild_local_vectors"
REBUILD_LOCK_TTL_SEC = 1800


async def rebuild_local_vector_indexes(
    *,
    drop_indexes: bool = True,
    acquire_lock: bool = False,
    trigger: str = "manual",
) -> Dict[str, Any]:
    """
    Ensure vector index schemas exist, then trigger background full sync.

    When drop_indexes=True (manual「一键重构」), existing indexes and documents
    are dropped first so Embedding 维度变更后可干净重建。
    When drop_indexes=False (startup), only ensure + sync — keep existing vectors
    searchable while re-embedding in the background.
    """
    if not settings.REDIS_ENABLE:
        raise RuntimeError("Redis is disabled")

    r = await redis.get_redis()
    if not r:
        await redis.init_redis()
        r = await redis.get_redis()
    if not r:
        raise RuntimeError("Redis client not available")

    lock_held = False
    if acquire_lock:
        try:
            acquired = await r.set(REBUILD_LOCK_KEY, trigger, nx=True, ex=REBUILD_LOCK_TTL_SEC)
            if not acquired:
                msg = "Local vector rebuild skipped: another rebuild is already in progress"
                logger.info("[%s] %s", trigger, msg)
                return {
                    "status": "skipped",
                    "message": msg,
                    "logs": [msg],
                    "table_count": 0,
                    "metric_count": 0,
                    "example_count": 0,
                }
            lock_held = True
        except Exception as lock_err:
            logger.warning("[%s] Failed to acquire rebuild lock, proceeding anyway: %s", trigger, lock_err)

    from app.services.ai.metadata_index_service import MetadataIndexService
    from app.services.ai.example_index_service import ExampleIndexService

    logs: List[str] = []
    try:
        if drop_indexes:
            meta_idx = await MetadataIndexService.index_name()
            try:
                await r.execute_command("FT.DROPINDEX", meta_idx, "DD")
                logs.append(f"Successfully dropped metadata index: {meta_idx} (with documents)")
            except Exception as e:
                logs.append(f"Metadata index drop skipped or failed: {str(e)}")

            ex_idx = await ExampleIndexService.index_name()
            try:
                await r.execute_command("FT.DROPINDEX", ex_idx, "DD")
                logs.append(f"Successfully dropped example index: {ex_idx} (with documents)")
            except Exception as e:
                logs.append(f"Example index drop skipped or failed: {str(e)}")

        await MetadataIndexService.ensure_index(force=True)
        logs.append(
            "Recreated metadata index schema" if drop_indexes else "Ensured metadata index schema"
        )
        await ExampleIndexService.ensure_index(force=True)
        logs.append(
            "Recreated example index schema" if drop_indexes else "Ensured example index schema"
        )

        from app.core.orm import AsyncSessionLocal
        from app.services.metadata_service import MetadataService
        from app.models.chatbi_example import ChatBIExample
        from sqlalchemy import select, func

        table_count = 0
        metric_count = 0
        example_count = 0
        enabled_datasets: Optional[list] = None

        async with AsyncSessionLocal() as db:
            try:
                datasets = await MetadataService.get_datasets(db)
                enabled_datasets = [ds for ds in datasets if ds.status == 1]
                for ds in enabled_datasets:
                    for table in ds.tables:
                        if hasattr(table, "status") and table.status != 1:
                            continue
                        table_count += 1
                    if ds.metrics:
                        metric_count += len(ds.metrics)
            except Exception as db_err:
                logs.append(f"Counting metadata items failed: {str(db_err)}")

            try:
                stmt = select(func.count(ChatBIExample.id)).where(ChatBIExample.status == "approved")
                res = await db.execute(stmt)
                example_count = res.scalar() or 0
            except Exception as db_err:
                logs.append(f"Counting examples failed: {str(db_err)}")

        await MetadataIndexService.sync_all_datasets()
        enabled_n = len(enabled_datasets) if enabled_datasets is not None else "unknown"
        logs.append(f"Triggered background sync for all enabled datasets (Total: {enabled_n})")
        await ExampleIndexService.sync_all_examples()
        logs.append(f"Triggered background sync for all approved examples (Total: {example_count})")

        action = "重构" if drop_indexes else "同步"
        msg = (
            f"已成功{action}本地向量索引。已在后台启动重新向量化任务，共计："
            f"{table_count} 张数据表、{metric_count} 个业务指标及 {example_count} 条案例。"
            f"任务在后台异步执行，请在后台终端控制台查看最新进度。"
        )
        logger.info("[%s] %s", trigger, msg)
        # Keep lock until TTL so reload/multi-worker won't immediately re-trigger
        # while background sync is still running.
        lock_held = False
        return {
            "status": "success",
            "message": msg,
            "logs": logs,
            "table_count": table_count,
            "metric_count": metric_count,
            "example_count": example_count,
        }
    except Exception:
        if lock_held:
            try:
                await r.delete(REBUILD_LOCK_KEY)
            except Exception:
                pass
        raise


async def maybe_rebuild_local_vectors_on_startup() -> None:
    """When metadata_provider=local, ensure indexes and full-sync without DROP."""
    if not settings.REDIS_ENABLE:
        logger.info("[startup] Redis disabled; skip local vector sync")
        return

    from app.services.config_service import ConfigService

    try:
        provider = await ConfigService.get("metadata_provider", default="local")
    except Exception as e:
        logger.warning("[startup] Failed to read metadata_provider; skip local vector sync: %s", e)
        return

    if str(provider or "").strip().lower() != "local":
        logger.info("[startup] metadata_provider=%s; skip local vector sync", provider)
        return

    logger.info("[startup] metadata_provider=local; triggering local vector sync (no drop)")
    try:
        result = await rebuild_local_vector_indexes(
            drop_indexes=False,
            acquire_lock=True,
            trigger="startup",
        )
        for line in result.get("logs") or []:
            logger.info("[startup-rebuild] %s", line)
    except Exception as e:
        logger.warning("[startup] Local vector sync failed: %s", e)
