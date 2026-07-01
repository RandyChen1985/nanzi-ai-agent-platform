import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from app.services.data_adapter.base import SQLSafetyError
from app.services.data_adapter.mysql import MySQLAdapter
from app.services.data_adapter.clickhouse import ClickHouseAdapter
from app.services.data_adapter.oracle import OracleAdapter
from app.services.data_adapter.sqlserver import SQLServerAdapter

# 测试 SQL 安全策略校验（基于基类的 _validate_sql_safety 逻辑）
@pytest.mark.no_infrastructure
def test_sql_safety_validation():
    # 实例化一个 Adapter 来测试 safety
    adapter = MySQLAdapter(source_id=1)
    
    # 允许的 SQL 语句
    allowed_sqls = [
        "SELECT * FROM users",
        "select id, name from users where age > 18",
        "  SELECT * FROM orders;  ", # 带有空格和分号
        "WITH cte AS (SELECT 1) SELECT * FROM cte", # 只读 CTE 查询
        "EXPLAIN SELECT * FROM users", # 只读执行计划
        "SHOW TABLES", # 只读元数据查询
        "DESCRIBE users", # 只读表结构查询
        "DESC users", # DESCRIBE 简写
    ]
    for sql in allowed_sqls:
        # 不抛出异常即为通过
        adapter._validate_sql_safety(sql)
        
    # 被拒绝的 SQL 语句
    rejected_sqls = [
        "INSERT INTO users (name) VALUES ('test')",
        "UPDATE users SET name = 'test' WHERE id = 1",
        "DELETE FROM users WHERE id = 1",
        "DROP TABLE users",
        "ALTER TABLE users ADD COLUMN age INT",
        "CREATE TABLE users (id INT)",
        "SELECT * FROM users; DROP TABLE users", # 多语句执行限制
        "SELECT * FROM users; SELECT * FROM orders", # 多语句限制
        "", # 空语句
        "   ;  ", # 空语句
    ]
    
    for sql in rejected_sqls:
        with pytest.raises(SQLSafetyError):
            adapter._validate_sql_safety(sql)


@pytest.mark.asyncio
async def test_mysql_adapter():
    adapter = MySQLAdapter(source_id=1)
    
    # 1. 验证 get_tables 行为
    mock_rows = [("table1", "BASE TABLE"), ("view1", "VIEW")]
    mock_cursor = AsyncMock()
    mock_cursor.fetchall.return_value = mock_rows
    
    mock_conn = MagicMock()
    mock_cursor_cm = MagicMock()
    mock_cursor_cm.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_conn.cursor.return_value = mock_cursor_cm
    
    mock_pool = MagicMock()
    mock_conn_cm = MagicMock()
    mock_conn_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value = mock_conn_cm
    
    with patch("app.services.pool_manager.DataSourcePoolManager.get_pool", return_value=mock_pool):
        tables = await adapter.get_tables()
        assert len(tables) == 2
        assert tables[0] == {"name": "table1", "type": "TABLE"}
        assert tables[1] == {"name": "view1", "type": "VIEW"}
        mock_cursor.execute.assert_called_with("SHOW FULL TABLES")
        
    # 2. 验证 get_columns 行为 (包含 custom_sql)
    mock_cursor.description = [("id", 3, None, None, None, None, None), ("name", 253, None, None, None, None, None)]
    with patch("app.services.pool_manager.DataSourcePoolManager.get_pool", return_value=mock_pool):
        # 表字段获取
        cols = await adapter.get_columns(table_name="users")
        assert len(cols) == 2
        assert cols[0] == {"name": "id", "type": "String", "comment": ""}
        mock_cursor.execute.assert_called_with("SELECT * FROM `users` LIMIT 0")
        
        # 自定义 SQL 字段获取 (带 Jinja 变量)
        cols_custom = await adapter.get_columns(custom_sql="SELECT * FROM users WHERE id = {{ user_id }}", params={"user_id": 123})
        assert len(cols_custom) == 2
        assert cols_custom[1] == {"name": "name", "type": "String", "comment": ""}
        mock_cursor.execute.assert_called_with("SELECT * FROM (SELECT * FROM users WHERE id = 123) as t LIMIT 0")

    # 3. 验证 execute_sql 行为
    mock_cursor.fetchall.return_value = [(1, "Alice"), (2, "Bob")]
    with patch("app.services.pool_manager.DataSourcePoolManager.get_pool", return_value=mock_pool):
        res = await adapter.execute_sql("SELECT id, name FROM users")
        assert len(res["items"]) == 2
        assert res["columns"] == [{"name": "id", "type": "3"}, {"name": "name", "type": "253"}]
        assert res["items"][0] == [1, "Alice"]

    # 4. 验证 preview 行为 (安全过滤、LIMIT 追加和 Jinja 渲染)
    mock_cursor.fetchall.return_value = [(1, "Alice")]
    with patch("app.services.pool_manager.DataSourcePoolManager.get_pool", return_value=mock_pool):
        # 成功 Preview 并自动限制
        res_preview = await adapter.preview("SELECT id, name FROM users", limit=50)
        assert len(res_preview["rows"]) == 1
        assert "SELECT * FROM (SELECT id, name FROM users) AS _preview_sub LIMIT 50" in mock_cursor.execute.call_args[0][0]
        
        # 带 Jinja 变量的 Preview 并过滤非 SELECT 安全报错
        with pytest.raises(ValueError, match="安全策略违规"):
            await adapter.preview("UPDATE users SET name = '{{ name }}'", params={"name": "Alice"})


