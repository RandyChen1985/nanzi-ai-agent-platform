"""Shared local Redis vector index rebuild (metadata + ChatBI examples).

两条路径：

* ``rebuild_local_vector_indexes()``：同步编排，供启动流程与旧调用方使用。
* ``start_local_vector_rebuild()``：任务化编排（Redis Stream 事件 + SSE），
  供「重构本地向量数据」按钮使用，前端抽屉据此展示真实进度与逐条日志。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Set

from app.core import redis
from app.core.config import settings
from app.services.task_lock import (
    LOCAL_VECTORS_LOCK_KEY,
    STARTUP_LOCAL_VECTORS_LOCK_KEY,
    local_vectors_lock,
    startup_local_vectors_lock,
)
from app.services.task_log_service import task_log_service

logger = logging.getLogger(__name__)

REBUILD_LOCK_KEY = LOCAL_VECTORS_LOCK_KEY
REBUILD_LOCK_TTL_SEC = 1800
REBUILD_SCOPE = "local_vector_rebuild"

#: 持有后台任务引用，避免被垃圾回收提前取消。
_BACKGROUND_TASKS: Set[asyncio.Task] = set()


async def _client():
    r = await redis.get_redis()
    if not r:
        await redis.init_redis()
        r = await redis.get_redis()
    if not r:
        raise RuntimeError("Redis client not available")
    return r


def _progress(done: int, total: int) -> int:
    if total <= 0:
        return 0
    return max(0, min(100, int(done * 100 / total)))


async def get_active_rebuild_task_id() -> Optional[str]:
    """返回当前持有重构锁的任务 ID（无则 None）。"""
    return await local_vectors_lock().owner()


async def _run_local_vector_rebuild(task_id: str, trigger: str, r) -> None:
    """执行一次任务化重构：全程持有锁（自动续期），结束时释放。"""
    async with local_vectors_lock(r).hold(task_id):
        await _run_local_vector_rebuild_locked(task_id, trigger, r)


async def _run_local_vector_rebuild_locked(task_id: str, trigger: str, r) -> None:
    from app.services.ai.metadata_index_service import MetadataIndexService
    from app.services.ai.example_index_service import ExampleIndexService
    from app.services.ai.redis_index_utils import last_ensure_result

    failed_items: List[Dict[str, str]] = []
    done = 0
    total = 0
    dataset_success = 0
    dataset_failed = 0
    example_stats: Dict[str, int] = {"total": 0, "success": 0, "failed": 0, "skipped": 0}

    async def _publish(**kwargs: Any) -> None:
        try:
            await task_log_service.publish(task_id, **kwargs)
        except Exception as e:
            # 事件写失败不能中断重构本身。
            logger.warning("[vector-rebuild] 发布进度事件失败: %s", e)

    try:
        await _publish(
            event="started",
            stage="lock",
            message="已获取重构锁，开始重构本地向量索引",
            progress=0,
            extra={"trigger": trigger},
        )

        # --- 删除旧索引 ---
        for service, label in ((MetadataIndexService, "元数据"), (ExampleIndexService, "经验案例")):
            idx = await service.index_name()
            try:
                await r.execute_command("FT.DROPINDEX", idx, "DD")
                await _publish(
                    event="progress",
                    stage="drop",
                    message=f"已删除{label}索引 {idx}（含旧向量）",
                )
            except Exception as e:
                await _publish(
                    event="progress",
                    stage="drop",
                    message=f"{label}索引 {idx} 删除跳过：{e}",
                )

        # --- 重建索引（并回报维度）---
        for service, label, log_tag in (
            (MetadataIndexService, "元数据", "MetadataIndex"),
            (ExampleIndexService, "经验案例", "ExampleIndex"),
        ):
            idx = await service.index_name()
            ok = await service.ensure_index(force=True)
            detail = last_ensure_result(idx) or {}
            new_dim = detail.get("configured_dim")
            old_dim = detail.get("index_dim")
            if not ok:
                message = f"{label}索引 {idx} 重建失败：{detail.get('message') or '未知原因'}"
                failed_items.append({"name": f"{label}索引", "error": message})
                await _publish(event="progress", stage="index", message=message)
                continue
            if detail.get("action") == "recreated" and old_dim is not None:
                message = f"{label}索引 {idx} 已重建（向量维度 {old_dim} → {new_dim}）"
            else:
                message = f"{label}索引 {idx} 已就绪（向量维度 {new_dim}）"
            await _publish(event="progress", stage="index", message=message)

        # --- 统计总量 ---
        from app.core.orm import AsyncSessionLocal
        from app.services.metadata_service import MetadataService
        from app.models.chatbi_example import ChatBIExample
        from sqlalchemy import func, select

        table_count = 0
        metric_count = 0
        dataset_count = 0
        try:
            async with AsyncSessionLocal() as db:
                datasets = await MetadataService.get_datasets(db)
                enabled = [ds for ds in datasets if ds.status == 1]
                dataset_count = len(enabled)
                for ds in enabled:
                    for table in ds.tables:
                        if hasattr(table, "status") and table.status != 1:
                            continue
                        table_count += 1
                    if ds.metrics:
                        metric_count += 1
                stmt = select(func.count(ChatBIExample.id)).where(
                    ChatBIExample.status == "approved"
                )
                res = await db.execute(stmt)
                example_total = int(res.scalar() or 0)
        except Exception as db_err:
            logger.warning("[vector-rebuild] 统计待重构条目失败: %s", db_err)
            example_total = 0
            await _publish(
                event="progress",
                stage="count",
                message=f"统计待重构条目失败，仍将尝试同步：{db_err}",
            )

        total = dataset_count + example_total
        await _publish(
            event="progress",
            stage="count",
            message=(
                f"待重构：{dataset_count} 个启用数据集（{table_count} 张表、"
                f"{metric_count} 个指标）、{example_total} 条已批案例"
            ),
            progress=0,
            done=0,
            total=total,
            extra={
                "dataset_count": dataset_count,
                "table_count": table_count,
                "metric_count": metric_count,
                "example_count": example_total,
            },
        )

        # --- 元数据逐数据集 ---
        async def _on_metadata_progress(payload: Dict[str, Any]) -> None:
            nonlocal done, dataset_success, dataset_failed
            phase = payload.get("phase")
            if phase == "dataset_done":
                dataset_success += 1
                done += 1
                failed_note = (
                    f"，其中 {payload['failed']} 个文档向量化失败" if payload.get("failed") else ""
                )
                await _publish(
                    event="progress",
                    stage="metadata",
                    message=(
                        f"数据集【{payload.get('dataset_name')}】完成："
                        f"表 {payload.get('tables', 0)} + 指标 {payload.get('metrics', 0)}"
                        f"{failed_note}（{payload.get('elapsed_ms', 0)} ms）"
                    ),
                    progress=_progress(done, total),
                    done=done,
                    total=total,
                )
            elif phase == "dataset_failed":
                dataset_failed += 1
                done += 1
                name = f"数据集【{payload.get('dataset_name')}】"
                error = str(payload.get("error") or "同步失败")
                failed_items.append({"name": name, "error": error})
                await _publish(
                    event="progress",
                    stage="metadata",
                    message=f"{name}同步失败：{error}",
                    progress=_progress(done, total),
                    done=done,
                    total=total,
                    error_detail=error,
                )

        await _publish(
            event="progress",
            stage="metadata",
            message=f"开始重新向量化 {dataset_count} 个启用数据集的元数据",
            progress=_progress(done, total),
            done=done,
            total=total,
        )
        try:
            result = await MetadataIndexService.sync_all_datasets(
                progress_cb=_on_metadata_progress, wait=True
            )
            dataset_success = int(result.get("success", dataset_success))
            dataset_failed = int(result.get("failed", dataset_failed))
        except Exception as e:
            logger.error("[vector-rebuild] 元数据同步阶段失败: %s", e, exc_info=True)
            failed_items.append({"name": "元数据同步阶段", "error": str(e)})
            await _publish(
                event="progress",
                stage="metadata",
                message=f"元数据同步阶段异常：{e}",
                error_detail=str(e),
            )

        # --- 案例逐条 ---
        done = dataset_count
        await _publish(
            event="progress",
            stage="examples",
            message=f"开始重新向量化 {example_total} 条已批案例",
            progress=_progress(done, total),
            done=done,
            total=total,
        )

        async def _on_example_progress(payload: Dict[str, Any]) -> None:
            nonlocal done, example_stats
            phase = payload.get("phase")
            if phase == "example_done":
                example_stats["success"] += 1
                done += 1
                await _publish(
                    event="progress",
                    stage="examples",
                    message=(
                        f"案例 #{payload.get('example_id')}（{payload.get('dataset_name')}）"
                        f"完成（{payload.get('elapsed_ms', 0)} ms）"
                    ),
                    progress=_progress(done, total),
                    done=done,
                    total=total,
                )
            elif phase == "example_failed":
                example_stats["failed"] += 1
                done += 1
                name = f"案例 #{payload.get('example_id')}"
                error = str(payload.get("error") or "同步失败")
                failed_items.append({"name": name, "error": error})
                await _publish(
                    event="progress",
                    stage="examples",
                    message=f"{name}同步失败：{error}",
                    progress=_progress(done, total),
                    done=done,
                    total=total,
                    error_detail=error,
                )
            elif phase == "example_skipped":
                example_stats["skipped"] += 1
                done += 1
                await _publish(
                    event="progress",
                    stage="examples",
                    message=(
                        f"案例 #{payload.get('example_id')} 跳过："
                        f"{payload.get('reason') or '无需向量化'}"
                    ),
                    progress=_progress(done, total),
                    done=done,
                    total=total,
                )

        try:
            stats = await ExampleIndexService.sync_all_examples(
                progress_cb=_on_example_progress, wait=True
            )
            example_stats.update({k: int(v) for k, v in (stats or {}).items()})
        except Exception as e:
            logger.error("[vector-rebuild] 案例同步阶段失败: %s", e, exc_info=True)
            failed_items.append({"name": "案例同步阶段", "error": str(e)})
            await _publish(
                event="progress",
                stage="examples",
                message=f"案例同步阶段异常：{e}",
                error_detail=str(e),
            )

        # --- 汇总 ---
        success_total = dataset_success + example_stats.get("success", 0)
        failed_total = dataset_failed + example_stats.get("failed", 0)
        skipped_total = example_stats.get("skipped", 0)
        summary = (
            f"重构完成：成功 {success_total} 项"
            f"（数据集 {dataset_success} + 案例 {example_stats.get('success', 0)}），"
            f"失败 {failed_total} 项"
        )
        if skipped_total:
            summary += f"，跳过 {skipped_total} 项"

        has_fatal = any(
            item["name"].endswith("阶段") or item["name"].endswith("索引")
            for item in failed_items
        )
        # 全盘失败才算 failed；部分失败仍以 completed 收尾，失败明细在汇总里如实呈现。
        terminal = "completed"
        if success_total == 0 and (failed_total > 0 or has_fatal):
            terminal = "failed"

        await _publish(
            event=terminal,
            stage=terminal,
            message=summary,
            progress=100,
            done=done,
            total=total or done,
            error_detail=(f"{failed_total} 项失败，详见日志" if failed_total else None),
            failed_items=failed_items or None,
            extra={
                "dataset_success": dataset_success,
                "dataset_failed": dataset_failed,
                "example_success": example_stats.get("success", 0),
                "example_failed": example_stats.get("failed", 0),
                "example_skipped": skipped_total,
                "success_total": success_total,
                "failed_total": failed_total,
            },
        )
    except Exception as e:
        logger.error("[vector-rebuild] 重构任务异常终止: %s", e, exc_info=True)
        await _publish(
            event="failed",
            stage="failed",
            message=f"重构任务异常终止：{e}",
            progress=100,
            done=done,
            total=total or done,
            error_detail=str(e),
            failed_items=failed_items or None,
        )


async def start_local_vector_rebuild(*, trigger: str = "manual") -> Optional[str]:
    """启动一次任务化的本地向量重构，返回 task_id；已有任务在跑则返回 None。"""
    if not settings.REDIS_ENABLE:
        raise RuntimeError("Redis is disabled")

    r = await _client()
    lock = local_vectors_lock(r)
    task_id = task_log_service.new_task_id()
    try:
        acquired = await lock.acquire(task_id)
    except Exception as e:
        raise RuntimeError(f"无法获取重构锁：{e}") from e
    if not acquired:
        return None

    # 抢到锁之后的任何失败都必须把锁还回去，否则用户会被自己的残留锁挡住 30 分钟。
    try:
        await task_log_service.create_task(
            REBUILD_SCOPE,
            task_id=task_id,
            meta={"trigger": trigger},
        )
        task = asyncio.create_task(_run_local_vector_rebuild(task_id, trigger, r))
    except BaseException:
        await lock.release(task_id)
        raise
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task_id


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
        # 启动路径的同步是 fire-and-forget、无法感知结束时间，故用**独立的**启动锁并
        # 故意保留到 TTL 到期（防止 reload / 多 worker 反复触发）；手动重构走
        # LOCAL_VECTORS_LOCK_KEY，两者互不阻塞。
        try:
            acquired = await startup_local_vectors_lock(r).acquire(trigger)
            if not acquired:
                msg = "Local vector rebuild skipped: another startup sync is already in progress"
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
        # Keep the startup lock until TTL so reload/multi-worker won't immediately
        # re-trigger while the background sync is still running.
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
            await startup_local_vectors_lock(r).release(trigger)
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
