from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.services.ai.conversation_identity import require_user_id
from app.services.ai.runtime.lock_renewal import renew_lock_during_hold

logger = logging.getLogger(__name__)

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

    @staticmethod
    def _glob_escape(value: str) -> str:
        """转义 Redis glob（SCAN MATCH）元字符。

        uid 可能来自用户名等外部输入，若含 `*` / `?` / `[` / `]` / `\\`，未转义的
        MATCH 模式会扩散到其他用户的会话键，把别人的 conversation_id 列出来。
        """
        escaped: list[str] = []
        for char in str(value):
            if char in "*?[]\\":
                escaped.append("\\")
            escaped.append(char)
        return "".join(escaped)

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
                # 刻意不在拿到 lane 后自动清理同会话的 per-agent 会话锁：单独凭借
                # “取得了 lane”无法证明那些 agent_lock 属于已终止的 run——正常 run
                # 持锁期间，若其 lane 因续约故障 / 进程暂停 / 被显式取消而先行失效，
                # 旧 run 的 executor 仍可能握着自己 agent_lock 在跑。此时无条件清理会
                # 误删活跃锁、破坏互斥并并发操作同一 AgentState。孤儿 agent_lock 依靠
                # 自身 TTL 过期自愈；显式取消路径（force_release_all_for_conversation）
                # 已在任务停止后负责删除对应锁。
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

    async def list_active(
        self,
        *,
        user_id: str | int | None,
    ) -> dict[str, object]:
        """列出该用户当前仍持有 lane 锁的会话（纯只读，不写不删任何 key）。

        `state` 判定：lane 锁由 `renew_lock_during_hold` 以 `ttl/3` 为间隔续约，
        所以正常活跃的锁，其剩余 TTL 必然落在 `(2/3·ttl, ttl]`；剩余低于
        `ttl/2` 说明续约已连续错过（进程崩溃 / pod 被杀 / 任务卡死），判为
        `stale`（疑似僵死或孤儿锁）。阈值刻意不贴 `2/3·ttl`——那与正常下限零
        距离，一次事件循环调度抖动就会误报。响应里的 `stale_before_ttl_seconds`
        即该阈值，前端不要自行推算。
        """
        try:
            enabled = await self._is_enabled()
        except Exception as exc:
            logger.warning("[ConversationRunLane] list_active config check failed: %s", exc)
            enabled = False

        ttl_seconds = DEFAULT_LOCK_TTL_SECONDS
        try:
            ttl_seconds = await self._ttl_seconds()
        except Exception as exc:
            logger.warning("[ConversationRunLane] list_active ttl read failed: %s", exc)

        # 续约间隔是 ttl/3，正常活跃锁的剩余 TTL 落在 (2/3·ttl, ttl]；阈值取一半，
        # 留出半个续约周期的余量。若阈值贴着 2/3·ttl，流式输出/工具密集期的一次
        # 事件循环调度延迟（sleep 醒来 → pexpire 之间）就足以把正在跑的会话标成
        # 「疑似僵死」，而用户对该条目点「强制释放」是硬取消（存在 handle 时
        # request_stop 会 task.cancel()）——会误杀正常运行的 run。
        stale_before = max(1, int(ttl_seconds * 0.5))
        empty: dict[str, object] = {
            "ttl_seconds": ttl_seconds,
            "stale_before_ttl_seconds": stale_before,
            "items": [],
        }
        if not enabled:
            return empty

        from app.core.redis import get_redis

        try:
            redis = await get_redis()
        except Exception as exc:
            logger.warning("[ConversationRunLane] list_active Redis unavailable: %s", exc)
            return empty
        if redis is None:
            return empty

        uid = require_user_id(user_id)
        # SCAN 的 match 是 glob 模式：uid 里的元字符必须先转义，否则模式会扩散到
        # 别人的会话键。match 只是粗筛，uid 段仍要逐条二次校验。
        key_prefix = "nanzi:conv_run:"
        items: list[dict[str, object]] = []
        try:
            async for raw_key in redis.scan_iter(
                match=f"{key_prefix}{self._glob_escape(uid)}:*", count=200
            ):
                key = (
                    raw_key.decode("utf-8", errors="ignore")
                    if isinstance(raw_key, bytes)
                    else str(raw_key)
                )
                # 二次校验 uid 段：不能把匹配结果当作身份边界。conversation_id 用
                # partition 取整段（acquire 把 cid 里的 ":" 归一化为 "_"，当前 cid
                # 本身是 UUID）
                key_uid, sep, conversation_id = key[len(key_prefix):].partition(":")
                if not sep or key_uid != uid or not conversation_id:
                    continue

                raw_token = await redis.get(key)
                if raw_token is None:
                    continue
                if isinstance(raw_token, bytes):
                    raw_token = raw_token.decode("utf-8", errors="ignore")

                remaining: int | None = None
                ttl_reader = getattr(redis, "ttl", None)
                if ttl_reader is not None:
                    try:
                        parsed_ttl = int(await ttl_reader(key))
                    except (TypeError, ValueError):
                        parsed_ttl = -1
                    if parsed_ttl == -2:
                        # key 已过期，跳过
                        continue
                    # -1（无过期，理论不该出现）保守按活跃处理
                    remaining = parsed_ttl if parsed_ttl >= 0 else None

                state = "active" if remaining is None or remaining >= stale_before else "stale"
                items.append(
                    {
                        "conversation_id": conversation_id,
                        # 锁值只回传服务端 UUID trace token，异常内容一律丢弃
                        "trace_id": self._safe_trace_id(raw_token),
                        "ttl_seconds": remaining,
                        "state": state,
                    }
                )
        except Exception as exc:
            logger.warning("[ConversationRunLane] list_active scan failed: %s", exc)
            return empty

        def _sort_key(item: dict[str, object]) -> tuple[int, int]:
            ttl_value = item.get("ttl_seconds")
            return (
                0 if item.get("state") == "active" else 1,
                ttl_value if isinstance(ttl_value, int) else 10 ** 9,
            )

        items.sort(key=_sort_key)
        return {
            "ttl_seconds": ttl_seconds,
            "stale_before_ttl_seconds": stale_before,
            "items": items,
        }

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
        effective_ttl = (
            ttl_seconds
            if ttl_seconds is not None
            else await self._ttl_seconds()
        )
        renewal = asyncio.create_task(
            renew_lock_during_hold(
                key=key,
                token=token,
                ttl_seconds=effective_ttl,
            )
        )
        try:
            yield True
        finally:
            renewal.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renewal
            await self.release(key, token)


conversation_run_lane = ConversationRunLane()
