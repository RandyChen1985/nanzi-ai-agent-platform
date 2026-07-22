from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.conversation_resource_service import (
    ConversationResourceService,
    ResourceScopeStorageError,
)


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


@pytest.mark.asyncio
async def test_get_fails_closed_when_redis_is_unavailable():
    """Redis 故障时不得把已有 scope 降级成空范围。"""
    with patch("app.services.conversation_resource_service.get_redis", new_callable=AsyncMock, return_value=None):
        with pytest.raises(ResourceScopeStorageError):
            await ConversationResourceService.get(7, "conv-1")


@pytest.mark.asyncio
async def test_normalize_for_user_uses_server_owned_resource_metadata():
    """客户端伪造的数据集名与技能 scope 必须被服务端目录覆盖。"""
    dataset = SimpleNamespace(id=12, name="sales_ds", display_name="销售数据")
    with (
        patch(
            "app.services.metadata_service.MetadataService.list_accessible_dataset_options",
            new_callable=AsyncMock,
            return_value=[dataset],
        ),
        patch(
            "app.services.permission_service.PermissionService.filter_knowledge_dataset_ids",
            new_callable=AsyncMock,
            return_value=["kb-1"],
        ),
        patch(
            "app.services.ai.skill_resolver.list_skill_metas",
            return_value=[{"id": "chart", "name": "图表", "scope": "personal", "description": "desc"}],
        ),
    ):
        normalized = await ConversationResourceService.normalize_for_user(
            AsyncMock(),
            user_info={"user_id": 7, "user_name": "tester"},
            scope={
                "project_name": "demo",
                "datasets": [{"id": "12", "name": "伪造名", "dataset_name": "evil"}],
                "knowledge_bases": [{"id": "kb-1", "name": "KB"}],
                "skills": [{"id": "chart", "name": "伪造技能", "scope": "personal"}],
            },
        )

    assert normalized["datasets"] == [
        {"id": "12", "name": "销售数据", "dataset_name": "sales_ds"}
    ]
    assert normalized["skills"][0]["scope"] == "personal"
    assert normalized["skills"][0]["name"] == "图表"
