from __future__ import annotations

import json
import logging
import re
import uuid
import html
import xml.etree.ElementTree as ET
from typing import Any, AsyncGenerator, Dict, List, Optional
import pandas as pd
import duckdb

from app.core.context import get_current_agent_context
from app.core.orm import AsyncSessionLocal
from app.services.permission_service import PermissionService
from app.services.metadata_service import MetadataService
from app.services.sql_query_execution_service import execute_sql_query_core
from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
from app.services.ai.runtime.agentscope.messages import system_user_prompt_messages
from app.services.ai.config import AgentConfigProvider
from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.ai.federated_column_labels import (
    build_column_label_map,
    extract_alias_term_hints_from_join_sql,
    extract_column_term_map_from_schema,
    format_column_label_guide,
    load_column_term_map_for_datasets,
    merge_column_term_maps,
)
from app.services.ai.federated_sql_repair import (
    build_repair_schema_search_keywords,
    build_sql_repair_guidance,
    detect_sql_error,
    is_cross_dataset_scope_sql_error,
    is_non_retryable_permission_error,
    is_retryable_sql_error,
    merge_repair_schema_snippets,
    normalize_sql_text,
)
from app.services.sql_query_execution_service import dialect_from_data_source
from app.services.ai.time_anchor import (
    build_time_range_gate_message,
    detect_time_range_mismatch,
)

logger = logging.getLogger(__name__)

MAX_FEDERATED_ROWS = 1000
# 子查询注册进 DuckDB 时的行上限。必须显著大于最终 join 输出上限，
# 否则「先截断子查询再 join/聚合」会丢行导致关联缺失、汇总失真。
MAX_FEDERATED_SUBQUERY_ROWS = 50000
MAX_FEDERATED_PLAN_REPAIR_ROUNDS = 10
# 按失败节点（子查询 / memory_join）分别计数，每节点最多局部 repair 次数。
MAX_FEDERATED_SQL_REPAIR_PER_NODE = 10


def make_markdown_table(
    columns: list,
    rows: list,
    *,
    column_labels: dict[str, str] | None = None,
) -> str:
    if not columns or not rows:
        return "无结果数据。"

    def _header_name(col: Any) -> str:
        raw = col["name"] if isinstance(col, dict) else str(col)
        if column_labels:
            labeled = column_labels.get(raw) or column_labels.get(str(raw).lower())
            if labeled:
                return labeled
        return str(raw)

    headers = [_header_name(c) for c in columns]
    lines = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for r in rows:
        row_str = []
        for val in r:
            s = str(val).replace("\n", " ") if val is not None else ""
            row_str.append(s)
        lines.append("| " + " | ".join(row_str) + " |")
    return "\n".join(lines)


