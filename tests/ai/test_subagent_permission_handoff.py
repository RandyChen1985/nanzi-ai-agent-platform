"""子智能体工具授权交回主会话（A 文案 + B1 转发/续聊）的行为锁定。

背景：团队模式下子智能体执行 Bash 需要确认时，委派层把 permission_required 当成终止
中断，只返回「当前委派模式无法在子流程中完成确认」，确认卡永远到不了主会话。
"""

import asyncio
from contextlib import contextmanager

import pytest

from app.core.context import AgentContext


@pytest.mark.asyncio
async def test_permission_required_is_forwarded_to_parent_stream():
    """子代理的权限确认事件必须转发给父流，父会话才可能弹出确认卡。"""
    from app.services.ai.tools.agent_delegate_tool import _consume_sub_agent_stream

    async def sub_stream():
        yield {
            "type": "permission_required",
            "status": "pending",
            "permission_request_id": "perm_abc123",
            "title": "需要确认工具调用: Bash",
            "tool_call": {"id": "call_1", "name": "Bash", "args": {"command": "ls"}},
        }
        yield {"type": "answer_delta", "content": "授权后才能产生的正文"}

    eq = asyncio.Queue()
    ctx = AgentContext(agent_id="main", agent_name="MainAgent", event_queue=eq)
    full_output, interrupt = await _consume_sub_agent_stream(
        sub_stream(),
        main_ctx=ctx,
        sub_display_name="资产导出",
        subagent_metadata={"display_name": "资产导出", "run_id": "subrun_perm"},
    )

    assert interrupt == "permission_required"
    assert full_output == ""

    forwarded = []
    while not eq.empty():
        forwarded.append(await eq.get())
        eq.task_done()
    permission_events = [c for c in forwarded if c.get("type") == "permission_required"]
    assert len(permission_events) == 1
    event = permission_events[0]
    assert event["permission_request_id"] == "perm_abc123"
    # 父会话要知道这是哪个子智能体在请求授权，卡片上要显示名字。
    assert event["subagent"]["display_name"] == "资产导出"
    assert event["subagent"]["run_id"] == "subrun_perm"


@pytest.mark.asyncio
async def test_permission_required_message_points_to_confirmation_card():
    """文案必须给出可执行的动作，不能再说「打开对应子智能体对话」这种不存在的出路。"""
    from app.services.ai.tools.agent_delegate_tool import (
        DELEGATION_INTERRUPT_MESSAGES,
        PERMISSION_PENDING_SUB_AGENT_RESULT_MESSAGE,
    )

    message = DELEGATION_INTERRUPT_MESSAGES["permission_required"]
    assert "请直接打开对应子智能体对话" not in message
    assert "工具自动执行策略" not in message
    assert "确认卡" in message
    # 暂停不是失败：不再以「错误：」开头，否则批次统计会把它算成失败。
    assert not message.startswith("错误")
    assert message.startswith(PERMISSION_PENDING_SUB_AGENT_RESULT_MESSAGE)


def test_permission_pending_counts_as_waiting_not_failed():
    from app.services.ai.tools.agent_delegate_tool import (
        PERMISSION_PENDING_SUB_AGENT_RESULT_MESSAGE,
        _batch_result_status,
    )

    assert _batch_result_status(PERMISSION_PENDING_SUB_AGENT_RESULT_MESSAGE) == "needs_user_input"


def test_delegation_metadata_only_for_subagent_context():
    """主会话的确认不带委派来源；子智能体带上父会话与 run_id。"""
    from app.services.ai.runtime.agentscope.event_stream import _subagent_delegation_metadata

    main_ctx = AgentContext(agent_id="main", agent_name="主专家")
    assert _subagent_delegation_metadata(main_ctx) == {}

    child_ctx = AgentContext(
        agent_id="child",
        agent_name="资产导出",
        parent_conversation_id="conv_parent_1",
        delegation_run_id="subrun_perm",
        delegation_depth=1,
    )
    assert _subagent_delegation_metadata(child_ctx) == {
        "parent_conversation_id": "conv_parent_1",
        "run_id": "subrun_perm",
        "agent_name": "资产导出",
        "depth": 1,
    }


