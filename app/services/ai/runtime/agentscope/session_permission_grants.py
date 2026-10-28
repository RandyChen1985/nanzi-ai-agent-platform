"""会话级工具授权（「本会话总是允许」）的存储与判定。

只记录「某用户在某会话内已放行过哪些工具名」，不记录命令内容。

安全契约由调用点保证：本模块只在工具自身 ``check_permissions`` 的 ASK 出口被查询，
而禁用工具、命令黑名单、``dangerous`` 作用域、删除类 ``bypass_immune`` 决策全部在
此之前返回。因此本模块**不能**把 DENY 变成 ALLOW，只能把 ASK 降级为 ALLOW。

取不到用户身份或会话身份时一律 fail closed（不放行），避免把授权错配到别的会话。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.services.ai.conversation_identity import require_user_id
from app.services.ai.memory_service import memory_service

logger = logging.getLogger(__name__)

SESSION_GRANT_TTL_SECONDS = 24 * 60 * 60
SESSION_GRANT_KEY_TEMPLATE = "{prefix}:{user_id}:session_grant:{conversation_id}"


class SessionPermissionGrantStore:
    """Redis 优先、进程内存兜底的会话级工具授权表。"""

    def __init__(self) -> None:
        # conversation key -> {tool_name: expires_at}
        self._memory_fallback: dict[str, dict[str, float]] = {}

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _normalize_tool_name(tool_name: Any) -> str:
        return str(tool_name or "").strip()

    def _identity(self, user_id: Any, conversation_id: Any) -> tuple[str, str] | None:
        try:
            resolved_user = require_user_id(user_id)
        except Exception:
            return None
        resolved_conversation = str(conversation_id or "").strip()
        if not resolved_conversation:
            return None
        return resolved_user, resolved_conversation

    def key_for(self, user_id: Any, conversation_id: Any) -> str | None:
        identity = self._identity(user_id, conversation_id)
        if identity is None:
            return None
        resolved_user, resolved_conversation = identity
        return SESSION_GRANT_KEY_TEMPLATE.format(
            prefix=memory_service.KEY_PREFIX,
            user_id=resolved_user,
            conversation_id=resolved_conversation,
        )

    def _memory_bucket(self, key: str, *, now: float) -> dict[str, float]:
        bucket = self._memory_fallback.get(key) or {}
        for tool_name, expires_at in list(bucket.items()):
            if expires_at <= now:
                bucket.pop(tool_name, None)
        if bucket:
            self._memory_fallback[key] = bucket
        else:
            self._memory_fallback.pop(key, None)
        return bucket

    # ------------------------------------------------------------------ write

    async def grant(
        self,
        user_id: Any,
        conversation_id: Any,
        tool_name: Any,
    ) -> bool:
        """写入一条会话级放行记录；返回是否写入成功。"""

        normalized = self._normalize_tool_name(tool_name)
        key = self.key_for(user_id, conversation_id)
        if not normalized or key is None:
            return False

        now = time.time()
        bucket = self._memory_bucket(key, now=now)
        bucket[normalized] = now + SESSION_GRANT_TTL_SECONDS
        self._memory_fallback[key] = bucket

        try:
            from app.core.redis import get_redis

            redis = await get_redis()
            if redis:
                added = int(await redis.sadd(key, normalized) or 0)
                # TTL 只在该 key 第一次创建时设置：给工具 B 授权不应该把工具 A 的
                # 到期时间续回 24h（设计口径是「写入时设置，命中不续期」）。
                if added:
                    await redis.expire(key, SESSION_GRANT_TTL_SECONDS)
                return True
        except Exception as exc:  # pragma: no cover - Redis 异常走内存兜底
            logger.warning(
                "[SessionPermissionGrant] Redis grant failed, falling back to memory: %s",
                exc,
            )
            # 内存兜底只在本进程内可见；跨进程读不到这条记录，如实返回 False，
            # 让前端显示「已允许本次执行」而不是一个换实例就失效的「本会话」承诺。
            return False
        return True

    async def revoke(
        self,
        user_id: Any,
        conversation_id: Any,
        tool_name: Any,
    ) -> bool:
        """删除一条放行记录；返回是否有记录被删除。"""

        normalized = self._normalize_tool_name(tool_name)
        key = self.key_for(user_id, conversation_id)
        if not normalized or key is None:
            return False

        bucket = self._memory_bucket(key, now=time.time())
        existed = normalized in bucket
        bucket.pop(normalized, None)
        if bucket:
            self._memory_fallback[key] = bucket
        else:
            self._memory_fallback.pop(key, None)

        try:
            from app.core.redis import get_redis

            redis = await get_redis()
            if redis:
                removed = int(await redis.srem(key, normalized) or 0)
                return bool(removed) or existed
        except Exception as exc:  # pragma: no cover - Redis 异常走内存兜底
            logger.warning(
                "[SessionPermissionGrant] Redis revoke failed: %s",
                exc,
            )
        return existed

    # ------------------------------------------------------------------- read

    async def is_granted(
        self,
        user_id: Any,
        conversation_id: Any,
        tool_name: Any,
    ) -> bool:
        """该会话是否已放行该工具。身份缺失、工具名为空一律返回 False。"""

        normalized = self._normalize_tool_name(tool_name)
        key = self.key_for(user_id, conversation_id)
        if not normalized or key is None:
            return False

        # Redis 是权威来源：撤销要让所有进程立刻生效，所以内存兜底只在 Redis
        # 不可用时才参与判定，不能当成一级缓存（否则别的实例撤销后本进程仍放行）。
        try:
            from app.core.redis import get_redis

            redis = await get_redis()
            if redis:
                return bool(await redis.sismember(key, normalized))
        except Exception as exc:  # pragma: no cover - Redis 异常走内存兜底
            logger.warning(
                "[SessionPermissionGrant] Redis lookup failed: %s",
                exc,
            )
        return normalized in self._memory_bucket(key, now=time.time())

    async def list_grants(
        self,
        user_id: Any,
        conversation_id: Any,
    ) -> list[str]:
        key = self.key_for(user_id, conversation_id)
        if key is None:
            return []

        # 与 is_granted 同口径：Redis 可用时它是唯一权威来源，避免别的实例撤销后
        # 本进程的内存兜底继续把已撤销的工具列出来。
        try:
            from app.core.redis import get_redis

            redis = await get_redis()
            if redis:
                raw_names = await redis.smembers(key)
                resolved: set[str] = set()
                for item in raw_names or ():
                    name = item.decode("utf-8") if isinstance(item, bytes) else str(item)
                    name = name.strip()
                    if name:
                        resolved.add(name)
                return sorted(resolved)
        except Exception as exc:  # pragma: no cover - Redis 异常走内存兜底
            logger.warning(
                "[SessionPermissionGrant] Redis list failed: %s",
                exc,
            )
        return sorted(self._memory_bucket(key, now=time.time()).keys())

    def clear(self) -> None:
        self._memory_fallback.clear()


session_permission_grants = SessionPermissionGrantStore()

__all__ = [
    "SESSION_GRANT_TTL_SECONDS",
    "SessionPermissionGrantStore",
    "session_permission_grants",
]
