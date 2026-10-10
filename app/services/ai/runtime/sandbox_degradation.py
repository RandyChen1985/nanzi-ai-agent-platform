# -*- coding: utf-8 -*-
"""Per-conversation sandbox degradation notice.

When a sandbox policy (k8s/docker/e2b/ssh) fails to initialize and the chat
runner degrades a turn to the host local backend (Bash disabled), we persist a
short-lived, per-conversation marker so the UI can show a clear, persistent
notice (instead of silently degrading). The marker is cleared as soon as a later
turn successfully builds the sandbox workspace again, so "it will auto-recover"
is literally true.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# TTL of the degraded marker. Long enough to stay visible across refresh/polling,
# but bounded so a stale marker never leaks into a long-lived conversation
# indefinitely if the host restarts between clear signals.
_DEGRADED_TTL_SECONDS = 6 * 60 * 60


def _key(user_id: Optional[str], conversation_id: str) -> str:
    """降级提示的 Redis 键。

    必须带 user_id：会话 ID 由客户端传入，只按 conversation_id 存键时，任何持有他人
    conversation_id 的人都能读到对方会话的运行提示（虽非会话正文，仍属跨用户读取）。
    """
    return f"sandbox:degraded:{user_id or 'unknown'}:{conversation_id}"


async def set_sandbox_degraded(user_id: Optional[str], conversation_id: str, message: str) -> None:
    """Persist a transient degraded notice for ``conversation_id``."""
    if not conversation_id:
        return
    try:
        from app.core.redis import get_redis

        redis = await get_redis()
        if redis is None:
            return
        await redis.set(_key(user_id, conversation_id), message, ex=_DEGRADED_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[sandbox] Failed to persist degraded notice for %s: %s", conversation_id, exc)


async def clear_sandbox_degraded(user_id: Optional[str], conversation_id: str) -> None:
    """Clear the degraded notice once the sandbox is available again."""
    if not conversation_id:
        return
    try:
        from app.core.redis import get_redis

        redis = await get_redis()
        if redis is None:
            return
        await redis.delete(_key(user_id, conversation_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[sandbox] Failed to clear degraded notice for %s: %s", conversation_id, exc)


async def get_sandbox_degraded(user_id: Optional[str], conversation_id: str) -> Optional[str]:
    """Return the degraded notice message, or None when the sandbox is healthy."""
    if not conversation_id:
        return None
    try:
        from app.core.redis import get_redis

        redis = await get_redis()
        if redis is None:
            return None
        value = await redis.get(_key(user_id, conversation_id))
        if value is None:
            return None
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="ignore")
        return str(value)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[sandbox] Failed to read degraded notice for %s: %s", conversation_id, exc)
        return None
