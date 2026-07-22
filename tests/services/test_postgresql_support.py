from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.data_adapter.factory import get_adapter
from app.services.data_adapter.base import SQLSafetyError
from app.services.data_adapter.postgresql import PostgreSQLAdapter
from app.services.db_import_service import DBImportService
from app.services.db_profile_service import DbProfileService
from app.services.pool_manager import DataSourcePoolManager
from app.services.sql_query_execution_service import (
    dialect_from_data_source,
    extract_physical_table_refs_from_select_sql,
)


pytestmark = pytest.mark.no_infrastructure


def test_dialect_from_data_source_postgresql_aliases():
    assert dialect_from_data_source("postgresql_demo") == "postgres"
    assert dialect_from_data_source("pg_reporting") == "postgres"


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
        cursor.fetchall.side_effect = [
            [("demo", "customers", "Customer", "BASE TABLE")],
            [("customer_id", "integer", "int4", None, None, None, "NO", None)],
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


def test_profile_import_preview_preserves_postgresql_schema_in_physical_name():
    profile = SimpleNamespace(
        table_name="demo.orders",
        ddl="CREATE TABLE \"demo\".\"orders\" (\"id\" integer);",
        columns_profile=[{"name": "id", "term": "订单 ID"}],
        ai_tags=[],
        ai_term="订单明细表",
        ai_description="订单明细",
    )

    table = DbProfileService._profile_to_import_table(profile)

    assert table["physical_name"] == "demo.orders"
    assert table["term"] == "订单明细表"


def test_postgresql_table_refs_keep_schema_identity():
    """同名表位于不同 schema 时，权限门禁必须保留完整物理标识。"""
    error, refs = extract_physical_table_refs_from_select_sql(
        "SELECT * FROM permitted.orders p JOIN restricted.orders r ON p.id = r.id",
        "postgres",
    )

    assert error is None
    assert refs == {
        "permitted.orders": "permitted.orders",
        "restricted.orders": "restricted.orders",
    }


@pytest.mark.parametrize(
    "sql_text",
    [
        "WITH changed AS (UPDATE accounts SET balance = 0 RETURNING *) "
        "SELECT * FROM changed LIMIT 1",
        "WITH created AS (INSERT INTO accounts(balance) VALUES (1) RETURNING *) "
        "SELECT * FROM created LIMIT 1",
        "EXPLAIN ANALYZE DELETE FROM accounts",
    ],
)
def test_postgresql_adapter_rejects_nested_write_sql(sql_text):
    """PostgreSQL 可写 CTE 和 EXPLAIN 写操作必须被递归只读门禁拦截。"""
    adapter = PostgreSQLAdapter(source_id=11)

    with pytest.raises(SQLSafetyError, match="安全策略违规"):
        adapter._validate_sql_safety(sql_text)
