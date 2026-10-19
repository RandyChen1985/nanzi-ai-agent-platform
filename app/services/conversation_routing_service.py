"""会话级专家选择：记录每个会话最后选择的专家与路由模式。

与会话历史（``conversation:{uid}:{cid}:history``）和会话资源范围
（``conversation:{uid}:{cid}:resource_scope_v1``）共用同一套键前缀与存活期，
会话过期后被自然回收，不需要额外的清理任务。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from app.core.redis import get_redis
from app.services.ai.conversation_identity import require_user_id

logger = logging.getLogger(__name__)

#: 会话相关键的统一存活期（30 天），与会话历史保持一致。
TTL_SECONDS = 2592000


class ConversationRoutingService:
    KEY_PREFIX = "conversation"
    SUFFIX = "routing_v1"
    TTL_SECONDS = TTL_SECONDS

    @classmethod
    def _key(cls, user_id: Any, conversation_id: str) -> str:
        return f"{cls.KEY_PREFIX}:{require_user_id(user_id)}:{conversation_id}:{cls.SUFFIX}"

    @classmethod
    def _decode(cls, raw: Any) -> Optional[Dict[str, str]]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(value, dict):
            return None
        mode = "expert" if value.get("routing_mode") == "expert" else "auto"
        agent_id = str(value.get("expert_agent_id") or "").strip()
        if mode == "expert" and not agent_id:
            # expert 却没有专家 ID 属于脏数据：当作没有记录，让调用方回落账号默认，
            # 否则界面会稳定地停在一个永远选不中的专家上。
            return None
        return {"routing_mode": mode, "expert_agent_id": agent_id if mode == "expert" else ""}

    @classmethod
    async def get(cls, user_id: Any, conversation_id: str) -> Optional[Dict[str, str]]:
        """读取会话级专家记录；没有记录或 Redis 不可用时返回 None。"""
        if not str(conversation_id or "").strip():
            return None
        try:
            redis = await get_redis()
        except Exception:
            redis = None
        if not redis:
            return None
        try:
            raw = await redis.get(cls._key(user_id, conversation_id))
        except Exception:
            logger.warning("Failed to read conversation routing: %s", conversation_id)
            return None
        return cls._decode(raw)

    @classmethod
    async def replace(
        cls,
        user_id: Any,
        conversation_id: str,
        routing_mode: str,
        expert_agent_id: str = "",
    ) -> Dict[str, str]:
        """覆盖写入会话级专家记录；写入失败不抛异常（前端本地切换已生效）。"""
        mode = "expert" if routing_mode == "expert" else "auto"
        agent_id = str(expert_agent_id or "").strip() if mode == "expert" else ""
        payload = {"routing_mode": mode, "expert_agent_id": agent_id}
        if not str(conversation_id or "").strip():
            return payload
        try:
            redis = await get_redis()
            if redis:
                await redis.set(
                    cls._key(user_id, conversation_id),
                    json.dumps(payload, ensure_ascii=False),
                    ex=cls.TTL_SECONDS,
                )
        except Exception:
            logger.warning("Failed to persist conversation routing: %s", conversation_id)
        return payload
