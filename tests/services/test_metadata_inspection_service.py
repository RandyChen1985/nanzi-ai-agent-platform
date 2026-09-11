"""Unit tests for MetadataInspectionService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.metadata import MetaColumn, MetaDataset, MetaTable
from app.services.metadata_inspection_service import MetadataInspectionService, _normalize_col_type

pytestmark = pytest.mark.no_infrastructure


def test_normalize_col_type_same_family_not_mismatch():
    """同大类不同写法不应视为不一致：date vs datetime、Int64 vs int、varchar vs text。"""
    assert _normalize_col_type("date") == _normalize_col_type("datetime")
    assert _normalize_col_type("date") == _normalize_col_type("timestamp without time zone")
    assert _normalize_col_type("Int64") == _normalize_col_type("int")
    assert _normalize_col_type("UInt128") == _normalize_col_type("bigint")
    assert _normalize_col_type("Integer") == _normalize_col_type("int")
    assert _normalize_col_type("varchar(50)") == _normalize_col_type("text")
    assert _normalize_col_type("character varying") == _normalize_col_type("varchar")
    assert _normalize_col_type("bigint(20) unsigned") == _normalize_col_type("bigint")
    assert _normalize_col_type("decimal(18,4)") == _normalize_col_type("double precision")
    assert _normalize_col_type("Timestamp") == _normalize_col_type("datetime")


def test_normalize_col_type_cross_family_mismatch():
    """跨大类才视为不一致：text vs date、int vs varchar。"""
    assert _normalize_col_type("text") != _normalize_col_type("date")
    assert _normalize_col_type("int") != _normalize_col_type("varchar")
    assert _normalize_col_type("bigint") != _normalize_col_type("text")
    assert _normalize_col_type("bool") != _normalize_col_type("int")


@pytest.mark.asyncio
async def test_inspect_dataset_detects_missing_and_new_columns():
    """测试巡检引擎精准识别物理缺失列与物理新增列，并向 Redis Stream 推流与记录告警。"""
    mock_db = AsyncMock()

    # 构造数据集：纳管 1 张表 device_pue，元数据中声明了 3 列 (id, region, cpu_power_old)
    col1 = MetaColumn(physical_name="id")
    col2 = MetaColumn(physical_name="region")
    col3 = MetaColumn(physical_name="cpu_power_old")
    table = MetaTable(physical_name="device_pue", columns=[col1, col2, col3])
    dataset = MetaDataset(id=1, name="pue_dataset", data_source="mysql_pue", tables=[table])

    mock_adapter = AsyncMock()
    # 物理数据库实际返回的字段：id, region, cpu_power_new（注意：cpu_power_old 没了，多了 cpu_power_new）
    mock_adapter.get_columns.return_value = [
        {"name": "id", "type": "int"},
        {"name": "region", "type": "varchar"},
        {"name": "cpu_power_new", "type": "decimal"},
    ]

    with patch("app.services.metadata_service.MetadataService.get_dataset_by_id", new_callable=AsyncMock) as mock_get_ds, \
         patch("app.services.metadata_inspection_service.get_adapter", new_callable=AsyncMock) as mock_get_adapter, \
         patch("app.services.metadata_sync_log_service.metadata_sync_log_service.publish", new_callable=AsyncMock) as mock_publish, \
         patch("app.services.metadata_drift_service.MetadataDriftService.record_drift_alert_core", new_callable=AsyncMock) as mock_record_alert:

        mock_get_ds.return_value = dataset
        mock_get_adapter.return_value = mock_adapter

        res = await MetadataInspectionService.inspect_dataset(mock_db, dataset_id=1, task_id="task_test_123")

        assert res["success"] is True
        assert res["tables_scanned"] == 1
        assert res["stale_count"] == 1  # cpu_power_old 物理缺失
        assert res["new_count"] == 1    # cpu_power_new 物理新增

        # 验证调用了 record_drift_alert_core 记录两次告警
        assert mock_record_alert.call_count == 2
        # 验证推流终态为 completed
        completed_calls = [
            c for c in mock_publish.call_args_list if c.kwargs.get("event") == "completed"
        ]
        assert len(completed_calls) == 1
        assert completed_calls[0].kwargs["progress"] == 100
        mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_inspect_dataset_all_matching():
    """测试物理结构完全一致时，0 差异并推送成功报告。"""
    mock_db = AsyncMock()
    col1 = MetaColumn(physical_name="id")
    col2 = MetaColumn(physical_name="name")
    table = MetaTable(physical_name="users", columns=[col1, col2])
    dataset = MetaDataset(id=2, name="user_dataset", data_source="mysql_main", tables=[table])

    mock_adapter = AsyncMock()
    mock_adapter.get_columns.return_value = [
        {"name": "id", "type": "int"},
        {"name": "name", "type": "varchar"},
    ]

    with patch("app.services.metadata_service.MetadataService.get_dataset_by_id", new_callable=AsyncMock) as mock_get_ds, \
         patch("app.services.metadata_inspection_service.get_adapter", new_callable=AsyncMock) as mock_get_adapter, \
         patch("app.services.metadata_sync_log_service.metadata_sync_log_service.publish", new_callable=AsyncMock) as mock_publish:

        mock_get_ds.return_value = dataset
        mock_get_adapter.return_value = mock_adapter

        res = await MetadataInspectionService.inspect_dataset(mock_db, dataset_id=2, task_id="task_test_match")

        assert res["success"] is True
        assert res["stale_count"] == 0
        assert res["new_count"] == 0
        assert "完全一致" in mock_publish.call_args_list[-1].kwargs["message"]


@pytest.mark.asyncio
async def test_inspect_dataset_adapter_error():
    """测试数据源连接失败时，优雅推流 failed 并返回错误信息。"""
    mock_db = AsyncMock()
    dataset = MetaDataset(id=3, name="err_dataset", data_source="unreachable_ds", tables=[])

    with patch("app.services.metadata_service.MetadataService.get_dataset_by_id", new_callable=AsyncMock) as mock_get_ds, \
         patch("app.services.metadata_inspection_service.get_adapter", side_effect=RuntimeError("Connection refused")), \
         patch("app.services.metadata_sync_log_service.metadata_sync_log_service.publish", new_callable=AsyncMock) as mock_publish:

        mock_get_ds.return_value = dataset

        res = await MetadataInspectionService.inspect_dataset(mock_db, dataset_id=3, task_id="task_test_err")

        assert res["success"] is False
        assert "Connection refused" in res["error"]
        failed_calls = [
            c for c in mock_publish.call_args_list if c.kwargs.get("event") == "failed"
        ]
        assert len(failed_calls) == 1


@pytest.mark.asyncio
async def test_inspect_dataset_type_mismatch():
    """测试巡检发现字段类型不一致时，记录 type_mismatch 告警并正确统计。"""
    mock_db = AsyncMock()
    col1 = MetaColumn(physical_name="id", type="bigint")
    col2 = MetaColumn(physical_name="status", type="int")
    table = MetaTable(physical_name="orders", columns=[col1, col2])
    dataset = MetaDataset(id=4, name="order_dataset", data_source="mysql_main", tables=[table])

    mock_adapter = AsyncMock()
    mock_adapter.get_columns.return_value = [
        {"name": "id", "type": "bigint(20)"},          # 归一化为 integer，匹配
        {"name": "status", "type": "varchar(20)"},     # 归一化为 string，与 int 不匹配！
    ]

    with patch("app.services.metadata_service.MetadataService.get_dataset_by_id", new_callable=AsyncMock) as mock_get_ds, \
         patch("app.services.metadata_inspection_service.get_adapter", new_callable=AsyncMock) as mock_get_adapter, \
         patch("app.services.metadata_drift_service.MetadataDriftService.record_drift_alert_core", new_callable=AsyncMock) as mock_record_drift, \
         patch("app.services.metadata_sync_log_service.metadata_sync_log_service.publish", new_callable=AsyncMock) as mock_publish:

        mock_get_ds.return_value = dataset
        mock_get_adapter.return_value = mock_adapter

        res = await MetadataInspectionService.inspect_dataset(mock_db, dataset_id=4, task_id="task_test_mismatch")

        assert res["success"] is True
        assert res["stale_count"] == 0
        assert res["new_count"] == 0
        assert res["mismatch_count"] == 1

        mock_record_drift.assert_called_once()
        call_kwargs = mock_record_drift.call_args.kwargs
        assert call_kwargs["column_name"] == "status"
        assert call_kwargs["drift_type"] == "type_mismatch"
        assert "元数据声明为 int，物理库实际为 varchar(20)" in call_kwargs["error_sample"]


@pytest.mark.asyncio
async def test_inspect_all_datasets_full_flow():
    """测试 inspect_all_datasets 完整批量巡检聚合流程与报告生成。"""
    mock_db = AsyncMock()

    col1 = MetaColumn(physical_name="id", type="bigint")
    col2 = MetaColumn(physical_name="price", type="decimal")
    table = MetaTable(physical_name="products", columns=[col1, col2])
    dataset = MetaDataset(id=5, name="product_dataset", data_source="pg_main", tables=[table], status=1)

    from unittest.mock import MagicMock
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [dataset]
    mock_db.execute.return_value = mock_result

    mock_adapter = AsyncMock()
    mock_adapter.get_columns.return_value = [
        {"name": "id", "type": "bigint"},
        {"name": "price", "type": "varchar(50)"},  # mismatch!
        {"name": "extra_col", "type": "text"},     # new_in_db!
    ]

    with patch("app.services.metadata_inspection_service.get_adapter", new_callable=AsyncMock) as mock_get_adapter, \
         patch("app.services.metadata_service.MetadataService.get_dataset_by_id", new_callable=AsyncMock) as mock_get_ds, \
         patch("app.services.metadata_drift_service.MetadataDriftService.record_drift_alert_core", new_callable=AsyncMock), \
         patch("app.services.metadata_sync_log_service.metadata_sync_log_service.publish", new_callable=AsyncMock) as mock_publish:

        mock_get_adapter.return_value = mock_adapter
        mock_get_ds.return_value = dataset

        res = await MetadataInspectionService.inspect_all_datasets(mock_db, task_id="task_all_test")

        assert res["success"] is True
        assert res["datasets_scanned"] == 1
        assert res["tables_scanned"] == 1
        assert res["new_count"] == 1
        assert res["mismatch_count"] == 1
        assert res["drift_datasets_count"] == 1
        assert res["failed_datasets_count"] == 0

        # 验证最终 completed 事件汇总消息中包含了各统计项
        last_publish = mock_publish.call_args_list[-1].kwargs
        assert last_publish["event"] == "completed"
        assert "1 处物理新增" in last_publish["message"]
        assert "1 处类型不一致" in last_publish["message"]


@pytest.mark.asyncio
async def test_inspect_all_datasets_skips_disabled_datasets():
    """测试 active_only=True 模式下仅查询和扫描开启状态 (status == 1) 的数据集，排除禁用数据集。"""
    mock_db = AsyncMock()

    col1 = MetaColumn(physical_name="id", type="int")
    t1 = MetaTable(physical_name="active_table", columns=[col1])
    active_ds = MetaDataset(id=10, name="active_dataset", data_source="mysql_active", tables=[t1], status=1)

    from unittest.mock import MagicMock
    mock_result = MagicMock()
    # 模拟 SQL 过滤 where status == 1 只返回 active_ds
    mock_result.scalars.return_value.all.return_value = [active_ds]
    mock_db.execute.return_value = mock_result

    mock_adapter = AsyncMock()
    mock_adapter.get_columns.return_value = [{"name": "id", "type": "int"}]

    with patch("app.services.metadata_inspection_service.get_adapter", new_callable=AsyncMock) as mock_get_adapter, \
         patch("app.services.metadata_service.MetadataService.get_dataset_by_id", new_callable=AsyncMock) as mock_get_ds, \
         patch("app.services.metadata_sync_log_service.metadata_sync_log_service.publish", new_callable=AsyncMock):

        mock_get_adapter.return_value = mock_adapter
        mock_get_ds.return_value = active_ds

        res = await MetadataInspectionService.inspect_all_datasets(mock_db, task_id="task_active_test", active_only=True)

        assert res["success"] is True
        assert res["datasets_scanned"] == 1

        # 检查 executed 的 SQL 语句中包含 status == 1 条件
        executed_stmt = mock_db.execute.call_args[0][0]
        # 提取 where 条件
        assert "status = :status_1" in str(executed_stmt) or "status" in str(executed_stmt)



