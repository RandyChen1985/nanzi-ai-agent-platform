"""委派子智能体必须共享主会话的引用台账与 trace 缓冲。

两处都是「注释写着共享、实际各拿一份」的缺陷，正交于任何新功能：

1. `knowledge_citation_ledger` 从未传进子上下文。`new_turn_knowledge_citation_ledger`
   在 `delegation_depth > 0` 时只会「沿用 ctx 上已有的实例」，而 ctx 上是 None，
   于是子代理新建台账、编号从 1 重新发号，与主会话撞车。
2. `trace_buffer=main_ctx.trace_buffer` 传进构造函数只是 Pydantic 校验时复制的副本，
   子代理写的物理步骤落在随后被丢弃的 list 上。

这些断言直接抓子上下文实例，而不是比对源码文本——后者改名就失效。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.no_infrastructure

from app.core.context import AgentContext
from app.services.ai.tools import agent_delegate_tool as mod
from app.services.ai.knowledge_citation_ledger import (
    KnowledgeCitationLedger,
    new_turn_knowledge_citation_ledger,
)


class _FakeSession:
    async def __aenter__(self):
        return MagicMock()

    async def __aexit__(self, *exc):
        return False


def _fake_target_config() -> SimpleNamespace:
    return SimpleNamespace(
        agent_id=7,
        agent_name="data-agent",
        agent_display_name="数据分析助手",
        engine_type="LOCAL",
        engine_config={},
        tools=["execute_sql_query"],
        skills=[],
        skills_custom=False,
        is_active=True,
    )


def _empty_stream_executor() -> SimpleNamespace:
    """执行器只返回一个立即结束的空流，避免测试跑进真实的消费逻辑。"""

    def _execute(_history):
        async def _gen():
            return
            yield  # noqa: unreachable —— 让它成为异步生成器

        return _gen()

    return SimpleNamespace(execute=_execute)


async def _capture_child_context(monkeypatch, main_ctx: AgentContext) -> AgentContext:
    fake_agent = SimpleNamespace(
        name="data-agent",
        agent_name="data-agent",
        agent_id=7,
        is_active=True,
    )

    monkeypatch.setattr(mod, "AsyncSessionLocal", _FakeSession)
    monkeypatch.setattr(
        mod, "load_delegable_system_agent_candidates", AsyncMock(return_value=[fake_agent])
    )
    # 候选链路已收敛到三段式统一出口：名单层 / 权限层 / 就绪层，这里直接让三段都返回同一批。
    monkeypatch.setattr(
        mod,
        "resolve_delegable_agent_stages",
        AsyncMock(
            side_effect=lambda session, **kwargs: (
                [fake_agent],
                [fake_agent],
                [fake_agent],
            )
        ),
    )
    monkeypatch.setattr(
        mod.AgentManagerService,
        "get_active_agent_config",
        AsyncMock(return_value=_fake_target_config()),
    )
    monkeypatch.setattr(
        mod, "_dispatch_sub_agent_executor", AsyncMock(return_value=_empty_stream_executor())
    )

    captured: list[AgentContext] = []
    real_set = mod.set_agent_context

    def _capture(ctx):
        if ctx is not None:
            captured.append(ctx)
        return real_set(ctx)

    monkeypatch.setattr(mod, "set_agent_context", _capture)
    # 没有当前上下文时 sub_agent_call 会在第一行就返回，必须先把主上下文装上。
    real_set(main_ctx)

    # 模块里暴露的是 AgentScope 的 FunctionTool 包装，原函数挂在 `.func` 上
    await mod.sub_agent_call.func(agent_name="data-agent", query="查一下昨天的订单量")

    child = [
        ctx
        for ctx in captured
        if str(ctx.conversation_id or "").startswith("child_session_")
    ]
    assert child, "未能捕获子上下文"
    return child[0]


def _main_ctx() -> AgentContext:
    ctx = AgentContext(
        agent_id="main-agent",
        agent_name="主助手",
        conversation_id="conv-1",
        delegation_depth=0,
        trace_id="trace-main",
        user_id=1,
    )
    ctx.trace_buffer = []
    return ctx


async def test_delegated_child_shares_citation_ledger(monkeypatch):
    main_ctx = _main_ctx()
    # 这个函数返回台账本身，同时把它挂到 ctx.knowledge_citation_ledger 上。
    ledger = new_turn_knowledge_citation_ledger(main_ctx)
    assert isinstance(ledger, KnowledgeCitationLedger)
    assert main_ctx.knowledge_citation_ledger is ledger
    # 主会话先发一个号，子代理必须接着往下发，而不是从 1 重来。
    first_id = ledger.register({"doc_name": "主会话第一条引用"})

    child = await _capture_child_context(monkeypatch, main_ctx)

    assert child.knowledge_citation_ledger is main_ctx.knowledge_citation_ledger
    # 交叉验证：在「子上下文」上取本轮台账，拿到的必须是同一个实例。
    assert new_turn_knowledge_citation_ledger(child) is main_ctx.knowledge_citation_ledger
    assert main_ctx.knowledge_citation_ledger is not None
    assert first_id


async def test_delegated_child_shares_trace_buffer_by_reference(monkeypatch):
    main_ctx = _main_ctx()
    main_ctx.trace_buffer.append({"span": "main-step"})

    child = await _capture_child_context(monkeypatch, main_ctx)

    assert child.trace_buffer is main_ctx.trace_buffer
    child.trace_buffer.append({"span": "child-step"})
    assert {"span": "child-step"} in main_ctx.trace_buffer
