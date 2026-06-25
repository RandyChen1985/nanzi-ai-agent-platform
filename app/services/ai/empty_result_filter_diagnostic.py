"""空结果筛选条件诊断：检测 WHERE 文本字面量、自动 DISTINCT 候选值、生成 repair/终态提示。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.services.ai.sql_dialect_limit import apply_dialect_row_limit

logger = logging.getLogger(__name__)

MAX_AUTO_FILTER_DIAGNOSTICS = 3
MAX_ALTERNATIVE_COLUMN_PROBES = 3
from app.services.ai.runners.chatbi.constants import MAX_PLATFORM_AUTO_SQL_RETRIES

MAX_AUTO_FILTER_BUSINESS_RETRIES = MAX_PLATFORM_AUTO_SQL_RETRIES
MAX_AUTO_FILTER_COLUMN_SWAP_RETAIN_VALUE_RETRIES = 2
DISTINCT_CANDIDATE_LIMIT = 20

_DIMENSION_COLUMN_HINTS = (
    "qy",
    "region",
    "area",
    "city",
    "province",
    "zone",
    "district",
    "org",
    "dept",
    "name",
    "mc",
    "lx",
    "type",
    "fl",
    "管辖",
    "区域",
    "地区",
    "省市",
)

_STRING_FILTER_FALLBACK_RE = re.compile(
    r"""(?ix)
    \bwhere\b
    (?:
        [^;]*?
        \b(?:[a-z_][\w$]*\.)?[a-z_][\w$]*\s*
        (?:=|<>|!=|like|ilike)\s*
        (['"])(?:(?!\1).)*\1
        |
        [^;]*?
        \b(?:[a-z_][\w$]*\.)?[a-z_][\w$]*\s+in\s*\(
        [^)]*['"][^'"]+['"]
        [^)]*\)
    )
    """,
)


@dataclass(frozen=True)
class StringFilterLiteral:
    column: str
    operator: str
    values: tuple[str, ...]
    table: str = ""


@dataclass(frozen=True)
class FilterCorrection:
    kind: str  # column_swap | value_swap
    column: str
    operator: str
    old_values: tuple[str, ...]
    new_column: str = ""
    new_values: tuple[str, ...] = ()


@dataclass
class AutoFilterRetryResult:
    attempted: bool = False
    corrected_sql: str = ""
    raw_output: str = ""
    parsed_output: Any = None
    has_rows: bool = False
    summary: str = ""
    error: str = ""


@dataclass
class FilterDiagnosticResult:
    column: str
    table: str
    operator: str
    used_values: tuple[str, ...]
    diagnostic_sql: str
    candidates: list[str] = field(default_factory=list)
    suggested_values: list[str] = field(default_factory=list)
    suspect_wrong_column: bool = False
    alternative_columns: list[str] = field(default_factory=list)
    matched_alternative_column: str = ""
    matched_alternative_values: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "table": self.table,
            "operator": self.operator,
            "used_values": list(self.used_values),
            "diagnostic_sql": self.diagnostic_sql,
            "candidates": self.candidates,
            "suggested_values": self.suggested_values,
            "suspect_wrong_column": self.suspect_wrong_column,
            "alternative_columns": self.alternative_columns,
            "matched_alternative_column": self.matched_alternative_column,
            "matched_alternative_values": self.matched_alternative_values,
            "error": self.error,
        }


def sql_has_string_literal_filters(sql: str, *, dialect: str = "clickhouse") -> bool:
    """A：SQL 是否含 WHERE 文本字面量筛选（= / LIKE / IN 等）。"""
    return bool(extract_string_filter_literals(sql, dialect=dialect))


def extract_string_filter_literals(sql: str, *, dialect: str = "clickhouse") -> list[StringFilterLiteral]:
    text = str(sql or "").strip()
    if not text:
        return []

    main_table = _resolve_main_table_name(text, dialect=dialect)
    parsed_filters: list[StringFilterLiteral] = []
    try:
        expression = sqlglot.parse_one(text, read=dialect)
    except (ParseError, ValueError, TypeError):
        return _extract_string_filter_literals_fallback(text, main_table)

    where = expression.find(exp.Where)
    if not where:
        return _extract_string_filter_literals_fallback(text, main_table)

    for node in where.find_all(exp.Predicate):
        extracted = _extract_predicate_literal(node, main_table)
        if extracted:
            parsed_filters.append(extracted)
    if parsed_filters:
        return _dedupe_filters(parsed_filters)
    return _extract_string_filter_literals_fallback(text, main_table)


def build_distinct_diagnostic_sql(
    *,
    table: str,
    column: str,
    limit: int = DISTINCT_CANDIDATE_LIMIT,
    dialect: str = "clickhouse",
) -> str:
    table_name = _quote_identifier(table, dialect=dialect)
    column_name = _quote_identifier(column, dialect=dialect)
    safe_limit = max(1, min(int(limit), DISTINCT_CANDIDATE_LIMIT))
    inner = f"SELECT DISTINCT {column_name} FROM {table_name}"
    return apply_dialect_row_limit(inner, dialect=dialect, limit=safe_limit, max_limit=DISTINCT_CANDIDATE_LIMIT)



def suggest_close_values(literal: str, candidates: list[str]) -> list[str]:
    needle = str(literal or "").strip()
    if not needle:
        return []
    suggestions: list[str] = []
    for candidate in candidates:
        value = str(candidate or "").strip()
        if not value or value == needle:
            continue
        if needle in value or value in needle:
            suggestions.append(value)
    return suggestions[:5]


def literal_matches_candidates(literal: str, candidates: list[str]) -> bool:
    needle = str(literal or "").strip()
    if not needle or not candidates:
        return False
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value == needle:
            return True
    return False


def suggest_alternative_filter_columns(
    *,
    table: str,
    used_column: str,
    schema_table_columns: dict[str, list[str]] | None,
    max_columns: int = MAX_ALTERNATIVE_COLUMN_PROBES + 1,
) -> list[str]:
    if not schema_table_columns:
        return []
    table_key = _normalize_identifier(table)
    used_key = _normalize_identifier(used_column)
    columns = schema_table_columns.get(table_key) or []
    if not columns:
        for key, values in schema_table_columns.items():
            if _normalize_identifier(key) == table_key:
                columns = values
                break
    scored: list[tuple[int, str]] = []
    for column in columns:
        column_name = str(column or "").strip()
        if not column_name:
            continue
        if _normalize_identifier(column_name) == used_key:
            continue
        score = _score_dimension_column(column_name)
        if score <= 0:
            continue
        scored.append((score, column_name))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [name for _, name in scored[: max(1, max_columns)]]


def should_escalate_empty_after_value_correction(diagnostics: list[dict[str, Any]] | None) -> bool:
    """修正筛选值后仍为空时，是否应升级为「疑似 WHERE 字段选错」。"""
    if not diagnostics:
        return False
    for item in diagnostics:
        if not isinstance(item, dict):
            continue
        if item.get("matched_alternative_column") or item.get("suspect_wrong_column"):
            return True
        if item.get("suggested_values"):
            return True
    return False


def build_automatic_filter_corrections(
    diagnostics: list[FilterDiagnosticResult],
) -> list[FilterCorrection]:
    """根据诊断结果构造平台自动改写 WHERE 的修正计划（优先高置信换字段+换值，其次换取值）。"""
    corrections: list[FilterCorrection] = []
    for item in diagnostics:
        if item.error:
            continue
        if item.matched_alternative_column:
            new_values = tuple(item.matched_alternative_values or item.suggested_values or item.used_values)
            corrections.append(
                FilterCorrection(
                    kind="column_swap",
                    column=item.column,
                    new_column=item.matched_alternative_column,
                    operator=item.operator,
                    old_values=item.used_values,
                    new_values=new_values or item.used_values,
                )
            )
            continue
        if item.suggested_values:
            corrections.append(
                FilterCorrection(
                    kind="value_swap",
                    column=item.column,
                    operator=item.operator,
                    old_values=item.used_values,
                    new_values=(item.suggested_values[0],),
                )
            )
    return corrections


def _looks_like_numeric_token(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return bool(re.fullmatch(r"-?\d+(?:\.\d+)?", text))


def _candidate_values_for_value_retry(
    item: FilterDiagnosticResult,
    *,
    limit: int = 3,
) -> list[str]:
    """字面量与库内候选不匹配时，优先用 suggested / 模糊匹配 / 样例候选做 value_swap。"""
    values: list[str] = []
    literal = str(item.used_values[0] if item.used_values else "").strip()

    for suggested in item.suggested_values:
        text = str(suggested or "").strip()
        if text and text not in values:
            values.append(text)
        if len(values) >= limit:
            return values[:limit]

    if literal and not literal_matches_candidates(literal, item.candidates):
        for close in suggest_close_values(literal, item.candidates):
            if close not in values:
                values.append(close)
            if len(values) >= limit:
                return values[:limit]

    if (not literal or not literal_matches_candidates(literal, item.candidates)) and item.candidates:
        for candidate in item.candidates:
            text = str(candidate or "").strip()
            if not text or text == literal or text in values:
                continue
            values.append(text)
            if len(values) >= limit:
                break
    return values[:limit]


def _should_allow_column_swap_retain_value(item: FilterDiagnosticResult) -> bool:
    """空字面量/数值列已有候选时，不盲目换字段并保留原条件值。"""
    literal = str(item.used_values[0] if item.used_values else "").strip()
    if not literal and item.candidates:
        return False
    if literal and item.candidates and all(_looks_like_numeric_token(c) for c in item.candidates[:3]):
        if not _looks_like_numeric_token(literal):
            return False
    return item.suspect_wrong_column or bool(item.alternative_columns)


def build_automatic_filter_retry_plans(
    diagnostics: list[FilterDiagnosticResult],
    *,
    sql: str,
    dialect: str = "clickhouse",
    max_plans: int = MAX_AUTO_FILTER_BUSINESS_RETRIES,
) -> list[tuple[list[FilterCorrection], str]]:
    """按优先级生成修正方案：高置信换字段+换值 → 换取值 → 保留原值的换字段（单独限次）。"""
    plans: list[tuple[list[FilterCorrection], str]] = []
    seen_sql: set[str] = {_normalize_sql(sql)}
    column_swap_retain_count = 0

    def add_plan(
        corrections: list[FilterCorrection],
        description: str,
        *,
        retain_value_column_swap: bool = False,
    ) -> None:
        nonlocal column_swap_retain_count
        if retain_value_column_swap and column_swap_retain_count >= MAX_AUTO_FILTER_COLUMN_SWAP_RETAIN_VALUE_RETRIES:
            return
        if len(plans) >= max_plans:
            return
        rewritten = rewrite_sql_with_filter_corrections(sql, corrections, dialect=dialect)
        if not rewritten:
            return
        key = _normalize_sql(rewritten)
        if key in seen_sql:
            return
        seen_sql.add(key)
        plans.append((corrections, description))
        if retain_value_column_swap:
            column_swap_retain_count += 1

    for item in diagnostics:
        if item.error:
            continue

        if item.matched_alternative_column:
            new_values = tuple(item.matched_alternative_values or item.suggested_values or item.used_values)
            old = item.used_values[0] if item.used_values else "?"
            new = new_values[0] if new_values else old
            add_plan(
                [
                    FilterCorrection(
                        kind="column_swap",
                        column=item.column,
                        new_column=item.matched_alternative_column,
                        operator=item.operator,
                        old_values=item.used_values,
                        new_values=new_values or item.used_values,
                    )
                ],
                f"第{{n}}次：字段 `{item.column}` → `{item.matched_alternative_column}`，条件值 `{old}` → `{new}`",
            )

        for suggested in _candidate_values_for_value_retry(item):
            old = item.used_values[0] if item.used_values else "?"
            add_plan(
                [
                    FilterCorrection(
                        kind="value_swap",
                        column=item.column,
                        operator=item.operator,
                        old_values=item.used_values,
                        new_values=(suggested,),
                    )
                ],
                f"第{{n}}次：字段 `{item.column}` 条件值 `{old}` → `{suggested}`",
            )

        if not _should_allow_column_swap_retain_value(item):
            continue

        for alt_column in item.alternative_columns:
            if _normalize_identifier(alt_column) == _normalize_identifier(item.matched_alternative_column):
                continue
            if _normalize_identifier(alt_column) == _normalize_identifier(item.column):
                continue
            old = item.used_values[0] if item.used_values else "?"
            add_plan(
                [
                    FilterCorrection(
                        kind="column_swap",
                        column=item.column,
                        new_column=alt_column,
                        operator=item.operator,
                        old_values=item.used_values,
                        new_values=item.used_values,
                    )
                ],
                f"第{{n}}次：尝试将字段 `{item.column}` 替换为 `{alt_column}`（保留条件值 `{old}`）",
                retain_value_column_swap=True,
            )
    return plans


def rewrite_sql_with_filter_corrections(
    sql: str,
    corrections: list[FilterCorrection],
    *,
    dialect: str = "clickhouse",
) -> str | None:
    if not sql or not corrections:
        return None
    try:
        expression = sqlglot.parse_one(sql, read=dialect)
    except (ParseError, ValueError, TypeError):
        return _rewrite_sql_with_filter_corrections_fallback(sql, corrections)

    where = expression.find(exp.Where)
    if not where:
        return _rewrite_sql_with_filter_corrections_fallback(sql, corrections)

    changed = False
    for correction in corrections:
        for predicate in list(where.find_all(exp.Predicate)):
            if _apply_filter_correction_to_predicate(predicate, correction):
                changed = True
    if not changed:
        return None
    return expression.sql(dialect=dialect)


def result_has_data_rows(parsed: Any) -> bool:
    row_lists = _extract_result_row_lists(parsed)
    return bool(row_lists and any(len(rows) > 0 for rows in row_lists))


async def run_automatic_filter_retry(
    *,
    sql: str,
    diagnostics: list[FilterDiagnosticResult],
    data_source: str,
    dataset_name: str,
    user_id: Optional[int],
    is_admin: bool,
    execute_sql,
    max_retries: int = MAX_AUTO_FILTER_BUSINESS_RETRIES,
) -> AutoFilterRetryResult:
    """平台自动改写 WHERE（换字段/换值）并重试最多 5 次业务 SQL（保留原值的换字段单独限 2 次）。"""
    from app.services.sql_query_execution_service import dialect_from_data_source

    dialect = dialect_from_data_source(data_source)
    retry_plans = build_automatic_filter_retry_plans(
        diagnostics,
        sql=sql,
        dialect=dialect,
        max_plans=max(1, int(max_retries)),
    )
    if not retry_plans:
        return AutoFilterRetryResult(error="未能生成有效的自动修正 SQL")

    last_result = AutoFilterRetryResult()
    summary_lines = ["【平台自动重试】"]

    for attempt_index, (corrections, description_template) in enumerate(retry_plans, start=1):
        corrected_sql = rewrite_sql_with_filter_corrections(sql, corrections, dialect=dialect)
        if not corrected_sql:
            continue
        description = description_template.format(n=attempt_index)
        summary_lines.append(description)

        try:
            raw = await execute_sql(
                sql=corrected_sql,
                data_source=data_source,
                dataset_name=dataset_name,
                user_id=user_id,
                is_admin=is_admin,
            )
        except Exception as exc:
            logger.warning(
                "[EmptyFilterDiagnostic] automatic filter retry failed (attempt %s): %s",
                attempt_index,
                exc,
            )
            last_result = AutoFilterRetryResult(
                attempted=True,
                corrected_sql=corrected_sql,
                summary="\n".join(summary_lines),
                error=str(exc)[:300],
            )
            continue

        if _looks_like_sql_error(raw):
            last_result = AutoFilterRetryResult(
                attempted=True,
                corrected_sql=corrected_sql,
                summary="\n".join(summary_lines),
                error=str(raw)[:500],
            )
            continue

        parsed = _try_parse_json(raw)
        has_rows = result_has_data_rows(parsed)
        summary = "\n".join(summary_lines)
        if has_rows:
            summary += f"；第 {attempt_index} 次重试已返回数据。"
            return AutoFilterRetryResult(
                attempted=True,
                corrected_sql=corrected_sql,
                raw_output=str(raw),
                parsed_output=parsed,
                has_rows=True,
                summary=summary,
            )

        last_result = AutoFilterRetryResult(
            attempted=True,
            corrected_sql=corrected_sql,
            raw_output=str(raw),
            parsed_output=parsed,
            has_rows=False,
            summary=summary + f"；第 {attempt_index} 次重试仍为空。",
        )

    if last_result.attempted:
        last_result.summary = (last_result.summary or "\n".join(summary_lines)) + (
            f"；共尝试 {min(len(retry_plans), max_retries)} 次业务 SQL 仍为空，请继续人工复核字段/条件。"
        )
        return last_result
    return AutoFilterRetryResult(error="未能生成有效的自动修正 SQL")


def format_repair_diagnostic_block(diagnostics: list[FilterDiagnosticResult]) -> str:
    if not diagnostics:
        return ""
    lines = ["【平台自动筛选诊断】已在空结果后自动查询字段候选值，请优先参考以下证据修正 SQL："]
    for item in diagnostics:
        used = "、".join(f"'{value}'" for value in item.used_values) or "（未知）"
        if item.error:
            lines.append(
                f"- 字段 `{item.column}`（条件 {item.operator} {used}）诊断失败：{item.error}"
            )
            continue
        if not item.candidates:
            lines.append(
                f"- 字段 `{item.column}`（条件 {item.operator} {used}）在表 `{item.table}` 中未查到任何候选值。"
            )
            continue
        preview = "、".join(f"'{value}'" for value in item.candidates[:8])
        if len(item.candidates) > 8:
            preview += " …"
        lines.append(
            f"- 字段 `{item.column}` 使用了 {used}，但库内候选值示例：{preview}"
        )
        if item.suggested_values:
            suggested = "、".join(f"'{value}'" for value in item.suggested_values)
            lines.append(f"  可能应改为：{suggested}")
        if item.matched_alternative_column:
            lines.append(
                f"  疑似 WHERE 字段选错：条件值在 `{item.matched_alternative_column}` 的候选值中更匹配，"
                f"平台将尝试自动改写为 `{item.matched_alternative_column}` 并重查 1 次。"
            )
        elif item.suspect_wrong_column:
            if item.alternative_columns:
                alt_preview = "、".join(f"`{name}`" for name in item.alternative_columns[:4])
                lines.append(
                    f"  当前字段 `{item.column}` 的候选值与口语条件不匹配，疑似筛错字段；"
                    f"请对照 Schema 尝试：{alt_preview}。"
                )
            else:
                lines.append(
                    f"  当前字段 `{item.column}` 无有效候选值或与条件不匹配，"
                    "请重新核对 get_dataset_schema 中该表的维度字段（term/physical_name）。"
                )
    lines.append(
        "请基于上述候选值或字段修正 WHERE 条件后，再执行 1 次最终业务 SQL；"
        "若修正取值后仍为空，优先怀疑 WHERE 字段是否选错，而不是直接判定为技术故障。"
    )
    return "\n".join(lines)


def format_empty_filter_guard_message(diagnostics: list[FilterDiagnosticResult]) -> str:
    if not diagnostics:
        return ""
    hints: list[str] = []
    for item in diagnostics:
        if item.error or not item.candidates:
            continue
        used = "、".join(f"「{value}」" for value in item.used_values) or "当前条件"
        preview = "、".join(f"「{value}」" for value in item.candidates[:6])
        hint = f"字段 `{item.column}` 使用了 {used}，库内常见取值为：{preview}"
        all_values_match = bool(item.used_values) and all(
            literal_matches_candidates(value, item.candidates) for value in item.used_values
        )
        if all_values_match and not item.suggested_values:
            hint += "；该取值在库内存在，空结果更可能由其他筛选条件、时间范围或 JOIN 导致"
        if item.suggested_values:
            suggested = "、".join(f"「{value}」" for value in item.suggested_values)
            hint += f"；您是否想查询 {suggested}？"
        if item.matched_alternative_column:
            hint += f"；条件可能应写在字段 `{item.matched_alternative_column}` 而不是 `{item.column}`"
        elif item.suspect_wrong_column and item.alternative_columns:
            alt_preview = "、".join(f"`{name}`" for name in item.alternative_columns[:3])
            hint += f"；也可能筛错了字段，可尝试 {alt_preview}"
        hints.append(hint)
    if not hints:
        return ""
    joined = "\n".join(f"{index}. {text}" for index, text in enumerate(hints, start=1))
    return (
        "SQL 已成功执行，但按当前筛选条件未返回数据。\n\n"
        f"平台已自动核对筛选字段候选值：\n{joined}\n\n"
        "💡 **建议您可以尝试**：\n"
        "1. 对照上方库内常见取值，确认筛选值是否完全一致（含大小写、空格、前后缀等）。\n"
        "2. 若取值看似正确仍无数据，请核对是否选错了 WHERE 字段（对照 Schema 中的维度字段 term）。\n"
        "3. 适当放宽或修正筛选条件后重新提问。\n"
        "4. 若仍无数据，可能确实不存在同时满足全部条件的记录。"
    )


def parse_distinct_values(parsed_output: Any, *, column: str) -> list[str]:
    if not isinstance(parsed_output, dict):
        return []
    column_names = _extract_column_names(parsed_output)
    target_idx = _resolve_column_index(column_names, column)
    values: list[str] = []
    for key in ("items", "rows", "data", "records"):
        rows = parsed_output.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            value = _extract_row_value(row, target_idx, column_names, column)
            if value is None:
                continue
            text = str(value).strip()
            if text and text not in values:
                values.append(text)
        if values:
            return values
    return values


async def run_empty_filter_diagnostics(
    *,
    sql: str,
    data_source: str,
    dataset_name: str,
    user_id: Optional[int],
    is_admin: bool,
    execute_sql,
    schema_table_columns: dict[str, list[str]] | None = None,
) -> list[FilterDiagnosticResult]:
    """B：对空结果 SQL 中的文本筛选字段自动执行 DISTINCT 诊断。"""
    from app.services.sql_query_execution_service import dialect_from_data_source

    dialect = dialect_from_data_source(data_source)
    filters = extract_string_filter_literals(sql, dialect=dialect)
    if not filters:
        return []

    main_table = filters[0].table or _resolve_main_table_name(sql, dialect=dialect)
    results: list[FilterDiagnosticResult] = []
    seen_columns: set[str] = set()

    for item in filters:
        column = item.column.strip()
        if not column or column in seen_columns:
            continue
        seen_columns.add(column)
        if len(results) >= MAX_AUTO_FILTER_DIAGNOSTICS:
            break

        table = item.table or main_table
        if not table:
            continue

        diagnostic_sql = build_distinct_diagnostic_sql(table=table, column=column, dialect=dialect)
        result = FilterDiagnosticResult(
            column=column,
            table=table,
            operator=item.operator,
            used_values=item.values,
            diagnostic_sql=diagnostic_sql,
        )
        try:
            raw = await execute_sql(
                sql=diagnostic_sql,
                data_source=data_source,
                dataset_name=dataset_name,
                user_id=user_id,
                is_admin=is_admin,
            )
        except Exception as exc:
            logger.warning("[EmptyFilterDiagnostic] diagnostic SQL failed: %s", exc)
            result.error = str(exc)[:300]
            results.append(result)
            continue

        if _looks_like_sql_error(raw):
            result.error = str(raw)[:500]
            results.append(result)
            continue

        parsed = _try_parse_json(raw)
        candidates = parse_distinct_values(parsed, column=column)
        result.candidates = candidates
        for literal in item.values:
            result.suggested_values.extend(suggest_close_values(literal, candidates))
        result.suggested_values = _dedupe_preserve(result.suggested_values)[:5]
        await _maybe_probe_alternative_columns(
            result,
            literals=item.values,
            schema_table_columns=schema_table_columns,
            data_source=data_source,
            dataset_name=dataset_name,
            user_id=user_id,
            is_admin=is_admin,
            execute_sql=execute_sql,
            dialect=dialect,
        )
        results.append(result)

    return results


async def _maybe_probe_alternative_columns(
    result: FilterDiagnosticResult,
    *,
    literals: tuple[str, ...],
    schema_table_columns: dict[str, list[str]] | None,
    data_source: str,
    dataset_name: str,
    user_id: Optional[int],
    is_admin: bool,
    execute_sql,
    dialect: str = "clickhouse",
) -> None:
    needs_probe = not result.candidates
    if not needs_probe:
        for literal in literals:
            if not literal_matches_candidates(literal, result.candidates) and not suggest_close_values(
                literal, result.candidates
            ):
                needs_probe = True
                break
    if not needs_probe:
        return

    alternatives = suggest_alternative_filter_columns(
        table=result.table,
        used_column=result.column,
        schema_table_columns=schema_table_columns,
    )
    result.alternative_columns = alternatives
    if not alternatives:
        result.suspect_wrong_column = not result.candidates
        return

    for alt_column in alternatives[:MAX_ALTERNATIVE_COLUMN_PROBES]:
        diagnostic_sql = build_distinct_diagnostic_sql(table=result.table, column=alt_column, dialect=dialect)
        try:
            raw = await execute_sql(
                sql=diagnostic_sql,
                data_source=data_source,
                dataset_name=dataset_name,
                user_id=user_id,
                is_admin=is_admin,
            )
        except Exception as exc:
            logger.warning("[EmptyFilterDiagnostic] alternative column probe failed: %s", exc)
            continue
        if _looks_like_sql_error(raw):
            continue
        alt_candidates = parse_distinct_values(_try_parse_json(raw), column=alt_column)
        for literal in literals:
            if literal_matches_candidates(literal, alt_candidates) or suggest_close_values(literal, alt_candidates):
                result.matched_alternative_column = alt_column
                result.matched_alternative_values = _resolve_best_values_for_literal(literal, alt_candidates)
                result.suspect_wrong_column = True
                return

    result.suspect_wrong_column = True


def looks_like_generic_sql_failure_reply(text: str) -> bool:
    """D：识别模型把空结果误说成通用 SQL 技术故障的回复。"""
    normalized = str(text or "").strip()
    if not normalized:
        return False
    markers = (
        "数据查询遇到了一些技术问题",
        "暂时无法获取结果",
        "底层服务正在维护",
        "technical problem",
        "technical issue",
    )
    return any(marker in normalized for marker in markers)


def _extract_predicate_literal(node: exp.Expression, main_table: str) -> StringFilterLiteral | None:
    if isinstance(node, exp.EQ):
        return _binary_literal(node.this, node.expression, "=", main_table)
    if isinstance(node, exp.NEQ):
        return _binary_literal(node.this, node.expression, "!=", main_table)
    if isinstance(node, (exp.Like, exp.ILike)):
        operator = "LIKE" if isinstance(node, exp.Like) else "ILIKE"
        return _binary_literal(node.this, node.expression, operator, main_table)
    if isinstance(node, exp.In):
        column = _column_name(node.this)
        if not column:
            return None
        values = tuple(
            value
            for value in (_string_literal(item) for item in node.expressions)
            if value is not None
        )
        if not values:
            return None
        return StringFilterLiteral(column=column, operator="IN", values=values, table=main_table)
    return None


def _binary_literal(left: exp.Expression, right: exp.Expression, operator: str, main_table: str) -> StringFilterLiteral | None:
    column = _column_name(left)
    literal = _string_literal(right)
    if column and literal is not None:
        return StringFilterLiteral(column=column, operator=operator, values=(literal,), table=main_table)
    column = _column_name(right)
    literal = _string_literal(left)
    if column and literal is not None:
        return StringFilterLiteral(column=column, operator=operator, values=(literal,), table=main_table)
    return None


def _column_name(node: exp.Expression | None) -> str | None:
    if isinstance(node, exp.Column):
        parts = [str(part).strip() for part in node.parts if str(part).strip()]
        if parts:
            return parts[-1]
        name = str(getattr(node, "name", "") or "").strip()
        return name or None
    return None


def _string_literal(node: exp.Expression | None) -> str | None:
    if isinstance(node, exp.Literal) and node.is_string:
        return str(node.this)
    return None


def _resolve_main_table_name(sql: str, *, dialect: str) -> str:
    try:
        expression = sqlglot.parse_one(sql, read=dialect)
    except (ParseError, ValueError, TypeError):
        return _resolve_main_table_name_fallback(sql)
    from_clause = expression.find(exp.From)
    if not from_clause:
        return _resolve_main_table_name_fallback(sql)
    table = from_clause.find(exp.Table)
    if isinstance(table, exp.Table):
        return _table_physical_name(table)
    return _resolve_main_table_name_fallback(sql)


def _table_physical_name(table: exp.Table) -> str:
    parts = [str(part).strip() for part in table.parts if str(part).strip()]
    if parts:
        return parts[-1]
    return str(getattr(table, "name", "") or "").strip()


def _resolve_main_table_name_fallback(sql: str) -> str:
    match = re.search(r"(?is)\bfrom\s+([`\"(\[]?[a-z_][\w$]*[`\"\])]?)", str(sql or ""))
    if not match:
        return ""
    return match.group(1).strip("[]()`\"")


def _extract_string_filter_literals_fallback(sql: str, main_table: str) -> list[StringFilterLiteral]:
    if not _STRING_FILTER_FALLBACK_RE.search(str(sql or "")):
        return []
    filters: list[StringFilterLiteral] = []
    eq_like = re.finditer(
        r"(?is)\b([a-z_][\w$]*)\s*(=|<>|!=|like|ilike)\s*['\"]([^'\"]*)['\"]",
        str(sql or ""),
    )
    for match in eq_like:
        filters.append(
            StringFilterLiteral(
                column=match.group(1),
                operator=match.group(2).upper(),
                values=(match.group(3),),
                table=main_table,
            )
        )
    return _dedupe_filters(filters)


def _dedupe_filters(filters: list[StringFilterLiteral]) -> list[StringFilterLiteral]:
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    deduped: list[StringFilterLiteral] = []
    for item in filters:
        key = (item.column.lower(), item.operator.upper(), item.values)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _quote_identifier(name: str, dialect: str = "clickhouse") -> str:
    cleaned = str(name or "").strip()
    if not cleaned:
        return cleaned
    if re.fullmatch(r"[a-z_][\w$]*", cleaned, flags=re.IGNORECASE):
        return cleaned
    if dialect == "oracle":
        escaped = cleaned.replace('"', '""')
        return f'"{escaped}"'
    escaped = cleaned.replace("`", "``")
    return f"`{escaped}`"


def _looks_like_sql_error(raw: Any) -> bool:
    text = str(raw or "").strip()
    if not text:
        return True
    if text.startswith(("[TOOL_ERROR]", "[Validation Failed]", "[Permission Denied]", "[Security Error]", "Error:")):
        return True
    parsed = _try_parse_json(text)
    if isinstance(parsed, dict) and any(key in parsed for key in ("columns", "items", "rows", "data", "records")):
        return False
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in ("syntax error", "unknown column", "unknown table", "validation failed", "permission denied")
    )


def _try_parse_json(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _extract_column_names(parsed: dict[str, Any]) -> list[str]:
    columns = parsed.get("columns")
    if not isinstance(columns, list):
        return []
    names: list[str] = []
    for item in columns:
        if isinstance(item, dict):
            name = item.get("name") or item.get("field") or item.get("column")
        else:
            name = item
        names.append(str(name or "").strip())
    return names


def _resolve_column_index(column_names: list[str], column: str) -> int | None:
    target = str(column or "").strip().lower()
    for index, name in enumerate(column_names):
        if str(name or "").strip().lower() == target:
            return index
    if len(column_names) == 1:
        return 0
    return None


def _extract_row_value(row: Any, target_idx: int | None, column_names: list[str], column: str) -> Any:
    if isinstance(row, dict):
        if column in row:
            return row.get(column)
        lowered = str(column or "").strip().lower()
        for key, value in row.items():
            if str(key or "").strip().lower() == lowered:
                return value
        if len(row) == 1:
            return next(iter(row.values()))
        return None
    if isinstance(row, list):
        if target_idx is not None and target_idx < len(row):
            return row[target_idx]
        if len(row) == 1:
            return row[0]
    return None


def _dedupe_preserve(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        key = str(value)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return deduped


def _normalize_identifier(name: str) -> str:
    return re.sub(r"[`\"'\[\]]", "", str(name or "").strip()).lower()


def _score_dimension_column(column_name: str) -> int:
    lowered = str(column_name or "").strip().lower()
    if not lowered:
        return 0
    score = 0
    for hint in _DIMENSION_COLUMN_HINTS:
        hint_lower = hint.lower()
        if hint_lower in lowered:
            score += 3 if len(hint_lower) >= 3 else 2
    if lowered.endswith("_name") or lowered.endswith("_mc"):
        score += 1
    return score


def _resolve_best_values_for_literal(literal: str, candidates: list[str]) -> list[str]:
    needle = str(literal or "").strip()
    if not needle:
        return []
    if literal_matches_candidates(needle, candidates):
        return [needle]
    close = suggest_close_values(needle, candidates)
    return close[:1]


def _normalize_sql(sql: str) -> str:
    return " ".join(str(sql or "").split()).lower()


def _extract_result_row_lists(parsed: Any, depth: int = 0) -> list[list[Any]]:
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
            row_lists.extend(_extract_result_row_lists(value, depth + 1))
    return row_lists


def _apply_filter_correction_to_predicate(predicate: exp.Expression, correction: FilterCorrection) -> bool:
    if isinstance(predicate, exp.In):
        return _apply_filter_correction_to_in(predicate, correction)
    if isinstance(predicate, (exp.EQ, exp.NEQ, exp.Like, exp.ILike)):
        return _apply_filter_correction_to_binary(predicate, correction)
    return False


def _apply_filter_correction_to_binary(
    predicate: exp.Expression,
    correction: FilterCorrection,
) -> bool:
    left = getattr(predicate, "this", None)
    right = getattr(predicate, "expression", None)
    if not _predicate_matches_correction(left, right, correction):
        if not _predicate_matches_correction(right, left, correction):
            return False
        left, right = right, left
    if correction.kind == "column_swap" and correction.new_column:
        _replace_column_node(left, correction.new_column)
    new_value = _pick_replacement_value(correction)
    if new_value is not None and isinstance(right, exp.Literal) and right.is_string:
        right.replace(exp.Literal.string(new_value))
        return True
    return correction.kind == "column_swap"


def _apply_filter_correction_to_in(predicate: exp.In, correction: FilterCorrection) -> bool:
    if _normalize_identifier(_column_name(predicate.this) or "") != _normalize_identifier(correction.column):
        return False
    if correction.kind == "column_swap" and correction.new_column:
        _replace_column_node(predicate.this, correction.new_column)
    if correction.new_values:
        new_literals = [exp.Literal.string(value) for value in correction.new_values]
        predicate.set("expressions", new_literals)
        return True
    return correction.kind == "column_swap"


def _predicate_matches_correction(
    column_node: exp.Expression | None,
    literal_node: exp.Expression | None,
    correction: FilterCorrection,
) -> bool:
    if _normalize_identifier(_column_name(column_node) or "") != _normalize_identifier(correction.column):
        return False
    literal = _string_literal(literal_node)
    if literal is None:
        return correction.operator.upper() != "IN"
    if not correction.old_values:
        return True
    return literal in correction.old_values


def _pick_replacement_value(correction: FilterCorrection) -> str | None:
    if correction.new_values:
        return str(correction.new_values[0])
    if correction.old_values:
        return str(correction.old_values[0])
    return None


def _replace_column_node(node: exp.Expression | None, new_column: str) -> None:
    if not isinstance(node, exp.Column):
        return
    table_parts = [str(part).strip() for part in node.parts[:-1] if str(part).strip()]
    if table_parts:
        node.set("this", exp.to_table(table_parts[-1] if len(table_parts) == 1 else ".".join(table_parts)))
        node.set("table", exp.to_table(".".join(table_parts)))
        node.set("name", new_column)
    node.set("this", exp.to_identifier(new_column))


def _rewrite_sql_with_filter_corrections_fallback(
    sql: str,
    corrections: list[FilterCorrection],
) -> str | None:
    rewritten = str(sql or "")
    changed = False
    for correction in corrections:
        old_value = correction.old_values[0] if correction.old_values else None
        new_value = _pick_replacement_value(correction)
        if correction.kind == "column_swap" and correction.new_column:
            pattern = rf"(?is)(\b{re.escape(correction.column)}\s*(?:=|<>|!=|like|ilike)\s*(['\"]))"
            replacement = rf"\1{correction.new_column} \2"  # wrong order
            # simpler: replace column name before operator when followed by old literal
            if old_value is not None and new_value is not None:
                old_fragment = (
                    rf"\b{re.escape(correction.column)}\s*=\s*(['\"]){re.escape(old_value)}\1"
                )
                new_fragment = f"{correction.new_column} = '{new_value}'"
                updated, count = re.subn(old_fragment, new_fragment, rewritten)
                if count:
                    rewritten = updated
                    changed = True
                    continue
            col_pattern = rf"(?is)\b{re.escape(correction.column)}(\s*(?:=|<>|!=|like|ilike)\s*)"
            updated, count = re.subn(col_pattern, rf"{correction.new_column}\1", rewritten, count=1)
            if count:
                rewritten = updated
                changed = True
        elif correction.kind == "value_swap" and old_value and new_value:
            pattern = rf"(?is)(\b{re.escape(correction.column)}\s*=\s*(['\"])){re.escape(old_value)}\2"
            updated, count = re.subn(pattern, rf"\1{new_value}\2", rewritten, count=1)
            if count:
                rewritten = updated
                changed = True
    return rewritten if changed else None
