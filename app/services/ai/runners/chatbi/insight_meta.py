"""Build user-facing ChatBI evidence and deterministic follow-up actions."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from app.services.ai.runners.chatbi.run_state import DataRunState

_ROW_KEYS = ("rows", "data", "result", "results", "items", "records")
_DATE_NAME_RE = re.compile(r"(date|time|day|week|month|year|日期|时间|日|周|月|年)", re.I)
_DATE_VALUE_RE = re.compile(r"^\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?")


def _parse_result(output: Any) -> Any:
    if isinstance(output, (dict, list)):
        return output
    try:
        return json.loads(str(output or "").strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _rows_from_result(parsed: Any, depth: int = 0) -> list[dict[str, Any]]:
    if depth > 4:
        return []
    if isinstance(parsed, list):
        return [row for row in parsed if isinstance(row, dict)]
    if not isinstance(parsed, dict):
        return []
    for key in _ROW_KEYS:
        value = parsed.get(key)
        if isinstance(value, list):
            dict_rows = [row for row in value if isinstance(row, dict)]
            if dict_rows:
                return dict_rows
            columns = parsed.get("columns")
            if isinstance(columns, list):
                return [
                    {
                        str(column.get("name") if isinstance(column, dict) else column): row[index]
                        for index, column in enumerate(columns)
                        if index < len(row)
                    }
                    for row in value
                    if isinstance(row, list)
                ]
        elif isinstance(value, dict):
            rows = _rows_from_result(value, depth + 1)
            if rows:
                return rows
    return []


def _column_roles(rows: list[dict[str, Any]]) -> tuple[list[str], list[str], list[str]]:
    if not rows:
        return [], [], []
    columns = list(rows[0].keys())
    numeric: list[str] = []
    temporal: list[str] = []
    categorical: list[str] = []
    for column in columns:
        values = [row.get(column) for row in rows[:20] if row.get(column) is not None]
        if not values:
            continue
        if _DATE_NAME_RE.search(column) or any(_DATE_VALUE_RE.match(str(value)) for value in values):
            temporal.append(column)
        elif all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            numeric.append(column)
        else:
            categorical.append(column)
    return numeric, temporal, categorical


def _action(action_id: str, label: str, description: str, query: str, priority: int) -> dict[str, Any]:
    return {
        "id": action_id,
        "label": label,
        "description": description,
        "action_type": "send_query",
        "query": query,
        "priority": priority,
        "requires_data_result": True,
    }


def _build_actions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    numeric, temporal, categorical = _column_roles(rows)
    actions: list[dict[str, Any]] = []
    if numeric and temporal:
        actions.append(_action(
            "trend", "查看趋势", "按时间分析变化和明显拐点",
            "基于刚才的查询结果，按时间展示主要指标的变化趋势，并指出明显拐点。", 100,
        ))
    if numeric and len(rows) > 1 and (temporal or categorical):
        actions.append(_action(
            "visualize", "可视化分析", "根据数据结构选择合适图表",
            "基于刚才的查询结果做可视化分析，并根据时间、分类和数值结构选择合适的图表。", 95,
        ))
    if numeric and categorical:
        dimension = categorical[0]
        actions.append(_action(
            "ranking", "查看排名", f"按{dimension}比较指标高低",
            f"基于刚才的查询结果，按{dimension}对主要指标进行排名，并说明最高和最低项。", 90,
        ))
        actions.append(_action(
            "contribution", "分析贡献度", f"分析各{dimension}的影响占比",
            f"基于刚才的查询结果，分析各{dimension}对整体结果的贡献度，并指出主要贡献项。", 80,
        ))
    if numeric and len(rows) > 1:
        actions.append(_action(
            "anomaly", "查找异常", "找出明显偏离整体的数据",
            "基于刚才的查询结果，找出明显异常值或偏离整体水平的记录，并说明判断依据。", 70,
        ))
    if not actions:
        actions.append(
            _action("summary", "总结关键结论", "提炼重点和业务含义", "基于刚才的查询结果，总结关键结论、异常点和业务含义。", 50)
        )
    return sorted(actions, key=lambda item: item["priority"], reverse=True)[:4]


def _build_sources(state: DataRunState) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for binding in state.table_bindings.values():
        dataset = str(binding.dataset_name or "").strip()
        source = str(binding.data_source or "").strip()
        table = str(binding.physical_name or "").strip()
        if table:
            grouped[(dataset, source)].append({"physical_name": table})
    return [
        {"dataset_name": dataset, "data_source": source, "tables": tables}
        for (dataset, source), tables in grouped.items()
    ]


def build_chatbi_insight_meta(state: DataRunState) -> dict[str, Any] | None:
    """Return an additive SSE event only for a successful structured SQL result."""
    if not state.has_successful_nonempty_sql or state.last_successful_sql_output is None:
        return None
    parsed = _parse_result(state.last_successful_sql_output)
    rows = _rows_from_result(parsed)
    if not rows:
        return None
    notice = parsed.get("permission_notice") if isinstance(parsed, dict) else None
    safe_notice = {
        key: notice.get(key)
        for key in ("row_filter_applied", "dataset_name", "rule_count", "message")
        if isinstance(notice, dict) and notice.get(key) is not None
    }
    raw_sql = str(state.last_successful_sql_args.get("sql") or state.last_successful_sql_args.get("query") or "").strip()
    executed_sql = str(notice.get("executed_sql") or "").strip() if isinstance(notice, dict) else ""
    repair_count = sum(int(count or 0) for count in state.repair_attempts.values()) + int(state.platform_auto_sql_attempts or 0)
    return {
        "type": "chatbi_insight_meta",
        "data": {
            "version": 1,
            "status": "success",
            "sources": _build_sources(state),
            "permission": safe_notice,
            "execution": {
                "mode": "repaired" if repair_count else "direct",
                "row_count": len(rows),
                "repair_count": repair_count,
                "federated": False,
            },
            "final_sql": executed_sql or raw_sql,
            "actions": _build_actions(rows),
        },
    }


def take_chatbi_insight_meta_event(state: DataRunState) -> dict[str, Any] | None:
    """Build and mark the additive event so resume/reconcile paths cannot duplicate it."""
    if state.insight_meta_emitted:
        return None
    event = build_chatbi_insight_meta(state)
    if event is not None:
        state.insight_meta_emitted = True
    return event


def build_federated_chatbi_insight_meta(
    *,
    final_data: Any,
    dataset_names: list[str],
    final_sql: str,
) -> dict[str, Any] | None:
    """Build the same user-facing contract for a successful DuckDB federated join."""
    parsed = _parse_result(final_data)
    rows = _rows_from_result(parsed)
    if not rows:
        return None
    return {
        "type": "chatbi_insight_meta",
        "data": {
            "version": 1,
            "status": "success",
            "sources": [
                {"dataset_name": name, "data_source": "", "tables": []}
                for name in dataset_names
                if str(name or "").strip()
            ],
            "permission": {},
            "execution": {
                "mode": "federated",
                "row_count": len(rows),
                "repair_count": 0,
                "federated": True,
            },
            "final_sql": str(final_sql or "").strip(),
            "actions": _build_actions(rows),
        },
    }
