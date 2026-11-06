"""批量删除数据集 API 契约测试。

接口只负责：参数校验（空 / 超量 / 去重）→ 查存在性 → 逐条复用
``MetadataService.delete_dataset`` → 汇总 deleted / missing / failed。
删除本身的副作用（日志、RAGFlow、向量、菜单缓存）由服务层负责，这里 mock 掉。
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.dependencies import get_current_user, require_api_key
from app.core.orm import get_db_session
from app.main import app

pytestmark = pytest.mark.no_infrastructure


class _FakeResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class _FakeSession:
    def __init__(self, existing_ids):
        self.execute = AsyncMock(return_value=_FakeResult(existing_ids))
        self.rollback = AsyncMock()
        self.commit = AsyncMock()


@pytest.fixture(autouse=True)
def override_auth_and_db():
    admin_user = {
        "user_id": 1,
        "user_name": "admin",
        "role": "admin",
        "permissions": ["element:metadata:delete"],
    }
    session = _FakeSession([1, 2])
    app.dependency_overrides[require_api_key] = lambda: admin_user
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[get_db_session] = lambda: session
    yield session
    app.dependency_overrides.pop(require_api_key, None)
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db_session, None)


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_batch_delete_reports_deleted_and_missing():
    with patch(
        "app.api.portal.endpoints.metadata.MetadataService.delete_dataset",
        new_callable=AsyncMock,
    ) as mock_delete:
        async with _client() as client:
            resp = await client.post(
                "/api/portal/metadata/datasets/batch-delete",
                json={"ids": [1, 2, 2, 999]},
            )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["deleted"] == [1, 2]
    assert data["missing"] == [999]
    assert data["failed"] == []
    # 重复 id 只删一次
    assert mock_delete.await_count == 2


@pytest.mark.asyncio
async def test_batch_delete_rejects_empty_ids():
    async with _client() as client:
        resp = await client.post("/api/portal/metadata/datasets/batch-delete", json={"ids": []})

    assert resp.status_code == 400
    assert "请先选择要删除的数据集" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_batch_delete_rejects_too_many_ids():
    async with _client() as client:
        resp = await client.post(
            "/api/portal/metadata/datasets/batch-delete",
            json={"ids": list(range(1, 102))},
        )

    assert resp.status_code == 400
    assert "单次最多删除 100 个数据集" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_batch_delete_collects_failures_without_stopping():
    session = app.dependency_overrides[get_db_session]()
    session.execute = AsyncMock(return_value=_FakeResult([1, 2]))
    calls = {"n": 0}

    async def _delete(db, dataset_id, **kwargs):
        calls["n"] += 1
        if dataset_id == 1:
            raise RuntimeError("ragflow timeout")

    with patch(
        "app.api.portal.endpoints.metadata.MetadataService.delete_dataset",
        new_callable=AsyncMock,
        side_effect=_delete,
    ):
        async with _client() as client:
            resp = await client.post(
                "/api/portal/metadata/datasets/batch-delete",
                json={"ids": [1, 2]},
            )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["deleted"] == [2]
    assert data["failed"][0]["id"] == 1
    assert "ragflow timeout" in data["failed"][0]["error"]
    # 单条失败只回滚该条，不影响后面的 id
    assert calls["n"] == 2
    session.rollback.assert_awaited()


def test_batch_delete_accepts_delete_or_edit_permission():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[3] / "app/api/portal/endpoints/metadata.py"
    ).read_text(encoding="utf-8")
    block = source[source.index('"/datasets/batch-delete"') :]
    block = block[: block.index("@router.get")]
    assert '("element", "element:metadata:delete")' in block
    assert '("element", "element:metadata:edit")' in block
