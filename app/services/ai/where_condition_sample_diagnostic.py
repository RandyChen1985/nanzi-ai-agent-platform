"""WHERE 条件不确定时的源表样例探查：SQL 执行失败后自动读取真实字段值，辅助 repair。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.services.ai.empty_result_filter_diagnostic import (
    _looks_like_sql_error,
    _quote_identifier,
    _resolve_main_table_name,
    _try_parse_json,
)
from app.services.ai.sql_dialect_limit import apply_dialect_row_limit, dialect_limit_hint

logger = logging.getLogger(__name__)

MAX_WHERE_PROBE_COLUMNS = 4
MAX_WHERE_PROBE_SAMPLES = 5
MAX_WHERE_PROBE_TABLES = 2

_WHERE_RESERVED = frozenset(
    {
        "and",
        "or",
        "not",
        "in",
        "is",
        "null",
        "between",
        "like",
        "exists",
        "select",
        "from",
        "where",
        "group",
        "order",
        "by",
        "having",
        "limit",
        "rownum",
        "fetch",
        "first",
        "rows",
        "only",
        "date",
        "timestamp",
        "to_date",
        "to_char",
        "to_timestamp",
        "cast",
        "case",
        "when",
        "then",
        "else",
        "end",
        "as",
        "on",
        "join",
        "left",
        "right",
        "inner",
        "outer",
        "cross",
        "using",
    }
)


@dataclass(frozen=True)
class WherePredicateRef:
    table: str
    column: str
    operator: str = ""


@dataclass
class WhereSampleDiagnosticResult:
    table: str
    columns: list[str] = field(default_factory=list)
    diagnostic_sql: str = ""
    sample_rows: list[dict[str, str]] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "columns": list(self.columns),
            "diagnostic_sql": self.diagnostic_sql,
            "sample_rows": list(self.sample_rows),
            "error": self.error,
        }


@dataclass
class AutoWhereFormatRetryResult:
    attempted: bool = False
    corrected_sql: str = ""
    raw_output: str = ""
    parsed_output: Any = None
    has_rows: bool = False
    summary: str = ""
    error: str = ""
    probe_summary: str = ""


def is_where_condition_sql_error(message: str) -> bool:
    """WHERE 条件类型/格式/字面量与列实际存储不一致时常见的执行错误。"""
    text = str(message or "").lower()
    if not text.strip():
        return False
    patterns = (
        "ora-01861",
        "ora-01830",
        "ora-01843",
        "ora-01722",
        "literal does not match format string",
        "invalid number",
        "invalid number format model",
        "date format",
        "datetime format",
        "cannot parse datetime",
        "cannot parse date",
        "incorrect datetime value",
        "datetime field overflow",
        "data type mismatch",
        "incompatible types",
    )
    if any(pattern in text for pattern in patterns):
        return True
    return bool(re.search(r"expected\s+\w+\s+but\s+got", text))


def is_date_format_sql_error(message: str) -> bool:
    text = str(message or "").lower()
    if not text.strip():
        return False
    patterns = (
        "ora-01861",
        "ora-01830",
        "literal does not match format string",
        "date format",
        "datetime format",
        "cannot parse datetime",
        "cannot parse date",
    )
    return any(pattern in text for pattern in patterns)


def is_invalid_number_sql_error(message: str) -> bool:
    text = str(message or "").lower()
    if not text.strip():
        return False
    return (
        "ora-01722" in text
        or "invalid number" in text
        or "invalid number format model" in text
    )


@dataclass
class SchemaColumnHint:
    """Schema 列元信息（仅用于确定探查哪些列；样例值仍从库内 SELECT 获取）。"""

    name: str
    col_type: str = ""
    examples: list[str] = field(default_factory=list)


def schema_column_hints_from_bindings(table_bindings: dict[str, Any] | None) -> dict[str, list[SchemaColumnHint]]:
    """从 run_state.table_bindings 提取列名/类型/示例，供探查列推断使用。"""
    hints: dict[str, list[SchemaColumnHint]] = {}
    for binding in (table_bindings or {}).values():
        table_name = str(getattr(binding, "physical_name", "") or "").strip()
        if not table_name:
            continue
        columns = getattr(binding, "columns", None) or []
        table_hints: list[SchemaColumnHint] = []
        for column in columns:
            name = str(getattr(column, "name", "") or "").strip()
            if not name:
                continue
            examples = getattr(column, "examples", None) or []
            table_hints.append(
                SchemaColumnHint(
                    name=name,
                    col_type=str(getattr(column, "col_type", "") or "").strip(),
                    examples=[str(item).strip() for item in examples if str(item).strip()],
                )
            )
        if table_hints:
            hints[table_name] = table_hints
    return hints


_STRING_LIKE_COLUMN_TYPES = frozenset({
    "varchar", "varchar2", "char", "nchar", "nvarchar", "nvarchar2", "text", "string", "clob",
})
_DATE_LIKE_COLUMN_TYPES = frozenset({
    "date", "datetime", "timestamp", "timestamptz", "datetime2", "smalldatetime",
})
_NUMERIC_LIKE_COLUMN_TYPES = frozenset({
    "number", "numeric", "int", "integer", "float", "double", "decimal", "bigint", "smallint",
})
_WHERE_PROBE_NAME_TOKENS = (
    "date",
    "time",
    "month",
    "year",
    "day",
    "_at",
    "_on",
)


def _normalize_table_key(name: str) -> str:
    return re.sub(r"[`\"'\[\]]", "", str(name or "").strip()).lower()


def _normalize_column_type(col_type: str) -> str:
    return re.sub(r"\([^)]*\)", "", str(col_type or "").strip()).strip().lower()


def _resolve_schema_column_names(
    table: str,
    schema_table_columns: dict[str, list[str]] | None,
) -> list[str]:
    if not schema_table_columns:
        return []
    table_key = _normalize_table_key(table)
    columns = schema_table_columns.get(table) or schema_table_columns.get(table_key) or []
    if columns:
        return [str(item).strip() for item in columns if str(item).strip()]
    for key, values in schema_table_columns.items():
        if _normalize_table_key(key) == table_key:
            return [str(item).strip() for item in values if str(item).strip()]
    return []


def _resolve_schema_column_hints(
    table: str,
    schema_column_hints: dict[str, list[SchemaColumnHint]] | None,
) -> list[SchemaColumnHint]:
    if not schema_column_hints:
        return []
    table_key = _normalize_table_key(table)
    hints = schema_column_hints.get(table) or schema_column_hints.get(table_key) or []
    if hints:
        return list(hints)
    for key, values in schema_column_hints.items():
        if _normalize_table_key(key) == table_key:
            return list(values)
    return []


def _column_appears_in_sql(column: str, sql_text: str) -> bool:
    col = str(column or "").strip()
    if not col:
        return False
    return bool(re.search(rf"(?<![\w.]){re.escape(col)}(?![\w])", sql_text, flags=re.IGNORECASE))


def _looks_like_date_example(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return bool(
        re.search(r"\d{4}[-/年]\d{1,2}", text)
        or re.search(r"\d{4}-\d{2}-\d{2}", text)
        or re.search(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}", text)
    )


def _score_schema_column_for_where_probe(
    hint: SchemaColumnHint,
    *,
    sql_text: str,
    error_message: str,
) -> int:
    name = str(hint.name or "").strip()
    if not name:
        return 0
    col_type = _normalize_column_type(hint.col_type)
    lowered = name.lower()
    score = 0
    if _column_appears_in_sql(name, sql_text):
        score += 10
    if any(token in lowered for token in _WHERE_PROBE_NAME_TOKENS):
        score += 2
    if any(_looks_like_date_example(item) for item in hint.examples):
        score += 2
    if is_date_format_sql_error(error_message):
        if col_type in _DATE_LIKE_COLUMN_TYPES or col_type in _STRING_LIKE_COLUMN_TYPES:
            score += 3
    if is_invalid_number_sql_error(error_message):
        if col_type in _STRING_LIKE_COLUMN_TYPES:
            score += 2
        if col_type in _NUMERIC_LIKE_COLUMN_TYPES:
            score += 1
        if lowered.startswith("is_") or lowered.endswith("_flag") or lowered.endswith("_status"):
            score += 2
    return score


def infer_probe_columns_from_schema(
    table: str,
    sql_text: str,
    *,
    error_message: str = "",
    schema_table_columns: dict[str, list[str]] | None = None,
    schema_column_hints: dict[str, list[SchemaColumnHint]] | None = None,
    max_columns: int = MAX_WHERE_PROBE_COLUMNS,
) -> list[str]:
    """从 Schema 推断应探查的列：优先 SQL 中出现 + 与错误类型相关的列。"""
    hints = _resolve_schema_column_hints(table, schema_column_hints)
    if not hints and schema_table_columns:
        hints = [
            SchemaColumnHint(name=name)
            for name in _resolve_schema_column_names(table, schema_table_columns)
        ]
    if not hints:
        return []

    scored: list[tuple[int, str]] = []
    for hint in hints:
        column_score = _score_schema_column_for_where_probe(
            hint,
            sql_text=sql_text,
            error_message=error_message,
        )
        if column_score <= 0:
            continue
        scored.append((column_score, hint.name))
    scored.sort(key=lambda item: (-item[0], item[1].lower()))
    return [name for _, name in scored[: max(1, max_columns)]]


def extract_where_predicate_columns_from_sql(
    sql: str,
    *,
    dialect: str = "oracle",
    max_columns: int = MAX_WHERE_PROBE_COLUMNS,
    schema_table_columns: dict[str, list[str]] | None = None,
    schema_column_hints: dict[str, list[SchemaColumnHint]] | None = None,
    error_message: str = "",
) -> list[WherePredicateRef]:
    """从失败 SQL 的 WHERE 中提取参与比较/过滤的列（不限于日期）。"""
    text = str(sql or "").strip()
    if not text:
        return []

    main_table = _resolve_main_table_name(text, dialect=dialect)
    found: list[WherePredicateRef] = []
    seen: set[tuple[str, str]] = set()

    def _add(table: str, column: str, operator: str = "") -> bool:
        col = str(column or "").strip()
        if not col or col.lower() in _WHERE_RESERVED:
            return False
        tbl = str(table or "").strip()
        key = (tbl.lower(), col.lower())
        if key in seen:
            return False
        seen.add(key)
        found.append(WherePredicateRef(table=tbl, column=col, operator=operator))
        return True

    try:
        expression = sqlglot.parse_one(text, read=dialect)
        where = expression.find(exp.Where)
        if where:
            for predicate in where.find_all(exp.Predicate):
                _collect_predicate_columns(predicate, _add)
    except (ParseError, ValueError, TypeError):
        pass

    if not found:
        _extract_where_predicate_columns_fallback(text, main_table, _add, max_columns=max_columns * 3)

    schema_columns = infer_probe_columns_from_schema(
        main_table,
        text,
        error_message=error_message,
        schema_table_columns=schema_table_columns,
        schema_column_hints=schema_column_hints,
        max_columns=max_columns * 2,
    )
    for column in schema_columns:
        if _column_appears_in_sql(column, text):
            _add(main_table, column)

    if not found:
        for column in schema_columns:
            _add(main_table, column)

    return _prioritize_where_probe_columns(found, text)[:max_columns]


def _prioritize_where_probe_columns(
    refs: list[WherePredicateRef],
    sql_text: str,
) -> list[WherePredicateRef]:
    """优先探查 DATE/TO_DATE/TO_CHAR 相关列，避免 billing_status 等占满 4 列配额。"""
    upper = str(sql_text or "").upper()

    def score(ref: WherePredicateRef) -> tuple[int, int]:
        col = ref.column.upper()
        col_quoted = re.escape(col)
        date_score = 0
        if re.search(rf"\bDATE\s+'[^']+'", upper) and col in upper:
            if re.search(rf"\b{col_quoted}\s*(?:>=|<=|>|<|=)", upper, flags=re.IGNORECASE):
                date_score += 4
        if re.search(rf"TO_(?:DATE|CHAR)\s*\(\s*(?:\w+\.)?{col_quoted}\b", upper, flags=re.IGNORECASE):
            date_score += 3
        if any(token in col for token in ("DATE", "TIME", "MONTH", "YEAR")):
            date_score += 2
        if ref.operator in {"GTE", "LTE", "GT", "LT", "BETWEEN"}:
            date_score += 1
        return (-date_score, refs.index(ref))

    return sorted(refs, key=score)


def build_where_sample_probe_sql(
    *,
    table: str,
    columns: list[str],
    dialect: str = "oracle",
    limit: int = MAX_WHERE_PROBE_SAMPLES,
) -> str:
    """生成读取源表样例行 SQL，一次返回 WHERE 相关列的真实值（默认最多 5 行）。"""
    table_name = _quote_identifier(table, dialect=dialect)
    safe_limit = max(1, min(int(limit), MAX_WHERE_PROBE_SAMPLES))

    if not columns:
        inner = f"SELECT * FROM {table_name}"
        return apply_dialect_row_limit(inner, dialect=dialect, limit=safe_limit, max_limit=MAX_WHERE_PROBE_SAMPLES)

    select_parts = [
        f"{_quote_identifier(col, dialect=dialect)} AS {_quote_identifier(col, dialect=dialect)}"
        for col in columns[:MAX_WHERE_PROBE_COLUMNS]
    ]
    not_null = " OR ".join(
        f"{_quote_identifier(col, dialect=dialect)} IS NOT NULL" for col in columns[:MAX_WHERE_PROBE_COLUMNS]
    )
    select_clause = ", ".join(select_parts)
    inner = f"SELECT {select_clause} FROM {table_name} WHERE ({not_null})"
    return apply_dialect_row_limit(inner, dialect=dialect, limit=safe_limit, max_limit=MAX_WHERE_PROBE_SAMPLES)


def build_where_sample_probe_fallback_sql(
    *,
    table: str,
    column: str,
    dialect: str = "oracle",
    limit: int = MAX_WHERE_PROBE_SAMPLES,
) -> str:
    """单列探查：批量 OR 探查无样例时，逐列再试。"""
    table_name = _quote_identifier(table, dialect=dialect)
    col_name = _quote_identifier(column, dialect=dialect)
    safe_limit = max(1, min(int(limit), MAX_WHERE_PROBE_SAMPLES))
    inner = f"SELECT {col_name} AS {col_name} FROM {table_name} WHERE {col_name} IS NOT NULL"
    return apply_dialect_row_limit(inner, dialect=dialect, limit=safe_limit, max_limit=MAX_WHERE_PROBE_SAMPLES)


def build_where_sample_probe_plans(
    failed_sql: str,
    *,
    dialect: str = "oracle",
    schema_table_columns: dict[str, list[str]] | None = None,
    schema_column_hints: dict[str, list[SchemaColumnHint]] | None = None,
    error_message: str = "",
) -> list[tuple[str, list[str], str]]:
    """按表分组生成探查计划：(table, columns, probe_sql)。"""
    predicates = extract_where_predicate_columns_from_sql(
        failed_sql,
        dialect=dialect,
        schema_table_columns=schema_table_columns,
        schema_column_hints=schema_column_hints,
        error_message=error_message,
    )
    if not predicates:
        main_table = _resolve_main_table_name(failed_sql, dialect=dialect)
        if not main_table:
            return []
        schema_cols = infer_probe_columns_from_schema(
            main_table,
            failed_sql,
            error_message=error_message,
            schema_table_columns=schema_table_columns,
            schema_column_hints=schema_column_hints,
        )
        probe_sql = build_where_sample_probe_sql(
            table=main_table,
            columns=schema_cols,
            dialect=dialect,
        )
        return [(main_table, schema_cols, probe_sql)]

    grouped: dict[str, list[str]] = {}
    order: list[str] = []
    for item in predicates:
        table_key = item.table or _resolve_main_table_name(failed_sql, dialect=dialect)
        table_key = _resolve_physical_table_name(failed_sql, table_key, dialect=dialect)
        if not table_key:
            continue
        if table_key not in grouped:
            grouped[table_key] = []
            order.append(table_key)
        if item.column not in grouped[table_key]:
            grouped[table_key].append(item.column)

    plans: list[tuple[str, list[str], str]] = []
    for table_key in order[:MAX_WHERE_PROBE_TABLES]:
        cols = grouped.get(table_key) or []
        probe_sql = build_where_sample_probe_sql(table=table_key, columns=cols, dialect=dialect)
        plans.append((table_key, cols, probe_sql))
    return plans


def build_where_condition_probe_repair_hint(
    failed_sql: str,
    *,
    dialect: str = "oracle",
    diagnostics: list[WhereSampleDiagnosticResult] | None = None,
    schema_table_columns: dict[str, list[str]] | None = None,
    schema_column_hints: dict[str, list[SchemaColumnHint]] | None = None,
    error_message: str = "",
) -> str:
    """生成 WHERE 样例探查 repair 提示；若已有诊断结果则直接引用样例值。"""
    if diagnostics:
        block = format_where_condition_repair_block(diagnostics)
        if block:
            return f"\n\n{block}"

    if not str(failed_sql or "").strip():
        limit_hint = dialect_limit_hint(dialect)
        return (
            "\n\n【WHERE 条件探查建议】WHERE 条件因类型/格式与库内实际值不一致而失败时，"
            f"请先执行 1 条诊断 SQL 读取源表相关列的真实样例（SELECT 列 FROM 表 WHERE 列 IS NOT NULL {limit_hint}），"
            "再按样例格式重写 WHERE，禁止继续猜测 DATE/TO_DATE/字符串写法。"
        )

    plans = build_where_sample_probe_plans(
        failed_sql,
        dialect=dialect,
        schema_table_columns=schema_table_columns,
        schema_column_hints=schema_column_hints,
        error_message=error_message,
    )
    if not plans:
        limit_hint = dialect_limit_hint(dialect)
        return (
            "\n\n【WHERE 条件探查建议】无法从失败 SQL 解析 WHERE 列时，"
            f"请对主表执行 SELECT * FROM 主表 {limit_hint} 查看真实字段样例，再修正 WHERE。"
        )

    lines = [
        "【WHERE 条件探查 — 必须先看清真实样例再改 SQL】",
        "探查列来自失败 SQL 的 WHERE 解析 + 当前 Schema 列定义（type/examples 仅用于选列，不代替库内样例）。",
        "样例值必须来自下面诊断 SQL 的查询结果；Schema type/examples 可能与库内实际存储不一致。",
        "禁止继续猜测 DATE/TO_DATE/字符串区间写法；请先执行下面诊断 SQL（计入 repair 轮次，不算最终答案）：",
    ]
    for _table, cols, probe_sql in plans:
        col_hint = "、".join(f"`{c}`" for c in cols) if cols else "（主表相关列）"
        lines.append(f"- 探查列 {col_hint}：\n```sql\n{probe_sql}\n```")
    lines.append(
        "根据 sample 行中各列的真实字符串/日期格式，重写 WHERE 比较方式："
        "字符串列用同格式字面量区间；确为 DATE 类型列才用 DATE 'YYYY-MM-DD'；"
        "禁止对 VARCHAR 列套 TO_DATE/TO_CHAR；禁止对标志位列用 `= 0` 应改为 `= '0'`。"
    )
    return "\n\n" + "\n".join(lines)


def format_where_condition_repair_block(diagnostics: list[WhereSampleDiagnosticResult]) -> str:
    if not diagnostics:
        return ""
    lines = [
        "【平台自动 WHERE 样例探查】已在 SQL 执行失败后自动读取源表真实字段值，请据此修正 WHERE："
    ]
    for item in diagnostics:
        cols = "、".join(f"`{c}`" for c in item.columns) if item.columns else "（全表样例）"
        if item.error:
            lines.append(f"- 表 `{item.table}` 列 {cols} 探查失败：{item.error}")
            continue
        if not item.sample_rows:
            lines.append(
                f"- 表 `{item.table}` 列 {cols} 未查到非空样例。"
                "若错误为 ORA-01861，可尝试去掉 DATE 关键字改用字符串字面量；"
                "若错误为 ORA-01722，检查标志列是否应写 `= '0'` 而非 `= 0`，并去掉 TO_CHAR/TO_DATE。"
            )
            continue
        preview_parts: list[str] = []
        for row in item.sample_rows[:3]:
            cells = [f"{k}={v!r}" for k, v in row.items()]
            preview_parts.append("{" + ", ".join(cells) + "}")
        preview = "；".join(preview_parts)
        lines.append(f"- 表 `{item.table}` 列 {cols} 真实样例：{preview}")
    lines.append(
        "请按上述样例的实际格式重写 WHERE（字符串区间 / DATE 字面量 / 去掉错误的 TO_DATE），"
        "修正后再执行 1 次最终业务 SQL。"
    )
    return "\n".join(lines)


def parse_sample_rows(parsed_output: Any, *, columns: list[str]) -> list[dict[str, str]]:
    if not isinstance(parsed_output, dict):
        return []
    column_names = _extract_result_column_names(parsed_output)
    rows_out: list[dict[str, str]] = []
    for key in ("items", "rows", "data", "records"):
        rows = parsed_output.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows[:MAX_WHERE_PROBE_SAMPLES]:
            mapped = _row_to_sample_dict(row, column_names, columns)
            if mapped and mapped not in rows_out:
                rows_out.append(mapped)
        if rows_out:
            break
    return rows_out


async def run_where_condition_diagnostics(
    *,
    sql: str,
    data_source: str,
    dataset_name: str,
    user_id: Optional[int],
    is_admin: bool,
    execute_sql,
    error_message: str = "",
    schema_table_columns: dict[str, list[str]] | None = None,
    schema_column_hints: dict[str, list[SchemaColumnHint]] | None = None,
) -> list[WhereSampleDiagnosticResult]:
    """SQL 因 WHERE 类型/格式错误失败后，自动探查源表样例行。"""
    from app.services.sql_query_execution_service import dialect_from_data_source

    if not is_where_condition_sql_error(error_message):
        return []

    dialect = dialect_from_data_source(data_source)
    plans = build_where_sample_probe_plans(
        sql,
        dialect=dialect,
        schema_table_columns=schema_table_columns,
        schema_column_hints=schema_column_hints,
        error_message=error_message,
    )
    if not plans:
        return []

    results: list[WhereSampleDiagnosticResult] = []
    for table, columns, probe_sql in plans:
        result = WhereSampleDiagnosticResult(
            table=table,
            columns=list(columns),
            diagnostic_sql=probe_sql,
        )
        try:
            raw = await execute_sql(
                sql=probe_sql,
                data_source=data_source,
                dataset_name=dataset_name,
                user_id=user_id,
                is_admin=is_admin,
            )
        except Exception as exc:
            logger.warning("[WhereConditionDiagnostic] probe SQL failed: %s", exc)
            result.error = str(exc)[:300]
            results.append(result)
            continue

        if _looks_like_sql_error(raw):
            result.error = str(raw)[:500]
            results.append(result)
            continue

        parsed = _try_parse_json(raw)
        result.sample_rows = parse_sample_rows(parsed, columns=columns or [])
        if not result.sample_rows and columns:
            result.sample_rows = await _probe_columns_individually(
                table=table,
                columns=columns,
                dialect=dialect,
                data_source=data_source,
                dataset_name=dataset_name,
                user_id=user_id,
                is_admin=is_admin,
                execute_sql=execute_sql,
            )
        results.append(result)
    return results


async def _probe_columns_individually(
    *,
    table: str,
    columns: list[str],
    dialect: str,
    data_source: str,
    dataset_name: str,
    user_id: Optional[int],
    is_admin: bool,
    execute_sql,
) -> list[dict[str, str]]:
    """批量 OR 探查无样例时，逐列再试；合并为单行样例字典。"""
    merged: dict[str, str] = {}
    for column in columns[:MAX_WHERE_PROBE_COLUMNS]:
        probe_sql = build_where_sample_probe_fallback_sql(
            table=table,
            column=column,
            dialect=dialect,
        )
        try:
            raw = await execute_sql(
                sql=probe_sql,
                data_source=data_source,
                dataset_name=dataset_name,
                user_id=user_id,
                is_admin=is_admin,
            )
        except Exception:
            continue
        if _looks_like_sql_error(raw):
            continue
        parsed = _try_parse_json(raw)
        rows = parse_sample_rows(parsed, columns=[column])
        for row in rows:
            value = str(row.get(column) or row.get(column.upper()) or row.get(column.lower()) or "").strip()
            if value:
                merged[column] = value
                break
    return [merged] if merged else []


def _sample_values_for_columns(diagnostics: list[WhereSampleDiagnosticResult]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for item in diagnostics:
        for col in item.columns:
            key = col.lower()
            for row in item.sample_rows:
                raw = str(row.get(col) or row.get(col.upper()) or row.get(col.lower()) or "").strip()
                if raw and raw not in values.setdefault(key, []):
                    values[key].append(raw)
    return values


def _column_uses_datetime_strings(samples: list[str]) -> bool:
    for sample in samples:
        if re.search(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}", sample):
            return True
    return False


def _string_bound_for_date_literal(date_literal: str, *, use_datetime: bool, operator: str) -> str:
    value = str(date_literal or "").strip()
    if not value:
        return value
    if use_datetime and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return f"{value} 00:00:00"
    return value.split(" ", 1)[0]


def rewrite_where_date_literals_as_strings(
    sql: str,
    *,
    columns: list[str],
    sample_values: dict[str, list[str]],
) -> str | None:
    """将 WHERE 中对字符串日期列误用的 DATE/TO_DATE/TO_CHAR 写法改为字符串比较。"""
    if not sql or not columns:
        return None

    rewritten = str(sql)
    changed = False
    for column in columns:
        samples = sample_values.get(column.lower()) or []
        if not samples:
            continue
        use_datetime = _column_uses_datetime_strings(samples)
        col_re = re.escape(column)
        qual_re = rf"(?:\w+\.)?{col_re}"

        for operator in (">=", "<=", ">", "<", "="):
            pattern = rf"({qual_re}\s*{re.escape(operator)}\s*)DATE\s*'([^']+)'"

            def _replace_date(match: re.Match[str], op: str = operator) -> str:
                nonlocal changed
                prefix = match.group(1)
                literal = match.group(2)
                bound = _string_bound_for_date_literal(literal, use_datetime=use_datetime, operator=op)
                changed = True
                return f"{prefix}'{bound}'"

            rewritten, count = re.subn(pattern, _replace_date, rewritten, flags=re.IGNORECASE)
            if count:
                changed = True

        rewritten, count = re.subn(
            rf"TO_CHAR\s*\(\s*({qual_re})\s*,[^)]+\)",
            r"\1",
            rewritten,
            flags=re.IGNORECASE,
        )
        if count:
            changed = True

        rewritten, count = re.subn(
            rf"TO_DATE\s*\(\s*({qual_re})\s*,[^)]+\)",
            r"\1",
            rewritten,
            flags=re.IGNORECASE,
        )
        if count:
            changed = True

    if not changed or rewritten.strip() == str(sql).strip():
        return None
    return rewritten


def rewrite_where_numeric_literals_as_strings(sql: str) -> str | None:
    """ORA-01722 常见原因：VARCHAR 标志列与数字 0/1 比较。"""
    rewritten = str(sql or "")
    changed = False
    pattern = re.compile(
        r"(\(?\s*(?:\w+\.)?(\w+)\s*=\s*)(0|1)(\s+OR\s+\2\s+IS\s+NULL\s*\)?)",
        flags=re.IGNORECASE,
    )

    def _replace(match: re.Match[str]) -> str:
        nonlocal changed
        prefix = match.group(1)
        literal = match.group(3)
        suffix = match.group(4)
        changed = True
        return f"{prefix}'{literal}'{suffix}"

    rewritten = pattern.sub(_replace, rewritten)
    if not changed or rewritten.strip() == str(sql).strip():
        return None
    return rewritten


def rewrite_where_date_literals_blind(sql: str) -> str | None:
    """样例为空且 ORA-01861 时：去掉 DATE 关键字，改为字符串字面量（保守兜底）。"""
    rewritten = str(sql or "")
    changed = False

    def _replace(match: re.Match[str]) -> str:
        nonlocal changed
        changed = True
        return f"{match.group(1)}'{match.group(2)}'"

    rewritten = re.subn(
        r"((?:\w+\.)?\w+\s*(?:>=|<=|>|<|=)\s*)DATE\s*'([^']+)'",
        _replace,
        rewritten,
        flags=re.IGNORECASE,
    )[0]
    if not changed or rewritten.strip() == str(sql).strip():
        return None
    return rewritten


def build_where_format_corrected_sql(
    sql: str,
    diagnostics: list[WhereSampleDiagnosticResult],
    *,
    error_message: str = "",
) -> str | None:
    if not diagnostics:
        return None
    sample_values = _sample_values_for_columns(diagnostics)
    columns: list[str] = []
    for item in diagnostics:
        for col in item.columns:
            if col not in columns:
                columns.append(col)

    if sample_values:
        corrected = rewrite_where_date_literals_as_strings(
            sql,
            columns=[col for col in columns if col.lower() in sample_values],
            sample_values=sample_values,
        )
        if corrected:
            return corrected

    if is_date_format_sql_error(error_message):
        corrected = rewrite_where_date_literals_blind(sql)
        if corrected:
            return corrected

    if is_invalid_number_sql_error(error_message):
        rewritten = str(sql)
        changed = False
        for column in columns:
            qual_re = rf"(?:\w+\.)?{re.escape(column)}"
            rewritten, count = re.subn(
                rf"TO_CHAR\s*\(\s*({qual_re})\s*,[^)]+\)",
                r"\1",
                rewritten,
                flags=re.IGNORECASE,
            )
            if count:
                changed = True
            rewritten, count = re.subn(
                rf"TO_DATE\s*\(\s*({qual_re})\s*,[^)]+\)",
                r"\1",
                rewritten,
                flags=re.IGNORECASE,
            )
            if count:
                changed = True
        numeric_fix = rewrite_where_numeric_literals_as_strings(rewritten)
        if numeric_fix:
            return numeric_fix
        if changed and rewritten.strip() != str(sql).strip():
            return rewritten
        return rewrite_where_numeric_literals_as_strings(sql)
    return None


async def run_automatic_where_format_retry(
    *,
    sql: str,
    diagnostics: list[WhereSampleDiagnosticResult],
    data_source: str,
    dataset_name: str,
    user_id: Optional[int],
    is_admin: bool,
    execute_sql,
    error_message: str = "",
) -> AutoWhereFormatRetryResult:
    """探查样例后，平台自动改写 WHERE 日期/类型写法并重试 1 次业务 SQL。"""
    from app.services.ai.empty_result_filter_diagnostic import result_has_data_rows

    probe_summary = format_where_condition_repair_block(diagnostics)
    corrected_sql = build_where_format_corrected_sql(
        sql,
        diagnostics,
        error_message=error_message,
    )
    if not corrected_sql:
        return AutoWhereFormatRetryResult(
            probe_summary=probe_summary,
            error="未能根据样例生成有效的 WHERE 自动修正 SQL",
        )

    summary = (
        "【平台自动 WHERE 修正重试】已根据源表样例将 DATE/TO_DATE/TO_CHAR 写法改为字符串比较，"
        "并自动重试 1 次业务 SQL。"
    )
    try:
        raw = await execute_sql(
            sql=corrected_sql,
            data_source=data_source,
            dataset_name=dataset_name,
            user_id=user_id,
            is_admin=is_admin,
        )
    except Exception as exc:
        logger.warning("[WhereConditionDiagnostic] automatic WHERE retry failed: %s", exc)
        return AutoWhereFormatRetryResult(
            attempted=True,
            corrected_sql=corrected_sql,
            probe_summary=probe_summary,
            summary=summary,
            error=str(exc)[:300],
        )

    if _looks_like_sql_error(raw):
        return AutoWhereFormatRetryResult(
            attempted=True,
            corrected_sql=corrected_sql,
            probe_summary=probe_summary,
            summary=summary,
            error=str(raw)[:500],
        )

    parsed = _try_parse_json(raw)
    has_rows = result_has_data_rows(parsed)
    if has_rows:
        summary += " 本次重试已返回数据。"
    else:
        summary += " 本次重试执行成功但结果为空，请继续人工复核 WHERE。"
    return AutoWhereFormatRetryResult(
        attempted=True,
        corrected_sql=corrected_sql,
        raw_output=str(raw),
        parsed_output=parsed,
        has_rows=has_rows,
        probe_summary=probe_summary,
        summary=summary,
    )


def build_where_probe_schema_context_for_dataset(
    dataset_name: str,
    *,
    schema_output: str = "",
    sql_query_binding: Any | None = None,
) -> tuple[dict[str, list[str]] | None, dict[str, list[SchemaColumnHint]] | None]:
    """按数据集从 Schema binding / schema_output 提取 WHERE 探查所需的列元信息。"""
    from app.services.ai.chatbi_sql_query_binding import (
        bindings_to_table_columns,
        extract_schema_table_bindings,
    )

    dataset_key = str(dataset_name or "").strip().lower()
    selected: dict[str, Any] = {}

    if sql_query_binding is not None:
        for table_key, binding in (getattr(sql_query_binding, "tables", None) or {}).items():
            binding_dataset = str(getattr(binding, "dataset_name", "") or "").strip().lower()
            if not dataset_key or not binding_dataset or binding_dataset == dataset_key:
                selected[str(table_key)] = binding

    if not selected and str(schema_output or "").strip():
        for table_key, binding in extract_schema_table_bindings(schema_output).items():
            binding_dataset = str(binding.dataset_name or "").strip().lower()
            if not dataset_key or not binding_dataset or binding_dataset == dataset_key:
                selected[str(table_key)] = binding

    if not selected:
        return None, None

    schema_table_columns = bindings_to_table_columns(selected) or None
    schema_column_hints = schema_column_hints_from_bindings(selected) or None
    return schema_table_columns, schema_column_hints


async def try_automatic_where_condition_repair(
    *,
    sql: str,
    data_source: str,
    dataset_name: str,
    user_id: Optional[int],
    is_admin: bool,
    execute_sql,
    error_message: str = "",
    schema_table_columns: dict[str, list[str]] | None = None,
    schema_column_hints: dict[str, list[SchemaColumnHint]] | None = None,
) -> AutoWhereFormatRetryResult:
    """WHERE 类型/格式错误时：探查源表样例 + 自动修正并重试 1 次（单源/联邦子查询共用）。"""
    if not is_where_condition_sql_error(error_message):
        return AutoWhereFormatRetryResult()

    diagnostics = await run_where_condition_diagnostics(
        sql=sql,
        data_source=data_source,
        dataset_name=dataset_name,
        user_id=user_id,
        is_admin=is_admin,
        execute_sql=execute_sql,
        error_message=error_message,
        schema_table_columns=schema_table_columns,
        schema_column_hints=schema_column_hints,
    )
    if not diagnostics:
        return AutoWhereFormatRetryResult()

    return await run_automatic_where_format_retry(
        sql=sql,
        diagnostics=diagnostics,
        data_source=data_source,
        dataset_name=dataset_name,
        user_id=user_id,
        is_admin=is_admin,
        execute_sql=execute_sql,
        error_message=error_message,
    )


def _resolve_physical_table_name(sql: str, table_ref: str, *, dialect: str) -> str:
    """将 SQL 中的表别名解析为物理表名（探查 SQL 不能用别名）。"""
    ref = str(table_ref or "").strip()
    if not ref:
        return ""
    try:
        expression = sqlglot.parse_one(str(sql or ""), read=dialect)
    except (ParseError, ValueError, TypeError):
        return ref
    alias_map: dict[str, str] = {}
    for table in expression.find_all(exp.Table):
        physical = _table_physical_name_from_node(table)
        if not physical:
            continue
        alias = str(getattr(table, "alias", "") or "").strip()
        alias_map[physical.lower()] = physical
        if alias:
            alias_map[alias.lower()] = physical
    return alias_map.get(ref.lower(), ref)


def _table_physical_name_from_node(table: exp.Table) -> str:
    parts = [str(part).strip() for part in table.parts if str(part).strip()]
    if parts:
        return parts[-1]
    return str(getattr(table, "name", "") or "").strip()


def _collect_predicate_columns(predicate: exp.Expression, add_fn) -> None:
    if isinstance(predicate, exp.Between):
        _collect_column_ref(predicate.this, add_fn, "BETWEEN")
        return
    if isinstance(predicate, exp.In):
        _collect_column_ref(predicate.this, add_fn, "IN")
        return
    if isinstance(predicate, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.ILike)):
        op = type(predicate).__name__.upper()
        _collect_column_ref(predicate.this, add_fn, op)
        return
    if isinstance(predicate, exp.And):
        for child in predicate.flatten():
            if isinstance(child, exp.Predicate):
                _collect_predicate_columns(child, add_fn)
        return
    if isinstance(predicate, exp.Or):
        for child in predicate.flatten():
            if isinstance(child, exp.Predicate):
                _collect_predicate_columns(child, add_fn)


def _collect_column_ref(node: exp.Expression | None, add_fn, operator: str) -> None:
    if node is None:
        return
    if isinstance(node, exp.Column):
        table = str(getattr(node, "table", "") or "").strip()
        column = str(getattr(node, "name", "") or "").strip()
        if column:
            add_fn(table, column, operator)
        return
    if isinstance(node, exp.Func):
        for arg in node.expressions:
            if isinstance(arg, exp.Column):
                table = str(getattr(arg, "table", "") or "").strip()
                column = str(getattr(arg, "name", "") or "").strip()
                if column:
                    add_fn(table, column, operator)


def _extract_where_predicate_columns_fallback(
    sql: str,
    main_table: str,
    add_fn,
    *,
    max_columns: int,
) -> None:
    text = re.sub(r"/\*.*?\*/", " ", str(sql or ""), flags=re.DOTALL)
    text = re.sub(r"--[^\n]*", " ", text)
    patterns = (
        r"(\w+)\.(\w+)\s*(?:>=|<=|>|<|<>|!=|=|LIKE|IN|BETWEEN)\b",
        r"(?<![\w.])(\w+)\s*(?:>=|<=|>|<|<>|!=|=|LIKE|IN|BETWEEN)\b",
    )
    count = 0
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            if count >= max_columns:
                return
            added = False
            if match.lastindex == 2:
                added = bool(add_fn(match.group(1), match.group(2), ""))
            else:
                added = bool(add_fn(main_table, match.group(1), ""))
            if added:
                count += 1


def _extract_result_column_names(parsed: dict[str, Any]) -> list[str]:
    for key in ("columns", "column_names", "fields"):
        cols = parsed.get(key)
        if isinstance(cols, list):
            names: list[str] = []
            for col in cols:
                if isinstance(col, dict):
                    name = str(col.get("name") or col.get("field") or "").strip()
                else:
                    name = str(col or "").strip()
                if name:
                    names.append(name)
            if names:
                return names
    return []


def _row_to_sample_dict(row: Any, column_names: list[str], expected_columns: list[str]) -> dict[str, str]:
    if isinstance(row, dict):
        if expected_columns:
            return {
                col: str(row.get(col, row.get(col.upper(), row.get(col.lower(), "")))).strip()
                for col in expected_columns
                if str(row.get(col, row.get(col.upper(), row.get(col.lower(), "")))).strip()
            }
        return {str(k): str(v).strip() for k, v in row.items() if str(v).strip()}
    if isinstance(row, (list, tuple)):
        mapped: dict[str, str] = {}
        for idx, col_name in enumerate(column_names):
            if idx >= len(row):
                break
            value = str(row[idx]).strip()
            if value:
                mapped[col_name] = value
        if expected_columns:
            return {col: mapped.get(col, "") for col in expected_columns if mapped.get(col)}
        return mapped
    return {}