def test_pending_snapshot_keeps_delegation_across_redis_roundtrip():
    """快照经 asdict 落 Redis 后必须还原委派来源；旧快照没有该键时退回空 dict。"""
    import dataclasses
    import json

    from app.services.ai.runtime.agentscope.pending_store import PendingAgentScopeStore

    snapshot = PendingAgentScopeStore._from_dict(
        {
            "request_id": "perm_1",
            "kind": "permission",
            "user_id": "1",
            "conversation_id": "child_session_abc",
            "trace_id": "sub_trace",
            "reply_id": "reply_1",
            "agent_name": "资产导出",
            "tool_call": {"id": "call_1", "name": "Bash"},
            "agent_state": {},
            "stream_state": {},
            "runner_context": {},
            "delegation": {
                "parent_conversation_id": "conv_parent_1",
                "run_id": "subrun_perm",
            },
            "created_at": 1.0,
        }
    )
    assert snapshot is not None
    assert snapshot.delegation["parent_conversation_id"] == "conv_parent_1"

    # 写入方向：asdict 必须带上 delegation，否则 Redis 里根本没有这个键。
    payload = json.loads(json.dumps(dataclasses.asdict(snapshot), ensure_ascii=False))
    assert payload["delegation"]["run_id"] == "subrun_perm"

    legacy = PendingAgentScopeStore._from_dict(
        {
            "request_id": "perm_legacy",
            "kind": "permission",
            "user_id": "1",
            "conversation_id": "conv_main",
            "trace_id": "t",
            "reply_id": "r",
            "agent_name": "主专家",
            "tool_call": {},
            "agent_state": {},
            "stream_state": {},
            "runner_context": {},
            "created_at": 1.0,
        }
    )
    assert legacy is not None
    assert legacy.delegation == {}


@pytest.mark.asyncio
async def test_once_grant_is_consumed_exactly_once_and_matches_command():
    """一次性授权只放行签名完全一致的那一次调用，用过即失效。"""
    from app.services.ai.runtime.agentscope.session_permission_grants import (
        OncePermissionGrantStore,
        tool_args_fingerprint,
    )

    store = OncePermissionGrantStore()
    scope = "conv_handoff::sub::资产导出"
    fingerprint = tool_args_fingerprint({"command": "python export.py"})

    assert await store.consume(1, scope, "Bash", fingerprint) is False  # 没授权前不放行
    assert await store.grant(1, scope, "Bash", fingerprint) is True
    assert await store.consume(1, scope, "Bash", fingerprint) is True
    assert await store.consume(1, scope, "Bash", fingerprint) is False  # 用完即删

    # 授权的是 python export.py，换成 rm 不能蹭这次授权。
    assert await store.grant(1, scope, "Bash", fingerprint) is True
    other = tool_args_fingerprint({"command": "rm -rf /tmp/x"})
    assert await store.consume(1, scope, "Bash", other) is False
    assert await store.consume(1, scope, "Bash", fingerprint) is True


@pytest.mark.asyncio
async def test_once_grant_scope_isolates_subagents_and_other_sessions():
    """A 子代理的授权不放行 B 子代理，也不放行主会话。"""
    from app.services.ai.runtime.agentscope.session_permission_grants import (
        OncePermissionGrantStore,
        delegation_grant_scope,
        tool_args_fingerprint,
    )

    store = OncePermissionGrantStore()
    fingerprint = tool_args_fingerprint({"command": "ls"})
    scope_a = delegation_grant_scope("conv_1", "资产导出")
    scope_b = delegation_grant_scope("conv_1", "知识库助手")

    assert scope_a != scope_b
    assert delegation_grant_scope("", "资产导出") == ""  # 主会话没有委派作用域

    assert await store.grant(1, scope_a, "Bash", fingerprint) is True
    assert await store.consume(1, scope_b, "Bash", fingerprint) is False
    assert await store.consume(1, "conv_1", "Bash", fingerprint) is False
    assert await store.consume(1, scope_a, "Bash", fingerprint) is True


