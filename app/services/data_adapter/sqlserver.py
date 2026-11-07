import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

from app.services.ai.sql_dialect_limit import apply_dialect_row_limit

from .base import DataSourceAdapter, SQLSafetyError, standardize_items
from .models import LogicalQuery, ResultSet
from .sql_template import render_sql_template

logger = logging.getLogger(__name__)


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
        """获取当前库下的所有表和视图名称（含中文备注）"""
        from app.services.pool_manager import DataSourcePoolManager

        pool = await DataSourcePoolManager.get_pool(self.source_id)
        sql = """
            SELECT
                t.TABLE_NAME,
                ISNULL(CAST(ep.value AS NVARCHAR(MAX)), '') AS TABLE_COMMENT,
                CASE WHEN t.TABLE_TYPE = 'VIEW' THEN 'VIEW' ELSE 'TABLE' END AS obj_type
            FROM INFORMATION_SCHEMA.TABLES t
            LEFT JOIN sys.tables st ON st.name = t.TABLE_NAME
            LEFT JOIN sys.extended_properties ep
                ON ep.major_id = st.object_id AND ep.minor_id = 0 AND ep.name = 'MS_Description'
            WHERE t.TABLE_TYPE IN ('BASE TABLE', 'VIEW')
              AND t.TABLE_CATALOG = DB_NAME()
            ORDER BY t.TABLE_NAME
        """

        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(sql)
                rows = await cursor.fetchall()

        return [
            {"name": row[0], "comment": row[1] or "", "type": row[2]}
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
                # custom_sql 来自外部输入，历史上既没有沙箱渲染也没有安全校验：
                # 统一入口先沙箱渲染，再对最终 SQL 执行 _validate_sql_safety。
                sql = render_sql_template(raw_sql, params, self._validate_sql_safety)
            except SQLSafetyError as e:
                raise ValueError(str(e))
            except Exception as e:
                logger.warning(f"Jinja2 模板渲染异常，回退使用原始 SQL: {e}")
                sql = raw_sql
            final_sql = apply_dialect_row_limit(
                sql,
                dialect="tsql",
                limit=0,
                max_limit=1000,
                min_limit=0,
            )
            # apply_dialect_row_limit 会重写 SELECT/TOP，最终真正执行的是 final_sql，
            # 必须对它再校验一次（例如带 TOP 重写后仍可能拼出第二条语句）。
            try:
                self._validate_sql_safety(final_sql)
            except SQLSafetyError as e:
                raise ValueError(str(e))
        elif table_name:
            meta_sql = """
                SELECT
                    c.COLUMN_NAME,
                    c.DATA_TYPE,
                    ISNULL(CAST(ep.value AS NVARCHAR(MAX)), '') AS COLUMN_COMMENT
                FROM INFORMATION_SCHEMA.COLUMNS c
                LEFT JOIN sys.tables st ON st.name = c.TABLE_NAME
                LEFT JOIN sys.columns sc
                    ON sc.object_id = st.object_id AND sc.name = c.COLUMN_NAME
                LEFT JOIN sys.extended_properties ep
                    ON ep.major_id = sc.object_id
                    AND ep.minor_id = sc.column_id
                    AND ep.name = 'MS_Description'
                WHERE c.TABLE_NAME = ?
                  AND c.TABLE_CATALOG = DB_NAME()
                ORDER BY c.ORDINAL_POSITION
            """
            async with pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(meta_sql, (table_name,))
                    rows = await cursor.fetchall()
            return [
                {"name": row[0], "type": row[1] or "String", "comment": row[2] or ""}
                for row in rows
            ]
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
        # 物理执行入口守住只读契约，避免内部调用链绕过上层 validate_sql。
        try:
            self._validate_sql_safety(sql)
        except SQLSafetyError as e:
            raise ValueError(str(e))

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

    async def preview(
        self,
        sql: str,
        limit: int = 100,
        params: Dict[str, Any] = None,
        offset: int = 0,
        include_total: bool = False,
    ) -> Dict[str, Any]:
        """供数据源管理或 SQL Lab 调试的 Preview 接口，强制最大行数和只读拦截"""
        params = params or {}
        limit = min(max(int(limit or 100), 1), 1000)

        # 1. 模板本身先做一次只读校验：明显违规的输入（如 UPDATE）尽早拒绝。
        try:
            self._validate_sql_safety(sql)
        except SQLSafetyError as e:
            raise ValueError(str(e))

        # 2. 沙箱渲染 + 渲染后再次校验。
        #    只读校验必须落在最终要执行的 SQL 上：模板 `SELECT ... {{ x }}` 本身是
        #    只读的，但 x 可以渲染出 `1; DROP TABLE ...`，只校验模板会漏掉这类注入。
        try:
            rendered_sql = render_sql_template(sql, params, self._validate_sql_safety)
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
