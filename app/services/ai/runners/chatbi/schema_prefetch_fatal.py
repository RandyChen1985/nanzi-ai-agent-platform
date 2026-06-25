"""Lightweight schema fatal probe for prefetch (avoids full apply_schema_tool_result)."""

from __future__ import annotations

from typing import Any

from app.services.ai.runners.chatbi.run_state import DataRunState
from app.services.ai.runners.chatbi.schema_fatal import is_schema_fatal


def build_prefetch_fatal_probe_state(runner: Any, schema_output: Any) -> DataRunState:
    """Populate only flags required by is_schema_fatal — no bindings / miss counters."""
    state = DataRunState()
    state.schema_service_unavailable = runner._is_schema_service_unavailable(schema_output)
    state.no_authorized_schema = runner._is_no_authorized_schema(schema_output)
    state.rag_not_synced = runner._is_rag_not_synced(schema_output)
    return state


def is_prefetch_schema_fatal(runner: Any, schema_output: Any) -> bool:
    return is_schema_fatal(build_prefetch_fatal_probe_state(runner, schema_output))