class FederatedQueryExecutor:
    """跨数据集 DuckDB 联邦查询执行器"""

    def __init__(
        self,
        agent_runner: Any,
        schema_output: str,
        datasets: list[str],
    ):
        self.agent_runner = agent_runner
        self.schema_output = schema_output
        self.datasets = datasets
        self.user_info = agent_runner.user_info
        self.trace_buffer = agent_runner.trace_buffer

    async def execute(
        self,
        runtime_messages: List[Any],
        system_prompt: str,
        user_question: str,
        *,
        _repair_attempt: int = 0,
        _repair_context: Optional[Dict[str, Any]] = None,
        _original_user_question: Optional[str] = None,
        _subquery_cache: Optional[Dict[str, str]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from app.services.ai.runtime.agentscope.trace_context import TraceSpanContext

        original_user_question = _original_user_question or user_question
        # 跨 repair 轮复用已成功的子查询结果，避免每轮重跑全部子查询造成的重复 DB 负载。
        # key = (dataset_name, 归一化 SQL)，仅命中完全相同的成功子查询才复用。
        if _subquery_cache is None:
            _subquery_cache = {}
        # 记录本次执行中发生的「降级留空 / 行截断」情况，最终注入 synthesis，避免把不完整结果当完整结论解读。
        degraded_datasets: list[str] = []
        truncated_notes: list[str] = []
        
        async with TraceSpanContext(
            trace_buffer=self.trace_buffer,
            event_type="agent_execution",
            span_name="FederatedQueryExecutor",
        ) as span:
            
            # 1. 计划生成日志
            plan_log_id = f"fed_plan_{uuid.uuid4().hex[:8]}"
            yield {
                "type": "log",
                "id": plan_log_id,
                "title": "生成跨源联邦查询计划",
                "details": "正在分析跨数据集元数据，并编排分布式子查询计划...",
                "status": "pending",
            }
            
            plan_output = ""
            try:
                llm = await AgentConfigProvider.get_configured_llm(
                    streaming=False,
                    config=getattr(self.agent_runner, "config", None),
                )
                chat_client = chat_client_from_handle(llm)
                
                # 从 schema 中提取数据集与物理数据库类型的映射，用于方言约束
                dataset_dialect_map = self._extract_dialect_map(self.schema_output)
                plan_prompt = DataQueryPrompts.build_federated_plan_prompt(
                    self.schema_output,
                    original_user_question,
                    dataset_dialect_map=dataset_dialect_map,
                )
                if _repair_context:
                    plan_prompt = self._build_federated_plan_repair_prompt(
                        plan_prompt,
                        _repair_context,
                        _repair_attempt,
                    )
                plan_messages = system_user_prompt_messages(plan_prompt, user_prompt=original_user_question)
                
                plan_output = await chat_client.generate_text(plan_messages)
                plan_output = plan_output.strip()
                
                # 解析 XML 执行计划
                sub_queries, join_sql = self._parse_federated_plan(plan_output)
                
                if not sub_queries:
                    raise ValueError(f"联邦计划生成失败：未能成功提取到有效的子查询节点。 LLM 输出内容:\n{plan_output}")
                if not join_sql:
                    raise ValueError(f"联邦计划生成失败：未能提取到有效的 <memory_join> 逻辑。 LLM 输出内容:\n{plan_output}")
                
                yield {
                    "type": "log",
                    "id": plan_log_id,
                    "title": "生成跨源联邦查询计划",
                    "details": f"联邦计划编排完成，共规划了 {len(sub_queries)} 个子查询。计划明细：\n{plan_output}",
                    "status": "success",
                }
                
            except Exception as e:
                logger.error("[FederatedQueryExecutor] Plan generation failed: %s", e, exc_info=True)
                # 计划生成/XML 解析失败时，只要还有 repair 预算就重试一轮（把原始输出回灌让 LLM 自纠），
                # 不再像以前那样直接 abort。
                if _repair_attempt < MAX_FEDERATED_PLAN_REPAIR_ROUNDS:
                    yield {
                        "type": "log",
                        "id": f"fed_repair_{uuid.uuid4().hex[:8]}",
                        "title": "修复联邦查询计划",
                        "details": (
                            "联邦计划生成或 XML 解析失败，正在基于错误重新生成完整联邦计划。"
                            f"\n错误信息: {str(e)}"
                        ),
                        "status": "warning",
                    }
                    repair_context = {
                        "stage": "联邦计划生成",
                        "dataset_name": "plan",
                        "failed_sql": "",
                        "error": str(e),
                        "previous_plan": plan_output,
                    }
                    async for chunk in self.execute(
                        runtime_messages,
                        system_prompt,
                        original_user_question,
                        _repair_attempt=_repair_attempt + 1,
                        _repair_context=repair_context,
                        _original_user_question=original_user_question,
                        _subquery_cache=_subquery_cache,
                    ):
                        yield chunk
                    return
                yield {
                    "type": "log",
                    "id": plan_log_id,
                    "title": "生成联邦查询计划失败",
                    "details": f"错误: {str(e)}",
                    "status": "error",
                }
                yield {
                    "content": f"\n❌ 跨源联邦查询计划编排失败：{str(e)}",
                    "status": "error"
                }
                return

            # 2. 执行子查询并注册到 DuckDB
            duckdb_conn = duckdb.connect(
                database=":memory:",
                config={"enable_external_access": False},
            )
            try:
                user_id = self._current_user_id()
                is_admin = self._current_user_is_admin()
                current_agent_context = get_current_agent_context()
                user_dimensions = (
                    getattr(current_agent_context, "user_dimensions", None)
                    if current_agent_context is not None
                    else None
                )
                
                async with AsyncSessionLocal() as session:
                    perm_service = PermissionService(session)
                    subquery_citation_sources: list[dict[str, Any]] = []

                    for idx, sq in enumerate(sub_queries):
                        dataset_name = sq["dataset_name"]
                        temp_table = sq["temp_table"]
                        sub_sql = sq["sql"]
                        is_primary = idx == 0
                        node_repair_count = 0
                        failed_sql_signatures: set[str] = set()

                        while True:
                            dataset = None
                            sub_log_id = f"fed_sub_{uuid.uuid4().hex[:8]}_{idx}"
                            yield {
                                "type": "log",
                                "id": sub_log_id,
                                "title": f"执行子查询 ({dataset_name})",
                                "details": (
                                    f"正在执行数据集 '{dataset_name}' 的子查询，"
                                    f"注册为临时表 '{temp_table}'..."
                                    + (f"（局部 repair 第 {node_repair_count} 次）" if node_repair_count else "")
                                ),
                                "status": "pending",
                            }

                            try:
                                dataset = await MetadataService.get_dataset_by_name(session, dataset_name)
                                if not dataset:
                                    raise ValueError(f"元数据中未找到指定的数据集: '{dataset_name}'。")

                                if not is_admin:
                                    if not user_id:
                                        raise PermissionError(
                                            f"未提供有效的用户身份，无权访问数据集: '{dataset_name}'"
                                        )
                                    has_perm = await perm_service.check_permission(
                                        int(user_id), "metadata", str(dataset.id)
                                    )
                                    if not has_perm:
                                        raise PermissionError(
                                            f"无权访问数据集: '{dataset_name}' (ID: {dataset.id})"
                                        )

                                time_range_risk = detect_time_range_mismatch(
                                    original_user_question, sub_sql
                                )
                                if time_range_risk:
                                    raise ValueError(build_time_range_gate_message(time_range_risk))

                                cache_key = f"{dataset.name}::{self._normalize_sub_sql(sub_sql)}"
                                cached_res = _subquery_cache.get(cache_key)
                                cache_hit = cached_res is not None
                                if cache_hit:
                                    res_str = cached_res
                                else:
                                    async with TraceSpanContext(
                                        trace_buffer=self.trace_buffer,
                                        event_type="tool_call",
                                        span_name="execute_sql_query",
                                        tool_name="execute_sql_query",
                                        tool_input={
                                            "sql": sub_sql,
                                            "data_source": dataset.data_source,
                                            "dataset_name": dataset.name,
                                        },
                                    ) as sub_span:
                                        res_str = await execute_sql_query_core(
                                            session,
                                            sql=sub_sql,
                                            data_source=dataset.data_source,
                                            dataset_name=dataset.name,
                                            user_id=int(user_id) if user_id else None,
                                            user_dimensions=user_dimensions or None,
                                            agent_context=current_agent_context,
                                            is_admin=is_admin,
                                            bypass_table_auth=False,
                                        )
                                        sub_span.set_output(res_str)

                                is_err, err_msg = detect_sql_error(res_str)
                                if is_err:
                                    raise ValueError(err_msg or res_str)

                                _subquery_cache[cache_key] = res_str
                                res_data = json.loads(res_str)
                                col_names = [col["name"] for col in res_data.get("columns", [])]
                                raw_items = res_data.get("items") or res_data.get("rows") or []
                                items = self._limit_rows(raw_items, max_rows=MAX_FEDERATED_SUBQUERY_ROWS)
                                if len(raw_items) > len(items):
                                    limit_note = (
                                        f"（原始返回 {len(raw_items)} 行，已截断到 "
                                        f"{MAX_FEDERATED_SUBQUERY_ROWS} 行，join/聚合结果可能不完整）"
                                    )
                                    truncated_notes.append(
                                        f"数据集 '{dataset_name}' 子查询返回 {len(raw_items)} 行，"
                                        f"已截断到 {MAX_FEDERATED_SUBQUERY_ROWS} 行参与关联"
                                    )
                                else:
                                    limit_note = ""
                                cache_note = "（命中子查询结果缓存，未重跑）" if cache_hit else ""

                                df = pd.DataFrame(items, columns=col_names)
                                duckdb_conn.register(temp_table, df)
                                subquery_citation_sources.append(
                                    {
                                        "dataset_name": dataset_name,
                                        "data_source": dataset.data_source if dataset is not None else "",
                                        "sql": sub_sql,
                                        "row_count": len(items),
                                        "columns": col_names,
                                        "items": items[:3],
                                    }
                                )
                                yield {
                                    "type": "log",
                                    "id": sub_log_id,
                                    "title": f"执行子查询 ({dataset_name})",
                                    "details": (
                                        f"子查询执行成功{cache_note}。已导入临时表 '{temp_table}' "
                                        f"({len(items)} 行数据){limit_note}。\nSQL:\n{sub_sql}"
                                    ),
                                    "status": "success",
                                }
                                break

                            except Exception as e:
                                error_text = str(e)
                                logger.error(
                                    "[FederatedQueryExecutor] Subquery execution failed on dataset %s: %s",
                                    dataset_name,
                                    e,
                                    exc_info=True,
                                )

                                if is_non_retryable_permission_error(error_text):
                                    yield {
                                        "type": "log",
                                        "id": sub_log_id,
                                        "title": f"执行子查询 ({dataset_name}) 失败",
                                        "details": f"错误信息: {error_text}\nSQL:\n{sub_sql}",
                                        "status": "error",
                                    }
                                    yield {
                                        "content": (
                                            f"\n❌ 跨源联邦子查询失败（数据集: '{dataset_name}'）：{error_text}"
                                        ),
                                        "status": "error",
                                    }
                                    return

                                if (
                                    is_cross_dataset_scope_sql_error(error_text)
                                    and _repair_attempt < MAX_FEDERATED_PLAN_REPAIR_ROUNDS
                                ):
                                    yield {
                                        "type": "log",
                                        "id": f"fed_repair_{uuid.uuid4().hex[:8]}",
                                        "title": "修复联邦查询计划（跨数据集引表）",
                                        "details": (
                                            f"子查询 ({dataset_name}) 引用了不属于该数据集的表，"
                                            "局部 SQL repair 无法解决，正在重新生成完整联邦计划。"
                                            f"\n错误信息: {error_text}\nSQL:\n{sub_sql}"
                                        ),
                                        "status": "warning",
                                    }
                                    repair_context = {
                                        "stage": f"子查询 ({dataset_name}) 跨数据集引表",
                                        "dataset_name": dataset_name,
                                        "failed_sql": sub_sql,
                                        "error": error_text,
                                        "previous_plan": plan_output,
                                    }
                                    async for chunk in self.execute(
                                        runtime_messages,
                                        system_prompt,
                                        original_user_question,
                                        _repair_attempt=_repair_attempt + 1,
                                        _repair_context=repair_context,
                                        _original_user_question=original_user_question,
                                        _subquery_cache=_subquery_cache,
                                    ):
                                        yield chunk
                                    return

                                can_local_repair = is_retryable_sql_error(error_text)
                                if (
                                    can_local_repair
                                    and node_repair_count < MAX_FEDERATED_SQL_REPAIR_PER_NODE
                                ):
                                    norm_sql = normalize_sql_text(sub_sql)
                                    repeat_blocked = norm_sql in failed_sql_signatures
                                    explain_context = ""
                                    schema_snippet = self._extract_schema_snippet_for_dataset(
                                        self.schema_output,
                                        dataset_name,
                                    )
                                    repair_schema_keywords = ""
                                    try:
                                        if dataset is not None:
                                            schema_snippet, repair_schema_keywords = (
                                                await self._refresh_schema_snippet_for_repair(
                                                    session,
                                                    failed_sql=sub_sql,
                                                    dataset_name=dataset_name,
                                                    error_text=error_text,
                                                    data_source=dataset.data_source,
                                                    base_snippet=schema_snippet,
                                                    user_id=user_id,
                                                    is_admin=is_admin,
                                                )
                                            )
                                            explain_context = await self._fetch_subquery_explain_for_repair(
                                                session,
                                                sub_sql=sub_sql,
                                                dataset=dataset,
                                                user_id=user_id,
                                                user_dimensions=user_dimensions,
                                                agent_context=current_agent_context,
                                                is_admin=is_admin,
                                            )
                                    except Exception as schema_exc:
                                        logger.warning(
                                            "[FederatedQueryExecutor] Repair schema refresh failed on %s: %s",
                                            dataset_name,
                                            schema_exc,
                                            exc_info=True,
                                        )
                                    yield {
                                        "type": "log",
                                        "id": f"fed_repair_{uuid.uuid4().hex[:8]}",
                                        "title": "修复联邦子查询 SQL",
                                        "details": (
                                            f"子查询 ({dataset_name}) 执行失败，"
                                            f"正在按单源 SQL repair 方式局部修正该节点 SQL"
                                            f"（第 {node_repair_count + 1}/{MAX_FEDERATED_SQL_REPAIR_PER_NODE} 次）。"
                                            + (
                                                f"\nSchema 检索词: {repair_schema_keywords}"
                                                if repair_schema_keywords
                                                else ""
                                            )
                                            + f"\n错误信息: {error_text}\nSQL:\n{sub_sql}"
                                        ),
                                        "status": "warning",
                                    }
                                    try:
                                        sub_sql = await self._repair_federated_node_sql(
                                            chat_client,
                                            node_kind="sub_query",
                                            user_question=original_user_question,
                                            plan_output=plan_output,
                                            dataset_name=dataset_name,
                                            temp_table=temp_table,
                                            failed_sql=sub_sql,
                                            error_text=error_text,
                                            repair_attempt=node_repair_count + 1,
                                            repeat_blocked=repeat_blocked,
                                            sub_queries=sub_queries,
                                            join_sql=join_sql,
                                            schema_snippet=schema_snippet,
                                            explain_context=explain_context,
                                        )
                                        sq["sql"] = sub_sql
                                        new_norm = normalize_sql_text(sub_sql)
                                        if new_norm == norm_sql:
                                            failed_sql_signatures.add(norm_sql)
                                        node_repair_count += 1
                                        continue
                                    except Exception as repair_err:
                                        logger.error(
                                            "[FederatedQueryExecutor] Subquery local repair failed: %s",
                                            repair_err,
                                            exc_info=True,
                                        )
                                        error_text = str(repair_err)

                                if is_primary:
                                    yield {
                                        "type": "log",
                                        "id": sub_log_id,
                                        "title": f"执行主表子查询 ({dataset_name}) 失败",
                                        "details": f"错误信息: {error_text}\nSQL:\n{sub_sql}",
                                        "status": "error",
                                    }
                                    yield {
                                        "content": (
                                            f"\n❌ 跨源联邦主表子查询失败（数据集: '{dataset_name}'）：{error_text}"
                                        ),
                                        "status": "error",
                                    }
                                    return

                                degraded_datasets.append(dataset_name)
                                yield {
                                    "type": "log",
                                    "id": sub_log_id,
                                    "title": f"执行子查询 ({dataset_name}) 失败，已自动降级",
                                    "details": (
                                        f"警告: 关联数据集 '{dataset_name}' 的查询失败，"
                                        f"相关字段已自动降级留空处理。错误信息: {error_text}\nSQL:\n{sub_sql}"
                                    ),
                                    "status": "warning",
                                }
                                yield {
                                    "content": (
                                        f"\n⚠️ 警告: 关联数据集 '{dataset_name}' 的查询失败，"
                                        "相关字段已自动降级留空处理。"
                                    ),
                                    "status": "warning",
                                }
                                col_names = self._infer_degraded_subquery_columns(
                                    sub_sql, dataset_name, dataset_dialect_map
                                )
                                df = pd.DataFrame(columns=col_names)
                                duckdb_conn.register(temp_table, df)
                                subquery_citation_sources.append(
                                    {
                                        "dataset_name": dataset_name,
                                        "data_source": dataset.data_source if dataset is not None else "",
                                        "sql": sub_sql,
                                        "row_count": 0,
                                        "columns": col_names,
                                        "items": [],
                                    }
                                )
                                break

                join_log_id = f"fed_join_{uuid.uuid4().hex[:8]}"
                join_repair_count = 0
                join_failed_signatures: set[str] = set()
                columns: list[dict[str, str]] = []
                join_rows: list[Any] = []
                final_data: dict[str, Any] = {}

                while True:
                    yield {
                        "type": "log",
                        "id": join_log_id,
                        "title": "内存联邦聚合计算",
                        "details": (
                            "正在 DuckDB 内存中对所有临时表执行多源关联与最终汇总计算..."
                            + (f"（局部 repair 第 {join_repair_count} 次）" if join_repair_count else "")
                        ),
                        "status": "pending",
                    }
                    try:
                        join_error = self._validate_memory_join_sql(join_sql)
                        if join_error:
                            raise ValueError(join_error)
                        duck_res = duckdb_conn.execute(join_sql)
                        raw_join_rows = duck_res.fetchall()
                        join_rows = self._limit_rows(raw_join_rows)
                        columns = self._columns_from_duckdb_description(duck_res.description)
                        if len(raw_join_rows) > len(join_rows):
                            limit_note = (
                                f"（原始产出 {len(raw_join_rows)} 行，已截断到 {MAX_FEDERATED_ROWS} 行）"
                            )
                            truncated_notes.append(
                                f"最终关联结果共 {len(raw_join_rows)} 行，仅展示前 {MAX_FEDERATED_ROWS} 行"
                            )
                        else:
                            limit_note = ""
                        final_data = {
                            "columns": columns,
                            "items": [list(r) for r in join_rows],
                        }
                        yield {
                            "type": "log",
                            "id": join_log_id,
                            "title": "内存联邦聚合计算",
                            "details": (
                                f"内存关联计算成功。汇总产出 {len(join_rows)} 行数据{limit_note}。\nSQL:\n{join_sql}"
                            ),
                            "status": "success",
                        }
                        break
                    except Exception as e:
                        error_text = str(e)
                        logger.error(
                            "[FederatedQueryExecutor] Memory join failed: %s",
                            e,
                            exc_info=True,
                        )
                        if (
                            is_retryable_sql_error(error_text)
                            and join_repair_count < MAX_FEDERATED_SQL_REPAIR_PER_NODE
                        ):
                            norm_sql = normalize_sql_text(join_sql)
                            repeat_blocked = norm_sql in join_failed_signatures
                            yield {
                                "type": "log",
                                "id": f"fed_repair_{uuid.uuid4().hex[:8]}",
                                "title": "修复联邦聚合 SQL",
                                "details": (
                                    "内存联邦聚合 SQL 执行失败，正在局部修正 memory_join SQL"
                                    f"（第 {join_repair_count + 1}/{MAX_FEDERATED_SQL_REPAIR_PER_NODE} 次）。"
                                    f"\n错误信息: {error_text}\nSQL:\n{join_sql}"
                                ),
                                "status": "warning",
                            }
                            try:
                                join_sql = await self._repair_federated_node_sql(
                                    chat_client,
                                    node_kind="memory_join",
                                    user_question=original_user_question,
                                    plan_output=plan_output,
                                    dataset_name="federated",
                                    temp_table="",
                                    failed_sql=join_sql,
                                    error_text=error_text,
                                    repair_attempt=join_repair_count + 1,
                                    repeat_blocked=repeat_blocked,
                                    sub_queries=sub_queries,
                                    join_sql=join_sql,
                                )
                                new_norm = normalize_sql_text(join_sql)
                                if new_norm == norm_sql:
                                    join_failed_signatures.add(norm_sql)
                                join_repair_count += 1
                                continue
                            except Exception as repair_err:
                                error_text = str(repair_err)
                        yield {
                            "type": "log",
                            "id": join_log_id,
                            "title": "内存联邦聚合计算失败",
                            "details": f"错误信息: {error_text}\nSQL:\n{join_sql}",
                            "status": "error",
                        }
                        yield {
                            "content": f"\n❌ 内存联邦计算失败：{error_text}",
                            "status": "error",
                        }
                        return

                # 4. 可视化分析与总结解读
                schema_term_map = extract_column_term_map_from_schema(self.schema_output)
                db_term_map = await load_column_term_map_for_datasets(session, self.datasets)
                term_map = merge_column_term_maps(
                    schema_term_map,
                    db_term_map,
                    extract_alias_term_hints_from_join_sql(join_sql, merge_column_term_maps(schema_term_map, db_term_map)),
                )
                column_names = [str(c.get("name") or "") for c in columns]
                column_labels = build_column_label_map(column_names, term_map)
                column_label_guide = format_column_label_guide(term_map, column_names, label_map=column_labels)
                final_md_table = make_markdown_table(columns, join_rows, column_labels=column_labels)
                
                # 保存至 "上一轮数据结果" 中供下一次追问与微调
                try:
                    await self.agent_runner._save_last_data_result_for_followups(
                        {
                            "sql": join_sql,
                            "data_source": "duckdb",
                            "dataset_name": "federated"
                        },
                        final_data
                    )
                except Exception as e:
                    logger.warning("[FederatedQueryExecutor] Failed to save last data result: %s", e)

                from app.services.ai.chatbi_citation_utils import build_federated_chatbi_citation_event

                federated_citation = build_federated_chatbi_citation_event(
                    tool_call_id=f"fed_cite_{uuid.uuid4().hex[:8]}",
                    subquery_sources=subquery_citation_sources,
                    join_sql=join_sql,
                    final_data=final_data,
                    dataset_names=self.datasets,
                )
                if federated_citation:
                    yield federated_citation

                # 调用 LLM 做最终的分析和可视化（附带数据完整性提示，避免把降级/截断结果当完整结论）
                data_caveats = self._build_data_caveats(degraded_datasets, truncated_notes)
                synthesis_prompt = DataQueryPrompts.build_federated_synthesis_prompt(
                    original_user_question,
                    final_md_table,
                    data_caveats=data_caveats,
                    dataset_names=self.datasets,
                    column_label_guide=column_label_guide,
                )
                
                # 流式输出总结（synthesis_prompt 已含用户问题，user_prompt 用默认值避免重复）
                llm_stream = await AgentConfigProvider.get_configured_llm(
                    streaming=True,
                    config=getattr(self.agent_runner, "config", None),
                )
                chat_client_stream = chat_client_from_handle(llm_stream)
                synthesis_messages = system_user_prompt_messages(synthesis_prompt)
                
                chunk_count = 0
                async for msg_chunk in chat_client_stream.stream_messages(synthesis_messages):
                    text = msg_chunk.content if isinstance(msg_chunk.content, str) else ""
                    if text:
                        chunk_count += 1
                        yield {"content": text}
                
                logger.info("[FederatedQueryExecutor] Synthesis complete. Total text chunks yielded: %d", chunk_count)
                yield {"content": "", "status": "success"}
            finally:
                duckdb_conn.close()

    @staticmethod
    def _parse_fixed_sql_response(raw: str) -> str:
        text = str(raw or "").strip()
        match = re.search(r"<fixed_sql>(.*?)</fixed_sql>", text, re.DOTALL | re.IGNORECASE)
        content = match.group(1).strip() if match else text
        if content.startswith("<![CDATA[") and content.endswith("]]>"):
            content = content[9:-3].strip()
        elif content.startswith("<![CDATA["):
            content = content[9:].strip()
        elif content.endswith("]]>"):
            content = content[:-3].strip()
        return html.unescape(content).strip()

    @staticmethod
    def _summarize_subqueries_for_prompt(sub_queries: list[dict[str, str]]) -> str:
        lines: list[str] = []
        for sq in sub_queries:
            lines.append(
                f"- dataset={sq.get('dataset_name')}, temp_table={sq.get('temp_table')}, "
                f"sql={str(sq.get('sql') or '')[:500]}"
            )
        return "\n".join(lines)

    async def _repair_federated_node_sql(
        self,
        chat_client: Any,
        *,
        node_kind: str,
        user_question: str,
        plan_output: str,
        dataset_name: str,
        temp_table: str,
        failed_sql: str,
        error_text: str,
        repair_attempt: int,
        repeat_blocked: bool,
        sub_queries: list[dict[str, str]],
        join_sql: str,
        schema_snippet: str = "",
        explain_context: str = "",
    ) -> str:
        repair_guidance = build_sql_repair_guidance(
            error_text,
            failed_sql,
            repeat_blocked=repeat_blocked,
            for_federated_node=True,
        )
        prompt = DataQueryPrompts.build_federated_node_repair_prompt(
            node_kind=node_kind,
            user_question=user_question,
            schema_context=self.schema_output,
            plan_output=plan_output,
            dataset_name=dataset_name,
            temp_table=temp_table,
            failed_sql=failed_sql,
            error_text=error_text,
            repair_attempt=repair_attempt,
            repair_guidance=repair_guidance,
            sub_queries_summary=self._summarize_subqueries_for_prompt(sub_queries),
            join_sql=join_sql,
            schema_snippet=schema_snippet,
            explain_context=explain_context,
        )
        messages = system_user_prompt_messages(prompt, user_prompt=user_question)
        raw = await chat_client.generate_text(messages)
        fixed_sql = self._parse_fixed_sql_response(raw)
        if not fixed_sql:
            raise ValueError(f"联邦 SQL 局部 repair 未能解析 fixed_sql。LLM 输出:\n{raw[:2000]}")
        return fixed_sql

    async def _refresh_schema_snippet_for_repair(
        self,
        session: Any,
        *,
        failed_sql: str,
        dataset_name: str,
        error_text: str,
        data_source: Optional[str],
        base_snippet: str,
        user_id: Optional[int],
        is_admin: bool,
    ) -> tuple[str, str]:
        """repair 时按失败 SQL 涉及表名调用 get_dataset_schema 逻辑补充 Schema。"""
        from app.services.chatbi_dataset_schema_service import fetch_dataset_schema_core

        dialect = dialect_from_data_source(data_source)
        keywords = build_repair_schema_search_keywords(
            failed_sql,
            dataset_name=dataset_name,
            error_text=error_text,
            sql_dialect=dialect,
        )
        if not keywords.strip():
            return base_snippet, ""

        refreshed = await fetch_dataset_schema_core(
            session,
            keywords=keywords,
            user_id=int(user_id) if user_id else None,
            is_admin=is_admin,
        )
        merged = merge_repair_schema_snippets(base_snippet, refreshed)
        return merged, keywords

    @staticmethod
    def _extract_schema_snippet_for_dataset(
        schema_output: str,
        dataset_name: str,
        *,
        max_chars: int = 3500,
    ) -> str:
        if not schema_output or not dataset_name:
            return ""
        lines = schema_output.splitlines()
        chunks: list[str] = []
        capture = False
        blank_run = 0
        pattern = re.compile(rf"^\s*dataset:\s*{re.escape(dataset_name)}\s*$", re.IGNORECASE)
        for line in lines:
            if pattern.match(line.strip()):
                capture = True
                blank_run = 0
            elif capture and re.match(r"^\s*dataset:\s*\S+", line.strip()) and not pattern.match(line.strip()):
                break
            if capture:
                chunks.append(line)
                if not line.strip():
                    blank_run += 1
                    if blank_run >= 2 and len(chunks) > 24:
                        break
                else:
                    blank_run = 0
                if sum(len(part) + 1 for part in chunks) > max_chars:
                    break
        text = "\n".join(chunks).strip()
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... [Schema 片段已截断]"
        return text

    @staticmethod
    def _format_explain_result_for_repair(raw: Any, explain_sql: str) -> str:
        text = str(raw or "").strip()
        if not text:
            return "EXPLAIN 返回空结果。"
        is_err, err_msg = detect_sql_error(raw)
        if is_err:
            return (
                "EXPLAIN 未能生成有效计划（常与执行错误同源，请重点检查 WHERE 中的类型转换/函数）。\n"
                f"EXPLAIN SQL:\n{explain_sql[:2000]}\n"
                f"EXPLAIN 返回:\n{(err_msg or text)[:2000]}"
            )
        try:
            parsed = json.loads(text)
            formatted = json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            formatted = text
        if len(formatted) > 3000:
            formatted = formatted[:3000] + "\n... [EXPLAIN 输出已截断]"
        return formatted

    async def _fetch_subquery_explain_for_repair(
        self,
        session: Any,
        *,
        sub_sql: str,
        dataset: Any,
        user_id: Optional[int],
        user_dimensions: Any,
        agent_context: Any,
        is_admin: bool,
    ) -> str:
        explain_sql = f"EXPLAIN {str(sub_sql or '').strip()}"
        try:
            result = await execute_sql_query_core(
                session,
                sql=explain_sql,
                data_source=dataset.data_source,
                dataset_name=dataset.name,
                user_id=int(user_id) if user_id else None,
                user_dimensions=user_dimensions or None,
                agent_context=agent_context,
                is_admin=is_admin,
                bypass_table_auth=False,
            )
        except Exception as exc:
            logger.warning(
                "[FederatedQueryExecutor] EXPLAIN for repair failed on dataset %s: %s",
                getattr(dataset, "name", dataset),
                exc,
                exc_info=True,
            )
            return f"EXPLAIN 调用异常: {exc}"
        return self._format_explain_result_for_repair(result, explain_sql)

    @staticmethod
    def _infer_degraded_subquery_columns(
        sub_sql: str,
        dataset_name: str,
        dataset_dialect_map: Optional[dict[str, str]],
    ) -> list[str]:
        col_names: list[str] = []
        try:
            import sqlglot
            from sqlglot import exp

            dialect = dataset_dialect_map.get(dataset_name) if dataset_dialect_map else None
            parsed = sqlglot.parse(sub_sql, read=dialect)
            if parsed:
                expression = parsed[0]
                if isinstance(expression, exp.Select):
                    cols: list[str] = []
                    for expr in expression.expressions:
                        alias = getattr(expr, "alias", None)
                        name = getattr(expr, "name", None)
                        if isinstance(expr, exp.Alias):
                            cols.append(expr.alias)
                        elif isinstance(expr, exp.Column):
                            cols.append(expr.name)
                        elif alias:
                            cols.append(alias)
                        elif name:
                            cols.append(name)
                        else:
                            cols.append(expr.sql(dialect))
                    col_names = [c.strip('`"\'') for c in cols if c]
        except Exception as parse_err:
            logger.warning(
                "[FederatedQueryExecutor] Failed to parse columns from failed subquery SQL: %s",
                parse_err,
                exc_info=True,
            )
        return col_names

    @staticmethod
    def _build_federated_plan_repair_prompt(
        base_prompt: str,
        repair_context: Dict[str, Any],
        repair_attempt: int,
    ) -> str:
        error = str(repair_context.get("error") or "").strip()
        failed_sql = str(repair_context.get("failed_sql") or "").strip()
        previous_plan = str(repair_context.get("previous_plan") or "").strip()
        stage = str(repair_context.get("stage") or "联邦查询执行").strip()
        dataset_name = str(repair_context.get("dataset_name") or "").strip()
        repair_guidance = build_sql_repair_guidance(
            error,
            failed_sql,
            for_federated_node=False,
        )
        return (
            f"{base_prompt}\n\n"
            "【上一轮联邦计划生成/解析失败，需要 repair 后重试】\n"
            f"repair_attempt: {repair_attempt}\n"
            f"失败阶段: {stage}\n"
            f"失败数据集: {dataset_name or '未知'}\n"
            f"错误信息:\n{error[:2000]}\n\n"
            f"失败 SQL:\n{failed_sql[:4000]}\n\n"
            f"上一轮完整联邦计划:\n{previous_plan[:8000]}\n\n"
            "【本轮 repair 要求】\n"
            "1. 必须重新输出完整 `<multi_dataset_plan>` XML，不要只输出局部 SQL 或解释文字。\n"
            "2. 禁止原样重复失败 SQL；必须围绕错误信息最小化修正字段名、表名、JOIN、WHERE、日期转换、时间边界或聚合逻辑。\n"
            "3. 不得通过扩大权限、跨数据集直连 JOIN、外部文件访问或写操作绕过平台约束。"
            f"\n{repair_guidance}"
        )

    def _current_user_id(self) -> Optional[int]:
        resolver = getattr(self.agent_runner, "_current_user_id", None)
        if callable(resolver):
            return resolver()
        if not self.user_info:
            return None
        raw_user_id = self.user_info.get("user_id") or self.user_info.get("id")
        if not raw_user_id:
            return None
        try:
            return int(raw_user_id)
        except (TypeError, ValueError):
            return None

    def _current_user_is_admin(self) -> bool:
        resolver = getattr(self.agent_runner, "_current_user_is_admin", None)
        if callable(resolver):
            return bool(resolver())
        if not self.user_info:
            return False
        if bool(self.user_info.get("is_admin")):
            return True
        role = str(
            self.user_info.get("role")
            or self.user_info.get("role_name")
            or ""
        ).strip().lower()
        return role in {"admin", "administrator", "平台管理员"}

    @staticmethod
    def _limit_rows(rows: list[Any], *, max_rows: int = MAX_FEDERATED_ROWS) -> list[Any]:
        if not isinstance(rows, list):
            return []
        return rows[:max_rows]

    @staticmethod
    def _normalize_sub_sql(sql: str) -> str:
        """子查询缓存 key 用的归一化（小写 + 折叠空白），仅用于完全相同 SQL 的复用判定。"""
        return " ".join(str(sql or "").strip().lower().split())

    @staticmethod
    def _build_data_caveats(degraded_datasets: list[str], truncated_notes: list[str]) -> str:
        """汇总降级/截断情况，供 synthesis prompt 显式标注数据完整性，避免把不完整结果当完整结论。"""
        parts: list[str] = []
        if degraded_datasets:
            uniq = list(dict.fromkeys(degraded_datasets))
            parts.append(
                "以下关联数据集查询失败，相关字段已降级留空，可能缺失："
                + "、".join(uniq)
            )
        if truncated_notes:
            uniq_notes = list(dict.fromkeys(truncated_notes))
            parts.append("以下数据存在行数截断，统计/汇总口径可能不完整：" + "；".join(uniq_notes))
        return "\n".join(parts)

    @staticmethod
    def _validate_memory_join_sql(sql: str) -> Optional[str]:
        sql_text = str(sql or "").strip()
        if not sql_text:
            return "内存联邦 SQL 为空。"
        sql_no_comments = re.sub(r"(--[^\n]*|/\*.*?\*/)", " ", sql_text, flags=re.DOTALL)
        if ";" in sql_no_comments.rstrip(";"):
            return "内存联邦 SQL 只允许单条 SELECT 查询。"
        if not re.match(r"^(SELECT|WITH)\b", sql_no_comments.strip(), flags=re.IGNORECASE):
            return "内存联邦 SQL 只允许 SELECT/WITH 查询。"
        blocked = re.search(
            r"\b(COPY|INSTALL|LOAD|ATTACH|DETACH|EXPORT|IMPORT|CREATE|INSERT|UPDATE|DELETE|DROP|ALTER|PRAGMA|CALL|"
            r"READ_CSV|READ_CSV_AUTO|READ_PARQUET|READ_JSON|READ_JSON_AUTO|READ_TEXT|READ_BLOB|HTTPFS)\b",
            sql_no_comments,
            flags=re.IGNORECASE,
        )
        if blocked:
            return f"内存联邦 SQL 禁止外部访问或写操作: {blocked.group(1)}"
        return None

    @staticmethod
    def _duckdb_type_to_result_type(type_name: Any) -> str:
        text = str(type_name or "").upper()
        if any(token in text for token in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "REAL", "HUGEINT", "UBIGINT")):
            return "number"
        if "BOOL" in text:
            return "boolean"
        if any(token in text for token in ("DATE", "TIME")):
            return "datetime"
        return "string"

    @classmethod
    def _columns_from_duckdb_description(cls, description: Any) -> list[dict[str, str]]:
        columns: list[dict[str, str]] = []
        for desc in description or []:
            name = str(desc[0]) if desc else ""
            type_name = desc[1] if len(desc) > 1 else ""
            columns.append({"name": name, "type": cls._duckdb_type_to_result_type(type_name)})
        return columns

    @staticmethod
    def _extract_dialect_map(schema_output: str) -> dict[str, str]:
        """从合并 Schema YAML 文本中提取 dataset_name -> data_source 的方言映射。
        
        Schema 每个 table chunk 中包含相邻的两行：
          dataset: <dataset_name>
          ...
          data_source: <type>   (如 mysql, clickhouse, oracle)
        
        此函数扫描所有这样的组合并去重，返回 {dataset_name: data_source} 字典。
        """
        dialect_map: dict[str, str] = {}
        # 匹配 "dataset: xxx" 后（允许中间有若干行）紧跟着 "data_source: yyy"
        # 因为 YAML 中 dataset 和 data_source 在同一 block 内出现，逐行扫描更健壮
        lines = schema_output.splitlines()
        current_dataset: str | None = None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("dataset:"):
                parts = stripped.split(":", 1)
                if len(parts) == 2:
                    current_dataset = parts[1].strip()
            elif stripped.startswith("data_source:") and current_dataset:
                parts = stripped.split(":", 1)
                if len(parts) == 2:
                    ds_type = parts[1].strip()
                    if ds_type and current_dataset not in dialect_map:
                        dialect_map[current_dataset] = ds_type
                    # 读到 data_source 后重置，避免错误归属
                    current_dataset = None
        return dialect_map

    def _parse_federated_plan(self, xml_content: str) -> tuple[list[dict], str]:
        """解析联邦 XML 执行计划，采用正则表达式容错提取"""
        sub_queries = []
        
        # 匹配 <sub_query dataset_name="..." temp_table="...">SQL</sub_query>
        sub_query_matches = re.finditer(
            r'<sub_query\s+dataset_name=["\']([^"\']+)["\']\s+temp_table=["\']([^"\']+)["\']\s*>(.*?)</sub_query>',
            xml_content,
            re.DOTALL | re.IGNORECASE
        )
        
        for m in sub_query_matches:
            ds_name = m.group(1).strip()
            temp_tb = m.group(2).strip()
            sql_content = m.group(3).strip()
            
            # 去掉 CDATA 包裹
            if sql_content.startswith("<![CDATA[") and sql_content.endswith("]]>"):
                sql_content = sql_content[9:-3].strip()
            elif sql_content.startswith("<![CDATA["):
                sql_content = sql_content[9:].strip()
            elif sql_content.endswith("]]>"):
                sql_content = sql_content[:-3].strip()
                
            # HTML 字符实体反转义
            sql_content = html.unescape(sql_content).strip()
            
            sub_queries.append({
                "dataset_name": ds_name,
                "temp_table": temp_tb,
                "sql": sql_content
            })
            
        join_match = re.search(r'<memory_join\s*>(.*?)</memory_join>', xml_content, re.DOTALL | re.IGNORECASE)
        join_sql = ""
        if join_match:
            join_sql = join_match.group(1).strip()
            if join_sql.startswith("<![CDATA[") and join_sql.endswith("]]>"):
                join_sql = join_sql[9:-3].strip()
            elif join_sql.startswith("<![CDATA["):
                join_sql = join_sql[9:].strip()
            elif join_sql.endswith("]]>"):
                join_sql = join_sql[:-3].strip()
                
            join_sql = html.unescape(join_sql).strip()
            
        return sub_queries, join_sql
