import datetime
import logging
import time
import uuid
from typing import Dict, Any, Iterable, List, Mapping, Optional, Sequence

from sqlalchemy import func, text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from app.core.orm import AsyncSessionLocal
from app.core.redis import get_redis
from app.models.knowledge import KnowledgeBaseMetric

logger = logging.getLogger(__name__)

DATASET_NAMES_REDIS_KEY = "kb:citation:dataset_names"
DOC_NAMES_REDIS_KEY = "kb:citation:doc_names"
# 文档 → 所属知识库映射：仅用于把「知识库名 / 文件名」拼进 target_name，
# 以便同名文件可区分。不落库为独立列，避免为展示需求引入表结构变更。
DOC_DATASETS_REDIS_KEY = "kb:citation:doc_datasets"
SYNC_LOCK_KEY = "kb:citation:stats_sync_lock"
SYNC_LOCK_TTL_SECONDS = 120
BATCH_UPSERT_CHUNK_SIZE = 500
MAX_RANGE_DAYS = 366


def build_knowledge_metrics_upsert_statement(
    *,
    metric_date,
    target_type: str,
    target_id: str,
    target_name: str,
    search_count: int,
    citation_count: int,
    dialect_name: str,
):
    """Build the incremental knowledge metrics upsert for one SQL dialect."""
    table = KnowledgeBaseMetric.__table__
    values = {
        "metric_date": metric_date,
        "target_type": target_type,
        "target_id": target_id,
        "target_name": target_name,
        "search_count": search_count,
        "citation_count": citation_count,
        "created_at": func.now(),
        "updated_at": func.now(),
    }
    normalized_dialect = dialect_name.strip().lower()
    if normalized_dialect == "postgresql":
        statement = postgresql_insert(table).values(**values)
        return statement.on_conflict_do_update(
            constraint="uix_kb_metric_date_target",
            set_={
                "search_count": table.c.search_count + statement.excluded.search_count,
                "citation_count": table.c.citation_count + statement.excluded.citation_count,
                "target_name": statement.excluded.target_name,
                "updated_at": func.now(),
            },
        )
    if normalized_dialect == "mysql":
        statement = mysql_insert(table).values(**values)
        return statement.on_duplicate_key_update(
            search_count=table.c.search_count + statement.inserted.search_count,
            citation_count=table.c.citation_count + statement.inserted.citation_count,
            target_name=statement.inserted.target_name,
            updated_at=func.now(),
        )
    raise ValueError(f"Unsupported SQL dialect: {dialect_name}")


def build_knowledge_metrics_batch_upsert_statement(*, rows: Sequence[dict], dialect_name: str):
    """Build a multi-row incremental upsert so one round trip persists a whole chunk.

    ``VALUES(col)`` / ``excluded.col`` both resolve to the *current* row inside a
    multi-values insert, so the accumulate semantics of the single-row builder are
    preserved while removing the per-row network round trip.
    """
    table = KnowledgeBaseMetric.__table__
    if not rows:
        raise ValueError("rows must not be empty")
    values = [
        {
            "metric_date": row["metric_date"],
            "target_type": row["target_type"],
            "target_id": row["target_id"],
            "target_name": row.get("target_name"),
            "search_count": row.get("search_count", 0),
            "citation_count": row.get("citation_count", 0),
            "created_at": func.now(),
            "updated_at": func.now(),
        }
        for row in rows
    ]
    normalized_dialect = dialect_name.strip().lower()
    if normalized_dialect == "postgresql":
        statement = postgresql_insert(table).values(values)
        return statement.on_conflict_do_update(
            constraint="uix_kb_metric_date_target",
            set_={
                "search_count": table.c.search_count + statement.excluded.search_count,
                "citation_count": table.c.citation_count + statement.excluded.citation_count,
                "target_name": statement.excluded.target_name,
                "updated_at": func.now(),
            },
        )
    if normalized_dialect == "mysql":
        statement = mysql_insert(table).values(values)
        return statement.on_duplicate_key_update(
            search_count=table.c.search_count + statement.inserted.search_count,
            citation_count=table.c.citation_count + statement.inserted.citation_count,
            target_name=statement.inserted.target_name,
            updated_at=func.now(),
        )
    raise ValueError(f"Unsupported SQL dialect: {dialect_name}")


