"""会话运行时构建分段耗时（runtime:build）的行为契约。

背景：`_build_native_agent` 是「只返回对象、不 yield」的阻塞段，工作区准备 / 工具
绑定 / 工具集装配 / 配置加载 / 中间件 / Agent 构造全在里面。这段静默期只有 keepalive
心跳，用户看到的是「上一帧之后很久没有新事件」。本模块把分段耗时旁路推到时间线，
并在首个模型调用开始时补记「首 token 前的静默段」。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.ai.runtime.agentscope.build_progress import (
    BUILD_PROGRESS_LOG_ID,
    BUILD_STAGE_LABELS,
    BuildStageTimer,
    build_progress_event,
    emit_build_progress,
    format_build_details,
)

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]


def _clock(values: list[float]):
    """把 clock 固定成给定序列：__init__ 取一次，每个 mark 取一次，elapsed 取一次。"""
    iterator = iter(values)
    return lambda: next(iterator)


def test_stage_timer_accumulates_adjacent_segments():
    timer = BuildStageTimer(clock=_clock([0.0, 0.25, 0.30, 0.40]))
    assert timer.mark("config") == pytest.approx(250.0)
    assert timer.mark("workspace") == pytest.approx(50.0)
    assert timer.stages() == pytest.approx({"config": 250.0, "workspace": 50.0})
    # total 用「此刻」补齐最后一段，避免忘了 mark 时偏小
    assert timer.elapsed_ms() == pytest.approx(400.0)


def test_stage_timer_repeated_stage_accumulates():
    """同一阶段多次 mark（例如降级分支里又进一次工作区）必须累加，不能覆盖。"""
    timer = BuildStageTimer(clock=_clock([0.0, 0.10, 0.10, 0.35, 0.40]))
    timer.mark("workspace")
    timer.mark("tool_bind")
    timer.mark("workspace")
    assert timer.stages() == pytest.approx({"workspace": 350.0, "tool_bind": 0.0})


def test_stage_timer_blank_stage_only_advances_clock():
    """空阶段名只推进计时游标、不写脏 key（防御 mark 传空字符串）。"""
    timer = BuildStageTimer(clock=_clock([0.0, 0.10, 0.20, 0.30]))
    timer.mark("")
    timer.mark("agent")
    assert timer.stages() == pytest.approx({"agent": 100.0})


def test_mark_never_returns_negative_on_backwards_clock():
    """单调时钟被注入成回退值时也不能出现负耗时（时间线里负数是脏数据）。"""
    timer = BuildStageTimer(clock=_clock([1.0, 0.5, 1.2]))
    assert timer.mark("config") == pytest.approx(0.0)
    assert timer.elapsed_ms() == pytest.approx(200.0)


def test_format_build_details_orders_known_stages_then_unknown_then_total():
    stages = {
        "agent": 3.0,
        "workspace": 320.0,
        "tool_bind": 12.0,
        "config": 45.0,
    }
    details = format_build_details(stages, 400.0)
    lines = details.splitlines()

    # 已知阶段按 BUILD_STAGE_LABELS 的声明顺序，与 mark 顺序无关
    assert lines[0].startswith("工作区准备")
    assert lines[1].startswith("工具绑定")
    assert lines[2].startswith("模型与上下文配置")
    assert lines[3].startswith("Agent 构造")
    assert lines[-1] == "构建合计 400ms"
    # 耗时是整数毫秒，避免 "320.00000001ms" 这种噪声
    assert "320ms" in details


def test_format_build_details_keeps_unlabelled_stages_visible():
    """新增 mark 却忘了配展示名时，宁可显示原始 key，也不能让时间凭空消失。"""
    details = format_build_details({"new_stage": 9.0}, 9.0)
    assert "new_stage 9ms" in details
    assert "合计 9ms" in details


def test_format_build_details_appends_extra_lines_verbatim():
    details = format_build_details(
        {"workspace": 320.0},
        320.0,
        extra_lines=["", "首次模型调用前 12400ms（含上下文压缩/入参准备）"],
    )
    assert details.endswith("首次模型调用前 12400ms（含上下文压缩/入参准备）")
    assert "\n\n" not in details  # 空行被丢弃


def test_build_progress_event_uses_stable_id_for_frontend_merge():
    event = build_progress_event(status="success", details="合计 1ms")
    assert event["type"] == "log"
    assert event["id"] == BUILD_PROGRESS_LOG_ID == "runtime:build"
    assert event["title"] == "会话运行时准备"
    assert event["category"] == "system"
    assert event["details"] == "合计 1ms"
    assert event["status"] == "success"


def test_build_progress_event_clamps_tiny_durations_to_one_ms():
    """与 workspace:prepare 同口径：0ms 看起来像没测，下限给 1ms。"""
    assert build_progress_event(status="success", execution_time_ms=0.2)[
        "execution_time_ms"
    ] == pytest.approx(1.0)
    assert "execution_time_ms" not in build_progress_event(status="pending")


def test_emit_build_progress_is_noop_without_agent_context():
    with patch("app.core.context.get_current_agent_context", return_value=None):
        asyncio.run(emit_build_progress(status="success", details="x"))
    with patch(
        "app.core.context.get_current_agent_context",
        return_value=SimpleNamespace(event_queue=None),
    ):
        asyncio.run(emit_build_progress(status="success", details="x"))


def test_emit_build_progress_pushes_event_into_queue():
    queue: asyncio.Queue = asyncio.Queue()

    async def _run():
        with patch(
            "app.core.context.get_current_agent_context",
            return_value=SimpleNamespace(event_queue=queue),
        ):
            await emit_build_progress(
                status="success",
                details="工作区准备 1ms\n合计 1ms",
                execution_time_ms=1.2,
            )
        return queue.get_nowait()

    event = asyncio.run(_run())
    assert event["id"] == BUILD_PROGRESS_LOG_ID
    assert event["status"] == "success"
    assert event["details"].startswith("工作区准备")
    assert event["execution_time_ms"] == pytest.approx(1.2)


def test_emit_build_progress_skips_delegated_subagents():
    """子代理的构建卡会与父级同 id 互相覆盖，必须跳过。"""
    for ctx in (
        SimpleNamespace(event_queue=asyncio.Queue(), delegation_depth=1),
        SimpleNamespace(
            event_queue=asyncio.Queue(),
            delegation_depth=0,
            parent_conversation_id="main-conversation",
        ),
    ):
        with patch("app.core.context.get_current_agent_context", return_value=ctx):
            asyncio.run(emit_build_progress(status="success", details="x"))
        assert ctx.event_queue.empty() is True


def test_emit_build_progress_swallows_queue_failures():
    """旁路投递失败绝不能打断本轮构建。"""

    class _BrokenQueue:
        async def put(self, _item):
            raise RuntimeError("queue closed")

    with patch(
        "app.core.context.get_current_agent_context",
        return_value=SimpleNamespace(event_queue=_BrokenQueue()),
    ):
        asyncio.run(emit_build_progress(status="success", details="x"))


# ---------------------------------------------------------------------------
# 接线契约：两条构建路径都要埋点，且阶段顺序与真实调用顺序一致
# ---------------------------------------------------------------------------


def _assistant_source() -> str:
    return (ROOT / "app/services/ai/runners/assistant_agent_runner.py").read_text(
        encoding="utf-8"
    )


def _chatbi_builder_source() -> str:
    return (ROOT / "app/services/ai/runners/chatbi/agent_builder.py").read_text(
        encoding="utf-8"
    )


def test_assistant_builder_marks_every_stage():
    source = _assistant_source()
    for stage in BUILD_STAGE_LABELS:
        assert f'build_timer.mark("{stage}")' in source, f"缺少阶段埋点：{stage}"
    assert "await self._emit_build_timing()" in source
    assert "return agent" in source


def test_assistant_builder_marks_in_call_order():
    """顺序错了分段会把耗时记到别的阶段上，比没有埋点更误导。"""
    source = _assistant_source()
    body = source[source.index("async def _build_native_agent("):]
    order = [
        body.index('build_timer.mark("config")'),
        body.index('build_timer.mark("workspace")'),
        body.index('build_timer.mark("tool_bind")'),
        body.index('build_timer.mark("toolkit")'),
        body.index('build_timer.mark("middleware")'),
        body.index('build_timer.mark("agent")'),
        body.index("await self._emit_build_timing()"),
    ]
    assert order == sorted(order)


def test_assistant_builder_emits_after_agent_constructed():
    """必须在 Agent 构造之后才发卡：提前发会把构造耗时漏掉。"""
    source = _assistant_source()
    body = source[source.index("async def _build_native_agent("):]
    assert body.index("agent = Agent(") < body.index("await self._emit_build_timing()")


def test_builder_emits_pending_before_stage_marks():
    """等待期间必须已经有一张可见卡：构建段只有 keepalive 心跳，用户会以为卡死。"""
    assistant = _assistant_source()
    body = assistant[assistant.index("async def _build_native_agent("):]
    assert 'status="pending"' in body
    assert body.index('status="pending"') < body.index('build_timer.mark("config")')

    chatbi = _chatbi_builder_source()
    assert 'status="pending"' in chatbi
    assert chatbi.index('status="pending"') < chatbi.index('build_timer.mark("workspace")')


def test_first_model_call_wait_is_logged_once_and_after_build():
    source = _assistant_source()
    assert "await self._emit_first_model_call_wait()" in source
    # 只在 MODEL_CALL_START 分支补记
    assert "if event_type == \"MODEL_CALL_START\":" in source
    # 守卫：重复模型调用不再补记
    assert "if getattr(self, \"_first_model_call_wait_logged\", False):" in source


def test_chatbi_builder_marks_and_emits():
    source = _chatbi_builder_source()
    for stage in BUILD_STAGE_LABELS:
        assert f'build_timer.mark("{stage}")' in source, f"ChatBI 缺少阶段埋点：{stage}"
    assert "await emit_build_progress(" in source
    # 对比「最后一次」emit：pending 卡在构建开头、汇总卡在 Agent 构造之后
    assert source.index("agent = dar.Agent(**kwargs)") < source.rindex(
        "await emit_build_progress("
    )
