"""Orchestrates session summary merge after chat turns."""
import json
import logging
import time
from typing import Any, Dict, List, Optional

from app.core.redis import get_redis
from app.services.ai.conversation_summarizer import ConversationSummarizer
from app.services.ai.daily_summary_service import DailySummaryService
from app.services.ai.embedding_client import EmbeddingClient
from app.services.ai.memory_index_service import MemoryIndexService
from app.services.ai.memory_service import memory_service
from app.services.memory_config_service import MemoryConfigService

logger = logging.getLogger(__name__)

DEBOUNCE_KEY_PREFIX = "memory:debounce:"


class SessionSummaryService:
    @staticmethod
    def _embedding_text(meta: Dict[str, Any]) -> str:
        parts = [
            meta.get("title") or "",
            meta.get("summary") or "",
            " ".join(str(v) for v in meta.get("key_facts") or []),
            " ".join(str(v) for v in meta.get("decisions") or []),
            " ".join(str(v) for v in meta.get("open_items") or []),
            " ".join(str(v) for v in meta.get("entities") or []),
            str(meta.get("memory_type") or ""),
        ]
        return "\n".join(p for p in parts if p)

    @staticmethod
    async def is_enabled() -> bool:
        if not await MemoryConfigService.get_bool("memory_service_enabled", True):
            return False
        return await MemoryConfigService.get_bool("memory_summary_enabled", True)

    @staticmethod
    def _debounce_key(user_id: str, conversation_id: str) -> str:
        return f"{DEBOUNCE_KEY_PREFIX}{user_id}:{conversation_id}"

    @staticmethod
    async def should_run(user_id: str, conversation_id: str, assistant_content: str) -> bool:
        min_chars = await MemoryConfigService.get_int("memory_summarize_min_assistant_chars", 30)
        if len((assistant_content or "").strip()) < min_chars:
            return False

        redis = await get_redis()
        if not redis:
            return True

        debounce_seconds = await MemoryConfigService.get_int("memory_summarize_debounce_seconds", 300)
        debounce_turns = await MemoryConfigService.get_int("memory_summarize_debounce_turns", 3)

        key = SessionSummaryService._debounce_key(user_id, conversation_id)
        raw = await redis.get(key)
        state = {"last_run": 0, "pending_turns": 0}
        if raw:
            try:
                state = json.loads(raw)
            except json.JSONDecodeError:
                pass

        state["pending_turns"] = int(state.get("pending_turns", 0)) + 1
        now = time.time()
        last_run = float(state.get("last_run") or 0)

        if state["pending_turns"] < debounce_turns and (now - last_run) < debounce_seconds:
            await redis.set(key, json.dumps(state), ex=debounce_seconds * 2)
            return False

        state["last_run"] = now
        state["pending_turns"] = 0
        await redis.set(key, json.dumps(state), ex=debounce_seconds * 2)
        return True

    @staticmethod
    async def finalize_session(user_id: str, conversation_id: str) -> Dict[str, Any]:
        """Force summary flush when switching or closing a conversation (bypass debounce)."""
        if not await SessionSummaryService.is_enabled():
            return {"finalized": False, "reason": "service_disabled"}

        uid = str(user_id)
        cid = (conversation_id or "").strip()
        if not cid:
            return {"finalized": False, "reason": "missing_conversation_id"}

        history = await memory_service.get_history(uid, cid, limit=40)
        if not history:
            return {"finalized": False, "reason": "empty_history"}

        await SessionSummaryService.merge_session_summary(
            uid, cid, assistant_content=" ", force=True
        )
        return {"finalized": True, "conversation_id": cid}

    @staticmethod
    async def merge_session_summary(
        user_id: str,
        conversation_id: str,
        assistant_content: str = "",
        force: bool = False,
    ) -> None:
        if not await SessionSummaryService.is_enabled():
            return
        uid = str(user_id)
        if not force and not await SessionSummaryService.should_run(
            uid, conversation_id, assistant_content
        ):
            return

        try:
            history = await memory_service.get_history(uid, conversation_id, limit=40)
            meta = await ConversationSummarizer.summarize(history)
            title = meta.get("title") or "会话摘要"
            summary = meta.get("summary") or ""
            turn_count = max(1, len([m for m in history if m.get("role") == "user"]))

            embedding = None
            try:
                embed_text = SessionSummaryService._embedding_text(meta)
                embedding = await EmbeddingClient.embed_text(embed_text)
            except Exception as e:
                logger.warning("[SessionSummary] Embedding failed: %s", e)

            await MemoryIndexService.upsert_summary(
                user_id=uid,
                conversation_id=conversation_id,
                title=title,
                summary=summary,
                turn_count=turn_count,
                embedding=embedding,
                key_facts=meta.get("key_facts") or [],
                decisions=meta.get("decisions") or [],
                open_items=meta.get("open_items") or [],
                entities=meta.get("entities") or [],
                memory_type=meta.get("memory_type") or "general",
            )
            await DailySummaryService.refresh_for_date(uid)

            max_sessions = await MemoryConfigService.get_int("memory_summary_max_sessions", 50)
            all_items = await MemoryIndexService.list_summaries(uid, limit=max_sessions + 10)
            if len(all_items) > max_sessions:
                for old in all_items[max_sessions:]:
                    await MemoryIndexService.delete_summary(uid, old["conversation_id"])

            logger.info("[SessionSummary] Updated summary for user=%s conv=%s", uid, conversation_id)
        except Exception as e:
            logger.error("[SessionSummary] merge failed: %s", e, exc_info=True)

    @staticmethod
    async def search_for_user(
        user_id: str,
        query: Optional[str] = None,
        scope: str = "summary",
        conversation_id: Optional[str] = None,
        limit: int = 5,
    ) -> Dict[str, Any]:
        uid = str(user_id)
        result: Dict[str, Any] = {"summaries": [], "history": []}

        query_embedding = None
        if query and query.strip():
            try:
                query_embedding = await EmbeddingClient.embed_text(query.strip())
            except Exception as e:
                logger.warning("[SessionSummary] query embed failed: %s", e)

        if scope in ("summary", "both"):
            result["summaries"] = await MemoryIndexService.search_summaries(
                uid, query=query, query_embedding=query_embedding, limit=limit
            )
            # Increment reference_count for each recalled summary
            redis = await get_redis()
            if redis and result["summaries"]:
                for s in result["summaries"]:
                    cid = s.get("conversation_id")
                    if cid:
                        key = f"memory:summary:{uid}:{cid}"
                        try:
                            # Increment reference_count in Redis
                            await redis.hincrby(key, "reference_count", 1)
                            # Update reference_count value in the current in-memory object
                            s["reference_count"] = int(s.get("reference_count") or 0) + 1
                        except Exception as ex:
                            logger.warning("[SessionSummary] failed to incr reference_count for key %s: %s", key, ex)

        if scope in ("history", "both"):
            cid = conversation_id
            if not cid and result["summaries"]:
                cid = result["summaries"][0].get("conversation_id")
            if cid:
                hist = await memory_service.get_history(uid, cid, limit=limit * 4)
                result["history"] = hist
                result["conversation_id"] = cid

        return result
