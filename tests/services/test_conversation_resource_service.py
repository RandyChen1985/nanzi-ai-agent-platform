from unittest.mock import AsyncMock, patch

import pytest

from app.services.conversation_resource_service import ConversationResourceService


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_replace_keeps_only_explicit_project_resources():
    redis = AsyncMock()
    with patch("app.services.conversation_resource_service.get_redis", new_callable=AsyncMock, return_value=redis):
        scope = await ConversationResourceService.replace(
            7,
            "conv-1",
            {
                "project_name": "销售分析",
                "datasets": [{"id": "sales_ds", "name": "销售数据"}, {"name": "无 ID"}],
                "knowledge_bases": [{"id": "kb-1"}],
                "skills": [{"id": "skill-1"}],
            },
        )

    assert scope["project_name"] == "销售分析"
    assert [item["id"] for item in scope["datasets"]] == ["sales_ds"]
    redis.set.assert_awaited_once()

