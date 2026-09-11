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
    # 可见正文 vs 成功 SQL 的相对时序（单调递增）。用于判定「工具后无收口正文」。
    event_seq: int = 0
    last_visible_content_at: int = 0
    last_successful_nonempty_sql_at: int = 0
    last_tool_name: str = ""
    schema_completed: bool = False
    schema_service_unavailable: bool = False
    rag_not_synced: bool = False
    sql_completed: bool = False
    sql_before_schema: bool = False
    schema_miss: bool = False
    schema_needs_refinement: bool = False
    schema_ambiguous: bool = False
    schema_ambiguous_reason: str = ""
    schema_ambiguity_candidates: list[dict[str, str]] = field(default_factory=list)
    no_authorized_schema: bool = False
    empty_sql_result: bool = False
    empty_sql_reason: str = ""
    empty_sql_text: str = ""
    empty_filter_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    empty_filter_diagnostic_summary: str = ""
    empty_filter_auto_retry_sql: str = ""
    expecting_final_sql_after_diagnostic: bool = False
    diagnostic_sql_pending_final: bool = False
    next_sql_is_final_business_query: bool = False
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
    sql_plan_auto_generated: bool = False
    sql_plan_payload: dict[str, Any] = field(default_factory=dict)
    sql_plan_sql_normalized: str = ""
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
    # 工具层向模型回传抽样结果前，暂存完整 SQL 输出，供 on_tool_result_end 落库/enrich。
    pending_sql_tool_full_output: Any = None
    # 模型承诺继续细化查数但未发起工具：进入 repair 强制 execute_sql_query。
    deferred_continue_query: bool = False
    # 平台 empty/WHERE 自动重试已拿到非空结果，但模型工具观测仍是旧空/错结果 → 停 ReAct 改走缓存合成。
    platform_auto_retry_ready: bool = False
    # 模型侧结果范围：full=全量进上下文；sample=仅样例（用于用户可见提示）。
    model_result_scope: dict[str, Any] | None = None
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
    current_result_id: str = ""
    insight_meta_emitted: bool = False
    schema_output: str = ""
    table_bindings: dict[str, TableBinding] = field(default_factory=dict)
    schema_table_columns: dict[str, list[str]] = field(default_factory=dict)
    sql_query_binding: Any | None = None
    failed_sql_signatures: dict[str, int] = field(default_factory=dict)
    last_failed_sql_normalized: str = ""
    last_failed_sql_text: str = ""
    last_sql_error_summary: str = ""
    where_condition_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    where_condition_diagnostic_summary: str = ""
    where_condition_auto_retry_sql: str = ""
    schema_refresh_required: bool = False
    schema_refreshed_after_sql_error: bool = False
    preflight_fail_signatures: dict[str, int] = field(default_factory=dict)
    platform_auto_sql_attempts: int = 0
    # ---- 方案 A：数据库报错驱动的 Schema 过时纠正（内存态，仅当轮）----
    # 已识别为「真实过时」并被剔除的列（list[dict]，含 field_name/table_key）。
    stale_columns_dropped: list[dict[str, str]] = field(default_factory=list)
    # 剔除失效列后重建的当轮可见 Schema 文本。
    corrected_schema_output: str = ""
    # 剔除后各表剩余可用列（normalized table_key -> [column_name]）。
    corrected_table_columns: dict[str, list[str]] = field(default_factory=dict)
    # 是否已对当轮做过一次纠正（配合失败签名计数做熔断，防死循环）。
    stale_correction_applied: bool = False
    # 已预生成的修复提示文案（注入下一轮 ReAct，告知模型已剔除的失效列与剩余可用列）。
    stale_repair_hint: str = ""
    # 熔断：方案 A 的纠正修复同一轮只触发一次，surfaced 后置 True，防止反复剔除/死循环。
    stale_repair_consumed: bool = False

    @property
    def successful_sql_after_visible_content(self) -> bool:
        """True when the latest nonempty business SQL happened after the last visible reply text."""
        return (
            int(self.last_successful_nonempty_sql_at or 0) > 0
            and int(self.last_successful_nonempty_sql_at or 0)
            > int(self.last_visible_content_at or 0)
        )

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
            and not self.duration_anomaly
            and not self.tool_loop_fuse_triggered
        )
