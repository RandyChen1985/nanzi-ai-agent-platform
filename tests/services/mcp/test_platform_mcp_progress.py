"""平台自建 MCP 服务端：长工具要主动上报进度。

远端能否发 ``notifications/progress`` 取决于服务端是否调用
``Context.report_progress``；mcp 2.x 在调用方没带 progressToken 时它是 no-op，
所以「加了也不会有副作用」。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services.mcp import platform_mcp

pytestmark = pytest.mark.no_infrastructure


class _RecordingCtx:
    def __init__(self) -> None:
        self.calls: list[tuple[float, float | None, str | None]] = []

    async def report_progress(
        self,
        progress: float,
        total: float | None = None,
        message: str | None = None,
    ) -> None:
        self.calls.append((progress, total, message))


class _NullSession:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False


def _principal() -> SimpleNamespace:
    return SimpleNamespace(claims={"request_id": "req-1"}, user_id=1)


async def test_agent_call_common_reports_three_stages(monkeypatch):
    async def _noop(*args, **kwargs):
        return None

    async def _agent(*args, **kwargs):
        return {"status": "completed", "content": "hi"}

    async def _load_client(*args, **kwargs):
        return SimpleNamespace()

    async def _load_user(*args, **kwargs):
        return None, {"user_id": 1}

    async def _load_agent(*args, **kwargs):
        return SimpleNamespace(id="agent-1")

    monkeypatch.setattr(platform_mcp, "AsyncSessionLocal", lambda: _NullSession())
    monkeypatch.setattr(platform_mcp, "_load_client", _load_client)
    monkeypatch.setattr(platform_mcp, "_load_principal_user", _load_user)
    monkeypatch.setattr(platform_mcp, "_load_authorized_agent", _load_agent)
    monkeypatch.setattr(platform_mcp, "_invoke_agent", _agent)
    monkeypatch.setattr(platform_mcp, "_write_audit", _noop)

    ctx = _RecordingCtx()
    await platform_mcp._agent_call_common(
        _principal(),
        method_name="agent_invoke",
        agent_id="agent-1",
        message="hi",
        conversation_id=None,
        client_request_id=None,
        ctx=ctx,
    )

    assert len(ctx.calls) == 3, ctx.calls
    progresses = [call[0] for call in ctx.calls]
    assert progresses == [1, 2, 3], progresses
    assert all(call[1] == 3 for call in ctx.calls), ctx.calls
    assert "智能体" in str(ctx.calls[1][2]), ctx.calls[1]


async def test_agent_call_common_without_ctx_is_silent(monkeypatch):
    async def _noop(*args, **kwargs):
        return None

    async def _agent(*args, **kwargs):
        return {"status": "completed", "content": "hi"}

    async def _load_client(*args, **kwargs):
        return SimpleNamespace()

    async def _load_user(*args, **kwargs):
        return None, {"user_id": 1}

    async def _load_agent(*args, **kwargs):
        return SimpleNamespace(id="agent-1")

    monkeypatch.setattr(platform_mcp, "AsyncSessionLocal", lambda: _NullSession())
    monkeypatch.setattr(platform_mcp, "_load_client", _load_client)
    monkeypatch.setattr(platform_mcp, "_load_principal_user", _load_user)
    monkeypatch.setattr(platform_mcp, "_load_authorized_agent", _load_agent)
    monkeypatch.setattr(platform_mcp, "_invoke_agent", _agent)
    monkeypatch.setattr(platform_mcp, "_write_audit", _noop)

    # ctx=None（调用方没提供 progressToken）必须正常工作，不受进度逻辑影响
    result = await platform_mcp._agent_call_common(
        _principal(),
        method_name="agent_invoke",
        agent_id="agent-1",
        message="hi",
        conversation_id=None,
        client_request_id=None,
        ctx=None,
    )

    assert result["status"] == "completed"


async def test_report_progress_swallows_transport_errors():
    class _BrokenCtx:
        async def report_progress(self, *args, **kwargs):
            raise RuntimeError("connection closed")

    # 进度是可观测性支线：上报失败绝不能让工具失败
    await platform_mcp._report_progress(_BrokenCtx(), 1, 3, "step")


async def test_knowledge_search_reports_stages(monkeypatch):
    async def _noop(*args, **kwargs):
        return None

    async def _true(*args, **kwargs):
        return True

    async def _scope(*args, **kwargs):
        return ["kb-1"], []

    async def _cfg(*args, **kwargs):
        return None

    class _Rag:
        def __init__(self, *args, **kwargs):
            pass

        async def retrieve(self, *args, **kwargs):
            return [{"content": "chunk"}]

    monkeypatch.setattr(platform_mcp, "_principal_from_context", _principal)
    monkeypatch.setattr(platform_mcp, "_require_scope", lambda *a, **k: None)
    monkeypatch.setattr(platform_mcp, "is_platform_mcp_enabled", _true)
    monkeypatch.setattr(platform_mcp, "is_platform_mcp_capability_enabled", _true)
    monkeypatch.setattr(platform_mcp, "is_knowledge_base_enabled", _true)
    monkeypatch.setattr(platform_mcp, "_resolve_knowledge_scope", _scope)
    monkeypatch.setattr(platform_mcp.ConfigService, "get", _cfg)
    monkeypatch.setattr(
        platform_mcp,
        "resolve_rag_retrieval_params",
        lambda **kwargs: (0.3, 0.7, 10),
    )
    monkeypatch.setattr(platform_mcp, "RagFlowClient", _Rag)
    monkeypatch.setattr(platform_mcp, "_write_audit", _noop)

    ctx = _RecordingCtx()
    result = await platform_mcp.knowledge_search("怎么退款", ctx=ctx)

    assert result["status"] == "ok"
    assert len(ctx.calls) >= 2, ctx.calls
    assert [call[0] for call in ctx.calls] == sorted(call[0] for call in ctx.calls), "进度必须单调递增"
    assert any("检索" in str(call[2]) for call in ctx.calls), ctx.calls
