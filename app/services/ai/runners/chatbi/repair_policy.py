"""ChatBI repair loop policy: kind detection, budgets, messages, and tool choices."""

from __future__ import annotations

from typing import Any

from app.services.ai.data_query_semantic_intent import (
    DataQuerySemanticIntent,
    format_empty_result_semantic_repair_context,
)
from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.ai.runtime.tool_loop_detector import ToolLoopDetector
from app.services.ai.runners.chatbi.constants import DATA_REPAIR_BUDGETS, MAX_DATA_REPAIR_ROUNDS
from app.services.ai.runners.chatbi.run_state import DataRunState
from app.services.ai.runners.chatbi.schema_fatal import is_schema_fatal
from app.services.ai.runners.chatbi.sql_gates import is_schema_reference_sql_error
from app.services.ai.where_condition_sample_diagnostic import (
    build_where_condition_probe_repair_hint,
    is_invalid_number_sql_error,
    is_where_condition_sql_error,
    schema_column_hints_from_bindings,
)
from app.services.ai.runners.chatbi.sql_repair_hints import (
    cross_dataset_scope_repair_hint,
    invalid_identifier_repair_hint,
    sql_repair_taxonomy_hint,
)
from app.services.ai.time_anchor import build_data_query_time_anchor_block


def current_repair_kind(state: DataRunState) -> str:
    if is_schema_fatal(state):
        return ""
    if state.schema_ambiguous:
        return "schema_ambiguous"
    if state.schema_refresh_required and not state.schema_refreshed_after_sql_error:
        return "schema_refresh_after_sql_error"
    if state.requires_fresh_data and state.sql_before_schema and not state.schema_completed:
        return "sql_before_schema"
    if state.schema_miss and not state.no_authorized_schema:
        return "schema_miss"
    if state.schema_needs_refinement:
        return "schema_refinement"
    if state.sql_sandbox_blocked:
        return "sql_sandbox_blocked"
    if state.sql_static_risk:
        return "sql_static_risk"
    if state.time_range_anomaly:
        return "time_range_anomaly"
    if state.sql_plan_missing:
        return "sql_plan_missing"
    if state.failed_sql_repeat_gate_block:
        return "failed_sql_repeat"
    if state.sql_error:
        return "sql_error"
    if state.empty_sql_result:
        return "empty_sql_result"
    if state.ratio_anomaly:
        return "ratio_anomaly"
    if state.duration_anomaly:
        return "duration_anomaly"
    if state.tool_loop_fuse_triggered:
        return "tool_loop_fuse"
    if state.diagnostic_sql_pending_final:
        return "diagnostic_sql_pending_final"
    if (
        state.requires_fresh_data
        and state.requires_sql_query
        and state.schema_completed
        and state.sql_plan_seen
        and not state.sql_completed
        and not state.ready_to_answer
    ):
        return "missing_sql"
    if (
        state.requires_fresh_data
        and state.blocked_content.strip()
        and not state.ready_to_answer
    ):
        if not state.schema_completed:
            return "missing_schema"
        if state.requires_sql_query and not state.sql_completed:
            return "missing_sql"
    return ""


def repair_budget_exhausted(state: DataRunState) -> bool:
    kind = current_repair_kind(state)
    if not kind:
        return False
    budget = DATA_REPAIR_BUDGETS.get(kind, MAX_DATA_REPAIR_ROUNDS)
    return state.repair_attempts.get(kind, 0) >= budget


def record_repair_attempt(state: DataRunState) -> None:
    kind = current_repair_kind(state)
    if not kind:
        return
    state.repair_attempts[kind] = state.repair_attempts.get(kind, 0) + 1


def _where_probe_repair_hint(state: DataRunState, *, error_text: str) -> str:
    return build_where_condition_probe_repair_hint(
        state.last_failed_sql_text,
        schema_table_columns=state.schema_table_columns or None,
        schema_column_hints=schema_column_hints_from_bindings(state.table_bindings) or None,
        error_message=error_text,
    )


