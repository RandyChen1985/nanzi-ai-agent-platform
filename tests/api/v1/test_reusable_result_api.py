"""可复用结果列表接口的安全契约测试。"""

import inspect
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.api.v1.endpoints import chat as chat_endpoint
from app.api.v1.endpoints.chat import (
    _history_reusable_metadata_window,
    _should_enrich_history_reusable_metadata,
)
from app.api.v1.endpoints.chat import ChatCompletionRequest
from app.schemas.agent import AgentExecutionHistoryResponse
from app.services.ai.memory_service import memory_service
from app.core.orm import get_db_session


pytestmark = pytest.mark.no_infrastructure


def test_artifact_list_supports_conversation_and_trace_scope():
    source = inspect.getsource(chat_endpoint.list_artifacts)

    assert "conversation_id: Optional[str] = Query(None" in source
    assert "trace_id: Optional[str] = Query(None" in source
    assert "AiArtifact.conversation_id == conversation_id" in source
    assert "AiArtifact.trace_id == trace_id" in source


def _fake_require_api_key(user_info):
    async def _inner():
        return user_info

    return _inner


class _FakeScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, *, conversation_owned: bool):
        self.conversation_owned = conversation_owned
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _FakeScalarResult(1 if self.conversation_owned else None)


def _fake_db_session(*, conversation_owned: bool):
    session = _FakeSession(conversation_owned=conversation_owned)

    async def _inner():
        yield session

    return _inner, session


