from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.metadata_service import MetadataService

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_list_accessible_dataset_options_returns_empty_without_user_for_non_admin():
    db = AsyncMock()
    rows = await MetadataService.list_accessible_dataset_options(
        db,
        user_id=None,
        is_admin=False,
    )
    assert rows == []
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_list_accessible_dataset_options_admin_queries_once():
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [MagicMock(id=1, name="ds1")]
    db.execute.return_value = result

    rows = await MetadataService.list_accessible_dataset_options(
        db,
        user_id=99,
        is_admin=True,
        status=1,
    )
    assert len(rows) == 1
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_accessible_dataset_options_non_admin_applies_permission_filter(monkeypatch):
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db.execute.return_value = result

    # 权限范围统一由 dataset_scope_service 解析（含全员基线/过期/表级），
    # 这里固定返回 {7}，只验证列表查询附加了 id 过滤。
    async def fake_accessible_ids(_db, *, user_id=None, is_admin=False):
        assert user_id == 42
        assert is_admin is False
        return {7}

    monkeypatch.setattr(
        MetadataService, "accessible_dataset_ids", staticmethod(fake_accessible_ids)
    )

    await MetadataService.list_accessible_dataset_options(
        db,
        user_id=42,
        is_admin=False,
        status=1,
    )
    db.execute.assert_awaited_once()
    stmt = db.execute.await_args.args[0]
    # 非管理员路径会附加 id IN (可访问数据集)
    assert hasattr(stmt, "whereclause")
    assert stmt.whereclause is not None