def build_repair_message(
    state: DataRunState,
    *,
    semantic_intent: DataQuerySemanticIntent | None = None,
) -> str:
    if is_schema_fatal(state):
        return ""
    if state.requires_fresh_data and state.sql_before_schema and not state.schema_completed:
        return (
            "【Schema 顺序要求】本轮新数据查询必须先调用 get_dataset_schema 获取数据集定义，"
            "再调用 execute_sql_query。\n"
            f"{DataQueryPrompts.MUST_FETCH_SCHEMA}\n"
            "在获得有效 schema 前禁止生成或执行 SQL，也禁止直接回答用户。"
        )
    if state.schema_miss and not state.no_authorized_schema:
        controlled_hint = ""
        if state.controlled_schema_retry_keywords:
            controlled_hint = (
                f"本次重试必须使用平台受控重试 keywords：{state.controlled_schema_retry_keywords}。"
                "禁止另行发挥或改写为无关业务关键词。"
            )
        return (
            "【Schema 重试要求】上一轮 get_dataset_schema 未命中相关数据集定义。"
            f"{controlled_hint}"
            "请仅基于用户原问题中的业务对象、指标或维度重新调用 get_dataset_schema，"
            "禁止追加与业务对象无关的通用元数据词或其他系统关键词。"
            "在获得有效 schema 前禁止生成或执行 SQL，也禁止直接回答用户。"
        )
    if state.schema_needs_refinement:
        return (
            "【Schema 相关性不足】上一轮 get_dataset_schema 返回了低置信度或目录型结果，"
            "尚不足以可靠生成 SQL。请换用更具体的业务对象、指标、维度、系统名或同义词重新调用 get_dataset_schema。"
            "在获得相关性更明确的 schema 前禁止执行 SQL 或直接回答用户。"
        )
    if state.schema_ambiguous:
        return (
            "【Schema 歧义澄清要求】上一轮 get_dataset_schema 返回多个高置信度候选，"
            f"{state.schema_ambiguous_reason}。请停止生成 SQL，先用自然语言和 quick 按钮请用户确认"
            "具体数据集、指标口径或业务对象；确认前禁止执行 SQL。"
        )
    if state.schema_refresh_required and not state.schema_refreshed_after_sql_error:
        summary = (state.last_sql_error_summary or state.sql_error_message or "").strip()
        return (
            "【Schema 重查要求】上一轮 SQL 因字段/表/标识符引用错误失败，"
            f"错误信息：{summary[:800]}\n"
            f"{invalid_identifier_repair_hint(summary)}\n"
            f"{DataQueryPrompts.SCHEMA_REFERENCE_SQL_ERROR_REPAIR_GUIDE}\n"
            "必须先重新调用 get_dataset_schema，核对物理列名、表名与 JOIN 键。"
            "在未完成 Schema 重查前禁止 execute_sql_query，也禁止原样重复失败 SQL。"
        )
    if state.sql_static_risk:
        return (
            "【SQL 静态风险修正要求】上一轮 execute_sql_query 被平台拦截，"
            f"原因：{state.sql_static_risk_reason}\n"
            "请修正 SQL 后重新调用 execute_sql_query，例如补充时间范围、限制返回行数、"
            "或补齐 JOIN 条件。修正并执行成功前禁止直接回答用户。"
        )
    if state.time_range_anomaly:
        time_anchor = build_data_query_time_anchor_block()
        return (
            "【相对时间范围修正要求】上一轮 execute_sql_query 因 SQL 时间范围与问题中的相对时间不一致被平台拦截。\n"
            f"原因：{state.time_range_anomaly_reason}\n\n"
            f"{time_anchor}\n\n"
            f"{DataQueryPrompts.TIME_RANGE_ANOMALY_REPAIR_GUIDE}\n"
            "请仅修正 WHERE 中的日期起止条件后重新调用 execute_sql_query；"
            "修正并执行成功前禁止直接回答用户。"
        )
    if state.sql_sandbox_blocked:
        return (
            "【SQL 性能与安全阻断修正要求】上一轮 execute_sql_query 被前置安全沙箱网关拦截。\n"
            f"拦截原因：{state.sql_sandbox_blocked_reason}\n"
            "请修正 SQL 后重新调用 execute_sql_query。修正指南：\n"
            "1. 若提示含有笛卡尔积，请检查 JOIN 并补充 ON 或 USING 关联条件；\n"
            "2. 若提示估算扫描行数（Rows）过大，请添加有效的主键/索引列过滤、或加入特定时间范围条件以缩窄扫描区间。\n"
            "修正并执行成功前禁止直接回答用户。"
        )
    if state.sql_plan_missing:
        return (
            "【SQL Plan 补充要求】上一轮 execute_sql_query 因缺少结构化 SQL Plan 被平台拦截。\n"
            f"{DataQueryPrompts.HIGH_RISK_REQUIRE_PLAN}\n"
            f"{DataQueryPrompts.SQL_PLAN_ENFORCEMENT}\n"
            "请先输出完整 <sql_plan>{...}</sql_plan>，然后基于同一计划修正并调用 execute_sql_query。"
            "禁止跳过计划直接查数。"
        )
    if state.failed_sql_repeat_gate_block:
        error_text = (state.last_sql_error_summary or state.sql_error_message or "").strip()
        repair = (
            "【重复失败 SQL 修正要求】上一轮尝试原样重复执行已经失败的 SQL，已被平台拦截。"
            f"原始数据库错误：{error_text[:800]}\n"
            "请只围绕原始数据库错误修正 SQL 后再次调用 execute_sql_query；"
            "必须修改导致失败的字段名、表名、JOIN 条件、筛选条件、时间转换或聚合逻辑，"
            "禁止继续提交与上次完全相同的 SQL。SQL 成功前禁止直接回答用户。"
        )
        repair += sql_repair_taxonomy_hint(error_text)
        repair += invalid_identifier_repair_hint(error_text)
        if is_schema_reference_sql_error(error_text):
            repair += f"\n\n{DataQueryPrompts.SCHEMA_REFERENCE_SQL_ERROR_REPAIR_GUIDE}"
        if is_where_condition_sql_error(error_text):
            repair += f"\n\n{DataQueryPrompts.WHERE_CONDITION_PROBE_REPAIR_GUIDE}"
            if state.where_condition_diagnostic_summary:
                repair += f"\n\n{state.where_condition_diagnostic_summary}"
            else:
                repair += _where_probe_repair_hint(state, error_text=error_text)
            if is_invalid_number_sql_error(error_text):
                repair += f"\n\n{DataQueryPrompts.INVALID_NUMBER_SQL_ERROR_REPAIR_GUIDE}"
        repair += cross_dataset_scope_repair_hint(error_text)
        return repair
    if state.sql_error:
        error_text = (state.last_sql_error_summary or state.sql_error_message or "").strip()
        repair = (
            "【SQL 修正要求】上一轮 execute_sql_query 执行失败。"
            f"错误信息：{error_text[:800]}\n"
            "请基于已获得的 get_dataset_schema 结果修正 SQL，并再次调用 execute_sql_query。"
            "禁止原样重复提交与上次完全相同的失败 SQL。"
            "在 SQL 成功前禁止直接回答用户。"
        )
        repair += sql_repair_taxonomy_hint(error_text)
        if state.last_failed_sql_normalized:
            repair += (
                "\n\n【禁止重复 SQL】上次失败 SQL 归一化后与当前尝试一致时，平台将直接拦截。"
                "必须修改至少一处：列名、表名、JOIN、WHERE、时间范围或聚合逻辑。"
            )
        err_lower = error_text.lower()
        repair += invalid_identifier_repair_hint(error_text)
        repair += cross_dataset_scope_repair_hint(error_text)
        if is_schema_reference_sql_error(error_text):
            repair += f"\n\n{DataQueryPrompts.SCHEMA_REFERENCE_SQL_ERROR_REPAIR_GUIDE}"
        if is_where_condition_sql_error(error_text):
            repair += f"\n\n{DataQueryPrompts.WHERE_CONDITION_PROBE_REPAIR_GUIDE}"
            if state.where_condition_diagnostic_summary:
                repair += f"\n\n{state.where_condition_diagnostic_summary}"
            else:
                repair += _where_probe_repair_hint(state, error_text=error_text)
            if is_invalid_number_sql_error(error_text):
                repair += f"\n\n{DataQueryPrompts.INVALID_NUMBER_SQL_ERROR_REPAIR_GUIDE}"
        if "invalid expression" in err_lower or "unexpected token" in err_lower:
            repair += f"\n\n{DataQueryPrompts.SQL_PAGINATION_SYNTAX_GUIDE}"
        return repair
    if state.empty_sql_result:
        repair = DataQueryPrompts.empty_result_recheck(
            state.empty_sql_reason,
            executed_sql=state.empty_sql_text,
        )
        if state.empty_filter_diagnostic_summary:
            repair = f"{repair}\n\n{state.empty_filter_diagnostic_summary}"
        semantic_repair = format_empty_result_semantic_repair_context(
            semantic_intent,
            diagnostics=state.empty_filter_diagnostics,
        )
        if semantic_repair:
            repair = f"{repair}\n\n{semantic_repair}"
        return repair
    if state.ratio_anomaly:
        return DataQueryPrompts.ratio_anomaly_recheck(state.ratio_anomaly_reason)
    if state.duration_anomaly:
        return (
            "【时间差/时延结果异常复核】上一轮 execute_sql_query 返回了明显异常的时间差、"
            f"时延或时长字段。原因：{state.duration_anomaly_reason}\n"
            "请检查 SQL 中时间字段的相减方向、时区口径、now()/当前时间来源、单位换算（秒/毫秒/分钟/小时），"
            "修正 SQL 后重新调用 execute_sql_query。修正并执行成功前禁止直接回答用户。"
        )
    if state.tool_loop_fuse_triggered:
        reason = (state.tool_loop_fuse_reason or "").strip()
        return (
            "【工具循环熔断复核】检测到重复或无效的工具调用模式，已自动中止当前 ReAct 流。\n"
            f"原因：{reason[:600]}\n"
            "请停止重复提交相同工具参数；必须更换 get_dataset_schema 的 keywords，"
            "或修正 execute_sql_query 的 SQL（字段、WHERE、JOIN、时间范围至少改一处）后重试。"
            "禁止在未完成有效查数前直接回答用户。"
        )
    if state.diagnostic_sql_pending_final:
        return (
            "【最终 SQL 执行要求】上一轮 execute_sql_query 是诊断 SQL，只能用于定位候选值、"
            "时间范围、子查询或 JOIN 问题，不能作为最终业务结论。"
            "请基于诊断证据修正原查询，并立即调用 execute_sql_query 执行最终 SQL；"
            "最终 SQL 成功前禁止直接回答用户。"
        )
    if (
        state.requires_fresh_data
        and state.requires_sql_query
        and state.schema_completed
        and state.sql_plan_seen
        and not state.sql_completed
        and not state.ready_to_answer
    ):
        return (
            "【查数顺序要求】你已输出中间推理文本，但尚未执行 execute_sql_query。\n"
            f"{DataQueryPrompts.FORCE_SQL_AFTER_SCHEMA}\n"
            "禁止直接回答用户，必须先完成 SQL 查数。"
        )
    if (
        state.requires_fresh_data
        and state.blocked_content.strip()
        and not state.ready_to_answer
    ):
        if not state.schema_completed:
            return (
                "【查数顺序要求】本轮新数据查询尚未完成 get_dataset_schema 与 execute_sql_query，"
                "禁止直接回答用户。\n"
                f"{DataQueryPrompts.MUST_FETCH_SCHEMA}\n"
                "请先调用 get_dataset_schema 获取数据集定义，再调用 execute_sql_query 查数。"
            )
        if state.requires_sql_query and not state.sql_completed:
            return (
                "【查数顺序要求】你已获取数据集 Schema，但尚未执行 execute_sql_query。\n"
                f"{DataQueryPrompts.FORCE_SQL_AFTER_SCHEMA}\n"
                "禁止直接回答用户，必须先完成 SQL 查数。"
            )
    return ""