@pytest.mark.asyncio
async def test_clickhouse_adapter():
    adapter = ClickHouseAdapter(source_id=2)
    
    mock_rows = [("ch_table", "MergeTree"), ("ch_view", "View")]
    mock_cursor = AsyncMock()
    mock_cursor.fetchall.return_value = mock_rows
    
    mock_conn = MagicMock()
    mock_cursor_cm = MagicMock()
    mock_cursor_cm.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_conn.cursor.return_value = mock_cursor_cm
    
    mock_pool = MagicMock()
    mock_conn_cm = MagicMock()
    mock_conn_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.connection.return_value = mock_conn_cm
    
    with patch("app.services.pool_manager.DataSourcePoolManager.get_pool", return_value=mock_pool):
        # 1. get_tables
        tables = await adapter.get_tables()
        assert len(tables) == 2
        assert tables[0] == {"name": "ch_table", "type": "TABLE"}
        assert tables[1] == {"name": "ch_view", "type": "VIEW"}
        assert "system.tables" in mock_cursor.execute.call_args[0][0]
        
        # 2. get_columns
        mock_cursor.description = [("metric", "String", None, None, None, None, None)]
        mock_cursor.fetchall.return_value = [("metric", "String", "", "", "", "", "")]
        cols = await adapter.get_columns(table_name="metrics")
        assert len(cols) == 1
        assert cols[0]["name"] == "metric"
        
        # 3. execute_sql
        mock_cursor.fetchall.return_value = [("cpu_load",)]
        res = await adapter.execute_sql("SELECT metric FROM metrics")
        assert res["items"] == [["cpu_load"]]
        assert res["columns"] == [{"name": "metric", "type": "String"}]


