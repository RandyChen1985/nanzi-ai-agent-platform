import uuid

import pytest

from app.services.ai.runtime.conversation_run_cancel import release_conversation_run_locks

pytestmark = pytest.mark.no_infrastructure


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def delete(self, key):
        if key in self.store:
            del self.store[key]
            return 1
        return 0

    async def eval(self, script, numkeys, key, token):
        if self.store.get(key) == token:
            self.store.pop(key, None)
            return 1
        return 0

    async def scan_iter(self, match=None, count=50):
        prefix = (match or "").replace("*", "")
        for key in list(self.store.keys()):
            if key.startswith(prefix):
                yield key


@pytest.mark.asyncio
async def test_release_conversation_run_locks_clears_lane_and_session_locks(monkeypatch):
    fake = FakeRedis()
    conversation_id = f"conv-cancel-{uuid.uuid4().hex}"
    lane_key = f"nanzi:conv_run:u1:{conversation_id.replace(':', '_')}"
    session_key = f"conversation:u1:{conversation_id}:agent_lock:DataAgent"
    fake.store[lane_key] = "trace-1"
    fake.store[session_key] = "token-1"

    async def _redis():
        return fake

    monkeypatch.setattr("app.core.redis.get_redis", _redis)

    result = await release_conversation_run_locks(
        user_id="u1",
        conversation_id=conversation_id,
        trace_id="trace-1",
    )

    assert result["success"] is True
    assert result["lane_released"] is True
    assert result["session_locks_released"] == 1
    assert lane_key not in fake.store
    assert session_key not in fake.store


@pytest.mark.asyncio
async def test_release_conversation_run_locks_noop_without_conversation_id():
    result = await release_conversation_run_locks(
        user_id="u1",
        conversation_id=None,
    )
    assert result == {
        "success": False,
        "lane_released": False,
        "session_locks_released": 0,
    }