def _session_dialect_name(session) -> str:
    return session.get_bind().dialect.name


class KnowledgeMetricsService:
    """
    Service to process and persist RAG citation and search metrics.
    """

    @staticmethod
    def _fill_trend_gaps(trend: list, start_date: str, end_date: str) -> list:
        """补全日期范围内无数据的日期，确保前端折线图能连续渲染。"""
        start = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()
        by_date = {item["date"]: item for item in trend}
        filled = []
        current = start
        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            filled.append(by_date.get(date_str, {
                "date": date_str,
                "search_count": 0,
                "citation_count": 0,
            }))
            current += datetime.timedelta(days=1)
        return filled

    @staticmethod
    def _decode_redis_hash(raw_map) -> Dict[str, str]:
        if not raw_map:
            return {}
        return {
            (k.decode("utf-8") if isinstance(k, bytes) else str(k)): (
                v.decode("utf-8") if isinstance(v, bytes) else str(v)
            )
            for k, v in raw_map.items()
        }

    @staticmethod
    def _fallback_dataset_name(dataset_id: str) -> str:
        return f"知识库: {dataset_id[:8]}"

    @staticmethod
    def _previous_period(start_date: str, end_date: str) -> "tuple[str, str]":
        """Return the immediately preceding window of equal length (day granularity)."""
        start = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()
        span = (end - start).days + 1
        prev_end = start - datetime.timedelta(days=1)
        prev_start = prev_end - datetime.timedelta(days=span - 1)
        return prev_start.strftime("%Y-%m-%d"), prev_end.strftime("%Y-%m-%d")

    # ------------------------------------------------------------------
    # 埋点写入（检索量 / 引用量）
    #
    # 此前埋点只写在 KnowledgeAgentRunner 里，即只覆盖「知识库执行器」这条链路；
    # 普通智能体通过 search_knowledge_base 工具查知识库时完全不计数，运营分析对
    # 这类对话永远是 0。口径收敛到这里，两条链路共用同一份实现，避免各写一套漂移。
    # ------------------------------------------------------------------

    @staticmethod
    def _knowledge_hits(citations: Sequence[dict]) -> List[dict]:
        return [
            c for c in citations or []
            if c.get("source_type", "knowledge") == "knowledge"
        ]

    @classmethod
    async def record_search_hits(
        cls,
        citations: Sequence[dict],
        *,
        metric_date: Optional[str] = None,
    ) -> None:
        """记录一次知识库检索的命中量（chunk 级）。

        检索量在拿到切片时即可确定；引用量要等回答生成后才能判定 ``[ID:n]``，
        由 :meth:`record_citation_hits` 负责。

        知识库名称不在这里解析：归并同步（``_sync_under_lock``）会把所有出现过的
        知识库 id 统一解析并落缓存，此处重复解析只会多打一次 RAGFlow。
        """
        hits = cls._knowledge_hits(citations)
        if not hits:
            return
        redis = await get_redis()
        if not redis:
            logger.warning("[KnowledgeMetricsService] Redis unavailable; search metrics skipped.")
            return
        date_str = metric_date or time.strftime("%Y-%m-%d")
        key_prefix = f"kb:citation:stats:{date_str}"
        for c in hits:
            await redis.hincrby(f"{key_prefix}:dataset:search", c.get("dataset_id") or "default", 1)
            doc_id = c.get("doc_id")
            if not doc_id:
                continue
            await redis.hset(DOC_NAMES_REDIS_KEY, doc_id, c.get("doc_name") or "Unknown Document")
            if c.get("dataset_id"):
                await redis.hset(DOC_DATASETS_REDIS_KEY, doc_id, c["dataset_id"])
            await redis.hincrby(f"{key_prefix}:document:search", doc_id, 1)

    @classmethod
    async def record_citation_hits(
        cls,
        citations: Sequence[dict],
        cited_ref_ids: Iterable[str],
        *,
        metric_date: Optional[str] = None,
    ) -> None:
        """（执行器路径）统计回答中真正被引用的知识库来源（chunk 级）。

        ``cited_ref_ids`` 是回答正文里 ``[ID:n]`` 的编号集合，而 n 来自**未经过滤**
        的 citations 顺序（提示词按全量列表编号）。因此这里必须按原始顺序 enumerate，
        绝不能先过滤掉非知识库项再编号，否则所有引用都会整体错位。

        工具路径请改用 :meth:`record_citation_hits_for_refs`：那里的编号由引用台账
        在整轮内全局发放，与列表位置无关。
        """
        pairs = [
            (str(index), c)
            for index, c in enumerate(citations or [], 1)
            if c.get("source_type", "knowledge") == "knowledge"
        ]
        await cls._write_citation_hits(pairs, cited_ref_ids, metric_date=metric_date)

    @classmethod
    async def record_citation_hits_for_refs(
        cls,
        citations_by_ref: Mapping[str, dict],
        cited_ref_ids: Iterable[str],
        *,
        metric_date: Optional[str] = None,
    ) -> None:
        """（工具路径）按台账发放的整轮全局 ``[ID:n]`` 统计引用量。"""
        pairs = [
            (str(ref_id), c)
            for ref_id, c in (citations_by_ref or {}).items()
            if c.get("source_type", "knowledge") == "knowledge"
        ]
        await cls._write_citation_hits(pairs, cited_ref_ids, metric_date=metric_date)

    @classmethod
    async def _write_citation_hits(
        cls,
        pairs: Sequence[tuple],
        cited_ref_ids: Iterable[str],
        *,
        metric_date: Optional[str] = None,
    ) -> None:
        cited = {str(value) for value in cited_ref_ids or ()}
        if not cited:
            return
        hits = [(ref_id, c) for ref_id, c in pairs if ref_id in cited]
        if not hits:
            return
        redis = await get_redis()
        if not redis:
            logger.warning("[KnowledgeMetricsService] Redis unavailable; citation metrics skipped.")
            return
        date_str = metric_date or time.strftime("%Y-%m-%d")
        key_prefix = f"kb:citation:stats:{date_str}"
        for _ref_id, c in hits:
            await redis.hincrby(f"{key_prefix}:dataset:citation", c.get("dataset_id") or "default", 1)
            doc_id = c.get("doc_id")
            if doc_id:
                await redis.hincrby(f"{key_prefix}:document:citation", doc_id, 1)

    @classmethod
    async def resolve_dataset_names(cls, dataset_ids: List[str]) -> Dict[str, str]:
        """解析知识库中文名：Redis 缓存 → 本地元数据表 → RAGFlow 列表。"""
        ids = list({
            str(ds_id).strip()
            for ds_id in dataset_ids
            if ds_id and str(ds_id).strip() not in {"", "default"}
        })
        if not ids:
            return {}

        resolved: Dict[str, str] = {}
        redis = await get_redis()
        if redis:
            cached = cls._decode_redis_hash(await redis.hgetall(DATASET_NAMES_REDIS_KEY))
            for ds_id in ids:
                name = (cached.get(ds_id) or "").strip()
                if name:
                    resolved[ds_id] = name

        missing = [ds_id for ds_id in ids if ds_id not in resolved]
        if missing:
            async with AsyncSessionLocal() as session:
                placeholders = ", ".join(f":id{i}" for i in range(len(missing)))
                params = {f"id{i}": ds_id for i, ds_id in enumerate(missing)}
                sql = (
                    "SELECT ragflow_dataset_id, name FROM knowledge_base_metadata "
                    f"WHERE ragflow_dataset_id IN ({placeholders})"
                )
                res = await session.execute(text(sql), params)
                for row in res.fetchall():
                    name = (row[1] or "").strip()
                    if name:
                        resolved[row[0]] = name

        missing = [ds_id for ds_id in ids if ds_id not in resolved]
        if missing:
            try:
                from app.services.ai.ragflow_client import RagFlowClient

                client = RagFlowClient(config_prefix="knowledge_ragflow")
                ragflow_datasets = await client.list_datasets(page_size=500)
                missing_set = set(missing)
                for ds in ragflow_datasets:
                    ds_id = str(ds.get("id") or "").strip()
                    name = str(ds.get("name") or "").strip()
                    if ds_id in missing_set and name:
                        resolved[ds_id] = name
            except Exception as e:
                logger.warning(f"[KnowledgeMetricsService] RAGFlow dataset name lookup failed: {e}")

        if redis:
            for ds_id, name in resolved.items():
                await redis.hset(DATASET_NAMES_REDIS_KEY, ds_id, name)

        return resolved

    @classmethod
    async def sync_redis_metrics_to_db(cls):
        """
        Sync aggregated search and citation statistics from Redis to DB.

        A short Redis lock serializes concurrent callers (request path and the
        scheduler). Without it two racing syncs read the same Redis counters and
        each applies the accumulate-style upsert, double counting every metric.
        """
        redis = await get_redis()
        if not redis:
            logger.error("[KnowledgeMetricsService] Redis client unavailable; sync skipped.")
            return

        lock_token = uuid.uuid4().hex
        try:
            acquired = await redis.set(
                SYNC_LOCK_KEY,
                lock_token,
                nx=True,
                ex=SYNC_LOCK_TTL_SECONDS,
            )
        except Exception as lock_err:
            logger.error(f"[KnowledgeMetricsService] Failed to acquire sync lock: {lock_err}")
            return
        if not acquired:
            logger.info("[KnowledgeMetricsService] Another sync is in progress; sync skipped.")
            return

        try:
            await cls._sync_under_lock(redis)
        finally:
            try:
                # 仅释放本实例持有的锁，避免误删其他实例刚续上的锁
                current_token = await redis.get(SYNC_LOCK_KEY)
                if isinstance(current_token, bytes):
                    current_token = current_token.decode("utf-8")
                if current_token == lock_token:
                    await redis.delete(SYNC_LOCK_KEY)
            except Exception as release_err:
                logger.warning(f"[KnowledgeMetricsService] Failed to release sync lock: {release_err}")

    @classmethod
    async def _sync_under_lock(cls, redis):
        # SCAN 而非 KEYS：KEYS 在大 key 空间下会阻塞 Redis 主线程
        keys = [
            key
            async for key in redis.scan_iter(match="kb:citation:stats:*", count=500)
        ]
        if not keys:
            logger.info("[KnowledgeMetricsService] No redis metrics keys found for sync.")
            return

        metrics_map = {}

        # Get cached doc/dataset name maps
        doc_names = cls._decode_redis_hash(await redis.hgetall(DOC_NAMES_REDIS_KEY))
        dataset_names = cls._decode_redis_hash(await redis.hgetall(DATASET_NAMES_REDIS_KEY))
        doc_datasets = cls._decode_redis_hash(await redis.hgetall(DOC_DATASETS_REDIS_KEY))

        for key_bytes in keys:
            key = key_bytes.decode("utf-8") if isinstance(key_bytes, bytes) else key_bytes
            parts = key.split(":")
            # Structure: ["kb", "citation", "stats", "YYYY-MM-DD", "target_type", "action"]
            if len(parts) < 6:
                continue

            date_str = parts[3]
            target_type = parts[4]  # dataset or document
            action = parts[5]       # search or citation

            hash_data = await redis.hgetall(key)
            if not hash_data:
                continue

            if date_str not in metrics_map:
                metrics_map[date_str] = {}
            if target_type not in metrics_map[date_str]:
                metrics_map[date_str][target_type] = {}

            for t_id_bytes, val_bytes in hash_data.items():
                t_id = t_id_bytes.decode("utf-8") if isinstance(t_id_bytes, bytes) else t_id_bytes
                val = int(val_bytes) if val_bytes else 0

                if t_id not in metrics_map[date_str][target_type]:
                    metrics_map[date_str][target_type][t_id] = {"search": 0, "citation": 0}

                metrics_map[date_str][target_type][t_id][action] = val

        # 数据集维度的 target_id 与文档归属映射里的 dataset 都要解析出中文名
        dataset_ids_to_resolve = sorted(
            {
                target_id
                for type_map in metrics_map.values()
                for target_type, id_map in type_map.items()
                if target_type == "dataset"
                for target_id in id_map
            }
            | {ds_id for ds_id in doc_datasets.values() if ds_id}
        )
        if dataset_ids_to_resolve:
            resolved_names = await cls.resolve_dataset_names(dataset_ids_to_resolve)
            dataset_names.update(resolved_names)

        # 本地元数据表优先级最高
        async with AsyncSessionLocal() as session:
            try:
                res = await session.execute(text("SELECT ragflow_dataset_id, name FROM knowledge_base_metadata"))
                rows = res.fetchall()
                for r in rows:
                    if r[1]:
                        dataset_names[r[0]] = r[1]
            except Exception as e:
                logger.warning(f"[KnowledgeMetricsService] Failed to prefetch dataset names: {e}")

        # Batch insert or update metrics in DB
        async with AsyncSessionLocal() as session:
            try:
                dialect_name = _session_dialect_name(session)
                pending: List[dict] = []
                for date_str, type_map in metrics_map.items():
                    try:
                        metric_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                    except ValueError:
                        continue

                    for target_type, id_map in type_map.items():
                        for target_id, counts in id_map.items():
                            if target_type == "dataset":
                                target_name = dataset_names.get(target_id) or cls._fallback_dataset_name(target_id)
                            else:
                                # 名称快照带上所属知识库，前端无需额外字段即可区分同名文件
                                doc_name = doc_names.get(target_id) or f"文档: {target_id[:8]}"
                                owner_name = dataset_names.get(doc_datasets.get(target_id) or "")
                                target_name = f"{owner_name} / {doc_name}" if owner_name else doc_name
                            pending.append({
                                "metric_date": metric_date,
                                "target_type": target_type,
                                "target_id": target_id,
                                "target_name": target_name,
                                "search_count": counts.get("search", 0),
                                "citation_count": counts.get("citation", 0),
                            })

                for offset in range(0, len(pending), BATCH_UPSERT_CHUNK_SIZE):
                    chunk = pending[offset:offset + BATCH_UPSERT_CHUNK_SIZE]
                    await session.execute(
                        build_knowledge_metrics_batch_upsert_statement(
                            rows=chunk,
                            dialect_name=dialect_name,
                        )
                    )

                await session.commit()
                logger.info(
                    "[KnowledgeMetricsService] Sync metrics to DB successful (%d rows, %d keys).",
                    len(pending),
                    len(keys),
                )

                # Delete successfully processed keys from Redis
                for key_bytes in keys:
                    await redis.delete(key_bytes)

            except Exception as db_err:
                await session.rollback()
                logger.error(f"[KnowledgeMetricsService] DB metrics sync failed: {db_err}", exc_info=True)

    @classmethod
    async def get_metrics_summary(
        cls,
        start_date: str,
        end_date: str,
        *,
        include_previous: bool = True,
        order_by: str = "citation",
    ) -> Dict[str, Any]:
        """
        Get aggregated operational metrics summary for frontend charts dashboard.

        ``order_by`` switches the ranking basis (``citation`` / ``search``) inside
        SQL, so the top-10 set is genuinely ranked by the chosen metric rather than
        being a client-side re-sort of an already-truncated citation ranking.

        访问控制由接口层统一负责（运营分析仅对管理员开放），本方法不做行级过滤。
        """
        # 白名单映射，杜绝排序字段注入
        normalized_order = "search" if str(order_by).lower() == "search" else "citation"
        order_column = "total_search" if normalized_order == "search" else "total_citation"

        # 1. Dataset citations Top-10
        dataset_sql = f"""
            SELECT target_id, SUM(search_count) as total_search, SUM(citation_count) as total_citation
            FROM knowledge_base_metrics
            WHERE target_type = 'dataset' AND metric_date BETWEEN :start_date AND :end_date
            GROUP BY target_id
            ORDER BY {order_column} DESC, total_search DESC, total_citation DESC
            LIMIT 10
        """

        # 2. Document citations Top-10
        document_sql = f"""
            SELECT target_id, MAX(target_name) as target_name,
                   SUM(search_count) as total_search, SUM(citation_count) as total_citation
            FROM knowledge_base_metrics
            WHERE target_type = 'document' AND metric_date BETWEEN :start_date AND :end_date
            GROUP BY target_id
            ORDER BY {order_column} DESC, total_search DESC, total_citation DESC
            LIMIT 10
        """

        # 3. 按日趋势（仅统计 document 维度，避免与 dataset 重复计数）
        trend_sql = """
            SELECT metric_date, SUM(search_count) as total_search, SUM(citation_count) as total_citation
            FROM knowledge_base_metrics
            WHERE target_type = 'document' AND metric_date BETWEEN :start_date AND :end_date
            GROUP BY metric_date
            ORDER BY metric_date ASC
        """

        # 4. 区间总计 + 活跃文献源（口径与趋势一致，均取 document 维度）
        totals_sql = """
            SELECT COALESCE(SUM(search_count), 0) as total_search,
                   COALESCE(SUM(citation_count), 0) as total_citation,
                   COUNT(DISTINCT CASE WHEN search_count > 0 THEN target_id END) as active_docs
            FROM knowledge_base_metrics
            WHERE target_type = 'document' AND metric_date BETWEEN :start_date AND :end_date
        """

        # 5. 数据新鲜度：让前端能展示「数据截至」
        freshness_sql = """
            SELECT MAX(updated_at) FROM knowledge_base_metrics
            WHERE metric_date BETWEEN :start_date AND :end_date
        """

        try:
            async with AsyncSessionLocal() as session:
                res_ds = await session.execute(
                    text(dataset_sql),
                    {"start_date": start_date, "end_date": end_date},
                )
                dataset_rows = res_ds.fetchall()

                params = {"start_date": start_date, "end_date": end_date}
                res_doc = await session.execute(text(document_sql), params)
                doc_rows = res_doc.fetchall()

                res_trend = await session.execute(text(trend_sql), params)
                trend = [{
                    "date": r[0].strftime("%Y-%m-%d") if isinstance(r[0], datetime.date) else str(r[0]),
                    "search_count": int(r[1]),
                    "citation_count": int(r[2])
                } for r in res_trend.fetchall()]
                trend = cls._fill_trend_gaps(trend, start_date, end_date)

                res_totals = await session.execute(text(totals_sql), params)
                totals_row = res_totals.fetchone()
                totals = {
                    "search_count": int(totals_row[0] or 0),
                    "citation_count": int(totals_row[1] or 0),
                    "active_docs": int(totals_row[2] or 0),
                }

                res_freshness = await session.execute(text(freshness_sql), params)
                last_updated_raw = res_freshness.scalar()
                last_updated = (
                    last_updated_raw.isoformat()
                    if isinstance(last_updated_raw, datetime.datetime)
                    else None
                )

                previous_totals = None
                if include_previous:
                    prev_start, prev_end = cls._previous_period(start_date, end_date)
                    res_prev = await session.execute(text(totals_sql), {
                        "start_date": prev_start,
                        "end_date": prev_end,
                    })
                    prev_row = res_prev.fetchone()
                    previous_totals = {
                        "search_count": int(prev_row[0] or 0),
                        "citation_count": int(prev_row[1] or 0),
                        "active_docs": int(prev_row[2] or 0),
                        "start_date": prev_start,
                        "end_date": prev_end,
                    }

            # 名称解析统一放到查询 session 之外，避免 session 嵌套占用连接
            dataset_name_map = await cls.resolve_dataset_names([r[0] for r in dataset_rows])
            datasets = [{
                "id": r[0],
                "name": dataset_name_map.get(r[0]) or cls._fallback_dataset_name(r[0]),
                "search_count": int(r[1]),
                "citation_count": int(r[2]),
            } for r in dataset_rows]

            documents = [{
                "id": r[0],
                "name": r[1],
                "search_count": int(r[2]),
                "citation_count": int(r[3]),
            } for r in doc_rows]

            return {
                "datasets": datasets,
                "documents": documents,
                "trend": trend,
                "active_docs": totals["active_docs"],
                "totals": totals,
                "previous_totals": previous_totals,
                "last_updated": last_updated,
                "order_by": normalized_order,
                "range": {"start_date": start_date, "end_date": end_date},
            }
        except Exception as e:
            logger.error(f"[KnowledgeMetricsService] Query metrics summary failed: {e}", exc_info=True)
            return {
                "datasets": [],
                "documents": [],
                "trend": [],
                "active_docs": 0,
                "totals": {"search_count": 0, "citation_count": 0, "active_docs": 0},
                "previous_totals": None,
                "last_updated": None,
                "order_by": normalized_order,
                "range": {"start_date": start_date, "end_date": end_date},
            }
