"""Portal preference routing fields are persisted per user without overwriting other prefs."""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.portal.endpoints import portal_prefs


pytestmark = pytest.mark.no_infrastructure


class FakeRedis:
    def __init__(self, value=None, initial_store=None):
        self.value = value
        self.store = dict(initial_store or {})
        self.saved = None

    async def get(self, key):
        if key in self.store:
            return self.store[key]
        # 如果未传入字典，兼容单值测试
        if not self.store and self.value is not None:
            return self.value
        return None

    async def set(self, key, value):
        self.saved = value
        self.store[key] = value
        self.value = value

    async def delete(self, key):
        self.store.pop(key, None)
        if self.saved == key:
            self.saved = None


def user_info(user_id=7, role="user"):
    return {"user_id": user_id, "role": role}


@pytest.mark.asyncio
async def test_get_portal_prefs_defaults_to_auto_routing(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))

    result = await portal_prefs.get_portal_prefs(user_info())

    assert result["data"]["routing_mode"] == "auto"
    assert result["data"]["expert_agent_id"] == ""
    assert result["data"]["routing_configured"] is False


@pytest.mark.asyncio
async def test_update_routing_prefs_preserves_other_fields(monkeypatch):
    redis = FakeRedis(
        json.dumps(
            {
                "pinned_group_ids": ["group-1"],
                "markdown_theme": "academic",
            }
        )
    )
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))
    monkeypatch.setattr(
        portal_prefs.AgentManagerService,
        "resolve_embed_agent_access",
        _resolved_agent,
    )
    monkeypatch.setattr(
        portal_prefs.AgentManagerService,
        "get_active_agent_config",
        _resolved_agent_config,
    )

    result = await portal_prefs.update_routing_prefs(
        portal_prefs.RoutingPreferenceUpdate(
            routing_mode="expert",
            expert_agent_id="agent-1",
        ),
        session=object(),
        user_info=user_info(),
    )

    saved = json.loads(redis.saved)
    assert result["data"] == {
        "routing_mode": "expert",
        "expert_agent_id": "agent-1",
    }
    assert saved["pinned_group_ids"] == ["group-1"]
    assert saved["markdown_theme"] == "academic"
    assert saved["routing_mode"] == "expert"
    assert saved["expert_agent_id"] == "agent-1"
    assert saved["routing_configured"] is True


@pytest.mark.asyncio
async def test_update_auto_routing_clears_expert_agent_id(monkeypatch):
    redis = FakeRedis(json.dumps({"expert_agent_id": "old-agent"}))
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))

    result = await portal_prefs.update_routing_prefs(
        portal_prefs.RoutingPreferenceUpdate(routing_mode="auto"),
        session=object(),
        user_info=user_info(),
    )

    saved = json.loads(redis.saved)
    assert result["data"]["routing_mode"] == "auto"
    assert result["data"]["expert_agent_id"] == ""
    assert saved["expert_agent_id"] == ""


@pytest.mark.asyncio
async def test_legacy_full_preference_update_does_not_wipe_routing(monkeypatch):
    redis = FakeRedis(
        json.dumps(
            {
                "routing_mode": "expert",
                "expert_agent_id": "agent-1",
                "markdown_theme": "academic",
            }
        )
    )
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))

    await portal_prefs.update_portal_prefs(
        portal_prefs.PortalPrefsUpdate(
            pinned_group_ids=["group-2"],
        ),
        user_info(),
    )

    saved = json.loads(redis.saved)
    assert saved["routing_mode"] == "expert"
    assert saved["expert_agent_id"] == "agent-1"


@pytest.mark.asyncio
async def test_full_preference_update_cannot_bypass_routing_access_check(monkeypatch):
    redis = FakeRedis(json.dumps({"routing_mode": "auto", "expert_agent_id": ""}))
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))

    await portal_prefs.update_portal_prefs(
        portal_prefs.PortalPrefsUpdate.model_validate(
            {
                "routing_mode": "expert",
                "expert_agent_id": "private-agent",
            }
        ),
        user_info(),
    )

    saved = json.loads(redis.saved)
    assert saved["routing_mode"] == "auto"
    assert saved["expert_agent_id"] == ""


def test_routing_preference_model_rejects_invalid_mode():
    with pytest.raises(ValidationError):
        portal_prefs.RoutingPreferenceUpdate(routing_mode="invalid")


@pytest.mark.asyncio
async def test_update_expert_routing_requires_agent_id(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))

    with pytest.raises(HTTPException) as exc_info:
        await portal_prefs.update_routing_prefs(
            portal_prefs.RoutingPreferenceUpdate(
                routing_mode="expert",
                expert_agent_id="",
            ),
            session=object(),
            user_info=user_info(),
        )

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_update_expert_routing_rejects_forbidden_agent(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))

    async def forbidden(*_args, **_kwargs):
        raise PermissionError("agent_forbidden")

    monkeypatch.setattr(
        portal_prefs.AgentManagerService,
        "resolve_embed_agent_access",
        forbidden,
    )

    with pytest.raises(HTTPException) as exc_info:
        await portal_prefs.update_routing_prefs(
            portal_prefs.RoutingPreferenceUpdate(
                routing_mode="expert",
                expert_agent_id="private-agent",
            ),
            session=object(),
            user_info=user_info(),
        )

    assert exc_info.value.status_code == 403
    assert redis.saved is None


