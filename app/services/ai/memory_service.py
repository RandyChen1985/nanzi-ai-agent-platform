import json
import logging
from typing import List, Dict, Any, Optional
from urllib.parse import quote

from redis.exceptions import WatchError

from app.core.redis import get_redis
from app.services.ai.conversation_identity import require_user_id

logger = logging.getLogger(__name__)

_PERSIST_REUSABLE_RESULT_SCRIPT = """
local stack_key = KEYS[2]
local existing = {}
local raw = redis.call('GET', stack_key)
if raw then
    local ok, decoded = pcall(cjson.decode, raw)
    if ok and type(decoded) == 'table' then
        existing = decoded
    end
end

local result_id = ARGV[4]
local next_stack = {}
for _, item in ipairs(existing) do
    if type(item) == 'table' and
       (result_id == '' or tostring(item['result_id'] or '') ~= result_id) then
        table.insert(next_stack, item)
    end
end

table.insert(next_stack, cjson.decode(ARGV[1]))
local max_depth = math.max(1, tonumber(ARGV[3]) or 10)
while #next_stack > max_depth do
    table.remove(next_stack, 1)
end

redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
redis.call('SET', stack_key, cjson.encode(next_stack), 'EX', ARGV[2])
return 1
"""

_PERSIST_DATA_RESULT_STACK_SCRIPT = """
local stack_key = KEYS[1]
local existing = {}
local raw = redis.call('GET', stack_key)
if raw then
    local ok, decoded = pcall(cjson.decode, raw)
    if ok and type(decoded) == 'table' then
        existing = decoded
    end
end

local result_id = ARGV[4]
local next_stack = {}
for _, item in ipairs(existing) do
    if type(item) == 'table' and
       (result_id == '' or tostring(item['result_id'] or '') ~= result_id) then
        table.insert(next_stack, item)
    end
end

table.insert(next_stack, cjson.decode(ARGV[1]))
local max_depth = math.max(1, tonumber(ARGV[3]) or 10)
while #next_stack > max_depth do
    table.remove(next_stack, 1)
end

redis.call('SET', stack_key, cjson.encode(next_stack), 'EX', ARGV[2])
return 1
"""

