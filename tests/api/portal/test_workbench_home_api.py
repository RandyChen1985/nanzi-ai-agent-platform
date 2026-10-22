from unittest.mock import AsyncMock

import pytest


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_workbench_home_passes_current_user_and_roles(monkeypatch):
    from app.api.portal.endpoints import workbench

    payload = {"mode": "new_user", "attention": []}
    build = AsyncMock(return_value=payload)
    monkeypatch.setattr(workbench, "get_user_role_ids", AsyncMock(return_value=[2, 3]))
    monkeypatch.setattr(workbench.WorkbenchHomeService, "build", build)

    response = await workbench.get_workbench_home(
        user_info={"user_id": "7", "user_name": "alice", "role": "user"},
        db=AsyncMock(),
    )

    assert response.data == payload
    build.assert_awaited_once()
    assert build.await_args.kwargs["user_id"] == 7
    assert build.await_args.kwargs["role_ids"] == [2, 3]
    assert build.await_args.kwargs["user"]["user_name"] == "alice"


@pytest.mark.asyncio
async def test_workbench_agents_expose_own_avatar(monkeypatch):
    """卡片头像要能用智能体自己的 image；没配置时前端再回退首字。"""
    from app.services import workbench_home_service as service
    from app.services.ai import agent_manager

    class FakeAgent:
        id = 11
        name = "main"
        display_name = "主助手"
        description = "兜底助手"
        avatar_url = "/branding/agents/11.png"
        execution_count = 3
        sort_order = 0

    monkeypatch.setattr(
        agent_manager.AgentManagerService,
        "list_allowed_agents",
        AsyncMock(return_value=[FakeAgent()]),
    )

    agents = await service._load_agents(AsyncMock(), user={"user_id": 7})

    assert agents[0]["avatar_url"] == "/branding/agents/11.png"
    assert agents[0]["name"] == "主助手"