def _fake_bash_native(*, behavior: str = "ask", bypass_immune: bool = False):
    """最小的原生工具替身：作用域落到 ask，原生判定按参数返回。"""

    class FakeNative:
        name = "Bash"
        is_read_only = False

        def check_permissions(self, tool_input, context):
            from agentscope.permission import PermissionBehavior, PermissionDecision

            return PermissionDecision(
                behavior=(
                    PermissionBehavior.PASSTHROUGH
                    if behavior == "passthrough"
                    else PermissionBehavior.ASK
                ),
                message="需要确认",
                bypass_immune=bypass_immune,
            )

    return FakeNative()


@contextmanager
def _platform_context(ctx):
    """把平台 AgentContext 装进 contextvar——真实运行时就是这么给的。

    `check_permissions` 的 `context` 参数是 AgentScope 的 PermissionContext，
    委派身份只能从平台上下文取，所以测试必须同时摆好这两样，否则测的是假路径。
    """
    from app.core.context import get_current_agent_context, set_agent_context

    previous = get_current_agent_context()
    set_agent_context(ctx)
    try:
        yield
    finally:
        set_agent_context(previous)


def _permission_context(mode: str = "default"):
    from agentscope.permission import PermissionContext, PermissionMode

    return PermissionContext(mode=PermissionMode[mode.upper()])


def _child_ctx(*, user_id: int = 7, agent_name: str = "资产导出", parent: str = "conv_perm_flow"):
    return AgentContext(
        agent_id="child",
        agent_name=agent_name,
        parent_conversation_id=parent,
        delegation_depth=1,
        conversation_id="child_session_x",
        user_id=user_id,
    )


async def _check(store_tool_input, ctx, *, mode: str = "default"):
    from unittest.mock import AsyncMock, patch

    from app.services.ai.runtime.agentscope.tools import runtime_tool_from_native

    tool = runtime_tool_from_native(
        _fake_bash_native(),
        approval_mode="ask",
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
    )
    with patch(
        "app.services.ai.runtime.agentscope.tools._enforce_tool_forbidden",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.services.ai.runtime.agentscope.tools._enforce_command_blacklist",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.services.ai.runtime.agentscope.tools._session_grant_decision",
        new=AsyncMock(return_value=None),
    ), _platform_context(ctx):
        return await tool.check_permissions(store_tool_input, _permission_context(mode))


@pytest.mark.asyncio
async def test_subagent_tool_consumes_once_grant_from_parent_confirmation():
    """子代理续聊时，父会话授权的「允许这一次」必须让同一个调用直接放行。"""
    from agentscope.permission import PermissionBehavior

    from app.services.ai.runtime.agentscope.session_permission_grants import (
        delegation_grant_scope,
        once_permission_grants,
        tool_args_fingerprint,
    )

    tool_input = {"command": "python export.py"}
    await once_permission_grants.grant(
        7,
        delegation_grant_scope("conv_perm_flow", "资产导出"),
        "Bash",
        tool_args_fingerprint(tool_input),
    )

    ctx = _child_ctx()
    first = await _check(tool_input, ctx)
    second = await _check(tool_input, ctx)

    assert first.behavior == PermissionBehavior.ALLOW
    assert first.decision_reason == "delegation_once_grant"
    # 一次性授权不能被连续两次调用复用。
    assert second.behavior == PermissionBehavior.ASK