class MemoryService:
    """
    Manages conversation history in Redis.
    Uses Redis LIST to store JSON-serialized messages.
    """
    
    # Key pattern: conversation:{user_id}:{conversation_id}:history
    KEY_PREFIX = "conversation"
    HISTORY_SUFFIX = "history"
    DATA_RESULT_SUFFIX = "last_data_result"
    DATA_RESULT_STACK_SUFFIX = "data_result_stack_v1"
    SESSION_TOOL_ARTIFACT_SUFFIX = "session_tool_artifact_v1"
    REUSABLE_RESULT_SUFFIX = "reusable_result_v1"
    CONTEXT_SNAPSHOT_SUFFIX = "context_snapshot_v1"
    
    def __init__(self, max_history_turns: int = 50, ttl: int = 2592000):
        """
        :param max_history_turns: Maximum number of dialogue turns (user + assistant) to keep.
        :param ttl: Time-to-live for the conversation in seconds (default 30 days).
        """
        self.max_history_len = max_history_turns * 2
        self.ttl = ttl

    def _get_key(self, user_id: str, conversation_id: str) -> str:
        """
        Generate Redis Key.
        Format: conversation:{user_id}:{conversation_id}:history
        """
        uid = require_user_id(user_id)
        return f"{self.KEY_PREFIX}:{uid}:{conversation_id}:{self.HISTORY_SUFFIX}"

    def _get_data_result_key(self, user_id: str, conversation_id: str) -> str:
        uid = require_user_id(user_id)
        return f"{self.KEY_PREFIX}:{uid}:{conversation_id}:{self.DATA_RESULT_SUFFIX}"

    def _get_data_result_stack_key(self, user_id: str, conversation_id: str) -> str:
        uid = require_user_id(user_id)
        return f"{self.KEY_PREFIX}:{uid}:{conversation_id}:{self.DATA_RESULT_STACK_SUFFIX}"

    def _get_session_tool_artifact_key(self, user_id: str, conversation_id: str) -> str:
        uid = require_user_id(user_id)
        return f"{self.KEY_PREFIX}:{uid}:{conversation_id}:{self.SESSION_TOOL_ARTIFACT_SUFFIX}"

    def _get_reusable_result_key(self, user_id: str, conversation_id: str) -> str:
        uid = require_user_id(user_id)
        return f"{self.KEY_PREFIX}:{uid}:{conversation_id}:{self.REUSABLE_RESULT_SUFFIX}:current"

    def _get_reusable_result_stack_key(self, user_id: str, conversation_id: str) -> str:
        uid = require_user_id(user_id)
        return f"{self.KEY_PREFIX}:{uid}:{conversation_id}:{self.REUSABLE_RESULT_SUFFIX}:stack"

    def _get_digest_key(self, user_id: str, conversation_id: str) -> str:
        """跨轮溢出摘录（digest）的独立 Redis key。

        与 history（LIST）分开存储，保证不进入 get_conversation_history 的展示路径。
        仅保存本轮压缩得到的 system 摘录文本。
        """
        uid = require_user_id(user_id)
        return f"{self.KEY_PREFIX}:{uid}:{conversation_id}:digest"

    def _get_context_snapshot_key(self, user_id: str, conversation_id: str) -> str:
        uid = require_user_id(user_id)
        return f"{self.KEY_PREFIX}:{uid}:{conversation_id}:{self.CONTEXT_SNAPSHOT_SUFFIX}"

    async def get_context_snapshot(self, user_id: str, conversation_id: str) -> Optional[Dict[str, Any]]:
        try:
            redis = await get_redis()
            if not redis:
                return None
            raw = await redis.get(self._get_context_snapshot_key(user_id, conversation_id))
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            value = json.loads(raw) if raw else None
            return value if isinstance(value, dict) else None
        except Exception as exc:
            logger.warning("[MemoryService] Failed to get context snapshot: %s", exc)
            return None

    async def set_context_snapshot(
        self,
        user_id: str,
        conversation_id: str,
        snapshot: Dict[str, Any],
    ) -> bool:
        redis = await get_redis()
        if not redis or not isinstance(snapshot, dict):
            return False
        try:
            await redis.set(
                self._get_context_snapshot_key(user_id, conversation_id),
                json.dumps(snapshot, ensure_ascii=False),
                ex=self.ttl,
            )
            return True
        except Exception as exc:
            logger.warning("[MemoryService] Failed to set context snapshot: %s", exc)
            return False

    def _get_digest_meta_key(
        self,
        user_id: str,
        conversation_id: str,
        field: str,
    ) -> str:
        uid = require_user_id(user_id)
        return f"{self.KEY_PREFIX}:{uid}:{conversation_id}:digest_{field}"

    def _get_seq_counter_key(self, user_id: str, conversation_id: str) -> str:
        """会话内消息单调序号计数器的 Redis key。

        该计数器与 history LIST 的索引解耦：即使 `ltrim` 压缩了列表索引，
        新追加消息的 seq 仍保持严格单调递增，供摘要游标（synced_seq）判定增量窗口，
        且编辑重发（截断后重发）时新消息 seq 更大，能使摘要基于当前分支重算。
        """
        uid = require_user_id(user_id)
        return f"{self.KEY_PREFIX}:{uid}:{conversation_id}:seq_counter"

    def _get_context_revision_key(self, user_id: str, conversation_id: str) -> str:
        """Return the branch revision used to invalidate in-flight digest tasks."""
        uid = require_user_id(user_id)
        return f"{self.KEY_PREFIX}:{uid}:{conversation_id}:context_revision"

    async def get_data_result_stack(
        self,
        user_id: str,
        conversation_id: str,
    ) -> List[Dict[str, Any]]:
        redis = await get_redis()
        if not redis:
            return []
        key = self._get_data_result_stack_key(user_id, conversation_id)
        try:
            raw = await redis.get(key)
            if not raw:
                return []
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            parsed = json.loads(raw)
            from app.services.ai.reusable_result import sanitize_reusable_result_payload

            safe_items = []
            if isinstance(parsed, list):
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    safe_item = sanitize_reusable_result_payload(item)
                    if safe_item:
                        safe_items.append(safe_item)
            return safe_items
        except Exception as e:
            logger.error("[MemoryService] Failed to get data result stack from key %s: %s", key, e)
            return []

    async def push_data_result_ref(
        self,
        user_id: str,
        conversation_id: str,
        payload: Dict[str, Any],
        *,
        max_depth: int = 10,
    ) -> None:
        from app.services.ai.chatbi_result_stack import ChatBIResultRef

        redis = await get_redis()
        if not redis:
            logger.warning("[MemoryService] Redis client not available for push_data_result_ref")
            return
        key = self._get_data_result_stack_key(user_id, conversation_id)
        try:
            from app.services.ai.reusable_result import sanitize_reusable_result_payload

            result_ref = ChatBIResultRef.from_dict(payload)
            safe_payload = sanitize_reusable_result_payload(result_ref.to_dict()) or {}
            result_id = str(safe_payload.get("result_id") or "")
            eval_script = getattr(redis, "eval", None)
            if not callable(eval_script):
                logger.error("[MemoryService] Redis client does not support EVAL for data result stack")
                return
            await eval_script(
                _PERSIST_DATA_RESULT_STACK_SCRIPT,
                1,
                key,
                json.dumps(safe_payload, ensure_ascii=False),
                str(self.ttl),
                str(max(1, int(max_depth or 10))),
                result_id,
            )
        except Exception as e:
            logger.error("[MemoryService] Failed to push data result stack key %s: %s", key, e)

    async def get_digest(self, user_id: str, conversation_id: str) -> Optional[str]:
        """读取跨轮溢出摘录（digest）文本。

        用独立 key 存储，不进入展示路径；无摘录或读取失败均返回 ``None``
        （由调用方降级为确定性压缩，不影响主流程）。
        """
        redis = await get_redis()
        if not redis:
            return None
        key = self._get_digest_key(user_id, conversation_id)
        try:
            raw = await redis.get(key)
            if not raw:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return raw
        except Exception as e:
            logger.error("[MemoryService] Failed to get digest key %s: %s", key, e)
            return None

    async def get_context_revision(self, user_id: str, conversation_id: str) -> int:
        """Read the current conversation branch revision for async derived state."""
        redis = await get_redis()
        if not redis:
            return 0

        key = self._get_context_revision_key(user_id, conversation_id)
        try:
            return int(await redis.get(key) or 0)
        except Exception as e:
            logger.warning(
                "[MemoryService] Failed to get context revision key %s: %s",
                key,
                e,
            )
            return 0

    async def get_current_seq(self, user_id: str, conversation_id: str) -> int:
        """读取当前会话已分配的最高消息序号。"""
        redis = await get_redis()
        if not redis:
            return 0
        try:
            return int(await redis.get(self._get_seq_counter_key(user_id, conversation_id)) or 0)
        except Exception as e:
            logger.warning(
                "[MemoryService] Failed to get seq counter for %s:%s: %s",
                user_id,
                conversation_id,
                e,
            )
            return 0

    async def set_digest(self, user_id: str, conversation_id: str, content: str) -> None:
        """写入跨轮溢出摘录（digest）文本，沿用会话 TTL（默认 30 天）。"""
        redis = await get_redis()
        if not redis:
            return
        key = self._get_digest_key(user_id, conversation_id)
        meta_keys = [
            self._get_digest_meta_key(user_id, conversation_id, field)
            for field in ("seq", "revision", "quality")
        ]
        try:
            if not content:
                await redis.delete(key, *meta_keys)
            else:
                await redis.set(key, content, ex=self.ttl)
                await redis.delete(*meta_keys)
        except Exception as e:
            logger.error("[MemoryService] Failed to set digest key %s: %s", key, e)

    async def set_digest_if_current(
        self,
        user_id: str,
        conversation_id: str,
        content: str,
        *,
        source_seq: int,
        source_revision: Optional[int] = None,
        quality: int = 0,
        allow_newer_seq: bool = False,
        override_same_seq_same_quality: bool = False,
    ) -> bool:
        """仅在摘要来源仍对应当前历史时写入 digest。

        先用 seq counter 做快速淘汰，再用 Redis WATCH/MULTI 保护检查与写入之间的
        竞态，避免较旧的后台摘要覆盖更新历史对应的摘要。上下文压缩摘要可以
        ``allow_newer_seq=True``：同一分支在摘要生成期间追加 assistant 消息不应让
        这个“历史前缀摘要”失效；分支 revision 变化和更高 source_seq 的摘要仍会淘汰它。

        ``override_same_seq_same_quality=True`` 允许同 source_seq 且同 quality 的
        确定性摘要互相覆盖：路由阶段按目标模型 budget 二次重压出的确定性摘要是
        该窗口的权威版本，需能以相同 seq/quality 覆盖 pre-route 阶段落的确定性摘要，
        否则 Redis 上的 digest 会停留在旧窗口，与 executor 实际所见的窗口不一致。
        （仍拒绝更高 source_seq 与更高 stored_quality，绝不把 LLM 摘要降级覆盖。）
        """
        redis = await get_redis()
        if not redis:
            return False
        seq_key = self._get_seq_counter_key(user_id, conversation_id)
        revision_key = self._get_context_revision_key(user_id, conversation_id)
        digest_key = self._get_digest_key(user_id, conversation_id)
        digest_seq_key = self._get_digest_meta_key(user_id, conversation_id, "seq")
        digest_revision_key = self._get_digest_meta_key(user_id, conversation_id, "revision")
        digest_quality_key = self._get_digest_meta_key(user_id, conversation_id, "quality")
        try:
            current_raw = await redis.get(seq_key)
            current_seq = int(current_raw or 0)
            if not allow_newer_seq and current_seq > int(source_seq):
                return False
            if source_revision is not None:
                current_revision = int(await redis.get(revision_key) or 0)
                if current_revision != int(source_revision):
                    return False
            stored_seq = int(await redis.get(digest_seq_key) or -1)
            stored_quality = int(await redis.get(digest_quality_key) or -1)
            if (
                stored_seq > int(source_seq)
                or (
                    stored_seq == int(source_seq)
                    and (
                        stored_quality > int(quality)
                        or (
                            not override_same_seq_same_quality
                            and stored_quality >= int(quality)
                        )
                    )
                )
            ):
                return False

            async with redis.pipeline() as pipe:
                # redis-py accepts multiple WATCH names, while lightweight test
                # doubles may accept only one key. Repeated WATCH calls are
                # equivalent and keep the atomic check portable.
                watch_keys = [seq_key, digest_seq_key, digest_quality_key]
                if source_revision is not None:
                    watch_keys.extend([revision_key, digest_revision_key])
                for watch_key in watch_keys:
                    await pipe.watch(watch_key)
                current_raw = await pipe.get(seq_key)
                current_seq = int(current_raw or 0)
                if not allow_newer_seq and current_seq > int(source_seq):
                    await pipe.reset()
                    return False
                if source_revision is not None:
                    current_revision = int(await pipe.get(revision_key) or 0)
                    if current_revision != int(source_revision):
                        await pipe.reset()
                        return False
                stored_seq = int(await pipe.get(digest_seq_key) or -1)
                stored_quality = int(await pipe.get(digest_quality_key) or -1)
                if (
                    stored_seq > int(source_seq)
                    or (
                        stored_seq == int(source_seq)
                        and (
                            stored_quality > int(quality)
                            or (
                                not override_same_seq_same_quality
                                and stored_quality >= int(quality)
                            )
                        )
                    )
                ):
                    await pipe.reset()
                    return False
                pipe.multi()
                if content:
                    pipe.set(digest_key, content, ex=self.ttl)
                    pipe.set(digest_seq_key, int(source_seq), ex=self.ttl)
                    pipe.set(
                        digest_quality_key,
                        int(quality),
                        ex=self.ttl,
                    )
                    if source_revision is not None:
                        pipe.set(
                            digest_revision_key,
                            int(source_revision),
                            ex=self.ttl,
                        )
                else:
                    pipe.delete(
                        digest_key,
                        digest_seq_key,
                        digest_revision_key,
                        digest_quality_key,
                    )
                await pipe.execute()
            return True
        except WatchError:
            return False
        except Exception as e:
            logger.error(
                "[MemoryService] Failed to conditionally set digest key %s: %s",
                digest_key,
                e,
            )
            return False

    async def reset_context_state(
        self,
        user_id: str,
        conversation_id: str,
        *,
        delete_summary: bool = True,
    ) -> None:
        """清理历史截断后不能继续复用的上下文派生状态。

        seq counter 必须保留单调性；其余状态都必须从当前历史重新建立，避免
        编辑重发后把旧分支摘要合入新分支。
        """
        redis = await get_redis()
        if redis:
            try:
                revision_key = self._get_context_revision_key(user_id, conversation_id)
                # seq counter is intentionally preserved, while the revision changes
                # on every branch reset so an already-running digest task cannot
                # repopulate the old branch after truncation/clear.
                await redis.incr(revision_key)
                await redis.expire(revision_key, self.ttl)
                await redis.delete(self._get_digest_key(user_id, conversation_id))
                await redis.delete(
                    self._get_digest_meta_key(user_id, conversation_id, "seq"),
                    self._get_digest_meta_key(user_id, conversation_id, "revision"),
                    self._get_digest_meta_key(user_id, conversation_id, "quality"),
                )
                await redis.delete(f"memory:debounce:{user_id}:{conversation_id}")
            except Exception as e:
                logger.warning(
                    "[MemoryService] Failed to reset context cache for %s:%s: %s",
                    user_id,
                    conversation_id,
                    e,
                )
        if delete_summary:
            try:
                from app.services.ai.memory_index_service import MemoryIndexService

                await MemoryIndexService.delete_summary(str(user_id), conversation_id)
            except Exception as e:
                logger.warning(
                    "[MemoryService] Failed to reset structured summary for %s:%s: %s",
                    user_id,
                    conversation_id,
                    e,
                )

    async def get_current_data_result(
        self,
        user_id: str,
        conversation_id: str,
    ) -> Optional[Dict[str, Any]]:
        stack = await self.get_data_result_stack(user_id, conversation_id)
        if stack:
            return stack[-1]
        return await self.get_last_data_result(user_id, conversation_id)

    async def get_history(self, user_id: str, conversation_id: str, limit: Optional[int] = None, offset: int = 0) -> List[Dict[str, str]]:
        """
        Retrieve history from Redis with pagination support.
        offset=0 means the most recent messages.

        使用 Redis LIST 负索引直接读取最近窗口，避免先 `llen` 再 `lrange` 的额外往返；
        同时将 limit/offset 精确转换为保留窗口内的 Redis 区间，减少大列表下的数据传输。
        """
        redis = await get_redis()
        if not redis:
            return []

        key = self._get_key(user_id, conversation_id)
        logger.info(
            "[MemoryService] Fetching history for key: %s. Limit: %s, Offset: %s",
            key,
            limit,
            offset,
        )

        if self.max_history_len <= 0:
            return []

        try:
            effective_offset = max(0, int(offset or 0))
        except (TypeError, ValueError):
            effective_offset = 0

        # 负索引以列表尾部为基准，因此不需要先读取列表长度。
        # 当 offset 达到保留窗口长度时，旧实现也不会返回窗口外的历史。
        if limit is not None and limit > 0:
            if effective_offset >= self.max_history_len:
                return []
            start_incl = max(
                -self.max_history_len,
                -limit - effective_offset,
            )
            end_incl = -1 - effective_offset
        else:
            start_incl = -self.max_history_len
            end_incl = -1

        data = await redis.lrange(key, start_incl, end_incl)

        history = []
        for item in data:
            try:
                history.append(json.loads(item))
            except Exception as e:
                logger.error(f"Failed to parse history item: {item}. Error: {e}")

        return history

    @staticmethod
    def merge_context_snapshot(
        history: List[Dict[str, Any]],
        snapshot: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """将压缩快照与快照创建后的新消息合并。"""
        if not isinstance(snapshot, dict):
            return history
        compacted = snapshot.get("messages")
        if not isinstance(compacted, list):
            return history
        try:
            source_seq = int(snapshot.get("source_seq") or 0)
        except (TypeError, ValueError):
            source_seq = 0
        if source_seq <= 0:
            return history
        newer = [
            message for message in history
            if isinstance(message, dict) and int(message.get("seq") or 0) > source_seq
        ]
        return [*compacted, *newer]

    async def get_effective_context_history(
        self,
        user_id: str,
        conversation_id: str,
    ) -> List[Dict[str, Any]]:
        """读取实际供后续请求使用的历史（压缩快照加新增消息）。"""
        history = await self.get_history(user_id, conversation_id)
        snapshot = await self.get_context_snapshot(user_id, conversation_id)
        return self.merge_context_snapshot(history, snapshot)

    async def add_message(self, user_id: str, conversation_id: str, role: str, content: str, trace_id: Optional[str] = None, files: Optional[List[Dict[str, Any]]] = None, agent_name: Optional[str] = None, agent_type: Optional[str] = None, agent_display_name: Optional[str] = None, prompt_tokens: Optional[int] = 0, completion_tokens: Optional[int] = 0, total_tokens: Optional[int] = None, has_data_output: Optional[bool] = None, reusable_result_id: Optional[str] = None, reusable_result_status: Optional[str] = None, reasoning_content: Optional[str] = None, process_timeline: Optional[List[Dict[str, Any]]] = None, tool_run_text: Optional[str] = None, status: Optional[str] = None):
        """
        Append a single message to the conversation history.
        Now supports trace_id, attachment files, and token usage values.

        agent_name: 处理该轮的智能体 name(slug)。仅对 assistant 消息记录，
        用于后续路由的会话粘性（让追问沿用上一轮智能体）。
        agent_type: 处理该轮的智能体类型（如 system/agent/rag 等主类型）。仅对 assistant 消息记录。
        agent_display_name: 处理该轮的智能体展示名。仅对 assistant 消息记录。
        tool_run_text: 本轮已完成且成功的工具最终结果文本（独立字段，不拼入
        content），仅用于后续轮上下文重建，不直接展示给用户；过程日志、思考
        卡片和失败调用不应写入此字段。
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
        if role == "assistant":
            message["agent_type"] = agent_type or "GENERAL"
            if agent_display_name:
                message["agent_display_name"] = agent_display_name
            if status:
                message["status"] = status
        if reasoning_content:
            message["reasoning_content"] = reasoning_content
        if process_timeline:
            message["process_timeline"] = process_timeline
        _prompt_tokens = int(prompt_tokens or 0)
        _completion_tokens = int(completion_tokens or 0)
        message["prompt_tokens"] = _prompt_tokens
        message["completion_tokens"] = _completion_tokens
        message["total_tokens"] = int(total_tokens or (_prompt_tokens + _completion_tokens))
        message["has_data_output"] = bool(has_data_output or False)
        if role == "assistant":
            if reusable_result_id:
                message["reusable_result_id"] = str(reusable_result_id)
            if reusable_result_status:
                message["reusable_result_status"] = str(reusable_result_status)
        if tool_run_text:
            from app.services.ai.runtime.agentscope.tool_result_context import (
                TOOL_RESULT_CONTEXT_VERSION,
            )

            message["tool_run_text"] = tool_run_text
            message["tool_run_text_version"] = TOOL_RESULT_CONTEXT_VERSION

        # 单调 seq：独立计数器分配，与 list 索引解耦。
        # 先 INCR 取新 seq（在 rpush 之前已知），供摘要游标 synced_seq 判定增量窗口。
        seq_key = self._get_seq_counter_key(user_id, conversation_id)
        assigned_seq = await redis.incr(seq_key)
        message["seq"] = assigned_seq

        # Push to list
        try:
            val = json.dumps(message, ensure_ascii=False)
            async with redis.pipeline() as pipe:
                await pipe.rpush(key, val)
                await pipe.ltrim(key, -self.max_history_len, -1)
                await pipe.expire(key, self.ttl)
                await pipe.expire(seq_key, self.ttl)
                await pipe.execute()
            logger.info(f"[MemoryService] Added message to key: {key}. TraceID: {trace_id}")
        except Exception as e:
            logger.error(f"[MemoryService] Failed to add message to key {key}: {e}")

    async def update_last_user_message_content(
        self,
        user_id: str,
        conversation_id: str,
        content: str,
    ) -> bool:
        """Patch the latest user message content (e.g. persist vision sidecar text)."""
        redis = await get_redis()
        if not redis:
            logger.warning("[MemoryService] Redis client not available for update_last_user_message_content")
            return False
        key = self._get_key(user_id, conversation_id)
        try:
            data = await redis.lrange(key, 0, -1)
            for index in range(len(data) - 1, -1, -1):
                try:
                    message = json.loads(data[index])
                except Exception:
                    continue
                if message.get("role") != "user":
                    continue
                message["content"] = content
                await redis.lset(key, index, json.dumps(message, ensure_ascii=False))
                return True
        except Exception as e:
            logger.error(f"[MemoryService] Failed to update last user message for key {key}: {e}")
            return False
        return False

    async def truncate_history(
        self,
        user_id: str,
        conversation_id: str,
        keep_count: int,
    ) -> bool:
        """
        Truncate conversation history to keep only the first `keep_count` messages.
        If keep_count <= 0, deletes the entire history key.
        """
        redis = await get_redis()
        if not redis:
            logger.warning("[MemoryService] Redis client not available for truncate_history")
            return False
        key = self._get_key(user_id, conversation_id)
        try:
            if keep_count <= 0:
                await redis.delete(key)
                logger.info(f"[MemoryService] Deleted history key: {key}")
            else:
                await redis.ltrim(key, 0, keep_count - 1)
                await redis.expire(key, self.ttl)
                logger.info(f"[MemoryService] Truncated history key: {key} to {keep_count} items")
            await self.reset_context_state(user_id, conversation_id)
            return True
        except Exception as e:
            logger.error(f"[MemoryService] Failed to truncate history for key {key}: {e}")
            return False

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
            from app.services.ai.reusable_result import sanitize_reusable_result_payload

            return sanitize_reusable_result_payload(parsed)
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
            from app.services.ai.reusable_result import sanitize_reusable_result_payload

            safe_payload = sanitize_reusable_result_payload(payload) or {}
            await redis.set(key, json.dumps(safe_payload, ensure_ascii=False), ex=self.ttl)
            logger.info(f"[MemoryService] Stored last data result for key: {key}")
        except Exception as e:
            logger.error(f"[MemoryService] Failed to set last data result for key {key}: {e}")

    async def get_session_tool_artifact(
        self,
        user_id: str,
        conversation_id: str,
    ) -> Optional[Dict[str, Any]]:
        """通用智能体：上一轮主工具结果快照。"""
        redis = await get_redis()
        if not redis:
            return None
        key = self._get_session_tool_artifact_key(user_id, conversation_id)
        try:
            raw = await redis.get(key)
            if not raw:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            parsed = json.loads(raw)
            from app.services.ai.reusable_result import sanitize_reusable_result_payload

            return sanitize_reusable_result_payload(parsed)
        except Exception as e:
            logger.error("[MemoryService] Failed to get session tool artifact %s: %s", key, e)
            return None

    async def set_session_tool_artifact(
        self,
        user_id: str,
        conversation_id: str,
        payload: Dict[str, Any],
    ) -> None:
        redis = await get_redis()
        if not redis:
            logger.warning("[MemoryService] Redis unavailable for set_session_tool_artifact")
            return
        key = self._get_session_tool_artifact_key(user_id, conversation_id)
        try:
            from app.services.ai.reusable_result import sanitize_reusable_result_payload

            safe_payload = sanitize_reusable_result_payload(payload) or {}
            await redis.set(key, json.dumps(safe_payload, ensure_ascii=False), ex=self.ttl)
            logger.info("[MemoryService] Stored session tool artifact for key: %s", key)
        except Exception as e:
            logger.error("[MemoryService] Failed to set session tool artifact %s: %s", key, e)

    async def get_reusable_result(
        self,
        user_id: str,
        conversation_id: str,
    ) -> Optional[Dict[str, Any]]:
        """读取当前会话的统一可复用结果。"""
        try:
            redis = await get_redis()
            if not redis:
                return None
            key = self._get_reusable_result_key(user_id, conversation_id)
            raw = await redis.get(key)
            if not raw:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            parsed = json.loads(raw)
            from app.services.ai.reusable_result import sanitize_reusable_result_payload

            return sanitize_reusable_result_payload(parsed)
        except Exception as exc:
            logger.warning("[MemoryService] Failed to get reusable result: %s", exc)
            return None

    async def get_reusable_result_stack(
        self,
        user_id: str,
        conversation_id: str,
    ) -> List[Dict[str, Any]]:
        """读取当前会话最近的统一可复用结果，旧结果在列表前面。"""
        try:
            redis = await get_redis()
            if not redis:
                return []
            key = self._get_reusable_result_stack_key(user_id, conversation_id)
            raw = await redis.get(key)
            if not raw:
                return []
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            parsed = json.loads(raw)
            from app.services.ai.reusable_result import sanitize_reusable_result_payload

            safe_items = []
            if isinstance(parsed, list):
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    safe_item = sanitize_reusable_result_payload(item)
                    if safe_item:
                        safe_items.append(safe_item)
            return safe_items
        except Exception as exc:
            logger.warning("[MemoryService] Failed to get reusable result stack: %s", exc)
            return []

    async def _persist_reusable_result_atomic(
        self,
        redis: Any,
        user_id: str,
        conversation_id: str,
        payload: Dict[str, Any],
        *,
        max_depth: int = 10,
    ) -> bool:
        """原子写入 current 和 stack，避免并发读改写丢失结果。"""
        eval_script = getattr(redis, "eval", None)
        if not callable(eval_script):
            logger.error("[MemoryService] Redis client does not support EVAL")
            return False

        from app.services.ai.reusable_result import sanitize_reusable_result_payload

        safe_payload = sanitize_reusable_result_payload(payload) or {}
        current_key = self._get_reusable_result_key(user_id, conversation_id)
        stack_key = self._get_reusable_result_stack_key(user_id, conversation_id)
        payload_json = json.dumps(safe_payload, ensure_ascii=False)
        result_id = str(safe_payload.get("result_id") or "")
        depth = max(1, int(max_depth or 10))
        result = await eval_script(
            _PERSIST_REUSABLE_RESULT_SCRIPT,
            2,
            current_key,
            stack_key,
            payload_json,
            str(self.ttl),
            str(depth),
            result_id,
        )
        return bool(result)

    async def set_reusable_result(
        self,
        user_id: str,
        conversation_id: str,
        payload: Dict[str, Any],
    ) -> bool:
        """写入 current，并将结果幂等加入 stack。"""
        if not isinstance(payload, dict):
            return False
        try:
            redis = await get_redis()
            if not redis:
                return False
            return await self._persist_reusable_result_atomic(
                redis,
                user_id,
                conversation_id,
                payload,
            )
        except Exception as exc:
            logger.warning("[MemoryService] Failed to set reusable result: %s", exc)
            return False

    async def push_reusable_result(
        self,
        user_id: str,
        conversation_id: str,
        payload: Dict[str, Any],
        *,
        max_depth: int = 10,
    ) -> bool:
        """将结果追加到 stack 并同步更新 current；相同 result_id 只保留最新一份。"""
        if not isinstance(payload, dict):
            return False
        try:
            redis = await get_redis()
            if not redis:
                return False
            return await self._persist_reusable_result_atomic(
                redis,
                user_id,
                conversation_id,
                payload,
                max_depth=max_depth,
            )
        except Exception as exc:
            logger.warning("[MemoryService] Failed to push reusable result: %s", exc)
            return False

    async def clear_history(self, user_id: str, conversation_id: str):
        """
        Delete a conversation history.
        """
        redis = await get_redis()
        if not redis:
            return
        from app.services.conversation_resource_service import ConversationResourceService
        await ConversationResourceService.delete(user_id, conversation_id)
        key = self._get_key(user_id, conversation_id)
        logger.info(f"[MemoryService] Clearing history for key: {key}")
        await redis.delete(key)
        await redis.delete(self._get_data_result_key(user_id, conversation_id))
        await redis.delete(self._get_data_result_stack_key(user_id, conversation_id))
        await redis.delete(self._get_session_tool_artifact_key(user_id, conversation_id))
        await redis.delete(self._get_reusable_result_key(user_id, conversation_id))
        await redis.delete(self._get_reusable_result_stack_key(user_id, conversation_id))
        await self.reset_context_state(user_id, conversation_id)

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

    def _get_active_conversation_key(
        self,
        user_id: str,
        instance_id: Optional[str] = None,
    ) -> str:
        uid = require_user_id(user_id)
        normalized_instance_id = str(instance_id or "").strip()
        if not normalized_instance_id:
            return f"{self.KEY_PREFIX}:{uid}:active"
        encoded_instance_id = quote(normalized_instance_id, safe="")
        return f"{self.KEY_PREFIX}:{uid}:active:{encoded_instance_id}"

    async def get_active_conversation(
        self,
        user_id: str,
        instance_id: Optional[str] = None,
    ) -> Optional[str]:
        redis = await get_redis()
        if not redis:
            return None
        key = self._get_active_conversation_key(user_id, instance_id)
        try:
            val = await redis.get(key)
            if isinstance(val, bytes):
                return val.decode("utf-8")
            return val
        except Exception as e:
            logger.error(f"[MemoryService] Failed to get active conversation: {e}")
            return None

    async def set_active_conversation(
        self,
        user_id: str,
        conversation_id: str,
        instance_id: Optional[str] = None,
    ):
        redis = await get_redis()
        if not redis:
            return
        key = self._get_active_conversation_key(user_id, instance_id)
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
        uid = require_user_id(user_id)
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
