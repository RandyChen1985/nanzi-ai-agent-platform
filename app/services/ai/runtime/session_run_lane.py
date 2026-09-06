from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

logger = logging.getLogger(__name__)

from app.services.ai.conversation_identity import require_user_id

DEFAULT_LOCK_TTL_SECONDS = 600
DEFAULT_WAIT_SECONDS = 0.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.1
# 追问等待模式（不是严格 FIFO 队列）：
#   reject   —— 会话繁忙时立即拒绝（旧行为）。
#   followup —— 有界等待当前 run 结束后再处理（改善"再搜一下"等连续追问体验）。
DEFAULT_FOLLOWUP_WAIT_MODE = "followup"
DEFAULT_FOLLOWUP_WAIT_SECONDS = 30.0


class ConversationRunBusyError(RuntimeError):
    """Raised when a conversation already has an active agent run."""


class ConversationRunLane:
    """Serialize agent turns per (user_id, conversation_id)."""

    def _lock_key(self, user_id: str | int | None, conversation_id: str) -> str:
        uid = require_user_id(user_id)
        safe_cid = conversation_id.replace(":", "_")
        return f"nanzi:conv_run:{uid}:{safe_cid}"

    @staticmethod
    def _safe_trace_id(value: object) -> str | None:
        text = str(value or "").strip()
        if not text or len(text) > 128:
            return None
        try:
            uuid.UUID(text)
        except (TypeError, ValueError, AttributeError):
            return None
        return text

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

    async def _followup_wait_mode(self) -> str:
        from app.services.config_service import ConfigService

        raw = await ConfigService.get("agent_session_followup_wait_mode", None)
        if raw is None:
            # Backward compatibility for configs created while this was named "queue mode".
            raw = await ConfigService.get(
                "agent_session_queue_mode",
                DEFAULT_FOLLOWUP_WAIT_MODE,
            )
        mode = str(raw or "").strip().lower()
        return mode if mode in {"reject", "followup"} else DEFAULT_FOLLOWUP_WAIT_MODE

    async def _followup_wait_seconds(self) -> float:
        from app.services.config_service import ConfigService

        raw = await ConfigService.get("agent_session_followup_wait_seconds", None)
        if raw is None:
            raw = await ConfigService.get(
                "agent_session_queue_followup_wait_seconds",
                str(DEFAULT_FOLLOWUP_WAIT_SECONDS),
            )
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return DEFAULT_FOLLOWUP_WAIT_SECONDS

    async def _effective_wait_seconds(self) -> float:
        """根据追问等待模式解析默认等待时长（未显式传入 wait_seconds 时使用）。

        - 若显式配置 ``agent_session_run_lock_wait_seconds`` > 0，则优先采用（向后兼容/覆盖）。
        - 否则按追问等待模式：reject → 0；followup → 有界等待当前 run 结束。
        """
        explicit = await self._wait_seconds()
        if explicit > 0:
            return explicit
        if await self._followup_wait_mode() == "reject":
            return 0.0
        return await self._followup_wait_seconds()

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
        wait = wait_seconds if wait_seconds is not None else await self._effective_wait_seconds()
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
                # 既然本 run 已独占会话 lane，任何残留的 per-agent 会话锁都必然是
                # 上次被中断/崩溃 run 的孤儿锁。主动清掉它们，避免后续 executor 在
                # 本应立即可用的 agent_lock 上干等 wait_seconds（“双层会话锁叠加”
                # 削弱并发吞吐的主因）。此处是尽力而为，失败不影响主流程。
                await self._clear_stale_agent_locks(user_id, conversation_id)
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

    async def _clear_stale_agent_locks(
        self,
        user_id: str | int | None,
        conversation_id: str | None,
    ) -> None:
        """尽力清除该会话遗留的 per-agent 会话锁（孤儿锁）。

        进入 conversation_run_lane 即代表当前会话已无其他合法 run 在运行，因此
        同会话内仍存在的 per-agent session_lock 必然是上次被中断 run 的残留。
        不清除的话，本 run 内的 executor 会在 agentscope_session_lock.hold 上
        空等至 wait_seconds，造成本可避免的延迟（“双层会话锁叠加”）。
        """
        if not conversation_id or not user_id:
            return
        try:
            from app.services.ai.runtime.agentscope.session_lock import (
                agentscope_session_lock,
            )

            released = await agentscope_session_lock.force_release_all_for_conversation(
                user_id=user_id,
                conversation_id=conversation_id,
            )
            if released:
                logger.info(
                    "[ConversationRunLane] cleared %d stale agent session lock(s) "
                    "for conversation=%s",
                    released,
                    conversation_id,
                )
        except Exception as exc:
            logger.warning(
                "[ConversationRunLane] failed to clear stale agent session locks: %s",
                exc,
            )

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

    async def force_release(
        self,
        *,
        user_id: str | int | None,
        conversation_id: str | None,
    ) -> bool:
        """Delete the run-lane lock regardless of holder token (client cancel)."""
        if not conversation_id:
            return False

        from app.core.redis import get_redis

        redis = await get_redis()
        if redis is None:
            return False

        key = self._lock_key(user_id, conversation_id)
        try:
            deleted = await redis.delete(key)
            return bool(deleted)
        except Exception as exc:
            logger.warning("[ConversationRunLane] force_release failed: %s", exc)
            return False

    async def is_locked(
        self,
        *,
        user_id: str | int | None,
        conversation_id: str | None,
    ) -> bool:
        """Check whether the conversation run lane currently holds an active lock."""
        if not conversation_id or not await self._is_enabled():
            return False

        from app.core.redis import get_redis

        redis = await get_redis()
        if redis is None:
            return False

        key = self._lock_key(user_id, conversation_id)
        try:
            return bool(await redis.exists(key))
        except Exception as exc:
            logger.warning("[ConversationRunLane] is_locked check failed: %s", exc)
            return False

    async def get_status(
        self,
        *,
        user_id: str | int | None,
        conversation_id: str | None,
    ) -> dict[str, object | None]:
        """读取当前会话运行状态，不改变锁，也不暴露锁的内部实现细节。"""
        empty = {"active": False, "trace_id": None, "ttl_seconds": None}
        if not conversation_id:
            return empty
        try:
            enabled = await self._is_enabled()
        except Exception as exc:
            logger.warning("[ConversationRunLane] get_status config check failed: %s", exc)
            return empty
        if not enabled:
            return empty

        from app.core.redis import get_redis

        try:
            redis = await get_redis()
        except Exception as exc:
            logger.warning("[ConversationRunLane] get_status Redis unavailable: %s", exc)
            return empty
        if redis is None:
            return empty

        key = self._lock_key(user_id, conversation_id)
        try:
            raw_token = await redis.get(key)
            if raw_token is None:
                return empty
            if isinstance(raw_token, bytes):
                raw_token = raw_token.decode("utf-8", errors="ignore")
            token = str(raw_token)
            # 锁值只允许返回服务端 UUID trace token；异常/非预期 Redis 内容不回传给客户端。
            trace_id = self._safe_trace_id(token)
            ttl_seconds = None
            ttl_reader = getattr(redis, "ttl", None)
            if ttl_reader is not None:
                raw_ttl = await ttl_reader(key)
                try:
                    parsed_ttl = int(raw_ttl)
                    if parsed_ttl >= 0:
                        ttl_seconds = parsed_ttl
                except (TypeError, ValueError):
                    pass
            return {"active": True, "trace_id": trace_id, "ttl_seconds": ttl_seconds}
        except Exception as exc:
            logger.warning("[ConversationRunLane] get_status failed: %s", exc)
            return empty

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
