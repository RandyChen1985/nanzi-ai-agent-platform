"""调度白名单（delegable_agent_ids）的语义回归。

覆盖两套不对称规则：

- 系统级：未配置 = 全部有 ``agent:<id>`` 授权的；配置后 = 名单 ∩ 授权。
- 用户自建：未配置 = 一个都不可委派（历史行为）；配置后 = 名单 ∩ 本人创建。

名单只有一份：``sub_agent_call`` 是唯一事实源，``sub_agent_batch_call`` 跟随它。
batch 上历史遗留的字段仅作兜底读取，不再允许两个工具各配一份——否则「只配一个」
时另一个按未配置走全量，自建子专家会整批消失。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.schemas.agent import ToolConfigItem

pytestmark = pytest.mark.no_infrastructure


def _agent(agent_id: str, *, is_system: bool = True, created_by: str = "admin", enabled: bool = True):
    return SimpleNamespace(
        id=agent_id,
        name=agent_id,
        display_name=agent_id,
        is_system=is_system,
        is_enabled=enabled,
        created_by=created_by,
        engine_type="LOCAL",
        capabilities=[],
        engine_config={},
        sort_order=0,
    )


# --------------------------------------------------------------------------
# 白名单解析
# --------------------------------------------------------------------------


def test_allowlist_absent_means_unconfigured():
    from app.services.ai.tools.agent_delegate_tool import resolve_delegation_allowlist

    # 字符串老格式：连配置项都没有
    assert resolve_delegation_allowlist(["execute_sql_query"]) == (False, None)
    # ToolConfigItem 存在但没设该字段
    item = ToolConfigItem(name="sub_agent_call")
    assert resolve_delegation_allowlist([item]) == (False, None)
    # 完全没有工具配置
    assert resolve_delegation_allowlist(None) == (False, None)


def test_allowlist_empty_list_is_explicit_deny():
    from app.services.ai.tools.agent_delegate_tool import resolve_delegation_allowlist

    assert resolve_delegation_allowlist(
        [ToolConfigItem(name="sub_agent_call", delegable_agent_ids=[])]
    ) == (True, [])


def test_allowlist_accepts_dict_and_object_forms():
    from app.services.ai.tools.agent_delegate_tool import resolve_delegation_allowlist

    as_dict = {"name": "sub_agent_call", "delegable_agent_ids": ["a1", "a2"]}
    assert resolve_delegation_allowlist([as_dict]) == (True, ["a1", "a2"])

    as_obj = ToolConfigItem(name="sub_agent_call", delegable_agent_ids=["a1"])
    assert resolve_delegation_allowlist([as_obj]) == (True, ["a1"])


def test_allowlist_drops_blank_and_duplicated_ids():
    from app.services.ai.tools.agent_delegate_tool import resolve_delegation_allowlist

    item = ToolConfigItem(
        name="sub_agent_call",
        delegable_agent_ids=[" a1 ", "", "a1", None, "a2"],
    )
    assert resolve_delegation_allowlist([item]) == (True, ["a1", "a2"])


def test_batch_follows_single_call_allowlist():
    from app.services.ai.tools.agent_delegate_tool import resolve_delegation_allowlist

    tools = [
        ToolConfigItem(name="sub_agent_call", delegable_agent_ids=["a1"]),
        ToolConfigItem(name="sub_agent_batch_call"),
    ]
    assert resolve_delegation_allowlist(tools, tool_name="sub_agent_batch_call") == (True, ["a1"])


def test_single_call_is_the_only_source_of_truth():
    """两个都配时以 sub_agent_call 为准：batch 自己的那份不再生效。

    曾经「各配一份」的结果是：只填 batch 的人，batch 收窄、single 却走全量；
    两条委派路径对同一份配置给出相反语义。统一到 single 后，batch 上的配置只是
    存量的兜底来源，不参与覆盖。
    """
    from app.services.ai.tools.agent_delegate_tool import resolve_delegation_allowlist

    tools = [
        ToolConfigItem(name="sub_agent_call", delegable_agent_ids=["a1", "a2"]),
        ToolConfigItem(name="sub_agent_batch_call", delegable_agent_ids=["a3"]),
    ]
    assert resolve_delegation_allowlist(tools) == (True, ["a1", "a2"])
    assert resolve_delegation_allowlist(tools, tool_name="sub_agent_batch_call") == (True, ["a1", "a2"])


def test_batch_empty_list_does_not_override_single_call():
    """batch 上的空数组不再单独表示「禁止批量委派」：名单只有一份。"""
    from app.services.ai.tools.agent_delegate_tool import resolve_delegation_allowlist

    tools = [
        ToolConfigItem(name="sub_agent_call", delegable_agent_ids=["a1"]),
        ToolConfigItem(name="sub_agent_batch_call", delegable_agent_ids=[]),
    ]
    assert resolve_delegation_allowlist(tools, tool_name="sub_agent_batch_call") == (True, ["a1"])


def test_single_call_reads_batch_legacy_allowlist():
    """存量：历史上只配在 sub_agent_batch_call 上时，串行委派也必须认这份名单。

    这正是用户反馈的现场：团成员只配在批量委派上，单次委派按「未配置」走系统级
    全量，自建子专家被静默剔除。兜底读取让存量配置无需重配即可生效。
    """
    from app.services.ai.tools.agent_delegate_tool import resolve_delegation_allowlist

    tools = [ToolConfigItem(name="sub_agent_batch_call", delegable_agent_ids=["a1"])]
    assert resolve_delegation_allowlist(tools) == (True, ["a1"])
    assert resolve_delegation_allowlist(tools, tool_name="sub_agent_batch_call") == (True, ["a1"])


# --------------------------------------------------------------------------
# 工具开关：一个入口，两个工具
# --------------------------------------------------------------------------


def test_pair_keeps_both_tools_when_single_is_configured():
    """页面只勾 sub_agent_call，落库必须同时有 batch——否则模型没有并行入口。"""
    from app.services.ai.tools.agent_delegate_tool import ensure_delegation_tool_pair

    tools = [{"name": "sub_agent_call", "delegable_agent_ids": ["a1"]}]
    paired = ensure_delegation_tool_pair(tools, as_dict=True)
    assert {item["name"] for item in paired if isinstance(item, dict)} == {
        "sub_agent_call",
        "sub_agent_batch_call",
    }
    # 名单仍只挂 single，batch 上不复制一份
    assert next(i for i in paired if i["name"] == "sub_agent_call")["delegable_agent_ids"] == ["a1"]
    assert "delegable_agent_ids" not in next(i for i in paired if i["name"] == "sub_agent_batch_call")


def test_pair_upgrades_batch_only_legacy_version():
    """存量只勾了 batch：补上 single，串行委派才不会消失。"""
    from app.services.ai.tools.agent_delegate_tool import ensure_delegation_tool_pair

    paired = ensure_delegation_tool_pair(["sub_agent_batch_call"])
    assert paired == ["sub_agent_batch_call", "sub_agent_call"]


def test_pair_drops_allowlist_from_batch_tool():
    """两个工具都带名单的存量配置：batch 那份丢弃，只认 single。"""
    from app.services.ai.tools.agent_delegate_tool import ensure_delegation_tool_pair

    paired = ensure_delegation_tool_pair(
        [
            {"name": "sub_agent_call", "delegable_agent_ids": ["a1"]},
            {"name": "sub_agent_batch_call", "delegable_agent_ids": ["a2"]},
        ],
        as_dict=True,
    )
    batch = next(i for i in paired if i["name"] == "sub_agent_batch_call")
    assert "delegable_agent_ids" not in batch


def test_pair_leaves_non_delegation_tools_untouched():
    """没勾委派工具的智能体不能被凭空塞进两个工具。"""
    from app.services.ai.tools.agent_delegate_tool import ensure_delegation_tool_pair

    tools = [{"name": "search_knowledge_base"}]
    assert ensure_delegation_tool_pair(tools, as_dict=True) == tools
    assert ensure_delegation_tool_pair([]) == []
    assert ensure_delegation_tool_pair(None) == []


# --------------------------------------------------------------------------
# 权限层过滤
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_personal_agent_blocked_when_allowlist_not_configured():
    """未配置白名单时，自建智能体一律不可委派——这是历史行为，不能被"放开"顺手改掉。"""
    from app.services.ai.tools.agent_delegate_tool import filter_delegable_agents

    mine = _agent("mine-1", is_system=False, created_by="alice")
    system = _agent("sys-1")

    permitted = await filter_delegable_agents(
        AsyncMock(),
        [mine, system],
        user_id=None,
        is_admin=False,
        user_name="alice",
        current_agent_id="caller",
        allow_personal=False,
    )

    assert [a.id for a in permitted] == ["sys-1"]


@pytest.mark.asyncio
async def test_personal_agent_allowed_when_allowlist_configured_and_owner():
    from app.services.ai.tools.agent_delegate_tool import filter_delegable_agents

    mine = _agent("mine-1", is_system=False, created_by="alice")
    others = _agent("other-1", is_system=False, created_by="bob")

    permitted = await filter_delegable_agents(
        AsyncMock(),
        [mine, others],
        user_id=None,
        is_admin=False,
        user_name="alice",
        current_agent_id="caller",
        allow_personal=True,
    )

    assert [a.id for a in permitted] == ["mine-1"]


@pytest.mark.asyncio
async def test_personal_agent_disabled_is_still_excluded():
    from app.services.ai.tools.agent_delegate_tool import filter_delegable_agents

    disabled = _agent("mine-1", is_system=False, created_by="alice", enabled=False)

    permitted = await filter_delegable_agents(
        AsyncMock(),
        [disabled],
        user_id=None,
        is_admin=False,
        user_name="alice",
        current_agent_id="caller",
        allow_personal=True,
    )

    assert permitted == []


@pytest.mark.asyncio
async def test_caller_itself_is_never_delegable():
    from app.services.ai.tools.agent_delegate_tool import filter_delegable_agents

    permitted = await filter_delegable_agents(
        AsyncMock(),
        [_agent("caller")],
        user_id=None,
        is_admin=False,
        user_name="alice",
        current_agent_id="caller",
        allow_personal=True,
    )

    assert permitted == []


@pytest.mark.asyncio
async def test_admin_may_delegate_others_personal_agent():
    from app.services.ai.tools.agent_delegate_tool import filter_delegable_agents

    others = _agent("other-1", is_system=False, created_by="bob")

    permitted = await filter_delegable_agents(
        AsyncMock(),
        [others],
        user_id=None,
        is_admin=True,
        user_name="admin",
        current_agent_id="caller",
        allow_personal=True,
    )

    assert [a.id for a in permitted] == ["other-1"]


# --------------------------------------------------------------------------
# 统一出口
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_empty_allowlist_short_circuits_without_querying():
    from app.services.ai.tools.agent_delegate_tool import resolve_delegable_agent_stages

    session = AsyncMock()
    candidates, permitted, ready = await resolve_delegable_agent_stages(
        session,
        user_id=7,
        is_admin=False,
        user_name="alice",
        current_agent_id="caller",
        allowlist_configured=True,
        allowlist=[],
    )

    assert (candidates, permitted, ready) == ([], [], [])
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_allowlist_limits_candidates_to_named_ids():
    from app.services.ai.tools import agent_delegate_tool

    session = AsyncMock()
    named = [_agent("sys-1"), _agent("sys-2")]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            agent_delegate_tool,
            "load_agents_by_delegation_ids",
            AsyncMock(return_value=named),
        )
        mp.setattr(
            agent_delegate_tool,
            "_filter_ready_delegable_agents",
            AsyncMock(side_effect=lambda _s, agents: list(agents)),
        )
        candidates, permitted, ready = await agent_delegate_tool.resolve_delegable_agent_stages(
            session,
            user_id=None,
            is_admin=False,
            user_name="alice",
            current_agent_id="caller",
            allowlist_configured=True,
            allowlist=["sys-1", "sys-2"],
        )

    assert [a.id for a in candidates] == ["sys-1", "sys-2"]
    assert [a.id for a in ready] == ["sys-1", "sys-2"]


# --------------------------------------------------------------------------
# 批量委派读取自己的白名单
# --------------------------------------------------------------------------


class _FakeSession:
    async def __aenter__(self):
        return AsyncMock()

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_batch_call_uses_shared_allowlist(monkeypatch):
    """``sub_agent_batch_call`` 内部复用 ``sub_agent_call``，名单也必须是同一份。

    早先按「当前是谁在调」各取一份，结果 batch 上的配置能单独收窄、single 反而走
    全量；现在名单只有一个事实源，批量委派必须读到 sub_agent_call 那份。
    """
    from app.core.context import AgentContext, set_agent_context
    from app.services.ai.tools import agent_delegate_tool as mod

    caller_config = SimpleNamespace(
        tools=[
            ToolConfigItem(name="sub_agent_call", delegable_agent_ids=["serial-only"]),
            ToolConfigItem(name="sub_agent_batch_call", delegable_agent_ids=["batch-only"]),
        ]
    )

    main_ctx = AgentContext(
        agent_id="main-agent",
        agent_name="主助手",
        conversation_id="conv-1",
        delegation_depth=0,
        user_id=1,
    )
    main_ctx.trace_buffer = []
    set_agent_context(main_ctx)

    captured: list[tuple[bool, list[str] | None]] = []

    async def _fake_stages(session, **kwargs):
        captured.append((kwargs.get("allowlist_configured"), kwargs.get("allowlist")))
        return [], [], []

    monkeypatch.setattr(mod, "AsyncSessionLocal", _FakeSession)
    monkeypatch.setattr(
        mod.AgentManagerService,
        "get_active_agent_config",
        AsyncMock(return_value=caller_config),
    )
    monkeypatch.setattr(mod, "resolve_delegable_agent_stages", _fake_stages)

    tool = mod.BatchSubAgentTool()
    await tool.ainvoke({"calls": [{"agent_name": "data-agent", "query": "查一下订单量"}]})

    assert captured == [(True, ["serial-only"])]


@pytest.mark.asyncio
async def test_single_call_uses_single_allowlist(monkeypatch):
    from app.core.context import AgentContext, set_agent_context
    from app.services.ai.tools import agent_delegate_tool as mod

    caller_config = SimpleNamespace(
        tools=[
            ToolConfigItem(name="sub_agent_call", delegable_agent_ids=["serial-only"]),
            ToolConfigItem(name="sub_agent_batch_call", delegable_agent_ids=["batch-only"]),
        ]
    )

    main_ctx = AgentContext(
        agent_id="main-agent",
        agent_name="主助手",
        conversation_id="conv-1",
        delegation_depth=0,
        user_id=1,
    )
    main_ctx.trace_buffer = []
    set_agent_context(main_ctx)

    captured: list[tuple[bool, list[str] | None]] = []

    async def _fake_stages(session, **kwargs):
        captured.append((kwargs.get("allowlist_configured"), kwargs.get("allowlist")))
        return [], [], []

    monkeypatch.setattr(mod, "AsyncSessionLocal", _FakeSession)
    monkeypatch.setattr(
        mod.AgentManagerService,
        "get_active_agent_config",
        AsyncMock(return_value=caller_config),
    )
    monkeypatch.setattr(mod, "resolve_delegable_agent_stages", _fake_stages)

    await mod.sub_agent_call.func(agent_name="data-agent", query="查一下订单量")

    assert captured == [(True, ["serial-only"])]
