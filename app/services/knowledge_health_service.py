"""知识库健康巡检：一屏看清哪些库失联、是空的、解析失败堆着。

为什么落快照而不是实时算：实时算要对每个库调一次文档列表，
50 个库就是一串慢请求，而且每次打开页面都要重算。按天落表后打开即读，
历史趋势也顺带有了。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import delete, func, select

from app.models.knowledge import KnowledgeBaseMetadata, KnowledgeHealthSnapshot
from app.services.ai.ragflow_client import RagFlowClient

logger = logging.getLogger(__name__)

# RAGFlow 的解析状态：UNSTART/0、DONE/1、RUNNING/2、FAIL/3
_RUN_DONE = {1, "1", "DONE"}
_RUN_RUNNING = {2, "2", "RUNNING"}
_RUN_FAIL = {3, "3", "FAIL"}


def summarize_documents(documents: Iterable[Any]) -> Dict[str, int]:
    """把一批文档的解析状态归成四档。

    认不出状态的算「尚未解析」而不是「失败」——把未知当失败会让
    治理视图长期挂着一堆假告警，最后没人看。
    """
    stats = {"documents": 0, "parsed": 0, "parsing": 0, "failed": 0, "unparsed": 0, "chunks": 0}
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        stats["documents"] += 1
        try:
            stats["chunks"] += int(doc.get("chunk_count") or doc.get("chunk_num") or 0)
        except (TypeError, ValueError):
            pass

        run = doc.get("run")
        if run in _RUN_DONE:
            stats["parsed"] += 1
        elif run in _RUN_RUNNING:
            stats["parsing"] += 1
        elif run in _RUN_FAIL:
            stats["failed"] += 1
        else:
            stats["unparsed"] += 1
    return stats


def summarize_snapshots(rows: Iterable[KnowledgeHealthSnapshot]) -> dict:
    """把当天快照汇成治理摘要。"""
    items = list(rows)
    missing = [row for row in items if row.is_missing]
    empty = [row for row in items if not row.is_missing and (row.documents or 0) == 0]
    failing = [row for row in items if (row.failed or 0) > 0]
    stale = [row for row in items if (row.stale_days or 0) >= 90]
    return {
        "total": len(items),
        "missing": len(missing),
        "empty": len(empty),
        "failing": len(failing),
        "stale": len(stale),
        "missing_items": [
            {"dataset_id": row.dataset_id, "name": row.name} for row in missing
        ][:20],
        "failing_items": [
            {"dataset_id": row.dataset_id, "name": row.name, "failed": row.failed}
            for row in failing
        ][:20],
    }


class KnowledgeHealthService:
    @classmethod
    async def scan(cls, db, *, concurrency: int = 4) -> dict:
        """巡检全部 active 库并写入当天快照（重复执行是覆盖，不会堆重复行）。"""
        rows = (
            (
                await db.execute(
                    select(KnowledgeBaseMetadata).where(KnowledgeBaseMetadata.status != "deleted")
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return {"date": date.today().isoformat(), "scanned": 0, **summarize_snapshots([])}

        client = RagFlowClient(config_prefix="knowledge_ragflow")
        try:
            engine_items = await client.list_all_datasets_cached(refresh=True)
            engine_ids = {str(item.get("id")) for item in engine_items if item.get("id")}
        except Exception as exc:
            logger.warning("[Knowledge] 巡检无法读取引擎侧库列表，全部按失联处理: %s", exc)
            engine_ids = set()

        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def inspect(row: KnowledgeBaseMetadata) -> Dict[str, Any]:
            dataset_id = row.ragflow_dataset_id
            is_missing = dataset_id not in engine_ids
            stats = {"documents": 0, "parsed": 0, "parsing": 0, "failed": 0, "unparsed": 0, "chunks": 0}
            if not is_missing:
                async with semaphore:
                    try:
                        docs = await client.list_documents(dataset_id, page_size=100)
                        stats = summarize_documents(docs)
                    except Exception as exc:
                        logger.debug("[Knowledge] 巡检读取 %s 文档失败: %s", dataset_id, exc)

            updated = row.updated_at or row.created_at
            stale_days = (datetime.now() - updated).days if updated else None
            return {
                "dataset_id": dataset_id,
                "name": row.name,
                "is_missing": is_missing,
                "visibility": row.visibility,
                "owner_user_id": row.owner_user_id,
                "stale_days": stale_days,
                **stats,
            }

        inspected = await asyncio.gather(*(inspect(row) for row in rows))

        today = date.today()
        # 同一天重复跑要覆盖：先删当天旧快照，否则唯一键冲突或数字翻倍
        await db.execute(
            delete(KnowledgeHealthSnapshot).where(KnowledgeHealthSnapshot.snapshot_date == today)
        )
        snapshots: List[KnowledgeHealthSnapshot] = []
        for data in inspected:
            snapshot = KnowledgeHealthSnapshot(snapshot_date=today, **data)
            db.add(snapshot)
            snapshots.append(snapshot)
        await db.commit()

        return {
            "date": today.isoformat(),
            "scanned": len(snapshots),
            **summarize_snapshots(snapshots),
        }

    @classmethod
    async def latest(cls, db) -> dict:
        """最近一次巡检的结果（没有快照时返回空摘要，不报错）。"""
        latest_date = (
            await db.execute(select(func.max(KnowledgeHealthSnapshot.snapshot_date)))
        ).scalar()
        if not latest_date:
            return {
                "date": None,
                "total": 0,
                "missing": 0,
                "empty": 0,
                "failing": 0,
                "stale": 0,
                "missing_items": [],
                "failing_items": [],
                "items": [],
            }

        rows = (
            (
                await db.execute(
                    select(KnowledgeHealthSnapshot)
                    .where(KnowledgeHealthSnapshot.snapshot_date == latest_date)
                    .order_by(
                        KnowledgeHealthSnapshot.is_missing.desc(),
                        KnowledgeHealthSnapshot.failed.desc(),
                        KnowledgeHealthSnapshot.documents.asc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        return {
            "date": latest_date.isoformat() if hasattr(latest_date, "isoformat") else str(latest_date),
            **summarize_snapshots(rows),
            "items": [
                {
                    "dataset_id": row.dataset_id,
                    "name": row.name,
                    "documents": row.documents,
                    "parsed": row.parsed,
                    "parsing": row.parsing,
                    "failed": row.failed,
                    "unparsed": row.unparsed,
                    "chunks": row.chunks,
                    "is_missing": row.is_missing,
                    "visibility": row.visibility,
                    "stale_days": row.stale_days,
                }
                for row in rows
            ],
        }
