"""委派候选口径的回归测试。

``load_delegable_system_agent_candidates`` 是委派链（目录 / 欢迎语清单 / 子代理通讯录 /
子代理候选）的唯一候选来源。历史缺陷：各调用方分别复用 ``AgentManagerService.list_agents``，
那是 UI 可见性口径（按 created_by 归属过滤），而工具/后台调用拿不到 user，
会把全部系统级智能体（created_by='admin'）剃成空列表。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.agent import AIAgent

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_load_delegable_candidates_returns_queried_agents():
    from app.services.ai.tools.agent_delegate_tool import load_delegable_system_agent_candidates

    agents = [SimpleNamespace(id="sys1"), SimpleNamespace(id="sys2")]
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = agents
    session.execute = AsyncMock(return_value=result)

    out = await load_delegable_system_agent_candidates(session)

    assert out == agents
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_roster_resolves_system_candidates_without_user_context():
    """欢迎语清单 / 子代理通讯录必须拿到系统级候选，不能被 created_by 过滤剃光。"""
    from app.services.ai.agent_roster import resolve_delegable_system_agents_for_user

    agents = [
        AIAgent(
            id="sys1",
            name="chat-bi",
            display_name="数据智能助手",
            is_system=True,
            created_by="admin",
            is_enabled=True,
            engine_type="LOCAL",
        ),
        AIAgent(
            id="sys2",
            name="knowledge-base",
            display_name="知识库助手",
            is_system=True,
            created_by="admin",
            is_enabled=True,
            engine_type="LOCAL",
        ),
    ]

    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = agents
    result.fetchall.return_value = []
    result.all.return_value = []
    session.execute = AsyncMock(return_value=result)

    with patch(
        "app.services.ai.tools.agent_delegate_tool.resolve_runnable_delegable_system_agents",
        AsyncMock(return_value=[]),
    ) as resolve_mock:
        await resolve_delegable_system_agents_for_user(
            session,
            user_info={"user_id": 7, "role": "user"},
            current_agent_id="current-agent",
        )

    passed_agents = resolve_mock.await_args.args[1]
    assert [a.name for a in passed_agents] == ["chat-bi", "knowledge-base"]
