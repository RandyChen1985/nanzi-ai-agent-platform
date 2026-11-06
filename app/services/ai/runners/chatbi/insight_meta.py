"""Build user-facing ChatBI evidence and deterministic follow-up actions."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from app.services.ai.runners.chatbi.run_state import DataRunState
from app.services.ai.grounding.ledger import classify_evidence_result

_ROW_KEYS = ("rows", "data", "result", "results", "items", "records")
_DATE_NAME_RE = re.compile(r"(date|time|day|week|month|year|日期|时间|日|周|月|年)", re.I)
_DATE_VALUE_RE = re.compile(r"^\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?")

# 气泡下方平台明细表：前端默认每页行数；SSE 最多嵌入行数（超出截断，仍报 total）。
CHATBI_RESULT_TABLE_PAGE_SIZE = 50
CHATBI_RESULT_TABLE_MAX_EMBED_ROWS = 2000
_UNSET_COUNT = object()


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


def _result_count_metadata(parsed: Any, rows: list[Any]) -> dict[str, Any]:
    """读取执行层计数元数据，避免把返回样例行数当成数据库总数。"""
    if not isinstance(parsed, dict):
        return {
            "total": len(rows),
            "returned": len(rows),
            "truncated": False,
            "status": "legacy_derived",
        }

    metadata_present = any(
        key in parsed for key in ("total_count", "returned_count", "truncated", "count_status")
    )
    status = str(parsed.get("count_status") or "").strip().lower()
    if status == "exact":
        try:
            total = int(parsed.get("total_count"))
        except (TypeError, ValueError):
            total = None
    elif status == "unknown" or metadata_present:
        total = None
    else:
        total = len(rows)

    try:
        returned = int(parsed.get("returned_count")) if metadata_present and parsed.get("returned_count") is not None else len(rows)
    except (TypeError, ValueError):
        returned = len(rows)
    if returned < 0:
        returned = len(rows)

    raw_truncated = parsed.get("truncated") if metadata_present else None
    truncated = raw_truncated if isinstance(raw_truncated, bool) else (
        returned < total if total is not None else None
    )
    return {
        "total": total,
        "returned": returned,
        "truncated": truncated,
        "status": status or "legacy_derived",
    }


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


def _action(action_id: str, label: str, description: str, query: str, priority: int, *, action_type: str = "send_query") -> dict[str, Any]:
    return {
        "id": action_id,
        "label": label,
        "description": description,
        "action_type": action_type,
        "query": query,
        "priority": priority,
        "requires_data_result": True,
    }


def _visualize_query(numeric: list[str], temporal: list[str], categorical: list[str]) -> str:
    """Build a slightly richer visualize follow-up while staying incremental."""
    structure_hints: list[str] = []
    if temporal and numeric:
        structure_hints.append(
            f"时间列（{'、'.join(temporal[:2])}）与数值列优先折线或柱状趋势图"
        )
    if categorical and numeric:
        structure_hints.append(
            f"分类列（{'、'.join(categorical[:2])}）与数值列优先柱状/条形或占比饼图"
        )
    chart_hint = "；".join(structure_hints) if structure_hints else "根据时间、分类和数值结构选择合适图表"
    return (
        "基于刚才的查询结果做结构化可视化分析："
        "先简要说明数据范围与适合出图的字段，再提炼关键发现、对比或异常；"
        f"{chart_hint}；"
        "用合法的 ```chart ECharts 输出图表，并在图后解读图表结论；"
        "图表数据必须完全来自刚才的查询结果，不得编造；"
        "若数据不足以生成可靠图表，请说明原因而不要强行出图；"
        "最后给出简要结论与建议。不要重复上一轮已展示的完整表格或三段式报告。"
    )


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
            "visualize", "可视化分析", "选合适图表并解读关键发现",
            _visualize_query(numeric, temporal, categorical), 95,
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
    actions.append(_action(
        "brief", "生成业务简报", "整理为可直接汇报的结论、数据和图表",
        "把刚才的查询结果整理成一份业务简报，包含核心结论、关键数据、图表建议、风险和后续动作。", 85,
        action_type="local_action",
    ))
    if numeric:
        actions.append(_action(
            "monitor", "创建订阅", "定时执行本次查询并按设置发送通知",
            "基于刚才的查询条件创建定时订阅；先确认执行频率、时间和通知方式。", 75,
            action_type="local_action",
        ))
    return sorted(actions, key=lambda item: item["priority"], reverse=True)[:6]


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


def _build_evidence_metadata(
    parsed: Any,
    evidence_metadata: dict[str, Any] | None = None,
    state: DataRunState | None = None,
) -> dict[str, Any]:
    """Normalize the additive evidence contract consumed by both chat surfaces."""
    result = parsed if isinstance(parsed, dict) else {}
    metadata = evidence_metadata if isinstance(evidence_metadata, dict) else {}
    source_ref = metadata.get("source_ref") or result.get("source_ref")
    if not source_ref and state is not None:
        datasets = sorted(
            {
                str(binding.dataset_name or "").strip()
                for binding in state.table_bindings.values()
                if str(binding.dataset_name or "").strip()
            }
        )
        if len(datasets) == 1:
            source_ref = f"dataset://{datasets[0]}"
    return {
        "result_status": str(
            metadata.get("status")
            or result.get("result_status")
            or classify_evidence_result(parsed).value
        ),
        "source_ref": source_ref,
        "observed_at": metadata.get("observed_at") or result.get("observed_at") or result.get("saved_at"),
        "source_as_of": (
            metadata.get("source_as_of")
            or result.get("source_as_of")
            or result.get("data_as_of")
            or result.get("as_of")
        ),
        "freshness": str(metadata.get("freshness") or result.get("freshness") or "dynamic"),
    }


def _jsonable_cell(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, dict)):
        try:
            return json.loads(json.dumps(value, ensure_ascii=False, default=str))
        except Exception:
            return str(value)
    return str(value)


def build_result_table_payload(
    rows: list[dict[str, Any]],
    *,
    total_row_count: int | None | object = _UNSET_COUNT,
    truncated: bool | None | object = _UNSET_COUNT,
) -> dict[str, Any] | None:
    """Structured table for platform UI (not for LLM context)."""
    if not rows:
        return None
    columns: list[str] = list(rows[0].keys())
    for row in rows[1 : min(100, len(rows))]:
        for key in row.keys():
            if key not in columns:
                columns.append(str(key))
    total = len(rows) if total_row_count is _UNSET_COUNT else total_row_count
    embed_rows = rows[:CHATBI_RESULT_TABLE_MAX_EMBED_ROWS]
    matrix = [
        [_jsonable_cell(row.get(column)) for column in columns]
        for row in embed_rows
    ]
    return {
        "columns": columns,
        "rows": matrix,
        "total_row_count": total,
        "embedded_row_count": len(embed_rows),
        "page_size": CHATBI_RESULT_TABLE_PAGE_SIZE,
        "truncated": (
            total > len(embed_rows)
            if truncated is _UNSET_COUNT and total is not None
            else (
                (bool(truncated) or total > len(embed_rows))
                if truncated is not _UNSET_COUNT and total is not None
                else truncated
            )
        ),
    }


def build_chatbi_insight_meta(
    state: DataRunState,
    *,
    evidence_metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return an additive SSE event only for a successful structured SQL result."""
    if not state.has_successful_nonempty_sql or state.last_successful_sql_output is None:
        return None
    parsed = _parse_result(state.last_successful_sql_output)
    rows = _rows_from_result(parsed)
    if not rows:
        return None
    count_meta = _result_count_metadata(parsed, rows)
    notice = parsed.get("permission_notice") if isinstance(parsed, dict) else None
    safe_notice = {
        key: notice.get(key)
        for key in (
            "row_filter_applied",
            "dataset_name",
            "rule_count",
            "message",
            # 字段级权限：列被剔掉时告诉前端哪些字段没返回，避免用户以为查询漏了数据
            "omitted_columns",
        )
        if isinstance(notice, dict) and notice.get(key) is not None
    }
    raw_sql = str(state.last_successful_sql_args.get("sql") or state.last_successful_sql_args.get("query") or "").strip()
    executed_sql = str(notice.get("executed_sql") or "").strip() if isinstance(notice, dict) else ""
    repair_count = sum(int(count or 0) for count in state.repair_attempts.values()) + int(state.platform_auto_sql_attempts or 0)
    table = build_result_table_payload(
        rows,
        total_row_count=count_meta["total"],
        truncated=count_meta["truncated"],
    )
    scope = getattr(state, "model_result_scope", None)
    has_count_metadata = isinstance(parsed, dict) and any(
        key in parsed for key in ("total_count", "returned_count", "truncated", "count_status")
    )
    if has_count_metadata or not isinstance(scope, dict) or scope.get("mode") not in {"full", "sample"}:
        from app.services.ai.runners.chatbi.sql_result_compact import build_model_result_scope

        class _ScopeRunner:
            @staticmethod
            def _try_parse_json_output(output: Any) -> Any:
                return _parse_result(output)

        scope = build_model_result_scope(_ScopeRunner(), state.last_successful_sql_output)
    return {
        "type": "chatbi_insight_meta",
        "data": {
            "version": 1,
            "status": "success",
            "result_id": state.current_result_id or None,
            "sources": _build_sources(state),
            "permission": safe_notice,
            "evidence": _build_evidence_metadata(parsed, evidence_metadata, state),
            "execution": {
                "mode": "repaired" if repair_count else "direct",
                "row_count": count_meta["returned"],
                "total_row_count": count_meta["total"],
                "returned_row_count": count_meta["returned"],
                "truncated": count_meta["truncated"],
                "count_status": count_meta["status"],
                "repair_count": repair_count,
                "federated": False,
            },
            "final_sql": executed_sql or raw_sql,
            "actions": _build_actions(rows),
            "table": table,
            "analysis_scope": scope,
        },
    }


