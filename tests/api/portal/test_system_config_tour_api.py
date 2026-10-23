"""系统配置引导状态：全局单键 Redis 读写。

引导是「系统级、全局一份」：任何进入系统配置页的人都能结束播放并写完成态，
但只有 element:system:config_save 的人才会被自动播（前端判定）。
"""

import inspect
import json

import pytest

from app.api.portal.endpoints import system as system_endpoints


pytestmark = pytest.mark.no_infrastructure

TOUR_KEY = "agent:tour:system_config"


class FakeRedis:
    def __init__(self, value=None):
        self.value = value
        self.saved = None

    async def get(self, key):
        assert key == TOUR_KEY
        return self.value

    async def set(self, key, value):
        assert key == TOUR_KEY
        self.value = value
        self.saved = value


async def _resolved(value):
    return value


def admin_user():
    return {
        "user_id": "7",
        "user_name": "admin",
        "real_name": "超级管理员",
        "role": "admin",
    }


def test_tour_key_is_global_single_key():
    assert system_endpoints.SYSTEM_CONFIG_TOUR_KEY == TOUR_KEY


@pytest.mark.asyncio
async def test_get_tour_returns_defaults_when_key_missing(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(system_endpoints.redis, "get_redis", lambda: _resolved(redis))

    result = await system_endpoints.get_system_config_tour(admin_user())

    assert result["data"] == {
        "version": 0,
        "completed_at": "",
        "completed_by": 0,
        "completed_by_name": "",
        "skipped": False,
    }
    # 不写空键：Redis 里没有就是没有
    assert redis.saved is None


@pytest.mark.asyncio
async def test_put_tour_records_completer_and_skipped(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(system_endpoints.redis, "get_redis", lambda: _resolved(redis))

    result = await system_endpoints.update_system_config_tour(
        system_endpoints.SystemConfigTourUpdate(
            version=1,
            completed_at="2026-10-23T15:02:11+08:00",
            skipped=True,
        ),
        admin_user(),
    )

    saved = json.loads(redis.saved)
    assert saved["version"] == 1
    assert saved["skipped"] is True
    assert saved["completed_by"] == 7
    assert saved["completed_by_name"] == "超级管理员"
    assert result["data"]["completed_at"] == "2026-10-23T15:02:11+08:00"


@pytest.mark.asyncio
async def test_get_tour_reads_back_written_state(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(system_endpoints.redis, "get_redis", lambda: _resolved(redis))

    await system_endpoints.update_system_config_tour(
        system_endpoints.SystemConfigTourUpdate(
            version=2,
            completed_at="2026-10-24T09:00:00+08:00",
        ),
        admin_user(),
    )
    result = await system_endpoints.get_system_config_tour(admin_user())

    assert result["data"]["version"] == 2
    assert result["data"]["completed_by_name"] == "超级管理员"


@pytest.mark.asyncio
async def test_tour_endpoints_return_503_without_redis(monkeypatch):
    monkeypatch.setattr(system_endpoints.redis, "get_redis", lambda: _resolved(None))

    with pytest.raises(system_endpoints.HTTPException) as read_error:
        await system_endpoints.get_system_config_tour(admin_user())
    assert read_error.value.status_code == 503

    with pytest.raises(system_endpoints.HTTPException) as write_error:
        await system_endpoints.update_system_config_tour(
            system_endpoints.SystemConfigTourUpdate(version=1, completed_at="x"),
            admin_user(),
        )
    assert write_error.value.status_code == 503


def test_tour_endpoints_require_menu_permission_only():
    """读和写都只要菜单权限：写不要求 config_save，任何人都能结束播放。"""

    for fn in (
        system_endpoints.get_system_config_tour,
        system_endpoints.update_system_config_tour,
    ):
        source = inspect.getsource(fn)
        assert 'require_permission("menu", "menu:system:config")' in source
        assert "element:system:config_save" not in source
