"""「我的权限」分类折叠与「菜单 / 功能点」树形化的契约。

用户诉求（2026-10-20）：
1. 个人中心「我的权限」的分类区块要能折叠；
2. 界面菜单与功能点原本是并排两张卡片，层级归属看不出来、阅读不方便，
   希望合成一张树形结构。

契约分两层：
- 源码：PersonalCenter 不再输出并排两张卡；四个分类共用同一折叠外壳；
  默认全展开且不落任何存储；树组件只读、不发请求。
- 真渲染（SSR，见 personal_permission_tree_ssr.cjs）：只渲染已授权节点，
  未授权父级只作浅灰分组，后端新增权限码走「其他」兜底。
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PERSONAL = ROOT / "frontend/src/views/PersonalCenter.vue"
CARD = ROOT / "frontend/src/components/personal/PermissionSectionCard.vue"
TREE = ROOT / "frontend/src/components/personal/PersonalPermissionTree.vue"
TREE_NODE = ROOT / "frontend/src/components/personal/PersonalPermissionTreeNode.vue"
SSR_SCRIPT = ROOT / "tests/frontend/personal_permission_tree_ssr.cjs"

from menu_tree_fixture import frontend_permission_tree  # noqa: E402

pytestmark = pytest.mark.no_infrastructure

SSR_CHECKS = [
    "rendersGrantedMenuAndElements",
    "prunesUnauthorizedSiblings",
    "ungrantedParentIsContainer",
    "pageBadgeOnlyOnGrantedSubmenu",
    "indentsThreeLevels",
    "showsGrantedElementCounter",
    "collapseButtonsDefaultExpanded",
    "unmatchedFallbackUsesDisplayName",
    "deepOnlyParentChainIsAllContainers",
    "deepOnlyKeepsGrantedLeaf",
    "emptyStateExplicit",
    "expandedCardIsOpen",
    "collapsedCardHidesBodyButKeepsDom",
]


def _node_ready() -> bool:
    if shutil.which("node") is None:
        return False
    node_modules = ROOT / "frontend/node_modules"
    return all(
        (node_modules / name).exists() for name in ("vue", "esbuild", "@vue/server-renderer")
    )


@pytest.fixture(scope="module")
def ssr_result() -> dict:
    if not _node_ready():
        pytest.skip("需要 node 与 frontend/node_modules（vue / esbuild / @vue/server-renderer）")
    completed = subprocess.run(
        ["node", str(SSR_SCRIPT)],
        cwd=ROOT / "frontend",
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "MENU_TREE_JSON": json.dumps(frontend_permission_tree(), ensure_ascii=False)},
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize("check", SSR_CHECKS)
def test_tree_rendering_contract(ssr_result: dict, check: str):
    assert ssr_result["checks"][check], f"渲染契约未满足：{check}"


def _personal_source() -> str:
    return PERSONAL.read_text(encoding="utf-8")


def test_menus_and_elements_are_no_longer_two_side_by_side_cards():
    """合并成一张树形卡：旧的「界面菜单 / 功能点」两个标题与扁平映射都不能残留。"""
    source = _personal_source()

    assert ">界面菜单<" not in source, "不应再出现独立的「界面菜单」卡片标题"
    assert ">功能点</h3>" not in source, "不应再出现独立的「功能点」卡片标题"
    assert "getPermissionDisplayName" not in source, "扁平徽章映射已被树组件取代"


def test_all_four_sections_share_the_collapsible_shell():
    """智能体 / 知识库 / 数据集 / 平台功能权限四类共用同一外壳与同一套折叠状态。"""
    source = _personal_source()

    assert source.count("<PermissionSectionCard") == 4, "四类资源都应使用统一的可折叠卡片"
    for key in ("agents", "datasets", "metadata", "access"):
        assert f"isPermissionSectionCollapsed('{key}')" in source, f"缺少 {key} 的折叠态绑定"
        assert f"togglePermissionSection('{key}')" in source, f"缺少 {key} 的折叠开关"

    assert "title=\"平台功能权限\"" in source
    assert "<PersonalPermissionTree" in source


def test_collapse_defaults_to_all_expanded_and_is_not_persisted():
    """默认全展开；状态只活在本次会话（不写 localStorage / sessionStorage）。"""
    source = _personal_source()

    assert "ref<PermissionSectionKey[]>([])" in source, "默认应是空集合 = 全部展开"
    assert "localStorage" not in source, "折叠状态不做持久化，避免出现「上次折叠了但忘了」"
    assert "sessionStorage" not in source
    assert "toggleAllPermissionSections" in source, "需要提供全部展开 / 全部折叠的快捷入口"


def test_tree_component_is_read_only_and_follows_menu_tree():
    tree = TREE.read_text(encoding="utf-8")

    assert "MENU_TREE" not in tree, "组件不得再内置菜单数据"
    assert "tree: PermissionNode[]" in tree or "tree?: PermissionNode[]" in tree
    assert "buildGrantedPermissionView" in tree
    assert "PersonalPermissionTreeNode" in tree
    assert "axios" not in tree and "fetch(" not in tree, "只读展示组件不得自己拉数据"
    assert "collapsedIds = ref<string[]>([])" in tree, "树内节点默认也应是展开的"


def test_tree_node_marks_container_and_page_semantics():
    node = TREE_NODE.read_text(encoding="utf-8")

    assert "仅分组" in node, "未授权父级必须明确标成分组，不能看起来像已授权"
    assert "页面" in node, "子菜单要标「页面」，与一级菜单区分"
    assert 'data-granted' in node, "渲染契约依赖 data-granted 区分授权与容器"


def test_collapsible_shell_is_accessible_and_keeps_dom():
    card = CARD.read_text(encoding="utf-8")

    assert ':aria-expanded="!collapsed"' in card
    assert ':aria-controls="bodyId"' in card
    assert 'v-show="!collapsed"' in card, "用 v-show 保留子树，折叠不应丢掉树内展开状态"
    assert "useId" in card, "多个实例的 aria-controls 不能撞 id"
