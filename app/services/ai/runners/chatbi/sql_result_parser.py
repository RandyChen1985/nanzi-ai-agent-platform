"""Parse and analyze execute_sql_query tool outputs."""

from __future__ import annotations

import ast
import json
import re
from typing import Any

from app.services.ai.runners.chatbi.constants import (
    DELAY_SECONDS_EXTREME_THRESHOLD,
    SCHEMA_GATE_PREFIX,
    SQL_PLAN_GATE_PREFIX,
)
from app.services.ai.runners.chatbi.run_state import DataRunState


def try_parse_json_output(tool_output: Any) -> Any:
    if isinstance(tool_output, (list, dict)):
        return tool_output
    if not isinstance(tool_output, str):
        return tool_output
    text = tool_output.strip()
    if not text or text[0] not in "[{":
        return tool_output
    try:
        return json.loads(text)
    except Exception:
        if len(text) < 5000:
            try:
                return ast.literal_eval(text)
            except Exception:
                pass
        return tool_output


def extract_result_row_lists(parsed: Any, depth: int = 0) -> list[list[Any]]:
    if depth > 4:
        return []
    if isinstance(parsed, list):
        return [parsed]
    if not isinstance(parsed, dict):
        return []
    row_lists: list[list[Any]] = []
    for key, value in parsed.items():
        if str(key) not in {"items", "rows", "data", "list", "result", "records"}:
            continue
        if isinstance(value, list):
            row_lists.append(value)
        elif isinstance(value, dict):
            row_lists.extend(extract_result_row_lists(value, depth + 1))
    return row_lists


def result_has_data_rows(parsed: Any) -> bool:
    row_lists = extract_result_row_lists(parsed)
    return bool(row_lists and any(len(rows) > 0 for rows in row_lists))


def detect_empty_result(parsed: Any) -> str | None:
    row_lists = extract_result_row_lists(parsed)
    if row_lists and not any(len(rows) > 0 for rows in row_lists):
        return "SQL 返回的行容器为空，未命中任何数据行"
    if isinstance(parsed, dict):
        total_like_keys = ("total", "count", "total_count")
        if (
            any(parsed.get(k) == 0 for k in total_like_keys)
            and row_lists
            and not result_has_data_rows(parsed)
        ):
            return "SQL 返回 total/count=0，且未命中任何数据行"
    return None


def is_structured_sql_result(parsed: Any) -> bool:
    if isinstance(parsed, list):
        return True
    if not isinstance(parsed, dict):
        return False
    if any(key in parsed for key in ("columns", "items", "rows", "data", "records")):
        return True
    return bool(extract_result_row_lists(parsed))


def detect_sql_error(output: Any) -> tuple[bool, str]:
    text = str(output or "")
    if not text.strip():
        return False, ""

    error_prefixes = (
        "[TOOL_ERROR]",
        "[Validation Failed]",
        "[Permission Denied]",
        "[Security Error]",
        f"{SCHEMA_GATE_PREFIX}",
        f"{SQL_PLAN_GATE_PREFIX}",
        "Error: Dataset",
    )
    if any(text.startswith(prefix) for prefix in error_prefixes):
        return True, text[:1000]

    parsed = try_parse_json_output(output)
    if is_structured_sql_result(parsed):
        return False, ""

    error_patterns = [
        r"unknown column",
        r"unknown table",
        r"syntax error",
        r"sql syntax",
        r"access denied",
        r"permission denied",
        r"unauthorized",
        r"SQL Syntax Error",
        r"SQL Validation Failed",
        r"\bORA-\d{3,5}\b",
        r"invalid identifier",
        r"invalid number",
        r"no such (?:column|table)",
        r"does not exist",
        r"not a group by",
        r"cannot parse",
        r"literal does not match",
        r"\btimed?\s*out\b",
        r"\btimeout\b",
        r"lock wait",
        r"connection (?:refused|reset|closed|error)",
        r"division by zero",
        r"\bCode:\s*\d+\.\s*DB::Exception",
    ]
    if any(re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE) for pattern in error_patterns):
        return True, text[:1000]
    return False, ""


def is_trusted_empty_result(sql: str, state: DataRunState) -> bool:
    if state.requires_sql_plan:
        return False
    sql_upper = " ".join(str(sql or "").upper().split())
    if not sql_upper:
        return False
    complex_markers = (
        " JOIN ",
        " GROUP BY ",
        " HAVING ",
        " UNION ",
        " INTERSECT ",
        " EXCEPT ",
        " WITH ",
        " OVER ",
    )
    if any(marker in f" {sql_upper} " for marker in complex_markers):
        return False
    ratio_markers = (
        "RATE",
        "RATIO",
        "PCT",
        "PERCENT",
        "UTILIZATION",
        "利用率",
        "占比",
        "比率",
        "比例",
    )
    if any(marker in sql_upper for marker in ratio_markers):
        return False
    from app.services.ai.empty_result_filter_diagnostic import sql_has_string_literal_filters

    if sql_has_string_literal_filters(sql):
        return False
    return True


