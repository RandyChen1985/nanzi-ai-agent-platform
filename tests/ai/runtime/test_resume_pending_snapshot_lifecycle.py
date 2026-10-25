"""契约：恢复轮跑完之前不得消费挂起快照（断网/刷新后仍可重试）。

两个恢复端点（权限确认 / 外部执行提交）在客户端读流中断时会被整体取消，而落库与
审计在流尾。此前是「进入会话锁后立刻 pop 快照」：一次断网就把快照删掉了——用户刷新后
点卡片只会拿到「请求不存在或已过期」，这一轮既不落库、也无处重试。

现在的约定：
1. 进锁后只 `peek` 复核，不消费。会话锁保证同一轮只有一个恢复者，复核足以挡住
   「排队等锁期间另一个请求已经跑完并消费掉快照」的重复恢复；
2. 本轮完整跑完（循环结束、仍在锁内、落库之前）才 `pop`；
3. 中途被取消（客户端断开）→ 不消费，快照保留，用户可以直接重试。
"""
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.ai.agent_service import AgentService
from app.services.ai.runtime.agentscope import resume as resume_module
from app.services.ai.runtime.agentscope.confirmations import (
    PendingAgentScopeConfirmation,
    pending_agentscope_confirmations,
)
from app.services.ai.runtime.agentscope.pending_store import PendingAgentScopeSnapshot

pytestmark = [pytest.mark.asyncio, pytest.mark.no_infrastructure]

STREAM_CHUNKS = (
    {"type": "answer_delta", "content": "确认后继续输出的"},
    {"type": "answer_delta", "content": "第一段正文。"},
)


def _confirmation(kind: str) -> PendingAgentScopeConfirmation:
    snapshot = PendingAgentScopeSnapshot(
        request_id="req-1",
        kind=kind,
        user_id="u1",
        conversation_id="c1",
        trace_id="trace-1",
        reply_id="reply-1",
        agent_name="GeneralAgent",
        tool_call={"id": "call_1", "name": "bash", "input": "{}"},
        agent_state={},
        stream_state={},
        runner_context={"runner_type": "general", "config": {}},
    )
    return PendingAgentScopeConfirmation.from_snapshot(snapshot)


class _FakeRunner:
    """只提供恢复流需要的最小表面；真实 runner 由 build_* 的补丁替代。"""

    def __init__(self) -> None:
        self.config = SimpleNamespace(model_name="m1", agent_name="GeneralAgent")
        self.conversation_id = "c1"
        self.trace_buffer = []

    async def resume_agentscope_native_confirmation(self, pending, *, confirmed=True):
        for chunk in STREAM_CHUNKS:
            yield chunk

    async def resume_agentscope_external_execution(self, pending, *, execution_results):
        for chunk in STREAM_CHUNKS:
            yield chunk


def _install(monkeypatch, kind: str, events: list[str]) -> _FakeRunner:
    runner = _FakeRunner()

    @contextlib.asynccontextmanager
    async def _noop_context(*args, **kwargs):
        yield None

    async def _peek(*args, **kwargs):
        return _confirmation(kind)

    async def _pop(*args, **kwargs):
        events.append("pop")
        return _confirmation(kind)

    monkeypatch.setattr(resume_module, "track_conversation_run", _noop_context)
    monkeypatch.setattr(resume_module.conversation_run_lane, "hold", _noop_context)
    monkeypatch.setattr(pending_agentscope_confirmations, "peek_async", _peek)
    monkeypatch.setattr(pending_agentscope_confirmations, "pop_async", _pop)
    monkeypatch.setattr(
        resume_module.AgentScopeResumeHandler,
        "build_agentscope_runner_from_pending",
        classmethod(lambda cls, pending, **kwargs: runner),
    )
    monkeypatch.setattr(
        resume_module.AgentScopeResumeHandler,
        "restore_runner_execution_context",
        classmethod(AsyncMock()),
    )
    # 落库/审计不在本契约范围内：跳过持久化，审计换成空实现。
    monkeypatch.setattr(
        "app.services.ai.agent_service._should_persist_turn_history",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        resume_module.AuditManager, "log_transaction", AsyncMock(return_value=True)
    )
    return runner


def _stream(flow: str):
    if flow == "permission":
        return AgentService().resume_agentscope_permission_stream(
            permission_request_id="req-1",
            confirmed=True,
            user_info=None,
        )
    return AgentService().resume_agentscope_external_execution_stream(
        external_execution_request_id="req-1",
        results=[{"id": "call_1", "name": "bash", "output": "ok"}],
        user_info=None,
    )


@pytest.mark.parametrize("flow", ["permission", "external"])
async def test_pending_snapshot_survives_a_client_disconnect(monkeypatch, flow):
    """客户端中途断开（生成器被关闭）→ 挂起快照必须保留，用户可重试。"""
    events: list[str] = []
    _install(monkeypatch, flow, events)
    stream = _stream(flow)

    # 第一帧是入锁前的确认结果；再取一帧确认已进入恢复循环。
    first = await stream.__anext__()
    assert first["type"] in {"permission_result", "external_execution_result"}
    assert (await stream.__anext__())["type"] == "answer_delta"

    await stream.aclose()  # 等价于客户端断开：Starlette 取消响应/关闭生成器

    assert "pop" not in events, "恢复流未跑完就消费了挂起快照，断网后无法重试"


@pytest.mark.parametrize("flow", ["permission", "external"])
async def test_completed_resume_consumes_the_snapshot_inside_the_lock(monkeypatch, flow):
    """本轮完整跑完才消费，且必须早于终态帧（落库/审计在锁外，不能更晚）。"""
    events: list[str] = []
    _install(monkeypatch, flow, events)

    async for chunk in _stream(flow):
        events.append(str(chunk.get("type") or ""))

    assert events.count("answer_delta") == len(STREAM_CHUNKS)
    assert events.count("pop") == 1
    last_chunk_at = max(
        index for index, event in enumerate(events) if event == "answer_delta"
    )
    assert events.index("pop") > last_chunk_at, "必须在恢复流跑完之后才消费"
    assert events.index("pop") < events.index("run_status"), "消费必须早于终态帧"
