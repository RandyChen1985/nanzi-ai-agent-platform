"""菜单重构收尾：权限树分组不可授权 / 外链数据刷新 / 改码后缓存失效。

这三处都是"只有真跑起来才暴露"的缺陷，用契约测试钉住，避免以后又改回去：

1. 权限树里的分组（`group:*`）只是展示容器，被勾选/全选会往授权表写垃圾行；
2. 外链页首屏用本地快照解析节点，新加的外链查不到就停在"无权限"，且不会重试；
3. 改外链 slug 时权限码跟着改名，授权行迁移了，但用户 Redis 缓存里还是旧码。
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

ACCESS_TREE = ROOT / "frontend/src/components/user/MenuAccessTree.vue"
PERMISSION_LISTS = ROOT / "frontend/src/utils/permissionLists.ts"
EXTERNAL_VIEW = ROOT / "frontend/src/views/ExternalMenuView.vue"
PERMISSION_SERVICE = ROOT / "app/services/permission_service.py"
MENU_SERVICE = ROOT / "app/services/menu_service.py"

pytestmark = pytest.mark.no_infrastructure


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 1. 分组不可授权


def test_permission_tree_groups_render_no_checkbox():
    source = _read(ACCESS_TREE)

    # 分组只有折叠图标，没有复选框；标题点击退化为折叠而不是授权
    assert "const isGroupNode = (node: PermissionNode) => node.id.startsWith('group:')" in source
    assert '<span v-if="isGroupNode(menu)"' in source
    assert ':tabindex="disabled && !isGroupNode(menu) ? -1 : 0"' in source
    assert "const onHeaderClick = (menu: PermissionNode) => {" in source
    assert "if (isGroupNode(menu)) {" in source
    assert "toggleCollapse(menu.id)" in source
    # 分组行显式标注，避免用户以为漏了勾选框
    assert ">分组</span>" in source


def test_group_ids_never_enter_the_grant_lists():
    source = _read(PERMISSION_LISTS)

    assert "const MENU_GROUP_ID_PREFIX = 'group:'" in source
    assert "const isMenuGroupId = (id: string) => id.startsWith(MENU_GROUP_ID_PREFIX)" in source

    # 单个勾选：分组直接返回原值
    entry = source[source.index("export function applyMenuEntryToggle"):]
    entry = entry[: entry.index("\n}\n")]
    assert "if (isMenuGroupId(menuId)) {" in entry
    assert "return { menus: nextMenus, elements: nextElements }" in entry

    # 批量级联：分组只级联子孙，绝不把自身写进授权
    cascade = source[source.index("export function applyMenuCascadeToggle"):]
    cascade = cascade[: cascade.index("\n}\n")]
    assert "!isMenuGroupId(menuId)" in cascade
    # 必须保留"全选分组"的能力，不能整段 early return
    assert "descendants.forEach" in cascade


def test_backend_drops_group_ids_as_a_last_line_of_defense():
    source = _read(PERMISSION_SERVICE)

    assert 'MENU_GROUP_ID_PREFIX = "group:"' in source
    assert "def is_menu_group_id(resource_id: object) -> bool:" in source
    # 角色与用户两条写入路径都要过滤
    assert source.count("if is_menu_group_id(menu_id):") >= 2
    assert source.count("if is_menu_group_id(element_id):") >= 2


# ------------------------------------------------------------- 2. 外链页刷新重试


def test_external_view_retries_after_navigation_refresh():
    source = _read(EXTERNAL_VIEW)

    assert "const { findNode, groups } = useNavigation()" in source
    assert "watch(groups, () => {" in source
    # 只在"查不到 / 地址变了"时重解析，避免每次刷新都重载 iframe
    assert "if (!current || current.url !== next.url) resolveNode()" in source
    # 模板 ref 与实际读取的键不一致会触发 TS6133，这里确认没有遗留死变量
    assert "frameRef" not in source


# ----------------------------------------------------------- 3. 改码后的缓存失效


def test_renaming_a_menu_code_purges_stale_permission_cache():
    service = _read(PERMISSION_SERVICE)
    menu = _read(MENU_SERVICE)

    assert "async def purge_all_permission_cache() -> int:" in service
    # 覆盖 v7 全局键与各智能体作用域键，且用 SCAN 而非 KEYS
    assert '"sys:auth:permissions:v7:user:*"' in service
    assert "async for key in redis.scan_iter(match=pattern, count=500):" in service

    rename = menu[menu.index("async def _rename_permission_code"):]
    rename = rename[: rename.index("async def delete_node")]
    assert "PermissionService.purge_all_permission_cache()" in rename
    # 缓存故障不能连带让改名失败
    assert "except Exception as ex:" in rename
    assert "logger.warning" in rename


def test_is_menu_group_id_matches_only_group_codes():
    from app.services.permission_service import is_menu_group_id

    assert is_menu_group_id("group:system") is True
    assert is_menu_group_id(" group:assistant ") is True
    assert is_menu_group_id("menu:system") is False
    assert is_menu_group_id("element:menu:edit") is False
    assert is_menu_group_id(None) is False
    assert is_menu_group_id("") is False
