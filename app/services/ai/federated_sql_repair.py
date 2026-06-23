"""联邦查询（FederatedQueryExecutor）专用的 SQL 错误检测与局部 repair 指引。

仅由 app.services.ai.executors.federated_executor 引用；
单源 ChatBI 仍使用 DataAgentRunner 内独立实现，与本模块无依赖。
"""
from __future__ import annotations

import ast
import json
import re
from typing import Any, Tuple

from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.ai.time_anchor import TIME_RANGE_GATE_PREFIX, build_data_query_time_anchor_block

SCHEMA_GATE_PREFIX = "[SCHEMA_GATE]"
SQL_PLAN_GATE_PREFIX = "[SQL_PLAN_GATE]"


def normalize_sql_text(sql: str) -> str:
    return " ".join(str(sql or "").strip().lower().split())


def build_repair_schema_search_keywords(
    failed_sql: str,
    *,
    dataset_name: str = "",
    error_text: str = "",
    sql_dialect: str = "clickhouse",
) -> str:
    """从失败 SQL / 错误信息 / 数据集名构造 get_dataset_schema(keywords) 检索词。"""
    parts: list[str] = []
    seen: set[str] = set()

    def _add(token: str) -> None:
        value = str(token or "").strip()
        if not value:
            return
        key = value.lower()
        if key in seen:
            return
        seen.add(key)
        parts.append(value)

    if dataset_name:
        _add(dataset_name)

    try:
        from app.services.sql_query_execution_service import extract_physical_table_refs_from_select_sql

        _, refs = extract_physical_table_refs_from_select_sql(failed_sql, sql_dialect)
        for table in refs.values():
            _add(table)
    except Exception:
        pass

    for ident in extract_invalid_sql_identifiers(error_text):
        _add(ident)
        if "." in ident:
            _add(ident.rsplit(".", 1)[-1])

    return " ".join(parts[:12])


def merge_repair_schema_snippets(
    base_snippet: str,
    refreshed_snippet: str,
    *,
    max_chars: int = 5000,
) -> str:
    """合并 prefetch Schema 片段与 repair 时按需检索到的 Schema。"""
    base = str(base_snippet or "").strip()
    refreshed = str(refreshed_snippet or "").strip()
    if not refreshed:
        return base[:max_chars] if base else ""
    skip_markers = (
        "[Tool Error]",
        "No relevant schema info found",
        "No authorized datasets found",
        "metadata service unavailable",
    )
    if any(marker.lower() in refreshed.lower() for marker in skip_markers):
        return base[:max_chars] if base else ""
    if refreshed in base:
        return base[:max_chars] if base else refreshed[:max_chars]
    marker = "【repair 按需 get_dataset_schema 补充】"
    combined = f"{base}\n\n{marker}\n{refreshed}".strip() if base else f"{marker}\n{refreshed}"
    if len(combined) > max_chars:
        combined = combined[:max_chars] + "\n... [Schema 片段已截断]"
    return combined


def is_cross_dataset_scope_sql_error(message: Any) -> bool:
    text = str(message or "")
    if not text.strip():
        return False
    return (
        "不属于当前指定的数据集" in text
        or "普通 execute_sql_query 严禁跨数据集" in text
    )


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
        r"does not have a column",
        r"binder error",
        r"未 select 的字段",
    )
    return any(re.search(pattern, err) for pattern in patterns)


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


def invalid_identifier_repair_hint(message: str) -> str:
    identifiers = extract_invalid_sql_identifiers(message)
    if not identifiers:
        return ""
    return (
        "\n\n【无效标识符定位】本次数据库明确报出的无效字段/标识符："
        f"{', '.join(identifiers)}。请在 Schema 返回的物理列名中逐项核对；"
        "若 schema 未列出这些字段，必须删除或替换为真实物理列，"
        "并同步修正 SELECT、JOIN、WHERE、GROUP BY、ORDER BY 中所有引用，"
        "不得继续使用这些字段名。"
    )


def extract_cross_dataset_violation(message: str) -> tuple[str, str]:
    """从 Validation Failed 文案解析违规表名与当前 dataset。"""
    text = str(message or "")
    match = re.search(
        r"表\s*'([^']+)'\s*不属于当前指定的数据集\s*'([^']+)'",
        text,
    )
    if not match:
        return "", ""
    return match.group(1).strip(), match.group(2).strip()