@pytest.mark.asyncio
async def test_oracle_adapter():
    adapter = OracleAdapter(source_id=3)
    
    # 模拟 Oracle 的 cursor 结构
    mock_rows = [("T_USER", "TABLE"), ("V_USER", "VIEW")]
    mock_cursor = AsyncMock()
    mock_cursor.fetchall.return_value = mock_rows
    mock_cursor.description = [("ID", None, None, None, None, None, None), ("NAME", None, None, None, None, None, None)]
    
    mock_conn = MagicMock()
    mock_cursor_cm = MagicMock()
    mock_cursor_cm.__aenter__ = AsyncMock(return_value=mock_cursor)
    # 适配 oracle cursor() & cursor_async()
    mock_conn.cursor.return_value = mock_cursor_cm
    mock_conn.cursor_async.return_value = mock_cursor_cm
    
    mock_pool = MagicMock()
    mock_pool.acquire = AsyncMock(return_value=mock_conn)
    
    with patch("app.services.pool_manager.DataSourcePoolManager.get_pool", return_value=mock_pool):
        # 1. get_tables
        tables = await adapter.get_tables()
        assert len(tables) == 2
        assert tables[0] == {"name": "T_USER", "type": "TABLE"}
        assert tables[1] == {"name": "V_USER", "type": "VIEW"}
        
        # 2. get_columns
        mock_cursor.fetchall.return_value = [("ID", "NUMBER", "Primary Key"), ("NAME", "VARCHAR2", "User Name")]
        cols = await adapter.get_columns(table_name="T_USER")
        assert len(cols) == 2
        assert cols[0]["name"] == "ID"
        
        # 3. execute_sql
        mock_cursor.fetchall.return_value = [(1, "Oracle")]
        res = await adapter.execute_sql("SELECT ID, NAME FROM T_USER")
        assert res["items"] == [[1, "Oracle"]]
        assert res["columns"] == [{"name": "ID", "type": "None"}, {"name": "NAME", "type": "None"}]


@pytest.mark.asyncio
async def test_sqlserver_adapter():
    adapter = SQLServerAdapter(source_id=4)

    mock_rows = [("dbo_users", "BASE TABLE"), ("dbo_user_view", "VIEW")]
    mock_cursor = AsyncMock()
    mock_cursor.fetchall.return_value = mock_rows

    mock_conn = MagicMock()
    mock_cursor_cm = MagicMock()
    mock_cursor_cm.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_conn.cursor.return_value = mock_cursor_cm

    mock_pool = MagicMock()
    mock_conn_cm = MagicMock()
    mock_conn_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value = mock_conn_cm

    with patch("app.services.pool_manager.DataSourcePoolManager.get_pool", return_value=mock_pool):
        tables = await adapter.get_tables()
        assert len(tables) == 2
        assert tables[0] == {"name": "dbo_users", "type": "TABLE"}
        assert tables[1] == {"name": "dbo_user_view", "type": "VIEW"}
        assert "INFORMATION_SCHEMA.TABLES" in mock_cursor.execute.call_args[0][0]

        mock_cursor.description = [("id", str), ("name", str)]
        cols = await adapter.get_columns(table_name="dbo_users")
        assert len(cols) == 2
        assert cols[0] == {"name": "id", "type": "String", "comment": ""}
        mock_cursor.execute.assert_called_with("SELECT TOP 0 * FROM [dbo_users]")

        cols_custom = await adapter.get_columns(
            custom_sql="SELECT * FROM dbo_users WHERE id = {{ user_id }}",
            params={"user_id": 123},
        )
        assert len(cols_custom) == 2
        mock_cursor.execute.assert_called_with(
            "SELECT TOP 0 * FROM (SELECT * FROM dbo_users WHERE id = 123) AS t"
        )

        mock_cursor.fetchall.return_value = [(1, "MSSQL")]
        res = await adapter.execute_sql("SELECT id, name FROM dbo_users")
        assert res["items"] == [[1, "MSSQL"]]
        assert res["columns"] == [{"name": "id", "type": "<class 'str'>"}, {"name": "name", "type": "<class 'str'>"}]

        mock_cursor.fetchall.return_value = [(1, "MSSQL")]
        res_preview = await adapter.preview("SELECT id, name FROM dbo_users", limit=50)
        assert len(res_preview["rows"]) == 1
        assert mock_cursor.execute.call_args[0][0].upper().startswith("SELECT TOP 50")

        with pytest.raises(ValueError, match="安全策略违规"):
            await adapter.preview("UPDATE dbo_users SET name = '{{ name }}'", params={"name": "MSSQL"})
