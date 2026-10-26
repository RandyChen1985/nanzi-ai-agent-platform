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
    block = page.split("const bulkSelectableDatasets = computed(", 1)[1].split("\n)", 1)[0]
    assert "canManageDataset(dataset)" in block
    assert "is_missing_in_ragflow" not in block


def test_single_select_keeps_the_same_rule(page):
    """单个勾选与全选必须同口径，否则又是「点得动但全选漏」。"""
    block = page.split("const toggleDatasetSelection = ", 1)[1].split("\n}", 1)[0]
    assert "canManageDataset(dataset)" in block
    assert "is_missing_in_ragflow" not in block


def test_delete_button_explains_missing_case(page):
    """失联库的删除是「清理残留」，不该和普通删库共用一句文案。"""
    assert "清理失效残留配置（不会去 RAGFlow 删除）" in page


# ------------------------------------------------- 行内操作按钮：不用原生 title


def test_row_action_buttons_use_custom_tooltip(page):
    """原生 title 会飘到容器外被裁掉——这排按钮绝对定位在 right-3，紧贴右边缘。"""
    for text in ("新建虚拟文件夹", "修改元数据"):
        assert f'title="{text}"' not in page
        assert f'text="{text}"' in page   # 文案仍在，改由 HoverTip 承载
    assert 'title="删除知识库"' not in page


# ------------------------------------------------- 引用徽章不能压在操作按钮下面


def test_agent_ref_badge_moved_next_to_the_name(page):
    """徽章原本在右侧指标区，而那片在悬停时被绝对定位的操作按钮覆盖：
    文档数会 group-hover:hidden 让位，徽章不会 —— 于是和按钮叠在一起。
    状态类信息统一靠左，右侧整块留给按钮。
    """
    name_block = page.split("<!-- Display name + creator -->", 1)[1].split("创建人:", 1)[0]
    assert "agent_ref_count" in name_block


def test_agent_ref_badge_gone_from_the_right_slot(page):
    right_slot = page.split("<!-- Indicators & Actions -->", 1)[1].split("<!-- Display doc count -->", 1)[0]
    assert "agent_ref_count" not in right_slot


def test_right_slot_only_renders_things_that_yield_on_hover(page):
    """留在右侧的常驻元素必须会在悬停时隐藏，否则还是会和按钮重叠。"""
    right_slot = page.split("<!-- Indicators & Actions -->", 1)[1].split("<!-- Hover Action Buttons -->", 1)[0]
    for line in right_slot.splitlines():
        if "rounded-full" in line and "doc_count" in line:
            assert "group-hover:hidden" in line