def cross_dataset_scope_repair_hint(message: str) -> str:
    if not is_cross_dataset_scope_sql_error(message):
        return ""
    foreign_table, dataset_name = extract_cross_dataset_violation(message)
    table_clause = (
        f"（违规表 `{foreign_table}` 不属于 `{dataset_name}`）"
        if foreign_table and dataset_name
        else ""
    )
    return (
        "\n\n【跨数据集 SQL 修正要求 — 必须整计划重构，禁止局部修补】"
        f"{table_clause}\n"
        "1) 每个 `<sub_query>` 的 SQL 只能引用该 `dataset_name` 下已注册的物理表；"
        "禁止 IN/JOIN/EXISTS 子查询引用其他数据集表（例如 HR_ds 里写 VIEW_AI_VISIT_LOG）。\n"
        "2) 正确做法：为违规表单独增加一个 sub_query（归属其真实 dataset），"
        "在 `<memory_join>` 用 DuckDB 对临时表做 JOIN / IN 过滤。\n"
        "3) 示例：拜访明细在 crm_ds、人员在 HR_ds 时，应拆成 "
        "`crm_ds: SELECT follow_up_person FROM visit_view WHERE ...` + "
        "`HR_ds: SELECT id, name FROM HRMRESOURCE WHERE status=0`，"
        "再在 memory_join: `FROM t_visit v JOIN t_hr h ON v.follow_up_person = h.id`。\n"
        "4) 不得通过删 WHERE 条件、写死 ID 列表或猜测表名绕过门禁。"
    )


def sql_repair_taxonomy_hint(message: str) -> str:
    text = str(message or "")
    lower = text.lower()
    if is_cross_dataset_scope_sql_error(text):
        category = "cross_dataset_scope"
        focus = (
            "为违规表新增对应 dataset 的 sub_query，在 memory_join 关联；"
            "禁止在单 sub_query 内 IN/JOIN 其他数据集表"
        )
    elif is_date_format_sql_error(text):
        category = "date_format"
        focus = "核对日期字段类型与 TO_DATE/TO_CHAR/DATE 字面量；字符串列勿对列套 TO_CHAR"
    elif is_invalid_number_sql_error(text):
        category = "invalid_number"
        focus = "核对 VARCHAR 日期列是否误用 TO_CHAR/TO_NUMBER；改用字符串区间或 DATE 字面量范围"
    elif is_schema_reference_sql_error(text):
        category = "invalid_identifier"
        focus = "核对字段名、表名或别名引用"
    elif "not a group by" in lower or "group by expression" in lower or "ora-00979" in lower:
        category = "group_by_mismatch"
        focus = "核对 SELECT 中非聚合字段是否全部进入 GROUP BY，或改为聚合表达式"
    elif "join" in lower and ("cartesian" in lower or "missing" in lower or "condition" in lower):
        category = "join_condition_missing"
        focus = "补齐 JOIN ON 条件，并确认左右表关联键来自 Schema"
    elif "permission" in lower or "unauthorized" in lower or "access denied" in lower:
        category = "permission_denied"
        focus = "不要改写 SQL 绕过权限，应如实说明权限不足或请求授权"
    elif "syntax" in lower or "unexpected token" in lower or "invalid expression" in lower:
        category = "syntax_error"
        focus = "修正数据库方言语法、分页写法、函数名和括号结构"
    else:
        category = "sql_execution_error"
        focus = "根据数据库错误信息最小化修改 SQL，禁止无依据更换业务口径"
    return f"\n\n【SQL Repair Taxonomy】错误分类：{category}\n修复重点：{focus}。"


def _try_parse_json_output(tool_output: Any) -> Any:
    if isinstance(tool_output, (dict, list)):
        return tool_output
    text = str(tool_output or "").strip()
    if not text:
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


def is_structured_sql_result(parsed: Any) -> bool:
    if isinstance(parsed, list):
        return True
    if not isinstance(parsed, dict):
        return False
    if any(key in parsed for key in ("columns", "items", "rows", "data", "records")):
        return True
    return bool(_extract_result_row_lists(parsed))


def detect_sql_error(output: Any) -> Tuple[bool, str]:
    """检测 tool/SQL 执行结果是否应视为错误并进入联邦 repair。"""
    text = str(output or "")
    if not text.strip():
        return False, ""

    error_prefixes = (
        "[TOOL_ERROR]",
        "[Validation Failed]",
        "[Permission Denied]",
        "[Security Error]",
        "[Performance Blocked]",
        SCHEMA_GATE_PREFIX,
        SQL_PLAN_GATE_PREFIX,
        "Error: Dataset",
    )
    if any(text.startswith(prefix) for prefix in error_prefixes):
        return True, text[:1000]

    parsed = _try_parse_json_output(output)
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
        r"元数据中未找到指定的数据集",
        r"内存联邦 SQL",
        TIME_RANGE_GATE_PREFIX.lower(),
        r"相对时间",
        r"时间锚点",
        r"时间范围与用户相对时间",
    ]
    if any(re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE) for pattern in error_patterns):
        return True, text[:1000]
    return False, ""


