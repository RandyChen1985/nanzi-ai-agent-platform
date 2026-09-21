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


class DualKeyRedis:
    """支持「全局键 + 用户偏好键」同时读写的假 Redis（不覆盖无关键）。"""

    def __init__(self, initial_store=None):
        self.store = dict(initial_store or {})

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)


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
    user_key = portal_prefs._redis_key(7)
    redis = DualKeyRedis(initial_store={user_key: json.dumps({"markdown_theme": "apple"})})
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))

    # 管理员更新成功（未携带 base_avatar 的旧客户端仍可写入）
    result = await portal_prefs.update_agent_avatar(
        portal_prefs.AgentAvatarUpdate(avatar="/branding/avatars/agent_avatar.png"),
        user_info=user_info(role="admin"),
    )

    assert result["code"] == 0
    assert result["data"]["agent_avatar"] == "/branding/avatars/agent_avatar.png"
    assert redis.store[portal_prefs.GLOBAL_AGENT_AVATAR_KEY] == "/branding/avatars/agent_avatar.png"

    # 全局键是唯一权威源：不再向调用者的个人偏好键写入头像副本（避免埋下倒灌隐患）
    saved_user_prefs = json.loads(redis.store[user_key])
    assert saved_user_prefs.get("agent_avatar", "") == ""
    assert saved_user_prefs["markdown_theme"] == "apple"

    # 普通用户获取时自动拿到管理员设置的全局头像
    user_res = await portal_prefs.get_portal_prefs(user_info(user_id=99, role="user"))
    assert user_res["code"] == 0
    assert user_res["data"]["agent_avatar"] == "/branding/avatars/agent_avatar.png"


@pytest.mark.asyncio
async def test_update_agent_avatar_accepts_matching_base_avatar(monkeypatch):
    redis = DualKeyRedis(
        initial_store={portal_prefs.GLOBAL_AGENT_AVATAR_KEY: "avatar_v1.png"}
    )
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))

    result = await portal_prefs.update_agent_avatar(
        portal_prefs.AgentAvatarUpdate(avatar="avatar_v2.png", base_avatar="avatar_v1.png"),
        user_info=user_info(role="admin"),
    )

    assert result["code"] == 0
    assert redis.store[portal_prefs.GLOBAL_AGENT_AVATAR_KEY] == "avatar_v2.png"


@pytest.mark.asyncio
async def test_update_agent_avatar_rejects_stale_base_avatar(monkeypatch):
    """停留在旧页面的管理员不能把陈旧头像倒灌回全局键。"""
    redis = DualKeyRedis(
        initial_store={portal_prefs.GLOBAL_AGENT_AVATAR_KEY: "avatar_new.png"}
    )
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))

    with pytest.raises(HTTPException) as exc_info:
        await portal_prefs.update_agent_avatar(
            portal_prefs.AgentAvatarUpdate(avatar="avatar_old.png", base_avatar="avatar_old.png"),
            user_info=user_info(role="admin"),
        )

    assert exc_info.value.status_code == 409
    # 全局头像保持其他管理员刚设置的新值，冲突详情回传服务端当前值供前端同步
    assert redis.store[portal_prefs.GLOBAL_AGENT_AVATAR_KEY] == "avatar_new.png"
    assert exc_info.value.detail["agent_avatar"] == "avatar_new.png"


@pytest.mark.asyncio
async def test_update_agent_avatar_rejects_stale_empty_base_avatar(monkeypatch):
    """旧页面输入框为空时提交空值，不能删除其他管理员刚设置的全局头像。"""
    redis = DualKeyRedis(
        initial_store={portal_prefs.GLOBAL_AGENT_AVATAR_KEY: "avatar_new.png"}
    )
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))

    with pytest.raises(HTTPException) as exc_info:
        await portal_prefs.update_agent_avatar(
            portal_prefs.AgentAvatarUpdate(avatar="", base_avatar=""),
            user_info=user_info(role="admin"),
        )

    assert exc_info.value.status_code == 409
    assert redis.store[portal_prefs.GLOBAL_AGENT_AVATAR_KEY] == "avatar_new.png"


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
async def test_full_update_portal_prefs_never_accepts_agent_avatar(monkeypatch):
    """全量偏好入口已彻底拔除头像字段：管理员与普通用户都无法借此回写头像。"""
    stale_avatar = "stale_old_avatar.png"

    for role in ("user", "admin"):
        user_key = portal_prefs._redis_key(99)
        redis = FakeRedis(
            initial_store={
                user_key: json.dumps({"markdown_theme": "default", "agent_avatar": stale_avatar}),
                portal_prefs.GLOBAL_AGENT_AVATAR_KEY: "new_global_avatar.png",
            }
        )
        monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))

        # 旧客户端仍可能把整个偏好（含 agent_avatar）原样回传，必须被忽略
        body = portal_prefs.PortalPrefsUpdate.model_validate(
            {"markdown_theme": "minimal", "agent_avatar": "hacked_avatar.png"}
        )
        await portal_prefs.update_portal_prefs(body, user_info(user_id=99, role=role))

        saved = json.loads(redis.store[user_key])
        assert saved.get("agent_avatar", "") == "", f"{role} 不得写入个人头像副本"
        assert saved["markdown_theme"] == "minimal"
        # 全局权威值不受全量偏好写入影响
        assert redis.store[portal_prefs.GLOBAL_AGENT_AVATAR_KEY] == "new_global_avatar.png"


@pytest.mark.asyncio
async def test_update_agent_avatar_rejects_over_long_url(monkeypatch):
    """头像地址长度上限与前端 MAX_AGENT_AVATAR_URL_LENGTH 对齐。

    历史缺陷：两个预设头像使用内联 data URI（2197 / 2196 字符），超过 2048 上限，
    点击后直接 422。这里锁定该边界，避免再次出现"某些预设点了报错"的回归。
    """
    redis = DualKeyRedis()
    monkeypatch.setattr(portal_prefs, "get_redis", lambda: _resolved(redis))

    # 正好达到上限仍可写入
    boundary = "x" * 2048
    result = await portal_prefs.update_agent_avatar(
        portal_prefs.AgentAvatarUpdate(avatar=boundary),
        user_info=user_info(role="admin"),
    )
    assert result["data"]["agent_avatar"] == boundary

    # 超出一个字符即在请求模型层被拒绝
    with pytest.raises(ValidationError):
        portal_prefs.AgentAvatarUpdate(avatar="x" * 2049)


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