@pytest.mark.asyncio
async def test_once_grant_does_not_bypass_safety_ask_or_unattended_mode():
    """一次性授权不许绕过原生 safety ASK 与无人值守：门禁与「本会话允许」同一道。"""
    from agentscope.permission import PermissionBehavior

    from app.services.ai.runtime.agentscope.session_permission_grants import (
        delegation_grant_scope,
        once_permission_grants,
        tool_args_fingerprint,
    )

    # 用普通命令：删除类命令会更早被 `_shell_deletion_permission_decision` 直接 DENY，
    # 测不到「原生 safety ASK 是否被令牌绕过」这一层。
    tool_input = {"command": "python export.py"}
    scope = delegation_grant_scope("conv_perm_flow", "资产导出")
    fingerprint = tool_args_fingerprint(tool_input)
    ctx = _child_ctx()

    # 1) 无人值守（DONT_ASK）：令牌在，也不放行，且不能被消费掉
    await once_permission_grants.grant(7, scope, "Bash", fingerprint)
    unattended = await _check(tool_input, ctx, mode="dont_ask")
    assert unattended.behavior == PermissionBehavior.ASK
    still_there = await once_permission_grants.consume(7, scope, "Bash", fingerprint)
    assert still_there is True, "DONT_ASK 下不该消费掉用户的授权令牌"

    # 2) bypass_immune 的 safety ASK：同样不放行
    await once_permission_grants.grant(7, scope, "Bash", fingerprint)
    from unittest.mock import AsyncMock, patch

    from app.services.ai.runtime.agentscope.tools import runtime_tool_from_native

    tool = runtime_tool_from_native(
        _fake_bash_native(bypass_immune=True),
        approval_mode="ask",
        user_id=7,
        conversation_id="child_session_x",
    )
    with patch(
        "app.services.ai.runtime.agentscope.tools._enforce_tool_forbidden",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.services.ai.runtime.agentscope.tools._enforce_command_blacklist",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.services.ai.runtime.agentscope.tools._session_grant_decision",
        new=AsyncMock(return_value=None),
    ), _platform_context(ctx):
        safety = await tool.check_permissions(tool_input, _permission_context())
    assert safety.behavior == PermissionBehavior.ASK
    assert safety.bypass_immune is True


@pytest.mark.asyncio
async def test_once_grant_belongs_to_the_agent_that_was_granted():
    """授权给「资产导出」的令牌，不能放行另一个子代理的同一调用。"""
    from unittest.mock import AsyncMock, patch

    from agentscope.permission import PermissionBehavior

    from app.services.ai.runtime.agentscope.session_permission_grants import (
        delegation_grant_scope,
        once_permission_grants,
        tool_args_fingerprint,
    )
    from app.services.ai.runtime.agentscope.tools import runtime_tool_from_native

    tool_input = {"command": "python export.py"}
    await once_permission_grants.grant(
        8,
        delegation_grant_scope("conv_perm_flow", "资产导出"),
        "Bash",
        tool_args_fingerprint(tool_input),
    )

    other_ctx = AgentContext(
        agent_id="child2",
        agent_name="知识库助手",
        parent_conversation_id="conv_perm_flow",
        delegation_depth=1,
        conversation_id="child_session_y",
        user_id=8,
    )
    tool = runtime_tool_from_native(_fake_bash_native(), approval_mode="ask", user_id=8, conversation_id="child_session_y")
    with patch(
        "app.services.ai.runtime.agentscope.tools._enforce_tool_forbidden",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.services.ai.runtime.agentscope.tools._enforce_command_blacklist",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.services.ai.runtime.agentscope.tools._session_grant_decision",
        new=AsyncMock(return_value=None),
    ):
        decision = await tool.check_permissions(tool_input, other_ctx)

    assert decision.behavior == PermissionBehavior.ASK