def reset_state_for_repair(state: DataRunState) -> None:
    repair_kind = current_repair_kind(state)
    state.blocked_content = ""
    state.full_content = ""
    state.content_emitted = False
    state.ignore_text_block = False
    state.active_text_block_id = ""
    state.text_blocks_emitted_since_last_tool = 0
    state.current_text_block_emitted = False
    state.halt_current_react = False
    state.sql_completed = False
    state.sql_error = False
    state.sql_error_message = ""
    state.empty_sql_result = False
    state.empty_sql_reason = ""
    state.empty_sql_text = ""
    state.where_condition_diagnostics = []
    state.where_condition_diagnostic_summary = ""
    state.where_condition_auto_retry_sql = ""
    state.last_failed_sql_text = ""
    state.sql_plan_missing = False
    state.sql_before_schema = False
    state.sql_static_risk = False
    state.sql_static_risk_reason = ""
    state.time_range_anomaly = False
    state.time_range_anomaly_reason = ""
    state.sql_sandbox_blocked = False
    state.sql_sandbox_blocked_reason = ""
    state.failed_sql_repeat_gate_block = False
    state.preflight_fail_signatures = {}
    state.platform_auto_sql_attempts = 0
    if repair_kind in {"empty_sql_result", "ratio_anomaly", "duration_anomaly"}:
        state.expecting_final_sql_after_diagnostic = True
    state.diagnostic_sql_pending_final = False
    state.ratio_anomaly = False
    state.ratio_anomaly_reason = ""
    state.duration_anomaly = False
    state.duration_anomaly_reason = ""
    state.last_successful_sql_output = None
    state.last_successful_sql_args = {}
    state.successful_sqls = {}
    state.sql_citation_counter = 0
    state.emitted_sql_citation_signatures = []
    state.schema_miss = False
    state.schema_needs_refinement = False
    state.tool_loop_detector = ToolLoopDetector()
    state.tool_call_signatures = {}
    if repair_kind != "tool_loop_fuse":
        state.tool_loop_fuse_triggered = False
        state.tool_loop_fuse_reason = ""