def take_chatbi_insight_meta_event(
    state: DataRunState,
    *,
    evidence_metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build and mark the additive event so resume/reconcile paths cannot duplicate it."""
    if state.insight_meta_emitted:
        return None
    event = build_chatbi_insight_meta(state, evidence_metadata=evidence_metadata)
    if event is not None:
        state.insight_meta_emitted = True
    return event


def build_federated_chatbi_insight_meta(
    *,
    final_data: Any,
    dataset_names: list[str],
    final_sql: str,
    evidence_metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build the same user-facing contract for a successful DuckDB federated join."""
    parsed = _parse_result(final_data)
    rows = _rows_from_result(parsed)
    if not rows:
        return None
    count_meta = _result_count_metadata(parsed, rows)
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
            "evidence": _build_evidence_metadata(parsed, evidence_metadata),
            "execution": {
                "mode": "federated",
                "row_count": count_meta["returned"],
                "total_row_count": count_meta["total"],
                "returned_row_count": count_meta["returned"],
                "truncated": count_meta["truncated"],
                "count_status": count_meta["status"],
                "repair_count": 0,
                "federated": True,
            },
            "final_sql": str(final_sql or "").strip(),
            "actions": _build_actions(rows),
            "table": build_result_table_payload(
                rows,
                total_row_count=count_meta["total"],
                truncated=count_meta["truncated"],
            ),
            "analysis_scope": {
                "mode": "full",
                "total_row_count": count_meta["total"],
                "model_row_count": count_meta["returned"],
                "user_notice": "",
            },
        },
    }


