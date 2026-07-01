import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

from app.services.data_adapter.factory import get_adapter
from app.services.data_adapter.sqlserver import build_sqlserver_odbc_dsn, normalize_sqlserver_type
from app.services.db_import_service import DBImportService
from app.services.pool_manager import DataSourcePoolManager
from app.services.sql_query_execution_service import dialect_from_data_source


def test_normalize_sqlserver_type_aliases():
    assert normalize_sqlserver_type("sqlserver")
    assert normalize_sqlserver_type("MSSQL")
    assert normalize_sqlserver_type("tsql")
    assert not normalize_sqlserver_type("mysql")


def test_build_sqlserver_odbc_dsn():
    dsn = build_sqlserver_odbc_dsn(
        {
            "host": "mssql.local",
            "port": 1433,
            "database": "erp",
            "user": "sa",
            "password": "secret",
        }
    )
    assert "DRIVER={ODBC Driver 18 for SQL Server}" in dsn
    assert "SERVER=mssql.local,1433" in dsn
    assert "DATABASE=erp" in dsn
    assert "UID=sa" in dsn
    assert "PWD=secret" in dsn
    assert "TrustServerCertificate=yes" in dsn


def test_dialect_from_data_source_sqlserver():
    assert dialect_from_data_source("sqlserver_erp") == "sqlserver"
    assert dialect_from_data_source("mssql_prod") == "sqlserver"
    assert dialect_from_data_source("default_clickhouse") == "clickhouse"


@pytest.mark.asyncio
async def test_factory_returns_sqlserver_adapter():
    db_config = SimpleNamespace(id=9, name="sqlserver_erp", db_type="sqlserver")

    with patch("app.core.orm.AsyncSessionLocal") as mock_session_local, \
         patch("app.services.db_connection_service.DbConnectionService.get_config_by_name", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = db_config
        mock_session_local.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_session_local.return_value.__aexit__ = AsyncMock(return_value=False)

        adapter = await get_adapter("sqlserver_erp")
        assert adapter.__class__.__name__ == "SQLServerAdapter"
        assert adapter.source_id == 9


@pytest.mark.asyncio
async def test_pool_manager_create_sqlserver_pool():
    config = SimpleNamespace(
        host="mssql.local",
        port=1433,
        database_name="erp",
        db_user="sa",
        password="secret",
    )
    mock_pool = MagicMock()
    mock_aioodbc = MagicMock()
    mock_aioodbc.create_pool = AsyncMock(return_value=mock_pool)

    with patch.dict("sys.modules", {"aioodbc": mock_aioodbc}):
        pool = await DataSourcePoolManager._create_sqlserver_pool(config)

    assert pool is mock_pool
    mock_aioodbc.create_pool.assert_awaited_once()
    dsn = mock_aioodbc.create_pool.await_args.kwargs["dsn"]
    assert "SERVER=mssql.local,1433" in dsn
    assert "DATABASE=erp" in dsn


@pytest.mark.asyncio
async def test_db_import_service_sqlserver_connection():
    mock_conn = MagicMock()
    mock_cursor = AsyncMock()
    mock_cursor_cm = MagicMock()
    mock_cursor_cm.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_conn.cursor.return_value = mock_cursor_cm
    mock_conn.close = AsyncMock()

    mock_aioodbc = MagicMock()
    mock_aioodbc.connect = AsyncMock(return_value=mock_conn)

    config = {
        "host": "mssql.local",
        "port": 1433,
        "database": "erp",
        "user": "sa",
        "password": "secret",
    }

    with patch.dict("sys.modules", {"aioodbc": mock_aioodbc}):
        ok = await DBImportService.test_sqlserver_connection(config)

    assert ok is True
    mock_aioodbc.connect.assert_awaited_once()
    mock_cursor.execute.assert_awaited_with("SELECT 1")
    mock_conn.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_db_import_service_sqlserver_tables_and_ddl():
    mock_conn = MagicMock()
    mock_cursor = AsyncMock()
    mock_cursor_cm = MagicMock()
    mock_cursor_cm.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_conn.cursor.return_value = mock_cursor_cm
    mock_conn.close = AsyncMock()

    mock_aioodbc = MagicMock()
    mock_aioodbc.connect = AsyncMock(return_value=mock_conn)

    config = {
        "host": "mssql.local",
        "port": 1433,
        "database": "erp",
        "user": "sa",
        "password": "secret",
    }

    mock_cursor.fetchone = AsyncMock(side_effect=[("BASE TABLE",)])
    mock_cursor.fetchall = AsyncMock(
        side_effect=[
            [("users", "用户表", "table"), ("user_view", "", "view")],
            [
                ("id", "int", None, 10, 0, "NO", None),
                ("name", "nvarchar", 50, None, None, "YES", None),
            ],
        ]
    )

    with patch.dict("sys.modules", {"aioodbc": mock_aioodbc}):
        tables = await DBImportService.get_sqlserver_tables(config)
        ddl = await DBImportService.get_sqlserver_ddl(config, ["users"])

    assert tables == [
        {"name": "users", "comment": "用户表", "type": "table"},
        {"name": "user_view", "comment": "", "type": "view"},
    ]
    assert "CREATE TABLE [users]" in ddl
    assert "[id] int" in ddl
    assert "[name] nvarchar(50) NULL" in ddl
