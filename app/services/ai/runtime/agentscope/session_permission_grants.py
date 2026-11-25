"""会话级工具授权（「本会话总是允许」）的存储与判定。

只记录「某用户在某会话内已放行过哪些工具名」，不记录命令内容。

安全契约由调用点保证：本模块只在工具自身 ``check_permissions`` 的 ASK 出口被查询，
而禁用工具、命令黑名单、``dangerous`` 作用域、删除类 ``bypass_immune`` 决策全部在
此之前返回。因此本模块**不能**把 DENY 变成 ALLOW，只能把 ASK 降级为 ALLOW。

取不到用户身份或会话身份时一律 fail closed（不放行），避免把授权错配到别的会话。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from app.services.ai.conversation_identity import require_user_id
from app.services.ai.memory_service import memory_service

logger = logging.getLogger(__name__)

SESSION_GRANT_TTL_SECONDS = 24 * 60 * 60
SESSION_GRANT_KEY_TEMPLATE = "{prefix}:{user_id}:session_grant:{conversation_id}"

# 「允许这一次」的有效期：用户在卡片上点完之后，父会话要先续聊子智能体、子智能体
# 再跑到同一个调用，全程得来得及。给两分钟足够，过期就当没授权过重新问。
ONCE_GRANT_TTL_SECONDS = 120
ONCE_GRANT_KEY_TEMPLATE = "{prefix}:{user_id}:perm_once:{scope}:{tool_name}:{fingerprint}"


def delegation_grant_scope(
    parent_conversation_id: Any,
    agent_name: Any,
) -> str:
    """子智能体的授权作用域键；主会话返回空串。

    授权必须按「父会话 + 哪个子智能体」隔离：只按父会话记，会让 A 子代理拿到的
    放行顺带放行 B 子代理，用户看到的却是「我只授权了资产导出那一个」。
    """
    parent = str(parent_conversation_id or "").strip()
    if not parent:
        return ""
    agent = str(agent_name or "").strip()
    return f"{parent}::sub::{agent}"


def tool_args_fingerprint(tool_input: Any) -> str:
    """工具参数指纹：授权只对这一份参数生效，防止批了 `ls` 却放行 `rm`。"""
    try:
        payload = json.dumps(
            tool_input,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    except Exception:
        payload = str(tool_input)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class OncePermissionGrantStore:
    """一次性放行令牌：Redis 优先、进程内存兜底，消费即删。"""

    def __init__(self) -> None:
        self._memory_fallback: dict[str, float] = {}

    def _key(
        self,
        user_id: Any,
        scope: Any,
        tool_name: Any,
        fingerprint: Any,
    ) -> str | None:
        try:
            resolved_user = require_user_id(user_id)
        except Exception:
            return None
        resolved_scope = str(scope or "").strip()
        resolved_tool = str(tool_name or "").strip()
        resolved_fingerprint = str(fingerprint or "").strip()
        if not resolved_scope or not resolved_tool or not resolved_fingerprint:
            return None
        return ONCE_GRANT_KEY_TEMPLATE.format(
            prefix=memory_service.KEY_PREFIX,
            user_id=resolved_user,
            scope=resolved_scope,
            tool_name=resolved_tool,
            fingerprint=resolved_fingerprint,
        )

    async def grant(
        self,
        user_id: Any,
        scope: Any,
        tool_name: Any,
        fingerprint: Any,
    ) -> bool:
        key = self._key(user_id, scope, tool_name, fingerprint)
        if key is None:
            return False

        try:
            from app.core.redis import get_redis

            redis = await get_redis()
        except Exception as exc:  # pragma: no cover - 连接异常按不可用处理
            logger.warning("[OncePermissionGrant] Redis unavailable: %s", exc)
            redis = None

        if redis is None:
            # 没有 Redis 时退回进程内存，与 session grant 同口径：单实例仍可用。
            self._memory_fallback[key] = time.time() + ONCE_GRANT_TTL_SECONDS
            return True

        try:
            await redis.set(key, "1", ex=ONCE_GRANT_TTL_SECONDS)
            # 写入成功就清掉内存项，避免同一令牌在 Redis 与内存里各存一份。
            self._memory_fallback.pop(key, None)
            return True
        except Exception as exc:  # pragma: no cover - Redis 异常
            logger.warning("[OncePermissionGrant] Redis grant failed: %s", exc)
            # 跨进程读不到这条记录就别假装授权成功：卡片若显示「已允许」而续聊的
            # 子智能体在另一个 worker 上仍被拦，用户会以为平台坏了。
            self._memory_fallback.pop(key, None)
            return False

    async def consume(
        self,
        user_id: Any,
        scope: Any,
        tool_name: Any,
        fingerprint: Any,
    ) -> bool:
        """取用令牌；命中即删除，返回是否放行。"""
        key = self._key(user_id, scope, tool_name, fingerprint)
        if key is None:
            return False

        try:
            from app.core.redis import get_redis

            redis = await get_redis()
            if redis:
                # GETDEL 原子取删：并发两个同名调用时只有一个能拿到令牌，
                # 不会出现「一次授权放行两次执行」。
                removed = await redis.getdel(key)
                return bool(removed)
        except Exception as exc:  # pragma: no cover - Redis 异常走内存兜底
            logger.warning("[OncePermissionGrant] Redis consume failed: %s", exc)

        expires_at = self._memory_fallback.pop(key, None)
        if expires_at is None:
            return False
        return expires_at > time.time()

    def clear(self) -> None:
        self._memory_fallback.clear()


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
                # 到期时间续回 24h（设计口径是「写入时设置、命中不续期」）。
                #
                # 判断依据必须是「这个 Set 有没有 TTL」而不是 SADD 的返回值：SADD 返回
                # 的是本次新加入的元素数，工具 B 此前不在集合里时就返回 1——无论 key
                # 是否早已存在，于是每加一个工具都会把同一会话下所有既有授权续期。
                if added and await redis.ttl(key) < 0:
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
once_permission_grants = OncePermissionGrantStore()

__all__ = [
    "ONCE_GRANT_TTL_SECONDS",
    "SESSION_GRANT_TTL_SECONDS",
    "OncePermissionGrantStore",
    "SessionPermissionGrantStore",
    "delegation_grant_scope",
    "once_permission_grants",
    "session_permission_grants",
    "tool_args_fingerprint",
]
