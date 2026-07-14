from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from app.services.ai.memory_service import memory_service

logger = logging.getLogger(__name__)

PENDING_CONFIRMATION_TTL_SECONDS = 600
PENDING_KEY_SUFFIX = "pending_perm"


@dataclass
class PendingAgentScopeSnapshot:
    request_id: str
    kind: Literal["permission", "external"]
    user_id: str | None
    conversation_id: str | None
    trace_id: str
    reply_id: str
    agent_name: str
    tool_call: dict[str, Any]
    agent_state: dict[str, Any]
    stream_state: dict[str, Any]
    runner_context: dict[str, Any]
    evidence_receipts: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def is_expired(self, now: float | None = None) -> bool:
        now = now or time.time()
        return now - self.created_at > PENDING_CONFIRMATION_TTL_SECONDS


class PendingAgentScopeStore:
    """Redis-backed pending confirmation/external-execution snapshots."""

    def __init__(self) -> None:
        self._memory_fallback: dict[str, PendingAgentScopeSnapshot] = {}

    def _user_id(self, user_id: str | int | None) -> str:
        return str(user_id) if user_id is not None else "anonymous"

    def _key(self, user_id: str | int | None, request_id: str) -> str:
        return (
            f"{memory_service.KEY_PREFIX}:{self._user_id(user_id)}:"
            f"pending:{request_id}"
        )

    def _user_matches(
        self,
        snapshot: PendingAgentScopeSnapshot,
        user_id: str | int | None,
    ) -> bool:
        if snapshot.user_id is None or user_id is None:
            return True
        return str(snapshot.user_id) == str(user_id)

    async def register(self, snapshot: PendingAgentScopeSnapshot) -> PendingAgentScopeSnapshot:
        await self.prune()
        from app.core.redis import get_redis

        redis = await get_redis()
        if redis:
            key = self._key(snapshot.user_id, snapshot.request_id)
            try:
                await redis.set(
                    key,
                    json.dumps(asdict(snapshot), ensure_ascii=False),
                    ex=PENDING_CONFIRMATION_TTL_SECONDS,
                )
            except Exception as exc:
                logger.warning("[PendingAgentScopeStore] Redis save failed: %s", exc)
                self._memory_fallback[snapshot.request_id] = snapshot
        else:
            self._memory_fallback[snapshot.request_id] = snapshot
        return snapshot

    async def pop(self, request_id: str, *, user_id: str | int | None = None) -> PendingAgentScopeSnapshot | None:
        await self.prune()
        from app.core.redis import get_redis

        redis = await get_redis()
        if redis:
            key = self._key(user_id, request_id)
            try:
                raw = await redis.get(key)
                if raw:
                    await redis.delete(key)
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    snapshot = self._from_dict(json.loads(raw))
                    if snapshot and not snapshot.is_expired():
                        return snapshot
            except Exception as exc:
                logger.warning("[PendingAgentScopeStore] Redis pop failed: %s", exc)

        snapshot = self._memory_fallback.get(request_id)
        if snapshot and snapshot.is_expired():
            self._memory_fallback.pop(request_id, None)
            return None
        if snapshot and not self._user_matches(snapshot, user_id):
            return None
        if snapshot:
            self._memory_fallback.pop(request_id, None)
        return snapshot

    async def peek(self, request_id: str, *, user_id: str | int | None = None) -> PendingAgentScopeSnapshot | None:
        await self.prune()
        from app.core.redis import get_redis

        redis = await get_redis()
        if redis:
            key = self._key(user_id, request_id)
            try:
                raw = await redis.get(key)
                if raw:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    snapshot = self._from_dict(json.loads(raw))
                    if snapshot and not snapshot.is_expired():
                        return snapshot
            except Exception as exc:
                logger.warning("[PendingAgentScopeStore] Redis peek failed: %s", exc)
        snapshot = self._memory_fallback.get(request_id)
        if snapshot and snapshot.is_expired():
            self._memory_fallback.pop(request_id, None)
            return None
        return snapshot

    async def prune(self) -> None:
        now = time.time()
        expired = [
            request_id
            for request_id, snapshot in self._memory_fallback.items()
            if snapshot.is_expired(now)
        ]
        for request_id in expired:
            self._memory_fallback.pop(request_id, None)

    def clear(self) -> None:
        self._memory_fallback.clear()

    @staticmethod
    def _from_dict(data: dict[str, Any]) -> PendingAgentScopeSnapshot | None:
        try:
            return PendingAgentScopeSnapshot(
                request_id=str(data["request_id"]),
                kind=data.get("kind", "permission"),
                user_id=data.get("user_id"),
                conversation_id=data.get("conversation_id"),
                trace_id=str(data.get("trace_id", "")),
                reply_id=str(data.get("reply_id", "")),
                agent_name=str(data.get("agent_name", "")),
                tool_call=dict(data.get("tool_call") or {}),
                agent_state=dict(data.get("agent_state") or {}),
                stream_state=dict(data.get("stream_state") or {}),
                runner_context=dict(data.get("runner_context") or {}),
                evidence_receipts=list(data.get("evidence_receipts") or []),
                created_at=float(data.get("created_at", time.time())),
            )
        except Exception as exc:
            logger.warning("[PendingAgentScopeStore] Invalid snapshot payload: %s", exc)
            return None

    def new_request_id(self) -> str:
        return f"perm_{uuid.uuid4().hex}"


pending_agentscope_store = PendingAgentScopeStore()
