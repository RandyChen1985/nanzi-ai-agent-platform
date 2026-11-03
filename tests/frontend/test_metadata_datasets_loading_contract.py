"""元数据管理列表页加载态与分批渲染契约。

数据集多时后端要逐个统计关系数，接口偏慢；页面原先 loading 初值为 false，
首帧渲染空白，用户看到的是「整块白」而不是加载中。契约锁住：
初始就进入加载态、有明确文案、骨架跟随视图，并且列表分批渲染避免一次挂载全部卡片。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

DATASETS_VIEW = ROOT / "frontend/src/views/MetadataDatasets.vue"


def _loading_block() -> str:
    source = DATASETS_VIEW.read_text(encoding="utf-8")
    start = source.index("<!-- Loading State")
    end = source.index("<!-- Filtered Empty State -->")
    return source[start:end]


def test_loading_state_is_on_from_first_render():
    source = DATASETS_VIEW.read_text(encoding="utf-8")

    assert "const loading = ref(true)" in source, "首帧就要是加载态，否则请求发出前会空白"


def test_loading_skeleton_follows_view_mode_and_has_label():
    block = _loading_block()

    assert "正在加载数据集…" in block
    assert 'role="status"' in block and 'aria-busy="true"' in block
    # 网格 / 列表各有对应骨架，避免数据到达前后高度剧烈跳动
    assert "viewMode === 'grid'" in block
    assert "h-36" in block
    assert "h-[76px]" in block
    assert "animate-pulse" in block


def test_dataset_list_renders_in_batches():
    source = DATASETS_VIEW.read_text(encoding="utf-8")

    assert "const visibleDatasets = computed" in source
    assert "const loadMoreDatasets" in source
    assert source.count('v-for="ds in visibleDatasets"') == 2, "网格与列表都应改用分批后的列表"
    assert 'v-for="ds in displayDatasets"' not in source
    assert '@click="loadMoreDatasets"' in source
    assert "加载更多" in source
    # 筛选 / 排序变化后回到第一屏，避免停在旧的展开位置
    assert "watch([searchQuery, statusFilter, sortField, sortDirection]" in source
