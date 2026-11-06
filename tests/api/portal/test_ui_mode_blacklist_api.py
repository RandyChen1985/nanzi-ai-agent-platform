"""界面模式黑名单在偏好接口上的行为。

只钉三件对外可见的事：

1. GET /portal-prefs 返回的 ``ui_mode`` 一定落在黑名单允许的范围内（读侧夹逼）；
2. PUT /ui-mode 目标被禁时 403，且提示里能看出是哪个角色禁的；
3. 切换模式不能把用户的其他偏好（置顶等）冲掉。

DB 边界用假 ``_load_ui_mode_policy`` 替换（策略本身在
``tests/services/test_ui_mode_policy.py`` 里单独测），Redis 用内存假实现。
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.no_infrastructure

from app.api.portal.endpoints import portal_prefs as prefs_module  # noqa: E402
from app.core.dependencies import require_api_key  # noqa: E402
from app.main import app  # noqa: E402
from app.services.ui_mode_policy import UiModePolicy  # noqa: E402

USER_ID = 7
PREFS_KEY = f"agent:portal_prefs:{USER_ID}"


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value):
        self.store[key] = value


@pytest.fixture
async def client():
    async def _fake_user():
        return {"user_id": USER_ID, "role": "user", "user_name": "lin.qian"}

    app.dependency_overrides[require_api_key] = _fake_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(require_api_key, None)


@pytest.fixture
def fake_redis(monkeypatch) -> _FakeRedis:
    redis = _FakeRedis()

    async def _get_redis():
        return redis

    monkeypatch.setattr(prefs_module, "get_redis", _get_redis)
    return redis


@pytest.fixture
def stub_policy(monkeypatch):
    def _apply(policy: UiModePolicy) -> None:
        async def _load(_db, _user_info):
            return policy

        monkeypatch.setattr(prefs_module, "_load_ui_mode_policy", _load)

    return _apply


# ---------------- 读侧 ----------------


async def test_get_clamps_blocked_mode_to_allowed_one(client, fake_redis, stub_policy):
    fake_redis.store[PREFS_KEY] = json.dumps({"ui_mode": "pro"})
    stub_policy(UiModePolicy(blocked={"pro"}, blocked_by=["一线客服"]))

    resp = await client.get("/api/portal/portal-prefs")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ui_mode"] == "user"
    assert data["blocked_ui_modes"] == ["pro"]
    assert data["ui_mode_allowed"] == ["user"]
    assert data["ui_mode_blocked_by"] == ["一线客服"]


async def test_get_keeps_user_choice_when_nothing_blocked(client, fake_redis, stub_policy):
    fake_redis.store[PREFS_KEY] = json.dumps({"ui_mode": "pro"})
    stub_policy(UiModePolicy())

    resp = await client.get("/api/portal/portal-prefs")

    data = resp.json()["data"]
    assert data["ui_mode"] == "pro"
    assert data["blocked_ui_modes"] == []
    assert data["ui_mode_allowed"] == ["user", "pro"]


# ---------------- 写侧 ----------------


async def test_switch_to_blocked_mode_returns_403_with_source(client, fake_redis, stub_policy):
    stub_policy(UiModePolicy(blocked={"pro"}, blocked_by=["一线客服"]))

    resp = await client.put(
        "/api/portal/portal-prefs/ui-mode", json={"ui_mode": "pro"}
    )

    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert "一线客服" in detail
    assert "专业模式" in detail


async def test_switch_to_allowed_mode_keeps_other_prefs(client, fake_redis, stub_policy):
    fake_redis.store[PREFS_KEY] = json.dumps(
        {"ui_mode": "pro", "pinned_group_ids": ["g1"], "card_order": ["c1"]}
    )
    stub_policy(UiModePolicy(blocked={"user"}, blocked_by=["财务经理"]))

    resp = await client.put(
        "/api/portal/portal-prefs/ui-mode", json={"ui_mode": "pro"}
    )

    assert resp.status_code == 200
    saved = json.loads(fake_redis.store[PREFS_KEY])
    assert saved["ui_mode"] == "pro"
    assert saved["pinned_group_ids"] == ["g1"]
    assert saved["card_order"] == ["c1"]


# ---------------- 角色写入：装配层面的校验 ----------------


from fastapi import HTTPException  # noqa: E402
from datetime import datetime  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from app.api.portal.endpoints import roles as roles_module  # noqa: E402


class _RoleDb:
    """只够 update_role 用的最小 session：拿角色、记录是否提交。"""

    def __init__(self, role):
        self.role = role
        self.committed = False

    async def get(self, _model, _pk):
        return self.role

    async def commit(self):
        self.committed = True

    async def refresh(self, _obj):
        return None


@pytest.fixture
def stub_blocked_io(monkeypatch):
    """把角色黑名单的读写换掉，只验证校验顺序。"""

    def _apply(existing: str = "") -> None:
        async def _map(_db, _ids):
            return {3: existing}

        async def _set(_db, _role_id, blocked):
            from app.services.ui_mode_policy import serialize_blocked

            return serialize_blocked(blocked)

        monkeypatch.setattr(roles_module, "blocked_map_for_roles", _map)
        monkeypatch.setattr(roles_module, "set_role_blocked", _set)

    return _apply


def _make_role(code: str = "cs_agent", name: str = "一线客服"):
    return SimpleNamespace(
        id=3, code=code, name=name, description=None, created_at=datetime(2026, 1, 1)
    )


async def test_update_role_rejects_blocking_both_modes(stub_blocked_io):
    stub_blocked_io()
    db = _RoleDb(_make_role())

    with pytest.raises(HTTPException) as exc:
        await roles_module.update_role(
            3,
            roles_module.UpdateRoleRequest(name="一线客服", blocked_ui_modes="user,pro"),
            admin={},
            db=db,
        )

    assert exc.value.status_code == 400
    assert "至少保留一种" in exc.value.detail
    # 校验必须在 commit 之前：不能先改掉 name 再报错
    assert db.committed is False


async def test_update_role_rejects_blocking_admin_role(stub_blocked_io):
    stub_blocked_io()
    db = _RoleDb(_make_role(code="admin", name="平台管理员"))

    with pytest.raises(HTTPException) as exc:
        await roles_module.update_role(
            3,
            roles_module.UpdateRoleRequest(blocked_ui_modes="pro"),
            admin={},
            db=db,
        )

    assert exc.value.status_code == 400
    assert "管理员角色" in exc.value.detail
    assert db.committed is False


async def test_update_role_accepts_single_mode(stub_blocked_io):
    stub_blocked_io()
    db = _RoleDb(_make_role())

    result = await roles_module.update_role(
        3,
        roles_module.UpdateRoleRequest(blocked_ui_modes="pro"),
        admin={},
        db=db,
    )

    assert result["blocked_ui_modes"] == "pro"
    assert db.committed is True