def build_saved_report_chatbi_insight_meta(
    parsed_result: Any,
    *,
    sql: str = "",
    dataset_name: str | None = None,
    data_source: str | None = None,
    permission_notice: dict[str, Any] | None = None,
    result_id: str | None = None,
) -> dict[str, Any] | None:
    """黄金报表直跑成功后，复用 ChatBI 继续分析动作契约。"""
    parsed = _parse_result(parsed_result)
    rows = _rows_from_result(parsed)
    if not rows:
        return None
    notice = permission_notice if isinstance(permission_notice, dict) else {}
    if not notice and isinstance(parsed, dict) and isinstance(parsed.get("permission_notice"), dict):
        notice = parsed.get("permission_notice") or {}
    safe_notice = {
        key: notice.get(key)
        for key in ("row_filter_applied", "dataset_name", "rule_count", "message")
        if notice.get(key) is not None
    }
    dataset = str(dataset_name or safe_notice.get("dataset_name") or "").strip()
    source = str(data_source or "").strip()
    count_meta = _result_count_metadata(parsed, rows)
    return {
        "version": 1,
        "status": "success",
        "result_id": result_id,
        "sources": (
            [{"dataset_name": dataset, "data_source": source, "tables": []}]
            if dataset or source
            else []
        ),
        "permission": safe_notice,
        "evidence": _build_evidence_metadata(parsed),
        "execution": {
            "mode": "direct",
            "row_count": count_meta["returned"],
            "total_row_count": count_meta["total"],
            "returned_row_count": count_meta["returned"],
            "truncated": count_meta["truncated"],
            "count_status": count_meta["status"],
            "repair_count": 0,
            "federated": False,
        },
        "final_sql": str(sql or "").strip(),
        "actions": _build_actions(rows),
        "table": build_result_table_payload(
            rows,
            total_row_count=count_meta["total"],
            truncated=count_meta["truncated"],
        ),
        "analysis_scope": {
            "mode": "full",
            "total_row_count": count_meta["total"],
            "model_row_count": count_meta["returned"],
            "user_notice": (
                f"当前已返回 {count_meta['returned']} 行，数据库总数未统计。"
                if count_meta["total"] is None and count_meta["status"] == "unknown"
                else ""
            ),
        },
    }
