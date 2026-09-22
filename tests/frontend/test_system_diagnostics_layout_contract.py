"""系统诊断页（diagnostics tab）的布局契约。

背景：该页原来是「左 1/3 操作卡片 + 右 2/3 控制台」的左右分栏，操作按钮在左侧
卡片内纵向堆叠，且面板高度写死 600px。现改为「操作区置顶 + 全宽控制台」的上下
结构，并把向量检测详情折叠起来，让控制台/浏览器获得更大的可视高度。

这些断言锁定的是**信息架构**（谁在谁上面、是否还分栏），不是具体像素值。
"""

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return (ROOT / "frontend/src/views/SystemConfig.vue").read_text(encoding="utf-8")


def _diagnostics_block(source: str) -> str:
    start = source.index("<!-- DIAGNOSTICS TAB -->")
    end = source.index("<!-- BRANDING TAB -->")
    return source[start:end]


def test_diagnostics_uses_vertical_layout_instead_of_side_by_side_columns():
    """最外层容器必须改为纵向 flex，且不再存在三列分栏。"""
    block = _diagnostics_block(_source())
    head = block[: block.index("<!-- 顶部：连接与能力检查")]

    assert "activeTab === 'diagnostics'" in head
    assert "flex flex-col" in head, "诊断页外层应为纵向排布"
    assert "lg:grid-cols-3" not in head, "不应再使用「左操作 + 右控制台」的三列分栏"
    assert "lg:col-span-1" not in head


def test_action_cards_live_in_a_top_row_of_at_most_two_columns():
    """两张操作卡片置顶并排，窄屏自动堆叠。"""
    block = _diagnostics_block(_source())

    assert "grid-cols-1 xl:grid-cols-2" in block
    top = block[: block.index("<!-- 下方：诊断控制台")]
    assert "缓存与会话管理" in top, "Redis 卡片应在控制台上方"
    assert "检测 RediSearch 与会话摘要向量索引能力" in top, "向量搜索卡片应在控制台上方"


def test_action_buttons_are_inline_not_a_vertical_full_width_stack():
    """操作按钮横向排列（可换行），不再是每行一个的整列宽按钮。"""
    block = _diagnostics_block(_source())
    top = block[: block.index("<!-- 下方：诊断控制台")]

    for label in ("测试连接", "扫描 Keys", "清理 Keys", "重新检测", "重构本地向量数据"):
        assert label in top, f"{label} 按钮应位于顶部操作区"

    assert "flex flex-wrap gap-2" in top, "操作按钮应横向排布并可换行"
    assert "w-full inline-flex justify-center items-center" not in top, (
        "操作按钮不应再使用整列宽（w-full）的纵向堆叠样式"
    )


def test_vector_health_detail_is_collapsed_by_default():
    """检测详情默认收起，点击「查看详情」才展开。"""
    source = _source()
    block = _diagnostics_block(source)

    assert "const vectorHealthExpanded = ref(false)" in source, "详情默认收起"
    assert "vectorHealthExpanded = !vectorHealthExpanded" in block, "需要展开/收起切换"
    assert 'v-if="vectorHealthExpanded && redisVectorHealth"' in block
    assert "查看详情" in block and "收起详情" in block


def test_console_and_redis_browser_panel_spans_full_width():
    """控制台/浏览器面板占满整行，且高度自适应而非写死 600px。"""
    block = _diagnostics_block(_source())
    tail = block[block.index("<!-- 下方：诊断控制台"):]

    assert "lg:col-span-2" not in block, "面板不再只占两列，应span整行"
    assert "flex-1 min-h-[520px]" in tail, "面板高度应自适应并保留可用的最小高度"
    assert "h-[600px]" not in block, "不应再写死 600px 高度"


def test_redis_browser_keeps_key_list_and_detail_side_by_side():
    """浏览器内部仍是「Key 列表 + 详情」并排，本次改造不改变它。"""
    block = _diagnostics_block(_source())
    browser = block[block.index("<!-- Tab: Redis Browser -->"):]

    assert "<!-- Left Column: Keys list -->" in browser
    assert "<!-- Right Column: Key detail -->" in browser
    assert "fetchRedisKeyDetail" in browser
    assert "formatRedisValue" in browser
