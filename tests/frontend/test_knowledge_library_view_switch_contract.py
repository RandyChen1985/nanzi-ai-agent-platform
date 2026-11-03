"""知识库列表「列表 / 卡片」视图切换的契约。

同一份数据两种扫读方式：
- 列表：字段对齐，库多时横向比对快（文档数、引用、授权一排看下来）；
- 卡片：库少时看轮廓，描述能占两行，适合「先看是干什么的再点进去」。

两条硬要求：
1. 两种视图共用同一份行数据（libraryListRows），切换不重新请求；
2. 点卡片的落点与点行完全一致（selectDatasetNode），不能出现两套入口。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_view_switch_has_two_options(page: str) -> None:
    """切换器的选项与实际渲染分支必须一一对应。"""
    assert "const LIBRARY_VIEW_OPTIONS = [" in page
    assert "{ key: 'list', label: '列表'" in page
    assert "{ key: 'card', label: '卡片'" in page
    assert "const libraryView = ref<'list' | 'card'>('list')" in page


def test_views_share_the_same_rows(page: str) -> None:
    """两种视图读同一份数据，切换不该触发请求。"""
    assert 'v-for="ds in libraryListRows"' in page
    # 列表分支与卡片分支都从 libraryListRows 取数
    assert page.count('v-for="ds in libraryListRows"') >= 2


def test_card_click_uses_the_same_entry_as_row(page: str) -> None:
    """卡片和行必须走同一个进入路径，否则详情态会出现两套来源。"""
    card_start = page.index('<!-- 卡片视图：')
    card_block = page[card_start: page.index('</div>\n        </div>', card_start)]
    assert '@click="selectDatasetNode(ds)"' in card_block


def test_card_shows_docs_references_and_grants(page: str) -> None:
    """卡片上的三个指标要和列表列同源，避免两边口径不一致。"""
    card_start = page.index('<!-- 卡片视图：')
    card_block = page[card_start: card_start + 5000]
    for snippet in ('{{ docCountOf(ds) }}', '(ds.agent_ref_count ?? 0)', '{{ libraryGrantText(ds) }}'):
        assert snippet in card_block


def test_card_falls_back_when_description_is_empty(page: str) -> None:
    """没有描述时给占位文案，否则卡片高度会参差不齐。"""
    assert "const libraryDescOf = (ds: KnowledgeBase): string =>" in page
    assert "'暂无描述'" in page
    assert 'min-h-[34px]' in page
