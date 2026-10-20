"""菜单删除规则（`MenuService.delete_node`）。

删除是唯一不可撤销的操作，因此单独钉行为而不是只断言源码结构：
内置项一律不可删；自建的空分组可以删（误建的分组否则只能改名复用）；
有子项的分组必须拒绝——连带删子树等于一次误点清掉一批菜单与它们的授权行。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.menu_service import MenuService, MenuValidationError

pytestmark = pytest.mark.no_infrastructure


def _row(**overrides):
    base = {
        "id": 9,
        "code": "group:test",
        "node_type": "group",
        "builtin": False,
        "permission_code": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _service(monkeypatch, row, *, has_child: bool):
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(first=lambda: (1,) if has_child else None)),
        delete=AsyncMock(),
        flush=AsyncMock(),
    )
    service = MenuService(db)
    monkeypatch.setattr(service, "table_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "_get_row", AsyncMock(return_value=row))
    monkeypatch.setattr(service, "invalidate", AsyncMock(return_value=1))
    return service, db


async def test_empty_custom_group_can_be_deleted(monkeypatch):
    service, db = _service(monkeypatch, _row(), has_child=False)

    await service.delete_node(9)

    db.delete.assert_awaited_once()
    db.flush.assert_awaited_once()


async def test_group_with_children_is_refused(monkeypatch):
    service, db = _service(monkeypatch, _row(), has_child=True)

    with pytest.raises(MenuValidationError) as exc:
        await service.delete_node(9)

    assert "还有菜单" in str(exc.value)
    db.delete.assert_not_awaited()


async def test_builtin_group_cannot_be_deleted(monkeypatch):
    """内置分组（含「系统管理」）即便为空也不许删，否则侧栏结构会缺一块。"""
    service, db = _service(monkeypatch, _row(code="group:system", builtin=True), has_child=False)

    with pytest.raises(MenuValidationError) as exc:
        await service.delete_node(28)

    assert "内置菜单不可删除" in str(exc.value)
    db.delete.assert_not_awaited()


async def test_builtin_page_cannot_be_deleted(monkeypatch):
    service, _ = _service(monkeypatch, _row(node_type="page", builtin=True), has_child=False)

    with pytest.raises(MenuValidationError):
        await service.delete_node(9)


async def test_custom_page_is_still_refused(monkeypatch):
    """自建页面本来就不允许创建，删除时也不能悄悄放行。"""
    service, db = _service(monkeypatch, _row(node_type="page", builtin=False), has_child=False)

    with pytest.raises(MenuValidationError) as exc:
        await service.delete_node(9)

    assert "只允许删除外链菜单" in str(exc.value)
    db.delete.assert_not_awaited()


async def test_external_menu_deletion_also_removes_its_grants(monkeypatch):
    """外链删掉后授权行必须一起清，否则角色上会留一条指向不存在菜单的授权。"""
    service, db = _service(
        monkeypatch,
        _row(node_type="external", permission_code="menu:ext:grafana"),
        has_child=False,
    )

    await service.delete_node(9)

    executed_sql = [str(call.args[0]) for call in db.execute.await_args_list]
    assert any("DELETE FROM ai_agent_resource_permissions" in sql for sql in executed_sql)
    db.delete.assert_awaited_once()
