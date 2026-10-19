"""会话历史置顶偏好的后端契约。

覆盖三件事：

1. 清洗规则（空 ID / 去重 / 截断 / 非负轮次 / 上限 20）；
2. 全量偏好 PUT 在「不带该字段」时必须保留置顶（数据门户等旧调用方会全量回写），
   显式传空列表才是清空；
3. 专用端点只动置顶，不碰其它偏好；Redis 不可用时 503。
"""

import json

import pytest
from fastapi import HTTPException

from app.api.portal.endpoints import portal_prefs


pytestmark = pytest.mark.no_infrastructure


class FakeRedis:
    def __init__(self, value=None):
        self.value = value
        self.saved = value

    async def get(self, key):
        return self.value

    async def set(self, key, value):
        self.value = value
        self.saved = value


async def _resolved(value):
    return value


def user_info(user_id=7, role="user"):
    return {"user_id": user_id, "role": role}


def _pin(conversation_id, **overrides):
    payload = {
        "conversation_id": conversation_id,
        "title": f"标题 {conversation_id}",
        "created_at": "2026-10-15T09:22:00",
        "agent_name": "主助手(Main)",
        "turn_count": 3,
    }
    payload.update(overrides)
    return portal_prefs.PinnedConversation(**payload)


def test_normalize_drops_blank_ids_keeps_first_and_truncates():
    long_title = "题" * 260
    long_agent = "A" * 140
    raw = [
        _pin("  "),
        _pin("c1", title=long_title, agent_name=long_agent, turn_count=-5),
        _pin("c1", title="重复项必须被丢弃"),
        _pin("c2", created_at="x" * 80, turn_count=2),
    ]

    normalized = portal_prefs._normalize_pinned_conversations(raw)

    assert [item.conversation_id for item in normalized] == ["c1", "c2"]
    assert len(normalized[0].title) == 200
    assert len(normalized[0].agent_name) == 100
    assert normalized[0].turn_count == 0
    assert len(normalized[1].created_at) == 40


def test_normalize_caps_at_twenty_items():
    raw = [_pin(f"c{i}") for i in range(25)]

    normalized = portal_prefs._normalize_pinned_conversations(raw)

    assert len(normalized) == portal_prefs.MAX_PINNED_CONVERSATIONS == 20
    assert normalized[0].conversation_id == "c0"
    assert normalized[-1].conversation_id == "c19"


@pytest.mark.asyncio
async def test_legacy_payload_without_pins_reads_as_empty_list(monkeypatch):
    redis = FakeRedis(json.dumps({"pinned_group_ids": ["g1"], "markdown_theme": "academic"}))
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))

    result = await portal_prefs.get_portal_prefs(user_info())

    assert result["data"]["pinned_conversations"] == []
    assert result["data"]["pinned_group_ids"] == ["g1"]


@pytest.mark.asyncio
async def test_full_update_preserves_pins_when_field_is_omitted(monkeypatch):
    redis = FakeRedis(
        json.dumps(
            {
                "pinned_group_ids": ["g1"],
                "markdown_theme": "academic",
                "pinned_conversations": [
                    {
                        "conversation_id": "keep-me",
                        "title": "置顶会话",
                        "created_at": "2026-10-15T09:22:00",
                        "agent_name": "主助手(Main)",
                        "turn_count": 7,
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))

    await portal_prefs.update_portal_prefs(
        portal_prefs.PortalPrefsUpdate(pinned_group_ids=["g2"]),
        user_info(),
    )

    saved = json.loads(redis.saved)
    assert [item["conversation_id"] for item in saved["pinned_conversations"]] == ["keep-me"]
    assert saved["pinned_group_ids"] == ["g2"]


@pytest.mark.asyncio
async def test_full_update_replaces_pins_when_explicitly_provided(monkeypatch):
    redis = FakeRedis(json.dumps({"pinned_conversations": [{"conversation_id": "old"}]}))
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))

    await portal_prefs.update_portal_prefs(
        portal_prefs.PortalPrefsUpdate(pinned_conversations=[_pin("new")]),
        user_info(),
    )

    saved = json.loads(redis.saved)
    assert [item["conversation_id"] for item in saved["pinned_conversations"]] == ["new"]


@pytest.mark.asyncio
async def test_dedicated_endpoint_only_replaces_pins(monkeypatch):
    redis = FakeRedis(
        json.dumps(
            {
                "pinned_group_ids": ["portal-card"],
                "markdown_theme": "academic",
                "routing_mode": "expert",
                "expert_agent_id": "agent-1",
                "pinned_conversations": [{"conversation_id": "old"}],
            }
        )
    )
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))

    result = await portal_prefs.update_pinned_conversations(
        portal_prefs.PinnedConversationsUpdate(conversations=[_pin("a"), _pin("b")]),
        user_info(),
    )

    saved = json.loads(redis.saved)
    assert [item["conversation_id"] for item in saved["pinned_conversations"]] == ["a", "b"]
    # 其它偏好一字不动
    assert saved["pinned_group_ids"] == ["portal-card"]
    assert saved["markdown_theme"] == "academic"
    assert saved["routing_mode"] == "expert"
    assert saved["expert_agent_id"] == "agent-1"
    assert [item["conversation_id"] for item in result["data"]["pinned_conversations"]] == ["a", "b"]


@pytest.mark.asyncio
async def test_dedicated_endpoint_returns_503_without_redis(monkeypatch):
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(None))

    with pytest.raises(HTTPException) as exc_info:
        await portal_prefs.update_pinned_conversations(
            portal_prefs.PinnedConversationsUpdate(conversations=[_pin("a")]),
            user_info(),
        )

    assert exc_info.value.status_code == 503