def _subagent_pending(*, command: str = "python export.py", conversation_id: str = "child_session_z"):
    from types import SimpleNamespace

    from app.services.ai.runtime.agentscope.pending_store import PendingAgentScopeSnapshot

    snapshot = PendingAgentScopeSnapshot(
        request_id="perm_handoff",
        kind="permission",
        user_id="9",
        conversation_id=conversation_id,
        trace_id="sub_trace",
        reply_id="reply_h",
        agent_name="资产导出",
        tool_call={
            "id": "call_h",
            "name": "Bash",
            "input": json.dumps({"command": command}, ensure_ascii=False),
        },
        agent_state={},
        stream_state={},
        runner_context={},
        delegation={
            "parent_conversation_id": "conv_handoff",
            "run_id": "subrun_h",
            "agent_name": "资产导出",
            "depth": 1,
        },
    )
    return SimpleNamespace(
        snapshot=snapshot,
        tool_call=SimpleNamespace(id="call_h", name="Bash"),
        trace_id="sub_trace",
    )


async def _collect_handoff(pending, *, confirmed, grant_scope):
    from app.services.ai.runtime.agentscope.resume import AgentScopeResumeHandler

    chunks = []
    async for chunk in AgentScopeResumeHandler._handoff_subagent_permission(
        pending,
        request_id="perm_handoff",
        delegation=dict(pending.snapshot.delegation),
        confirmed=confirmed,
        grant_scope=grant_scope,
        user_id=9,
    ):
        chunks.append(chunk)
    return chunks


import json  # noqa: E402  (测试辅助用到)


@pytest.mark.asyncio
async def test_handoff_approve_once_writes_token_and_tells_parent_to_resume():
    """「允许这一次」→ 写一次性令牌，并把 resume 续聊指令交回父会话。"""
    from unittest.mock import AsyncMock, patch

    from app.services.ai.runtime.agentscope.session_permission_grants import (
        delegation_grant_scope,
        once_permission_grants,
        tool_args_fingerprint,
    )

    pending = _subagent_pending()
    with patch(
        "app.services.ai.runtime.agentscope.confirmations.pending_agentscope_confirmations.pop_async",
        new=AsyncMock(return_value=None),
    ) as pop_mock:
        chunks = await _collect_handoff(pending, confirmed=True, grant_scope="once")

    pop_mock.assert_awaited_once()  # 快照只能被消费一次

    handoff = next(c for c in chunks if c["type"] == "subagent_permission_handoff")
    assert handoff["status"] == "approved"
    assert handoff["parent_conversation_id"] == "conv_handoff"
    assert handoff["subagent"]["display_name"] == "资产导出"
    assert "用户授权" in handoff["directive"]
    assert 'sub_agent_call(agent_name="资产导出", resume=True)' in handoff["directive"]
    assert any(c["type"] == "permission_result" and c["status"] == "success" for c in chunks)
    assert any(c["type"] == "run_status" and c["status"] == "success" for c in chunks)

    # 令牌按「父会话 + 子智能体」作用域 + 参数指纹写入，子智能体续聊时能消费到。
    scope = delegation_grant_scope("conv_handoff", "资产导出")
    assert handoff["grant"] == {"scope": "once", "granted": True}
    assert await await_once_token_consumed(scope, {"command": "python export.py"}) is True
    assert await await_once_token_consumed(scope, {"command": "python export.py"}) is False


async def await_once_token_consumed(scope, tool_input):
    from app.services.ai.runtime.agentscope.session_permission_grants import (
        once_permission_grants,
        tool_args_fingerprint,
    )

    return await once_permission_grants.consume(
        9, scope, "Bash", tool_args_fingerprint(tool_input)
    )


@pytest.mark.asyncio
async def test_handoff_reject_writes_no_grant_and_asks_agent_to_switch_approach():
    from unittest.mock import AsyncMock, patch

    from app.services.ai.runtime.agentscope.session_permission_grants import (
        delegation_grant_scope,
    )

    pending = _subagent_pending(command="rm -rf /tmp/none")
    with patch(
        "app.services.ai.runtime.agentscope.confirmations.pending_agentscope_confirmations.pop_async",
        new=AsyncMock(return_value=None),
    ):
        chunks = await _collect_handoff(pending, confirmed=False, grant_scope="once")

    handoff = next(c for c in chunks if c["type"] == "subagent_permission_handoff")
    assert handoff["status"] == "rejected"
    assert "用户拒绝" in handoff["directive"]
    assert "改用不需要该工具权限的方式" in handoff["directive"]
    assert any(c["type"] == "permission_result" and c["status"] == "rejected" for c in chunks)

    # 拒绝不落任何授权：子智能体再试同一命令仍会被拦。
    scope = delegation_grant_scope("conv_handoff", "资产导出")
    assert await await_once_token_consumed(scope, {"command": "rm -rf /tmp/none"}) is False


