"""会话流事件日志：把正在运行的对话流事件按序暂存到 Redis，供断线恢复期增量拉取。

设计要点：
- 事件日志**只用于过程可视化**。本轮最终内容一律以落库历史为准，因此日志被
  LTRIM 截断、TTL 过期或 Redis 抖动都不会破坏终态正确性。
- seq 由 Redis INCR 分配，(user_id, conversation_id) 维度单调递增，与
  memory_service 的会话消息 seq 同一思路：计数器与 list 索引解耦，ltrim 压缩
  索引不影响序号单调性。前端按 after_seq 增量拉取，避免重复渲染。
- 任何异常都不得影响对话主流程：写入失败只告警，读取失败返回空批次。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.services.ai.conversation_identity import require_user_id

logger = logging.getLogger(__name__)

DEFAULT_MAX_EVENTS = 2000
DEFAULT_TTL_SECONDS = 1800
CONFIG_KEY_MAX_EVENTS = "chat_stream_journal_max_events"
CONFIG_KEY_TTL_SECONDS = "chat_stream_journal_ttl_seconds"


class ConversationStreamJournal:
    """按 (user, conversation) 保存流式事件的有界日志。"""

    def __init__(self, *, max_events: int | None = None, ttl_seconds: int | None = None) -> None:
        self._max_events = max_events
        self._ttl_seconds = ttl_seconds

    @staticmethod
    async def _redis():
        from app.core.redis import get_redis

        return await get_redis()

    @staticmethod
    def _base_key(user_id: str | int | None, conversation_id: str) -> str:
        uid = require_user_id(user_id)
        safe_cid = str(conversation_id or "").replace(":", "_")
        return f"nanzi:conv_stream:{uid}:{safe_cid}"

    @classmethod
    def _list_key(cls, user_id: str | int | None, conversation_id: str) -> str:
        return cls._base_key(user_id, conversation_id)

    @classmethod
    def _seq_key(cls, user_id: str | int | None, conversation_id: str) -> str:
        return f"{cls._base_key(user_id, conversation_id)}:seq"

    async def _limits(self) -> tuple[int, int]:
        max_events = self._max_events
        ttl_seconds = self._ttl_seconds
        try:
            from app.services.config_service import ConfigService

            if max_events is None:
                raw = await ConfigService.get(CONFIG_KEY_MAX_EVENTS, str(DEFAULT_MAX_EVENTS))
                try:
                    max_events = max(50, int(raw))
                except (TypeError, ValueError):
                    max_events = DEFAULT_MAX_EVENTS
            if ttl_seconds is None:
                raw = await ConfigService.get(CONFIG_KEY_TTL_SECONDS, str(DEFAULT_TTL_SECONDS))
                try:
                    ttl_seconds = max(60, int(raw))
                except (TypeError, ValueError):
                    ttl_seconds = DEFAULT_TTL_SECONDS
        except Exception:  # noqa: BLE001 - 配置读取失败时退回默认值
            max_events = max_events or DEFAULT_MAX_EVENTS
            ttl_seconds = ttl_seconds or DEFAULT_TTL_SECONDS
        return max_events, ttl_seconds

    async def append(
        self,
        user_id: str | int | None,
        conversation_id: str,
        events: list[dict[str, Any]],
    ) -> None:
        """追加事件；失败只告警，不影响对话主流程。"""
        if not conversation_id or not events:
            return
        try:
            redis = await self._redis()
            if redis is None:
                return
            list_key = self._list_key(user_id, conversation_id)
            seq_key = self._seq_key(user_id, conversation_id)
            valid_events = [event for event in events if isinstance(event, dict)]
            if not valid_events:
                return
            # 单次 INCRBY 预留整段序号：合并窗口最多能攒十几条高频增量，
            # 逐条 INCR 会让每次 flush 变成 N 次网络往返；原子预留同样保持严格单调。
            batch_size = len(valid_events)
            seq_end = int(await redis.incrby(seq_key, batch_size))
            seq_start = seq_end - batch_size + 1
            payloads: list[str] = []
            for offset, event in enumerate(valid_events):
                payload = dict(event)
                payload["_seq"] = seq_start + offset
                payloads.append(json.dumps(payload, ensure_ascii=False))
            max_events, ttl_seconds = await self._limits()
            async with redis.pipeline() as pipe:
                pipe.rpush(list_key, *payloads)
                pipe.ltrim(list_key, -max_events, -1)
                pipe.expire(list_key, ttl_seconds)
                pipe.expire(seq_key, ttl_seconds)
                await pipe.execute()
        except Exception as exc:  # noqa: BLE001 - 事件日志是尽力而为的旁路
            logger.warning(
                "[StreamJournal] append failed for conversation=%s: %s",
                conversation_id,
                exc,
            )

    async def read_after(
        self,
        user_id: str | int | None,
        conversation_id: str,
        *,
        after_seq: int = 0,
    ) -> dict[str, Any]:
        """读取 seq > after_seq 的事件；失败返回空批次。"""
        cursor = int(after_seq or 0)
        try:
            redis = await self._redis()
            if redis is None:
                return {"events": [], "next_seq": cursor}
            raw_items = await redis.lrange(self._list_key(user_id, conversation_id), 0, -1)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[StreamJournal] read failed for conversation=%s: %s",
                conversation_id,
                exc,
            )
            return {"events": [], "next_seq": cursor}

        events: list[dict[str, Any]] = []
        next_seq = cursor
        for raw in raw_items or []:
            try:
                event = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(event, dict):
                continue
            try:
                seq = int(event.get("_seq") or 0)
            except (TypeError, ValueError):
                continue
            if seq <= cursor:
                continue
            events.append(event)
            next_seq = max(next_seq, seq)
        return {"events": events, "next_seq": next_seq}

    async def clear(self, user_id: str | int | None, conversation_id: str) -> None:
        if not conversation_id:
            return
        try:
            redis = await self._redis()
            if redis is None:
                return
            await redis.delete(
                self._list_key(user_id, conversation_id),
                self._seq_key(user_id, conversation_id),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[StreamJournal] clear failed for conversation=%s: %s",
                conversation_id,
                exc,
            )


conversation_stream_journal = ConversationStreamJournal()
