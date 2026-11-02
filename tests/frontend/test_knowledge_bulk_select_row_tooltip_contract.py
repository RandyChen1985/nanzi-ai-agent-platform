"""知识库列表：全选要能带上失联库，行内按钮的提示不能被裁掉。"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


# ------------------------------------------------- 批量选中：失联库必须能选


def test_bulk_select_includes_missing_datasets(page):
    """全选不能漏掉失联库。

    单个勾选一直允许失联库（toggleDatasetSelection 不判 is_missing），
    只有全选的候选集把它排除了，两处口径打架。而失联库唯一的用途就是
    批量清理——元数据还在、RAGFlow 里已经没了，挨个点太折磨人。
    """
    # 批量勾选入口在「全部知识库」列表页，候选集是同一条规则
    block = page.split("const librarySelectableRows = computed(", 1)[1].split("\n)", 1)[0]
    assert "canManageDataset" in block
    assert "is_missing_in_ragflow" not in block


def test_single_select_keeps_the_same_rule(page):
    """单个勾选与全选必须同口径，否则又是「点得动但全选漏」。"""
    block = page.split("const toggleDatasetSelection = ", 1)[1].split("\n}", 1)[0]
    assert "canManageDataset(dataset)" in block
    assert "is_missing_in_ragflow" not in block


def test_delete_action_explains_missing_case(page):
    """失联库的删除是「清理残留」，菜单文案要跟普通删库区分开。"""
    assert "清理失效配置" in page
    assert "删除知识库" in page


# ------------------------------------------------- 行内操作按钮：不用原生 title


def test_row_actions_live_in_context_menu(page):
    """树行不再挂悬停按钮：绝对定位在右边缘会被裁切，也把行挤得只剩半行名字。
    操作统一收进右键菜单。"""
    assert "openDatasetMenu" in page
    assert "@contextmenu.prevent" in page
    assert "Hover Action Buttons" not in page
    for entry in ("新建文件夹", "编辑元数据", "删除知识库"):
        assert entry in page


# ------------------------------------------------- 引用徽章不能压在操作按钮下面


def test_tree_row_keeps_only_name_and_count(page):
    """树行按原型只留「名称 + 数量」：徽标和按钮都撤了，操作走右键菜单。"""
    assert "group-hover:opacity-0" in page     # 数量在悬停时让位给右键菜单，不叠字
    assert "被 {{ dataset.agent_ref_count }} 个智能体引用" not in page


def test_agent_ref_badge_gone_from_the_right_slot(page):
    right_slot = page.split("<!-- Indicators & Actions -->", 1)[1].split("<!-- Display doc count -->", 1)[0]
    assert "agent_ref_count" not in right_slot


def test_right_slot_only_renders_things_that_yield_on_hover(page):
    """留在右侧的常驻元素必须会在悬停时隐藏，否则还是会和按钮重叠。"""
    right_slot = page.split("<!-- Indicators & Actions -->", 1)[1].split("<!-- Hover Action Buttons -->", 1)[0]
    for line in right_slot.splitlines():
        if "rounded-full" in line and "doc_count" in line:
            assert "group-hover:hidden" in line
