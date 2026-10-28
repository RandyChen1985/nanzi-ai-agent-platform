"""平台技能管理权限（``element:skills:admin``）的同步判定契约。

## 为什么需要这一层

「安装/创建到平台全局技能库」的调用点分散在三处：

* ``create_skills(scope="global")``（模型逐字写 SKILL.md）
* ``import_skill_from_sandbox(scope="global")``（整目录收录第三方技能包）
* ``GET /skills/gallery``（决定要不要下发 ``install_prompt_global``）

前两处是 **同步工具函数**，没法 ``await PermissionService.check_permission``；而三处
必须用**同一个口径**：页面入口按 ``element:skills:admin`` 判定，工具里却只认
``role == "admin"``，会造成「按钮看得见、执行被拒」或者说反过来的漏权。

这里提供进程内、带 TTL 的同步读缓存：会话启动时异步预热一次，工具侧同步读。
**未命中按无权限处理（fail-closed）**——与「禁用公共技能」那份缓存恰好相反，
因为这条判定对应的是写平台库，宁可少放行也不能多放行。
"""
from __future__ import annotations

import pytest

from app.services.ai.skills import platform_admin

pytestmark = pytest.mark.no_infrastructure

ADMIN_USER = {"id": 1, "role": "admin"}
# 现成 session 的替身：避免单测真的去建 AsyncSessionLocal 连库
_DB = object()
REGULAR_USER = {"id": 2, "role": "user"}


@pytest.fixture(autouse=True)
def _clean_cache():
    platform_admin.clear_platform_skill_admin_cache()
    yield
    platform_admin.clear_platform_skill_admin_cache()


# --- 同步判定 ---------------------------------------------------------------


def test_admin_role_is_allowed_without_any_cache():
    assert platform_admin.can_manage_platform_skills(ADMIN_USER) is True


def test_regular_user_is_denied_when_cache_is_cold():
    """fail-closed：没预热就等于没授权，绝不因「查不到」而放行。"""
    assert platform_admin.can_manage_platform_skills(REGULAR_USER) is False


def test_cached_grant_allows_non_admin_user():
    platform_admin.remember_platform_skill_admin(2, True)

    assert platform_admin.can_manage_platform_skills(REGULAR_USER) is True


def test_cached_revoke_denies_even_before_expiry():
    platform_admin.remember_platform_skill_admin(2, False)

    assert platform_admin.can_manage_platform_skills(REGULAR_USER) is False


def test_missing_identity_is_denied():
    assert platform_admin.can_manage_platform_skills(None) is False
    assert platform_admin.can_manage_platform_skills({}) is False


def test_cache_entries_expire(monkeypatch):
    """缓存只是「最近一次异步查询结果」的副本，过期后必须回落到 fail-closed。"""
    platform_admin.remember_platform_skill_admin(2, True)
    assert platform_admin.can_manage_platform_skills(REGULAR_USER) is True

    # 把写入时刻往前推，超过 TTL
    monkeypatch.setattr(
        platform_admin,
        "_now",
        lambda: platform_admin.time.monotonic() + platform_admin.CACHE_TTL_SECONDS + 1,
    )
    assert platform_admin.can_manage_platform_skills(REGULAR_USER) is False


# --- 异步预热 ---------------------------------------------------------------


class _FakePermissionService:
    """替身：只记录入参并返回预设结果，不碰数据库。"""

    def __init__(self, db=None):
        self.db = db

    async def check_permission(self, user_id, resource_type, resource_id):  # noqa: D401
        _FakePermissionService.calls.append((user_id, resource_type, resource_id))
        return _FakePermissionService.granted


@pytest.fixture
def fake_permission_service(monkeypatch):
    _FakePermissionService.calls = []
    _FakePermissionService.granted = False
    monkeypatch.setattr(
        "app.services.permission_service.PermissionService", _FakePermissionService
    )
    return _FakePermissionService


@pytest.mark.asyncio
async def test_prime_records_grant_from_permission_service(fake_permission_service):
    fake_permission_service.granted = True

    allowed = await platform_admin.prime_platform_skill_admin(REGULAR_USER, db=_DB)

    assert allowed is True
    assert fake_permission_service.calls == [(2, "element", "element:skills:admin")]
    assert platform_admin.can_manage_platform_skills(REGULAR_USER) is True


@pytest.mark.asyncio
async def test_prime_records_denial(fake_permission_service):
    allowed = await platform_admin.prime_platform_skill_admin(REGULAR_USER, db=_DB)

    assert allowed is False
    assert platform_admin.can_manage_platform_skills(REGULAR_USER) is False


@pytest.mark.asyncio
async def test_prime_short_circuits_for_admin(fake_permission_service):
    """管理员不需要查库：与 require_permission 的 bypass 保持同一语义。"""
    allowed = await platform_admin.prime_platform_skill_admin(ADMIN_USER, db=_DB)

    assert allowed is True
    assert fake_permission_service.calls == []


@pytest.mark.asyncio
async def test_prime_failure_keeps_denying(monkeypatch):
    class _Boom:
        def __init__(self, db=None):
            pass

        async def check_permission(self, *args, **kwargs):
            raise RuntimeError("db down")

    monkeypatch.setattr("app.services.permission_service.PermissionService", _Boom)

    allowed = await platform_admin.prime_platform_skill_admin(REGULAR_USER, db=_DB)

    assert allowed is False
    assert platform_admin.can_manage_platform_skills(REGULAR_USER) is False


@pytest.mark.asyncio
async def test_prime_without_identity_does_not_query(fake_permission_service):
    allowed = await platform_admin.prime_platform_skill_admin({}, db=_DB)

    assert allowed is False
    assert fake_permission_service.calls == []
