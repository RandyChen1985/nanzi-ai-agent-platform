import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
OVERVIEW = ROOT / "frontend/src/views/Overview.vue"
DASHBOARD = ROOT / "frontend/src/components/dashboard"

pytestmark = pytest.mark.no_infrastructure


def _overview() -> str:
    return OVERVIEW.read_text(encoding="utf-8")


def _admin_block(source: str) -> str:
    start = source.index("userInfo?.role === 'admin'")
    end = source.index('<template v-else-if="userInfo">')
    return source[start:end]


def _user_block(source: str) -> str:
    start = source.index('<template v-else-if="userInfo">')
    end = source.index("<!-- 身份未知")
    return source[start:end]


def test_both_views_lead_with_the_same_hero_card() -> None:
    """两个视角都要有主角卡；管理员看平台成功率，普通用户看自己的。"""
    source = _overview()
    admin, user = _admin_block(source), _user_block(source)

    assert admin.count("<MetricHeroCard") == 1
    assert 'label="接口成功率"' in admin
    assert user.count("<MetricHeroCard") == 1
    assert 'label="我的成功率"' in user


def test_overview_no_longer_renders_the_pie_health_panel() -> None:
    """环形图的 legend 没有滚动与高度上限，工具一多就被裁切；已改为排行列表。"""
    source = _overview()

    for retired in (
        "AgentHealthPanel",
        "AgentPerformancePanel",
        "AgentTokenDistributionChart",
        "StatsCard",
    ):
        assert retired not in source


def test_ranking_bars_measure_share_of_total() -> None:
    """条形长度必须与右侧百分比同源。

    曾按最大值归一化：条最长的一项显示 100%，旁边的百分比却是 40%，
    同一条数据在两个位置给出两套读数。
    """
    source = (DASHBOARD / "ToolUsageRanking.vue").read_text(encoding="utf-8")

    assert "const totalCount = computed(" in source
    assert "count / totalCount.value" in source
    # 不允许把条形按最大值归一化
    assert "Math.max(..." not in source


def test_ranking_truncates_long_tool_names_with_title_hint() -> None:
    """MCP 工具名长达 40+ 字符，必须留 title 提示而不是硬折行。"""
    source = (DASHBOARD / "ToolUsageRanking.vue").read_text(encoding="utf-8")

    assert ':title="row.name"' in source
    assert "truncate" in source


def test_latency_trend_marks_threshold_and_peak() -> None:
    """耗时趋势要能直接读出「哪段超过阈值」，以及峰值是多少。"""
    source = (DASHBOARD / "LatencyTrendPanel.vue").read_text(encoding="utf-8")

    assert "MarkLineComponent" in source
    assert "MarkAreaComponent" in source
    assert "MarkPointComponent" in source
    assert "threshold: 5000" in source
    assert "type: 'max'" in source


def test_alert_strip_is_only_rendered_when_calls_failed() -> None:
    """没有失败时不显示黄色告警条，否则「全绿」也会被读成有事发生。"""
    source = _overview()

    assert source.count('<DashboardAlertStrip') == 2
    assert source.count('v-if="apiFailed > 0"') == 2


def test_admin_view_keeps_platform_metrics_and_user_view_does_not() -> None:
    """普通用户不该看到平台级用户数；这是他无权解读、也无从动作的指标。"""
    source = _overview()
    admin, user = _admin_block(source), _user_block(source)

    assert ':value="totalUsers"' in admin
    assert "totalUsers" not in user
    # 普通用户换成自己可动作的两项
    assert 'label="API Key"' in user
    assert 'label="平均响应"' in user


def test_recent_call_list_differs_by_role() -> None:
    """管理员关注新增用户，普通用户关注自己刚才问了什么。"""
    source = _overview()
    admin, user = _admin_block(source), _user_block(source)

    assert "<RecentUsersCard" in admin
    assert "<RecentUsersCard" not in user
    assert 'title="我的最近调用"' in user


def test_every_mini_metric_card_passes_an_icon_color() -> None:
    """图标颜色必须显式传。

    漏传会静默退回灰色——页面上只是「灰了一点」，扫一眼根本发现不了；
    而 mock 里四个指标是各有语义色的（调用紫、Token 橙、用户蓝、成功率绿）。
    """
    source = _overview()
    cards = re.findall(r"<MetricMiniCard\b.*?>", source, re.S)

    assert len(cards) == 8, f"预期 8 张次级卡，实际 {len(cards)}"
    for card in cards:
        assert "icon-color=" in card, card

    # 与设计稿逐项对齐
    for expected in ('icon-color="#7c3aed"', 'icon-color="#ea580c"',
                     'icon-color="#2563eb"', 'icon-color="#059669"'):
        assert source.count(expected) == 2, expected
