"""Session summary debounce and memory_search user isolation."""
import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.session_summary_service import SessionSummaryService

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_should_run_rejects_short_assistant():
    with patch(
        "app.services.ai.session_summary_service.MemoryConfigService.get_int",
        new_callable=AsyncMock,
        return_value=30,
    ):
        assert await SessionSummaryService.should_run("1", "c1", "hi") is False


@pytest.mark.asyncio
async def test_should_run_debounce_blocks_until_turns_or_time():
    redis = AsyncMock()
    redis.get = AsyncMock(
        return_value=json.dumps({"last_run": time.time(), "pending_turns": 0})
    )
    redis.set = AsyncMock()

    async def get_int(key, default=0):
        mapping = {
            "memory_summarize_min_assistant_chars": 10,
            "memory_summarize_debounce_seconds": 300,
            "memory_summarize_debounce_turns": 3,
        }
        return mapping.get(key, default)

    with patch(
        "app.services.ai.session_summary_service.MemoryConfigService.get_int",
        side_effect=get_int,
    ), patch(
        "app.services.ai.session_summary_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        ok = await SessionSummaryService.should_run("u1", "conv1", "a" * 50)
        assert ok is False
        assert redis.set.called
        state = json.loads(redis.set.call_args[0][1])
        assert state["pending_turns"] == 1


@pytest.mark.asyncio
async def test_should_run_after_enough_turns():
    redis = AsyncMock()
    redis.get = AsyncMock(
        return_value=json.dumps({"last_run": time.time(), "pending_turns": 2})
    )
    redis.set = AsyncMock()

    async def get_int(key, default=0):
        mapping = {
            "memory_summarize_min_assistant_chars": 10,
            "memory_summarize_debounce_seconds": 300,
            "memory_summarize_debounce_turns": 3,
        }
        return mapping.get(key, default)

    with patch(
        "app.services.ai.session_summary_service.MemoryConfigService.get_int",
        side_effect=get_int,
    ), patch(
        "app.services.ai.session_summary_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        ok = await SessionSummaryService.should_run("u1", "conv1", "assistant reply long enough")
        assert ok is True


@pytest.mark.asyncio
async def test_memory_search_uses_context_user_id():
    from app.core.context import AgentContext, set_agent_context
    from app.services.ai.tools import memory_search_tool

    ctx = AgentContext(
        agent_id="a1",
        agent_name="test",
        user_id=42,
        conversation_id="cid-1",
    )
    set_agent_context(ctx)

    with patch(
        "app.services.ai.tools.memory_search_tool.MemoryConfigService.get_bool",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "app.services.ai.tools.memory_search_tool.MemoryConfigService.get_int",
        new_callable=AsyncMock,
        return_value=3,
    ), patch(
        "app.services.ai.tools.memory_search_tool.SessionSummaryService.search_for_user",
        new_callable=AsyncMock,
        return_value={"summaries": [], "history": []},
    ) as mock_search:
        await memory_search_tool.memory_search.ainvoke({"scope": "summary", "query": "test"})
        mock_search.assert_awaited_once()
        assert mock_search.call_args.kwargs["user_id"] == "42"  # tool coerces to str


@pytest.mark.asyncio
async def test_merge_session_summary_stores_structured_fields_and_refreshes_daily():
    history = [
        {"role": "user", "content": "我们决定保留 conversation_id 粒度。"},
        {"role": "assistant", "content": "好的，同时增加 daily summary。"},
    ]
    meta = {
        "title": "记忆设计",
        "summary": "讨论了记忆管理的会话摘要和每日摘要设计。",
        "key_facts": ["一个会话对应一个 session summary"],
        "decisions": ["保留 conversation_id", "新增 daily summary"],
        "open_items": ["后续补管理 UI"],
        "entities": ["memory_search", "Redis"],
        "memory_type": "project",
    }

    with patch.object(
        SessionSummaryService, "is_enabled", new_callable=AsyncMock, return_value=True
    ), patch.object(
        SessionSummaryService, "should_run", new_callable=AsyncMock, return_value=True
    ), patch(
        "app.services.ai.session_summary_service.memory_service.get_history",
        new_callable=AsyncMock,
        return_value=history,
    ), patch(
        "app.services.ai.session_summary_service.ConversationSummarizer.summarize",
        new_callable=AsyncMock,
        return_value=meta,
    ), patch(
        "app.services.ai.session_summary_service.EmbeddingClient.embed_text",
        new_callable=AsyncMock,
        return_value=[0.1, 0.2],
    ) as mock_embed, patch(
        "app.services.ai.session_summary_service.MemoryIndexService.upsert_summary",
        new_callable=AsyncMock,
    ) as mock_upsert, patch(
        "app.services.ai.session_summary_service.MemoryIndexService.list_summaries",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.ai.session_summary_service.MemoryConfigService.get_int",
        new_callable=AsyncMock,
        return_value=50,
    ), patch(
        "app.services.ai.session_summary_service.DailySummaryService.refresh_for_date",
        new_callable=AsyncMock,
    ) as mock_daily:
        await SessionSummaryService.merge_session_summary("7", "conv-1", "assistant reply")

    mock_upsert.assert_awaited_once()
    kwargs = mock_upsert.await_args.kwargs
    assert kwargs["key_facts"] == meta["key_facts"]
    assert kwargs["decisions"] == meta["decisions"]
    assert kwargs["open_items"] == meta["open_items"]
    assert kwargs["entities"] == meta["entities"]
    assert kwargs["memory_type"] == "project"
    embed_text = mock_embed.await_args.args[0]
    assert "新增 daily summary" in embed_text
    assert "memory_search" in embed_text
    mock_daily.assert_awaited_once_with("7")
