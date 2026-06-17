import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from unittest.mock import AsyncMock, patch
from app.services.auth_service import AuthService


@pytest.mark.asyncio
async def test_recommend_table_questions_api_success(db_session):
    user_key = await AuthService.generate_api_key("test_recommend_table_user_api", role="user", db=db_session)

    mock_questions = [
        {"label": "用户增长趋势", "query": "统计最近30天每日新增用户数", "type": "dynamic"},
        {"label": "角色分布", "query": "按角色统计当前用户数量分布", "type": "dynamic"},
    ]

    with patch(
        "app.services.dataset_navigation_service.DatasetNavigationService.recommend_table_questions",
        AsyncMock(return_value=mock_questions),
    ) as mock_recommend:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            payload = {
                "table": "智能体用户表",
                "physical_table_name": "ai_agent_users",
                "dataset_name": "智能体数据集",
                "columns": [
                    {
                        "name": "id",
                        "term": "用户ID",
                        "type": "bigint",
                        "description": "主键",
                    }
                ],
            }
            resp = await client.post(
                "/api/v1/chat/dataset-menu/recommend-table-questions",
                json=payload,
                headers={"X-API-Key": user_key},
            )

            assert resp.status_code == 200
            data = resp.json()
            assert data["code"] == 200
            assert data["message"] == "success"
            assert len(data["data"]["questions"]) == 2
            assert data["data"]["questions"][0]["label"] == "用户增长趋势"
            assert data["data"]["questions"][0]["query"] == "统计最近30天每日新增用户数"

            mock_recommend.assert_awaited_once()
