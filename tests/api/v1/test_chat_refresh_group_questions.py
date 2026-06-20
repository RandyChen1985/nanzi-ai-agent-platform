import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from unittest.mock import AsyncMock, patch
from app.api.v1.endpoints import chat as chat_endpoint
from app.core.orm import get_db_session

pytestmark = pytest.mark.no_infrastructure


async def fake_require_api_key():
    return {"user_id": "7", "role": "user"}


async def fake_db_session():
    yield None

@pytest.mark.asyncio
async def test_refresh_group_questions_api_success():
    mock_questions = [
        {"label": "新问题1", "query": "指令1", "type": "dynamic"},
        {"label": "新问题2", "query": "指令2", "type": "dynamic"}
    ]
    
    with patch(
        "app.services.dataset_navigation_service.DatasetNavigationService.refresh_group_questions",
        AsyncMock(return_value=mock_questions)
    ) as mock_refresh:
        app.dependency_overrides[chat_endpoint.require_api_key] = fake_require_api_key
        app.dependency_overrides[get_db_session] = fake_db_session
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                payload = {
                    "group_title": "智能体运行分析",
                    "tables": ["智能体访问日志"],
                    "dataset_menu_hash": "abc123",
                    "group_id": "ai-agent-meta",
                    "exclude_questions": [
                        {"label": "旧问题", "query": "分析最近一周的智能体访问量"}
                    ],
                }
                resp = await client.post(
                    "/api/v1/chat/dataset-menu/refresh-group-questions",
                    json=payload,
                    headers={"X-API-Key": "test-key"}
                )

                assert resp.status_code == 200
                data = resp.json()
                assert data["code"] == 200
                assert data["message"] == "success"
                assert len(data["data"]["questions"]) == 2
                assert data["data"]["questions"][0]["label"] == "新问题1"
                assert data["data"]["questions"][0]["query"] == "指令1"

                mock_refresh.assert_awaited_once()
                kwargs = mock_refresh.await_args.kwargs
                assert kwargs["user_id"] == 7
                assert kwargs["is_admin"] is False
                assert kwargs["dataset_menu_hash"] == "abc123"
                assert kwargs["group_id"] == "ai-agent-meta"
                assert kwargs["exclude_questions"][0]["query"] == "分析最近一周的智能体访问量"
        finally:
            app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_refresh_group_followups_api_success():
    mock_followups = [
        {"label": "字段口径", "query": "说明字段口径", "type": "dynamic"},
        {"label": "关联分析", "query": "还能关联哪些表", "type": "dynamic"},
    ]

    with patch(
        "app.services.dataset_navigation_service.DatasetNavigationService.refresh_group_followups",
        AsyncMock(return_value=mock_followups),
    ) as mock_refresh:
        app.dependency_overrides[chat_endpoint.require_api_key] = fake_require_api_key
        app.dependency_overrides[get_db_session] = fake_db_session
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/chat/dataset-menu/refresh-group-questions",
                    json={
                        "group_title": "智能体元数据",
                        "tables": ["智能体访问日志"],
                        "purpose": "followups",
                    },
                    headers={"X-API-Key": "test-key"},
                )

                assert resp.status_code == 200
                data = resp.json()
                assert data["code"] == 200
                assert len(data["data"]["questions"]) == 2
                mock_refresh.assert_awaited_once()
        finally:
            app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_refresh_group_questions_api_returns_no_more_unique_reason():
    with patch(
        "app.services.dataset_navigation_service.DatasetNavigationService.refresh_group_questions",
        AsyncMock(return_value=[]),
    ):
        app.dependency_overrides[chat_endpoint.require_api_key] = fake_require_api_key
        app.dependency_overrides[get_db_session] = fake_db_session
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/chat/dataset-menu/refresh-group-questions",
                    json={
                        "group_title": "智能体元数据",
                        "tables": ["智能体访问日志"],
                        "dataset_menu_hash": "abc123",
                    },
                    headers={"X-API-Key": "test-key"},
                )

            assert resp.status_code == 200
            data = resp.json()
            assert data["code"] == 200
            assert data["data"]["questions"] == []
            assert data["data"]["refresh_disabled_reason"] == "暂无更多不同问题，稍后再试"
        finally:
            app.dependency_overrides.clear()
