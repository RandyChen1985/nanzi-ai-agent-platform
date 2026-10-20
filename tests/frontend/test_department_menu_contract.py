"""「部门管理」菜单接入契约。

菜单是数据驱动的单一源（`app/services/menu_seed.py`），新增一个页面要同时落地四处，
少一处就表现为"菜单能配出来、点开白屏"或"图标渲染成兜底 ≡"：

1. seed 里的 page 节点 + element 功能点；
2. `ALLOWED_PAGE_PATHS` / `ALLOWED_ICON_KEYS` 白名单；
3. seed 迁移 SQL（脚本生成，不手写）；
4. 前端路由注册。

权限口径：`menu:system:departments` 管菜单可见性，`element:dept:edit` 管页面内写操作，
`element:dept:delete` 单独管删除。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services import menu_seed

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
MYSQL_SEED = ROOT / "db-prod" / "V178-create_departments.sql"
PG_SEED = ROOT / "db-prod-pg" / "V79-create_departments.sql"
ROUTER = ROOT / "frontend/src/router/index.ts"
ICONS_TS = ROOT / "frontend/src/constants/menuIcons.ts"


def _node(code: str):
    for node in menu_seed.BUILTIN_MENU_SEED:
        if node.code == code:
            return node
    raise AssertionError(f"seed 缺少节点 {code}")


def test_department_page_node():
    node = _node("menu:system:departments")
    assert node.node_type == menu_seed.NODE_PAGE
    assert node.label == "部门管理"
    assert node.parent_code == "group:system"
    assert node.path == "/dashboard/departments"
    assert node.icon == "org"
    assert node.permission_code == "menu:system:departments"


def test_department_element_nodes():
    edit = _node("element:dept:edit")
    assert edit.node_type == menu_seed.NODE_ELEMENT
    assert edit.parent_code == "menu:system:departments"

    delete = _node("element:dept:delete")
    assert delete.node_type == menu_seed.NODE_ELEMENT
    assert delete.parent_code == "menu:system:departments"


def test_page_path_whitelisted():
    assert "/dashboard/departments" in menu_seed.ALLOWED_PAGE_PATHS


def test_icon_key_whitelisted_and_present_in_frontend_catalog():
    assert "org" in menu_seed.ALLOWED_ICON_KEYS
    icons = ICONS_TS.read_text(encoding="utf-8")
    assert "org:" in icons, "前端图标目录缺少 org 映射（侧栏会渲染成兜底图标）"


def test_seed_migrations_contain_department_nodes():
    for path in (MYSQL_SEED, PG_SEED):
        assert path.exists(), f"缺少 seed 迁移 {path}"
        sql = path.read_text(encoding="utf-8")
        assert "menu:system:departments" in sql
        assert "element:dept:edit" in sql
        assert "element:dept:delete" in sql
        # seed 迁移必须幂等（只插缺失节点），否则重复执行会撞唯一键
        assert "NOT EXISTS" in sql.upper() or "ON CONFLICT" in sql.upper()


def test_route_registered_with_permission_meta():
    src = ROUTER.read_text(encoding="utf-8")
    segment = src.split("path: 'departments'", 1)
    assert len(segment) == 2, "路由表缺少 departments"
    block = segment[1].split("},", 1)[0]
    assert "DepartmentManagement" in block
    assert "menu:system:departments" in block
