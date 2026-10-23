from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.data_adapter.factory import get_adapter
from app.services.data_adapter.postgresql import (
    PostgreSQLAdapter,
    normalize_postgresql_identifiers,
    normalize_postgresql_sql,
)
from app.services.db_import_service import DBImportService
from app.services.db_profile_service import DbProfileService
from app.services.pool_manager import DataSourcePoolManager
from app.services.sql_query_execution_service import dialect_from_data_source


pytestmark = pytest.mark.no_infrastructure


def test_dialect_from_data_source_postgresql_aliases():
    assert dialect_from_data_source("postgresql_demo") == "postgres"
    assert dialect_from_data_source("pg_reporting") == "postgres"


@pytest.mark.no_infrastructure
@pytest.mark.parametrize(
    ("sql_text", "expected"),
    [
        (
            "SELECT * FROM `public.ny_function`",
            'SELECT * FROM "public"."ny_function"',
        ),
        (
            "SELECT * FROM `public`.`ny_function`",
            'SELECT * FROM "public"."ny_function"',
        ),
        (
            "SELECT '`public.ny_function`' AS literal -- `keep`",
            "SELECT '`public.ny_function`' AS literal -- `keep`",
        ),
        (
            "SELECT $$`public.ny_function`$$ AS function_text",
            "SELECT $$`public.ny_function`$$ AS function_text",
        ),
    ],
)
def test_normalize_postgresql_identifiers_only_converts_identifier_backticks(sql_text, expected):
    assert normalize_postgresql_identifiers(sql_text) == expected


@pytest.mark.parametrize(
    ("sql_text", "expected"),
    [
        (
            "SELECT toDate(parseDateTimeBestEffort(create_time)) AS created_day FROM `public`.`orders`",
            'SELECT CAST(CAST(create_time AS TIMESTAMP) AS DATE) AS created_day FROM "public"."orders"',
        ),
        (
            "SELECT toYYYYMM(event_time) AS month_key FROM orders",
            "SELECT ((CAST(EXTRACT(YEAR FROM event_time) AS INTEGER) * 100) + CAST(EXTRACT(MONTH FROM event_time) AS INTEGER)) AS month_key FROM orders",
        ),
        (
            "SELECT toStartOfMonth(event_time), dateDiff('day', start_time, end_time) FROM orders",
            "SELECT DATE_TRUNC('month', event_time), CAST((end_time)::date - (start_time)::date AS BIGINT) FROM orders",
        ),
        (
            "SELECT toYear(event_time), toMonth(event_time), toDayOfWeek(event_time), toYYYYMMDD(event_time) FROM orders",
            "SELECT CAST(EXTRACT(YEAR FROM event_time) AS INTEGER), CAST(EXTRACT(MONTH FROM event_time) AS INTEGER), CAST(EXTRACT(ISODOW FROM event_time) AS INTEGER), ((CAST(EXTRACT(YEAR FROM event_time) AS INTEGER) * 10000) + (CAST(EXTRACT(MONTH FROM event_time) AS INTEGER) * 100) + CAST(EXTRACT(DAY FROM event_time) AS INTEGER)) FROM orders",
        ),
        (
            "SELECT 'toDate(event_time)' AS literal -- toDate(other_time)",
            "SELECT 'toDate(event_time)' AS literal -- toDate(other_time)",
        ),
        (
            "SELECT toDateOrNull(raw_date), parseDateTimeBestEffortOrNull(raw_time) FROM orders",
            "SELECT CASE WHEN pg_input_is_valid(CAST(raw_date AS TEXT), 'date') THEN CAST(raw_date AS DATE) ELSE NULL END, CASE WHEN pg_input_is_valid(CAST(raw_time AS TEXT), 'timestamp') THEN CAST(raw_time AS TIMESTAMP) ELSE NULL END FROM orders",
        ),
        (
            "SELECT dateDiff('hour', start_date, end_date) FROM orders",
            "SELECT CAST(EXTRACT(EPOCH FROM ((end_date)::timestamp - (start_date)::timestamp)) / 3600 AS BIGINT) FROM orders",
        ),
        (
            "SELECT formatDateTime(event_time, '%F %T %M') FROM orders",
            "SELECT TO_CHAR(event_time, 'YYYY-MM-DD HH24:MI:SS MI') FROM orders",
        ),
        (
            "SELECT toStartOfWeek(event_time), toStartOfWeek(event_time, 1) FROM orders",
            "SELECT DATE_TRUNC('week', event_time) - INTERVAL '1 day', DATE_TRUNC('week', event_time) FROM orders",
        ),
    ],
)
def test_normalize_postgresql_sql_converts_clickhouse_functions_without_touching_literals(
    sql_text, expected
):
    """验证历史指标中的 ClickHouse 日期函数可转换为 PostgreSQL，字符串和注释保持不变。"""
    assert normalize_postgresql_sql(sql_text) == expected


