"""主助手（main）不可作为委派目标的回归。

现场（2026-11-07）：用户在「调度白名单」弹窗里可以直接勾选「主助手(Main)」。
核实下来是**规则缺失**而不是渲染问题——整条链路都没有排除 main：

- 前端候选只排「自己」和「已禁用」，弹窗 `isSelectable` 只看 `isAuthorized`；
- 后端 `load_delegable_system_agent_candidates` 只查 `is_enabled + is_system`；
- `filter_delegable_agents` 只排当前智能体自己；
- 运行时 `sub_agent_call` 只拦自委派。

为什么必须排除：main 是 `AssistantAgentRunner` 的兜底入口，被委派进来时执行深度
已是 1，自身又被 `has_delegation_tool` 隐式挂了委派工具，再委派只会撞
`depth_exceeded`；语义上也等于把问题从专家丢回「无法分类」的万金油。

口径：固定 ID ``sys-agent-chat`` + 兼容 slug ``main`` / ``assistant`` / ``general-chat``
（与 `AgentManagerService._is_main_general_agent_record`、`is_main_general_agent` 同一份）。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.schemas.agent import ToolConfigItem

pytestmark = pytest.mark.no_infrastructure

MAIN_ID = "sys-agent-chat"


def _agent(agent_id: str, name: str | None = None, *, is_system: bool = True, created_by: str = "admin"):
    return SimpleNamespace(
        id=agent_id,
        name=name or agent_id,
        display_name=name or agent_id,
        is_system=is_system,
        is_enabled=True,
        created_by=created_by,
        engine_type="LOCAL",
        capabilities=[],
        engine_config={},
        sort_order=0,
    )


# --------------------------------------------------------------------------
# 身份判定
# --------------------------------------------------------------------------


def test_main_identity_matches_fixed_id_and_slugs():
    from app.services.ai.tools.agent_delegate_tool import is_main_general_agent_target

    assert is_main_general_agent_target(_agent(MAIN_ID, "main")) is True
    # 固定 ID 认就够了：显示名被改过也挡得住
    assert is_main_general_agent_target(_agent(MAIN_ID, "改过名的主助手")) is True
    # 兼容 slug：历史数据/克隆可能用的是这些名字
    for slug in ("main", "MAIN", "assistant", "general-chat"):
        assert is_main_general_agent_target(_agent("other-id", slug)) is True
    # 普通专家不能被误伤
    assert is_main_general_agent_target(_agent("sys-agent-chatbi", "chat-bi")) is False
    assert is_main_general_agent_target(None) is False


def test_requested_name_matches_main_aliases():
    from app.services.ai.tools.agent_delegate_tool import requested_agent_is_main

    for alias in ("main", "MAIN", "assistant", "general-chat", "general_chat", MAIN_ID):
        assert requested_agent_is_main(alias) is True
    assert requested_agent_is_main("chat-bi") is False
    assert requested_agent_is_main("") is False
    assert requested_agent_is_main(None) is False


# --------------------------------------------------------------------------
# 权限层：不同来源的候选都收敛到同一结果
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filter_drops_main_from_system_candidates():
    from app.services.ai.tools.agent_delegate_tool import filter_delegable_agents

    main = _agent(MAIN_ID, "main")
    expert = _agent("sys-agent-chatbi", "chat-bi")

    permitted = await filter_delegable_agents(
        AsyncMock(),
        [main, expert],
        user_id=None,
        is_admin=False,
        user_name="alice",
        current_agent_id="caller",
        allow_personal=False,
    )

    assert [a.id for a in permitted] == ["sys-agent-chatbi"]


@pytest.mark.asyncio
async def test_filter_drops_main_even_when_explicitly_allowlisted():
    """存量白名单里写了 main：运行时也必须拒绝，不能「配了就生效」。"""
    from app.services.ai.tools.agent_delegate_tool import filter_delegable_agents

    main = _agent(MAIN_ID, "main")
    mine = _agent("mine-1", "mine-1", is_system=False, created_by="alice")

    permitted = await filter_delegable_agents(
        AsyncMock(),
        [main, mine],
        user_id=None,
        is_admin=True,
        user_name="alice",
        current_agent_id="caller",
        allow_personal=True,
    )

    assert [a.id for a in permitted] == ["mine-1"]


@pytest.mark.asyncio
async def test_stages_exclude_main_for_allowlisted_candidates():
    """白名单点名 main + 专家：候选层仍能看到二者，但可委派集合里没有 main。"""
    from app.services.ai.tools import agent_delegate_tool as module

    main = _agent(MAIN_ID, "main")
    expert = _agent("sys-agent-chatbi", "chat-bi")

    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [main, expert]
    session.execute = AsyncMock(return_value=result)

    candidates, permitted, _ = await module.resolve_delegable_agent_stages(
        session,
        user_id=None,
        is_admin=True,
        user_name="admin",
        current_agent_id="caller",
        allowlist_configured=True,
        allowlist=[MAIN_ID, "sys-agent-chatbi"],
    )

    # 候选层保留（调用入口还要靠它把「main 不可委派」和「不在名单」区分成不同错误码）
    assert {a.id for a in candidates} == {MAIN_ID, "sys-agent-chatbi"}
    assert [a.id for a in permitted] == ["sys-agent-chatbi"]


# --------------------------------------------------------------------------
# 展示层：团队卡片不能把 main 算成成员
# --------------------------------------------------------------------------


def test_team_members_exclude_main_id():
    from app.services.ai.agent_manager import AgentManagerService

    version = SimpleNamespace(
        tools=[
            ToolConfigItem(
                name="sub_agent_call",
                delegable_agent_ids=[MAIN_ID, "sys-agent-chatbi"],
            )
        ]
    )

    assert AgentManagerService._resolve_team_members(version) == ["sys-agent-chatbi"]
