"""轨迹视图里的「图像用量」展示契约。

背景：图像接口是真正花钱的那次调用，平台原来只统计 LLM token，生图/改图零留痕。
后端已经把张数、尺寸、上游 token、耗时写进
`ai_agent_execution_traces.meta_info.image_usage`（见 `tests/test_image_usage_telemetry.py`），
这里锁住前端展示的一侧。

判定口径的注释在 `frontend/src/utils/traceUsage.ts`，几条都是刻意保留的：

- **只在真有用量时出现**：绝大多数工具没有这一项，不能平白多一个空块；
- **出一部分要显示 ``1/2``**：只说「1 张」会把「另一张没出来」盖掉；
- **拿不到 token 要如实说**：显示 0 或留空都会被读成「这次没花钱」。

另外，展示有两个入口——AgentDebug 等模块用的 `TraceLogViewer` 弹窗，和聊天日志页
`ChatLogs` 自己那套渲染。两边必须共用 `traceUsage.ts` 的口径，否则改一边忘一边，
同一个数字在两个页面显示得不一样。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
UTILS = "frontend/src/utils/traceUsage.ts"
VIEWER = "frontend/src/components/TraceLogViewer.vue"
CHAT_LOGS = "frontend/src/views/ChatLogs.vue"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_usage_rules_live_in_one_shared_module():
    """口径只能有一处：两个页面各写一份，迟早会漂。"""
    utils = _read(UTILS)
    assert "export function imageUsageFromStep" in utils
    assert "export function imageUsageChips" in utils
    assert "export function formatUsageDuration" in utils

    for component in (VIEWER, CHAT_LOGS):
        source = _read(component)
        assert "from '@/utils/traceUsage'" in source or 'from "@/utils/traceUsage"' in source
        # 不许在组件里重新实现一份
        assert "asked > done" not in source


def test_usage_is_read_from_trace_meta():
    utils = _read(UTILS)
    # 数据源就是后端写入的那一列
    assert "meta_info" in utils
    assert ".image_usage" in utils
    # 老轨迹没有这一项、或值不是对象，都当作「没有用量」
    assert 'typeof usage === "object"' in utils


def test_partial_count_shows_both_numbers():
    utils = _read(UTILS)
    assert "asked > done" in utils
    assert "`出图 ${done}/${asked} 张`" in utils


def test_missing_upstream_tokens_is_stated_not_faked():
    utils = _read(UTILS)
    # 上游不返回 usage 是常态，不能显示成 0 tokens
    assert "上游未返回 token 用量" in utils
    assert "`${tokens} tokens`" in utils


def test_both_trace_views_render_the_usage_block():
    """两个入口都要显示，只接一个等于「这个页面看不到、那个页面看得到」。"""
    for component in (VIEWER, CHAT_LOGS):
        source = _read(component)
        assert 'v-if="imageUsageFromStep(step)"' in source, component
        assert 'v-for="chip in imageUsageChips(step)"' in source, component


def test_viewer_can_expand_raw_meta_info():
    """弹窗视图保留原始 JSON：排查「这个数字哪来的」时要能一眼看到原文。"""
    source = _read(VIEWER)
    assert 'v-html="highlightJsonHtml(step.meta_info)"' in source
    assert "expandedSteps.value[`usage-${idx}`] = false;" in source