def extract_column_names(parsed: dict[str, Any]) -> list[str]:
    columns = parsed.get("columns")
    if not isinstance(columns, list):
        return []
    names: list[str] = []
    for item in columns:
        if isinstance(item, dict):
            name = item.get("name") or item.get("field") or item.get("column")
        else:
            name = item
        if name is None:
            names.append("")
        else:
            names.append(str(name))
    return names


def iter_named_result_rows(parsed: Any, depth: int = 0) -> list[dict[str, Any]]:
    if depth > 3:
        return []
    if isinstance(parsed, list):
        return [row for row in parsed if isinstance(row, dict)]
    if not isinstance(parsed, dict):
        return []

    rows: list[dict[str, Any]] = []
    column_names = extract_column_names(parsed)
    for key in ("items", "rows", "data", "records"):
        value = parsed.get(key)
        if isinstance(value, list):
            for row in value:
                if isinstance(row, dict):
                    rows.append(row)
                elif isinstance(row, list) and column_names:
                    rows.append(
                        {
                            column_names[index]: cell
                            for index, cell in enumerate(row)
                            if index < len(column_names) and column_names[index]
                        }
                    )
            if rows:
                return rows
        elif isinstance(value, dict):
            rows.extend(iter_named_result_rows(value, depth + 1))
            if rows:
                return rows
    return rows


def is_duration_like_column(column: str) -> bool:
    name = str(column or "").strip().lower()
    if not name:
        return False
    return bool(
        re.search(
            r"(interval|duration|latency|delay|lag|elapsed|time[_-]?diff|timediff|"
            r"diff[_-]?(seconds|minutes|hours|ms)|age[_-]?(seconds|minutes|hours)|"
            r"seconds|minutes|hours|milliseconds|"
            r"时延|延迟|耗时|时长|时间差|间隔|秒|分钟|小时)",
            name,
            flags=re.IGNORECASE,
        )
    )


def is_delay_like_column(column: str) -> bool:
    name = str(column or "").strip().lower()
    return bool(re.search(r"(latency|delay|lag|延迟|时延|滞后)", name, flags=re.IGNORECASE))


def detect_duration_anomaly(parsed: Any) -> tuple[bool, str]:
    rows = iter_named_result_rows(parsed)
    if not rows:
        return False, ""

    for row in rows[:50]:
        for column, value in row.items():
            if not is_duration_like_column(str(column)):
                continue
            if isinstance(value, bool):
                continue
            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                continue
            if numeric_value < 0:
                return True, (
                    f"字段 `{column}` 值为 {numeric_value:g}，时间差/时延/时长不应为负值，"
                    "疑似时间字段相减方向、时区或单位换算错误"
                )
            if is_delay_like_column(str(column)) and numeric_value > DELAY_SECONDS_EXTREME_THRESHOLD:
                return True, (
                    f"字段 `{column}` 值为 {numeric_value:g} 秒，超过 7 天，"
                    "疑似延迟计算口径、时区或单位换算错误"
                )
    return False, ""


def detect_ratio_anomaly(parsed: Any) -> tuple[bool, str]:
    ratio_col_pattern = re.compile(
        r"(rate|ratio|pct|percent|proportion|utilization|utilisation|"
        r"\u7387|\u5360\u6bd4|\u6bd4\u4f8b|\u8d1f\u8f7d\u7387|\u5229\u7528\u7387|\u6210\u529f\u7387|\u8f6c\u5316\u7387|\u5b8c\u6210\u7387)",
        re.IGNORECASE,
    )
    rows = iter_named_result_rows(parsed)
    if not rows:
        return False, ""

    for row in rows[:50]:
        for col, val in row.items():
            if not ratio_col_pattern.search(str(col)):
                continue
            try:
                fval = float(val)
            except (TypeError, ValueError):
                continue
            if fval > 1.5:
                return True, f"字段 `{col}` 值为 {fval:.4f}（超过 150%，疑似 JOIN 放大或分母错误）"
            if fval < -0.1:
                return True, f"字段 `{col}` 值为 {fval:.4f}（出现负值，疑似计算口径错误）"
    return False, ""