@pytest.mark.asyncio
async def test_list_reusable_results_returns_current_and_deduplicated_stack(monkeypatch):
    monkeypatch.setattr(
        memory_service,
        "get_reusable_result",
        AsyncMock(return_value={
            "result_id": "rr_2",
            "result_type": "data",
            "status": "success",
            "text_excerpt": "new result",
            "trace_id": "trace-2",
        }),
    )
    monkeypatch.setattr(
        memory_service,
        "get_reusable_result_stack",
        AsyncMock(return_value=[
            {
                "result_id": "rr_1",
                "result_type": "data",
                "status": "success",
                "text_excerpt": "old result",
                "trace_id": "trace-1",
            },
            {
                "result_id": "rr_2",
                "result_type": "data",
                "status": "success",
                "text_excerpt": "new result",
                "trace_id": "trace-2",
            },
        ]),
    )
    monkeypatch.setattr(memory_service, "get_last_data_result", AsyncMock(return_value=None))
    db_override, _ = _fake_db_session(conversation_owned=True)
    app.dependency_overrides[chat_endpoint.require_api_key] = _fake_require_api_key({"user_id": "7"})
    app.dependency_overrides[get_db_session] = db_override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/v1/chat/reusable-results?conversation_id=conv-1",
                headers={"X-API-Key": "test-key"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert [item["result_id"] for item in items] == ["rr_2", "rr_1"]
    assert items[0]["is_current"] is True
    assert items[0]["trace_id"] == "trace-2"
    assert items[1]["trace_id"] == "trace-1"
    assert all("tool_args" not in item for item in items)


@pytest.mark.asyncio
async def test_list_reusable_results_includes_legacy_data_cache_during_migration(monkeypatch):
    monkeypatch.setattr(memory_service, "get_reusable_result", AsyncMock(return_value=None))
    monkeypatch.setattr(memory_service, "get_reusable_result_stack", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        memory_service,
        "get_last_data_result",
        AsyncMock(return_value={
            "rows": {"rows": [{"amount": 10}]},
            "saved_at": "2026-08-30T10:00:00+00:00",
            "dataset_name": "sales",
        }),
    )
    db_override, _ = _fake_db_session(conversation_owned=True)
    app.dependency_overrides[chat_endpoint.require_api_key] = _fake_require_api_key({"user_id": "7"})
    app.dependency_overrides[get_db_session] = db_override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/chat/reusable-results?conversation_id=conv-1",
                headers={"X-API-Key": "test-key"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["result_type"] == "data"
    assert items[0]["is_current"] is True


@pytest.mark.asyncio
async def test_list_reusable_results_rejects_unowned_conversation(monkeypatch):
    monkeypatch.setattr(
        memory_service,
        "get_reusable_result",
        AsyncMock(return_value={
            "result_id": "rr-secret",
            "result_type": "data",
            "status": "success",
            "text_excerpt": "private result",
        }),
    )
    monkeypatch.setattr(memory_service, "get_reusable_result_stack", AsyncMock(return_value=[]))
    monkeypatch.setattr(memory_service, "get_last_data_result", AsyncMock(return_value=None))
    db_override, _ = _fake_db_session(conversation_owned=False)
    app.dependency_overrides[chat_endpoint.require_api_key] = _fake_require_api_key({"user_id": "7"})
    app.dependency_overrides[get_db_session] = db_override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/chat/reusable-results?conversation_id=not-owned",
                headers={"X-API-Key": "test-key"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert "private result" not in response.text


@pytest.mark.asyncio
async def test_list_reusable_results_requires_stable_user_identity():
    app.dependency_overrides[chat_endpoint.require_api_key] = _fake_require_api_key({})
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/v1/chat/reusable-results?conversation_id=conv-1",
                headers={"X-API-Key": "test-key"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


def test_chat_completion_request_accepts_bounded_reusable_result_id():
    request = ChatCompletionRequest(
        messages=[{"role": "user", "content": "继续分析"}],
        reusable_result_id="rr_old",
    )
    assert request.reusable_result_id == "rr_old"


def test_chat_completion_request_rejects_overlong_reusable_result_id():
    with pytest.raises(ValueError):
        ChatCompletionRequest(
            messages=[{"role": "user", "content": "继续分析"}],
            reusable_result_id="r" * 129,
        )


def test_history_response_exposes_output_metadata_for_refresh_recovery():
    fields = AgentExecutionHistoryResponse.model_fields

    assert "has_data_output" in fields
    assert "reusable_result_id" in fields
    assert "reusable_result_status" in fields


def test_history_endpoint_enriches_reusable_metadata_from_redis_history():
    source = inspect.getsource(chat_endpoint.get_history)

    assert "memory_service.get_history" in source
    assert "reusable_result_id" in source
    assert "reusable_result_status" in source


def test_history_reusable_metadata_skips_admin_cross_user_queries():
    assert _should_enrich_history_reusable_metadata({"role": "admin", "user_id": 1}) is False
    assert _should_enrich_history_reusable_metadata({"role": "user", "user_id": 1}) is True


def test_history_reusable_metadata_reads_only_the_requested_page_window():
    assert _history_reusable_metadata_window(1, 20) == {"limit": 40, "offset": 0}
    assert _history_reusable_metadata_window(2, 20) == {"limit": 40, "offset": 40}


@pytest.mark.asyncio
async def test_list_reusable_results_returns_empty_for_owned_empty_conversation(monkeypatch):
    """空会话（新创建、尚无任何执行历史）应视为归属并返回 200 空列表，而不是 404。

    新会话创建时前端会设置 active conversation = 该 cid，因此 active 判定应视为归属。
    """
    monkeypatch.setattr(memory_service, "get_reusable_result", AsyncMock(return_value=None))
    monkeypatch.setattr(memory_service, "get_reusable_result_stack", AsyncMock(return_value=[]))
    monkeypatch.setattr(memory_service, "get_last_data_result", AsyncMock(return_value=None))
    monkeypatch.setattr(memory_service, "history_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(
        memory_service,
        "get_active_conversation",
        AsyncMock(return_value="new-conv"),
    )
    # DB 无执行历史 → 旧实现会因归属失败返回 404
    db_override, _ = _fake_db_session(conversation_owned=False)
    app.dependency_overrides[chat_endpoint.require_api_key] = _fake_require_api_key({"user_id": "7"})
    app.dependency_overrides[get_db_session] = db_override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/chat/reusable-results?conversation_id=new-conv",
                headers={"X-API-Key": "test-key"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["data"]["items"] == []
    assert response.json()["data"]["total"] == 0


@pytest.mark.asyncio
async def test_list_reusable_results_still_404s_unowned_conversation_with_no_trace(monkeypatch):
    """对既无执行历史、也不是活跃会话、也无 Redis 历史痕遁的其他用户会话，仍应 404。"""
    monkeypatch.setattr(
        memory_service,
        "get_reusable_result",
        AsyncMock(return_value={
            "result_id": "rr-secret",
            "result_type": "data",
            "status": "success",
            "text_excerpt": "private result",
        }),
    )
    monkeypatch.setattr(memory_service, "get_reusable_result_stack", AsyncMock(return_value=[]))
    monkeypatch.setattr(memory_service, "get_last_data_result", AsyncMock(return_value=None))
    monkeypatch.setattr(memory_service, "history_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(memory_service, "get_active_conversation", AsyncMock(return_value="my-other-conv"))
    db_override, _ = _fake_db_session(conversation_owned=False)
    app.dependency_overrides[chat_endpoint.require_api_key] = _fake_require_api_key({"user_id": "7"})
    app.dependency_overrides[get_db_session] = db_override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/chat/reusable-results?conversation_id=stranger-conv",
                headers={"X-API-Key": "test-key"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
