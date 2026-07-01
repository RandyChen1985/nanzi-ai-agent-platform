import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

from jinja2 import BaseLoader, Environment, Undefined

from .base import DataSourceAdapter, SQLSafetyError, standardize_items
from .models import LogicalQuery, ResultSet

logger = logging.getLogger(__name__)


class SqlLabUndefined(Undefined):
    def __str__(self):
        return "NULL"

    def __html__(self):
        return "NULL"

    def __iter__(self):
        return iter([])

    def __bool__(self):
        return False


SQL_LAB_ENV = Environment(loader=BaseLoader(), undefined=SqlLabUndefined)


def build_sqlserver_odbc_dsn(config: Dict[str, Any]) -> str:
    """构建 SQL Server ODBC 连接串（供连接池与导入服务共用）。"""
    driver = os.environ.get("MSSQL_ODBC_DRIVER", "ODBC Driver 18 for SQL Server")
    port = int(config.get("port", 1433))
    server = f"{config.get('host')},{port}"
    database = config.get("database") or config.get("database_name") or ""
    user = config.get("user") or config.get("db_user") or ""
    password = config.get("password") or ""
    return (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"UID={user};"
        f"PWD={password};"
        f"TrustServerCertificate=yes;"
    )


def normalize_sqlserver_type(db_type: str) -> bool:
    return db_type.strip().lower() in ("sqlserver", "mssql", "tsql")


class SQLServerAdapter(DataSourceAdapter):
    """SQL Server 数据库适配器：封装基于 aioodbc 连接池的物理查询、结构探测和安全限制"""

    def __init__(self, source_id: int):
        self.source_id = source_id

    async def execute(self, query: LogicalQuery) -> ResultSet:
        raise NotImplementedError("本地适配器在智能体平台中仅供执行只读物理 SQL，暂不支持逻辑查询 execute 方法。")

    async def execute_summary(self, query: LogicalQuery, agg_fields: List[str] = None) -> Dict[str, Any]:
        raise NotImplementedError("本地适配器在智能体平台中仅供执行只读物理 SQL，暂不支持逻辑查询 execute_summary 方法。")

    async def get_tables(self) -> List[Dict[str, str]]:
        """获取当前库下的所有表和视图名称"""
        from app.services.pool_manager import DataSourcePoolManager

        pool = await DataSourcePoolManager.get_pool(self.source_id)
        sql = """
            SELECT TABLE_NAME, TABLE_TYPE
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_TYPE IN ('BASE TABLE', 'VIEW')
              AND TABLE_CATALOG = DB_NAME()
            ORDER BY TABLE_NAME
        """

        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(sql)
                rows = await cursor.fetchall()

        return [
            {"name": row[0], "type": "VIEW" if row[1] == "VIEW" else "TABLE"}
            for row in rows
        ]

    async def get_columns(
        self,
        table_name: Optional[str] = None,
        custom_sql: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, str]]:
        """获取表或自定义查询 SQL 的字段列定义"""
        from app.services.pool_manager import DataSourcePoolManager

        pool = await DataSourcePoolManager.get_pool(self.source_id)

        if custom_sql:
            raw_sql = custom_sql.strip().rstrip(";")
            try:
                sql = SQL_LAB_ENV.from_string(raw_sql).render(**(params or {}))
            except Exception as e:
                logger.warning(f"Jinja2 模板渲染异常，回退使用原始 SQL: {e}")
                sql = raw_sql
            final_sql = f"SELECT TOP 0 * FROM ({sql}) AS t"
        elif table_name:
            final_sql = f"SELECT TOP 0 * FROM [{table_name}]"
        else:
            return []

        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                try:
                    await cursor.execute(final_sql)
                    columns = [desc[0] for desc in cursor.description]
                except Exception as e:
                    logger.error(f"SQL Server 获取字段信息失败: {e}. SQL: {final_sql}")
                    raise ValueError(f"不合法的 SQL 语句或数据表: {e}")

        return [{"name": col, "type": "String", "comment": ""} for col in columns]

    async def execute_sql(self, sql: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """执行物理只读 SQL 并返回统一的结果格式。"""
        from app.services.pool_manager import DataSourcePoolManager

        if params is not None and not params:
            params = None

        pool = await DataSourcePoolManager.get_pool(self.source_id)

        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(sql, params or ())
                rows = await cursor.fetchall()

                columns = []
                if cursor.description:
                    for desc in cursor.description:
                        columns.append({"name": desc[0], "type": str(desc[1])})

                items = [list(row) for row in rows]
                return {"columns": columns, "items": standardize_items(items)}

    async def preview(self, sql: str, limit: int = 100, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """供数据源管理或 SQL Lab 调试的 Preview 接口，强制最大行数和只读拦截"""
        params = params or {}
        limit = min(max(int(limit or 100), 1), 1000)

        try:
            self._validate_sql_safety(sql)
        except SQLSafetyError as e:
            raise ValueError(str(e))

        rendered_sql = sql
        if "{{" in sql or "{%" in sql:
            try:
                rendered_sql = SQL_LAB_ENV.from_string(sql).render(**params)
                self._validate_sql_safety(rendered_sql)
            except SQLSafetyError as e:
                raise ValueError(str(e))
            except Exception as e:
                raise ValueError(f"Jinja2 模板渲染失败: {str(e)}")

        clean_sql = rendered_sql.strip().rstrip(";")
        top_match = re.search(r"\bTOP\s+(\d+)", clean_sql, re.IGNORECASE)
        if top_match:
            limit_val = int(top_match.group(1))
            final_sql = (
                clean_sql[: top_match.start(1)] + str(min(limit_val, limit)) + clean_sql[top_match.end(1) :]
            )
        else:
            final_sql = re.sub(r"(?is)^\s*select\s+", f"SELECT TOP {limit} ", clean_sql, count=1)

        start_time = time.perf_counter()

        from app.services.pool_manager import DataSourcePoolManager

        pool = await DataSourcePoolManager.get_pool(self.source_id)

        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                logger.info(f"[SQL Server Executor] 执行预览 SQL: {final_sql}")
                await cursor.execute(final_sql)
                rows = await cursor.fetchall()

                columns = []
                if cursor.description:
                    for desc in cursor.description:
                        columns.append({"name": desc[0], "type": str(desc[1])})

                items = [list(row) for row in rows]

        execution_time = (time.perf_counter() - start_time) * 1000

        return {
            "columns": columns,
            "rows": standardize_items(items),
            "execution_time_ms": execution_time,
            "scanned_rows": 0,
        }
