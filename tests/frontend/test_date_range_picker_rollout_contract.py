"""三个页面统一使用日历区间控件的接线契约。

## 背景

时间筛选原本有三套实现，同一个产品里三种交互：

- Token 统计：`<select>` 三选一（今日 / 近 7 天 / 近 30 天）
- 概览：`<select>` 三选一（今日 / 本周 / 本月）
- 运营分析：一排快捷按钮（近 7/30/90 天）+ 两个原生 `date` 输入框 + 「应用」

统一成一个 `DateRangePicker` 后，除了视觉一致，更要紧的是**口径一致**：
每个页面传给后端的区间必须与它自己显示的文案、以及后端的上限完全对齐。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PICKER = ROOT / "frontend/src/components/common/DateRangePicker.vue"
DATE_RANGE_UTIL = ROOT / "frontend/src/utils/dateRange.ts"
TOKEN_STATS = ROOT / "frontend/src/views/TokenStats.vue"
OVERVIEW = ROOT / "frontend/src/views/Overview.vue"
METRICS = ROOT / "frontend/src/views/KnowledgeMetrics.vue"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(path: Path) -> str:
    return re.sub(r"\s+", " ", _read(path))


def test_all_three_pages_use_the_same_picker():
    for page in (TOKEN_STATS, OVERVIEW, METRICS):
        source = _read(page)
        assert "<DateRangePicker" in source, f"{page.name} 应使用共享的日历区间组件"
        assert "<select" not in source, f"{page.name} 不应再有原生下拉"
        assert "type=\"date\"" not in source, f"{page.name} 不应再有原生日期输入"


def test_each_page_declares_its_own_limit():
    """上限跟着后端的实际约束走，不能三页共用一个数字。"""
    assert ':max-days="90"' in _read(TOKEN_STATS)
    assert ':max-days="90"' in _read(OVERVIEW)
    metrics = _flat(METRICS)
    assert "const MAX_RANGE_DAYS = 366" in metrics
    assert ':max-days="MAX_RANGE_DAYS"' in metrics


def test_presets_have_a_single_source_of_truth():
    """快捷区间只在 utils 定义一次，页面和组件都从这里取。"""
    util = _read(DATE_RANGE_UTIL)
    picker = _read(PICKER)

    assert "export function buildRangePresets" in util
    assert "buildRangePresets(" in picker
    assert "props.presetKeys ?? DEFAULT_PRESET_KEYS" in picker
    # 组件里不应再出现第二份区间计算
    assert "addDays(t, -6)" not in picker


def test_week_presets_follow_monday_first():
    util = _read(DATE_RANGE_UTIL)

    assert "offset = (base.getDay() + 6) % 7" in util, "周起点应为周一"
    assert '"thisWeek"' in util
    assert '"lastWeek"' in util


def test_overview_requests_share_one_range():
    source = _flat(OVERVIEW)
    body = source[source.index("const rangeParams"):source.index("const fetchAgentStats")]

    assert "start_date: dateRange.value.start" in body
    assert "end_date: dateRange.value.end" in body
    assert source.count("params: rangeParams.value") == 4, "四个概览接口都要带同一组区间"


def test_overview_labels_follow_the_selected_range():
    """切到「上月」时卡片还写「今日累计」是最典型的错位。"""
    source = _flat(OVERVIEW)

    assert ':foot="rangeFoot"' in source
    assert "{{ rangeName }} · 共" in source
    assert ':period-label="rangeName"' in source
    assert "rangePreset.value ? `${rangePreset.value.label}累计` : \"区间累计\"" in source


def test_overview_shortcuts_prefer_calendar_periods():
    source = _flat(OVERVIEW)

    assert (
        'const OVERVIEW_PRESET_KEYS = ["today", "yesterday", "thisWeek", "lastWeek", "thisMonth", "lastMonth"]'
        in source
    )
    assert ':preset-keys="OVERVIEW_PRESET_KEYS"' in source


def test_metrics_keeps_both_cards_in_sync():
    """主报表和检索质量卡必须一起换区间，否则同屏两个时间口径。"""
    source = _flat(METRICS)
    body = source[source.index("const onRangeChange"):source.index("const selectOrderBy")]

    assert "fetchMetrics()" in body
    assert "loadRetrievalStats()" in body


def test_metrics_retrieval_stats_uses_the_same_range():
    source = _flat(METRICS)
    body = source[source.index("const loadRetrievalStats"):source.index("const loadHealthSummary")]

    assert "start_date: activeRange.value.startDate" in body
    assert "end_date: activeRange.value.endDate" in body
    assert "days:" not in body, "检索质量不能再用滚动天数，否则区间选了过去它还是最近 N 天"


def test_metrics_keeps_the_visible_range_line():
    """顶部「统计区间：A ~ B（N 天）」是核对数字的依据，换控件时不能一起丢掉。"""
    source = _read(METRICS)

    assert "统计区间：{{ activeRange.startDate }} ~ {{ activeRange.endDate }}（{{ activeRangeDays }} 天）" in source
