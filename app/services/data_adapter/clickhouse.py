from typing import Any, Dict, List, Optional
from .base import DataSourceAdapter, SQLSafetyError, standardize_items
from .models import LogicalQuery, ResultSet
from .sql_template import render_sql_template
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from asynch.errors import InterfaceError
import logging
import time
import re

logger = logging.getLogger(__name__)

class ClickHouseAdapter(DataSourceAdapter):
    """ClickHouse 适配器：封装基于 asynch 连接池的原生 TCP 物理执行、结构探测及限流控制"""

    def __init__(self, source_id: int):
        self.source_id = source_id

    async def execute(self, query: LogicalQuery) -> ResultSet:
        raise NotImplementedError("本地适配器在智能体平台中仅供执行只读物理 SQL，暂不支持逻辑查询 execute 方法。")

    async def execute_summary(self, query: LogicalQuery, agg_fields: List[str] = None) -> Dict[str, Any]:
        raise NotImplementedError("本地适配器在智能体平台中仅供执行只读物理 SQL，暂不支持逻辑查询 execute_summary 方法。")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        retry=retry_if_exception_type((InterfaceError, OSError))
    )
    async def get_tables(self) -> List[Dict[str, str]]:
        """获取当前库下的所有表和视图名称"""
        from app.services.pool_manager import DataSourcePoolManager
        pool = await DataSourcePoolManager.get_pool(self.source_id)
        
        async with pool.connection() as conn:
            async with conn.cursor() as cursor:
                try:
                    sql = "SELECT name, comment, engine FROM system.tables WHERE database = currentDatabase() ORDER BY name"
                    await cursor.execute(sql)
                    rows = await cursor.fetchall()
                except Exception as e:
                    if "Missing columns" not in str(e) and "Code: 47" not in str(e):
                        raise
                    logger.warning("ClickHouse system.tables missing comment column, falling back: %s", e)
                    sql = "SELECT name, engine FROM system.tables WHERE database = currentDatabase() ORDER BY name"
                    await cursor.execute(sql)
                    rows = await cursor.fetchall()
                    return [
                        {
                            "name": row[0],
                            "comment": "",
                            "type": "VIEW" if "VIEW" in str(row[1]).upper() else "TABLE",
                        }
                        for row in rows
                    ]

                return [
                    {
                        "name": row[0],
                        "comment": row[1] or "",
                        "type": "VIEW" if "VIEW" in str(row[2]).upper() else "TABLE",
                    }
                    for row in rows
                ]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        retry=retry_if_exception_type((InterfaceError, OSError))
    )
    async def get_columns(self, table_name: Optional[str] = None, custom_sql: Optional[str] = None, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
        """获取表或自定义查询 SQL 的字段列定义"""
        if custom_sql:
            raw_sql = custom_sql
            try:
                # custom_sql 来自外部输入，同样必须沙箱渲染；渲染后的最终 SQL
                # 由统一入口交给 _validate_sql_safety 校验，不能只校验模板。
                sql = render_sql_template(raw_sql, params, self._validate_sql_safety)
            except SQLSafetyError as e:
                raise ValueError(str(e))
            except Exception as e:
                logger.warning(f"Jinja2 模板渲染异常，回退使用原始 SQL: {e}")
                sql = raw_sql

            query = f"DESCRIBE ({sql})"
        else:
            query = f"DESCRIBE {table_name}"

        # DESCRIBE 也是真正要发给 ClickHouse 的语句，尤其 table_name 是字符串拼接，
        # 需要校验最终 query（例如 table_name 里带分号拼出第二条语句时会被拦截）。
        try:
            self._validate_sql_safety(query)
        except SQLSafetyError as e:
            raise ValueError(str(e))
        
        from app.services.pool_manager import DataSourcePoolManager
        pool = await DataSourcePoolManager.get_pool(self.source_id)
        
        async with pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(query)
                rows = await cursor.fetchall()
                # rows 格式: (name, type, default_type, default_expression, comment, codec_expression, ttl_expression)
                return [{"name": row[0], "type": row[1], "comment": row[4]} for row in rows]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        retry=retry_if_exception_type((InterfaceError, OSError))
    )
    async def execute_sql(self, sql: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        执行物理只读 SQL 并返回统一结果格式。
        """
        # 物理执行入口守住只读契约，避免内部调用链绕过上层 validate_sql。
        try:
            self._validate_sql_safety(sql)
        except SQLSafetyError as e:
            raise ValueError(str(e))

        from app.services.pool_manager import DataSourcePoolManager
        pool = await DataSourcePoolManager.get_pool(self.source_id)
        
        async with pool.connection() as conn:
            async with conn.cursor() as cursor:
                logger.info(f"[ClickHouse Executor] 执行 Raw SQL: {sql}，参数: {params}")
                await cursor.execute(sql, params)
                rows = await cursor.fetchall()
                
                columns = []
                if cursor.description:
                    for desc in cursor.description:
                         columns.append({"name": desc[0], "type": desc[1]})
                         
                items = [list(row) for row in rows]
                
                return {
                    "columns": columns,
                    "items": standardize_items(items)
                }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        retry=retry_if_exception_type((InterfaceError, OSError))
    )
    async def preview(
        self,
        sql: str,
        limit: int = 100,
        params: Dict[str, Any] = None,
        offset: int = 0,
        include_total: bool = False,
    ) -> Dict[str, Any]:
        """
        供数据源管理或 SQL Lab 调试的 Preview 接口，强制最大行数、限制超时和只读拦截
        """
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

        # 3. 强制行数保护
        clean_sql = rendered_sql.strip().rstrip(";")
        limit_match = re.search(r"\bLIMIT\s+(\d+)", clean_sql, re.IGNORECASE)
        if limit_match:
            limit_val = int(limit_match.group(1))
            final_sql = (
                clean_sql[:limit_match.start(1)] + str(min(limit_val, limit)) + clean_sql[limit_match.end(1):]
            )
        else:
            final_sql = f"SELECT * FROM ({clean_sql}) LIMIT {limit}"
        
        start_time = time.perf_counter()
        
        from app.services.pool_manager import DataSourcePoolManager
        pool = await DataSourcePoolManager.get_pool(self.source_id)
        
        # 4. 执行并注入防挂起的安全查询设置
        query_settings = {
            "max_execution_time": 10,           # 最大执行时间 10s
            "max_rows_to_read": 10000000,       # 允许扫描的最大行数 10M
            "readonly": 1                       # 强制 ClickHouse 只读模式
        }
        
        settings_clause = " SETTINGS " + ", ".join([f"{k}={v}" for k, v in query_settings.items()])
        final_sql += settings_clause
        
        async with pool.connection() as conn:
            async with conn.cursor() as cursor:
                logger.info(f"[ClickHouse Executor] 执行预览 SQL: {final_sql}")
                await cursor.execute(final_sql)
                rows = await cursor.fetchall()
                
                columns = []
                if cursor.description:
                    for desc in cursor.description:
                          columns.append({"name": desc[0], "type": desc[1]})
                
                items = [list(row) for row in rows]
                
        execution_time = (time.perf_counter() - start_time) * 1000
        
        return {
            "columns": columns,
            "rows": standardize_items(items),
            "execution_time_ms": execution_time,
            "scanned_rows": 0
        }