def is_non_retryable_permission_error(error: Any) -> bool:
    text = str(error or "")
    if not text.strip():
        return False
    lower = text.lower()
    markers = (
        "permission denied",
        "unauthorized",
        "access denied",
        "无权访问",
        "未提供有效的用户身份",
        "权限不足",
        "[security error]",
        "[permission denied]",
    )
    return any(marker in lower for marker in markers)


def is_retryable_sql_error(error: Any) -> bool:
    """联邦局部 SQL repair 是否应继续重试该节点。"""
    text = str(error or "")
    if not text.strip():
        return False
    if is_non_retryable_permission_error(text):
        return False
    lower = text.lower()
    non_retryable_markers = (
        "禁止外部访问",
        "外部访问",
        "只允许单条 select",
        "只允许 select/with",
        "只允许 select",
    )
    if any(marker in lower for marker in non_retryable_markers):
        return False
    is_err, _ = detect_sql_error(text)
    if is_err:
        return True
    if is_date_format_sql_error(text) or is_schema_reference_sql_error(text):
        return True
    if is_invalid_number_sql_error(text):
        return True
    return False


def build_sql_repair_guidance(
    error_text: str,
    failed_sql: str,
    *,
    repeat_blocked: bool = False,
    for_federated_node: bool = False,
) -> str:
    """构建联邦子查询 / memory_join 局部 repair 的修正指引。"""
    error_text = str(error_text or "").strip()
    failed_sql = str(failed_sql or "").strip()
    err_lower = error_text.lower()
    action = (
        "请只修正下方失败 SQL，并按输出格式返回修正后的 SQL。"
        if for_federated_node
        else "请基于 Schema 修正失败 SQL，并重新输出完整 `<multi_dataset_plan>` XML。"
    )
    repair = (
        "【SQL 修正要求】上一轮 SQL 执行失败。"
        f"错误信息：{error_text[:2000]}\n"
        f"失败 SQL：\n{failed_sql[:4000]}\n"
        f"{action}"
        "禁止原样重复提交与上次完全相同的失败 SQL。"
    )
    repair += sql_repair_taxonomy_hint(error_text)
    if repeat_blocked:
        repair += (
            "\n\n【禁止重复 SQL】上一轮 repair 输出的 SQL 与失败 SQL 归一化后相同，已被视为无效修复。"
            "必须改用与 Schema 字段类型一致的另一种写法（例如字符串日期列改用 'YYYY-MM-DD' 字符串区间，"
            "DATE 列改用 DATE '...' 范围），不得再次提交相同 SQL。"
        )
    repair += invalid_identifier_repair_hint(error_text)
    repair += cross_dataset_scope_repair_hint(error_text)
    if is_schema_reference_sql_error(error_text):
        repair += f"\n\n{DataQueryPrompts.SCHEMA_REFERENCE_SQL_ERROR_REPAIR_GUIDE}"
    if for_federated_node:
        repair += (
            "\n\n【Schema 核对要求】repair 阶段已按需调用 get_dataset_schema 检索失败 SQL 涉及的表；"
            "必须以【本数据集 Schema 片段】中的 columns.type 与物理列名为准修正 SQL，"
            "禁止继续臆造 TO_CHAR/TO_DATE 或英文列名。"
        )
        if (
            "未 select 的字段" in error_text
            or "does not have a column" in err_lower
            or "binder error" in err_lower
        ):
            repair += (
                "\n\n【memory_join 字段约束 — 必读】"
                "memory_join 中的临时表（如 v）**只有 sub_query SELECT 出来的列**，"
                "与物理表 Schema 无关；物理表有 ID 列不代表临时表 v 有 ID。"
                "\n修正优先级："
                "\n1. 【推荐】删除 memory_join 中对不存在列的引用（例如去掉 `ORDER BY v.ID DESC`，只保留 `ORDER BY v.FOLLOW_UP_DATE DESC`）。"
                "\n2. 若必须按 ID 排序，需改 sub_query 在 SELECT 中补 ID——但本轮 repair 只能输出 memory_join，"
                "因此请采用方案 1，删除无效列引用。"
            )
    if is_date_format_sql_error(error_text):
        repair += f"\n\n{DataQueryPrompts.DATE_FORMAT_SQL_ERROR_REPAIR_GUIDE}"
    if is_invalid_number_sql_error(error_text):
        repair += f"\n\n{DataQueryPrompts.INVALID_NUMBER_SQL_ERROR_REPAIR_GUIDE}"
    if TIME_RANGE_GATE_PREFIX in error_text or "相对时间" in err_lower or "时间锚点" in error_text:
        repair += (
            f"\n\n{build_data_query_time_anchor_block()}\n\n"
            f"{DataQueryPrompts.TIME_RANGE_ANOMALY_REPAIR_GUIDE}"
        )
    if "invalid expression" in err_lower or "unexpected token" in err_lower:
        repair += f"\n\n{DataQueryPrompts.SQL_PAGINATION_SYNTAX_GUIDE}"
    return repair
