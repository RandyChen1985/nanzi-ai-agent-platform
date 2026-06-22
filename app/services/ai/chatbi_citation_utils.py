"""ChatBI 平台自动引用：查数成功后下发 SQL 来源 citation 卡片（方案 B，无需模型写 [ID:n]）。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.services.ai.federated_sql_repair import normalize_sql_text

_ROW_CONTAINER_KEYS = ("items", "rows", "data", "records", "list", "result")


def _try_parse_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def extract_result_row_items(parsed: Any) -> List[Any]:
    parsed = _try_parse_json(parsed)
    if isinstance(parsed, list):
        return parsed
    if not isinstance(parsed, dict):
        return []
    for key in _ROW_CONTAINER_KEYS:
        value = parsed.get(key)
        if isinstance(value, list):
            return value
    return []


def extract_result_column_names(parsed: Any) -> List[str]:
    parsed = _try_parse_json(parsed)
    if not isinstance(parsed, dict):
        return []
    columns = parsed.get("columns") or []
    names: List[str] = []
    for column in columns:
        if isinstance(column, dict):
            name = str(column.get("name") or column.get("field") or "").strip()
        else:
            name = str(column).strip()
        if name:
            names.append(name)
    return names


def count_result_rows(parsed: Any) -> int:
    return len(extract_result_row_items(parsed))


def _format_cell(value: Any, *, max_len: int = 80) -> str:
    if value is None:
        return "NULL"
    text = str(value).replace("\n", " ").strip()
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def format_result_sample(
    parsed: Any,
    *,
    limit: int = 3,
    column_names: Optional[List[str]] = None,
) -> str:
    rows = extract_result_row_items(parsed)
    if not rows:
        return "（无数据行）"
    columns = column_names or extract_result_column_names(parsed)
    lines: List[str] = []
    for row in rows[:limit]:
        if isinstance(row, dict):
            if columns:
                cells = [_format_cell(row.get(col)) for col in columns]
            else:
                cells = [_format_cell(value) for value in row.values()]
        elif isinstance(row, (list, tuple)):
            cells = [_format_cell(value) for value in row]
        else:
            cells = [_format_cell(row)]
        lines.append(" | ".join(cells))
    suffix = ""
    if len(rows) > limit:
        suffix = f"\n... 另有 {len(rows) - limit} 行未展示"
    return "\n".join(lines) + suffix


def format_chatbi_sql_citation_content(
    *,
    sql: str,
    dataset_name: str,
    data_source: str = "",
    parsed_output: Any = None,
    title: str = "查数 SQL",
    sample_limit: int = 3,
) -> str:
    sql_text = str(sql or "").strip() or "（未记录 SQL）"
    dataset = str(dataset_name or "").strip() or "未知数据集"
    source = str(data_source or "").strip()
    row_count = count_result_rows(parsed_output) if parsed_output is not None else None
    columns = extract_result_column_names(parsed_output) if parsed_output is not None else []

    lines = [f"【{title}】", sql_text, "", "【数据概览】"]
    overview_parts = [f"数据集：{dataset}"]
    if source:
        overview_parts.append(f"数据源：{source}")
    if row_count is not None:
        overview_parts.append(f"返回 {row_count} 行")
    if columns:
        preview_cols = ", ".join(columns[:8])
        if len(columns) > 8:
            preview_cols += f" 等 {len(columns)} 列"
        overview_parts.append(f"列：{preview_cols}")
    lines.append(" | ".join(overview_parts))

    if parsed_output is not None:
        lines.extend(["", "【结果样例】", format_result_sample(parsed_output, limit=sample_limit, column_names=columns)])
    return "\n".join(lines)


def build_chatbi_sql_citation_chunk(
    *,
    citation_index: int,
    tool_call_id: str,
    sql: str,
    dataset_name: str,
    data_source: str = "",
    parsed_output: Any = None,
    doc_name_suffix: str = "查数 SQL",
) -> Dict[str, Any]:
    dataset = str(dataset_name or "").strip() or "未知数据集"
    chunk_id = f"chatbi-sql:{tool_call_id}:{citation_index}"
    return {
        "id": str(citation_index),
        "chunk_id": chunk_id,
        "doc_name": f"{dataset} · {doc_name_suffix}",
        "content": format_chatbi_sql_citation_content(
            sql=sql,
            dataset_name=dataset_name,
            data_source=data_source,
            parsed_output=parsed_output,
            title=doc_name_suffix,
        ),
        "source_type": "chatbi_sql",
    }


def build_chatbi_citation_event(
    *,
    tool_call_id: str,
    chunks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "type": "citation",
        "tool_call_id": tool_call_id,
        "data": chunks,
    }


def build_federated_chatbi_citation_event(
    *,
    tool_call_id: str,
    subquery_sources: List[Dict[str, Any]],
    join_sql: str,
    final_data: Dict[str, Any],
    dataset_names: List[str],
) -> Optional[Dict[str, Any]]:
    chunks: List[Dict[str, Any]] = []
    index = 1
    for source in subquery_sources:
        dataset_name = str(source.get("dataset_name") or "").strip()
        sql = str(source.get("sql") or "").strip()
        if not sql:
            continue
        row_count = source.get("row_count")
        parsed_output: Dict[str, Any] = {}
        columns = source.get("columns") or []
        if columns:
            parsed_output["columns"] = [{"name": name} for name in columns]
        items = source.get("items")
        if isinstance(items, list):
            parsed_output["items"] = items
        elif row_count is not None:
            parsed_output["items"] = [[] for _ in range(int(row_count))]
        chunks.append(
            build_chatbi_sql_citation_chunk(
                citation_index=index,
                tool_call_id=tool_call_id,
                sql=sql,
                dataset_name=dataset_name,
                data_source=str(source.get("data_source") or ""),
                parsed_output=parsed_output or None,
                doc_name_suffix="联邦子查询",
            )
        )
        index += 1

    join_sql_text = str(join_sql or "").strip()
    if join_sql_text:
        join_dataset = "、".join(dataset_names) if dataset_names else "联邦查数"
        chunks.append(
            build_chatbi_sql_citation_chunk(
                citation_index=index,
                tool_call_id=tool_call_id,
                sql=join_sql_text,
                dataset_name=join_dataset,
                data_source="duckdb",
                parsed_output=final_data,
                doc_name_suffix="联邦聚合 SQL",
            )
        )

    if not chunks:
        return None
    return build_chatbi_citation_event(tool_call_id=tool_call_id, chunks=chunks)


def maybe_build_chatbi_sql_citation_event(
    state: Any,
    *,
    tool_call_id: str,
    tool_args: Dict[str, Any],
    parsed_output: Any,
) -> Optional[Dict[str, Any]]:
    """同一轮内对相同 SQL 只下发一次 citation，避免重复查数/复用缓存时重复展示。"""
    sql = str(tool_args.get("sql") or tool_args.get("query") or "").strip()
    if not sql:
        return None

    normalized = normalize_sql_text(sql)
    emitted = getattr(state, "emitted_sql_citation_signatures", None) or []
    if normalized in emitted:
        return None

    citation_index = int(getattr(state, "sql_citation_counter", 0) or 0) + 1
    state.sql_citation_counter = citation_index
    emitted.append(normalized)
    state.emitted_sql_citation_signatures = emitted

    chunk = build_chatbi_sql_citation_chunk(
        citation_index=citation_index,
        tool_call_id=tool_call_id,
        sql=sql,
        dataset_name=str(tool_args.get("dataset_name") or ""),
        data_source=str(tool_args.get("data_source") or ""),
        parsed_output=parsed_output,
    )
    return build_chatbi_citation_event(tool_call_id=tool_call_id, chunks=[chunk])
