"""会话级项目资源范围：仅保存用户主动挂载的资源。"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from app.core.redis import get_redis


class ConversationResourceService:
    KEY_PREFIX = "conversation"
    SUFFIX = "resource_scope_v1"

    @classmethod
    def _key(cls, user_id: Any, conversation_id: str) -> str:
        return f"{cls.KEY_PREFIX}:{user_id or 'anonymous'}:{conversation_id}:{cls.SUFFIX}"

    @classmethod
    async def get(cls, user_id: Any, conversation_id: str) -> Dict[str, Any]:
        try:
            redis = await get_redis()
        except Exception:
            redis = None
        if not redis:
            return {"project_name": "", "datasets": [], "knowledge_bases": [], "skills": []}
        try:
            raw = await redis.get(cls._key(user_id, conversation_id))
        except Exception:
            return {"project_name": "", "datasets": [], "knowledge_bases": [], "skills": []}
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            value = json.loads(raw or "{}")
        except (TypeError, ValueError):
            value = {}
        result: Dict[str, Any] = {"project_name": str(value.get("project_name") or "").strip()}
        result.update({
            key: [item for item in value.get(key, []) if isinstance(item, dict)]
            for key in ("datasets", "knowledge_bases", "skills")
        })
        return result

    @classmethod
    async def replace(cls, user_id: Any, conversation_id: str, scope: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        normalized: Dict[str, Any] = {"project_name": str(scope.get("project_name") or "").strip()[:100]}
        normalized.update({
            key: [item for item in scope.get(key, []) if isinstance(item, dict) and item.get("id")]
            for key in ("datasets", "knowledge_bases", "skills")
        })
        try:
            redis = await get_redis()
            if redis:
                await redis.set(cls._key(user_id, conversation_id), json.dumps(normalized, ensure_ascii=False), ex=604800)
        except Exception:
            pass
        return normalized

    @classmethod
    async def delete(cls, user_id: Any, conversation_id: str) -> None:
        try:
            redis = await get_redis()
            if redis:
                await redis.delete(cls._key(user_id, conversation_id))
        except Exception:
            pass
