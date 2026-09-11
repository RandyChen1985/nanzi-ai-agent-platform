"""Unit tests for MetadataDriftService."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.metadata import MetaColumn, MetaDataset, MetaSchemaDriftAlert, MetaTable
from app.services.metadata_drift_service import MetadataDriftService

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_record_drift_alert_creates_new_alert():
    """测试首次检出 Schema 漂移时，新增一条 pending 状态告警。"""
    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    # 第一次查询已存在告警返回 None，查询 table_id 返回 101
    mock_scalars = MagicMock()
    mock_scalars.first.return_value = None
    mock_result_alert = MagicMock()
    mock_result_alert.scalars.return_value = mock_scalars

    mock_result_table = MagicMock()
    mock_result_table.scalar.return_value = 101

    mock_db.execute.side_effect = [mock_result_alert, mock_result_table]

    alert = await MetadataDriftService.record_drift_alert_core(
        mock_db,
        dataset_id=1,
        table_name="device_pue",
        column_name="cpu_power_old",
        drift_type="missing_in_db",
        source="runtime",
        error_sample="unknown column 'cpu_power_old'",
    )

    assert alert is not None
    assert alert.dataset_id == 1
    assert alert.table_id == 101
    assert alert.table_name == "device_pue"
    assert alert.column_name == "cpu_power_old"
    assert alert.drift_type == "missing_in_db"
    assert alert.source == "runtime"
    assert alert.hit_count == 1
    assert alert.status == 0
    mock_db.add.assert_called_once_with(alert)


@pytest.mark.asyncio
async def test_record_drift_alert_with_dataset_name_resolution():
    """测试仅传入 dataset_name 时，通过数据库反查成功获取 dataset_id 并写入告警，避免 None 导致 1048 报错。"""
    mock_db = AsyncMock()
    mock_db.add = MagicMock()

    # 1. 反查 dataset_id 返回 55
    mock_ds_res = MagicMock()
    mock_ds_res.scalar.return_value = 55

    # 2. 查询已存在告警返回 None
    mock_scalars = MagicMock()
    mock_scalars.first.return_value = None
    mock_alert_res = MagicMock()
    mock_alert_res.scalars.return_value = mock_scalars

    # 3. 反查 table_id 返回 202
    mock_t_res = MagicMock()
    mock_t_res.scalar.return_value = 202

    mock_db.execute.side_effect = [mock_ds_res, mock_alert_res, mock_t_res]

    alert = await MetadataDriftService.record_drift_alert_core(
        mock_db,
        dataset_id=None,
        dataset_name="test_ds",
        table_name="test",
        column_name="salary",
        error_sample="Unknown column 'salary'",
    )

    assert alert is not None
    assert alert.dataset_id == 55
    assert alert.table_id == 202
    assert alert.column_name == "salary"
    mock_db.add.assert_called_once_with(alert)



@pytest.mark.asyncio
async def test_record_drift_alert_increments_hit_count_when_exists():
    """测试同表同列已有待处理告警时，自动累加 hit_count 并更新时间。"""
    mock_db = AsyncMock()
    existing_alert = MetaSchemaDriftAlert(
        id=1,
        dataset_id=1,
        table_name="device_pue",
        column_name="cpu_power_old",
        hit_count=2,
        status=0,
    )

    mock_scalars = MagicMock()
    mock_scalars.first.return_value = existing_alert
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    mock_db.execute.return_value = mock_result

    alert = await MetadataDriftService.record_drift_alert_core(
        mock_db,
        dataset_id=1,
        table_name="device_pue",
        column_name="cpu_power_old",
        error_sample="repeated error",
    )

    assert alert is existing_alert
    assert alert.hit_count == 3
    assert alert.error_sample == "repeated error"
    mock_db.add.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_alert_drop_column():
    """测试人机协同处置：选择下线字段时，删除对应 MetaColumn 并将告警标记为 resolved。"""
    mock_db = AsyncMock()
    alert = MetaSchemaDriftAlert(
        id=1,
        dataset_id=10,
        table_name="device_pue",
        column_name="cpu_power_old",
        status=0,
    )
    table = MetaTable(id=20, dataset_id=10, physical_name="device_pue", term="PUE指标表")
    col = MetaColumn(id=30, table_id=20, physical_name="cpu_power_old", term="旧功耗")

    # 查询 alert, 查询 table, 查询 col
    mock_alert_res = MagicMock()
    mock_alert_res.scalars.return_value.first.return_value = alert

    mock_table_res = MagicMock()
    mock_table_res.scalars.return_value.first.return_value = table

    mock_col_res = MagicMock()
    mock_col_res.scalars.return_value.first.return_value = col

    mock_db.execute.side_effect = [mock_alert_res, mock_table_res, mock_col_res]

    res = await MetadataDriftService.resolve_alert(mock_db, 1, action="drop_column")

    assert res["status"] == 1
    assert res["column_dropped"] is True
    assert alert.status == 1
    mock_db.delete.assert_called_once_with(col)
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_resolve_alert_ignore():
    """测试人机协同处置：选择忽略告警时，保留元数据列，告警状态置为 ignored。"""
    mock_db = AsyncMock()
    alert = MetaSchemaDriftAlert(
        id=1,
        dataset_id=10,
        table_name="device_pue",
        column_name="cpu_power_old",
        status=0,
    )
    mock_alert_res = MagicMock()
    mock_alert_res.scalars.return_value.first.return_value = alert
    mock_db.execute.return_value = mock_alert_res

    res = await MetadataDriftService.resolve_alert(mock_db, 1, action="ignore")

    assert res["status"] == 2
    assert res["column_dropped"] is False
    assert alert.status == 2
    mock_db.delete.assert_not_called()
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_resolve_alert_add_column():
    """测试人机协同处置：选择将物理新增字段录入元数据时，创建 MetaColumn 并标记告警为已解决。"""
    mock_db = AsyncMock()
    mock_db.add = MagicMock()

    alert = MetaSchemaDriftAlert(
        id=2,
        dataset_id=10,
        table_name="device_pue",
        column_name="pue_ratio_v2",
        status=0,
    )
    mock_alert_res = MagicMock()
    mock_alert_res.scalars.return_value.first.return_value = alert

    table = MetaTable(id=101, dataset_id=10, physical_name="device_pue")
    mock_table_res = MagicMock()
    mock_table_res.scalars.return_value.first.return_value = table

    # 查 column 不存在
    mock_col_res = MagicMock()
    mock_col_res.scalars.return_value.first.return_value = None

    # 查 dataset 关联
    mock_ds_res = MagicMock()
    mock_ds_res.scalars.return_value.first.return_value = None

    mock_db.execute.side_effect = [mock_alert_res, mock_table_res, mock_col_res, mock_ds_res]

    res = await MetadataDriftService.resolve_alert(mock_db, 2, action="add_column")

    assert res["status"] == 1
    assert res["column_added"] is True
    assert alert.status == 1
    mock_db.add.assert_called_once()
    added_col = mock_db.add.call_args[0][0]
    assert isinstance(added_col, MetaColumn)
    assert added_col.physical_name == "pue_ratio_v2"
    assert added_col.table_id == 101
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_batch_resolve_alerts():
    """测试批量处置告警。"""
    mock_db = AsyncMock()
    alert1 = MetaSchemaDriftAlert(id=1, dataset_id=10, table_name="t1", column_name="c1", status=0)
    alert2 = MetaSchemaDriftAlert(id=2, dataset_id=10, table_name="t1", column_name="c2", status=0)

    mock_alerts_res = MagicMock()
    mock_alerts_res.scalars.return_value.all.return_value = [alert1, alert2]
    mock_db.execute.return_value = mock_alerts_res

    res = await MetadataDriftService.batch_resolve_alerts(
        mock_db, dataset_id=10, action="ignore", drift_type="new_in_db"
    )

    assert res["processed_count"] == 2
    assert alert1.status == 2
    assert alert2.status == 2
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_get_drift_summary():
    """测试查询全局待处理告警总数与数据集分布映射。"""
    mock_db = AsyncMock()
    mock_res = MagicMock()
    mock_res.all.return_value = [(1, 3), (2, 1)]
    mock_db.execute.return_value = mock_res

    total, counts = await MetadataDriftService.get_drift_summary(mock_db)

    assert total == 4
    assert counts == {1: 3, 2: 1}


@pytest.mark.asyncio
async def test_get_all_drift_alerts():
    """测试查询跨数据集全局漂移告警列表，并附带 dataset_name。"""
    mock_db = AsyncMock()
    alert1 = MetaSchemaDriftAlert(id=1, dataset_id=10, table_name="t1", column_name="c1", status=0)
    alert2 = MetaSchemaDriftAlert(id=2, dataset_id=20, table_name="t2", column_name="c2", status=0)

    mock_res = MagicMock()
    mock_res.all.return_value = [(alert1, "销售分析库"), (alert2, "设备运营库")]
    mock_db.execute.return_value = mock_res

    alerts = await MetadataDriftService.get_all_drift_alerts(mock_db)

    assert len(alerts) == 2
    assert alerts[0]["dataset_name"] == "销售分析库"
    assert alerts[1]["dataset_name"] == "设备运营库"
    assert alerts[0]["column_name"] == "c1"


@pytest.mark.asyncio
async def test_batch_resolve_alerts_global():
    """测试跨数据集全局批量处置。"""
    mock_db = AsyncMock()
    alert1 = MetaSchemaDriftAlert(id=1, dataset_id=10, table_name="t1", column_name="c1", status=0)
    alert2 = MetaSchemaDriftAlert(id=2, dataset_id=20, table_name="t2", column_name="c2", status=0)

    mock_alerts_res = MagicMock()
    mock_alerts_res.scalars.return_value.all.return_value = [alert1, alert2]
    mock_db.execute.return_value = mock_alerts_res

    res = await MetadataDriftService.batch_resolve_alerts_global(
        mock_db, action="ignore", alert_ids=[1, 2]
    )

    assert res["processed_count"] == 2
    assert res["failed_count"] == 0
    assert alert1.status == 2
    assert alert2.status == 2
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_batch_resolve_partial_failure_counts_failed():
    """测试批量处置部分失败时，failed_count 正确统计，processed_count 保持严格成功数。"""
    mock_db = AsyncMock()
    alert1 = MetaSchemaDriftAlert(id=1, dataset_id=10, table_name="t1", column_name="c1", status=0)
    alert2 = MetaSchemaDriftAlert(id=2, dataset_id=10, table_name="t1", column_name="c2", status=0)

    mock_alerts_res = MagicMock()
    mock_alerts_res.scalars.return_value.all.return_value = [alert1, alert2]
    mock_db.execute.return_value = mock_alerts_res

    real_core = MetadataDriftService._resolve_single_alert_core

    async def flaky_core(db, alert, action):
        if alert.id == 2:
            raise ValueError(f"表 {alert.table_name} 未找到")
        return await real_core(db, alert, action)

    with patch.object(
        MetadataDriftService, "_resolve_single_alert_core", side_effect=flaky_core
    ):
        res = await MetadataDriftService.batch_resolve_alerts(
            mock_db, dataset_id=10, action="ignore", drift_type="new_in_db"
        )

    assert res["processed_count"] == 1
    assert res["failed_count"] == 1
    assert alert1.status == 2
    assert alert2.status == 0
    assert "1 项处置失败" in res["message"]
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_resolve_alert_sync_type():
    """测试处置类型不匹配告警：将元数据字段类型校准为物理库实际类型。"""
    mock_db = AsyncMock()
    alert = MetaSchemaDriftAlert(
        id=99,
        dataset_id=1,
        table_name="staff_list",
        column_name="sid",
        drift_type="type_mismatch",
        status=0,
        error_sample="巡检发现类型不匹配：元数据声明为 String，物理库实际为 tinyint",
    )
    mock_table = MetaTable(id=10, dataset_id=1, physical_name="staff_list")
    mock_col = MetaColumn(id=101, table_id=10, physical_name="sid", type="String")

    mock_alert_res = MagicMock()
    mock_alert_res.scalars.return_value.first.return_value = alert

    mock_t_res = MagicMock()
    mock_t_res.scalars.return_value.first.return_value = mock_table

    mock_c_res = MagicMock()
    mock_c_res.scalars.return_value.first.return_value = mock_col

    mock_ds_res = MagicMock()
    mock_ds_res.scalars.return_value.first.return_value = None  # 回退从 error_sample 解析

    mock_db.execute.side_effect = [mock_alert_res, mock_t_res, mock_c_res, mock_ds_res]

    with patch.object(MetadataDriftService, "_try_sync_local_vector", new_callable=AsyncMock) as mock_sync_vec:
        res = await MetadataDriftService.resolve_alert(mock_db, alert_id=99, action="sync_type")

    assert res["column_updated"] is True
    assert alert.status == 1  # 已解决
    assert mock_col.type == "tinyint"
    assert "已成功将字段 staff_list.sid 类型从 String 同步为物理库实际类型 tinyint" in res["message"]
    mock_db.commit.assert_called_once()
    mock_sync_vec.assert_called_once_with({1})


