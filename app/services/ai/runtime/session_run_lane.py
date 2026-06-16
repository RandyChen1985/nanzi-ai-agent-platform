from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

logger = logging.getLogger(__name__)

DEFAULT_LOCK_TTL_SECONDS = 600
DEFAULT_WAIT_SECONDS = 0.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.1


class ConversationRunBusyError(RuntimeError):
    """Raised when a conversation already has an active agent run."""


class ConversationRunLane:
    """Serialize agent turns per (user_id, conversation_id)."""

    def _lock_key(self, user_id: str | int | None, conversation_id: str) -> str:
        uid = str(user_id) if user_id is not None else "anonymous"
        safe_cid = conversation_id.replace(":", "_")
        return f"yunshu:conv_run:{uid}:{safe_cid}"

    async def _is_enabled(self) -> bool:
        from app.services.config_service import ConfigService

        raw = await ConfigService.get("agent_session_run_lock_enabled", "true")
        return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}

    async def _ttl_seconds(self) -> int:
        from app.services.config_service import ConfigService

        raw = await ConfigService.get("agent_session_run_lock_ttl_seconds", str(DEFAULT_LOCK_TTL_SECONDS))
        try:
            return max(30, int(raw))
        except (TypeError, ValueError):
            return DEFAULT_LOCK_TTL_SECONDS

    async def _wait_seconds(self) -> float:
        from app.services.config_service import ConfigService

        raw = await ConfigService.get("agent_session_run_lock_wait_seconds", str(DEFAULT_WAIT_SECONDS))
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return DEFAULT_WAIT_SECONDS

    async def acquire(
        self,
        *,
        user_id: str | int | None,
        conversation_id: str | None,
        trace_id: str,
        ttl_seconds: int | None = None,
        wait_seconds: float | None = None,
    ) -> tuple[str, str] | None:
        if not conversation_id:
            return None
        if not await self._is_enabled():
            return None

        from app.core.redis import get_redis

        redis = await get_redis()
        if redis is None:
            logger.warning("[ConversationRunLane] Redis unavailable; skipping run lock")
            return None

        ttl = ttl_seconds if ttl_seconds is not None else await self._ttl_seconds()
        wait = wait_seconds if wait_seconds is not None else await self._wait_seconds()
        key = self._lock_key(user_id, conversation_id)
        token = trace_id or uuid.uuid4().hex
        deadline = asyncio.get_running_loop().time() + wait

        while True:
            try:
                acquired = await redis.set(key, token, ex=ttl, nx=True)
            except Exception as exc:
                logger.warning("[ConversationRunLane] acquire failed: %s", exc)
                return None
            if acquired:
                return key, token
            if wait <= 0:
                break
            if asyncio.get_running_loop().time() > deadline:
                break
            await asyncio.sleep(DEFAULT_POLL_INTERVAL_SECONDS)

        logger.info(
            "[ConversationRunLane] busy conversation=%s user=%s trace=%s",
            conversation_id,
            user_id,
            trace_id,
        )
        return None

    async def release(self, key: str | None, token: str | None) -> None:
        if not key or not token:
            return

        from app.core.redis import get_redis

        redis = await get_redis()
        if redis is None:
            return

        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end"
        )
        try:
            await redis.eval(script, 1, key, token)
        except Exception as exc:
            logger.warning("[ConversationRunLane] release failed: %s", exc)

    @asynccontextmanager
    async def hold(
        self,
        *,
        user_id: str | int | None,
        conversation_id: str | None,
        trace_id: str,
        ttl_seconds: int | None = None,
        wait_seconds: float | None = None,
    ) -> AsyncIterator[bool]:
        """
        Yield True when the lane lock is held.
        Yield False when locking is skipped (no conversation_id / disabled / no redis).
        Raise ConversationRunBusyError when the lane is busy.
        """
        if not conversation_id:
            yield False
            return

        handle = await self.acquire(
            user_id=user_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
            ttl_seconds=ttl_seconds,
            wait_seconds=wait_seconds,
        )
        if handle is None:
            from app.core.redis import get_redis

            if await get_redis() is not None and await self._is_enabled():
                raise ConversationRunBusyError(
                    f"Conversation {conversation_id} is already processing another request"
                )
            yield False
            return

        key, token = handle
        try:
            yield True
        finally:
            await self.release(key, token)


conversation_run_lane = ConversationRunLane()
