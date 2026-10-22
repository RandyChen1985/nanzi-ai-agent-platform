"""Conversation finalize API and force summary flush."""
import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.session_summary_service import SessionSummaryService


@pytest.mark.asyncio
async def test_finalize_session_empty_history():
    with patch.object(
        SessionSummaryService, "is_enabled", new_callable=AsyncMock, return_value=True
    ), patch(
        "app.services.ai.session_summary_service.memory_service.get_history",
        new_callable=AsyncMock,
        return_value=[],
    ):
        result = await SessionSummaryService.finalize_session("1", "conv-a")
        assert result["finalized"] is False
        assert result["reason"] == "empty_history"


@pytest.mark.asyncio
async def test_finalize_session_calls_merge_with_force():
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    with patch.object(
        SessionSummaryService, "is_enabled", new_callable=AsyncMock, return_value=True
    ), patch(
        "app.services.ai.session_summary_service.memory_service.get_history",
        new_callable=AsyncMock,
        return_value=history,
    ), patch.object(
        SessionSummaryService,
        "merge_session_summary",
        new_callable=AsyncMock,
    ) as mock_merge:
        result = await SessionSummaryService.finalize_session("42", "conv-b")
        assert result["finalized"] is True
        mock_merge.assert_awaited_once()
        assert mock_merge.call_args.kwargs.get("force") is True


@pytest.mark.asyncio
async def test_finalize_api_endpoint():
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.core.dependencies import require_api_key, verify_v1_api_access

    app.dependency_overrides[require_api_key] = lambda: {"user_id": 7, "role": "user"}
    app.dependency_overrides[verify_v1_api_access] = lambda: None

    with patch(
        "app.services.ai.session_summary_service.SessionSummaryService.finalize_session",
        new_callable=AsyncMock,
        return_value={"finalized": True, "conversation_id": "cid-1"},
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                "/api/v1/chat/conversation/cid-1/finalize",
                headers={"X-API-Key": "test-key"},
            )

    app.dependency_overrides.clear()
    assert res.status_code == 200
    assert res.json()["data"]["finalized"] is True


@pytest.mark.asyncio
async def test_finalize_api_returns_before_summary_completion():
    """收尾接口必须立即答复，不能同步等摘要合并。

    历史侧栏连点会持续触发 finalize。摘要合并要跑 LLM + embedding（线上实测均值
    21.8s、最坏 57s），同步等待会把浏览器同源连接占满，随后的 GET /chat/history
    只能排队，界面卡在「正在加载历史记录...」。这里锁住「响应不等摘要」这一契约。
    """
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.core.dependencies import require_api_key, verify_v1_api_access

    app.dependency_overrides[require_api_key] = lambda: {"user_id": 7, "role": "user"}
    app.dependency_overrides[verify_v1_api_access] = lambda: None

    release = asyncio.Event()
    finished = asyncio.Event()
    observed: dict[str, str] = {}

    async def slow_finalize(user_id: str, conversation_id: str):
        observed["user_id"] = user_id
        observed["conversation_id"] = conversation_id
        await release.wait()
        finished.set()
        return {"finalized": True, "conversation_id": conversation_id}

    with patch.object(SessionSummaryService, "finalize_session", new=slow_finalize):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            started = time.perf_counter()
            res = await client.post(
                "/api/v1/chat/conversation/cid-bg/finalize",
                headers={"X-API-Key": "test-key"},
            )
            elapsed = time.perf_counter() - started

        assert res.status_code == 200
        assert res.json()["data"]["finalized"] is True
        assert res.json()["data"]["conversation_id"] == "cid-bg"
        # 摘要还没被放行，响应却已经回来了：说明它跑在后台。
        assert elapsed < 1.0, f"finalize 接口耗时 {elapsed:.2f}s，仍在同步等待摘要"
        assert not finished.is_set()

        release.set()
        await asyncio.wait_for(finished.wait(), timeout=2)
        assert observed == {"user_id": "7", "conversation_id": "cid-bg"}

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_finalize_api_rejects_empty_conversation_id():
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.core.dependencies import require_api_key, verify_v1_api_access

    app.dependency_overrides[require_api_key] = lambda: {"user_id": 7, "role": "user"}
    app.dependency_overrides[verify_v1_api_access] = lambda: None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/v1/chat/conversation/%20/finalize",
            headers={"X-API-Key": "test-key"},
        )

    app.dependency_overrides.clear()
    assert res.status_code == 200
    assert res.json()["data"]["finalized"] is False
    assert res.json()["data"]["reason"] == "missing_conversation_id"
