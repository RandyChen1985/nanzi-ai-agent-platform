"""RediSearch-backed local vector search service for ChatBI examples."""
import logging
import struct
import json
from typing import Any, Dict, List, Optional
from app.core.redis import get_redis, get_redis_binary
from app.services.ai.embedding_client import EmbeddingClient

logger = logging.getLogger(__name__)

EXAMPLE_INDEX_NAME = "yunshu:idx:example:local"
EXAMPLE_KEY_PREFIX = "yunshu:example:"

def _vector_to_bytes(vec: List[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)

def _tag_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace(".", "\\.")
        .replace("<", "\\<")
        .replace(">", "\\>")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace('"', '\\"')
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace(";", "\\;")
        .replace("!", "\\!")
        .replace("@", "\\@")
        .replace("#", "\\#")
        .replace("$", "\\$")
        .replace("%", "\\%")
        .replace("^", "\\^")
        .replace("&", "\\&")
        .replace("*", "\\*")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("-", "\\-")
        .replace("+", "\\+")
        .replace("=", "\\=")
        .replace("~", "\\~")
        .replace("|", "\\|")
        .replace(" ", "\\ ")
    )

class ExampleIndexService:
    @staticmethod
    async def index_name() -> str:
        return EXAMPLE_INDEX_NAME

    @staticmethod
    async def ensure_index() -> bool:
        redis = await get_redis()
        if not redis:
            return False
        idx = await ExampleIndexService.index_name()
        dim = await EmbeddingClient.get_dimensions(use_global=True)
        try:
            info = await redis.execute_command("FT.INFO", idx)
            if info:
                return True
        except Exception:
            pass
        try:
            await redis.execute_command(
                "FT.CREATE",
                idx,
                "ON",
                "HASH",
                "PREFIX",
                "1",
                EXAMPLE_KEY_PREFIX,
                "SCHEMA",
                "id",
                "TAG",
                "dataset_id",
                "TAG",
                "dataset_name",
                "TEXT",
                "question",
                "TEXT",
                "raw_query",
                "TEXT",
                "context_summary",
                "TEXT",
                "sql",
                "TEXT",
                "trace_id",
                "TAG",
                "agent_id",
                "TAG",
                "sql_metadata",
                "TEXT",
                "embedding",
                "VECTOR",
                "HNSW",
                "6",
                "TYPE",
                "FLOAT32",
                "DIM",
                str(dim),
                "DISTANCE_METRIC",
                "COSINE",
            )
            logger.info("[ExampleIndex] Created index %s dim=%s", idx, dim)
            return True
        except Exception as e:
            logger.warning("[ExampleIndex] FT.CREATE failed: %s", e)
            return False

    @staticmethod
    async def upsert_vector(
        example_id: int,
        dataset_id: int,
        dataset_name: str,
        question: str,
        raw_query: str,
        context_summary: str,
        sql_text: str,
        trace_id: str,
        agent_id: str,
        sql_metadata: Optional[Dict[str, Any]],
        embedding: List[float]
    ) -> None:
        """
        Upsert a ChatBI example with embedding into Redis HASH.
        """
        redis = await get_redis()
        if not redis:
            return
        
        key = f"{EXAMPLE_KEY_PREFIX}{example_id}"
        
        metadata_str = ""
        if sql_metadata:
            try:
                metadata_str = json.dumps(sql_metadata, ensure_ascii=False)
            except Exception as je:
                logger.warning("[ExampleIndex] Failed to serialize sql_metadata: %s", je)

        mapping = {
            "id": str(example_id),
            "dataset_id": str(dataset_id),
            "dataset_name": dataset_name or "",
            "question": question or "",
            "raw_query": raw_query or "",
            "context_summary": context_summary or "",
            "sql": sql_text or "",
            "trace_id": trace_id or "",
            "agent_id": agent_id or "",
            "sql_metadata": metadata_str,
            "embedding": _vector_to_bytes(embedding)
        }
        await redis.hset(key, mapping=mapping)
        await ExampleIndexService.ensure_index()

    @staticmethod
    async def delete_vector(example_id: int) -> None:
        """
        Delete a single example from local Redis.
        """
        redis = await get_redis()
        if not redis:
            return
        key = f"{EXAMPLE_KEY_PREFIX}{example_id}"
        await redis.delete(key)
        logger.info("[ExampleIndex] Deleted local Redis example vector for key %s", key)

    @staticmethod
    async def sync_all_examples() -> None:
        """
        Synchronize all approved ChatBI examples to local Redis.
        """
        from app.core.orm import AsyncSessionLocal
        from app.models.chatbi_example import ChatBIExample
        from app.models.metadata import MetaDataset
        from sqlalchemy import select
        import asyncio

        async def _run_sync():
            try:
                async with AsyncSessionLocal() as db:
                    # 1. 查询所有 approved 状态的案例
                    stmt = select(ChatBIExample).where(ChatBIExample.status == "approved")
                    res = await db.execute(stmt)
                    examples = res.scalars().all()
                    
                    logger.info("[ExampleIndex Sync] Found %d approved examples for local sync", len(examples))
                    
                    if not examples:
                        return
                        
                    await ExampleIndexService.ensure_index()

                    # 2. 批量查出对应的数据集名称，减少循环中的数据库查询
                    dataset_ids = list(set([ex.dataset_id for ex in examples if ex.dataset_id]))
                    dataset_name_map = {}
                    if dataset_ids:
                        stmt_ds = select(MetaDataset.id, MetaDataset.display_name).where(MetaDataset.id.in_(dataset_ids))
                        res_ds = await db.execute(stmt_ds)
                        for row in res_ds.all():
                            dataset_name_map[row[0]] = row[1]

                    # 3. 同步到 Redis
                    for ex in examples:
                        try:
                            # 如果没有增强的问题，退回到原问题进行向量化
                            text_to_embed = ex.refined_query or ex.user_query
                            if not text_to_embed:
                                continue
                            
                            embedding = await EmbeddingClient.embed_text(text_to_embed, use_global=True)
                            ds_name = dataset_name_map.get(ex.dataset_id) or "通用数据集"
                            
                            await ExampleIndexService.upsert_vector(
                                example_id=ex.id,
                                dataset_id=ex.dataset_id or 0,
                                dataset_name=ds_name,
                                question=ex.refined_query or ex.user_query,
                                raw_query=ex.user_query,
                                context_summary=ex.context_summary or "",
                                sql_text=ex.sql_text,
                                trace_id=ex.trace_id or "",
                                agent_id=ex.agent_id or "",
                                sql_metadata=ex.sql_metadata,
                                embedding=embedding
                            )
                        except Exception as ex_err:
                            logger.error("[ExampleIndex Sync] Sync failed for example %d: %s", ex.id, ex_err)
                            
                    logger.info("[ExampleIndex Sync] Local Redis example vectors sync complete.")
            except Exception as e:
                logger.error("[ExampleIndex Sync] Sync background task failed: %s", e, exc_info=True)

        try:
            asyncio.create_task(_run_sync())
        except Exception as e:
            logger.warning("[ExampleIndex Sync] Failed to trigger background sync: %s", e)

    @staticmethod
    async def search_knn(
        query_embedding: List[float],
        authorized_dataset_ids: Optional[List[int]],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Execute HNSW KNN search for examples with authorized_dataset_ids TAG pre-filtering.
        If authorized_dataset_ids is None, we perform search without dataset TAG filter.
        """
        redis = await get_redis_binary()
        if not redis:
            return []
        
        idx = await ExampleIndexService.index_name()
        await ExampleIndexService.ensure_index()

        if authorized_dataset_ids is None:
            query_expr = f"*=>[KNN {top_k} @embedding $vec AS score]"
        else:
            if not authorized_dataset_ids:
                return []
            tag_vals = "|".join(_tag_escape(str(ds_id)) for ds_id in authorized_dataset_ids)
            query_expr = f"(@dataset_id:{{{tag_vals}}})=>[KNN {top_k} @embedding $vec AS score]"

        raw = await redis.execute_command(
            "FT.SEARCH",
            idx,
            query_expr,
            "PARAMS",
            "2",
            "vec",
            _vector_to_bytes(query_embedding),
            "SORTBY",
            "score",
            "RETURN",
            "11",
            "id",
            "dataset_id",
            "dataset_name",
            "question",
            "raw_query",
            "context_summary",
            "sql",
            "trace_id",
            "agent_id",
            "sql_metadata",
            "score",
            "DIALECT",
            "2",
        )
        return ExampleIndexService._parse_knn_response(raw)

    @staticmethod
    def _parse_knn_response(raw: Any) -> List[Dict[str, Any]]:
        if not raw or not isinstance(raw, (list, tuple)) or len(raw) < 2:
            return []
        
        items: List[Dict[str, Any]] = []
        pos = 1
        while pos + 1 < len(raw):
            fields = raw[pos + 1]
            if not isinstance(fields, (list, tuple)):
                pos += 2
                continue
            
            row: Dict[str, Any] = {}
            for i in range(0, len(fields) - 1, 2):
                name = ExampleIndexService._hash_field_name(fields[i])
                value = ExampleIndexService._hash_text_value(fields[i + 1])
                row[name] = value
            
            # 解析并且重组为对齐 RAGFlow 输出的结构
            sql_meta_dict = None
            if row.get("sql_metadata"):
                try:
                    sql_meta_dict = json.loads(row["sql_metadata"])
                except Exception:
                    pass

            similarity = 0.0
            if "score" in row:
                try:
                    similarity = max(0.0, 1.0 - float(row["score"]))
                except (TypeError, ValueError):
                    pass

            item = {
                "id": int(row["id"]) if row.get("id") else None,
                "question": row.get("question") or row.get("raw_query"),
                "sql": row.get("sql") or "",
                "context_summary": row.get("context_summary") or "",
                "dataset_name": row.get("dataset_name") or "通用数据集",
                "trace_id": row.get("trace_id") or "",
                "sql_metadata": sql_meta_dict,
                "similarity": similarity
            }
            items.append(item)
            pos += 2
            
        return items

    @staticmethod
    def _hash_field_name(key: Any) -> str:
        if isinstance(key, bytes):
            return key.decode("utf-8", errors="replace")
        return str(key)

    @staticmethod
    def _hash_text_value(value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value) if value is not None else ""
