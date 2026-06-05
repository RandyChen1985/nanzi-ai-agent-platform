"""RediSearch-backed session summary index with SCAN fallback."""
import json
import logging
import struct
import time
from typing import Any, Dict, List, Optional

from app.core.redis import get_redis, get_redis_binary
from app.services.ai.embedding_client import EmbeddingClient
from app.services.memory_config_service import MemoryConfigService

logger = logging.getLogger(__name__)

SUMMARY_KEY_PREFIX = "memory:summary:"
# RediSearch 索引名（固定，不可通过 memory_service_configs 修改）
MEMORY_REDIS_INDEX_NAME = "yunshu:idx:memory:session_summary"


def _doc_key(user_id: str, conversation_id: str) -> str:
    return f"{SUMMARY_KEY_PREFIX}{user_id}:{conversation_id}"


def _vector_to_bytes(vec: List[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _bytes_to_vector(blob: bytes) -> List[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


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


class MemoryIndexService:
    @staticmethod
    async def index_name() -> str:
        return MEMORY_REDIS_INDEX_NAME

    @staticmethod
    async def summary_ttl_seconds() -> int:
        days = await MemoryConfigService.get_int("memory_summary_ttl_days", 30)
        return max(1, days) * 86400

    @staticmethod
    async def ensure_index() -> bool:
        redis = await get_redis()
        if not redis:
            return False
        idx = await MemoryIndexService.index_name()
        dim = await EmbeddingClient.get_dimensions()
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
                SUMMARY_KEY_PREFIX,
                "SCHEMA",
                "user_id",
                "TAG",
                "conversation_id",
                "TAG",
                "title",
                "TEXT",
                "summary",
                "TEXT",
                "last_active",
                "NUMERIC",
                "SORTABLE",
                "turn_count",
                "NUMERIC",
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
            logger.info("[MemoryIndex] Created index %s dim=%s", idx, dim)
            return True
        except Exception as e:
            logger.warning("[MemoryIndex] FT.CREATE failed: %s", e)
            return False

    @staticmethod
    async def upsert_summary(
        user_id: str,
        conversation_id: str,
        title: str,
        summary: str,
        turn_count: int,
        embedding: Optional[List[float]] = None,
        key_facts: Optional[List[str]] = None,
        decisions: Optional[List[str]] = None,
        open_items: Optional[List[str]] = None,
        entities: Optional[List[str]] = None,
        memory_type: str = "general",
    ) -> None:
        redis = await get_redis()
        if not redis:
            return
        key = _doc_key(user_id, conversation_id)
        now = int(time.time())
        mapping = {
            "user_id": str(user_id),
            "conversation_id": conversation_id,
            "title": title or conversation_id,
            "summary": summary or "",
            "last_active": str(now),
            "turn_count": str(turn_count),
            "summary_type": "session",
            "memory_type": memory_type or "general",
            "key_facts": json.dumps(key_facts or [], ensure_ascii=False),
            "decisions": json.dumps(decisions or [], ensure_ascii=False),
            "open_items": json.dumps(open_items or [], ensure_ascii=False),
            "entities": json.dumps(entities or [], ensure_ascii=False),
        }
        if embedding:
            mapping["embedding"] = _vector_to_bytes(embedding)
        await redis.hset(key, mapping=mapping)
        try:
            await redis.hsetnx(key, "reference_count", "0")
        except Exception as ex:
            logger.warning("[MemoryIndex] failed to hsetnx reference_count for %s: %s", key, ex)
        await redis.expire(key, await MemoryIndexService.summary_ttl_seconds())
        await MemoryIndexService.ensure_index()

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

    @staticmethod
    async def _hgetall_summary(key: str) -> Dict[Any, Any]:
        """摘要 HASH 含二进制 embedding，须用 decode_responses=False 读取。"""
        redis = await get_redis_binary()
        if not redis:
            return {}
        return await redis.hgetall(key)

    @staticmethod
    async def _parse_hash(data: Dict[Any, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        emb_raw: Any = None
        for k, v in data.items():
            field = MemoryIndexService._hash_field_name(k)
            if field == "embedding":
                emb_raw = v
                continue
            out[field] = MemoryIndexService._hash_text_value(v)

        out["has_embedding"] = bool(
            emb_raw and isinstance(emb_raw, bytes) and len(emb_raw) > 0
        )
        if isinstance(emb_raw, bytes) and emb_raw:
            try:
                out["_embedding_vec"] = _bytes_to_vector(emb_raw)
            except Exception:
                out["_embedding_vec"] = None
        out["last_active"] = int(out.get("last_active") or 0)
        out["turn_count"] = int(out.get("turn_count") or 0)
        try:
            out["reference_count"] = int(out.get("reference_count") or 0)
        except (TypeError, ValueError):
            out["reference_count"] = 0
        return out

    @staticmethod
    async def list_summaries(
        user_id: str,
        keyword: Optional[str] = None,
        conversation_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        redis = await get_redis()
        if not redis:
            return []
        pattern = f"{SUMMARY_KEY_PREFIX}{user_id}:*"
        if conversation_id:
            key = _doc_key(user_id, conversation_id)
            raw = await MemoryIndexService._hgetall_summary(key)
            if not raw:
                return []
            items = [await MemoryIndexService._parse_hash(raw)]
        else:
            items = []
            async for key in redis.scan_iter(match=pattern, count=200):
                key_str = key.decode("utf-8") if isinstance(key, bytes) else key
                raw = await MemoryIndexService._hgetall_summary(key_str)
                if raw:
                    items.append(await MemoryIndexService._parse_hash(raw))

        kw = (keyword or "").strip().lower()
        if kw:
            items = [
                i
                for i in items
                if kw in (i.get("summary") or "").lower()
                or kw in (i.get("title") or "").lower()
                or kw in (i.get("conversation_id") or "").lower()
            ]

        items.sort(key=lambda x: x.get("last_active") or 0, reverse=True)
        return items[:limit]

    @staticmethod
    def _item_day(item: Dict[str, Any]) -> str:
        ts = int(item.get("last_active") or 0)
        if ts <= 0:
            return ""
        return time.strftime("%Y-%m-%d", time.localtime(ts))

    @staticmethod
    async def list_session_summaries_for_day(
        user_id: str,
        day: str,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        items = await MemoryIndexService.list_summaries(user_id, limit=limit)
        return [
            i
            for i in items
            if i.get("summary_type", "session") == "session"
            and MemoryIndexService._item_day(i) == day
        ]

    @staticmethod
    async def count_session_summaries_for_day(user_id: str, day: str) -> int:
        return len(await MemoryIndexService.list_session_summaries_for_day(user_id, day))

    @staticmethod
    async def search_summaries(
        user_id: str,
        query: Optional[str] = None,
        query_embedding: Optional[List[float]] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        if query_embedding:
            try:
                return await MemoryIndexService._search_summaries_knn(
                    user_id, query_embedding, limit
                )
            except Exception as e:
                logger.warning("[MemoryIndex] KNN search failed, falling back to SCAN: %s", e)

        items = await MemoryIndexService.list_summaries(user_id, limit=200)
        if not items:
            return []

        if query_embedding:
            for item in items:
                vec = item.pop("_embedding_vec", None)
                item["score"] = _cosine(query_embedding, vec) if vec else 0.0
            items.sort(key=lambda x: x.get("score", 0), reverse=True)
            return items[:limit]

        if query and query.strip():
            q = query.strip().lower()
            filtered = [
                i
                for i in items
                if q in (i.get("summary") or "").lower() or q in (i.get("title") or "").lower()
            ]
            for i in filtered:
                i.pop("_embedding_vec", None)
            return filtered[:limit]

        for i in items:
            i.pop("_embedding_vec", None)
        return items[:limit]

    @staticmethod
    async def _search_summaries_knn(
        user_id: str,
        query_embedding: List[float],
        limit: int,
    ) -> List[Dict[str, Any]]:
        redis = await get_redis_binary()
        if not redis:
            return []
        idx = await MemoryIndexService.index_name()
        top_k = max(1, int(limit or 5))
        user_tag = _tag_escape(str(user_id))
        raw = await redis.execute_command(
            "FT.SEARCH",
            idx,
            f"(@user_id:{{{user_tag}}})=>[KNN {top_k} @embedding $vec AS score]",
            "PARAMS",
            "2",
            "vec",
            _vector_to_bytes(query_embedding),
            "SORTBY",
            "score",
            "RETURN",
            "15",
            "user_id",
            "conversation_id",
            "title",
            "summary",
            "last_active",
            "turn_count",
            "score",
            "summary_type",
            "memory_type",
            "date",
            "key_facts",
            "decisions",
            "open_items",
            "entities",
            "reference_count",
            "DIALECT",
            "2",
        )
        return MemoryIndexService._parse_knn_response(raw)

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
            row: Dict[str, Any] = {"has_embedding": True}
            for i in range(0, len(fields) - 1, 2):
                name = MemoryIndexService._hash_field_name(fields[i])
                value = MemoryIndexService._hash_text_value(fields[i + 1])
                row[name] = value
            row["last_active"] = int(row.get("last_active") or 0)
            row["turn_count"] = int(row.get("turn_count") or 0)
            if "score" in row:
                try:
                    row["score"] = max(0.0, 1.0 - float(row["score"]))
                except (TypeError, ValueError):
                    row.pop("score", None)
            items.append(row)
            pos += 2
        return items

    @staticmethod
    async def delete_summary(user_id: str, conversation_id: str) -> None:
        redis = await get_redis()
        if not redis:
            return
        await redis.delete(_doc_key(user_id, conversation_id))

    @staticmethod
    async def delete_all_for_user(user_id: str) -> int:
        redis = await get_redis()
        if not redis:
            return 0
        pattern = f"{SUMMARY_KEY_PREFIX}{user_id}:*"
        count = 0
        async for key in redis.scan_iter(match=pattern, count=200):
            await redis.delete(key)
            count += 1
        return count

    @staticmethod
    async def index_status() -> Dict[str, Any]:
        redis = await get_redis()
        idx = await MemoryIndexService.index_name()
        if not redis:
            return {"available": False, "index_name": idx, "message": "Redis 不可用"}
        try:
            info = await redis.execute_command("FT.INFO", idx)
            return {"available": True, "index_name": idx, "info": info}
        except Exception as e:
            return {"available": False, "index_name": idx, "message": str(e)}

    @staticmethod
    async def rebuild_index() -> Dict[str, Any]:
        await MemoryIndexService.ensure_index()
        return {"status": "success", "message": "索引已检查/创建（已有 HASH 文档会自动纳入 PREFIX 索引）"}
