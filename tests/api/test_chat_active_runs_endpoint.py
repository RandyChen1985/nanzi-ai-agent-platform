import pytest

from app.api.v1.endpoints.chat import list_active_conversation_runs
from app.services.ai.runtime.session_run_lane import ConversationRunLane

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_active_runs_endpoint_passes_user_id_and_returns_items(monkeypatch):
    """端点必须用稳定用户 ID 查锁，并把 lane 的快照原样封装成标准响应。"""
    seen: dict[str, object] = {}

    async def _list_active(self, *, user_id):
        seen["user_id"] = user_id
        return {
            "ttl_seconds": 600,
            "stale_before_ttl_seconds": 400,
            "items": [
                {
                    "conversation_id": "conv-1",
                    "trace_id": None,
                    "ttl_seconds": 512,
                    "state": "active",
                },
                {
                    "conversation_id": "conv-2",
                    "trace_id": None,
                    "ttl_seconds": 30,
                    "state": "stale",
                },
            ],
        }

    monkeypatch.setattr(ConversationRunLane, "list_active", _list_active)

    response = await list_active_conversation_runs(user_info={"user_id": "u-1"})

    assert seen["user_id"] == "u-1"
    assert response.code == 200
    assert response.data.ttl_seconds == 600
    assert response.data.stale_before_ttl_seconds == 400
    assert [item.conversation_id for item in response.data.items] == ["conv-1", "conv-2"]
    assert [item.state for item in response.data.items] == ["active", "stale"]


@pytest.mark.asyncio
async def test_active_runs_endpoint_defaults_to_empty_items(monkeypatch):
    """lane 返回空快照时也要给出可渲染的结构，而不是 null。"""

    async def _list_active(self, *, user_id):
        return {"ttl_seconds": 600, "stale_before_ttl_seconds": 400, "items": []}

    monkeypatch.setattr(ConversationRunLane, "list_active", _list_active)

    response = await list_active_conversation_runs(user_info={"user_id": "u-2"})

    assert response.data.items == []
