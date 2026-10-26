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
