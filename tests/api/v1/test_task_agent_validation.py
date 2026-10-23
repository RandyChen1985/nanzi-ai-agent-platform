"""定时任务绑定智能体的入参校验。

任务按所有者身份执行，若创建/改绑时不校验，普通用户可以绕开前端下拉直接调 API
绑定别人的私有智能体或已停用智能体，把定时任务变成越权执行入口。
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.tasks import _ensure_agent_selectable


class _Agent:
    def __init__(self, agent_id: str, is_enabled: bool = True) -> None:
        self.id = agent_id
        self.is_enabled = is_enabled


def _db(agent):
    db = AsyncMock()
    db.get = AsyncMock(return_value=agent)
    return db


@pytest.mark.asyncio
async def test_unknown_agent_rejected():
    with pytest.raises(HTTPException) as exc:
        await _ensure_agent_selectable(_db(None), {"user_id": 1, "role": "user"}, "a1")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_disabled_agent_rejected():
    with pytest.raises(HTTPException) as exc:
        await _ensure_agent_selectable(
            _db(_Agent("a1", is_enabled=False)), {"user_id": 1, "role": "user"}, "a1"
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_blank_agent_id_rejected():
    with pytest.raises(HTTPException) as exc:
        await _ensure_agent_selectable(_db(_Agent("a1")), {"user_id": 1, "role": "user"}, "   ")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_admin_skips_permission_lookup():
    checker = AsyncMock()
    with patch(
        "app.services.ai.agent_manager.AgentManagerService.list_allowed_agents", new=checker
    ):
        await _ensure_agent_selectable(_db(_Agent("a1")), {"user_id": 1, "role": "admin"}, "a1")
    checker.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_admin_without_permission_rejected():
    with patch(
        "app.services.ai.agent_manager.AgentManagerService.list_allowed_agents",
        new=AsyncMock(return_value=[_Agent("other")]),
    ):
        with pytest.raises(HTTPException) as exc:
            await _ensure_agent_selectable(_db(_Agent("a1")), {"user_id": 1, "role": "user"}, "a1")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_non_admin_with_permission_allowed():
    with patch(
        "app.services.ai.agent_manager.AgentManagerService.list_allowed_agents",
        new=AsyncMock(return_value=[_Agent("a1")]),
    ):
        await _ensure_agent_selectable(_db(_Agent("a1")), {"user_id": 1, "role": "user"}, "a1")
