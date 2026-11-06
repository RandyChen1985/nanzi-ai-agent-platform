"""数据集复制新建 API 契约测试。

关注接口层三件事，服务层逻辑见 ``tests/services/test_dataset_clone_service.py``：

1. ``GET /datasets/check-unique`` 不能被 ``GET /datasets/{dataset_id}`` 抢先匹配
   （否则路径参数收到 "check-unique" 会 422）；
2. ``POST /datasets/{source_id}/clone`` 的状态码契约：404 源不存在 / 400 参数不合法 / 200 成功；
3. 写接口权限绑定 ``element:metadata:edit``。
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.dependencies import get_current_user, require_api_key
from app.main import app
from app.schemas.metadata import DatasetResponse
from app.services.dataset_clone_service import CloneValidationError

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture(autouse=True)
def override_admin_auth():
    admin_user = {
        "user_id": 1,
        "user_name": "admin",
        "role": "admin",
        "permissions": ["element:metadata:edit"],
    }
    app.dependency_overrides[require_api_key] = lambda: admin_user
    app.dependency_overrides[get_current_user] = lambda: admin_user
    yield
    app.dependency_overrides.pop(require_api_key, None)
    app.dependency_overrides.pop(get_current_user, None)


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _clone_body(**overrides):
    body = {
        "name": "nanzi_resources_copy",
        "display_name": "资源数据集（副本）",
        "data_source": "nanzi_clickhouse",
        "tables": [
            {
                "physical_name": "dim_resource",
                "term": "资源维表",
                "columns": [
                    {"physical_name": "resource_id", "term": "资源ID", "type": "String"}
                ],
            }
        ],
        "relationships": [
            {
                "source_table": "dim_resource",
                "target_table": "dim_resource",
                "join_condition": "dim_resource.resource_id = dim_resource.resource_id",
                "join_type": "LEFT",
            }
        ],
        "metrics": [
            {"name": "resource_pv", "display_name": "资源访问量", "calculation_logic": "SUM(x)"}
        ],
        "copy_permissions": False,
        "log_mode": "detail",
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_check_unique_route_is_not_shadowed_by_dataset_id():
    payload = {
        "name": {"available": True, "reason": "ok", "message": "可用"},
        "display_name": {"available": False, "reason": "taken", "message": "已占用"},
    }
    with patch(
        "app.services.dataset_clone_service.DatasetCloneService.check_unique",
        new_callable=AsyncMock,
        return_value=payload,
    ) as mock_check:
        async with _client() as client:
            resp = await client.get(
                "/api/portal/metadata/datasets/check-unique",
                params={"name": "nanzi_resources_copy", "display_name": "资源数据集"},
            )

    # 422 就说明被 /datasets/{dataset_id} 抢先匹配了
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["name"]["available"] is True
    assert data["display_name"]["reason"] == "taken"
    mock_check.assert_awaited_once()


@pytest.mark.asyncio
async def test_clone_returns_dataset_and_summary():
    dataset = DatasetResponse(
        id=88,
        name="nanzi_resources_copy",
        display_name="资源数据集（副本）",
        data_source="nanzi_clickhouse",
        status=1,
        created_at=datetime(2026, 11, 6, 14, 32, 8),
        updated_at=datetime(2026, 11, 6, 14, 32, 8),
        table_count=1,
        metric_count=1,
        relationship_count=1,
    )
    summary = {
        "tables": 1,
        "columns": 1,
        "relationships": 1,
        "metrics": 1,
        "skipped_relationships": 0,
    }
    with patch(
        "app.services.dataset_clone_service.DatasetCloneService.clone_dataset",
        new_callable=AsyncMock,
        return_value={"dataset": dataset, "summary": summary},
    ) as mock_clone:
        async with _client() as client:
            resp = await client.post(
                "/api/portal/metadata/datasets/12/clone", json=_clone_body()
            )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["dataset"]["id"] == 88
    assert data["dataset"]["name"] == "nanzi_resources_copy"
    assert data["summary"] == summary
    assert mock_clone.await_args.args[1] == 12


@pytest.mark.asyncio
async def test_clone_returns_404_when_source_missing():
    with patch(
        "app.services.dataset_clone_service.DatasetCloneService.clone_dataset",
        new_callable=AsyncMock,
        side_effect=LookupError("source_dataset_not_found"),
    ):
        async with _client() as client:
            resp = await client.post(
                "/api/portal/metadata/datasets/999/clone", json=_clone_body()
            )

    assert resp.status_code == 404
    assert "源数据集不存在" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_clone_returns_400_on_validation_error():
    with patch(
        "app.services.dataset_clone_service.DatasetCloneService.clone_dataset",
        new_callable=AsyncMock,
        side_effect=CloneValidationError("数据集 ID「nanzi_resources」已被占用，请换一个"),
    ):
        async with _client() as client:
            resp = await client.post(
                "/api/portal/metadata/datasets/12/clone", json=_clone_body()
            )

    assert resp.status_code == 400
    assert "已被占用" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_clone_rejects_missing_name():
    body = _clone_body()
    body.pop("name")
    async with _client() as client:
        resp = await client.post("/api/portal/metadata/datasets/12/clone", json=body)
    # 项目统一把 RequestValidationError 转成 400（app/main.py 的 handler）
    assert resp.status_code == 400
    assert "参数校验失败" in resp.json()["message"]


def test_clone_routes_require_metadata_edit_permission():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[3] / "app/api/portal/endpoints/metadata.py").read_text(
        encoding="utf-8"
    )
    check_unique_section = source[
        source.index('"/datasets/check-unique"') : source.index('"/datasets/{source_id}/clone"')
    ]
    clone_section = source[
        source.index('"/datasets/{source_id}/clone"') : source.index(
            '"/datasets/{source_id}/clone"'
        )
        + 400
    ]
    assert 'require_permission("element", "element:metadata:edit")' in check_unique_section
    assert 'require_permission("element", "element:metadata:edit")' in clone_section
