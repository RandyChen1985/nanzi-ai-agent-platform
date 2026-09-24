"""工具调用的模型、温度与结果状态在会话时间线上的展示契约。

这些字段后端一直在 SSE 事件里上报（`assistant_agent_runner._build_tool_observation`
的 log event），但前端从未接住，导致排查时看不出「这条工具是哪个模型配的、
框架侧最终判定成什么状态」。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_frontend_forwards_tool_call_metadata_from_sse():
    handlers = _read("frontend/src/utils/agentscopeSseHandlers.ts")
    assert "tool_result_state: data.tool_result_state" in handlers
    assert "model: data.model" in handlers
    # 温度因为要排除 null 而写成多行，这里只锁语义，不锁排版。
    assert "temperature:" in handlers
    assert "data.temperature === null" in handlers

    timeline = _read("frontend/src/utils/processTimeline.ts")
    assert "model?: string;" in timeline
    assert "temperature?: number;" in timeline
    assert "tool_result_state?: string;" in timeline


def test_timeline_component_renders_tool_call_metadata():
    component = _read("frontend/src/components/chat/ChatExecutionTimeline.vue")
    assert "timelineMetaText" in component
    assert "模型" in component
    assert "温度" in component
    assert "工具状态" in component


def test_bash_intent_summary_is_wired_from_sse_to_timeline_row():
    """模型写的 Bash description 要从 SSE 一路透传到行标题（图 2 那种 `Bash · 意图`）。"""
    handlers = _read("frontend/src/utils/agentscopeSseHandlers.ts")
    assert "tool_summary" in handlers
    assert "data.tool_summary" in handlers

    timeline = _read("frontend/src/utils/processTimeline.ts")
    assert "tool_summary?: string;" in timeline
    assert "export function appendToolSummary" in timeline

    component = _read("frontend/src/components/chat/ChatExecutionTimeline.vue")
    assert "appendToolSummary" in component
    assert "item.tool_summary" in component
