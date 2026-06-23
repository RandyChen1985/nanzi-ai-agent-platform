"""Mutable state for a single ChatBI ReAct turn."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.services.ai.chatbi_sql_query_binding import TableBinding
from app.services.ai.runtime.tool_loop_detector import ToolLoopDetector


@dataclass
class DataRunState:
    tool_names: dict[str, str] = field(default_factory=dict)
    tool_args_text: dict[str, str] = field(default_factory=dict)
    tool_outputs: dict[str, str] = field(default_factory=dict)
    tool_started_at: dict[str, float] = field(default_factory=dict)
    full_content: str = ""
    blocked_content: str = ""
    content_emitted: bool = False
    schema_completed: bool = False
    schema_service_unavailable: bool = False
    rag_not_synced: bool = False
    sql_completed: bool = False
    sql_before_schema: bool = False
    schema_miss: bool = False
    schema_needs_refinement: bool = False
    schema_ambiguous: bool = False
    schema_ambiguous_reason: str = ""
    no_authorized_schema: bool = False
    empty_sql_result: bool = False
    empty_sql_reason: str = ""
    empty_sql_text: str = ""
    empty_filter_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    empty_filter_diagnostic_summary: str = ""
    empty_filter_auto_retry_sql: str = ""
    expecting_final_sql_after_diagnostic: bool = False
    diagnostic_sql_pending_final: bool = False
    sql_error: bool = False
    sql_error_message: str = ""
    sql_fatal_error: bool = False
    sql_fatal_message: str = ""
    sql_fatal_emitted: bool = False
    sql_static_risk: bool = False
    sql_static_risk_reason: str = ""
    time_range_anomaly: bool = False
    time_range_anomaly_reason: str = ""
    sql_sandbox_blocked: bool = False
    sql_sandbox_blocked_reason: str = ""
    sql_repeat_gate_block: bool = False
    failed_sql_repeat_gate_block: bool = False
    requires_sql_plan: bool = False
    requires_sql_query: bool = True
    sql_plan_seen: bool = False
    sql_plan_missing: bool = False
    requires_fresh_data: bool = True
    text_window: str = ""
    start_synthesis: float = field(default_factory=time.time)
    ignore_text_block: bool = False
    active_text_block_id: str = ""
    text_blocks_emitted_since_last_tool: int = 0
    current_text_block_emitted: bool = False
    halt_current_react: bool = False
    last_successful_sql_output: Any = None
    last_successful_sql_args: dict[str, Any] = field(default_factory=dict)
    successful_sqls: dict[str, Any] = field(default_factory=dict)
    sql_citation_counter: int = 0
    emitted_sql_citation_signatures: list[str] = field(default_factory=list)
    ratio_anomaly: bool = False
    ratio_anomaly_reason: str = ""
    duration_anomaly: bool = False
    duration_anomaly_reason: str = ""
    tool_call_signatures: dict[str, int] = field(default_factory=dict)
    tool_loop_detector: ToolLoopDetector = field(default_factory=ToolLoopDetector)
    tool_loop_fuse_triggered: bool = False
    tool_loop_fuse_reason: str = ""
    schema_miss_count: int = 0
    repair_attempts: dict[str, int] = field(default_factory=dict)
    last_schema_keywords: str = ""
    last_schema_tool_keywords: str = ""
    controlled_schema_retry_keywords: str = ""
    last_applied_schema_retry_keywords: str = ""
    pending_schema_retry: bool = False
    followup_data_saved: bool = False
    schema_output: str = ""
    table_bindings: dict[str, TableBinding] = field(default_factory=dict)
    schema_table_columns: dict[str, list[str]] = field(default_factory=dict)
    sql_query_binding: Any | None = None
    failed_sql_signatures: dict[str, int] = field(default_factory=dict)
    last_failed_sql_normalized: str = ""
    last_sql_error_summary: str = ""
    schema_refresh_required: bool = False
    schema_refreshed_after_sql_error: bool = False

    @property
    def has_successful_nonempty_sql(self) -> bool:
        if not self.requires_fresh_data:
            return False
        return (
            self.schema_completed
            and self.sql_completed
            and not self.sql_before_schema
            and not self.sql_error
            and not self.empty_sql_result
        )

    @property
    def ready_to_answer(self) -> bool:
        if self.tool_loop_fuse_triggered:
            return False
        if not self.requires_fresh_data:
            return True
        if not self.requires_sql_query:
            return (
                self.schema_completed
                and not self.schema_service_unavailable
                and not self.no_authorized_schema
                and not self.rag_not_synced
                and self.schema_miss_count < 2
            )
        if self.sql_fatal_error:
            return True
        if self.schema_ambiguous and not self.sql_before_schema and not self.sql_error:
            return True
        return (
            self.schema_completed
            and self.sql_completed
            and not self.sql_before_schema
            and not self.sql_static_risk
            and not self.time_range_anomaly
            and not self.sql_error
            and not self.empty_sql_result
            and not self.diagnostic_sql_pending_final
            and not self.ratio_anomaly
            and not self.duration_anomaly
            and not self.tool_loop_fuse_triggered
        )
