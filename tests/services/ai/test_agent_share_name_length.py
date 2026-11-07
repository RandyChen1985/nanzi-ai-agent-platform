"""分享副本命名不能超过 ai_agents.name 的 100 字符上限。"""
import pytest

from app.services.ai.agent_manager import AgentManagerService
from app.services.ai.agent_share_service import AgentShareService

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_unique_agent_name_truncates_to_column_limit(monkeypatch):
    async def no_conflict(session, name):
        return None

    monkeypatch.setattr(
        AgentManagerService, "find_agent_name_conflict", no_conflict
    )

    base = "a" * 100

    copy_name = await AgentShareService._unique_agent_name(object(), base)
    assert len(copy_name) <= 100
    assert copy_name.endswith("-copy")

    exact_name = await AgentShareService._unique_agent_name(
        object(), base, prefer_exact=True
    )
    assert len(exact_name) <= 100