def build_repair_title(state: DataRunState) -> str:
    if state.requires_fresh_data and state.sql_before_schema and not state.schema_completed:
        return "必须先检索数据集定义"
    if state.schema_miss and not state.no_authorized_schema:
        return "重试检索数据集定义"
    if state.schema_needs_refinement:
        return "优化数据集定义检索"
    if state.schema_ambiguous:
        return "确认数据集或指标口径"
    if state.schema_refresh_required and not state.schema_refreshed_after_sql_error:
        return "必须先重查数据集定义"
    if state.sql_sandbox_blocked:
        return "修正性能超限 SQL"
    if state.sql_static_risk:
        return "修正高风险 SQL"
    if state.time_range_anomaly:
        return "修正 SQL 时间范围"
    if state.sql_plan_missing:
        return "补充 SQL 计划"
    if state.failed_sql_repeat_gate_block:
        return "修正重复失败 SQL"
    if state.sql_error:
        return "修正 SQL 执行错误"
    if state.empty_sql_result:
        return "空结果筛选复核"
    if state.ratio_anomaly:
        return "比率/占比异常复核"
    if state.duration_anomaly:
        return "时间差/时延异常复核"
    if state.tool_loop_fuse_triggered:
        return "停止重复工具调用"
    if state.diagnostic_sql_pending_final:
        return "执行最终 SQL"
    if (
        state.requires_fresh_data
        and state.blocked_content.strip()
        and not state.ready_to_answer
    ):
        if not state.schema_completed:
            return "必须先完成查数流程"
        if state.requires_sql_query and not state.sql_completed:
            return "必须先执行 SQL 查数"
    if (
        state.requires_fresh_data
        and state.requires_sql_query
        and state.schema_completed
        and state.sql_plan_seen
        and not state.sql_completed
        and not state.ready_to_answer
    ):
        return "必须先执行 SQL 查数"
    return "修正 SQL 查询"


