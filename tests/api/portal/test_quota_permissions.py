"""额度接口的权限口径：能进菜单 ≠ 能改额度。

用户管理页把「系统默认额度」入口按 ``element:user:edit`` 隐藏后，后端若仍只校验
``menu:system:users``，就等于留了一个绕过入口的口子——没有编辑权限的人照样能直接
调接口改全站额度。这里锁住前后端同一口径，也顺便防止角色额度被误改。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "app/api/portal/endpoints/quota.py"

EDIT_PERMISSION = 'require_permission("element", "element:user:edit")'
ROLE_MENU_PERMISSION = 'require_permission("menu", "menu:system:roles")'


@pytest.fixture(scope="module")
def source() -> str:
    assert SOURCE.exists()
    return SOURCE.read_text(encoding="utf-8")


def _endpoint_block(source: str, func: str) -> str:
    """取单个端点函数体：从 async def 到下一个 @router 装饰器。"""
    start = source.index(f"async def {func}(")
    end = source.find("\n@router.", start)
    return source[start : end if end != -1 else len(source)]


@pytest.mark.parametrize(
    "func", ["get_system_quota", "update_system_quota", "delete_system_quota"]
)
def test_system_quota_requires_user_edit_permission(source: str, func: str) -> None:
    """系统默认额度是编辑动作：与前端入口判定的 element:user:edit 对齐。"""
    block = _endpoint_block(source, func)
    assert EDIT_PERMISSION in block
    # 旧的菜单权限口径不得回退
    assert "menu:system:users" not in block


@pytest.mark.parametrize(
    "func",
    ["get_user_quota_policy", "update_user_quota_policy", "delete_user_quota_policy"],
)
def test_user_quota_requires_user_edit_permission(source: str, func: str) -> None:
    """用户专属额度本来就是编辑权限，别在统一口径时被反向改回去。"""
    assert EDIT_PERMISSION in _endpoint_block(source, func)


@pytest.mark.parametrize(
    "func",
    ["get_role_quota_policy", "update_role_quota_policy", "delete_role_quota_policy"],
)
def test_role_quota_stays_on_role_menu_permission(source: str, func: str) -> None:
    """角色额度模板归角色管理菜单，不在用户管理页，不该被这次统一带走。"""
    block = _endpoint_block(source, func)
    assert ROLE_MENU_PERMISSION in block
    assert EDIT_PERMISSION not in block


def test_my_quota_stays_self_service(source: str) -> None:
    """「我的额度」是自助查询，任何登录用户都能看自己的，不需要编辑权限。"""
    block = _endpoint_block(source, "get_my_quota")
    assert "require_api_key" in block
    assert "require_permission" not in block
