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
MEMORY_REDIS_INDEX_NAME = "nanzi:idx:memory:session_summary"


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
            from app.services.memory_config_service import MemoryConfigService
            base_half_life = await MemoryConfigService.get_float("memory_base_half_life", 7.0)
            MemoryIndexService._apply_ebbinghaus_decay(items, base_half_life)
            for item in items:
                item.pop("_embedding_vec", None)
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
        items = MemoryIndexService._parse_knn_response(raw)
        
        # 引入艾宾浩斯时间衰减重排
        from app.services.memory_config_service import MemoryConfigService
        base_half_life = await MemoryConfigService.get_float("memory_base_half_life", 7.0)
        return MemoryIndexService._apply_ebbinghaus_decay(items, base_half_life)

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
    def _apply_ebbinghaus_decay(items: List[Dict[str, Any]], base_half_life: float) -> List[Dict[str, Any]]:
        import math
        import time
        now = time.time()
        for item in items:
            last_active = float(item.get("last_active") or 0)
            if last_active <= 0:
                item["final_score"] = float(item.get("score") or 0.0)
                continue
            
            # 时间差（天数）
            t = (now - last_active) / 86400.0
            if t < 0:
                t = 0.0
                
            # 引用频次
            try:
                ref_count = int(item.get("reference_count") or 0)
            except (TypeError, ValueError):
                ref_count = 0
                
            # 记忆强度对数放大
            S = base_half_life * (1.0 + math.log1p(ref_count))
            
            # 艾宾浩斯记忆保留度计算
            R = math.exp(-t / S)
            
            # 结合原有相似度分数得出综合评分
            item["final_score"] = float(item.get("score") or 0.0) * R
            
        # 根据 final_score 降序排序
        items.sort(key=lambda x: x.get("final_score", 0.0), reverse=True)
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
    async def consolidate_user_memories(user_id: str) -> None:
        """
        根据余弦相似度对用户的记忆进行聚类降噪，并调用大模型合并相似记忆碎片。
        """
        import math
        import time
        import uuid
        import re
        import asyncio
        from app.services.memory_config_service import MemoryConfigService
        from app.core.llm.client import get_llm_async
        from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
        from app.services.ai.runtime.agentscope.messages import RuntimeContentBlock, RuntimeMessage
        from app.services.ai.conversation_summarizer import ConversationSummarizer

        # 1. 加载参数与配置
        threshold = await MemoryConfigService.get_float("memory_consolidation_threshold", 0.82)
        redis = await get_redis()
        if not redis:
            return

        # 2. 拉取用户所有的记忆（最多 500 条）
        items = await MemoryIndexService.list_summaries(user_id, limit=500)
        # 过滤出有 Embedding 向量且 summary_type 为 session 的记忆进行归并
        valid_items = [
            i for i in items
            if i.get("has_embedding") and i.get("_embedding_vec") and i.get("summary_type") == "session"
        ]

        n = len(valid_items)
        if n < 2:
            return

        # 3. 强连通分量 (Connected Components) 聚类
        # 构建邻接表
        adj = {i: [] for i in range(n)}
        for i in range(n):
            vec_i = valid_items[i]["_embedding_vec"]
            for j in range(i + 1, n):
                vec_j = valid_items[j]["_embedding_vec"]
                # 计算余弦距离
                sim = _cosine(vec_i, vec_j)
                if sim >= threshold:
                    adj[i].append(j)
                    adj[j].append(i)

        # 深度优先搜索（DFS）寻找所有的连通分量
        visited = set()
        groups = []
        for i in range(n):
            if i not in visited:
                group = []
                stack = [i]
                visited.add(i)
                while stack:
                    curr = stack.pop()
                    group.append(curr)
                    for neighbor in adj[curr]:
                        if neighbor not in visited:
                            visited.add(neighbor)
                            stack.append(neighbor)
                if len(group) >= 2:
                    groups.append(group)

        if not groups:
            logger.info("[MemoryConsolidation] User %s has no memories to consolidate.", user_id)
            return

        # 4. 初始化 LLM 客户端
        llm = await get_llm_async(streaming=False, temperature=0.1)
        if not llm:
            logger.warning("[MemoryConsolidation] Failed to get LLM async handle.")
            return
        chat_client = chat_client_from_handle(llm)

        CONSOLIDATION_SYSTEM_PROMPT = (
            "你是记忆整理与降噪助手。你的任务是将同一位用户下多条高度相似或重复的记忆片段合并为一条。"
            "在合并时，必须精简啰嗦的表述，但必须保留核心事实（例如具体的人名、数据库名、物理表名、指标数值及偏好细节）。"
            "输出合并后的记忆，内容必须在 50 字以内。确保是一句通顺的陈述句。"
        )

        # 5. 循环处理每一个聚类组
        for idx, group_indices in enumerate(groups):
            group_items = [valid_items[g_idx] for g_idx in group_indices]

            # 拼接输入
            fragment_lines = []
            total_reference_count = 0
            for it in group_items:
                summary_text = (it.get("summary") or "").strip()
                if summary_text:
                    fragment_lines.append(f"- {summary_text}")
                total_reference_count += int(it.get("reference_count") or 0)

            if not fragment_lines:
                continue

            fragments_prompt = "\n".join(fragment_lines)
            user_content = f"请合并以下相似记忆碎片：\n\n{fragments_prompt}"

            try:
                # 调用 LLM 进行合并
                raw_reply = await ConversationSummarizer._generate_with_retry(
                    chat_client,
                    [
                        RuntimeMessage(
                            role="system",
                            content=[RuntimeContentBlock(type="text", text=CONSOLIDATION_SYSTEM_PROMPT)],
                        ),
                        RuntimeMessage(
                            role="user",
                            content=[RuntimeContentBlock(type="text", text=user_content)],
                        ),
                    ],
                    max_retries=3
                )

                merged_summary = raw_reply.strip()
                # 去除可能的 Markdown 包裹
                merged_summary = re.sub(r"^`{1,3}(markdown)?|`{1,3}$", "", merged_summary).strip()
                # 再次截断字数保证安全性
                merged_summary = merged_summary[:150]

                if not merged_summary:
                    continue

                # 生成合并记忆的 Embedding 向量
                from app.services.ai.embedding_client import EmbeddingClient
                merged_embedding = await EmbeddingClient.embed_text(merged_summary)

                # 写入合并记忆
                new_conv_id = f"consolidated_{uuid.uuid4().hex}"
                await MemoryIndexService.upsert_summary(
                    user_id=user_id,
                    conversation_id=new_conv_id,
                    title="记忆合并归纳",
                    summary=merged_summary,
                    turn_count=1,
                    embedding=merged_embedding,
                    memory_type="general"
                )

                # 继承引用次数并设置为 consolidated 类型的记忆
                new_key = _doc_key(user_id, new_conv_id)
                await redis.hset(new_key, "reference_count", str(total_reference_count))
                await redis.hset(new_key, "summary_type", "consolidated")

                # 物理删除原来的旧记忆碎片
                for it in group_items:
                    old_conv_id = it.get("conversation_id")
                    if old_conv_id and old_conv_id != new_conv_id:
                        await MemoryIndexService.delete_summary(user_id, old_conv_id)

                logger.info(
                    "[MemoryConsolidation] Consolidated %s memories into 1 for user %s. New Key: %s",
                    len(group_items), user_id, new_key
                )

                # 平抑 LLM 请求，睡眠 500ms
                await asyncio.sleep(0.5)

            except Exception as ex:
                logger.error(
                    "[MemoryConsolidation] Failed to consolidate group %s for user %s: %s",
                    idx, user_id, ex
                )

    @staticmethod
    async def index_status() -> Dict[str, Any]:
        redis = await get_redis()
        idx = await MemoryIndexService.index_name()
        if not redis:
            return {"available": False, "index_name": idx, "message": "Redis 不可用"}
        try:
            info = await redis.execute_command("FT.INFO", idx)
            
            # 清理嵌套结构中可能存在的非标准浮点数（如 NaN, Infinity），以防止 JSON 序列化失败
            import math
            def _sanitize(obj: Any) -> Any:
                if isinstance(obj, float):
                    if math.isnan(obj) or math.isinf(obj):
                        return None
                    return obj
                elif isinstance(obj, dict):
                    return {k: _sanitize(v) for k, v in obj.items()}
                elif isinstance(obj, (list, tuple)):
                    return [_sanitize(x) for x in obj]
                return obj

            return {"available": True, "index_name": idx, "info": _sanitize(info)}
        except Exception as e:
            return {"available": False, "index_name": idx, "message": str(e)}

    @staticmethod
    async def rebuild_index() -> Dict[str, Any]:
        await MemoryIndexService.ensure_index()
        return {"status": "success", "message": "索引已检查/创建（已有 HASH 文档会自动纳入 PREFIX 索引）"}
