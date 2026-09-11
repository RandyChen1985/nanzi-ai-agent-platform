"""API contract tests for Schema Drift Alerts & Inspection endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.dependencies import get_current_user, require_api_key
from app.main import app
from app.models.metadata import MetaSchemaDriftAlert

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture(autouse=True)
def override_admin_auth():
    admin_user = {
        "user_id": 1,
        "username": "admin",
        "role": "admin",
        "permissions": ["menu:metadata:datasets", "element:metadata:sync", "element:metadata:edit"],
    }
    app.dependency_overrides[require_api_key] = lambda: admin_user
    app.dependency_overrides[get_current_user] = lambda: admin_user
    yield
    app.dependency_overrides.pop(require_api_key, None)
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_get_drift_summary_contract():
    """测试获取全量漂移告警统计概览接口契约。"""
    with patch("app.api.portal.endpoints.metadata.MetadataDriftService.get_drift_summary", new_callable=AsyncMock) as mock_summary:
        mock_summary.return_value = (5, {1: 3, 2: 2})

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/portal/metadata/datasets/drift-summary")

        assert resp.status_code == 200
        data = resp.json()
        assert "total_pending" in data
        assert "datasets" in data
        assert data["total_pending"] == 5
        assert data["datasets"] == {"1": 3, "2": 2}


@pytest.mark.asyncio
async def test_get_dataset_drift_alerts_contract():
    """测试获取数据集下漂移告警列表接口契约。"""
    alert = MetaSchemaDriftAlert(
        id=10,
        dataset_id=1,
        table_name="device_pue",
        column_name="cpu_power_old",
        drift_type="missing_in_db",
        source="runtime",
        hit_count=3,
        status=0,
    )
    with patch("app.api.portal.endpoints.metadata.MetadataDriftService.get_dataset_drift_alerts", new_callable=AsyncMock) as mock_alerts:
        mock_alerts.return_value = [alert]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/portal/metadata/datasets/1/drift-alerts?status=0")

        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["id"] == 10
        assert items[0]["table_name"] == "device_pue"
        assert items[0]["column_name"] == "cpu_power_old"
        assert items[0]["drift_type"] == "missing_in_db"
        assert items[0]["hit_count"] == 3


@pytest.mark.asyncio
async def test_resolve_drift_alert_contract():
    """测试处置告警接口契约（下线字段/忽略）。"""
    with patch("app.api.portal.endpoints.metadata.MetadataDriftService.resolve_alert", new_callable=AsyncMock) as mock_resolve:
        mock_resolve.return_value = {
            "alert_id": 10,
            "status": 1,
            "column_dropped": True,
            "message": "已成功从元数据中下线字段",
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/portal/metadata/drift-alerts/10/resolve",
                json={"action": "drop_column"},
            )

        assert resp.status_code == 200
        res = resp.json()
        assert res["code"] == 200
        assert res["data"]["column_dropped"] is True


@pytest.mark.asyncio
async def test_trigger_inspection_contract():
    """测试触发巡检接口契约。"""
    mock_ds = AsyncMock()
    mock_ds.id = 1
    mock_ds.name = "test_ds"
    mock_ds.status = 1

    mock_task = MagicMock()
    mock_task.task_id = "sync_inspect_999"

    with patch("app.services.metadata_service.MetadataService.get_dataset_by_id", new_callable=AsyncMock) as mock_get_ds, \
         patch("app.services.metadata_sync_log_service.metadata_sync_log_service.create_task", new_callable=AsyncMock) as mock_create_task, \
         patch("app.services.metadata_sync_log_service.metadata_sync_log_service.publish", new_callable=AsyncMock):

        mock_get_ds.return_value = mock_ds
        mock_create_task.return_value = mock_task

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/portal/metadata/datasets/1/inspect-schema")

        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"] == "sync_inspect_999"
        assert body["dataset_id"] == 1
