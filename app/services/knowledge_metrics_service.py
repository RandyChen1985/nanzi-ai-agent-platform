import datetime
import logging
from typing import Dict, Any

from sqlalchemy import text
from app.core.orm import AsyncSessionLocal
from app.core.redis import get_redis

logger = logging.getLogger(__name__)


class KnowledgeMetricsService:
    """
    Service to process and persist RAG citation and search metrics.
    """

    @classmethod
    async def sync_redis_metrics_to_db(cls):
        """
        Sync aggregated search and citation statistics from Redis to DB.
        """
        redis = await get_redis()
        if not redis:
            logger.error("[KnowledgeMetricsService] Redis client unavailable; sync skipped.")
            return

        # Scan for keys matching pattern kb:citation:stats:YYYY-MM-DD:*
        keys = await redis.keys("kb:citation:stats:*")
        if not keys:
            logger.info("[KnowledgeMetricsService] No redis metrics keys found for sync.")
            return

        metrics_map = {}
        
        # Get cached doc_id -> doc_name map
        doc_names_map = await redis.hgetall("kb:citation:doc_names") or {}
        doc_names = {
            k.decode("utf-8") if isinstance(k, bytes) else k: v.decode("utf-8") if isinstance(v, bytes) else v 
            for k, v in doc_names_map.items()
        }

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

        # Fetch active dataset names snapshot from local DB metadata
        dataset_names = {}
        async with AsyncSessionLocal() as session:
            try:
                res = await session.execute(text("SELECT ragflow_dataset_id, name FROM knowledge_base_metadata"))
                rows = res.fetchall()
                for r in rows:
                    dataset_names[r[0]] = r[1]
            except Exception as e:
                logger.warning(f"[KnowledgeMetricsService] Failed to prefetch dataset names: {e}")

        # Batch insert or update metrics in DB
        async with AsyncSessionLocal() as session:
            try:
                for date_str, type_map in metrics_map.items():
                    try:
                        metric_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                    except ValueError:
                        continue
                        
                    for target_type, id_map in type_map.items():
                        for target_id, counts in id_map.items():
                            search_count = counts.get("search", 0)
                            citation_count = counts.get("citation", 0)
                            
                            target_name = None
                            if target_type == "dataset":
                                target_name = dataset_names.get(target_id) or f"知识库: {target_id[:8]}"
                            else:
                                target_name = doc_names.get(target_id) or f"文档: {target_id[:8]}"

                            # MySQL INSERT ... ON DUPLICATE KEY UPDATE for clean upsert
                            sql = """
                                INSERT INTO knowledge_base_metrics 
                                (metric_date, target_type, target_id, target_name, search_count, citation_count, created_at, updated_at)
                                VALUES 
                                (:metric_date, :target_type, :target_id, :target_name, :search_count, :citation_count, NOW(), NOW())
                                ON DUPLICATE KEY UPDATE 
                                search_count = search_count + :search_count,
                                citation_count = citation_count + :citation_count,
                                target_name = VALUES(target_name),
                                updated_at = NOW()
                            """
                            await session.execute(text(sql), {
                                "metric_date": metric_date,
                                "target_type": target_type,
                                "target_id": target_id,
                                "target_name": target_name,
                                "search_count": search_count,
                                "citation_count": citation_count
                            })
                
                await session.commit()
                logger.info("[KnowledgeMetricsService] Sync metrics to DB successful.")
                
                # Delete successfully processed keys from Redis
                for key_bytes in keys:
                    await redis.delete(key_bytes)
                
            except Exception as db_err:
                await session.rollback()
                logger.error(f"[KnowledgeMetricsService] DB metrics sync failed: {db_err}", exc_info=True)

    @classmethod
    async def get_metrics_summary(cls, start_date: str, end_date: str) -> Dict[str, Any]:
        """
        Get aggregated operational metrics summary for frontend charts dashboard.
        """
        async with AsyncSessionLocal() as session:
            # 1. Dataset citations Top-10
            dataset_sql = """
                SELECT target_id, target_name, SUM(search_count) as total_search, SUM(citation_count) as total_citation
                FROM knowledge_base_metrics
                WHERE target_type = 'dataset' AND metric_date BETWEEN :start_date AND :end_date
                GROUP BY target_id, target_name
                ORDER BY total_citation DESC, total_search DESC
                LIMIT 10
            """
            
            # 2. Document citations Top-10
            document_sql = """
                SELECT target_id, target_name, SUM(search_count) as total_search, SUM(citation_count) as total_citation
                FROM knowledge_base_metrics
                WHERE target_type = 'document' AND metric_date BETWEEN :start_date AND :end_date
                GROUP BY target_id, target_name
                ORDER BY total_citation DESC, total_search DESC
                LIMIT 10
            """
            
            # 3. Aggregated daily trends line
            trend_sql = """
                SELECT metric_date, SUM(search_count) as total_search, SUM(citation_count) as total_citation
                FROM knowledge_base_metrics
                WHERE metric_date BETWEEN :start_date AND :end_date
                GROUP BY metric_date
                ORDER BY metric_date ASC
            """
            
            try:
                res_ds = await session.execute(text(dataset_sql), {"start_date": start_date, "end_date": end_date})
                datasets = [{
                    "id": r[0], "name": r[1], "search_count": int(r[2]), "citation_count": int(r[3])
                } for r in res_ds.fetchall()]
                
                res_doc = await session.execute(text(document_sql), {"start_date": start_date, "end_date": end_date})
                documents = [{
                    "id": r[0], "name": r[1], "search_count": int(r[2]), "citation_count": int(r[3])
                } for r in res_doc.fetchall()]
                
                res_trend = await session.execute(text(trend_sql), {"start_date": start_date, "end_date": end_date})
                trend = [{
                    "date": r[0].strftime("%Y-%m-%d") if isinstance(r[0], datetime.date) else str(r[0]),
                    "search_count": int(r[1]),
                    "citation_count": int(r[2])
                } for r in res_trend.fetchall()]
                
                return {
                    "datasets": datasets,
                    "documents": documents,
                    "trend": trend
                }
            except Exception as e:
                logger.error(f"[KnowledgeMetricsService] Query metrics summary failed: {e}", exc_info=True)
                return {"datasets": [], "documents": [], "trend": []}
