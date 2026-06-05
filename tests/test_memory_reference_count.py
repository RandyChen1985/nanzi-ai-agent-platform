"""Test suite for memory reference count tracking."""
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.memory_index_service import MemoryIndexService
from app.services.ai.session_summary_service import SessionSummaryService

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_upsert_summary_initializes_reference_count():
    redis = AsyncMock()
    redis.hset = AsyncMock()
    redis.hsetnx = AsyncMock()
    redis.expire = AsyncMock()

    with patch(
        "app.services.ai.memory_index_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ), patch(
        "app.services.ai.memory_index_service.MemoryIndexService.summary_ttl_seconds",
        new_callable=AsyncMock,
        return_value=3600,
    ), patch(
        "app.services.ai.memory_index_service.MemoryIndexService.ensure_index",
        new_callable=AsyncMock,
        return_value=True,
    ):
        await MemoryIndexService.upsert_summary(
            user_id="user_123",
            conversation_id="conv_456",
            title="Test Title",
            summary="Test Summary",
            turn_count=2,
        )

        redis.hset.assert_called_once()
        redis.hsetnx.assert_called_once_with(
            "memory:summary:user_123:conv_456", "reference_count", "0"
        )
        redis.expire.assert_called_once()


@pytest.mark.asyncio
async def test_parse_hash_parses_reference_count():
    data = {
        b"user_id": b"user_123",
        b"conversation_id": b"conv_456",
        b"title": b"Test Title",
        b"summary": b"Test Summary",
        b"reference_count": b"5",  # Has reference count
    }

    parsed = await MemoryIndexService._parse_hash(data)
    assert parsed["reference_count"] == 5
    assert parsed["title"] == "Test Title"

    # Test fallback when missing or corrupted
    data_corrupted = data.copy()
    data_corrupted[b"reference_count"] = b"invalid_number"
    parsed_corrupted = await MemoryIndexService._parse_hash(data_corrupted)
    assert parsed_corrupted["reference_count"] == 0

    data_missing = data.copy()
    data_missing.pop(b"reference_count")
    parsed_missing = await MemoryIndexService._parse_hash(data_missing)
    assert parsed_missing["reference_count"] == 0


@pytest.mark.asyncio
async def test_search_for_user_increments_reference_count():
    redis = AsyncMock()
    redis.hincrby = AsyncMock()

    mock_summaries = [
        {
            "conversation_id": "conv_abc",
            "title": "A",
            "summary": "Sum A",
            "reference_count": 2,
        },
        {
            "conversation_id": "conv_xyz",
            "title": "B",
            "summary": "Sum B",
            "reference_count": 0,
        },
    ]

    with patch(
        "app.services.ai.session_summary_service.EmbeddingClient.embed_text",
        new_callable=AsyncMock,
        return_value=[0.1, 0.2],
    ), patch(
        "app.services.ai.session_summary_service.MemoryIndexService.search_summaries",
        new_callable=AsyncMock,
        return_value=mock_summaries,
    ), patch(
        "app.services.ai.session_summary_service.get_redis",
        new_callable=AsyncMock,
        return_value=redis,
    ):
        result = await SessionSummaryService.search_for_user(
            user_id="user_789", query="hello", scope="summary", limit=2
        )

        assert len(result["summaries"]) == 2
        # Check in-memory update
        assert result["summaries"][0]["reference_count"] == 3
        assert result["summaries"][1]["reference_count"] == 1

        # Check Redis hincrby commands
        assert redis.hincrby.call_count == 2
        redis.hincrby.assert_any_call("memory:summary:user_789:conv_abc", "reference_count", 1)
        redis.hincrby.assert_any_call("memory:summary:user_789:conv_xyz", "reference_count", 1)