def resolve_force_execute_sql_tool_choice(state: DataRunState) -> Any | None:
    from agentscope.tool import ToolChoice

    if (
        state.requires_fresh_data
        and state.requires_sql_query
        and state.schema_completed
        and not is_schema_fatal(state)
        and not state.sql_completed
    ):
        return ToolChoice(mode="execute_sql_query")
    return None


def resolve_initial_tool_choice(state: DataRunState) -> Any | None:
    if state.requires_sql_plan and not state.sql_plan_seen:
        return None
    return resolve_force_execute_sql_tool_choice(state)


def resolve_repair_tool_choice(state: DataRunState) -> Any | None:
    from agentscope.tool import ToolChoice

    if state.requires_fresh_data and state.sql_before_schema and not state.schema_completed:
        return ToolChoice(mode="get_dataset_schema")
    if state.schema_miss and not state.no_authorized_schema:
        return ToolChoice(mode="get_dataset_schema")
    if state.schema_needs_refinement:
        return ToolChoice(mode="get_dataset_schema")
    if state.schema_ambiguous:
        return None
    if state.schema_refresh_required and not state.schema_refreshed_after_sql_error:
        return ToolChoice(mode="get_dataset_schema")
    if state.sql_plan_missing:
        return None
    if state.sql_sandbox_blocked:
        return ToolChoice(mode="execute_sql_query")
    if state.sql_static_risk:
        return ToolChoice(mode="execute_sql_query")
    if state.time_range_anomaly:
        return ToolChoice(mode="execute_sql_query")
    if state.failed_sql_repeat_gate_block:
        return ToolChoice(mode="required")
    if state.ratio_anomaly:
        return ToolChoice(mode="required")
    if state.duration_anomaly:
        return ToolChoice(mode="execute_sql_query")
    if state.tool_loop_fuse_triggered:
        if state.schema_completed:
            return ToolChoice(mode="execute_sql_query")
        return ToolChoice(mode="get_dataset_schema")
    if state.empty_sql_result or state.diagnostic_sql_pending_final:
        return ToolChoice(mode="execute_sql_query")
    if state.sql_error:
        return ToolChoice(mode="required")
    if (
        state.requires_fresh_data
        and state.requires_sql_query
        and state.schema_completed
        and state.sql_plan_seen
        and not state.sql_completed
        and not state.ready_to_answer
    ):
        return resolve_force_execute_sql_tool_choice(state)
    if (
        state.requires_fresh_data
        and state.requires_sql_query
        and state.blocked_content.strip()
        and not state.ready_to_answer
    ):
        if not state.schema_completed:
            return ToolChoice(mode="get_dataset_schema")
        return resolve_force_execute_sql_tool_choice(state)
    return None