@pytest.mark.asyncio
async def test_factory_returns_postgresql_adapter():
    db_config = SimpleNamespace(id=9, name="postgresql_demo", db_type="postgresql")

    with patch("app.core.orm.AsyncSessionLocal") as mock_session_local, \
         patch(
             "app.services.db_connection_service.DbConnectionService.get_config_by_name",
             new_callable=AsyncMock,
         ) as mock_get:
        mock_get.return_value = db_config
        mock_session_local.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session_local.return_value.__aexit__ = AsyncMock(return_value=False)

        adapter = await get_adapter("postgresql_demo")

    assert isinstance(adapter, PostgreSQLAdapter)
    assert adapter.source_id == 9


@pytest.mark.asyncio
async def test_pool_manager_routes_postgresql():
    DataSourcePoolManager._pools.clear()
    config = SimpleNamespace(
        id=11,
        name="postgresql_demo",
        db_type="postgresql",
        host="127.0.0.1",
        port=5432,
        db_user="postgres",
        password="secret",
        database_name="nanzi_demo",
    )
    mock_pool = MagicMock()

    with patch(
        "app.services.db_connection_service.DbConnectionService.get_config",
        new_callable=AsyncMock,
        return_value=config,
    ), patch.object(
        DataSourcePoolManager,
        "_create_postgresql_pool",
        new_callable=AsyncMock,
        return_value=mock_pool,
    ) as create_pool:
        result = await DataSourcePoolManager.get_pool(11)

    assert result is mock_pool
    create_pool.assert_awaited_once_with(config)


@pytest.mark.asyncio
async def test_db_import_service_supports_postgresql_tables_and_ddl():
    config = {
        "host": "127.0.0.1",
        "port": 5432,
        "user": "postgres",
        "password": "secret",
        "database": "nanzi_demo",
    }

    with patch(
        "app.services.db_import_service.DBImportService._postgresql_connect",
        new_callable=AsyncMock,
    ) as connect:
        connection = MagicMock()
        cursor = AsyncMock()
        cursor.fetchone.return_value = ("BASE TABLE", "客户主数据")
        cursor.fetchall.side_effect = [
            [("demo", "customers", "Customer", "BASE TABLE")],
            [("customer_id", "integer", "int4", None, None, None, "NO", None, "客户编号")],
        ]
        cursor_cm = MagicMock()
        cursor_cm.__aenter__ = AsyncMock(return_value=cursor)
        connection.cursor.return_value = cursor_cm
        connection.close = AsyncMock()
        connect.return_value = connection

        tables = await DBImportService.get_postgresql_tables(config)
        ddl = await DBImportService.get_postgresql_ddl(config, ["demo.customers"])

    assert tables == [{"name": "demo.customers", "comment": "Customer", "type": "table"}]
    assert 'CREATE TABLE "demo"."customers"' in ddl
    # PG 的 COMMENT ON 必须在 DDL 里带出来，否则中文备注在选表导入时全部丢失
    assert 'COMMENT ON TABLE "demo"."customers" IS \'客户主数据\';' in ddl
    assert 'COMMENT ON COLUMN "demo"."customers"."customer_id" IS \'客户编号\';' in ddl


