"""记忆向量重构 / 记忆索引检查的任务化编排。

* ``run_memory_index_check()``：秒级的索引存在性与维度检查（必要时重建），
  同步执行并记录成一个任务，便于前端用同一个抽屉回放这几行日志。
* ``start_memory_vector_rebuild()``：先把索引检查/重建做完，再遍历全部
  ``memory:summary:*`` 用当前全局 Embedding 重新向量化，全程推送进度事件。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Set

from app.core import redis
from app.core.config import settings
from app.services.ai.memory_index_service import MemoryIndexService
from app.services.ai.redis_index_utils import last_ensure_result
from app.services.task_lock import MEMORY_VECTORS_LOCK_KEY, memory_vectors_lock
from app.services.task_log_service import task_log_service

logger = logging.getLogger(__name__)

MEMORY_REBUILD_LOCK_KEY = MEMORY_VECTORS_LOCK_KEY
MEMORY_REBUILD_LOCK_TTL_SEC = 1800
MEMORY_REBUILD_SCOPE = "memory_vector_rebuild"
MEMORY_INDEX_SCOPE = "memory_index_check"

_BACKGROUND_TASKS: Set[asyncio.Task] = set()


async def _client():
    r = await redis.get_redis()
    if not r:
        await redis.init_redis()
        r = await redis.get_redis()
    if not r:
        raise RuntimeError("Redis client not available")
    return r


async def get_active_memory_rebuild_task_id() -> Optional[str]:
    """返回当前持有记忆重构锁的任务 ID（无则 None）。"""
    return await memory_vectors_lock().owner()


async def _ensure_index_with_events(task_id: str, *, emit_terminal: bool = True) -> Dict[str, Any]:
    """索引检查/重建并把过程写成事件；返回结构化结果。

    ``emit_terminal=False`` 用于「索引检查」作为重构任务的一个阶段时：此时终态事件
    必须留给整条重构链路收尾，否则前端抽屉读到中途的 completed 就会停止订阅。
    """
    idx = await MemoryIndexService.index_name()

    async def _publish(**kwargs: Any) -> None:
        try:
            await task_log_service.publish(task_id, **kwargs)
        except Exception as e:  # pragma: no cover - 事件失败不阻断主流程
            logger.warning("[memory-rebuild] 发布进度事件失败: %s", e)

    configured_dim: Optional[int] = None
    try:
        from app.services.ai.embedding_client import EmbeddingClient

        configured_dim = await EmbeddingClient.get_dimensions()
    except Exception as e:
        logger.warning("[memory-rebuild] 读取 embed_dimensions 失败: %s", e)

    await _publish(
        event="progress",
        stage="index",
        message=f"检查记忆索引 {idx}（当前配置向量维度 {configured_dim}）",
    )

    ok = await MemoryIndexService.ensure_index(force=True)
    detail = dict(last_ensure_result(idx) or {})
    detail.setdefault("index_name", idx)
    detail["ok"] = ok
    if detail.get("configured_dim") is None:
        detail["configured_dim"] = configured_dim
    result = {
        "ok": ok,
        "action": detail.get("action"),
        "index_name": idx,
        "index_dim": detail.get("index_dim"),
        "configured_dim": detail.get("configured_dim"),
    }
    message = MemoryIndexService.ensure_message(detail)
    result["message"] = message

    if not ok:
        if emit_terminal:
            await _publish(
                event="failed",
                stage="failed",
                message=message,
                progress=100,
                error_detail=message,
            )
        else:
            await _publish(event="progress", stage="index", message=message, error_detail=message)
        return result

    await _publish(event="progress", stage="index", message=message)
    if emit_terminal:
        await _publish(
            event="completed",
            stage="completed",
            message=message,
            progress=100,
            extra={**result, "ok": True},
        )
    return result


async def run_memory_index_check() -> Dict[str, Any]:
    """同步执行「检查/创建索引」并记录为一个任务。"""
    record = await task_log_service.create_task(MEMORY_INDEX_SCOPE)
    detail = await _ensure_index_with_events(record.task_id)
    return {"task_id": record.task_id, **detail}


async def _run_memory_vector_rebuild(task_id: str, r) -> None:
    """执行一次记忆向量重构：全程持有锁（自动续期），结束时释放。"""
    async with memory_vectors_lock(r).hold(task_id):
        await _run_memory_vector_rebuild_locked(task_id, r)


async def _run_memory_vector_rebuild_locked(task_id: str, r) -> None:
    failed_items: List[Dict[str, str]] = []
    done = 0
    total = 0
    stats: Dict[str, int] = {"total": 0, "success": 0, "failed": 0, "skipped": 0}

    async def _publish(**kwargs: Any) -> None:
        try:
            await task_log_service.publish(task_id, **kwargs)
        except Exception as e:
            logger.warning("[memory-rebuild] 发布进度事件失败: %s", e)

    def _progress() -> int:
        if total <= 0:
            return 0
        return max(0, min(100, int(done * 100 / total)))

    try:
        await _publish(
            event="started",
            stage="index",
            message="已获取重构锁，开始重构记忆向量（会先检查并重建索引）",
            progress=0,
        )

        # --- 索引检查/重建（终态留给整条重构链路）---
        index_result = await _ensure_index_with_events(task_id, emit_terminal=False)
        if not index_result.get("ok"):
            await _publish(
                event="failed",
                stage="failed",
                message=f"索引检查/创建失败，终止记忆向量重构：{index_result.get('message')}",
                progress=100,
                error_detail=str(index_result.get("message")),
            )
            return

        # --- 全量重新向量化 ---
        async def _on_progress(payload: Dict[str, Any]) -> None:
            nonlocal done, total, stats
            phase = payload.get("phase")
            if phase == "scan":
                total = int(payload.get("total") or 0)
                await _publish(
                    event="progress",
                    stage="scan",
                    message=f"共发现 {total} 条记忆记录，开始逐条重新向量化",
                    progress=0,
                    done=0,
                    total=total,
                )
                return

            if phase == "item_done":
                stats["success"] += 1
                done = int(payload.get("done") or done + 1)
                await _publish(
                    event="progress",
                    stage="reembed",
                    message=(
                        f"记忆[{payload.get('summary_type')}] {payload.get('conversation_id')} "
                        f"重新向量化完成（维度 {payload.get('dim')}，"
                        f"{payload.get('elapsed_ms', 0)} ms）"
                    ),
                    progress=_progress(),
                    done=done,
                    total=total,
                )
            elif phase == "item_failed":
                stats["failed"] += 1
                done = int(payload.get("done") or done + 1)
                name = f"记忆[{payload.get('summary_type') or '-'}] {payload.get('conversation_id') or payload.get('key')}"
                error = str(payload.get("error") or "重新向量化失败")
                failed_items.append({"name": name, "error": error})
                await _publish(
                    event="progress",
                    stage="reembed",
                    message=f"{name} 重新向量化失败：{error}",
                    progress=_progress(),
                    done=done,
                    total=total,
                    error_detail=error,
                )
            elif phase == "item_skipped":
                stats["skipped"] += 1
                done = int(payload.get("done") or done + 1)
                await _publish(
                    event="progress",
                    stage="reembed",
                    message=(
                        f"记忆[{payload.get('summary_type') or '-'}] "
                        f"{payload.get('conversation_id') or payload.get('key')} 跳过："
                        f"{payload.get('reason') or '无需重新向量化'}"
                    ),
                    progress=_progress(),
                    done=done,
                    total=total,
                )

        result = await MemoryIndexService.reembed_all(progress_cb=_on_progress)
        stats.update({k: int(v) for k, v in (result or {}).items()})
        total = stats["total"] or total
        done = max(done, stats["success"] + stats["failed"] + stats["skipped"])

        summary = (
            f"记忆向量重构完成：成功 {stats['success']} 条，失败 {stats['failed']} 条"
        )
        if stats["skipped"]:
            summary += f"，跳过 {stats['skipped']} 条"

        terminal = "completed"
        if stats["success"] == 0 and stats["failed"] > 0:
            terminal = "failed"

        await _publish(
            event=terminal,
            stage=terminal,
            message=summary,
            progress=100,
            done=done,
            total=total or done,
            error_detail=(f"{stats['failed']} 条失败，详见日志" if stats["failed"] else None),
            failed_items=failed_items or None,
            extra=dict(stats),
        )
    except Exception as e:
        logger.error("[memory-rebuild] 记忆向量重构异常终止: %s", e, exc_info=True)
        await _publish(
            event="failed",
            stage="failed",
            message=f"记忆向量重构异常终止：{e}",
            progress=100,
            done=done,
            total=total or done,
            error_detail=str(e),
            failed_items=failed_items or None,
        )


async def start_memory_vector_rebuild(*, trigger: str = "manual") -> Optional[str]:
    """启动一次任务化的记忆向量重构，返回 task_id；已有任务在跑则返回 None。"""
    if not settings.REDIS_ENABLE:
        raise RuntimeError("Redis is disabled")

    r = await _client()
    lock = memory_vectors_lock(r)
    task_id = task_log_service.new_task_id()
    try:
        acquired = await lock.acquire(task_id)
    except Exception as e:
        raise RuntimeError(f"无法获取记忆重构锁：{e}") from e
    if not acquired:
        return None

    # 抢到锁之后的任何失败都必须把锁还回去，否则用户会被自己的残留锁挡住 30 分钟。
    try:
        await task_log_service.create_task(
            MEMORY_REBUILD_SCOPE,
            task_id=task_id,
            meta={"trigger": trigger},
        )
        task = asyncio.create_task(_run_memory_vector_rebuild(task_id, r))
    except BaseException:
        await lock.release(task_id)
        raise
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task_id
