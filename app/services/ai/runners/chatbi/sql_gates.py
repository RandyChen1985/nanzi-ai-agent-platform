"""SQL and schema gate detection, preflight, and static risk checks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.services.ai.runners.chatbi.constants import (
    FAILED_SQL_REPEAT_GATE_PREFIX,
    SCHEMA_GATE_PREFIX,
    SQL_PLAN_GATE_PREFIX,
    SQL_REPEAT_GATE_PREFIX,
    SQL_STATIC_GATE_PREFIX,
)
from app.services.ai.time_anchor import TIME_RANGE_GATE_PREFIX


def is_schema_gate_block(output: Any) -> bool:
    return str(output or "").startswith(SCHEMA_GATE_PREFIX)


def is_sql_repeat_gate_block(output: Any) -> bool:
    return str(output or "").startswith(SQL_REPEAT_GATE_PREFIX)


def is_sql_static_gate_block(output: Any) -> bool:
    return str(output or "").startswith(SQL_STATIC_GATE_PREFIX)


def is_time_range_gate_block(output: Any) -> bool:
    return str(output or "").startswith(TIME_RANGE_GATE_PREFIX)


def is_sql_sandbox_gate_block(output: Any) -> bool:
    return str(output or "").startswith("[Performance Blocked]")


def is_failed_sql_repeat_gate_block(output: Any) -> bool:
    return str(output or "").startswith(FAILED_SQL_REPEAT_GATE_PREFIX)


def is_sql_plan_gate_block(output: Any) -> bool:
    return str(output or "").startswith(SQL_PLAN_GATE_PREFIX)


def is_sql_schema_preflight_error(output: Any) -> bool:
    return str(output or "").startswith("[TOOL_ERROR] SQL 预检失败")


def is_cross_dataset_scope_sql_error(message: Any) -> bool:
    text = str(message or "")
    if not text.strip():
        return False
    return (
        "不属于当前指定的数据集" in text
        or "普通 execute_sql_query 严禁跨数据集" in text
    )


def normalize_sql_text(sql: str) -> str:
    return " ".join(str(sql or "").strip().lower().split())


def is_schema_reference_sql_error(message: str) -> bool:
    err = str(message or "").lower()
    if not err.strip():
        return False
    patterns = (
        r"unknown column",
        r"unknown table",
        r"invalid column",
        r"invalid field",
        r"bad field",
        r"no such column",
        r"no such table",
        r"column .+ does not exist",
        r"column not found",
        r"undefined column",
        r"invalid identifier",
        r"unresolved column",
        r"table .+ doesn't exist",
        r"table .+ does not exist",
    )
    return any(re.search(pattern, err) for pattern in patterns)


def extract_failed_repeat_original_error(output: Any) -> str:
    text = str(output or "").strip()
    for marker in ("上次错误摘要：", "上次错误摘要:"):
        if marker in text:
            return text.rsplit(marker, 1)[1].strip()[:800]
    return ""


def extract_invalid_sql_identifiers(message: str) -> list[str]:
    text = str(message or "")
    if not text.strip():
        return []
    candidates: list[str] = []
    patterns = (
        r"ORA-\d+:\s*(?:\"[^\"]+\"\.)?\"([^\"]+)\"\s*:\s*invalid identifier",
        r"unknown column\s+['\"]([^'\"]+)['\"]",
        r"no such column:\s*([A-Za-z_][A-Za-z0-9_.$]*)",
        r"column\s+['\"]?([A-Za-z_][A-Za-z0-9_.$]*)['\"]?\s+does not exist",
        r"invalid identifier\s+['\"]?([A-Za-z_][A-Za-z0-9_.$]*)['\"]?",
        r"unresolved column\s+['\"]?([A-Za-z_][A-Za-z0-9_.$]*)['\"]?",
    )
    for pattern in patterns:
        candidates.extend(re.findall(pattern, text, flags=re.IGNORECASE))

    identifiers: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if isinstance(candidate, tuple):
            candidate = next((part for part in candidate if part), "")
        value = str(candidate or "").strip().strip('"').strip("'")
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        identifiers.append(value)
        if len(identifiers) >= 8:
            break
    return identifiers


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


def normalize_sql_identifier(identifier: str) -> str:
    value = str(identifier or "").strip()
    value = value.strip('"').strip("`").strip("[").strip("]")
    return value.lower()


def split_schema_columns(raw: str) -> list[str]:
    values: list[str] = []
    for part in re.split(r"[,，]", str(raw or "")):
        name = part.strip().strip("-").strip()
        if not name:
            continue
        name = name.split("#", 1)[0].strip()
        if not name:
            continue
        values.append(name)
    return values


_STRING_LIKE_COLUMN_TYPES = frozenset({
    "varchar", "varchar2", "char", "nchar", "nvarchar", "nvarchar2", "text", "string", "clob",
})
_DATE_LIKE_COLUMN_TYPES = frozenset({
    "date", "datetime", "timestamp", "timestamptz", "datetime2", "smalldatetime",
})


@dataclass
class SchemaColumnMeta:
    name: str
    col_type: str = ""
    examples: list[str] = field(default_factory=list)


def _parse_schema_examples_value(raw: str) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text.replace("'", '"'))
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    values: list[str] = []
    for part in re.split(r"[,，]", text):
        item = part.strip().strip('"').strip("'")
        if item:
            values.append(item)
    return values


def _flush_schema_column(
    table_columns: dict[str, list[SchemaColumnMeta]],
    *,
    current_table: str,
    current_column: SchemaColumnMeta | None,
) -> None:
    if not current_table or current_column is None:
        return
    key = normalize_sql_identifier(current_table)
    columns = table_columns.setdefault(key, [])
    normalized = normalize_sql_identifier(current_column.name)
    existing = {normalize_sql_identifier(item.name) for item in columns}
    if normalized and normalized not in existing:
        columns.append(current_column)


def _format_schema_column_binding(column: SchemaColumnMeta) -> str:
    details: list[str] = []
    col_type = str(column.col_type or "").strip()
    if col_type:
        details.append(col_type)
    examples = [str(item).strip() for item in column.examples if str(item).strip()]
    if examples:
        sample = examples[0]
        if len(examples) > 1:
            details.append(f"例: {sample!r} 等")
        else:
            details.append(f"例: {sample!r}")
    if not details:
        return column.name
    return f"{column.name} ({', '.join(details)})"


def _column_date_filter_hint(column: SchemaColumnMeta) -> str:
    col_type = normalize_sql_identifier(str(column.col_type or ""))
    if not col_type:
        return ""
    if col_type in _STRING_LIKE_COLUMN_TYPES:
        return "字符串日期列：WHERE 用与样例值同格式的字符串比较，禁止 DATE/TO_DATE/TO_CHAR"
    if col_type in _DATE_LIKE_COLUMN_TYPES:
        return "日期列：可用 DATE 'YYYY-MM-DD' 做范围比较，禁止对列套 TO_CHAR/TO_DATE"
    return ""


def extract_schema_table_column_meta(output: Any) -> dict[str, list[SchemaColumnMeta]]:
    text = str(output or "")
    if not text.strip():
        return {}

    table_columns: dict[str, list[SchemaColumnMeta]] = {}
    current_table = ""
    columns_mode = False
    columns_indent = 0
    current_column: SchemaColumnMeta | None = None
    column_item_indent = 0
    examples_mode = False
    examples_indent = 0

    def set_table(name: str) -> None:
        nonlocal current_table, columns_mode, current_column, examples_mode
        table = str(name or "").strip().strip('"').strip("'")
        if not table:
            return
        _flush_schema_column(table_columns, current_table=current_table, current_column=current_column)
        current_table = table
        table_columns.setdefault(normalize_sql_identifier(table), [])
        columns_mode = False
        current_column = None
        examples_mode = False

    def start_column(name: str, *, item_indent: int) -> None:
        nonlocal current_column, column_item_indent, examples_mode
        _flush_schema_column(table_columns, current_table=current_table, current_column=current_column)
        current_column = SchemaColumnMeta(name=name)
        column_item_indent = item_indent
        examples_mode = False

    def add_simple_column(name: str) -> None:
        nonlocal current_column
        if not current_table:
            return
        _flush_schema_column(table_columns, current_table=current_table, current_column=current_column)
        current_column = None
        key = normalize_sql_identifier(current_table)
        column = SchemaColumnMeta(name=name)
        normalized = normalize_sql_identifier(name)
        existing = {normalize_sql_identifier(item.name) for item in table_columns.setdefault(key, [])}
        if normalized and normalized not in existing:
            table_columns[key].append(column)

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        header_match = re.match(r"---\s*\[Schema:\d+\].*?\btable=([^\s]+)", stripped, flags=re.IGNORECASE)
        if header_match:
            set_table(header_match.group(1))
            continue

        indent = len(line) - len(line.lstrip())
        if columns_mode and indent <= columns_indent and not stripped.startswith("-") and not examples_mode:
            _flush_schema_column(table_columns, current_table=current_table, current_column=current_column)
            current_column = None
            columns_mode = False
            examples_mode = False

        table_match = re.match(r"table_name:\s*([A-Za-z_][\w.$]*)\s*$", stripped, flags=re.IGNORECASE)
        if table_match:
            set_table(table_match.group(1))
            continue

        inline_columns = re.match(r"columns:\s*(.+)$", stripped, flags=re.IGNORECASE)
        if inline_columns:
            _flush_schema_column(table_columns, current_table=current_table, current_column=current_column)
            current_column = None
            columns_mode = True
            columns_indent = indent
            examples_mode = False
            for column in split_schema_columns(inline_columns.group(1)):
                add_simple_column(column)
            continue

        if re.match(r"columns:\s*$", stripped, flags=re.IGNORECASE):
            _flush_schema_column(table_columns, current_table=current_table, current_column=current_column)
            current_column = None
            columns_mode = True
            columns_indent = indent
            examples_mode = False
            continue

        inline_name_match = re.match(
            r"-\s*name:\s*([A-Za-z_][\w.$]*)\s*,?\s*type:\s*(.+?)\s*$",
            stripped,
            flags=re.IGNORECASE,
        )
        if columns_mode and inline_name_match:
            start_column(inline_name_match.group(1), item_indent=indent)
            current_column.col_type = inline_name_match.group(2).strip().strip('"').strip("'")
            continue

        physical_match = re.match(r"-\s*physical_name:\s*([A-Za-z_][\w.$]*)\s*$", stripped, flags=re.IGNORECASE)
        name_match = re.match(r"-\s*name:\s*([A-Za-z_][\w.$]*)\s*$", stripped, flags=re.IGNORECASE)
        simple_column_match = re.match(r"-\s*([A-Za-z_][\w.$]*)\s*$", stripped)

        if columns_mode and examples_mode:
            example_item = re.match(r"-\s*(.+?)\s*$", stripped)
            if example_item and indent > examples_indent:
                if current_column is not None:
                    value = example_item.group(1).strip().strip('"').strip("'")
                    if value:
                        current_column.examples.append(value)
                continue
            if indent <= examples_indent:
                examples_mode = False

        type_match = re.match(r"type:\s*(.+?)\s*$", stripped, flags=re.IGNORECASE)
        if columns_mode and type_match and current_column is not None and indent > column_item_indent:
            current_column.col_type = type_match.group(1).strip().strip('"').strip("'")
            continue

        examples_match = re.match(r"examples:\s*(.*)$", stripped, flags=re.IGNORECASE)
        if columns_mode and examples_match and current_column is not None and indent > column_item_indent:
            inline_examples = examples_match.group(1).strip()
            if inline_examples:
                current_column.examples.extend(_parse_schema_examples_value(inline_examples))
                examples_mode = False
            else:
                examples_mode = True
                examples_indent = indent
            continue

        if columns_mode:
            match = physical_match or name_match or simple_column_match
            if match:
                if name_match or physical_match:
                    start_column(match.group(1), item_indent=indent)
                else:
                    add_simple_column(match.group(1))
                continue

        if physical_match:
            set_table(physical_match.group(1))

    _flush_schema_column(table_columns, current_table=current_table, current_column=current_column)

    return {
        table: columns
        for table, columns in table_columns.items()
        if columns
    }


def extract_schema_table_columns(output: Any) -> dict[str, list[str]]:
    return {
        table: [column.name for column in columns]
        for table, columns in extract_schema_table_column_meta(output).items()
    }


def build_schema_binding_summary(output: Any) -> str:
    table_columns = extract_schema_table_column_meta(output)
    if not table_columns:
        return ""
    lines = [
        "【Schema Binding 摘要】",
        "以下为本轮 SQL 允许优先绑定的物理表与字段，请先完成“业务含义 -> 物理字段”的映射再生成 SQL。",
        "字段后括号内为类型/样例值；写 WHERE 时必须与类型一致。",
    ]
    date_hints: list[str] = []
    for table, columns in list(table_columns.items())[:8]:
        visible_columns = ", ".join(_format_schema_column_binding(column) for column in columns[:60])
        if len(columns) > 60:
            visible_columns += f", ... 共 {len(columns)} 列"
        lines.append(f"- {table.upper()}: {visible_columns}")
        for column in columns[:60]:
            hint = _column_date_filter_hint(column)
            if hint:
                date_hints.append(f"{table.upper()}.{column.name}: {hint}")
    if date_hints:
        lines.append("日期字段写法：")
        for hint in date_hints[:12]:
            lines.append(f"  - {hint}")
    lines.append("约束：禁止使用未列出的字段；若用户口径无法绑定到上述字段，请先重查 Schema 或澄清，不要臆造英文列名。")
    return "\n".join(lines)


def mask_sql_literals_and_comments(sql: str) -> str:
    text = str(sql or "")
    chars = list(text)
    i = 0
    while i < len(chars):
        ch = chars[i]
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if ch == "-" and nxt == "-":
            chars[i] = chars[i + 1] = " "
            i += 2
            while i < len(chars) and chars[i] not in "\r\n":
                chars[i] = " "
                i += 1
            continue
        if ch == "/" and nxt == "*":
            chars[i] = chars[i + 1] = " "
            i += 2
            while i < len(chars):
                if chars[i] == "*" and i + 1 < len(chars) and chars[i + 1] == "/":
                    chars[i] = chars[i + 1] = " "
                    i += 2
                    break
                chars[i] = " "
                i += 1
            continue
        if ch == "'":
            chars[i] = " "
            i += 1
            while i < len(chars):
                chars[i] = " "
                if text[i] == "'" and i + 1 < len(chars) and text[i + 1] == "'":
                    chars[i + 1] = " "
                    i += 2
                    continue
                if text[i] == "'":
                    i += 1
                    break
                i += 1
            continue
        i += 1
    return "".join(chars)


_PREFLIGHT_RESERVED_ALIASES = {
    "where", "join", "left", "right", "inner", "outer", "full", "cross", "on",
    "group", "order", "having", "limit", "fetch", "union", "where",
}
_PREFLIGHT_TABLE_PATTERN = re.compile(
    r"\b(?:from|join)\s+([A-Za-z_][\w.$]*)(?:\s+(?:as\s+)?([A-Za-z_][\w$]*))?",
    flags=re.IGNORECASE,
)


def _preflight_cte_names(sql_for_preflight: str) -> set[str]:
    return {
        normalize_sql_identifier(item)
        for item in re.findall(
            r"(?:\bwith\b|,)\s*([A-Za-z_][\w$]*)\s+as\s*\(",
            sql_for_preflight,
            flags=re.IGNORECASE,
        )
    }


def _extract_preflight_table_refs_regex(sql: str) -> dict[str, str]:
    sql_for_preflight = mask_sql_literals_and_comments(sql)
    cte_names = _preflight_cte_names(sql_for_preflight)
    refs: dict[str, str] = {}
    for match in _PREFLIGHT_TABLE_PATTERN.finditer(sql_for_preflight):
        table = match.group(1).split(".")[-1]
        table_norm = normalize_sql_identifier(table)
        if table_norm in cte_names:
            continue
        refs[table_norm] = table
    return refs


def extract_preflight_physical_table_refs(
    sql: str,
    *,
    dialect: str | None = None,
) -> dict[str, str]:
    """提取 SQL 中的物理表引用；有 dialect 时优先 sqlglot，失败则回退 regex。"""
    if not sql:
        return {}

    if dialect:
        try:
            from app.services.sql_query_execution_service import extract_physical_table_refs_from_select_sql

            err, refs = extract_physical_table_refs_from_select_sql(sql, dialect)
            if not err and refs:
                return refs
        except Exception:
            pass

    return _extract_preflight_table_refs_regex(sql)


def collect_preflight_unknown_tables(
    sql: str,
    schema_table_columns: dict[str, list[str]],
    *,
    dialect: str | None = None,
) -> dict[str, str]:
    """
    提取 SQL 中引用、但不在本轮 get_dataset_schema 表列表中的物理表。
    返回 {table_norm -> SQL 展示名}。
    """
    if not sql or not schema_table_columns:
        return {}

    unknown: dict[str, str] = {}
    for table_norm, table in extract_preflight_physical_table_refs(sql, dialect=dialect).items():
        if table_norm in schema_table_columns:
            continue
        unknown[table_norm] = table
    return unknown


def build_sql_schema_preflight_error(
    sql: str,
    schema_table_columns: dict[str, list[str]],
    *,
    extra_allowed_tables: set[str] | None = None,
) -> str:
    if not sql or not schema_table_columns:
        return ""

    sql_for_preflight = mask_sql_literals_and_comments(sql)
    alias_to_table: dict[str, str] = {}
    table_displays: dict[str, str] = {}
    cte_names = _preflight_cte_names(sql_for_preflight)
    permission_allowed = extra_allowed_tables or set()
    for match in _PREFLIGHT_TABLE_PATTERN.finditer(sql_for_preflight):
        table = match.group(1).split(".")[-1]
        alias = match.group(2) or table
        alias_norm = normalize_sql_identifier(alias)
        table_norm = normalize_sql_identifier(table)
        if table_norm in cte_names:
            continue
        if alias_norm in _PREFLIGHT_RESERVED_ALIASES:
            alias_norm = table_norm
        if table_norm not in schema_table_columns:
            if table_norm not in permission_allowed:
                available_tables = ", ".join(sorted(schema_table_columns.keys())[:40])
                return (
                    "[TOOL_ERROR] SQL 预检失败：字段/表引用错误。"
                    f"表 {table} 不在 get_dataset_schema 返回的表列表中，"
                    f"且不在当前用户可访问的物理表权限集内。"
                    f"当前 Schema 可用表：{available_tables}。"
                    "请先重新调用 get_dataset_schema 核对物理 table_name，"
                    "禁止根据 DataQueryIntentFrame、业务术语或中文含义凭空猜测表名。"
                )
        alias_to_table[alias_norm] = table_norm
        alias_to_table[table_norm] = table_norm
        table_displays[table_norm] = table

    if not alias_to_table:
        return ""

    for qualifier, column in re.findall(r"\b([A-Za-z_][\w$]*)\.([A-Za-z_][\w$]*)\b", sql_for_preflight):
        qualifier_norm = normalize_sql_identifier(qualifier)
        table_norm = alias_to_table.get(qualifier_norm)
        if not table_norm:
            continue
        allowed_columns = schema_table_columns.get(table_norm) or []
        if not allowed_columns:
            continue
        allowed_norms = {normalize_sql_identifier(item) for item in allowed_columns}
        column_norm = normalize_sql_identifier(column)
        if column_norm not in allowed_norms:
            table_display = table_displays.get(table_norm) or table_norm
            available = ", ".join(allowed_columns[:40])
            return (
                "[TOOL_ERROR] SQL 预检失败：字段/表引用错误。"
                f'ORA-00904: "{qualifier.upper()}"."{column.upper()}": invalid identifier。'
                f"字段 {qualifier}.{column} 不在 get_dataset_schema 返回的表 {table_display} 字段列表中。"
                f"可用字段：{available}。请替换或删除该字段后再调用 execute_sql_query。"
            )
    return ""


def detect_sql_static_risk(sql: str) -> str:
    sql_text = str(sql or "").strip()
    if not sql_text:
        return "SQL 为空"

    sql_clean = mask_sql_literals_and_comments(sql_text).strip()

    sql_upper = " ".join(sql_clean.upper().split())
    if not sql_upper.startswith(("SELECT ", "WITH ")):
        return "只允许执行只读 SELECT 查询"

    if re.search(r"\bORDER\s+BY\b(?:(?!\bCASE\b)[\s\S]){0,400}\bAND\b[\s\S]{0,120}\b(ROWNUM|LIMIT)\b", sql_upper):
        return (
            "ORDER BY 后不能接 AND ROWNUM/LIMIT；"
            "Oracle TopN 请用子查询包一层排序后外层 ROWNUM，或 FETCH FIRST N ROWS ONLY；"
            "MySQL/ClickHouse 请用 ORDER BY ... LIMIT N"
        )

    if " JOIN " in f" {sql_upper} ":
        if " CROSS JOIN " not in f" {sql_upper} " and " NATURAL JOIN " not in f" {sql_upper} ":
            if not re.search(r"\bJOIN\b[\s\S]{1,400}\b(ON|USING)\b", sql_upper):
                return "JOIN 缺少明确 ON 或 USING 条件，存在笛卡尔积风险"

    return ""


def is_diagnostic_sql(sql: str) -> bool:
    sql_upper = " ".join(str(sql or "").upper().split())
    if any(x in sql_upper for x in ("SHOW TABLES", "SHOW COLUMNS", "DESCRIBE ", "DESC ")):
        return True
    if "SELECT DISTINCT" in sql_upper and "LIMIT" in sql_upper:
        return True
    if "COUNT(" in sql_upper and "GROUP BY" not in sql_upper:
        return True
    return False


def is_rag_not_synced(tool_output: Any) -> bool:
    text = str(tool_output or "")
    return "none are synced to RAG knowledge base" in text


def is_no_authorized_schema(tool_output: Any) -> bool:
    text = str(tool_output or "")
    return "No authorized datasets found" in text or "未找到相关的授权数据集" in text


def is_no_relevant_schema(tool_output: Any) -> bool:
    text = str(tool_output or "")
    return (
        "No relevant schema info found" in text
        or "未找到相关数据集定义" in text
        or "未找到相关的元数据" in text
    )


def is_schema_service_unavailable(tool_output: Any) -> bool:
    text = str(tool_output or "")
    if "[元数据服务不可用]" in text:
        return True
    normalized = text.lstrip()
    return normalized.startswith("[Tool Error]") or normalized.startswith("[TOOL_ERROR]")


def is_sql_fatal_error(text: str) -> bool:
    q = str(text or "").strip()
    if not q:
        return False
    fatal_prefixes = (
        "[Permission Denied]",
        "[Security Error]",
        "Error: Dataset",
    )
    if any(q.startswith(prefix) for prefix in fatal_prefixes):
        return True
    fatal_keywords = [
        "未在元数据中注册",
        "拒绝执行",
        "没有表",
        "权限不足",
        "表不存在",
        "table does not exist",
        "access denied",
        "permission denied",
    ]
    q_lower = q.lower()
    return any(kw in q_lower for kw in fatal_keywords)


def has_sql_plan(text: str) -> bool:
    if not text:
        return False
    return re.search(r"<sql_plan>\s*\{[\s\S]*?\}\s*</sql_plan>", text, flags=re.IGNORECASE) is not None


def should_require_sql_plan(user_question: str) -> bool:
    question = (user_question or "").strip().lower()
    if not question:
        return False
    high_risk_keywords = [
        "率", "占比", "比例", "比率", "负载", "利用率", "pue", "成功率", "转化率", "人均", "单价",
        "同比", "环比", "趋势", "变化", "增长", "下降",
        "top", "排名", "排行", "分组", "维度", "group", "join",
        "p95", "p90", "分位", "中位", "median", "percentile",
        "rate", "ratio", "percentage", "percent", "proportion",
        "trend", "growth", "decline", "change", "yoy", "mom",
        "ranking", "rank", "distribution", "utilization", "utilisation",
    ]
    if any(keyword in question for keyword in high_risk_keywords):
        return True
    return re.search(r"按.{0,12}(组|类|类型|维度|机房|区域|部门|用户|状态)", question) is not None
