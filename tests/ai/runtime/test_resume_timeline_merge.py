"""审批/外部执行恢复流落库前回捞并合并同 trace_id 前序时间线单测。"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.ai.runtime.agentscope.resume import AgentScopeResumeHandler

pytestmark = pytest.mark.no_infrastructure

RESUME_PATH = (
    Path(__file__).resolve().parents[3] / "app/services/ai/runtime/agentscope/resume.py"
)


async def test_merge_prior_turn_snapshot_merges_only_matching_trace_assistant_entries(monkeypatch):
    """只回捞同 trace_id 的 assistant 前序快照（忽略 user 与其他轮次），并与当前恢复轮合并。"""
    fake_history = [
        {"role": "user", "content": "查一下并跑脚本", "trace_id": "trace-1"},
        {
            "role": "assistant",
            "content": "",
            "trace_id": "trace-other",
            "reasoning_content": "别轮的思考",
            "process_timeline": [
                {"kind": "log", "id": "other_prep", "title": "别轮准备", "status": "success"}
            ],
        },
        {
            "role": "assistant",
            "content": "我先查到了 2 条记录，下面准备执行命令。",
            "trace_id": "trace-1",
            "reasoning_content": "审批前思考",
            "process_timeline": [
                {
                    "kind": "log",
                    "id": "preparation:auth_context_capability",
                    "title": "鉴权及上下文与能力准备",
                    "status": "success",
                    "category": "system",
                },
                {
                    "kind": "log",
                    "id": "bash_1",
                    "title": "调用工具: Bash",
                    "details": "",
                    "tool_args": "echo hi",
                    "status": "pending",
                    "category": "tool",
                },
            ],
        },
    ]

    async def _get_history(user_id: str, conversation_id: str, *, limit=None):
        assert user_id == "u-1"
        assert conversation_id == "conv-1"
        assert limit == 20
        return fake_history

    monkeypatch.setattr(
        "app.services.ai.memory_service.memory_service",
        SimpleNamespace(get_history=_get_history),
    )

    current_timeline = [
        {
            "kind": "log",
            "id": "permission_p1",
            "title": "已允许工具调用",
            "details": "确认请求: p1",
            "status": "success",
            "category": "permission",
        },
        {
            "kind": "log",
            "id": "bash_1",
            "title": "工具完成: Bash",
            "details": "hi",
            "status": "success",
            "category": "tool",
            "execution_time_ms": 120,
        },
    ]

    merged_timeline, merged_reasoning, merged_content = (
        await AgentScopeResumeHandler._merge_prior_turn_snapshot(
            user_id="u-1",
            conversation_id="conv-1",
            trace_id="trace-1",
            current_timeline=current_timeline,
            current_reasoning="审批后思考",
            current_content="命令已执行完成，输出 hi。",
        )
    )

    assert merged_timeline is not None
    assert [item["id"] for item in merged_timeline] == [
        "preparation:auth_context_capability",
        "bash_1",
        "permission_p1",
    ]
    bash = next(item for item in merged_timeline if item["id"] == "bash_1")
    assert bash["status"] == "success"
    assert bash["tool_args"] == "echo hi"
    assert bash["details"] == "hi"
    assert merged_reasoning == "审批前思考\n\n审批后思考"
    assert merged_content == "我先查到了 2 条记录，下面准备执行命令。\n\n命令已执行完成，输出 hi。"


async def test_merge_prior_turn_snapshot_gracefully_falls_back_on_redis_error(monkeypatch):
    """Redis 读取失败时不得抛错或阻断落库，应原样回退当前恢复轮快照。"""

    async def _boom(user_id: str, conversation_id: str, *, limit=None):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(
        "app.services.ai.memory_service.memory_service",
        SimpleNamespace(get_history=_boom),
    )

    current = [{"kind": "log", "id": "permission_p1", "title": "已允许工具调用", "status": "success"}]
    merged_timeline, merged_reasoning, merged_content = (
        await AgentScopeResumeHandler._merge_prior_turn_snapshot(
            user_id="u-1",
            conversation_id="conv-1",
            trace_id="trace-1",
            current_timeline=current,
            current_reasoning="恢复轮思考",
            current_content="恢复轮正文",
        )
    )

    assert merged_timeline == current
    assert merged_reasoning == "恢复轮思考"
    assert merged_content == "恢复轮正文"


def test_resume_flow_wires_prior_snapshot_merge_before_persist_and_audit():
    """契约：resume.py 在落库与审计前必须调用 _merge_prior_turn_snapshot，并把合并后的
    时间线、思考正文与回答正文同时传给两路落库。
    """
    source = RESUME_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert tree is not None

    assert source.count("cls._merge_prior_turn_snapshot(") >= 2
    assert "final_content=merged_response_content" in source
    assert "final_reasoning=merged_reasoning_content" in source
    assert "content=final_content" in source
    assert "reasoning_content=final_reasoning or None" in source


@pytest.mark.asyncio
@pytest.mark.parametrize("reused_in_process", [True, False])
async def test_resume_flow_passes_upsert_trace_slice_and_metric_flags(
    monkeypatch, reused_in_process: bool
):
    """同进程恢复只落新增 trace 且覆盖全量 Token；跨进程恢复累加 Token 并顺延 step_number。"""
    from contextlib import asynccontextmanager
    from datetime import datetime
    from unittest.mock import AsyncMock

    from app.schemas.agent import AgentExecutionStep
    from app.services.ai.runtime.agentscope import resume as resume_module
    from app.services.ai.runtime.agentscope.confirmations import (
        pending_agentscope_confirmations,
    )

    prior_step = AgentExecutionStep(
        step_number=1,
        event_type="model_call",
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
        span_id="span-prior",
        timestamp=datetime.now(),
    )
    new_step = AgentExecutionStep(
        step_number=2 if reused_in_process else 1,
        event_type="model_call",
        prompt_tokens=60,
        completion_tokens=15,
        total_tokens=75,
        span_id="span-resume",
        timestamp=datetime.now(),
    )

    class _FakeRunner:
        def __init__(self):
            self.conversation_id = "conv-resume-1"
            self.config = SimpleNamespace(
                agent_id="a1",
                agent_name="ops",
                agent_version="v1",
                model_name="m1",
            )
            self.trace_buffer = [prior_step] if reused_in_process else []

        async def resume_agentscope_native_confirmation(self, pending, *, confirmed):
            self.trace_buffer.append(new_step)
            yield {"content": "恢复完成"}

    runner = _FakeRunner()
    pending = SimpleNamespace(
        request_id="req-upsert-1",
        trace_id="trace-upsert-resume-1",
        user_id="u1",
        runner=runner if reused_in_process else None,
        tool_call=SimpleNamespace(id="call_1", name="web_fetch"),
        state={"user_query": "帮我处理"},
        snapshot=SimpleNamespace(
            kind="permission",
            conversation_id="conv-resume-1",
            delegation={},
            runner_context={},
        ),
    )

    @asynccontextmanager
    async def _noop_cm(*_args, **_kwargs):
        yield SimpleNamespace(cancelled=False)

    captured_kwargs = {}

    async def _fake_log_transaction(*args, **kwargs):
        captured_kwargs["args"] = args
        captured_kwargs["kwargs"] = kwargs
        return True

    monkeypatch.setattr(resume_module.conversation_run_lane, "hold", _noop_cm)
    monkeypatch.setattr(resume_module, "track_conversation_run", _noop_cm)
    monkeypatch.setattr(
        pending_agentscope_confirmations, "peek_async", AsyncMock(return_value=pending)
    )
    monkeypatch.setattr(
        pending_agentscope_confirmations, "pop_async", AsyncMock(return_value=pending)
    )
    monkeypatch.setattr(
        AgentScopeResumeHandler,
        "build_agentscope_runner_from_pending",
        classmethod(lambda cls, p, **kw: runner),
    )
    monkeypatch.setattr(
        AgentScopeResumeHandler,
        "restore_runner_execution_context",
        classmethod(AsyncMock()),
    )
    monkeypatch.setattr(
        AgentScopeResumeHandler,
        "_merge_prior_turn_snapshot",
        classmethod(
            AsyncMock(return_value=([], None, "恢复完成"))
        ),
    )
    monkeypatch.setattr(
        "app.services.ai.agent_service._should_persist_turn_history",
        lambda *a, **k: False,
    )
    monkeypatch.setattr(resume_module.AuditManager, "log_transaction", _fake_log_transaction)

    _ = [
        chunk
        async for chunk in AgentScopeResumeHandler.resume_permission_stream(
            SimpleNamespace(_quota_block_message=AsyncMock(return_value=None)),
            permission_request_id="req-upsert-1",
            confirmed=True,
            user_info={"user_id": "u1"},
        )
    ]

    kwargs = captured_kwargs["kwargs"]
    assert [s.span_id for s in kwargs["trace_logs_to_save"]] == ["span-resume"]
    assert kwargs["accumulate_duration"] is True
    assert kwargs["dedup_trace_logs"] is True
    assert kwargs["accumulate_tokens"] is (not reused_in_process)
    assert kwargs["offset_trace_steps"] is (not reused_in_process)
