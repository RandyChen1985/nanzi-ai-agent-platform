"""EmbedChat 功能引导进度：账号级偏好字段与专用更新端点。"""

import json

import pytest
from pydantic import ValidationError

from app.api.portal.endpoints import portal_prefs


pytestmark = pytest.mark.no_infrastructure


class FakeRedis:
    def __init__(self, value=None):
        self.value = value
        self.saved = None

    async def get(self, key):
        return self.value

    async def set(self, key, value):
        self.value = value
        self.saved = value


async def _resolved(value):
    return value


def user_info(user_id=7):
    return {"user_id": user_id, "role": "user"}


def test_embed_tour_fields_default_to_unstarted():
    prefs = portal_prefs.PortalPrefs()

    assert prefs.embed_tour_version == 0
    assert prefs.embed_tour_step == 0
    assert prefs.embed_tour_completed_at == ""


@pytest.mark.asyncio
async def test_update_embed_tour_preserves_other_preferences(monkeypatch):
    redis = FakeRedis(
        json.dumps(
            {
                "pinned_group_ids": ["group-1"],
                "markdown_theme": "academic",
                "embed_tour_version": 1,
                "embed_tour_step": 2,
            }
        )
    )
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))

    result = await portal_prefs.update_embed_tour(
        portal_prefs.EmbedTourUpdate(version=1, step=5),
        user_info(),
    )

    saved = json.loads(redis.saved)
    assert result["data"] == {
        "embed_tour_version": 1,
        "embed_tour_step": 5,
        "embed_tour_completed_at": "",
        "embed_tour_skipped_at": "",
    }
    assert saved["pinned_group_ids"] == ["group-1"]
    assert saved["markdown_theme"] == "academic"
    assert saved["embed_tour_step"] == 5


@pytest.mark.asyncio
async def test_update_embed_tour_marks_completion(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))

    result = await portal_prefs.update_embed_tour(
        portal_prefs.EmbedTourUpdate(version=1, step=10, completed_at="2026-10-22T20:15:00+08:00"),
        user_info(),
    )

    saved = json.loads(redis.saved)
    assert result["data"]["embed_tour_step"] == 10
    assert saved["embed_tour_completed_at"] == "2026-10-22T20:15:00+08:00"


def test_embed_tour_step_rejects_out_of_range():
    # 10 是「已完成」，11 及以上越界
    assert portal_prefs.EmbedTourUpdate(version=1, step=10).step == 10
    with pytest.raises(ValidationError):
        portal_prefs.EmbedTourUpdate(version=1, step=11)
    with pytest.raises(ValidationError):
        portal_prefs.EmbedTourUpdate(version=-1, step=0)


def test_embed_tour_completed_at_is_trimmed():
    body = portal_prefs.EmbedTourUpdate(
        version=1,
        step=8,
        completed_at="  " + "x" * 80 + "  ",
    )

    assert len(body.completed_at) == 40
    assert not body.completed_at.startswith(" ")


@pytest.mark.asyncio
async def test_full_preference_update_keeps_embed_tour_state(monkeypatch):
    """旧页面全量回写偏好时，不能把「已看完」冲回未完成。"""
    redis = FakeRedis(
        json.dumps(
            {
                "embed_tour_version": 1,
                "embed_tour_step": 10,
                "embed_tour_completed_at": "2026-10-22T20:15:00+08:00",
            }
        )
    )
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))

    await portal_prefs.update_portal_prefs(
        portal_prefs.PortalPrefsUpdate(pinned_group_ids=["group-1"]),
        user_info(),
    )

    saved = json.loads(redis.saved)
    assert saved["embed_tour_version"] == 1
    assert saved["embed_tour_step"] == 10
    assert saved["embed_tour_completed_at"] == "2026-10-22T20:15:00+08:00"
    assert saved["pinned_group_ids"] == ["group-1"]


@pytest.mark.asyncio
async def test_update_embed_tour_records_skip(monkeypatch):
    """跳过必须被记住：首次进入就跳过时 completed_at 还是空的，
    只靠它拦不住自动播放，用户每次进来都会被从同一位置续播。
    """
    redis = FakeRedis()
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))

    result = await portal_prefs.update_embed_tour(
        portal_prefs.EmbedTourUpdate(
            version=1, step=2, skipped_at="2026-10-26T19:30:00+08:00"
        ),
        user_info(),
    )

    assert result["data"]["embed_tour_skipped_at"] == "2026-10-26T19:30:00+08:00"
    assert result["data"]["embed_tour_completed_at"] == ""
    saved = json.loads(redis.saved)
    assert saved["embed_tour_skipped_at"] == "2026-10-26T19:30:00+08:00"
