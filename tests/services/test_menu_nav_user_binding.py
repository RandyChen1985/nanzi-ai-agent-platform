"""导航权限的「用户身份取值」契约（`MenuService._load_user_menus`）。

为什么单独钉这一层：`require_api_key` 下发的用户字典里主键叫 `user_id`（字符串），
而少数接口自己拼的响应里叫 `id`。只认其中一个的后果不是报错，而是**静默读成空集**——
非 admin 用户登录后侧栏只剩「我的工作台」这类无权限码的公开菜单，看起来像"菜单丢了"。
这类问题不会让任何单测变红（单测通常顺手写 `{"id": ...}`），所以必须显式钉住真实形状。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.menu_service import MenuService

pytestmark = pytest.mark.no_infrastructure


def _grant(monkeypatch, menus: list[str], recorder: dict) -> None:
    async def fake_get_user_permissions(self, user_id):  # noqa: ANN001 - 与真实签名对齐
        recorder["user_id"] = user_id
        return SimpleNamespace(permissions=SimpleNamespace(menus=menus))

    monkeypatch.setattr(
        "app.services.permission_service.PermissionService.get_user_permissions",
        fake_get_user_permissions,
    )


async def test_reads_grants_from_the_real_auth_payload(monkeypatch):
    """真实认证字典：只有 `user_id`（字符串），没有 `id`。"""
    recorder: dict = {}
    _grant(monkeypatch, ["menu:ai_chat", "menu:system:users"], recorder)

    menus = await MenuService(None)._load_user_menus({"user_id": "178", "role": "user"})

    assert menus == ["menu:ai_chat", "menu:system:users"]
    # 字符串 id 必须转成 int 再查库，否则权限服务拿不到行
    assert recorder["user_id"] == 178


async def test_also_accepts_the_legacy_id_key(monkeypatch):
    """`{"id": ...}` 仍然要能用：内部复用与历史调用方传的是这个形状。"""
    recorder: dict = {}
    _grant(monkeypatch, ["menu:metadata"], recorder)

    menus = await MenuService(None)._load_user_menus({"id": 42})

    assert menus == ["menu:metadata"]
    assert recorder["user_id"] == 42


async def test_embedded_permissions_win_without_touching_the_database(monkeypatch):
    """调用方已经带了权限列表时不该再查库（避免一次请求两次鉴权）。"""

    async def exploding(self, user_id):  # noqa: ANN001
        raise AssertionError("不应该查库")

    monkeypatch.setattr(
        "app.services.permission_service.PermissionService.get_user_permissions", exploding
    )

    menus = await MenuService(None)._load_user_menus(
        {"user_id": "178", "permissions": {"menus": ["menu:prompts"]}}
    )

    assert menus == ["menu:prompts"]


@pytest.mark.parametrize("payload", [{}, {"user_id": ""}, {"user_id": None}, {"id": None}])
async def test_missing_or_blank_identity_returns_nothing_instead_of_raising(monkeypatch, payload):
    recorder: dict = {}
    _grant(monkeypatch, [], recorder)

    assert await MenuService(None)._load_user_menus(payload) == []
    assert "user_id" not in recorder


async def test_non_numeric_identity_does_not_break_the_sidebar(monkeypatch):
    """`user_id` 来自会话缓存，理论上可能是脏值；侧栏不能因此 500。"""
    recorder: dict = {}
    _grant(monkeypatch, [], recorder)

    assert await MenuService(None)._load_user_menus({"user_id": "not-a-number"}) == []
    assert "user_id" not in recorder


def test_auth_layer_and_menu_layer_agree_on_the_identity_key():
    """跨模块对账：认证下发的键名与菜单服务的取值必须能对上。

    这条断言存在的意义：两边任意一侧改名（或有人把 `user_id` 又改回 `id`），
    这里会先红，而不是等到用户登录后发现侧栏空了。
    """
    from pathlib import Path

    auth_source = Path("app/services/auth_service.py").read_text(encoding="utf-8")
    menu_source = Path("app/services/menu_service.py").read_text(encoding="utf-8")

    # 认证会话字典里的主键
    assert '"user_id": str(user.id)' in auth_source
    # 菜单服务必须同时认两种键名
    assert '.get("user_id") or (user or {}).get("id")' in menu_source
