"""跨轮保留子代理会话历史，让「用户回答后 resume」真的接得上。

续聊缓存原本只挂在 ``AgentContext`` 上，而用户回答提问卡是**新一轮请求**
（ctx 重建），缓存随之清空——父照提示词调 ``resume=True``，却每次都降级成
全新会话，用户答完问题还得把条件重讲一遍。这里把缓存按
``(user, conversation, agent)`` 落到 Redis，并带 TTL 兜住内存与隐私。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class DelegationSessionStore:
    """按会话维度缓存子代理历史，供下一轮 ``resume=true`` 复用。

    传入 ``redis_client=None`` 时所有操作退化为 no-op（进程内缓存由调用方
    ``AgentContext.delegation_session_histories`` 负责），这样 Redis 未配置或
    单元测试里不需要额外桩件。
    """

    KEY_PREFIX = "ai:delegation-session"
    #: 与 ``UserQuestionStore.DEFAULT_TTL_SECONDS``(900) 对齐：提问记录的存活时间
    #: 才是「用户还能提交答案」的真实窗口。缓存留得更久只会造成错位——用户点了
    #: 选项、回执却已被判过期，而缓存还躺在 Redis 里没人用。
    DEFAULT_TTL_SECONDS = 900

    def __init__(
        self,
        redis_client: Any = None,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ):
        self.redis_client = redis_client
        self.ttl_seconds = max(60, int(ttl_seconds))

    @classmethod
    async def from_runtime(cls) -> "DelegationSessionStore":
        """从运行时依赖构造；拿不到 Redis 时返回 no-op 实例而不是抛错。"""
        try:
            from app.core.redis import get_redis

            return cls(await get_redis())
        except Exception:  # noqa: BLE001 - Redis 不可用时只丢缓存，不影响委派
            logger.warning("获取 Redis 失败，子代理会话缓存退化为进程内", exc_info=True)
            return cls(None)

    @classmethod
    def _key(cls, user_id: str, conversation_id: str, agent_key: str) -> str:
        return f"{cls.KEY_PREFIX}:{user_id}:{conversation_id}:{agent_key}"

    @staticmethod
    def _identity(
        *, user_id: Any, conversation_id: Any
    ) -> tuple[str, str] | None:
        # 缺任一身份都不落库：缓存必须严格绑在一个会话上，否则会串到别人那里。
        uid = "" if user_id is None else str(user_id).strip()
        cid = "" if conversation_id is None else str(conversation_id).strip()
        if not uid or not cid:
            return None
        return uid, cid

    async def load(
        self,
        *,
        user_id: Any,
        conversation_id: Any,
        agent_key: str,
    ) -> List[Dict[str, str]]:
        identity = self._identity(user_id=user_id, conversation_id=conversation_id)
        if identity is None or self.redis_client is None:
            return []
        key = self._key(identity[0], identity[1], agent_key)
        try:
            raw = await self.redis_client.get(key)
        except Exception:  # noqa: BLE001 - 读不到就当没有缓存
            logger.warning("读取子代理会话缓存失败 key=%s", key, exc_info=True)
            return []
        if not raw:
            return []
        try:
            history = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("子代理会话缓存内容损坏，已忽略 key=%s", key)
            return []
        if not isinstance(history, list):
            return []
        return [item for item in history if isinstance(item, dict)]

    async def save(
        self,
        *,
        user_id: Any,
        conversation_id: Any,
        agent_key: str,
        history: List[Dict[str, str]],
    ) -> None:
        identity = self._identity(user_id=user_id, conversation_id=conversation_id)
        if identity is None or self.redis_client is None:
            return
        key = self._key(identity[0], identity[1], agent_key)
        try:
            await self.redis_client.set(
                key,
                json.dumps(list(history), ensure_ascii=False),
                ex=self.ttl_seconds,
            )
        except Exception:  # noqa: BLE001 - 写不进缓存只影响续聊体验，不能让委派失败
            logger.warning("保存子代理会话缓存失败 key=%s", key, exc_info=True)
