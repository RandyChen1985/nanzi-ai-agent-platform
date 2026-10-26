"""知识库检索日志与效果指标。

要回答的问题是「知识库到底有没有用」——尤其是**哪些问题搜不到**。
只统计命中数会掩盖这一点：命中 1 条和零结果在平均值里差别不大，
但对使用者来说一个是"勉强能用"，一个是"答非所问"。

写入失败一律静默：检索是主流程，日志写不进去不能反过来让检索报错。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from sqlalchemy import func, select

from app.core.orm import AsyncSessionLocal
from app.models.knowledge import KnowledgeRetrievalLog

logger = logging.getLogger(__name__)

SOURCE_TEST = "test"
SOURCE_AGENT = "agent"


def _normalize_query(query: Any) -> str:
    return str(query or "").strip()[:512]


class KnowledgeRetrievalLogService:
    @classmethod
    async def log(
        cls,
        *,
        query: str,
        dataset_ids: Optional[Iterable[Any]] = None,
        hit_count: int = 0,
        top_score: Optional[float] = None,
        latency_ms: Optional[int] = None,
        source: str = SOURCE_TEST,
        user: Optional[dict] = None,
    ) -> bool:
        text = _normalize_query(query)
        if not text:
            return False
        ids = [str(item).strip() for item in (dataset_ids or []) if str(item or "").strip()]
        try:
            async with AsyncSessionLocal() as session:
                session.add(
                    KnowledgeRetrievalLog(
                        query=text,
                        dataset_ids=ids or None,
                        hit_count=max(0, int(hit_count or 0)),
                        top_score=float(top_score) if top_score is not None else None,
                        latency_ms=int(latency_ms) if latency_ms is not None else None,
                        source=source if source in (SOURCE_TEST, SOURCE_AGENT) else SOURCE_TEST,
                        user_id=int(user["user_id"]) if user and user.get("user_id") else None,
                        user_name=(user or {}).get("user_name"),
                    )
                )
                await session.commit()
            return True
        except Exception as exc:  # pragma: no cover - 依赖真实数据库
            logger.warning("[Knowledge] 检索日志写入失败（不影响检索本身）: %s", exc)
            return False

    @classmethod
    async def stats(cls, db, *, days: int = 7, source: Optional[str] = None) -> dict:
        """近 N 天的检索效果：量、零结果率、平均命中、P95 延迟、搜不到的 query。"""
        days = max(1, min(90, int(days or 7)))
        since = datetime.now() - timedelta(days=days)

        conditions = [KnowledgeRetrievalLog.created_at >= since]
        if source in (SOURCE_TEST, SOURCE_AGENT):
            conditions.append(KnowledgeRetrievalLog.source == source)

        total = (
            await db.execute(
                select(func.count()).select_from(KnowledgeRetrievalLog).where(*conditions)
            )
        ).scalar() or 0

        zero = (
            await db.execute(
                select(func.count())
                .select_from(KnowledgeRetrievalLog)
                .where(*conditions, KnowledgeRetrievalLog.hit_count == 0)
            )
        ).scalar() or 0

        avg_hits = (
            await db.execute(
                select(func.avg(KnowledgeRetrievalLog.hit_count)).where(*conditions)
            )
        ).scalar()

        avg_latency = (
            await db.execute(
                select(func.avg(KnowledgeRetrievalLog.latency_ms)).where(*conditions)
            )
        ).scalar()

        # P95：按延迟降序跳过前 5%，取第一条。MySQL 没有 percentile_cont，
        # 这样两条查询在不同方言上都成立。
        p95_latency = None
        if total:
            offset = int(total * 0.05)
            p95_latency = (
                await db.execute(
                    select(KnowledgeRetrievalLog.latency_ms)
                    .where(*conditions, KnowledgeRetrievalLog.latency_ms.isnot(None))
                    .order_by(KnowledgeRetrievalLog.latency_ms.desc())
                    .offset(offset)
                    .limit(1)
                )
            ).scalar()

        top_zero_rows = (
            await db.execute(
                select(KnowledgeRetrievalLog.query, func.count().label("hits"))
                .where(*conditions, KnowledgeRetrievalLog.hit_count == 0)
                .group_by(KnowledgeRetrievalLog.query)
                .order_by(func.count().desc())
                .limit(10)
            )
        ).all()

        return {
            "days": days,
            "total_queries": int(total),
            "zero_result_count": int(zero),
            # 零结果率保留 4 位：7 天只有几十次查询时，1% 的差别也有意义
            "zero_result_rate": round(float(zero) / float(total), 4) if total else 0.0,
            "avg_hits": round(float(avg_hits), 2) if avg_hits is not None else 0.0,
            "avg_latency_ms": round(float(avg_latency), 1) if avg_latency is not None else None,
            "p95_latency_ms": int(p95_latency) if p95_latency is not None else None,
            "top_zero_queries": [
                {"query": row[0], "count": int(row[1])} for row in top_zero_rows
            ],
        }
