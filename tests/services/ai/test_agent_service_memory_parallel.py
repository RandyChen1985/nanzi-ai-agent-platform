import asyncio

import pytest
from unittest.mock import AsyncMock

from app.services.ai.agent_service import AgentService


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_date_memory_preload_reads_daily_sources_concurrently(monkeypatch):
    active = 0
    max_active = 0

    async def delayed_read(value):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        active -= 1
        return value

    monkeypatch.setattr(
        "app.services.ai.memory_service.ltm_service.fetch_memory",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.memory_config_service.MemoryConfigService.get_bool",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "app.services.ai.tools.memory_search_tool.parse_date_from_query",
        lambda _query: "2026-08-25",
    )
    monkeypatch.setattr(
        "app.services.ai.daily_summary_service.DailySummaryService.get_daily_summary",
        lambda _user_id, _target_day: delayed_read(
            {"summary": "昨日总结"}
        ),
    )
    monkeypatch.setattr(
        "app.services.ai.memory_index_service.MemoryIndexService.list_session_summaries_for_day",
        lambda _user_id, _target_day: delayed_read(
            [{"title": "昨日会话", "summary": "昨日内容"}]
        ),
    )

    result = await AgentService()._load_memory_context(
        user_info={"user_id": 1},
        early_turn_kind="general",
        execution_options=None,
        user_query="昨天发生了什么",
    )

    assert max_active == 2
    assert result[3] is not None
    assert "昨日总结" in result[3]
    assert "昨日会话" in result[3]
