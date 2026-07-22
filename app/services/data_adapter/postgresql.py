"""PostgreSQL data-source adapter and identifier helpers."""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Tuple

from jinja2 import BaseLoader, Environment, Undefined
from psycopg import sql

from .base import DataSourceAdapter, SQLSafetyError, standardize_items
from .models import LogicalQuery, ResultSet


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
POSTGRESQL_TYPES = ("postgres", "postgresql", "pg")


def is_postgresql_type(db_type: str) -> bool:
    return str(db_type or "").strip().lower() in POSTGRESQL_TYPES


def build_postgresql_conninfo(config: Dict[str, Any]) -> Dict[str, Any]:
    """Build psycopg connection kwargs from a saved data-source config."""
    return {
        "host": config.get("host"),
        "port": int(config.get("port") or 5432),
        "dbname": config.get("database") or config.get("database_name"),
        "user": config.get("user") or config.get("db_user"),
        "password": config.get("password") or "",
        "connect_timeout": 10,
    }


def split_postgresql_identifier(name: str) -> Tuple[Optional[str], str]:
    """Split ``schema.table`` while accepting quoted identifiers."""
    parts = [part.strip().strip('"').strip("`") for part in str(name or "").split(".")]
    if len(parts) == 2 and all(parts):
        return parts[0], parts[1]
    return None, parts[-1] if parts else ""


def quote_postgresql_identifier(name: str) -> str:
    """Quote an identifier without requiring a live psycopg connection."""
    return '"' + str(name).replace('"', '""') + '"'


def qualified_identifier(name: str, default_schema: str = "public") -> sql.Composed:
    schema, table = split_postgresql_identifier(name)
    if not table:
        raise ValueError("表名不能为空")
    return sql.SQL(".").join(
        [sql.Identifier(schema or default_schema), sql.Identifier(table)]
    )


class PostgreSQLAdapter(DataSourceAdapter):
    """PostgreSQL read-only adapter backed by psycopg's async connection pool."""

    def __init__(self, source_id: int):
        self.source_id = source_id

    async def execute(self, query: LogicalQuery) -> ResultSet:
        raise NotImplementedError("本地适配器仅支持执行只读物理 SQL")

    async def execute_summary(self, query: LogicalQuery, agg_fields: List[str] = None) -> Dict[str, Any]:
        raise NotImplementedError("本地适配器仅支持执行只读物理 SQL")

    async def get_tables(self) -> List[Dict[str, str]]:
        from app.services.pool_manager import DataSourcePoolManager

        pool = await DataSourcePoolManager.get_pool(self.source_id)
        query = """
            SELECT
                table_schema,
                table_name,
                COALESCE(obj_description(
                    (quote_ident(table_schema) || '.' || quote_ident(table_name))::regclass,
                    'pg_class'
                ), ''),
                table_type
            FROM information_schema.tables
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
              AND table_type IN ('BASE TABLE', 'VIEW')
            ORDER BY table_schema, table_name
        """
        async with pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(query)
                rows = await cursor.fetchall()

        return [
            {
                "name": f"{row[0]}.{row[1]}",
                "comment": row[2] or "",
                "type": "view" if row[3] == "VIEW" else "table",
            }
            for row in rows
        ]

    async def get_columns(
        self,
        table_name: Optional[str] = None,
        custom_sql: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, str]]:
        from app.services.pool_manager import DataSourcePoolManager

        pool = await DataSourcePoolManager.get_pool(self.source_id)
        if custom_sql:
            raw_sql = custom_sql.strip().rstrip(";")
            try:
                raw_sql = SQL_LAB_ENV.from_string(raw_sql).render(**(params or {}))
            except Exception:
                pass
            final_sql = f"SELECT * FROM ({raw_sql}) AS _pg_columns LIMIT 0"
            async with pool.connection() as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute(final_sql)
                    description = cursor.description or []
            return [{"name": desc[0], "type": str(desc[1]), "comment": ""} for desc in description]

        schema, table = split_postgresql_identifier(table_name or "")
        if not table:
            return []
        async with pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT c.column_name, c.data_type, c.udt_name,
                           COALESCE(col_description(
                               (quote_ident(c.table_schema) || '.' || quote_ident(c.table_name))::regclass,
                               c.ordinal_position
                           ), '')
                    FROM information_schema.columns c
                    WHERE c.table_schema = %s AND c.table_name = %s
                    ORDER BY c.ordinal_position
                    """,
                    (schema or "public", table),
                )
                rows = await cursor.fetchall()
        return [
            {"name": row[0], "type": row[1] or row[2] or "String", "comment": row[3] or ""}
            for row in rows
        ]

    async def execute_sql(self, sql_text: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        from app.services.pool_manager import DataSourcePoolManager

        pool = await DataSourcePoolManager.get_pool(self.source_id)
        async with pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(sql_text, params or ())
                rows = await cursor.fetchall()
                description = cursor.description or []
        return {
            "columns": [{"name": desc[0], "type": str(desc[1])} for desc in description],
            "items": standardize_items([list(row) for row in rows]),
        }

    async def preview(
        self,
        sql_text: str,
        limit: int = 100,
        params: Dict[str, Any] = None,
        offset: int = 0,
        include_total: bool = False,
    ) -> Dict[str, Any]:
        params = params or {}
        limit = min(max(int(limit or 100), 1), 1000)
        try:
            self._validate_sql_safety(sql_text)
        except SQLSafetyError as exc:
            raise ValueError(str(exc)) from exc

        rendered_sql = sql_text
        if "{{" in sql_text or "{%" in sql_text:
            try:
                rendered_sql = SQL_LAB_ENV.from_string(sql_text).render(**params)
                self._validate_sql_safety(rendered_sql)
            except SQLSafetyError as exc:
                raise ValueError(str(exc)) from exc
            except Exception as exc:
                raise ValueError(f"Jinja2 模板渲染失败: {exc}") from exc

        clean_sql = rendered_sql.strip().rstrip(";")
        limit_match = re.search(r"\bLIMIT\s+(\d+)", clean_sql, re.IGNORECASE)
        if limit_match:
            final_sql = (
                clean_sql[: limit_match.start(1)]
                + str(min(int(limit_match.group(1)), limit))
                + clean_sql[limit_match.end(1) :]
            )
        else:
            final_sql = f"SELECT * FROM ({clean_sql}) AS _preview_sub LIMIT {limit}"

        from app.services.pool_manager import DataSourcePoolManager

        started = time.perf_counter()
        total_count = None
        pool = await DataSourcePoolManager.get_pool(self.source_id)
        async with pool.connection() as connection:
            async with connection.cursor() as cursor:
                if include_total and not limit_match:
                    await cursor.execute(f"SELECT COUNT(*) FROM ({clean_sql}) AS _preview_count")
                    total_count = int((await cursor.fetchone())[0])
                await cursor.execute(final_sql, params or ())
                rows = await cursor.fetchall()
                description = cursor.description or []

        result = {
            "columns": [{"name": desc[0], "type": str(desc[1])} for desc in description],
            "rows": standardize_items([list(row) for row in rows]),
            "execution_time_ms": (time.perf_counter() - started) * 1000,
            "scanned_rows": 0,
            "offset": offset,
            "limit": limit,
        }
        if total_count is not None:
            result["total_count"] = total_count
        return result
