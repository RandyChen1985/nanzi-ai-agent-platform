"""跨轮子代理会话缓存：让「用户回答后 resume」真的接得上。

续聊缓存原先只挂在 ``AgentContext`` 上，而用户回答提问卡是**新的一轮请求**——
ctx 重建后缓存就没了。于是父智能体照提示词调 ``resume=True``，每次都降级成
全新会话，用户答完还得把条件重讲一遍。这里锁住 Redis 层的行为：

1. 存取往返；
2. 必带 TTL（不能无限期留着对话内容）；
3. 缺 user/conversation 身份时**拒绝落库**，否则缓存会串会话；
4. 不同会话、不同子代理互不串味；
5. Redis 故障时静默降级，不能把委派整块打挂。
"""
from __future__ import annotations

import pytest

from app.services.ai.delegation_session_store import DelegationSessionStore

pytestmark = pytest.mark.no_infrastructure


class _FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}
        self.ttl: dict[str, int | None] = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value
        self.ttl[key] = ex

    async def delete(self, key):
        self.data.pop(key, None)


HISTORY = [{"role": "user", "content": "帮我查上海到北京的火车票"}]


@pytest.mark.asyncio
async def test_save_then_load_roundtrip_sets_ttl():
    redis = _FakeRedis()
    store = DelegationSessionStore(redis)

    await store.save(
        user_id=7, conversation_id="c1", agent_key="huochepiao", history=HISTORY
    )
    loaded = await store.load(
        user_id=7, conversation_id="c1", agent_key="huochepiao"
    )

    assert loaded == HISTORY
    assert redis.ttl, "缓存必须设置 TTL，否则对话内容会永久留在 Redis 里"
    assert all(value == store.ttl_seconds for value in redis.ttl.values())


@pytest.mark.asyncio
async def test_missing_identity_skips_persistence():
    """没有 user/conversation 时不能落库，否则缓存会串到别的会话。"""
    redis = _FakeRedis()
    store = DelegationSessionStore(redis)

    await store.save(user_id=None, conversation_id="", agent_key="k", history=HISTORY)
    await store.save(user_id=7, conversation_id="", agent_key="k", history=HISTORY)

    assert redis.data == {}
    assert await store.load(user_id=None, conversation_id="", agent_key="k") == []


@pytest.mark.asyncio
async def test_different_conversation_and_agent_do_not_share():
    redis = _FakeRedis()
    store = DelegationSessionStore(redis)

    await store.save(user_id=7, conversation_id="c1", agent_key="a", history=HISTORY)

    assert await store.load(user_id=7, conversation_id="c2", agent_key="a") == []
    assert await store.load(user_id=7, conversation_id="c1", agent_key="b") == []
    assert await store.load(user_id=8, conversation_id="c1", agent_key="a") == []


@pytest.mark.asyncio
async def test_redis_failure_degrades_silently():
    """Redis 挂了只能丢缓存，不能让委派本身失败。"""

    class _Boom:
        async def get(self, key):
            raise RuntimeError("redis down")

        async def set(self, *args, **kwargs):
            raise RuntimeError("redis down")

        async def delete(self, key):
            raise RuntimeError("redis down")

    store = DelegationSessionStore(_Boom())

    await store.save(user_id=7, conversation_id="c1", agent_key="k", history=HISTORY)
    assert await store.load(user_id=7, conversation_id="c1", agent_key="k") == []


@pytest.mark.asyncio
async def test_corrupted_payload_is_ignored():
    redis = _FakeRedis()
    store = DelegationSessionStore(redis)
    await store.save(user_id=7, conversation_id="c1", agent_key="k", history=HISTORY)
    key = next(iter(redis.data))
    redis.data[key] = "{not-json"

    assert await store.load(user_id=7, conversation_id="c1", agent_key="k") == []


@pytest.mark.asyncio
async def test_without_redis_client_is_a_no_op():
    store = DelegationSessionStore(None)

    await store.save(user_id=7, conversation_id="c1", agent_key="k", history=HISTORY)
    assert await store.load(user_id=7, conversation_id="c1", agent_key="k") == []


def test_ttl_does_not_outlive_the_pending_question_window():
    """缓存不能比提问记录活得久。

    提问记录的 TTL 决定「用户还能不能提交答案」；缓存留得比它长只会造成错位——
    用户点了选项、回执却已被判过期，而那份缓存还躺在 Redis 里没人用。
    """
    from app.services.ai.user_question_store import UserQuestionStore

    assert (
        DelegationSessionStore.DEFAULT_TTL_SECONDS
        <= UserQuestionStore.DEFAULT_TTL_SECONDS
    )
