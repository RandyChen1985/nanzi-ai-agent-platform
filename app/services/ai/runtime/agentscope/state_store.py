from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.services.ai.memory_service import memory_service

logger = logging.getLogger(__name__)

STATE_KEY_SUFFIX = "agent_state"
SCHEMA_VERSION = 1
DEFAULT_TTL_SECONDS = 604800


@dataclass(frozen=True)
class RuntimeStateEnvelope:
    schema_version: int
    agent_name: str
    agent_version: str | None
    tools_fingerprint: str
    model_name: str | None
    updated_at: str
    state: dict[str, Any]

    def matches(self, *, tools_fingerprint: str, agent_name: str) -> bool:
        return (
            self.schema_version == SCHEMA_VERSION
            and self.agent_name == agent_name
            and self.tools_fingerprint == tools_fingerprint
        )


class AgentStateStore:
    def _user_id(self, user_id: str | int | None) -> str:
        return str(user_id) if user_id is not None else "anonymous"

    def _key(self, user_id: str | int | None, conversation_id: str, agent_name: str) -> str:
        uid = self._user_id(user_id)
        safe_agent = agent_name.replace(":", "_")
        return f"{memory_service.KEY_PREFIX}:{uid}:{conversation_id}:{STATE_KEY_SUFFIX}:{safe_agent}"

    async def load(
        self,
        user_id: str | int | None,
        conversation_id: str | None,
        agent_name: str,
    ) -> RuntimeStateEnvelope | None:
        if not conversation_id or not agent_name:
            return None
        from app.core.redis import get_redis

        redis = await get_redis()
        if not redis:
            return None
        key = self._key(user_id, conversation_id, agent_name)
        try:
            raw = await redis.get(key)
            if not raw:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            data = json.loads(raw)
            
            state_data = data.get("state") or {}
            # Prune the state context to avoid token bloat
            state_data = prune_agent_state_context(state_data)
            
            return RuntimeStateEnvelope(
                schema_version=int(data.get("schema_version", 0)),
                agent_name=str(data.get("agent_name", "")),
                agent_version=data.get("agent_version"),
                tools_fingerprint=str(data.get("tools_fingerprint", "")),
                model_name=data.get("model_name"),
                updated_at=str(data.get("updated_at", "")),
                state=state_data,
            )
        except Exception as exc:
            logger.warning("[AgentStateStore] Failed to load key=%s: %s", key, exc)
            return None

    async def save(
        self,
        *,
        user_id: str | int | None,
        conversation_id: str | None,
        agent_name: str,
        agent_version: str | None,
        tools_fingerprint: str,
        model_name: str | None,
        state: Any,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        if not conversation_id or not agent_name:
            return
        from app.core.redis import get_redis

        redis = await get_redis()
        if not redis:
            return
        key = self._key(user_id, conversation_id, agent_name)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "agent_name": agent_name,
            "agent_version": agent_version,
            "tools_fingerprint": tools_fingerprint,
            "model_name": model_name,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "state": state.model_dump(mode="json") if hasattr(state, "model_dump") else state,
        }
        try:
            await redis.set(
                key,
                json.dumps(payload, ensure_ascii=False),
                ex=ttl_seconds,
            )
        except Exception as exc:
            logger.warning("[AgentStateStore] Failed to save key=%s: %s", key, exc)

    async def delete(
        self,
        user_id: str | int | None,
        conversation_id: str | None,
        agent_name: str | None = None,
    ) -> None:
        if not conversation_id:
            return
        from app.core.redis import get_redis

        redis = await get_redis()
        if not redis:
            return
        uid = self._user_id(user_id)
        if agent_name:
            keys = [self._key(user_id, conversation_id, agent_name)]
        else:
            pattern = f"{memory_service.KEY_PREFIX}:{uid}:{conversation_id}:{STATE_KEY_SUFFIX}:*"
            keys = []
            try:
                async for key in redis.scan_iter(match=pattern, count=50):
                    keys.append(key)
            except Exception as exc:
                logger.warning("[AgentStateStore] Failed to scan keys: %s", exc)
                return
        if not keys:
            return
        try:
            await redis.delete(*keys)
        except Exception as exc:
            logger.warning("[AgentStateStore] Failed to delete keys: %s", exc)


def prune_agent_state_context(state_dict: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(state_dict, dict):
        return state_dict
    context = state_dict.get("context")
    if not isinstance(context, list):
        return state_dict

    from app.services.ai.executors.common import _clean_assistant_text

    pruned_context = []
    for msg in context:
        if not isinstance(msg, dict):
            pruned_context.append(msg)
            continue

        role = msg.get("name") or msg.get("role") or ""
        content = msg.get("content")

        # user messages are kept as is
        if role == "user" or role == "Human":
            pruned_context.append(msg)
            continue

        # assistant messages
        if isinstance(content, list):
            cleaned_content = []
            for block in content:
                if not isinstance(block, dict):
                    cleaned_content.append(block)
                    continue
                b_type = block.get("type")
                if b_type == "text":
                    text_val = block.get("text") or ""
                    if text_val:
                        cleaned_text = _clean_assistant_text(text_val, strip_thought=True)
                        if cleaned_text:
                            cleaned_content.append({**block, "text": cleaned_text})
                # tool_call and tool_result blocks are completely stripped!
            msg["content"] = cleaned_content
        elif isinstance(content, str):
            msg["content"] = _clean_assistant_text(content, strip_thought=True)

        pruned_context.append(msg)

    state_dict["context"] = pruned_context
    return state_dict


agent_state_store = AgentStateStore()

