"""工具入参（Bash 命令）在会话执行时间线上的展示契约。

用户排查「这条 Bash 到底跑了什么命令」时，时间线卡片此前只展示工具输出，
命令本身既不在副本里也不在事件里。命令必须作为独立字段 tool_args 透传，
与 details（工具输出）互不覆盖。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_backend_tool_events_carry_tool_args_for_timeline_cards():
    runner = _read("app/services/ai/runners/assistant_agent_runner.py")
    assert "format_tool_args_for_display" in runner
    assert 'log_event["tool_args"]' in runner

    stream = _read("app/services/ai/runtime/agentscope/event_stream.py")
    assert "format_tool_args_for_display" in stream
    assert 'start_log["tool_args"]' in stream
    # 写死的假占位必须消失，否则进行中的卡片会显示「参数: {}」。
    assert '"参数: {}"' not in stream


def test_frontend_forwards_tool_args_from_sse_into_timeline_items():
    handlers = _read("frontend/src/utils/agentscopeSseHandlers.ts")
    assert "tool_args: data.tool_args" in handlers

    timeline = _read("frontend/src/utils/processTimeline.ts")
    assert "tool_args?: string;" in timeline


def test_timeline_component_renders_tool_args_block():
    component = _read("frontend/src/components/chat/ChatExecutionTimeline.vue")
    assert "TimelineToolArgsBlock" in component
    assert "timelineArgsText" in component

    block = _read("frontend/src/components/chat/TimelineToolArgsBlock.vue")
    assert "{{ text }}" in block
    assert "命令" in block
    assert "参数" in block


def test_details_copy_button_never_shares_container_with_args_block():
    """入参区块与「工具输出」复制按钮必须分属不同 relative 容器。

    两者同容器时，输出复制按钮（absolute right-1 top-1.5 z-10）会几何重叠并
    盖住入参区块自己的复制按钮，点击「复制命令」实际复制的是工具输出。
    """
    component = _read("frontend/src/components/chat/ChatExecutionTimeline.vue")

    detail_containers = [
        line
        for line in component.splitlines()
        if 'class="group/details relative' in line and "border-t" in line
    ]
    assert len(detail_containers) == 4, "四个层级的输出区块都要独立成容器"
    for line in detail_containers:
        assert "hasVisibleTimelineText" in line
        assert "hasTimelineArgs" not in line, (
            "输出复制按钮的容器条件不得包含入参判断，否则会在只有入参时渲染出挡住的空按钮"
        )


def test_tool_args_copy_button_has_explicit_accessible_name():
    block = _read("frontend/src/components/chat/TimelineToolArgsBlock.vue")
    assert "'复制' + label" in block


def test_secondary_tool_emitters_forward_tool_args_too():
    """知识库 / ChatBI 链路同样把命令、SQL 这类入参透传到卡片，避免只有主链路可见。"""
    knowledge = _read("app/services/ai/runners/knowledge_agent_runner.py")
    # 知识库复用父类 _build_tool_observation 的结果，从中透传入参即可。
    assert 'observation.get("log", {}).get("tool_args")' in knowledge

    for path in (
        "app/services/ai/runners/chatbi/react_stream.py",
        "app/services/ai/runners/chatbi/schema_prefetch.py",
    ):
        source = _read(path)
        assert "format_tool_args_for_display" in source, path
        assert "tool_args" in source, path
