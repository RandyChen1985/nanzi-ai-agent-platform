"""Unit tests for MetadataDriftService."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.metadata import MetaColumn, MetaDataset, MetaSchemaDriftAlert, MetaTable
from app.services.metadata_drift_service import MetadataDriftService

pytestmark = pytest.mark.no_infrastructure


def _make_async_db_mock() -> AsyncMock:
    """构造与 SQLAlchemy ``AsyncSession`` 语义对齐的 mock。

    ``AsyncMock`` 会把所有属性都变成协程，但 ``AsyncSession.add()`` 是同步方法，
    ``begin_nested()`` 也是同步方法并返回异步上下文管理器。若不显式修正，被测代码中的
    ``db.add(...)`` 与 ``async with db.begin_nested():`` 会创建从未被 await 的协程，
    触发 ``RuntimeWarning: coroutine ... was never awaited``（进而表现为
    ``PytestUnraisableExceptionWarning``，并可能被错误归因到其它测试）。
    """
    db = AsyncMock()
    db.add = MagicMock()
    nested_ctx = MagicMock()
    nested_ctx.__aenter__ = AsyncMock(return_value=None)
    nested_ctx.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested = MagicMock(return_value=nested_ctx)
    return db


@pytest.mark.asyncio
async def test_record_drift_alert_creates_new_alert():
    """测试首次检出 Schema 漂移时，新增一条 pending 状态告警。"""
    mock_db = _make_async_db_mock()
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
    mock_db = _make_async_db_mock()

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
    mock_db = _make_async_db_mock()
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
    mock_db = _make_async_db_mock()
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
    mock_db = _make_async_db_mock()
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
    mock_db = _make_async_db_mock()

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
    # db.add 还会被 ChangelogService 用于写入变更日志，这里只断言恰好录入了一个 MetaColumn
    added_cols = [c.args[0] for c in mock_db.add.call_args_list if isinstance(c.args[0], MetaColumn)]
    assert len(added_cols) == 1
    added_col = added_cols[0]
    assert added_col.physical_name == "pue_ratio_v2"
    assert added_col.table_id == 101
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_batch_resolve_alerts():
    """测试批量处置告警。"""
    mock_db = _make_async_db_mock()
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
    mock_db = _make_async_db_mock()
    mock_res = MagicMock()
    mock_res.all.return_value = [(1, 3), (2, 1)]
    mock_db.execute.return_value = mock_res

    total, counts = await MetadataDriftService.get_drift_summary(mock_db)

    assert total == 4
    assert counts == {1: 3, 2: 1}


@pytest.mark.asyncio
async def test_get_all_drift_alerts():
    """测试查询跨数据集全局漂移告警列表，并附带 dataset_name。"""
    mock_db = _make_async_db_mock()
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
    mock_db = _make_async_db_mock()
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
    mock_db = _make_async_db_mock()
    alert1 = MetaSchemaDriftAlert(id=1, dataset_id=10, table_name="t1", column_name="c1", status=0)
    alert2 = MetaSchemaDriftAlert(id=2, dataset_id=10, table_name="t1", column_name="c2", status=0)

    mock_alerts_res = MagicMock()
    mock_alerts_res.scalars.return_value.all.return_value = [alert1, alert2]
    mock_db.execute.return_value = mock_alerts_res

    real_core = MetadataDriftService._resolve_single_alert_core

    async def flaky_core(db, alert, action, **kwargs):
        if alert.id == 2:
            raise ValueError(f"表 {alert.table_name} 未找到")
        return await real_core(db, alert, action, **kwargs)

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
    mock_db = _make_async_db_mock()
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
    mock_col = MetaColumn(id=50, table_id=10, physical_name="sid", type="String", term="员工编号")

    mock_alert_res = MagicMock()
    mock_alert_res.scalars.return_value.first.return_value = alert

    mock_t_res = MagicMock()
    mock_t_res.scalars.return_value.first.return_value = mock_table

    mock_c_res = MagicMock()
    mock_c_res.scalars.return_value.first.return_value = mock_col

    mock_db.execute.side_effect = [mock_alert_res, mock_t_res, mock_c_res]

    with patch.object(MetadataDriftService, "_try_sync_local_vector", new_callable=AsyncMock) as mock_sync_vec:
        res = await MetadataDriftService.resolve_alert(mock_db, alert_id=99, action="sync_type")

    assert res["column_updated"] is True
    assert mock_col.type == "Int64"
    assert alert.status == 1
    assert "已成功将字段 staff_list.sid 类型从 String 同步为物理库实际类型 Int64" in res["message"]
    mock_db.commit.assert_called_once()
    mock_sync_vec.assert_called_once_with({1})


def test_normalize_column_type_mapping():
    """测试将物理库各类细分数据类型统一归一化为平台 6 大通用标准类型。"""
    from app.services.metadata_drift_service import normalize_column_type

    # 1. String (包含容易被误判为整型的 longtext/longblob 及几何类型 point)
    for raw in ["varchar", "VARCHAR(255)", "varchar2", "text", "TINYTEXT", "longtext", "longblob", "point", "multipoint", "char(10)", "nvarchar(50)", "clob", "Nullable(String)", "unknown_type", ""]:
        assert normalize_column_type(raw) == "String", f"Failed on {raw}"

    # 2. Int64
    for raw in ["int", "INT(11) unsigned", "bigint", "TINYINT(1)", "smallint", "mediumint", "integer", "int4", "int8", "serial", "Nullable(Int64)", "number"]:
        assert normalize_column_type(raw) == "Int64", f"Failed on {raw}"

    # 3. Float64
    for raw in ["float", "double", "decimal(18,2)", "numeric(10,4)", "real", "binary_double", "number(10,2)", "Nullable(Float64)"]:
        assert normalize_column_type(raw) == "Float64", f"Failed on {raw}"

    # 4. DateTime
    for raw in ["date", "datetime", "timestamp", "timestamptz", "time", "Nullable(DateTime)"]:
        assert normalize_column_type(raw) == "DateTime", f"Failed on {raw}"

    # 5. Boolean
    for raw in ["bool", "boolean", "bit", "bit(1)"]:
        assert normalize_column_type(raw) == "Boolean", f"Failed on {raw}"

    # 6. JSON
    for raw in ["json", "jsonb", "object", "map", "array", "struct", "nested(a Int, b String)"]:
        assert normalize_column_type(raw) == "JSON", f"Failed on {raw}"


@pytest.mark.asyncio
async def test_resolve_alert_records_changelog():
    """测试巡检处置操作成功记录数据集变更日志 (Changelog) 并记录操作人。"""
    mock_db = _make_async_db_mock()
    alert = MetaSchemaDriftAlert(
        id=77,
        dataset_id=3,
        table_name="t_test",
        column_name="old_col",
        drift_type="missing_in_db",
        status=0,
    )
    mock_table = MetaTable(id=15, dataset_id=3, physical_name="t_test")
    mock_col = MetaColumn(id=99, table_id=15, physical_name="old_col", type="int", term="测试字段")

    mock_alert_res = MagicMock()
    mock_alert_res.scalars.return_value.first.return_value = alert
    mock_t_res = MagicMock()
    mock_t_res.scalars.return_value.first.return_value = mock_table
    mock_c_res = MagicMock()
    mock_c_res.scalars.return_value.first.return_value = mock_col

    mock_db.execute.side_effect = [mock_alert_res, mock_t_res, mock_c_res]

    with patch("app.services.changelog_service.ChangelogService.log_change", new_callable=AsyncMock) as mock_log:
        with patch.object(MetadataDriftService, "_try_sync_local_vector", new_callable=AsyncMock):
            res = await MetadataDriftService.resolve_alert(
                mock_db,
                alert_id=77,
                action="drop_column",
                user_id=1,
                user_name="admin",
            )

    assert res["column_dropped"] is True
    mock_log.assert_called_once()
    call_kwargs = mock_log.call_args.kwargs
    assert call_kwargs["resource_type"] == "table"
    assert call_kwargs["resource_id"] == "3:t_test"
    assert call_kwargs["operation"] == "update"
    assert call_kwargs["user_id"] == 1
    assert call_kwargs["user_name"] == "admin"
    assert "元数据巡检：下线物理缺失字段 t_test.old_col" in call_kwargs["reason"]


@pytest.mark.asyncio
async def test_resolve_alert_drop_table():
    """测试处置物理表缺失告警：从元数据中彻底下线整张表，关闭该表全部关联待处理告警并同步向量。"""
    mock_db = _make_async_db_mock()
    alert = MetaSchemaDriftAlert(
        id=88,
        dataset_id=1,
        table_name="dropped_orders",
        column_name="*",
        drift_type="table_missing_in_db",
        status=0,
        error_sample="巡检发现：物理数据库中已无此数据表 dropped_orders（整表缺失）",
    )
    mock_table = MetaTable(id=20, dataset_id=1, physical_name="dropped_orders")

    other_alert = MetaSchemaDriftAlert(
        id=89,
        dataset_id=1,
        table_name="dropped_orders",
        column_name="order_id",
        drift_type="missing_in_db",
        status=0,
    )

    mock_alert_res = MagicMock()
    mock_alert_res.scalars.return_value.first.return_value = alert

    mock_t_res = MagicMock()
    mock_t_res.scalars.return_value.first.return_value = mock_table

    mock_other_alerts_res = MagicMock()
    mock_other_alerts_res.scalars.return_value.all.return_value = [other_alert]

    mock_db.execute.side_effect = [mock_alert_res, mock_t_res, mock_other_alerts_res]

    with patch.object(MetadataDriftService, "_try_sync_local_vector", new_callable=AsyncMock) as mock_sync_vec:
        res = await MetadataDriftService.resolve_alert(mock_db, alert_id=88, action="drop_table")

    assert res["table_dropped"] is True
    assert alert.status == 1
    assert other_alert.status == 1  # 关联字段级告警也被标记为已处置
    assert "已成功从元数据中下线整表 dropped_orders" in res["message"]
    mock_db.delete.assert_called_once_with(mock_table)
    mock_db.commit.assert_called_once()
    mock_sync_vec.assert_called_once_with({1})


# --- AI 语义分析（新增字段可编辑收录）---

class _FakeLLMResponse:
    def __init__(self, raw: str):
        self.content = raw


@pytest.mark.asyncio
@patch(
    "app.services.ai.config.AgentConfigProvider.get_configured_llm",
    new_callable=AsyncMock,
)
async def test_analyze_new_column_ai_llm_success(mock_get_llm):
    """测试对新增字段发起 LLM 语义分析：成功返回建议的中文业务术语。"""
    mock_db = _make_async_db_mock()
    alert = MetaSchemaDriftAlert(
        id=50, dataset_id=3, table_name="device", column_name="billing_phone", status=0
    )
    mock_alert_res = MagicMock()
    mock_alert_res.scalars.return_value.first.return_value = alert

    ds = MetaDataset(id=3, name="设备域", data_source="")  # 空数据源：跳过物理/样例读取
    mock_ds_res = MagicMock()
    mock_ds_res.scalars.return_value.first.return_value = ds

    mock_db.execute.side_effect = [mock_alert_res, mock_ds_res]

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = _FakeLLMResponse(
        '```json\n{"term": "结算手机号", "description": "设备绑定的用于结算的手机号", "synonyms": ["billing_phone", "电话"]}\n```'
    )
    mock_get_llm.return_value = mock_llm

    result = await MetadataDriftService.analyze_new_column_ai(mock_db, 50)

    assert result["column_name"] == "billing_phone"
    assert result["llm_succeeded"] is True
    assert result["term"] == "结算手机号"
    assert "结算的手机号" in result["description"]
    assert result["synonyms"] == ["billing_phone", "电话"]


@pytest.mark.asyncio
@patch(
    "app.services.ai.config.AgentConfigProvider.get_configured_llm",
    new_callable=AsyncMock,
)
async def test_analyze_new_column_ai_llm_failure_falls_back(mock_get_llm):
    """测试 LLM 语义分析失败时不影响流程：返回 llm_succeeded=False 且带错误信息。"""
    mock_db = _make_async_db_mock()
    alert = MetaSchemaDriftAlert(
        id=51, dataset_id=3, table_name="device", column_name="billing_phone", status=0
    )
    mock_alert_res = MagicMock()
    mock_alert_res.scalars.return_value.first.return_value = alert

    ds = MetaDataset(id=3, name="设备域", data_source="")
    mock_ds_res = MagicMock()
    mock_ds_res.scalars.return_value.first.return_value = ds

    mock_db.execute.side_effect = [mock_alert_res, mock_ds_res]

    mock_llm = AsyncMock()
    mock_llm.ainvoke.side_effect = RuntimeError("LLM 超时")
    mock_get_llm.return_value = mock_llm

    result = await MetadataDriftService.analyze_new_column_ai(mock_db, 51)

    assert result["llm_succeeded"] is False
    assert result["term"] == ""
    assert "LLM 超时" in result["ai_error"]


@pytest.mark.asyncio
async def test_analyze_new_column_ai_alert_not_found():
    """测试分析一个不存在的告警时抛出 ValueError。"""
    mock_db = _make_async_db_mock()
    mock_alert_res = MagicMock()
    mock_alert_res.scalars.return_value.first.return_value = None
    mock_db.execute.return_value = mock_alert_res

    with pytest.raises(ValueError):
        await MetadataDriftService.analyze_new_column_ai(mock_db, 9999)


@pytest.mark.asyncio
async def test_resolve_alert_add_column_with_confirmed_term():
    """测试 add_column 时传管理员确认的中文术语，优先级高于物理注释/英文兜底。"""
    mock_db = _make_async_db_mock()

    alert = MetaSchemaDriftAlert(
        id=60,
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

    # 查 dataset 关联（无数据源，跳过物理读取）
    mock_ds_res = MagicMock()
    mock_ds_res.scalars.return_value.first.return_value = None

    mock_db.execute.side_effect = [mock_alert_res, mock_table_res, mock_col_res, mock_ds_res]

    res = await MetadataDriftService.resolve_alert(
        mock_db,
        alert_id=60,
        action="add_column",
        column_term="PUE 能效比",
        column_description="机房 PUE 能效比指标",
        column_synonyms=["pue", "能效比"],
    )

    assert res["status"] == 1
    assert res["column_added"] is True
    # 只取 MetaColumn（db.add 还会被 ChangelogService 用于写入变更日志）
    added_cols = [c.args[0] for c in mock_db.add.call_args_list if isinstance(c.args[0], MetaColumn)]
    assert len(added_cols) == 1
    added_col = added_cols[0]
    assert added_col.term == "PUE 能效比"
    assert added_col.description == "机房 PUE 能效比指标"
    assert added_col.synonyms == ["pue", "能效比"]
    mock_db.commit.assert_called_once()


# --- 备注缺失字段（missing_comment）AI 补充分析 + update_comment 处置 ---

@pytest.mark.asyncio
async def test_analyze_update_comment_ai_physical_comment_preferred():
    """物理库已有有效注释时，直接采用且不调用 LLM（from_source='physical'）。"""
    mock_db = _make_async_db_mock()
    alert = MetaSchemaDriftAlert(
        id=70, dataset_id=3, table_name="device", column_name="billing_phone", status=0
    )
    mock_alert_res = MagicMock()
    mock_alert_res.scalars.return_value.first.return_value = alert

    ds = MetaDataset(id=3, name="设备域", data_source="mysql")
    mock_ds_res = MagicMock()
    mock_ds_res.scalars.return_value.first.return_value = ds

    table = MetaTable(id=7, dataset_id=3, physical_name="device")
    mock_table_res = MagicMock()
    mock_table_res.scalars.return_value.first.return_value = table

    mock_db.execute.side_effect = [mock_alert_res, mock_ds_res, mock_table_res]

    mock_adapter = AsyncMock()
    mock_adapter.get_columns.return_value = [
        {"name": "billing_phone", "type": "varchar", "comment": "结算手机号"},
        {"name": "device_no", "type": "varchar", "comment": "设备编号"},
    ]
    with patch("app.services.data_adapter.factory.get_adapter", new_callable=AsyncMock) as mock_get_adapter, \
         patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", new_callable=AsyncMock) as mock_get_llm:
        mock_get_adapter.return_value = mock_adapter
        result = await MetadataDriftService.analyze_update_comment_ai(mock_db, 70)
        # 物理注释优先：直接返回，连 LLM 都不需要初始化
        mock_get_llm.assert_not_called()

    assert result["from_source"] == "physical"
    assert result["description"] == "结算手机号"
    assert result["llm_succeeded"] is False
    assert result["current_term"] is None


@pytest.mark.asyncio
@patch(
    "app.services.ai.config.AgentConfigProvider.get_configured_llm",
    new_callable=AsyncMock,
)
async def test_analyze_update_comment_ai_llm_success(mock_get_llm):
    """物理库无有效注释时，LLM 用语生成中文业务描述并保留现有 term。"""
    mock_db = _make_async_db_mock()
    alert = MetaSchemaDriftAlert(
        id=71, dataset_id=3, table_name="device", column_name="billing_phone", status=0
    )
    mock_alert_res = MagicMock()
    mock_alert_res.scalars.return_value.first.return_value = alert

    ds = MetaDataset(id=3, name="设备域", data_source="")  # 空数据源：跳过物理/样例读取
    mock_ds_res = MagicMock()
    mock_ds_res.scalars.return_value.first.return_value = ds

    col = MetaColumn(
        id=500, table_id=7, physical_name="billing_phone",
        term="结算手机号", description="", type="varchar",
    )
    table = MetaTable(id=7, dataset_id=3, physical_name="device", columns=[col])
    mock_table_res = MagicMock()
    mock_table_res.scalars.return_value.first.return_value = table

    mock_db.execute.side_effect = [mock_alert_res, mock_ds_res, mock_table_res]

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = _FakeLLMResponse(
        '{"description": "设备绑定的用于账务结算的手机号码", "synonyms": ["billing_phone", "电话"]}'
    )
    mock_get_llm.return_value = mock_llm

    result = await MetadataDriftService.analyze_update_comment_ai(mock_db, 71)

    assert result["from_source"] == "ai"
    assert result["llm_succeeded"] is True
    assert result["description"] == "设备绑定的用于账务结算的手机号码"
    assert result["synonyms"] == ["billing_phone", "电话"]
    assert result["current_term"] == "结算手机号"


@pytest.mark.asyncio
async def test_analyze_update_comment_ai_alert_not_found():
    """分析一个不存在的告警时抛出 ValueError。"""
    mock_db = _make_async_db_mock()
    mock_alert_res = MagicMock()
    mock_alert_res.scalars.return_value.first.return_value = None
    mock_db.execute.return_value = mock_alert_res

    with pytest.raises(ValueError):
        await MetadataDriftService.analyze_update_comment_ai(mock_db, 9999)


@pytest.mark.asyncio
async def test_resolve_alert_update_comment_with_confirmed_desc():
    """update_comment 处置：用管理员确认的描述更新字段备注，且保留现有业务术语。"""
    mock_db = _make_async_db_mock()

    alert = MetaSchemaDriftAlert(
        id=72, dataset_id=10, table_name="device_pue", column_name="pue_ratio", status=0
    )
    mock_alert_res = MagicMock()
    mock_alert_res.scalars.return_value.first.return_value = alert

    table = MetaTable(id=101, dataset_id=10, physical_name="device_pue")
    mock_table_res = MagicMock()
    mock_table_res.scalars.return_value.first.return_value = table

    col = MetaColumn(
        id=300, table_id=101, physical_name="pue_ratio",
        term="PUE 能效比", description="", type="decimal",
    )
    mock_col_res = MagicMock()
    mock_col_res.scalars.return_value.first.return_value = col

    mock_db.execute.side_effect = [mock_alert_res, mock_table_res, mock_col_res]

    res = await MetadataDriftService.resolve_alert(
        mock_db,
        alert_id=72,
        action="update_comment",
        column_description="机房 PUE 能效比指标",
        column_synonyms=["pue", "能效比"],
    )

    assert res["status"] == 1
    assert res["column_updated"] is True
    assert col.term == "PUE 能效比"  # 保留现有业务术语
    assert col.description == "机房 PUE 能效比指标"
    assert col.synonyms == ["pue", "能效比"]
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
@patch(
    "app.services.ai.config.AgentConfigProvider.get_configured_llm",
    new_callable=AsyncMock,
)
async def test_batch_resolve_alerts_add_column_auto_ai(mock_get_llm):
    """测试批量录入新增字段时，物理注释优先采纳，无注释字段自动并发调用 LLM 补全。"""
    mock_db = _make_async_db_mock()

    alert1 = MetaSchemaDriftAlert(
        id=801, dataset_id=1, table_name="sys_log", column_name="created_at", status=0
    )
    alert2 = MetaSchemaDriftAlert(
        id=802, dataset_id=1, table_name="sys_log", column_name="operator_id", status=0
    )

    ds = MetaDataset(id=1, name="系统日志域", data_source="")
    col_existing = MetaColumn(physical_name="id", term="主键编号", description="自增编号")
    table = MetaTable(id=10, dataset_id=1, physical_name="sys_log", columns=[col_existing])

    # 模拟数据库查询分流，杜绝脆弱的固定顺序列表导致的错位崩溃 (C3)
    async def fake_execute(stmt, *args, **kwargs):
        s_str = str(stmt).lower()
        res = MagicMock()
        if "meta_schema_drift_alerts" in s_str:
            res.scalars.return_value.all.return_value = [alert1, alert2]
            res.scalars.return_value.first.return_value = alert1
        elif "meta_tables" in s_str:
            res.scalars.return_value.first.return_value = table
            res.scalars.return_value.all.return_value = [table]
        elif "meta_columns" in s_str:
            res.scalars.return_value.first.return_value = None
            res.scalars.return_value.all.return_value = []
        elif "meta_datasets" in s_str:
            res.scalars.return_value.first.return_value = ds
            res.scalars.return_value.all.return_value = [ds]
        else:
            res.scalars.return_value.first.return_value = None
            res.scalars.return_value.all.return_value = []
        return res

    mock_db.execute.side_effect = fake_execute

    # mock 物理列注释：alert1 有注释，alert2 无注释
    async def fake_fetch_comment(db, ds_id, tbl, col):
        if col == "created_at":
            return "创建时间"
        return ""

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = _FakeLLMResponse(
        '{"term": "操作人账号", "description": "执行该操作的管理人员账号", "synonyms": ["operator"]}'
    )
    mock_get_llm.return_value = mock_llm

    with patch.object(MetadataDriftService, "_fetch_physical_comment", side_effect=fake_fetch_comment):
        with patch("app.services.changelog_service.ChangelogService.log_change", new_callable=AsyncMock):
            with patch("app.services.metadata_service.MetadataService._mark_dataset_as_modified", new_callable=AsyncMock):
                with patch.object(MetadataDriftService, "_try_sync_local_vector", new_callable=AsyncMock):
                    res = await MetadataDriftService.batch_resolve_alerts(
                        mock_db,
                        dataset_id=1,
                        action="add_column",
                        alert_ids=[801, 802],
                        auto_ai_complete=True,
                    )

    assert res["processed_count"] == 2
    assert res["failed_count"] == 0
    assert res["ai_completed_count"] == 1
    assert res["physical_completed_count"] == 1
    assert "1 项由 AI 自动生成中文名与描述" in res["message"]
    assert "1 项采用物理库注释" in res["message"]


@pytest.mark.asyncio
async def test_record_drift_alert_core_dedup_distinguishes_drift_type():
    """测试 record_drift_alert_core 在去重时区分 drift_type (I1)。

    同一表同字段若分别触发 type_mismatch 与 missing_comment，应分别生成告警，不可互相吞没。
    """
    mock_db = _make_async_db_mock()
    # 模拟第一次检查不存在，添加 alert
    mock_res_empty = MagicMock()
    mock_res_empty.scalars.return_value.first.return_value = None

    mock_db.execute.return_value = mock_res_empty

    alert = await MetadataDriftService.record_drift_alert_core(
        mock_db,
        dataset_id=1,
        table_name="orders",
        column_name="status",
        drift_type="missing_comment",
        source="manual_inspection",
    )

    assert alert is not None
    assert alert.drift_type == "missing_comment"
    assert alert.status == 0

    # 关键：去重查询必须把 drift_type 纳入 WHERE，否则 type_mismatch 会吞没 missing_comment。
    # 仅断言返回对象的 drift_type 无法证明这一点，必须检查实际下发的查询语句。
    dedup_stmt = mock_db.execute.call_args_list[0].args[0]
    compiled = str(dedup_stmt)
    assert "drift_type" in compiled
    assert "meta_schema_drift_alerts" in compiled


@pytest.mark.asyncio
async def test_resolve_alert_update_comment_skips_when_no_change():
    """测试 update_comment 在物理库无注释且未填写新备注时跳过处置，不产生虚假已解决 (I3)。"""
    mock_db = _make_async_db_mock()

    alert = MetaSchemaDriftAlert(
        id=73, dataset_id=10, table_name="device_pue", column_name="pue_ratio", status=0
    )
    mock_alert_res = MagicMock()
    mock_alert_res.scalars.return_value.first.return_value = alert

    table = MetaTable(id=101, dataset_id=10, physical_name="device_pue")
    mock_table_res = MagicMock()
    mock_table_res.scalars.return_value.first.return_value = table

    col = MetaColumn(
        id=300, table_id=101, physical_name="pue_ratio",
        term="PUE 能效比", description="", type="decimal", synonyms=None,
    )
    mock_col_res = MagicMock()
    mock_col_res.scalars.return_value.first.return_value = col

    mock_db.execute.side_effect = [mock_alert_res, mock_table_res, mock_col_res]

    # 模拟物理库也无有效注释
    with patch.object(MetadataDriftService, "_fetch_physical_comment", return_value=""):
        with patch.object(MetadataDriftService, "_try_sync_local_vector", new_callable=AsyncMock) as mock_sync_vec:
            res = await MetadataDriftService.resolve_alert(
                mock_db,
                alert_id=73,
                action="update_comment",
                column_description="",  # 管理员也未提供新描述
                column_synonyms=None,
            )

    assert res["column_updated"] is False
    assert alert.status == 0  # 依然保持未解决状态
    assert "跳过处置" in res["message"]
    mock_sync_vec.assert_not_called()  # 杜绝白跑向量重同步




def test_action_drift_type_map_guards_cross_type_actions():
    """C1: 处置动作必须与漂移类型匹配；未知/空类型放行以兼容历史数据。"""
    from app.services.metadata_drift_service import _is_action_allowed

    assert _is_action_allowed("table_missing_in_db", "drop_table") is True
    assert _is_action_allowed("table_missing_in_db", "add_column") is False
    assert _is_action_allowed("missing_in_db", "drop_column") is True
    assert _is_action_allowed("missing_in_db", "add_column") is False
    assert _is_action_allowed("new_in_db", "add_column") is True
    assert _is_action_allowed("type_mismatch", "sync_type") is True
    assert _is_action_allowed("missing_comment", "update_comment") is True
    # ignore 永远允许
    assert _is_action_allowed("table_missing_in_db", "ignore") is True
    # 未知/空漂移类型向后兼容放行
    assert _is_action_allowed(None, "add_column") is True
    assert _is_action_allowed("legacy_type", "add_column") is True


@pytest.mark.asyncio
async def test_resolve_alert_rejects_add_column_on_table_missing_alert():
    """C1: 对整表缺失告警执行 add_column 必须被拒绝，不得写入 physical_name='*' 的垃圾字段。"""
    mock_db = _make_async_db_mock()
    alert = MetaSchemaDriftAlert(
        id=900, dataset_id=1, table_name="gone_table", column_name="*",
        drift_type="table_missing_in_db", status=0,
    )
    mock_alert_res = MagicMock()
    mock_alert_res.scalars.return_value.first.return_value = alert
    mock_db.execute.side_effect = [mock_alert_res]

    with pytest.raises(ValueError, match="不支持处置动作"):
        await MetadataDriftService.resolve_alert(mock_db, 900, action="add_column")

    assert alert.status == 0  # 未被误标记为已解决
    mock_db.add.assert_not_called()  # 未写入任何 MetaColumn
    mock_db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_batch_resolve_skips_alerts_with_mismatched_action():
    """C1: 批量 add_column 遇到非 new_in_db 告警应跳过（计入 skipped_count），不误处置。"""
    mock_db = _make_async_db_mock()

    new_alert = MetaSchemaDriftAlert(
        id=901, dataset_id=1, table_name="device", column_name="billing_phone",
        drift_type="new_in_db", status=0,
    )
    missing_alert = MetaSchemaDriftAlert(
        id=902, dataset_id=1, table_name="gone_table", column_name="*",
        drift_type="table_missing_in_db", status=0,
    )

    mock_alerts_res = MagicMock()
    mock_alerts_res.scalars.return_value.all.return_value = [new_alert, missing_alert]

    table = MetaTable(id=101, dataset_id=1, physical_name="device")
    mock_table_res = MagicMock()
    mock_table_res.scalars.return_value.first.return_value = table

    mock_col_res = MagicMock()
    mock_col_res.scalars.return_value.first.return_value = None

    # 第 1 次为告警列表查询，第 2 次为元数据表查询，其后（列查询等）均返回「列不存在」
    _call_seq = {"n": 0}

    def _fake_execute(_stmt):
        _call_seq["n"] += 1
        if _call_seq["n"] == 1:
            return mock_alerts_res
        if _call_seq["n"] == 2:
            return mock_table_res
        return mock_col_res

    mock_db.execute.side_effect = _fake_execute

    with patch.object(MetadataDriftService, "_prepare_add_column_ai_metadata", new_callable=AsyncMock) as mock_prep, \
         patch.object(MetadataDriftService, "_try_sync_local_vector", new_callable=AsyncMock):
        mock_prep.return_value = {}
        res = await MetadataDriftService.batch_resolve_alerts(
            mock_db, dataset_id=1, action="add_column"
        )

    # 仅 new_in_db 告警被处置，整表缺失告警被跳过
    assert res["skipped_count"] == 1
    assert res["processed_count"] == 1
    assert missing_alert.status == 0
    # 预计算 AI 元数据时也应只传入可处置的告警
    assert [a.id for a in mock_prep.call_args[0][1]] == [901]


def test_quote_identifier_blocks_injection_but_allows_unicode():
    """采样 SQL 标识符安全引用：拦截可突破引号边界的名称，放行中文等合法名称。"""
    from app.services.metadata_drift_service import build_sample_sql, quote_identifier

    assert quote_identifier("device_pue") == '"device_pue"'
    assert quote_identifier("订单号") == '"订单号"'
    assert quote_identifier("AMT$") == '"AMT$"'
    # 注入 payload 与空值一律拒绝（调用方降级为无采样）
    assert quote_identifier('a" FROM "users" --') is None
    assert quote_identifier("a;DROP TABLE t") is None
    assert quote_identifier("a`b") is None
    assert quote_identifier("") is None
    assert quote_identifier(None) is None

    assert build_sample_sql("mysql", "订单号", "device_pue") == 'SELECT "订单号" FROM "device_pue" LIMIT 3'
    assert build_sample_sql("oracle", "id", "t") == 'SELECT "id" FROM "t" WHERE ROWNUM <= 3'
    assert build_sample_sql("tsql", "id", "t") == 'SELECT TOP 3 "id" FROM "t"'
    assert build_sample_sql("mysql", 'bad"name', "t") is None
