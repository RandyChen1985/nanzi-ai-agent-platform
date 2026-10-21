"""Serialize and restore DataRunState for AgentScope session persistence."""

from __future__ import annotations

from dataclasses import asdict, fields
from typing import Any

from app.services.ai.runtime.tool_loop_detector import ToolLoopDetector
from app.services.ai.runners.chatbi.run_state import DataRunState


def data_run_state_to_pending_state(
    state: DataRunState,
    stream_meta: dict[str, Any],
) -> dict[str, Any]:
    return {
        **stream_meta,
        "data_run_state": asdict(state),
    }


def sync_pending_data_run_state(
    state: DataRunState,
    pending_state: dict[str, Any],
) -> None:
    pending_state["data_run_state"] = asdict(state)


def build_stream_state(
    state: DataRunState,
    stream_meta: dict[str, Any],
) -> dict[str, Any]:
    stream_state = data_run_state_to_pending_state(state, stream_meta)
    stream_state["tool_names"] = state.tool_names
    stream_state["tool_args_text"] = state.tool_args_text
    stream_state["tool_outputs"] = state.tool_outputs
    stream_state["tool_started_at"] = state.tool_started_at
    stream_state.setdefault("tool_data", {})
    return stream_state


def _restore_table_bindings(raw_bindings: Any) -> Any:
    """``asdict(DataRunState)`` 会把 TableBinding 变成 dict；恢复时必须还原成
    dataclass，否则 insight_meta 等处的 ``binding.dataset_name`` 会 AttributeError。
    """
    if not isinstance(raw_bindings, dict):
        return raw_bindings
    from app.services.ai.chatbi_sql_query_binding import TableBinding
    from app.services.ai.runners.chatbi.sql_gates import SchemaColumnMeta

    restored: dict[str, Any] = {}
    for key, value in raw_bindings.items():
        if not isinstance(value, dict):
            restored[key] = value
            continue
        columns: list[Any] = []
        for column in value.get("columns") or []:
            if isinstance(column, dict):
                columns.append(
                    SchemaColumnMeta(
                        name=str(column.get("name") or ""),
                        col_type=str(column.get("col_type") or ""),
                        examples=list(column.get("examples") or []),
                    )
                )
            else:
                columns.append(column)
        restored[key] = TableBinding(
            physical_name=str(value.get("physical_name") or key),
            dataset_name=str(value.get("dataset_name") or ""),
            data_source=str(value.get("data_source") or ""),
            columns=columns,
        )
    return restored


def pending_state_to_data_run_state(
    pending_state: dict[str, Any],
) -> tuple[DataRunState, dict[str, Any]]:
    raw = pending_state.get("data_run_state") or {}
    valid_keys = {field.name for field in fields(DataRunState)}
    kwargs = {key: raw[key] for key in valid_keys if key in raw}
    detector_raw = kwargs.get("tool_loop_detector")
    if isinstance(detector_raw, dict):
        detector_keys = {field.name for field in fields(ToolLoopDetector)}
        try:
            kwargs["tool_loop_detector"] = ToolLoopDetector(
                **{key: detector_raw[key] for key in detector_keys if key in detector_raw}
            )
        except Exception:
            kwargs["tool_loop_detector"] = ToolLoopDetector()
    kwargs["table_bindings"] = _restore_table_bindings(kwargs.get("table_bindings"))
    raw_binding = kwargs.get("sql_query_binding")
    if isinstance(raw_binding, dict):
        from app.services.ai.chatbi_sql_query_binding import SqlQueryBinding

        kwargs["sql_query_binding"] = SqlQueryBinding(
            sql=str(raw_binding.get("sql") or ""),
            primary_dataset_name=str(raw_binding.get("primary_dataset_name") or ""),
            schema_output=str(raw_binding.get("schema_output") or ""),
            tables=_restore_table_bindings(raw_binding.get("tables")) or {},
            preflight_validated=bool(raw_binding.get("preflight_validated")),
        )
    data_state = DataRunState(**kwargs)
    stream_meta = {
        key: pending_state[key]
        for key in ("system_content", "max_steps", "execution_backend")
        if key in pending_state
    }
    return data_state, stream_meta
