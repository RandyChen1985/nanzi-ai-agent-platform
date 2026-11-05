"""Token 统计页「日历区间选择」的前端契约（`DateRangePicker.vue` + `TokenStats.vue`）。

## 背景

原来的时段控件是三选一下拉（今日 / 最近 7 天 / 最近 30 天），只能看固定窗口。
改成日历面板后要保证：

1. **任意区间**：起止日期可自由选，跨度受后端 90 天限制约束；
2. **快捷项仍在**：高频的「最近 7 天」不能退化成翻日历；
3. **三份数据同口径**：趋势、智能体分布、用户账单必须用同一个区间，否则同屏两套数字；
4. **边界可见**：未来日期不可选、超过上限要当场提示，不能静默截断。

## 为什么用源码契约

本仓库没有 vitest，前端行为契约统一走 pytest 读源码（与本目录其他契约测试同一套做法）。
"""
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOKEN_STATS = ROOT / "frontend/src/views/TokenStats.vue"
PICKER = ROOT / "frontend/src/components/common/DateRangePicker.vue"
DATE_RANGE_UTIL = ROOT / "frontend/src/utils/dateRange.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(path: Path) -> str:
    return re.sub(r"\s+", " ", _read(path))


# --------------------------------------------------------------------------- #
# TokenStats.vue：接线
# --------------------------------------------------------------------------- #


def test_period_select_is_replaced_by_calendar_picker():
    """旧的时段下拉必须彻底消失，不能和日历面板并存。"""
    source = _read(TOKEN_STATS)

    assert "<DateRangePicker" in source, "页面应使用日历区间组件"
    assert "v-model=\"dateRange\"" in source
    assert "<select" not in source, "旧的时段 select 应删除，否则会出现两套时间入口"
    assert "onPeriodChange" not in source
    assert 'period: period.value' not in source


def test_default_range_is_last_seven_days():
    """默认落到最近 7 天（含今天），与改造前的默认一致。"""
    source = _normalized(TOKEN_STATS)

    assert "formatDayKey(addDays(new Date(), -6))" in source
    assert "formatDayKey(addDays(new Date(), 0))" in source or "formatDayKey(new Date())" in source


def test_all_three_requests_share_one_range():
    """趋势、分布、账单必须用同一个区间参数对象，不能各传各的。"""
    source = _normalized(TOKEN_STATS)
    body = source[source.index("const refreshAll = async") : source.index("const trendChartOption")]

    assert "const rangeParams = { start_date: start, end_date: end };" in body
    assert body.count("params: rangeParams") == 3, "三个接口都要带同一组区间参数"
    assert "days: daysVal" not in body, "不应再回落到 days 参数"
    assert "params: { period:" not in body


def test_range_switch_triggers_refresh():
    source = _normalized(TOKEN_STATS)

    assert '@change="onRangeChange"' in source
    assert "const onRangeChange = () => {" in source
    assert "refreshAll();" in source[source.index("const onRangeChange") :][:200]


def test_applied_range_is_visible_on_the_page():
    """区间生效处要能看见具体日期，否则用户无法核对数字对应哪几天。"""
    source = _read(TOKEN_STATS)

    assert "{{ rangeLabel }}" in source
    assert "const rangeLabel = computed" in source


# --------------------------------------------------------------------------- #
# DateRangePicker.vue：交互要求
# --------------------------------------------------------------------------- #


def test_picker_exposes_panel_and_shortcuts():
    source = _read(PICKER)

    assert "data-date-range-panel" in source
    assert "快捷选择" in source
    for preset in (
        '"today"',
        '"yesterday"',
        '"last7"',
        '"last14"',
        '"last30"',
        '"last90"',
        '"thisMonth"',
        '"lastMonth"',
    ):
        assert preset in source, f"缺少快捷项 {preset}"


def test_future_dates_are_disabled():
    """未来日期不能选：统计里没有未来数据，选了只会得到一片 0。"""
    source = _normalized(PICKER)

    assert "function isDisabled(key: string): boolean" in source
    assert "key > todayKey.value" in source
    assert ":disabled=\"isDisabled(cell.key)\"" in source


def test_span_over_limit_is_blocked_with_visible_message():
    """超过 90 天必须当场提示，而不是静默截断或直接发出注定 400 的请求。"""
    source = _normalized(PICKER)

    assert "maxDays" in source
    assert "span > props.maxDays" in source
    assert "超过上限" in source, "超限时要有可读提示"
    assert "if (span > props.maxDays)" in source
    # 超限分支必须在 commit 之前 return，不能先发请求再报错
    guard_at = source.index("if (span > props.maxDays)")
    commit_at = source.index("commit();", guard_at)
    assert "return" in source[guard_at:commit_at], "超限时应中断提交"


def test_single_day_range_is_allowed():
    """同一天起始 = 单日统计，必须能选（这是「任意日期」的一部分）。"""
    source = _normalized(PICKER)

    assert "inclusiveDaySpan(draftStart.value, draftEnd.value)" in source
    assert "（单日）" in source


def test_panel_closes_on_outside_click_and_escape():
    source = _read(PICKER)

    assert 'document.addEventListener("mousedown", onDocumentMouseDown);' in source
    assert 'event.key === "Escape"' in source
    assert "onBeforeUnmount" in source, "监听器必须在卸载时移除"


def test_trigger_shows_both_preset_name_and_real_dates():
    """只显示「最近 7 天」不够——用户需要看到实际生效的日期。"""
    source = _read(PICKER)

    assert "triggerLabel" in source
    assert "triggerSubLabel" in source
    assert "formatMonthDay" in source


def test_narrow_screen_falls_back_to_bottom_sheet():
    """644px 的面板在手机上放不下，窄屏要贴底展示。"""
    source = _read(PICKER)

    assert "max-[720px]:fixed" in source
    assert "max-[720px]:inset-x-3" in source


def test_shortcuts_respect_max_days():
    """上限若被调用方调小（例如 30 天），快捷项也不能给出超限的区间。"""
    source = _normalized(PICKER)

    assert "inclusiveDaySpan(preset.range.start, preset.range.end) <= props.maxDays" in source


def test_max_days_matches_backend_limit():
    """前端上限必须与后端 _resolve_token_stats_range 的 90 天一致。"""
    picker = _read(PICKER)
    token_stats = _read(TOKEN_STATS)

    assert "maxDays: 90" in picker
    assert ':max-days="90"' in token_stats


# --------------------------------------------------------------------------- #
# 日期工具
# --------------------------------------------------------------------------- #


def test_day_key_parsing_avoids_utc_shift():
    """`new Date("2026-11-05")` 按 UTC 解析，在东八区会退回 11/04。"""
    source = _read(DATE_RANGE_UTIL)

    # 直接拿日期字符串构造 Date 会按 UTC 解析；必须拆成 y/m/d 走本地时区
    assert "new Date(key" not in source
    assert "new Date(str" not in source
    assert "new Date(y, m - 1, d)" in source


def test_inclusive_day_span_counts_both_ends():
    source = _read(DATE_RANGE_UTIL)

    body = source[source.index("export function inclusiveDaySpan") :]
    body = body[: body.index("export function formatMonthDay")]
    assert "+ 1" in body, "区间天数要含首尾：11-01 到 11-05 是 5 天"
