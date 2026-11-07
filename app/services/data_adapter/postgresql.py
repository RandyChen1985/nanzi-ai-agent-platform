"""PostgreSQL data-source adapter and identifier helpers."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from psycopg import sql

from .base import DataSourceAdapter, SQLSafetyError, standardize_items
from .models import LogicalQuery, ResultSet
from .sql_template import render_sql_template


POSTGRESQL_TYPES = ("postgres", "postgresql", "pg")
logger = logging.getLogger(__name__)


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


def normalize_postgresql_identifiers(sql_text: str) -> str:
    """将 SQL 中的 MySQL 反引号标识符转换为 PostgreSQL 双引号标识符。

    数据门户可能复用 MySQL 风格的物理表名（例如 ``public.ny_function``）。
    PostgreSQL 不支持反引号，因此只在 SQL 的标识符上下文中做兼容转换；
    单引号字符串、双引号标识符、注释和 dollar-quoted 字符串会原样保留。
    """
    source = str(sql_text or "")
    if "`" not in source:
        return source

    output: List[str] = []
    index = 0
    length = len(source)
    while index < length:
        current = source[index]

        # SQL 字符串中的反引号是普通文本，不能当作标识符转换。
        if current == "'":
            start = index
            index += 1
            while index < length:
                if source[index] == "'":
                    if index + 1 < length and source[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    break
                if source[index] == "\\" and index + 1 < length:
                    index += 2
                    continue
                index += 1
            output.append(source[start:index])
            continue

        # 双引号标识符允许包含反引号，同样必须保持原样。
        if current == '"':
            start = index
            index += 1
            while index < length:
                if source[index] == '"':
                    if index + 1 < length and source[index + 1] == '"':
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            output.append(source[start:index])
            continue

        # 保留行注释和块注释内容，避免转换示例 SQL 或说明文字。
        if source.startswith("--", index):
            end = source.find("\n", index)
            if end < 0:
                output.append(source[index:])
                break
            output.append(source[index:end])
            index = end
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end < 0:
                output.append(source[index:])
                break
            end += 2
            output.append(source[index:end])
            index = end
            continue

        # PostgreSQL 的 dollar-quoted 字符串常用于函数或复杂表达式。
        if current == "$":
            dollar_match = re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", source[index:])
            if dollar_match:
                delimiter = dollar_match.group(0)
                end = source.find(delimiter, index + len(delimiter))
                if end >= 0:
                    end += len(delimiter)
                    output.append(source[index:end])
                    index = end
                    continue

        if current != "`":
            output.append(current)
            index += 1
            continue

        # 读取一个 MySQL 反引号标识符，`` 表示标识符内部的单个反引号。
        start = index + 1
        index = start
        identifier_parts: List[str] = []
        buffer: List[str] = []
        closed = False
        while index < length:
            if source[index] == "`":
                if index + 1 < length and source[index + 1] == "`":
                    buffer.append("`")
                    index += 2
                    continue
                identifier_parts.append("".join(buffer))
                index += 1
                closed = True
                break
            buffer.append(source[index])
            index += 1

        if not closed:
            # 不完整的标识符交由数据库返回语法错误，避免猜测并改变原始 SQL。
            logger.warning("PostgreSQL SQL 存在未闭合的反引号标识符，保留原始内容")
            output.append(source[start - 1 :])
            break

        identifier = identifier_parts[0]
        # 上游把 schema.table 整体包在反引号中时，拆成 PostgreSQL 的限定标识符。
        quoted_parts = [quote_postgresql_identifier(part) for part in identifier.split(".")]
        output.append(".".join(quoted_parts))

    normalized = "".join(output)
    if normalized != source:
        logger.info("PostgreSQL SQL 已将 MySQL 反引号标识符转换为双引号")
    return normalized


# 业务指标历史上按 ClickHouse 方言生成；这些函数在 PostgreSQL 中需要显式改写。
_CLICKHOUSE_POSTGRESQL_FUNCTIONS = frozenset(
    {
        "parsedatetimebesteffort",
        "parsedatetimebesteffortorzero",
        "parsedatetimebesteffortornull",
        "todate",
        "todateornull",
        "todatetime",
        "todatetime64",
        "todatetimeornull",
        "toyyyymm",
        "toyyyymmdd",
        "toyyyymmddhhmmss",
        "toyear",
        "toisoyear",
        "toquarter",
        "tomonth",
        "toweek",
        "todayofyear",
        "todayofmonth",
        "todayofweek",
        "tohour",
        "tominute",
        "tosecond",
        "tostartofday",
        "tostartofhour",
        "tostartofmonth",
        "tostartofquarter",
        "tostartofyear",
        "tostartofweek",
        "datediff",
        "formatdatetime",
        "today",
    }
)


def _consume_sql_quoted_or_comment(source: str, index: int) -> Optional[int]:
    """返回字符串、标识符、注释或 dollar-quote 的结束位置。"""
    length = len(source)
    current = source[index]
    if current in ("'", '"', "`"):
        quote = current
        index += 1
        while index < length:
            if source[index] == quote:
                if index + 1 < length and source[index + 1] == quote:
                    index += 2
                    continue
                return index + 1
            if source[index] == "\\" and quote == "'" and index + 1 < length:
                index += 2
                continue
            index += 1
        return length

    if source.startswith("--", index):
        end = source.find("\n", index + 2)
        return length if end < 0 else end
    if source.startswith("/*", index):
        end = source.find("*/", index + 2)
        return length if end < 0 else end + 2
    if current == "$":
        dollar_match = re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", source[index:])
        if dollar_match:
            delimiter = dollar_match.group(0)
            end = source.find(delimiter, index + len(delimiter))
            return length if end < 0 else end + len(delimiter)
    return None


def _find_matching_parenthesis(source: str, opening_index: int) -> int:
    """查找函数调用右括号，忽略字符串和注释中的括号。"""
    depth = 1
    index = opening_index + 1
    while index < len(source):
        quoted_end = _consume_sql_quoted_or_comment(source, index)
        if quoted_end is not None:
            index = quoted_end
            continue
        current = source[index]
        if current == "(":
            depth += 1
        elif current == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def _split_function_arguments(arguments: str) -> List[str]:
    """按顶层逗号拆分参数，保留参数内嵌套函数和字符串内容。"""
    parts: List[str] = []
    start = 0
    depth = 0
    index = 0
    while index < len(arguments):
        quoted_end = _consume_sql_quoted_or_comment(arguments, index)
        if quoted_end is not None:
            index = quoted_end
            continue
        current = arguments[index]
        if current == "(":
            depth += 1
        elif current == ")":
            depth = max(0, depth - 1)
        elif current == "," and depth == 0:
            parts.append(arguments[start:index].strip())
            start = index + 1
        index += 1
    tail = arguments[start:].strip()
    if tail or arguments.strip():
        parts.append(tail)
    return parts


def _postgresql_extract_number(field: str, argument: str) -> str:
    """生成 PostgreSQL 的数值型 EXTRACT 表达式，保持指标分组键为整数。"""
    return f"CAST(EXTRACT({field} FROM {argument}) AS INTEGER)"


def _postgresql_safe_cast(argument: str, target_type: str) -> str:
    """模拟 ClickHouse OrNull 转换：非法输入返回 NULL，而不是让查询失败。"""
    normalized_type = target_type.upper()
    return (
        f"CASE WHEN pg_input_is_valid(CAST({argument} AS TEXT), '{target_type}') "
        f"THEN CAST({argument} AS {normalized_type}) ELSE NULL END"
    )


def _convert_clickhouse_function_to_postgresql(name: str, arguments: List[str]) -> Optional[str]:
    """将一个已拆分参数的 ClickHouse 函数转换为 PostgreSQL 表达式。"""
    function_name = name.lower()
    if function_name == "parsedatetimebesteffortornull" and arguments:
        return _postgresql_safe_cast(arguments[0], "timestamp")
    if function_name in {
        "parsedatetimebesteffort",
        "parsedatetimebesteffortorzero",
        "todatetime",
        "todatetime64",
        "todatetimeornull",
    } and arguments:
        return f"CAST({arguments[0]} AS TIMESTAMP)"
    if function_name == "todateornull" and arguments:
        return _postgresql_safe_cast(arguments[0], "date")
    if function_name == "todate" and arguments:
        return f"CAST({arguments[0]} AS DATE)"
    if function_name in {"toyear"} and arguments:
        return _postgresql_extract_number("YEAR", arguments[0])
    if function_name == "toisoyear" and arguments:
        return _postgresql_extract_number("ISOYEAR", arguments[0])
    if function_name == "toquarter" and arguments:
        return _postgresql_extract_number("QUARTER", arguments[0])
    if function_name == "tomonth" and arguments:
        return _postgresql_extract_number("MONTH", arguments[0])
    if function_name == "toweek" and arguments:
        return _postgresql_extract_number("WEEK", arguments[0])
    if function_name == "todayofyear" and arguments:
        return _postgresql_extract_number("DOY", arguments[0])
    if function_name == "todayofmonth" and arguments:
        return _postgresql_extract_number("DAY", arguments[0])
    if function_name == "todayofweek" and arguments:
        return _postgresql_extract_number("ISODOW", arguments[0])
    if function_name == "tohour" and arguments:
        return _postgresql_extract_number("HOUR", arguments[0])
    if function_name == "tominute" and arguments:
        return _postgresql_extract_number("MINUTE", arguments[0])
    if function_name == "tosecond" and arguments:
        return _postgresql_extract_number("SECOND", arguments[0])
    if function_name in {"tostartofday", "tostartofhour", "tostartofmonth", "tostartofquarter", "tostartofyear", "tostartofweek"} and arguments:
        units = {
            "tostartofday": "day",
            "tostartofhour": "hour",
            "tostartofmonth": "month",
            "tostartofquarter": "quarter",
            "tostartofyear": "year",
            "tostartofweek": "week",
        }
        if function_name == "tostartofweek":
            mode = arguments[1].strip().strip("'\"") if len(arguments) > 1 else "0"
            if mode == "1":
                return f"DATE_TRUNC('week', {arguments[0]})"
            return f"DATE_TRUNC('week', {arguments[0]}) - INTERVAL '1 day'"
        return f"DATE_TRUNC('{units[function_name]}', {arguments[0]})"
    if function_name == "toyyyymm" and arguments:
        year = _postgresql_extract_number("YEAR", arguments[0])
        month = _postgresql_extract_number("MONTH", arguments[0])
        return f"(({year} * 100) + {month})"
    if function_name == "toyyyymmdd" and arguments:
        year = _postgresql_extract_number("YEAR", arguments[0])
        month = _postgresql_extract_number("MONTH", arguments[0])
        day = _postgresql_extract_number("DAY", arguments[0])
        return f"(({year} * 10000) + ({month} * 100) + {day})"
    if function_name == "toyyyymmddhhmmss" and arguments:
        year = _postgresql_extract_number("YEAR", arguments[0])
        month = _postgresql_extract_number("MONTH", arguments[0])
        day = _postgresql_extract_number("DAY", arguments[0])
        hour = _postgresql_extract_number("HOUR", arguments[0])
        minute = _postgresql_extract_number("MINUTE", arguments[0])
        second = _postgresql_extract_number("SECOND", arguments[0])
        return (
            f"(({year} * 10000000000) + ({month} * 100000000) + ({day} * 1000000) + "
            f"({hour} * 10000) + ({minute} * 100) + {second})"
        )
    if function_name == "today" and not arguments:
        return "CURRENT_DATE"
    if function_name == "formatdatetime" and len(arguments) >= 2:
        format_text = arguments[1]
        if len(format_text) >= 2 and format_text[0] == format_text[-1] == "'":
            format_text = format_text[1:-1]
            format_tokens = {
                "%Y": "YYYY",
                "%m": "MM",
                "%d": "DD",
                "%H": "HH24",
                "%i": "MI",
                "%s": "SS",
                "%F": "YYYY-MM-DD",
                "%T": "HH24:MI:SS",
                "%M": "MI",
                "%j": "DDD",
                "%u": "ID",
                "%V": "IW",
                "%G": "IYYY",
                "%r": "HH12:MI:SS AM",
            }
            for source_token, target_token in format_tokens.items():
                format_text = format_text.replace(source_token, target_token)
            if re.search(r"%[A-Za-z]", format_text):
                return None
            format_text = "'" + format_text.replace("'", "''") + "'"
        return f"TO_CHAR({arguments[0]}, {format_text})"
    if function_name == "datediff" and len(arguments) >= 3:
        unit = arguments[0].strip().strip("'\"").lower()
        start, end = arguments[1], arguments[2]
        if unit in {"day", "days"}:
            return f"CAST(({end})::date - ({start})::date AS BIGINT)"
        seconds_per_unit = {"second": 1, "seconds": 1, "minute": 60, "minutes": 60, "hour": 3600, "hours": 3600}
        if unit in seconds_per_unit:
            divisor = seconds_per_unit[unit]
            return f"CAST(EXTRACT(EPOCH FROM (({end})::timestamp - ({start})::timestamp)) / {divisor} AS BIGINT)"
    return None


def normalize_postgresql_sql(sql_text: str) -> str:
    """统一转换 PostgreSQL SQL 中的标识符和常见 ClickHouse 日期函数。

    转换器采用括号/字符串感知扫描，不会改写字面量、注释或双引号标识符；无法识别的函数保持原样，
    以便将潜在的业务自定义函数交给数据库报错，并通过日志定位。
    """
    source = normalize_postgresql_identifiers(str(sql_text or ""))
    marker_pattern = r"(?i)\b(?:" + "|".join(sorted(_CLICKHOUSE_POSTGRESQL_FUNCTIONS, key=len, reverse=True)) + r")\s*\("
    if not re.search(marker_pattern, source):
        return source

    def normalize_fragment(fragment: str) -> str:
        output: List[str] = []
        index = 0
        while index < len(fragment):
            quoted_end = _consume_sql_quoted_or_comment(fragment, index)
            if quoted_end is not None:
                output.append(fragment[index:quoted_end])
                index = quoted_end
                continue
            current = fragment[index]
            if current.isalpha() or current == "_":
                name_start = index
                index += 1
                while index < len(fragment) and (fragment[index].isalnum() or fragment[index] == "_"):
                    index += 1
                name = fragment[name_start:index]
                opening = index
                while opening < len(fragment) and fragment[opening].isspace():
                    opening += 1
                if opening < len(fragment) and fragment[opening] == "(":
                    closing = _find_matching_parenthesis(fragment, opening)
                    if closing >= 0:
                        inner = normalize_fragment(fragment[opening + 1 : closing])
                        arguments = _split_function_arguments(inner)
                        converted = _convert_clickhouse_function_to_postgresql(name, arguments)
                        if converted is not None:
                            output.append(converted)
                        else:
                            output.append(fragment[name_start : opening + 1] + inner + ")")
                        index = closing + 1
                        continue
                output.append(fragment[name_start:index])
                continue
            output.append(current)
            index += 1
        return "".join(output)

    normalized = normalize_fragment(source)
    if normalized != source:
        logger.info("PostgreSQL SQL 已转换 ClickHouse 日期函数为兼容表达式")
    return normalized


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
                # custom_sql 来自外部输入，历史上既没有沙箱渲染也没有安全校验：
                # 统一入口先沙箱渲染，再对最终 SQL 执行 _validate_sql_safety。
                raw_sql = render_sql_template(raw_sql, params, self._validate_sql_safety)
            except SQLSafetyError as exc:
                raise ValueError(str(exc)) from exc
            except Exception:
                # 保持既有容错：模板语法错误时退回原始 SQL，由数据库给出具体错误。
                pass
            # 字段探测也必须使用与正式执行相同的方言转换，避免保存前探测成功、预览时失败。
            raw_sql = normalize_postgresql_sql(raw_sql)
            final_sql = f"SELECT * FROM ({raw_sql}) AS _pg_columns LIMIT 0"
            # 真正执行的是包裹后的 final_sql，需要再校验一次，避免“子查询看着只读、
            # 拼上外层后形态变化”导致校验漏判。
            try:
                self._validate_sql_safety(final_sql)
            except SQLSafetyError as exc:
                raise ValueError(str(exc)) from exc
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

        # 直接执行入口覆盖历史指标 SQL，兼容 ClickHouse 日期函数和反引号标识符。
        sql_text = normalize_postgresql_sql(sql_text)
        # 物理执行入口守住只读契约：校验的是方言归一化后真正发给数据库的 SQL。
        try:
            self._validate_sql_safety(sql_text)
        except SQLSafetyError as exc:
            raise ValueError(str(exc)) from exc
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
        # 1. 模板本身先做一次只读校验：明显违规的输入（如 UPDATE）尽早拒绝。
        try:
            self._validate_sql_safety(sql_text)
        except SQLSafetyError as exc:
            raise ValueError(str(exc)) from exc

        # 2. 沙箱渲染 + 渲染后再次校验。
        #    只读校验必须落在最终要执行的 SQL 上：模板 `SELECT ... {{ x }}` 本身是
        #    只读的，但 x 可以渲染出 `1; DROP TABLE ...`，只校验模板会漏掉这类注入。
        try:
            rendered_sql = render_sql_template(sql_text, params, self._validate_sql_safety)
        except SQLSafetyError as exc:
            raise ValueError(str(exc)) from exc
        except Exception as exc:
            raise ValueError(f"Jinja2 模板渲染失败: {exc}") from exc

        # 预览的 COUNT 包装与实际查询必须共享同一份已转换 SQL，否则 include_total 会单独报错。
        clean_sql = normalize_postgresql_sql(rendered_sql.strip().rstrip(";"))
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
