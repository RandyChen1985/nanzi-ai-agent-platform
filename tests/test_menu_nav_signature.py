"""导航条件请求必须绑定「当前用户」，不能只认全局菜单版本。

背景（2026-11 换号串菜单）：
`GET /api/portal/menus/nav?version=` 原本只比较全局菜单版本号，且这个比较发生在
读取用户权限之前。于是只要浏览器里留着**上一个身份**的快照，且菜单表没被改过，
服务端就会一直回 `{"unchanged": true}`，客户端永远渲染上一个人的菜单；而路由守卫
用的是新的 `user_info`，于是「菜单看得见、点进去被弹回工作台」。

这里钉住三条不变式：

1. 只有 `version` 与 `signature` 同时命中当前用户才允许 `unchanged`；
2. 换一个用户（哪怕权限集合相同）必须拿不到别人的签名命中；
3. 权限集合变化（角色/菜单授权调整）不需要额外的版本失效调用，签名本身就会变。

第 3 条是刻意的：`permission_service` 的授权变更并不会 `INCR sys:menu:version`，
把失效责任留给调用方迟早会漏；签名由「版本 + 身份 + 权限集合」现算，天然自愈。
"""

from __future__ import annotations

import pytest

from app.services.menu_service import MenuService, _builtin_nodes_payload

pytestmark = pytest.mark.no_infrastructure

VERSION = 2_000_002
ADMIN = {"user_id": "1", "role": "admin"}
PLAIN_USER = {"user_id": "2", "role": "user"}
OTHER_USER = {"user_id": "3", "role": "user"}


def _service(version: int = VERSION):
    """构造一个不碰 DB/Redis 的 MenuService：节点与版本都由测试注入。"""
    service = MenuService(db=None)
    nodes = _builtin_nodes_payload()

    async def _table_ready() -> bool:
        return True

    async def _get_version() -> int:
        return version

    async def _get_nodes():
        return nodes

    service.table_ready = _table_ready  # type: ignore[assignment]
    service.get_version = _get_version  # type: ignore[assignment]
    service.get_nodes = _get_nodes  # type: ignore[assignment]
    return service


async def _nav(service, user, *, menus, version=None, signature=None, is_admin=None):
    """直接调 get_nav：menus/is_admin 注入，避免走到权限服务。"""
    return await service.get_nav(
        user,
        version,
        client_signature=signature,
        menus=menus,
        is_admin=is_admin,
    )


async def test_unchanged_requires_matching_signature():
    service = _service()
    menus = ["menu:ai_chat"]
    first = await _nav(service, PLAIN_USER, menus=menus)
    assert first["unchanged"] is False
    assert first["signature"]

    cached = await _nav(service, PLAIN_USER, menus=menus, version=VERSION, signature=first["signature"])
    assert cached == {"version": VERSION, "signature": first["signature"], "unchanged": True}


async def test_legacy_client_without_signature_always_gets_full_tree():
    """老客户端只带 version：宁可多传一次树，也不能让它停在可能过期的快照上。"""
    service = _service()
    payload = await _nav(service, PLAIN_USER, menus=["menu:ai_chat"], version=VERSION)
    assert payload["unchanged"] is False
    assert payload["nodes"]


async def test_snapshot_signature_from_another_user_never_matches(monkeypatch):
    """换账号：即使菜单版本、权限集合都一样，也不能命中上一个人的签名。"""
    service = _service()
    menus = ["menu:ai_chat"]
    admin_payload = await _nav(service, ADMIN, menus=[], is_admin=True)
    # 拿管理员的签名去问普通用户
    payload = await _nav(
        service,
        OTHER_USER,
        menus=menus,
        version=VERSION,
        signature=admin_payload["signature"],
    )
    assert payload["unchanged"] is False
    assert payload["signature"] != admin_payload["signature"]

    # 两个权限相同的普通用户也不能互相命中
    user_a = await _nav(service, PLAIN_USER, menus=menus)
    user_b = await _nav(service, OTHER_USER, menus=menus, version=VERSION, signature=user_a["signature"])
    assert user_b["unchanged"] is False


async def test_permission_change_invalidates_signature_without_version_bump():
    """授权调整不会 INCR 菜单版本；签名随权限集合变化，必须自动拿新树。"""
    service = _service()
    before = await _nav(service, PLAIN_USER, menus=["menu:ai_chat"])
    after = await _nav(
        service,
        PLAIN_USER,
        menus=["menu:ai_chat", "menu:knowledge_management"],
        version=VERSION,
        signature=before["signature"],
    )
    assert after["unchanged"] is False
    assert after["signature"] != before["signature"]


async def test_signature_is_stable_for_same_identity_and_permissions():
    """签名必须稳定，否则每次请求都退化成全量，条件请求就白做了。"""
    service = _service()
    first = await _nav(service, PLAIN_USER, menus=["menu:ai_chat", "menu:metadata"])
    second = await _nav(service, PLAIN_USER, menus=["menu:metadata", "menu:ai_chat"])
    assert first["signature"] == second["signature"]
    # 权限顺序不同不影响签名（集合语义）
    again = await _nav(
        service,
        PLAIN_USER,
        menus=["menu:metadata", "menu:ai_chat"],
        version=VERSION,
        signature=first["signature"],
    )
    assert again["unchanged"] is True


async def test_table_missing_or_version_zero_never_short_circuits():
    """version=0（表未建立 / Redis 不可用）时必须回完整树，否则客户端永远停在旧快照。"""
    service = _service(version=0)
    first = await _nav(service, PLAIN_USER, menus=["menu:ai_chat"])
    payload = await _nav(
        service,
        PLAIN_USER,
        menus=["menu:ai_chat"],
        version=0,
        signature=first["signature"],
    )
    assert payload["unchanged"] is False
    assert payload["version"] == 0


async def test_non_ascii_signature_never_raises():
    """`?signature=中文` 不能把接口打成 500（hmac.compare_digest 对非 ASCII 会抛错）。"""
    service = _service()
    payload = await _nav(
        service,
        PLAIN_USER,
        menus=["menu:ai_chat"],
        version=VERSION,
        signature="中文签名",
    )
    assert payload["unchanged"] is False
    assert payload["nodes"]