@pytest.mark.asyncio
async def test_postgresql_adapter_preview_uses_pool_connection():
    adapter = PostgreSQLAdapter(source_id=11)
    pool = MagicMock()
    connection = MagicMock()
    cursor = AsyncMock()
    cursor.description = [("customer_id", "int4")]
    cursor.fetchall.return_value = [(1,)]
    cursor_cm = MagicMock()
    cursor_cm.__aenter__ = AsyncMock(return_value=cursor)
    connection.cursor.return_value = cursor_cm
    connection_cm = MagicMock()
    connection_cm.__aenter__ = AsyncMock(return_value=connection)
    pool.connection.return_value = connection_cm

    with patch(
        "app.services.pool_manager.DataSourcePoolManager.get_pool",
        new_callable=AsyncMock,
        return_value=pool,
    ):
        result = await adapter.preview("SELECT customer_id FROM demo.customers", limit=10)

    assert result["rows"] == [[1]]
    assert result["columns"][0]["name"] == "customer_id"
    cursor.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_postgresql_adapter_preview_normalizes_backticks_for_total_count():
    adapter = PostgreSQLAdapter(source_id=11)
    pool = MagicMock()
    connection = MagicMock()
    cursor = AsyncMock()
    cursor.description = [("id", "int4")]
    cursor.fetchone.return_value = (1,)
    cursor.fetchall.return_value = [(1,)]
    cursor_cm = MagicMock()
    cursor_cm.__aenter__ = AsyncMock(return_value=cursor)
    connection.cursor.return_value = cursor_cm
    connection_cm = MagicMock()
    connection_cm.__aenter__ = AsyncMock(return_value=connection)
    pool.connection.return_value = connection_cm

    with patch(
        "app.services.pool_manager.DataSourcePoolManager.get_pool",
        new_callable=AsyncMock,
        return_value=pool,
    ):
        result = await adapter.preview(
            "SELECT * FROM `public.ny_function`",
            limit=10,
            include_total=True,
        )

    assert result["total_count"] == 1
    executed_sql = [call.args[0] for call in cursor.execute.await_args_list]
    assert executed_sql == [
        'SELECT COUNT(*) FROM (SELECT * FROM "public"."ny_function") AS _preview_count',
        'SELECT * FROM (SELECT * FROM "public"."ny_function") AS _preview_sub LIMIT 10',
    ]


@pytest.mark.asyncio
async def test_postgresql_adapter_preview_converts_nested_metric_datetime_functions():
    """验证业务指标预览的行查询和 COUNT 查询均使用 PostgreSQL 日期表达式。"""
    adapter = PostgreSQLAdapter(source_id=11)
    pool = MagicMock()
    connection = MagicMock()
    cursor = AsyncMock()
    cursor.description = [("created_day", "date")]
    cursor.fetchone.return_value = (1,)
    cursor.fetchall.return_value = [("2026-08-28",)]
    cursor_cm = MagicMock()
    cursor_cm.__aenter__ = AsyncMock(return_value=cursor)
    connection.cursor.return_value = cursor_cm
    connection_cm = MagicMock()
    connection_cm.__aenter__ = AsyncMock(return_value=connection)
    pool.connection.return_value = connection_cm

    with patch(
        "app.services.pool_manager.DataSourcePoolManager.get_pool",
        new_callable=AsyncMock,
        return_value=pool,
    ):
        result = await adapter.preview(
            "SELECT toDate(parseDateTimeBestEffort(create_time)) AS created_day FROM orders",
            limit=10,
            include_total=True,
        )

    assert result["total_count"] == 1
    executed_sql = [call.args[0] for call in cursor.execute.await_args_list]
    assert all("parseDateTimeBestEffort" not in sql for sql in executed_sql)
    assert all("toDate(" not in sql for sql in executed_sql)
    assert "CAST(CAST(create_time AS TIMESTAMP) AS DATE)" in executed_sql[0]
    assert "CAST(CAST(create_time AS TIMESTAMP) AS DATE)" in executed_sql[1]


def test_profile_import_preview_strips_postgresql_schema_from_physical_name():
    profile = SimpleNamespace(
        table_name="demo.orders",
        ddl="CREATE TABLE \"demo\".\"orders\" (\"id\" integer);",
        columns_profile=[{"name": "id", "term": "订单 ID"}],
        ai_tags=[],
        ai_term="订单明细表",
        ai_description="订单明细",
    )

    table = DbProfileService._profile_to_import_table(profile)

    assert table["physical_name"] == "orders"
    assert table["term"] == "订单明细表"
