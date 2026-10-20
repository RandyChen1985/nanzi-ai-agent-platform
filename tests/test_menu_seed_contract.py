"""菜单种子的结构契约：`app/services/menu_seed.py` 是菜单数据的唯一来源。

为什么要有这一层：
菜单从「前端常量」搬进数据库后，种子既是迁移 SQL 的内容源，也是后端在表缺失时的兜底。
一旦种子自身出现重复权限码、孤儿父级、越界图标或第二个落地页，表现会是「侧栏少一项」
或「权限树里点不动」，而不是报错。用测试把这些不变式钉死，比上线后逐页核对便宜得多。
"""

from __future__ import annotations

import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from app.services import menu_seed

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.no_infrastructure


def test_seed_has_expected_shape_and_unique_codes():
    nodes = menu_seed.BUILTIN_MENU_SEED

    assert Counter(node.node_type for node in nodes) == {
        "group": 6,
        "page": 26,
        "element": 60,
    }
    codes = [node.code for node in nodes]
    assert len(codes) == len(set(codes)) == 92

    permission_codes = [node.permission_code for node in nodes if node.permission_code]
    # 权限码必须唯一：复用会让「授予 A 菜单」连带授予 B，取消时又一起清掉
    assert len(permission_codes) == len(set(permission_codes)), "存在重复权限码"
    assert "menu:system:menus" in permission_codes
    # 功能点的码在 element 集合里，不在菜单码集合里——两者混在一起会让权限校验查错类型
    assert "element:menu:edit" in menu_seed.builtin_element_permission_codes()


def test_every_parent_exists_and_groups_sit_at_root():
    nodes = menu_seed.BUILTIN_MENU_SEED
    by_code = {node.code: node for node in nodes}

    for node in nodes:
        if node.parent_code is None:
            assert node.node_type == "group" or node.code == "menu:dashboard", (
                f"{node.code} 不是分组却挂在根上"
            )
            continue
        parent = by_code.get(node.parent_code)
        assert parent is not None, f"{node.code} 的父级 {node.parent_code} 不存在"
        if node.node_type == "element":
            assert parent.node_type == "page", f"功能点 {node.code} 只能挂在页面下"
        else:
            assert parent.node_type == "group", f"{node.code} 应挂在分组下"

    for node in nodes:
        if node.node_type == "group":
            assert node.parent_code is None, f"分组 {node.code} 必须是根节点"


def test_pages_use_whitelisted_paths_and_route_names():
    for node in menu_seed.BUILTIN_MENU_SEED:
        if node.node_type != "page":
            continue
        assert node.path in menu_seed.ALLOWED_PAGE_PATHS, f"{node.code} 的路由不在白名单"
        # 落地页与路由守卫都靠这个字段高亮侧栏，空值会让「子页面」点进父页面时没有选中项
        assert node.active_route_names, f"{node.code} 缺少 active_route_names"


def test_icons_come_from_the_shared_whitelist():
    allowed = set(menu_seed.ALLOWED_ICON_KEYS)
    for node in menu_seed.BUILTIN_MENU_SEED:
        if node.icon is None:
            continue
        assert node.icon in allowed, f"{node.code} 使用了未登记图标 {node.icon}"


def test_exactly_one_landing_page_and_it_needs_no_permission():
    landings = [node for node in menu_seed.BUILTIN_MENU_SEED if node.is_landing]

    assert [node.code for node in landings] == ["menu:workbench"]
    # 落地页必须人人可见：否则没有菜单权限的新用户登录后无处可去
    assert landings[0].permission_code is None


def test_external_scheme_whitelist_rejects_dangerous_protocols():
    assert set(menu_seed.ALLOWED_EXTERNAL_SCHEMES) == {"http://", "https://"}
    for scheme in ("javascript:", "data:", "file:", "vbscript:"):
        assert scheme not in menu_seed.ALLOWED_EXTERNAL_SCHEMES


def test_permission_code_helpers_match_the_seed():
    menu_codes = set(menu_seed.builtin_menu_permission_codes())
    element_codes = set(menu_seed.builtin_element_permission_codes())

    # 「我的工作台」故意不带权限码：它是所有登录用户的兜底落地页
    assert {"menu:system:menus", "menu:ai_chat"} <= menu_codes
    assert "menu:workbench" not in menu_codes
    assert "element:menu:edit" in element_codes
    assert not (menu_codes & element_codes)


def test_generated_migrations_are_in_sync_with_the_seed():
    """两套迁移 SQL 必须由当前种子生成，否则「代码里的菜单」与「库里的菜单」会分叉。"""
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/gen_menu_seed_sql.py"), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, (
        "迁移 SQL 与 menu_seed.py 不一致，请执行 "
        "`python scripts/gen_menu_seed_sql.py` 重新生成：\n"
        f"{completed.stdout}{completed.stderr}"
    )
