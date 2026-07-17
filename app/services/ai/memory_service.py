import json
import logging
from typing import List, Dict, Any, Optional
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

class MemoryService:
    """
    Manages conversation history in Redis.
    Uses Redis LIST to store JSON-serialized messages.
    """
    
    # Key pattern: conversation:{user_id}:{conversation_id}:history
    KEY_PREFIX = "conversation"
    HISTORY_SUFFIX = "history"
    DATA_RESULT_SUFFIX = "last_data_result"
    
    def __init__(self, max_history_turns: int = 50, ttl: int = 604800):
        """
        :param max_history_turns: Maximum number of dialogue turns (user + assistant) to keep.
        :param ttl: Time-to-live for the conversation in seconds (default 7 days).
        """
        self.max_history_len = max_history_turns * 2
        self.ttl = ttl

    def _get_key(self, user_id: str, conversation_id: str) -> str:
        """
        Generate Redis Key.
        Format: conversation:{user_id}:{conversation_id}:history
        """
        # Ensure user_id is string and handle potential None (fallback to 'anonymous' or error?)
        # For security, we should probably fail if user_id is missing, but for backward compat 
        # with loose validation systems, we safeguard str conversion.
        uid = str(user_id) if user_id else "anonymous" 
        return f"{self.KEY_PREFIX}:{uid}:{conversation_id}:{self.HISTORY_SUFFIX}"

    def _get_data_result_key(self, user_id: str, conversation_id: str) -> str:
        uid = str(user_id) if user_id else "anonymous"
        return f"{self.KEY_PREFIX}:{uid}:{conversation_id}:{self.DATA_RESULT_SUFFIX}"

    async def get_history(self, user_id: str, conversation_id: str, limit: Optional[int] = None, offset: int = 0) -> List[Dict[str, str]]:
        """
        Retrieve history from Redis with pagination support.
        offset=0 means the most recent messages.
        """
        redis = await get_redis()
        if not redis:
            return []
            
        key = self._get_key(user_id, conversation_id)
        # Fetch all messages first (Redis operations are fast enough for <1000 items)
        data = await redis.lrange(key, 0, -1)
        logger.info(f"[MemoryService] Fetching history for key: {key}. Total items: {len(data)}, Limit: {limit}, Offset: {offset}")
        
        history = []
        for item in data:
            try:
                history.append(json.loads(item))
            except Exception as e:
                logger.error(f"Failed to parse history item: {item}. Error: {e}")
        
        # Apply Max Retention Logic first (ensure we only work with the valid window)
        if len(history) > self.max_history_len:
             history = history[-self.max_history_len:]

        # Apply Pagination (Reverse Slicing)
        # Scenario: Total 50. limit=10, offset=0 -> get last 10 (40-50)
        # Scenario: Total 50. limit=10, offset=10 -> get prev 10 (30-40)
        if limit:
            start_idx = len(history) - (offset + limit)
            end_idx = len(history) - offset
            
            # Boundary Checks
            if end_idx > len(history): end_idx = len(history)
            if start_idx < 0: start_idx = 0
            if end_idx <= 0: return [] # Offset too large
            
            if start_idx >= end_idx:
                return []
                
            history = history[start_idx:end_idx]

        return history

    async def add_message(self, user_id: str, conversation_id: str, role: str, content: str, trace_id: Optional[str] = None, files: Optional[List[Dict[str, Any]]] = None, agent_name: Optional[str] = None, prompt_tokens: Optional[int] = 0, completion_tokens: Optional[int] = 0, has_data_output: Optional[bool] = None):
        """
        Append a single message to the conversation history.
        Now supports trace_id, attachment files, and token usage values.

        agent_name: 处理该轮的智能体 name(slug)。仅对 assistant 消息记录，
        用于后续路由的会话粘性（让追问沿用上一轮智能体）。
        """
        redis = await get_redis()
        if not redis:
            logger.warning("[MemoryService] Redis client not available for add_message")
            return
            
        from datetime import datetime
        key = self._get_key(user_id, conversation_id)
        # 扩展消息体，包含 trace_id 和 files
        message = {
            "role": role, 
            "content": content,
            "trace_id": trace_id,
            "timestamp": datetime.now().isoformat()
        }
        if files:
            message["files"] = files
        if agent_name:
            message["agent_name"] = agent_name
        message["prompt_tokens"] = int(prompt_tokens or 0)
        message["completion_tokens"] = int(completion_tokens or 0)
        if has_data_output:
            message["has_data_output"] = True

        
        # Push to list
        try:
            val = json.dumps(message, ensure_ascii=False)
            async with redis.pipeline() as pipe:
                await pipe.rpush(key, val)
                await pipe.ltrim(key, -self.max_history_len, -1)
                await pipe.expire(key, self.ttl)
                await pipe.execute()
            logger.info(f"[MemoryService] Added message to key: {key}. TraceID: {trace_id}")
        except Exception as e:
            logger.error(f"[MemoryService] Failed to add message to key {key}: {e}")

    async def get_last_data_result(self, user_id: str, conversation_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve the latest structured SQL result for follow-up analysis/chart requests.
        """
        redis = await get_redis()
        if not redis:
            return None

        key = self._get_data_result_key(user_id, conversation_id)
        try:
            raw = await redis.get(key)
            if not raw:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except Exception as e:
            logger.error(f"[MemoryService] Failed to get last data result from key {key}: {e}")
            return None

    async def set_last_data_result(self, user_id: str, conversation_id: str, payload: Dict[str, Any]):
        """
        Store the latest structured SQL result so follow-up turns can reuse it without re-querying.
        """
        redis = await get_redis()
        if not redis:
            logger.warning("[MemoryService] Redis client not available for set_last_data_result")
            return

        key = self._get_data_result_key(user_id, conversation_id)
        try:
            await redis.set(key, json.dumps(payload, ensure_ascii=False), ex=self.ttl)
            logger.info(f"[MemoryService] Stored last data result for key: {key}")
        except Exception as e:
            logger.error(f"[MemoryService] Failed to set last data result for key {key}: {e}")

    async def clear_history(self, user_id: str, conversation_id: str):
        """
        Delete a conversation history.
        """
        redis = await get_redis()
        if not redis:
            return
        key = self._get_key(user_id, conversation_id)
        logger.info(f"[MemoryService] Clearing history for key: {key}")
        await redis.delete(key)
        await redis.delete(self._get_data_result_key(user_id, conversation_id))

    async def delete_session_memory(
        self,
        user_id: str,
        conversation_id: str,
        include_summary: bool = True,
        user_name: str | None = None,
        user_info: dict | None = None,
    ):
        """Delete LIST history and optionally summary index doc."""
        await self.clear_history(user_id, conversation_id)
        from app.services.ai.runtime.agentscope.state_store import agent_state_store
        from app.services.ai.runtime.agentscope.workspace import delete_workspace_for_session

        await agent_state_store.delete(user_id, conversation_id)
        await delete_workspace_for_session(
            user_id,
            conversation_id,
            user_name=user_name,
            user_info=user_info,
        )
        if include_summary:
            from app.services.ai.memory_index_service import MemoryIndexService
            await MemoryIndexService.delete_summary(str(user_id), conversation_id)

    async def history_exists(self, user_id: str, conversation_id: str) -> bool:
        redis = await get_redis()
        if not redis:
            return False
        return bool(await redis.exists(self._get_key(user_id, conversation_id)))

    async def get_active_conversation(self, user_id: str) -> Optional[str]:
        redis = await get_redis()
        if not redis:
            return None
        uid = str(user_id) if user_id else "anonymous"
        key = f"conversation:{uid}:active"
        try:
            val = await redis.get(key)
            if isinstance(val, bytes):
                return val.decode("utf-8")
            return val
        except Exception as e:
            logger.error(f"[MemoryService] Failed to get active conversation: {e}")
            return None

    async def set_active_conversation(self, user_id: str, conversation_id: str):
        redis = await get_redis()
        if not redis:
            return
        uid = str(user_id) if user_id else "anonymous"
        key = f"conversation:{uid}:active"
        try:
            await redis.set(key, conversation_id)
            logger.info(f"[MemoryService] Set active conversation for key {key} to {conversation_id}")
        except Exception as e:
            logger.error(f"[MemoryService] Failed to set active conversation: {e}")


memory_service = MemoryService()


class LongTermMemoryService:
    """
    Manages Long-Term Memory (LTM) in Redis.
    Uses Redis HASH to store user preferences, core facts, and profiles.
    Key pattern: nanzi:agent:ltm:{user_id}
    """
    
    KEY_PREFIX = "nanzi:agent:ltm"

    def _get_key(self, user_id: str) -> str:
        uid = str(user_id) if user_id else "anonymous"
        return f"{self.KEY_PREFIX}:{uid}"

    async def update_preference(self, user_id: str, key: str, value: str) -> bool:
        """
        Store or update a specific long-term preference/fact for a user.
        """
        redis = await get_redis()
        if not redis:
            logger.warning("[LTM] Redis client not available for update_preference")
            return False
            
        redis_key = self._get_key(user_id)
        try:
            await redis.hset(redis_key, key, value)
            logger.info(f"[LTM] Updated key '{key}' for user '{user_id}' in Redis.")
            return True
        except Exception as e:
            logger.error(f"[LTM] Failed to update preference for key {key}: {e}")
            return False

    async def fetch_memory(self, user_id: str) -> Dict[str, str]:
        """
        Retrieve all long-term preferences and facts for a user.
        """
        redis = await get_redis()
        if not redis:
            logger.warning("[LTM] Redis client not available for fetch_memory")
            return {}
            
        redis_key = self._get_key(user_id)
        try:
            data = await redis.hgetall(redis_key)
            if not data:
                return {}
            result = {}
            for k, v in data.items():
                k_str = k.decode("utf-8") if isinstance(k, bytes) else str(k)
                v_str = v.decode("utf-8") if isinstance(v, bytes) else str(v)
                result[k_str] = v_str
            return result
        except Exception as e:
            logger.error(f"[LTM] Failed to fetch memory for user {user_id}: {e}")
            return {}

    async def delete_preference(self, user_id: str, key: str) -> bool:
        """
        Delete a specific long-term preference/fact for a user.
        """
        redis = await get_redis()
        if not redis:
            logger.warning("[LTM] Redis client not available for delete_preference")
            return False

        redis_key = self._get_key(user_id)
        try:
            await redis.hdel(redis_key, key)
            logger.info(f"[LTM] Deleted key '{key}' for user '{user_id}' in Redis.")
            return True
        except Exception as e:
            logger.error(f"[LTM] Failed to delete preference for key {key}: {e}")
            return False


ltm_service = LongTermMemoryService()