@pytest.mark.asyncio
async def test_handoff_session_scope_grants_only_that_subagent():
    """「总是允许」落到「父会话 + 该子智能体」，不波及别的子代理与主会话。"""
    from unittest.mock import AsyncMock, patch

    from app.services.ai.runtime.agentscope.session_permission_grants import (
        delegation_grant_scope,
        session_permission_grants,
    )

    pending = _subagent_pending()
    with patch(
        "app.services.ai.runtime.agentscope.confirmations.pending_agentscope_confirmations.pop_async",
        new=AsyncMock(return_value=None),
    ):
        chunks = await _collect_handoff(pending, confirmed=True, grant_scope="session")

    handoff = next(c for c in chunks if c["type"] == "subagent_permission_handoff")
    assert handoff["grant"] == {"scope": "session", "granted": True}

    own_scope = delegation_grant_scope("conv_handoff", "资产导出")
    other_scope = delegation_grant_scope("conv_handoff", "知识库助手")
    assert await session_permission_grants.is_granted(9, own_scope, "Bash") is True
    assert await session_permission_grants.is_granted(9, other_scope, "Bash") is False
    assert await session_permission_grants.is_granted(9, "conv_handoff", "Bash") is False


@pytest.mark.asyncio
async def test_permission_pause_records_sub_session_history_for_resume():
    """停在授权点必须留下子会话缓存。

    否则父专家按续聊指令 resume 时读到空缓存，会被降级成「全新会话」：子智能体丢掉
    原始任务，也复现不出同一份工具参数，一次性授权按参数指纹匹配就永远命不中。
    """
    from unittest.mock import AsyncMock, patch

    from app.core.context import get_current_agent_context, set_agent_context
    from app.services.ai.delegation_session_store import DelegationSessionStore
    from app.services.ai.tools.agent_delegate_tool import sub_agent_call
    import sys
    from pathlib import Path as _Path

    _tools_dir = str(_Path(__file__).resolve().parent / "tools")
    if _tools_dir not in sys.path:
        sys.path.insert(0, _tools_dir)
    from test_delegation_session_resume import _delegation_env

    class _PermissionExecutor:
        async def execute(self, history):
            yield {
                "type": "permission_required",
                "status": "pending",
                "permission_request_id": "perm_test",
                "title": "需要确认工具调用: Bash",
                "tool_call": {"id": "c1", "name": "Bash", "args": {"command": "ls"}},
            }

    main_ctx = AgentContext(
        agent_id="main",
        agent_name="MainAgent",
        delegation_depth=0,
        trace_id="trace-perm",
        trace_buffer=[],
        user_id=7,
        conversation_id="conv-perm",
    )
    main_ctx.event_queue = asyncio.Queue()
    previous = get_current_agent_context()
    set_agent_context(main_ctx)
    try:
        with patch(
            "app.services.ai.delegation_session_store.DelegationSessionStore.from_runtime",
            AsyncMock(return_value=DelegationSessionStore(None)),
        ):
            with _delegation_env(_PermissionExecutor()):
                await sub_agent_call.func(agent_name="chat-bi", query="导出资产明细")
    finally:
        set_agent_context(previous)

    cached = main_ctx.delegation_session_histories.get("chat_bi") or []
    assert cached, "停在授权点没有写续聊缓存，resume 会降级成全新会话"
    assert cached[0]["role"] == "user"
    assert "导出资产明细" in cached[0]["content"]
    assert any("等待用户授权" in str(item.get("content") or "") for item in cached)
