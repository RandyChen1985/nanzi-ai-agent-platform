"""知识库管理的窄屏（手机）布局契约。

430px 以下的问题不是「不好看」，而是**用不了**：
- 左栏 228px 与主区并排 → 内容区只剩 200px，等于没有内容；
- 库头按钮组 `shrink-0` 不换行 → 溢出到容器外，最右边的按钮被裁掉；
- 文档表格 7 列固定宽 → 直接压成一条，看不出哪列是哪列。

所以窄屏的三条底线：左栏默认收起、库头竖排、表格横向滚动。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"
WORKSPACE = ROOT / "frontend/src/components/knowledge/DocWorkspace.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workspace() -> str:
    return WORKSPACE.read_text(encoding="utf-8")


def test_nav_is_collapsed_by_default_on_small_screens(page: str) -> None:
    """窄屏首屏就收起左栏，否则用户要先滚过整屏导航才看到内容。"""
    assert "window.matchMedia('(max-width: 1023px)').matches" in page
    assert "const navCollapsed = ref(" in page


def test_library_header_stacks_on_small_screens(page: str) -> None:
    """标题与操作按钮在窄屏各占一行；按钮组允许换行，不能溢出被裁。"""
    assert 'flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between' in page
    # 按钮组不能留着 shrink-0：不换行就会溢出到容器外，最右边的按钮被裁
    header_start = page.index("openLibDrawer('settings')")
    header_block = page[header_start - 400: header_start + 200]
    assert 'flex flex-wrap items-center gap-2' in header_block
    assert 'shrink-0 items-center gap-2' not in header_block


def test_doc_quota_moves_to_its_own_row_on_small_screens(page: str) -> None:
    """配额条跟随 `ml-auto` 会在换行后贴到奇怪的位置，窄屏让它独占一行。"""
    assert "inline-flex items-center gap-1.5 w-full sm:ml-auto sm:w-auto" in page


def test_doc_table_scrolls_horizontally(workspace: str) -> None:
    """7 列固定宽在手机上必须能横滚，而不是被压扁。"""
    assert 'class="min-w-[640px]"' in workspace