@pytest.mark.asyncio
async def test_update_agent_avatar_prefs_admin_success(monkeypatch):
    class DualKeyRedis:
        def __init__(self):
            self.store = {"agent:portal_prefs:7": json.dumps({"markdown_theme": "apple"})}

        async def get(self, key):
            return self.store.get(key)

        async def set(self, key, value):
            self.store[key] = value

        async def delete(self, key):
            self.store.pop(key, None)

    redis = DualKeyRedis()
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))

    # 管理员更新成功
    result = await portal_prefs.update_agent_avatar(
        portal_prefs.AgentAvatarUpdate(avatar="/branding/avatars/agent_avatar.png"),
        user_info=user_info(role="admin"),
    )

    assert result["code"] == 0
    assert result["data"]["agent_avatar"] == "/branding/avatars/agent_avatar.png"
    assert redis.store[portal_prefs.GLOBAL_AGENT_AVATAR_KEY] == "/branding/avatars/agent_avatar.png"

    # 普通用户获取时自动拿到管理员设置的全局头像
    user_res = await portal_prefs.get_portal_prefs(user_info(user_id=99, role="user"))
    assert user_res["code"] == 0
    assert user_res["data"]["agent_avatar"] == "/branding/avatars/agent_avatar.png"


@pytest.mark.asyncio
async def test_get_portal_prefs_global_avatar_overrides_personal_stale_prefs(monkeypatch):
    user_key = portal_prefs._redis_key(99)
    redis = FakeRedis(
        initial_store={
            user_key: json.dumps({"agent_avatar": "stale_old_avatar.png"}),
            portal_prefs.GLOBAL_AGENT_AVATAR_KEY: "new_global_avatar.png",
        }
    )
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))

    user_res = await portal_prefs.get_portal_prefs(user_info(user_id=99, role="user"))
    assert user_res["code"] == 0
    assert user_res["data"]["agent_avatar"] == "new_global_avatar.png"

    # 当全局重置为空时，个人偏好中的旧头像绝不能倒灌
    redis.store.pop(portal_prefs.GLOBAL_AGENT_AVATAR_KEY, None)
    user_res_reset = await portal_prefs.get_portal_prefs(user_info(user_id=99, role="user"))
    assert user_res_reset["data"]["agent_avatar"] == ""


@pytest.mark.asyncio
async def test_full_update_portal_prefs_cannot_override_agent_avatar_for_normal_user(monkeypatch):
    redis = FakeRedis(json.dumps({"markdown_theme": "default"}))
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))

    # 普通用户调用 update_portal_prefs 试图传入 agent_avatar
    await portal_prefs.update_portal_prefs(
        portal_prefs.PortalPrefsUpdate(
            agent_avatar="hacked_avatar.png",
            markdown_theme="minimal",
        ),
        user_info(user_id=99, role="user"),
    )

    saved = json.loads(redis.saved)
    assert saved.get("agent_avatar", "") == ""
    assert saved["markdown_theme"] == "minimal"


@pytest.mark.asyncio
async def test_update_agent_avatar_prefs_forbidden_for_normal_user():
    with pytest.raises(HTTPException) as exc_info:
        await portal_prefs.update_agent_avatar(
            portal_prefs.AgentAvatarUpdate(avatar="/branding/avatars/agent_avatar.png"),
            user_info=user_info(role="user"),
        )
    assert exc_info.value.status_code == 403
    assert "只有管理员才能设置" in exc_info.value.detail


@pytest.mark.asyncio
async def test_upload_agent_avatar_forbidden_for_normal_user():
    from io import BytesIO
    from starlette.datastructures import UploadFile as StarletteUploadFile

    fake_file = StarletteUploadFile(
        filename="robot.png",
        file=BytesIO(b"\x89PNG\r\n\x1a\nfake-image-content"),
        headers={"content-type": "image/png"},
    )
    with pytest.raises(HTTPException) as exc_info:
        await portal_prefs.upload_agent_avatar(
            file=fake_file,
            user_info=user_info(role="user"),
        )
    assert exc_info.value.status_code == 403
    assert "只有管理员才能上传" in exc_info.value.detail


@pytest.mark.asyncio
async def test_upload_agent_avatar_admin_saves_to_branding_dir(monkeypatch, tmp_path):
    from io import BytesIO
    from starlette.datastructures import UploadFile as StarletteUploadFile

    test_branding_dir = tmp_path / "branding_avatars"
    monkeypatch.setattr(portal_prefs, "BRANDING_AVATARS_DIR", str(test_branding_dir))

    fake_file = StarletteUploadFile(
        filename="robot.png",
        file=BytesIO(b"\x89PNG\r\n\x1a\nfake-image-content"),
        headers={"content-type": "image/png"},
    )

    result = await portal_prefs.upload_agent_avatar(
        file=fake_file,
        user_info=user_info(user_id=1, role="admin"),
    )

    assert result["code"] == 0
    assert result["data"]["avatar_url"].startswith("/branding/avatars/agent_avatar_")
    assert result["data"]["avatar_url"].endswith(".png")
    saved_files = list(test_branding_dir.glob("agent_avatar_*.png"))
    assert len(saved_files) == 1
    assert saved_files[0].read_bytes() == b"\x89PNG\r\n\x1a\nfake-image-content"




async def _resolved(value):
    return value


async def _resolved_agent(*_args, **_kwargs):
    return SimpleNamespace(id="agent-1")


async def _resolved_agent_config(*_args, **_kwargs):
    return SimpleNamespace(agent_id="agent-1")
