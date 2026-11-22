"""版本检测接口层：结果合并用户「忽略此版本」偏好。

服务层的比较与缓存由 `tests/services/test_update_check_service.py` 覆盖；
这里只钉接口层独有的两件事：
1. 「忽略」按**归一化三段版本**比对——忽略 1.0.24 之后出现 1.0.25 必须重新提示；
2. 偏好读取失败不能让整个版本检测失败（Redis 抖动时退回「没忽略过」）。
"""

from __future__ import annotations

import json

import pytest

from app.api.portal.endpoints import system as system_endpoints

pytestmark = pytest.mark.no_infrastructure


BASE_PAYLOAD = {
    "current": "1.0.23.0",
    "latest": "1.0.24",
    "latest_normalized": "1.0.24",
    "has_update": True,
    "reason": "第 3 段 24 > 23",
    "published_at": "2026-11-22T13:38:28+08:00",
    "release_url": "https://gitee.com/x/y/releases/tag/1.0.24",
    "notes": ["凭据中心"],
    "has_migrations": True,
    "checked_at": "2026-11-22T09:12:03+08:00",
    "source": "cache",
    "error": None,
}


def test_update_is_prompted_when_user_never_ignored() -> None:
    merged = system_endpoints._with_user_prefs(dict(BASE_PAYLOAD), "")
    assert merged["ignored"] is False
    assert merged["should_prompt"] is True
    assert merged["ignored_version"] == ""


def test_prompt_is_suppressed_for_ignored_version() -> None:
    merged = system_endpoints._with_user_prefs(dict(BASE_PAYLOAD), "1.0.24")
    assert merged["ignored"] is True
    assert merged["should_prompt"] is False


def test_bigger_tag_revives_the_prompt() -> None:
    """忽略的是 1.0.24：上游出 1.0.25 时必须重新提示（不是永久静音）。"""

    payload = dict(BASE_PAYLOAD, latest="1.0.25", latest_normalized="1.0.25")
    merged = system_endpoints._with_user_prefs(payload, "1.0.24")
    assert merged["ignored"] is False
    assert merged["should_prompt"] is True


def test_no_prompt_when_already_latest() -> None:
    payload = dict(
        BASE_PAYLOAD,
        latest="1.0.24",
        latest_normalized="1.0.24",
        has_update=False,
        current="1.0.24.0",
    )
    merged = system_endpoints._with_user_prefs(payload, "")
    assert merged["should_prompt"] is False


class _FakeRedis:
    def __init__(self, raw: str | None) -> None:
        self.raw = raw

    async def get(self, key: str):
        assert key == "agent:portal_prefs:7"
        return self.raw


@pytest.mark.asyncio
async def test_reads_ignored_version_from_portal_prefs(monkeypatch) -> None:
    raw = json.dumps({"ui_mode": "user", "ignored_update_version": "1.0.24"})

    async def _get_redis():
        return _FakeRedis(raw)

    monkeypatch.setattr(system_endpoints.redis, "get_redis", _get_redis)
    assert await system_endpoints._read_ignored_update_version(7) == "1.0.24"


@pytest.mark.asyncio
async def test_missing_key_or_broken_json_means_not_ignored(monkeypatch) -> None:
    async def _get_redis_none():
        return None

    monkeypatch.setattr(system_endpoints.redis, "get_redis", _get_redis_none)
    assert await system_endpoints._read_ignored_update_version(7) == ""

    async def _get_redis_broken():
        return _FakeRedis("{not json")

    monkeypatch.setattr(system_endpoints.redis, "get_redis", _get_redis_broken)
    assert await system_endpoints._read_ignored_update_version(7) == ""


@pytest.mark.asyncio
async def test_get_endpoint_passes_current_and_merges_prefs(monkeypatch) -> None:
    seen = {}

    async def _fake_check(current=None, *, force=False):
        seen["current"] = current
        seen["force"] = force
        return dict(BASE_PAYLOAD)

    async def _fake_ignored(user_id: int) -> str:
        seen["user_id"] = user_id
        return ""

    from app.services import update_check_service

    monkeypatch.setattr(update_check_service, "check_for_update", _fake_check)
    monkeypatch.setattr(system_endpoints, "_read_ignored_update_version", _fake_ignored)

    result = await system_endpoints.get_update_check(current="v1.0.23.0", user={"user_id": 7})
    assert result["code"] == 0
    assert seen == {"current": "v1.0.23.0", "force": False, "user_id": 7}
    assert result["data"]["should_prompt"] is True


@pytest.mark.asyncio
async def test_post_endpoint_forces_refresh(monkeypatch) -> None:
    seen = {}

    async def _fake_check(current=None, *, force=False):
        seen["force"] = force
        return dict(BASE_PAYLOAD)

    async def _fake_ignored(user_id: int) -> str:
        return ""

    from app.services import update_check_service
    from app.api.portal.endpoints.system import UpdateCheckRequest

    monkeypatch.setattr(update_check_service, "check_for_update", _fake_check)
    monkeypatch.setattr(system_endpoints, "_read_ignored_update_version", _fake_ignored)

    result = await system_endpoints.post_update_check(
        body=UpdateCheckRequest(current="1.0.23.0"), user={"user_id": 7}
    )
    assert seen["force"] is True
    assert result["data"]["latest"] == "1.0.24"
