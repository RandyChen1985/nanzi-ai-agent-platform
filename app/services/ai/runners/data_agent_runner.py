from __future__ import annotations
import asyncio
import json
import logging
import re
import time
import uuid
import ast
from dataclasses import dataclass, field, replace
import inspect
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional
from agentscope.agent import Agent, ReActConfig
from app.schemas.agent import AgentExecutionStep, ChatConfig
from app.services.ai.data_query_turn_classifier import DataQueryTurnType, data_query_turn_type_label, resolve_data_query_turn_classification
from app.services.ai.intent_service import IntentType
from app.services.ai.config import AgentConfigProvider
from app.services.ai.executors.base import BaseExecutor
from app.services.ai.executors.common import convert_history_to_messages, normalize_messages_for_llm
from app.services.ai.chatbi_sql_user_messages import format_empty_filter_result_content, map_sql_tool_error_for_user
from app.services.ai.data_query_semantic_intent import DataQuerySemanticIntent, format_empty_result_semantic_repair_context, semantic_intent_from_dict, semantic_intent_to_dict
from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.ai.grounding.ledger import EvidenceLedger
from app.services.ai.grounding.models import EvidenceType
from app.services.ai.grounding.policy import (
    FactRequirement,
    contains_grounding_fact_signal,
)
from app.services.ai.grounding.service import GroundingAuditResult, GroundingService
from app.services.ai.time_anchor import TIME_RANGE_GATE_PREFIX, build_data_query_time_anchor_block, build_time_range_gate_message, detect_time_range_mismatch
from app.services.ai.multimodal_support import ensure_multimodal_compatible, resolve_runtime_model_name
from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
from app.services.ai.runtime.agentscope.messages import RuntimeContentBlock, RuntimeMessage, system_user_prompt_messages
from app.services.ai.runtime.agentscope.agent_runtime import build_model_config, build_tools_fingerprint, load_context_config
from app.services.ai.runtime.agentscope.event_stream import is_interrupt_sse_chunk, map_standard_agentscope_event
from app.services.ai.runtime.agentscope.stream_reconcile import truncate_for_context
from app.services.ai.runtime.agentscope.session_lock import SessionLockTimeout, agentscope_session_lock
from app.services.ai.runtime.agentscope.state_store import agent_state_store
from app.services.ai.runtime.agentscope.workspace import get_local_workspace
from app.services.ai.runtime.agentscope.compat import AIMessage, HumanMessage, SystemMessage
from app.services.ai.runtime.agentscope.data_runtime import DATA_QUERY_MAX_STEPS_CAP
from app.services.ai.runtime.agentscope.data_tools import build_chatbi_toolkit
from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec, build_toolkit, runtime_tool_spec_from_legacy_tool
from app.services.ai.runtime.tool_loop_detector import ToolLoopDetector
from app.services.ai.tools.registry import ToolRegistry
from app.services.config_service import ConfigService
logger = logging.getLogger(__name__)
from app.services.ai.runners.chatbi.constants import DELAY_SECONDS_EXTREME_THRESHOLD, FAILED_SQL_REPEAT_GATE_PREFIX, FAILED_SQL_REPEAT_THRESHOLD, MAX_DATA_REPAIR_ROUNDS, SCHEMA_GATE_PREFIX, SCHEMA_RETRY_STOPWORDS, SCHEMA_RETRY_SUFFIXES, SQL_PLAN_GATE_PREFIX, SQL_REPEAT_GATE_PREFIX, SQL_STATIC_GATE_PREFIX, TOOL_LOOP_FUSE_THRESHOLD, _SQL_RESULT_DISPLAY_MAX_ROWS, _SQL_RESULT_ROW_KEYS, _SQL_TOOL_ERROR_DELIMITER, _SQL_TOOL_RESULT_DELIMITER
from app.services.ai.runners.chatbi.federated_upgrade import extract_schema_dataset_names as _extract_schema_dataset_names, looks_like_explicit_federated_query as _looks_like_explicit_federated_query, should_upgrade_to_federated_query as _should_upgrade_to_federated_query
from app.services.ai.runners.chatbi.run_state import DataRunState as _DataRunState
from app.services.ai.runners.chatbi import schema_fatal as chatbi_schema_fatal
from app.services.ai.runners.chatbi import schema_retry as chatbi_schema_retry
from app.services.ai.runners.chatbi import repair_policy as chatbi_repair
from app.services.ai.runners.chatbi import sql_gates as chatbi_sql_gates
from app.services.ai.runners.chatbi import sql_repair_hints as chatbi_sql_repair_hints
from app.services.ai.runners.chatbi import sql_result_parser as chatbi_sql_result
from app.services.ai.runners.chatbi import state_serialization as chatbi_state
from app.services.ai.runners.chatbi import tool_loop as chatbi_tool_loop
from app.services.ai.runners.chatbi import clarification as chatbi_clarification
from app.services.ai.runners.chatbi import synthesis as chatbi_synthesis
from app.services.ai.runners.chatbi import react_stream as chatbi_react_stream
from app.services.ai.runners.chatbi import tool_result_handlers as chatbi_tool_result_handlers
from app.services.ai.runners.chatbi import tool_gate_wrapper as chatbi_tool_gate
from app.services.ai.runners.chatbi import turn_handlers as chatbi_turn_handlers
from app.services.ai.runners.chatbi import native_turn as chatbi_native_turn
from app.services.ai.runners.chatbi import schema_prefetch as chatbi_schema_prefetch
from app.services.ai.runners.chatbi import few_shot as chatbi_few_shot
from app.services.ai.runners.chatbi import system_prompt as chatbi_system_prompt
from app.services.ai.runners.chatbi import agent_builder as chatbi_agent_builder
from app.services.ai.runners.chatbi import empty_filter as chatbi_empty_filter
from app.services.ai.runners.chatbi import where_condition_probe as chatbi_where_condition_probe
from app.services.ai.runners.chatbi import resume_stream as chatbi_resume_stream
from app.services.ai.runners.chatbi import followup_data as chatbi_followup_data

class UpgradeToFederatedQuery(Exception):

    def __init__(self, sql: str, datasets: set[str], binding=None):
        self.sql = sql
        self.datasets = datasets
        self.binding = binding

class DataAgentRunner(BaseExecutor):
    """AgentScope-native runner foundation for ChatBI/DataExecutor migration."""

    def __init__(self, config: ChatConfig, trace_id: str, trace_buffer: List[AgentExecutionStep], debug_options: Dict[str, Any]=None, user_info: Optional[Dict[str, Any]]=None, conversation_id: Optional[str]=None, permission_options: Dict[str, Any]=None):
        super().__init__(config, trace_id, trace_buffer, debug_options, user_info, conversation_id, permission_options)
        self._last_run_state: _DataRunState | None = None
        self.turn_classification = None
        self.intent_info = None
        self.intent_elapsed_ms = 0.0
        self._requires_fresh_data = True
        self._pending_few_shot_log: Optional[Dict[str, Any]] = None
        self._fewshot_examples: List[Dict[str, Any]] = []
        self._standalone_query = ''
        self._schema_search_keywords = ''
        self._semantic_intent: DataQuerySemanticIntent | None = None
        self._schema_similarity_threshold: float | None = None
        self._requires_sql_query = True
        self._active_history: List[Dict[str, str]] = []
        self._mixed_task_plan_active = False

    def _chatbi_grounding_audit(
        self,
        *,
        candidate_text: str,
        evidence_result: Any,
    ) -> GroundingAuditResult:
        has_fact_signal = contains_grounding_fact_signal(candidate_text)
        ledger = EvidenceLedger(
            user_id=self._runtime_user_id(),
            conversation_id=self.conversation_id,
        )
        if evidence_result is not None:
            ledger.record_success(
                call_id=f"chatbi-final:{uuid.uuid4().hex}",
                producer="chatbi_final_result",
                evidence_types={EvidenceType.INTERNAL_DATA},
                result=evidence_result,
                policy="allow_empty_success",
            )
        return GroundingService.audit(
            requirement=FactRequirement(
                required=has_fact_signal,
                accepted_types=(
                    frozenset({EvidenceType.INTERNAL_DATA})
                    if has_fact_signal
                    else frozenset()
                ),
            ),
            candidate_text=candidate_text,
            ledger=ledger,
            enabled=self._grounding_enabled(),
        )

    async def _ensure_schema_similarity_threshold(self) -> float:
        """与 fetch_dataset_schema_core 共用 ragflow_similarity_threshold。"""
        if self._schema_similarity_threshold is not None:
            return self._schema_similarity_threshold
        from app.services.config_service import ConfigService
        try:
            self._schema_similarity_threshold = float(await ConfigService.get('ragflow_similarity_threshold') or 0.2)
        except (TypeError, ValueError):
            self._schema_similarity_threshold = 0.2
        return self._schema_similarity_threshold

    @staticmethod
    def _schema_strong_confidence_threshold(similarity_threshold: float) -> float:
        """Schema 置信度达到配置的 ragflow_similarity_threshold 即可进入 SQL。"""
        return float(similarity_threshold)

    def _is_sql_plan_enabled(self) -> bool:
        raw = self.debug_options.get('enable_sql_plan')
        if isinstance(raw, bool):
            return raw
        return str(raw or '').strip().lower() in {'1', 'true', 'yes', 'on'}

    @staticmethod
    def _extract_schema_confidence_values(tool_output: Any) -> list[float]:
        from app.services.schema_chunk_format import extract_schema_confidence_values
        return extract_schema_confidence_values(tool_output)

    def _runtime_agent_name(self) -> str:
        return self.config.agent_name or 'DataAgent'

    def _runtime_user_id(self) -> str | None:
        user_id = self._current_user_id()
        return str(user_id) if user_id is not None else None

    def _runtime_user_name(self) -> str | None:
        if not self.user_info:
            return None
        raw_name = self.user_info.get("user_name") or self.user_info.get("username")
        if not raw_name:
            return None
        name = str(raw_name).strip()
        return name or None

    def _runner_context(self, *, system_content: str, max_steps: int) -> Dict[str, Any]:
        return {'runner_type': 'data', 'config': self.config.model_dump(), 'debug_options': self.debug_options, 'permission_options': self.permission_options, 'system_content': system_content, 'max_steps': max_steps, 'standalone_query': self._standalone_query, 'schema_search_keywords': self._schema_search_keywords, 'semantic_intent': semantic_intent_to_dict(self._semantic_intent), 'requires_fresh_data': self._requires_fresh_data, 'requires_sql_query': bool(getattr(self, '_requires_sql_query', True))}

    @classmethod
    def from_runner_context(cls, *, runner_context: Dict[str, Any], trace_id: str, trace_buffer: List[AgentExecutionStep] | None=None, user_info: Optional[Dict[str, Any]]=None, conversation_id: Optional[str]=None) -> 'DataAgentRunner':
        config = ChatConfig(**runner_context['config'])
        runner = cls(config=config, trace_id=trace_id, trace_buffer=trace_buffer or [], debug_options=runner_context.get('debug_options'), permission_options=runner_context.get('permission_options'), user_info=user_info, conversation_id=conversation_id)
        runner._standalone_query = str(runner_context.get('standalone_query') or '')
        runner._schema_search_keywords = str(runner_context.get('schema_search_keywords') or '')
        runner._semantic_intent = semantic_intent_from_dict(runner_context.get('semantic_intent'))
        runner._requires_fresh_data = bool(runner_context.get('requires_fresh_data', True))
        runner._requires_sql_query = bool(runner_context.get('requires_sql_query', True))
        return runner

    @staticmethod
    def _latest_user_runtime_messages(runtime_messages: List[Any]) -> List[Any]:
        for message in reversed(runtime_messages):
            if isinstance(message, HumanMessage):
                return [message]
        return []

    def _current_user_id(self) -> Optional[int]:
        if not self.user_info:
            return None
        raw_user_id = self.user_info.get('user_id') or self.user_info.get('id')
        if not raw_user_id:
            return None
        try:
            return int(raw_user_id)
        except (TypeError, ValueError):
            return None

    def _current_user_is_admin(self) -> bool:
        if not self.user_info:
            return False
        if bool(self.user_info.get('is_admin')):
            return True
        role = str(self.user_info.get('role') or self.user_info.get('role_name') or '').strip().lower()
        return role == 'admin' or role == 'administrator' or role == '平台管理员'

    async def _resolve_max_steps(self) -> int:
        max_steps_str = await ConfigService.get('agent_max_iterations')
        raw_max = int(max_steps_str) if max_steps_str else 6
        return min(raw_max, DATA_QUERY_MAX_STEPS_CAP)

    async def _load_last_data_result(self) -> Optional[Dict[str, Any]]:
        return await chatbi_followup_data.load_last_data_result(self)

    async def _load_last_data_result_with_retry(self, *, attempts: int=3, delay_seconds: float=0.15) -> Optional[Dict[str, Any]]:
        return await chatbi_followup_data.load_last_data_result_with_retry(self, attempts=attempts, delay_seconds=delay_seconds)

    @staticmethod
    def _normalize_rows_for_followup_save(parsed_tool_output: Any) -> Any:
        return chatbi_followup_data.normalize_rows_for_followup_save(parsed_tool_output)

    @staticmethod
    def _latest_data_assistant_excerpt(history: List[Dict[str, str]], *, max_chars: int=12000) -> str:
        return chatbi_followup_data.latest_data_assistant_excerpt(history, max_chars=max_chars)

    async def _save_last_data_result_for_followups(self, tool_args: Dict[str, Any], parsed_tool_output: Any) -> None:
        return await chatbi_followup_data.save_last_data_result_for_followups(self, tool_args, parsed_tool_output)

    async def _maybe_enrich_sql_tool_result(self, *, tool_args: dict[str, Any], output: Any, parsed_output: Any) -> tuple[Any, Any, Any | None]:
        if not isinstance(parsed_output, dict) or not self._is_structured_sql_result(parsed_output):
            return (output, parsed_output, None)
        dataset_name = str(tool_args.get('dataset_name') or '').strip()
        if not dataset_name:
            return (output, parsed_output, None)
        try:
            from app.core.orm import AsyncSessionLocal
            from app.services.ai.dimension_enrichment_service import DimensionEnrichmentService
            async with AsyncSessionLocal() as session:
                enrichment = await DimensionEnrichmentService.enrich_sql_result(session, result_payload=parsed_output, source_dataset_name=dataset_name, user_id=self._current_user_id(), is_admin=self._current_user_is_admin(), agent_context=None)
            if not enrichment.applied:
                return (output, parsed_output, enrichment)
            enriched_output = json.dumps(enrichment.payload, ensure_ascii=False)
            return (enriched_output, enrichment.payload, enrichment)
        except Exception as e:
            logger.warning('[DataAgentRunner] Dimension enrichment skipped: %s', e, exc_info=True)
            return (output, parsed_output, None)

    def resolve_has_data_output(self) -> bool:
        """本轮 ChatBI 是否产出了可复用的结构化查数结果（供前端展示「可视化分析」入口）。"""
        state = self._last_run_state
        if not state or not state.requires_fresh_data:
            return False
        return bool(state.followup_data_saved)

    async def _resolve_runtime_tools_from_config(self) -> list[RuntimeToolSpec]:
        return await chatbi_agent_builder.resolve_runtime_tools_from_config(self)

    @staticmethod
    def _is_schema_gate_block(output: Any) -> bool:
        return chatbi_sql_gates.is_schema_gate_block(output)

    @staticmethod
    def _is_sql_repeat_gate_block(output: Any) -> bool:
        return chatbi_sql_gates.is_sql_repeat_gate_block(output)

    @staticmethod
    def _is_sql_static_gate_block(output: Any) -> bool:
        return chatbi_sql_gates.is_sql_static_gate_block(output)

    @staticmethod
    def _is_time_range_gate_block(output: Any) -> bool:
        return chatbi_sql_gates.is_time_range_gate_block(output)

    @staticmethod
    def _is_sql_sandbox_gate_block(output: Any) -> bool:
        return chatbi_sql_gates.is_sql_sandbox_gate_block(output)

    @staticmethod
    def _is_failed_sql_repeat_gate_block(output: Any) -> bool:
        return chatbi_sql_gates.is_failed_sql_repeat_gate_block(output)

    @staticmethod
    def _is_sql_plan_gate_block(output: Any) -> bool:
        return chatbi_sql_gates.is_sql_plan_gate_block(output)

    @staticmethod
    def _is_sql_schema_preflight_error(output: Any) -> bool:
        return chatbi_sql_gates.is_sql_schema_preflight_error(output)

    @staticmethod
    def _is_cross_dataset_scope_sql_error(message: Any) -> bool:
        return chatbi_sql_gates.is_cross_dataset_scope_sql_error(message)

    @staticmethod
    def _normalize_sql_text(sql: str) -> str:
        return chatbi_sql_gates.normalize_sql_text(sql)

    @staticmethod
    def _is_schema_reference_sql_error(message: str) -> bool:
        return chatbi_sql_gates.is_schema_reference_sql_error(message)

    @staticmethod
    def _extract_failed_repeat_original_error(output: Any) -> str:
        return chatbi_sql_gates.extract_failed_repeat_original_error(output)

    @staticmethod
    def _extract_invalid_sql_identifiers(message: str) -> list[str]:
        return chatbi_sql_gates.extract_invalid_sql_identifiers(message)

    def _invalid_identifier_repair_hint(self, message: str) -> str:
        return chatbi_sql_repair_hints.invalid_identifier_repair_hint(message)

    def _cross_dataset_scope_repair_hint(self, message: str) -> str:
        return chatbi_sql_repair_hints.cross_dataset_scope_repair_hint(message)

    def _sql_repair_taxonomy_hint(self, message: str) -> str:
        return chatbi_sql_repair_hints.sql_repair_taxonomy_hint(message)

    @staticmethod
    def _normalize_sql_identifier(identifier: str) -> str:
        return chatbi_sql_gates.normalize_sql_identifier(identifier)

    @staticmethod
    def _split_schema_columns(raw: str) -> list[str]:
        return chatbi_sql_gates.split_schema_columns(raw)

    def _extract_schema_table_columns(self, output: Any) -> dict[str, list[str]]:
        return chatbi_sql_gates.extract_schema_table_columns(output)

    def _build_schema_binding_summary(self, output: Any) -> str:
        return chatbi_sql_gates.build_schema_binding_summary(output)

    @staticmethod
    def _is_date_format_sql_error(message: str) -> bool:
        return chatbi_sql_gates.is_date_format_sql_error(message)

    @staticmethod
    def _mask_sql_literals_and_comments(sql: str) -> str:
        return chatbi_sql_gates.mask_sql_literals_and_comments(sql)

    def _build_sql_schema_preflight_error(
        self,
        sql: str,
        schema_table_columns: dict[str, list[str]],
        *,
        extra_allowed_tables: set[str] | None = None,
    ) -> str:
        return chatbi_sql_gates.build_sql_schema_preflight_error(
            sql,
            schema_table_columns,
            extra_allowed_tables=extra_allowed_tables,
        )

    async def _resolve_sql_schema_preflight_error(
        self,
        sql: str,
        data_source: str | None,
        *,
        binding: Any | None = None,
        schema_table_columns: dict[str, list[str]] | None = None,
        allowed_dataset_names: set[str] | None = None,
    ) -> str:
        from app.core.orm import AsyncSessionLocal
        from app.services.ai.chatbi_sql_query_binding import resolve_sql_schema_preflight_with_binding

        async with AsyncSessionLocal() as session:
            return await resolve_sql_schema_preflight_with_binding(
                session,
                sql=sql,
                binding=binding,
                schema_table_columns=schema_table_columns,
                data_source=str(data_source or ""),
                user_id=self._current_user_id(),
                is_admin=self._current_user_is_admin(),
                allowed_dataset_names=allowed_dataset_names,
            )

    @staticmethod
    def _normalize_tool_arg_value(value: Any) -> Any:
        return chatbi_tool_loop.normalize_tool_arg_value(value)

    @classmethod
    def _tool_call_signature(cls, tool_name: str, tool_args: dict[str, Any] | None) -> str:
        return chatbi_tool_loop.tool_call_signature(tool_name, tool_args)

    def _record_tool_call_signature(self, state: _DataRunState, tool_name: str, tool_args: dict[str, Any] | None) -> None:
        return chatbi_tool_loop.record_tool_call_signature(state, tool_name, tool_args)

    @staticmethod
    def _schema_keywords_from_args(tool_args: dict[str, Any] | None) -> str:
        return chatbi_schema_retry.schema_keywords_from_args(tool_args)

    def _record_schema_keywords(self, state: _DataRunState, tool_args: dict[str, Any] | None) -> None:
        return chatbi_schema_retry.record_schema_keywords(state, tool_args)

    @staticmethod
    def _append_unique_keyword(tokens: list[str], keyword: str) -> None:
        return chatbi_schema_retry.append_unique_keyword(tokens, keyword)

    @staticmethod
    def _clean_schema_retry_phrase(text: str) -> str:
        return chatbi_schema_retry.clean_schema_retry_phrase(text)

    def _schema_retry_core_terms(self, *sources: Any) -> list[str]:
        return chatbi_schema_retry.schema_retry_core_terms(*sources)

    def _build_controlled_schema_retry_keywords(self, *sources: Any) -> str:
        return chatbi_schema_retry.build_controlled_schema_retry_keywords(*sources)

    def _prepare_controlled_schema_retry_keywords(self, state: _DataRunState, user_question: str='') -> None:
        return chatbi_schema_retry.prepare_controlled_schema_retry_keywords(state, schema_search_keywords=self._schema_search_keywords, standalone_query=self._standalone_query, user_question=user_question)

    def _tool_call_made_progress(self, state: _DataRunState, tool_name: str) -> bool:
        return chatbi_tool_loop.tool_call_made_progress(state, tool_name)

    def _wrap_tools_with_schema_gate(self, tools: list[RuntimeToolSpec], state: _DataRunState) -> list[RuntimeToolSpec]:
        return chatbi_tool_gate.wrap_tools_with_schema_gate(self, tools, state)

    async def _build_native_agent(self, *, native_model: Any, tools: list[RuntimeToolSpec], system_content: str, max_steps: int, primary_model_name: str, restored_state: Any=None) -> Any:
        return await chatbi_agent_builder.build_native_agent(self, native_model=native_model, tools=tools, system_content=system_content, max_steps=max_steps, primary_model_name=primary_model_name, restored_state=restored_state)

    async def execute(self, history: List[Dict[str, str]]) -> AsyncGenerator[Dict[str, Any], None]:
        from app.services.ai.runtime.agentscope.trace_context import TraceSpanContext
        async with TraceSpanContext(trace_buffer=self.trace_buffer, event_type='agent_execution', span_name='DataAgentRunner'):
            async for chunk in self._execute_raw(history):
                yield chunk

    async def _execute_raw(self, history: List[Dict[str, str]]) -> AsyncGenerator[Dict[str, Any], None]:
        self._active_history = list(history or [])
        model_name = resolve_runtime_model_name(self.config, prefer_synthesis=True)
        incompatible_msg = await ensure_multimodal_compatible(history, model_name)
        if incompatible_msg:
            yield {'content': incompatible_msg, 'status': 'error'}
            return
        runtime_messages = [message for message in normalize_messages_for_llm(convert_history_to_messages(history, strip_thought=True)) if not isinstance(message, SystemMessage)]
        user_question = next((str(getattr(message, 'content', '')) for message in reversed(runtime_messages)), '')
        if not self._mixed_task_plan_active:
            from app.services.ai.chatbi_task_plan import (
                ChatBITaskStatus,
                build_chatbi_task_plan,
            )

            task_plan = build_chatbi_task_plan(user_question)
            # brief/monitor/action 尚无 operation-aware runner，不能伪装成查询子句执行。
            if task_plan.is_executable:
                yield task_plan.as_event()
                task_history = list(history[:-1])
                self._mixed_task_plan_active = True
                try:
                    by_id = {task.task_id: task for task in task_plan.tasks}
                    for task in task_plan.tasks:
                        dependencies = [by_id[item] for item in task.depends_on if item in by_id]
                        if any(dep.status != ChatBITaskStatus.SUCCEEDED for dep in dependencies):
                            task.status = ChatBITaskStatus.SKIPPED
                            task.error = "dependency_failed"
                            yield {
                                "type": "chatbi_task_status",
                                "data": {"plan_id": task_plan.plan_id, "task_id": task.task_id, "status": task.status.value},
                            }
                            continue
                        task.status = ChatBITaskStatus.RUNNING
                        yield {
                            "type": "chatbi_task_status",
                            "data": {"plan_id": task_plan.plan_id, "task_id": task.task_id, "status": task.status.value},
                        }
                        current_history = task_history + [{"role": "user", "content": task.query}]
                        assistant_content: list[str] = []
                        failed = False
                        async for chunk in self._execute_raw(current_history):
                            if chunk.get("content"):
                                assistant_content.append(str(chunk["content"]))
                            if chunk.get("type") == "error" or chunk.get("status") == "error":
                                failed = True
                            yield chunk
                        task.status = ChatBITaskStatus.FAILED if failed else ChatBITaskStatus.SUCCEEDED
                        task_history.append({"role": "user", "content": task.query})
                        task_history.append({
                            "role": "assistant",
                            "content": "".join(assistant_content) or f"任务已{task.status.value}",
                        })
                        yield {
                            "type": "chatbi_task_status",
                            "data": {"plan_id": task_plan.plan_id, "task_id": task.task_id, "status": task.status.value},
                        }
                finally:
                    self._mixed_task_plan_active = False
                return
        last_data_result_for_turn = await self._load_last_data_result_with_retry()
        turn_cls, turn_intent_info, turn_elapsed_ms = await resolve_data_query_turn_classification(user_question, history, user_info=self.user_info, conversation_id=self.conversation_id, has_last_data_result=last_data_result_for_turn is not None)
        if turn_cls.turn_type == DataQueryTurnType.NON_DATA_REQUEST and last_data_result_for_turn:
            from app.services.ai.runners.chatbi.non_data_policy import (
                NonDataDisposition,
                resolve_non_data_disposition,
            )
            result_action = resolve_non_data_disposition(
                user_question,
                has_last_data_result=True,
            )
            if result_action.disposition == NonDataDisposition.RESULT_ACTION:
                # Defensive correction for classifier drift: result tools belong to ChatBI.
                turn_cls.turn_type = DataQueryTurnType.RESULT_ACTION
                turn_cls.reasoning = result_action.reason
                turn_cls.requires_fresh_data = False
                turn_cls.requires_few_shot = False
                turn_cls.requires_sql_query = False
                turn_cls.intent = IntentType.DATA_QUERY
                turn_intent_info.intent = IntentType.DATA_QUERY
        self.turn_classification = turn_cls
        self.intent_info = turn_intent_info
        self.intent_elapsed_ms = turn_elapsed_ms
        self._requires_fresh_data = turn_cls.requires_fresh_data
        self._requires_sql_query = bool(getattr(turn_cls, 'requires_sql_query', True))
        yield {'type': 'log', 'id': f'chatbi_turn_{uuid.uuid4().hex[:8]}', 'title': 'ChatBI 请求类别分析结果', 'details': f'{data_query_turn_type_label(turn_cls.turn_type)}。{turn_cls.reasoning}', 'status': 'success', 'category': 'intent', 'turn_type': turn_cls.turn_type.value, 'execution_time_ms': turn_elapsed_ms}
        if turn_cls.turn_type in chatbi_turn_handlers.EARLY_EXIT_TURN_TYPES:
            async for chunk in chatbi_turn_handlers.dispatch_early_turn(self, turn_cls=turn_cls, history=history, runtime_messages=runtime_messages, user_question=user_question, last_data_result_for_turn=last_data_result_for_turn):
                yield chunk
            return
        tools = await self._resolve_runtime_tools_from_config()
        max_steps = await self._resolve_max_steps()
        self._standalone_query = user_question
        if turn_cls.requires_fresh_data:
            self._standalone_query = await chatbi_schema_prefetch.resolve_standalone_query_for_new_data_query(self, user_question, runtime_messages)
        system_content = await chatbi_system_prompt.build_system_content(self, context_action_result=last_data_result_for_turn if not turn_cls.requires_fresh_data else None, include_context_action=not turn_cls.requires_fresh_data)
        if (
            turn_cls.turn_type == DataQueryTurnType.DATA_FOLLOWUP_QUERY
            and last_data_result_for_turn
        ):
            from app.services.ai.runners.chatbi.drilldown_context import (
                build_inherited_analysis_context,
            )

            system_content += build_inherited_analysis_context(
                last_data_result_for_turn,
                user_question,
            )
        if turn_cls.requires_few_shot:
            system_content = await chatbi_few_shot.inject_few_shot_examples(self, system_content, user_question=self._standalone_query, runtime_messages=runtime_messages)
            if self._pending_few_shot_log:
                yield self._pending_few_shot_log
                self._pending_few_shot_log = None
        else:
            yield chatbi_few_shot.skip_few_shot_log()
        schema_setup = chatbi_schema_prefetch.SchemaSetupOutcome(system_content=system_content)
        async for chunk in chatbi_schema_prefetch.yield_fresh_data_schema_setup(self, schema_setup, turn_cls=turn_cls, user_question=user_question, runtime_messages=runtime_messages, tools=tools):
            yield chunk
        system_content = schema_setup.system_content
        prefetched_schema_output = schema_setup.prefetched_schema_output
        if schema_setup.stop_execution:
            return
        llm_handle = await AgentConfigProvider.get_configured_llm(streaming=True, config=self.config)
        native_model = getattr(llm_handle, 'native_model', None)
        if native_model is None:
            yield {'type': 'error', 'status': 'error', 'content': '当前模型适配器未提供 AgentScope native_model，无法执行 ChatBI AgentScope 原生工具链。'}
            return
        agent_name = self._runtime_agent_name()
        tools_fingerprint = build_tools_fingerprint(self.config, tools)
        model_name = getattr(native_model, 'model', self.config.model_name)
        try:
            async with agentscope_session_lock.hold(user_id=self._runtime_user_id(), conversation_id=self.conversation_id, agent_name=agent_name, ttl_seconds=300):
                async for chunk in self._run_native_agent_turn(native_model=native_model, tools=tools, tools_fingerprint=tools_fingerprint, model_name=model_name, agent_name=agent_name, system_content=system_content, max_steps=max_steps, llm_handle=llm_handle, runtime_messages=runtime_messages, user_question=user_question, turn_cls=turn_cls, prefetched_schema_output=prefetched_schema_output):
                    yield chunk
        except SessionLockTimeout:
            yield {'type': 'error', 'status': 'error', 'content': '当前会话正在处理中，请稍后再试。'}
        except UpgradeToFederatedQuery as e:
            async for chunk in chatbi_turn_handlers.run_federated_sql_upgrade(self, turn_cls=turn_cls, exc=e, prefetched_schema_output=prefetched_schema_output, system_content=system_content, runtime_messages=runtime_messages):
                yield chunk
            return

    async def _run_native_agent_turn(self, *, native_model: Any, tools: list[RuntimeToolSpec], tools_fingerprint: str, model_name: Any, agent_name: str, system_content: str, max_steps: int, llm_handle: Any, runtime_messages: List[Any], user_question: str, turn_cls: Any, prefetched_schema_output: str | None=None) -> AsyncGenerator[Dict[str, Any], None]:
        async for chunk in chatbi_native_turn.run_native_agent_turn(self, native_model=native_model, tools=tools, tools_fingerprint=tools_fingerprint, model_name=model_name, agent_name=agent_name, system_content=system_content, max_steps=max_steps, llm_handle=llm_handle, runtime_messages=runtime_messages, user_question=user_question, turn_cls=turn_cls, prefetched_schema_output=prefetched_schema_output):
            yield chunk

    async def _inject_few_shot_examples(self, system_content: str, *, user_question: str, runtime_messages: List[Any]) -> str:
        return await chatbi_few_shot.inject_few_shot_examples(self, system_content, user_question=user_question, runtime_messages=runtime_messages)

    @staticmethod
    def _example_schema_keyword_context(examples: List[Dict[str, Any]], limit: int=3) -> str:
        return chatbi_schema_prefetch.example_schema_keyword_context(examples, limit=limit)

    @staticmethod
    def _clean_schema_fallback_query(text: str) -> str:
        return chatbi_schema_prefetch.clean_schema_fallback_query(text)

    async def _plan_schema_search_keywords(self, user_question: str, standalone_query: str, examples: List[Dict[str, Any]], runtime_messages: List[Any] | None=None) -> str:
        return await chatbi_schema_prefetch.plan_schema_search_keywords(self, user_question, standalone_query, examples, runtime_messages=runtime_messages)

    @staticmethod
    def _semantic_intent_recent_context(runtime_messages: List[Any]) -> str:
        return chatbi_schema_prefetch.semantic_intent_recent_context(runtime_messages)

    def _format_need_analysis_success_details(self, keywords: str) -> str:
        return chatbi_schema_prefetch.format_need_analysis_success_details(self, keywords)

    @staticmethod
    def _is_invalid_schema_search_keywords(keywords: str) -> bool:
        return chatbi_schema_prefetch.is_invalid_schema_search_keywords(keywords)

    async def _auto_invoke_get_dataset_schema(self, *, keywords: str, tools: list[RuntimeToolSpec]) -> AsyncGenerator[Dict[str, Any], None]:
        async for chunk in chatbi_schema_prefetch.auto_invoke_get_dataset_schema(self, keywords=keywords, tools=tools):
            yield chunk

    @staticmethod
    def _should_rewrite_contextual_new_data_query(user_question: str, runtime_messages: List[Any]) -> bool:
        return chatbi_schema_prefetch.should_rewrite_contextual_new_data_query(user_question, runtime_messages)

    async def _resolve_standalone_query_for_new_data_query(self, user_question: str, runtime_messages: List[Any]) -> str:
        return await chatbi_schema_prefetch.resolve_standalone_query_for_new_data_query(self, user_question, runtime_messages)

    async def _generate_clarification_content(self, *, user_question: str, history: List[Dict[str, str]], reasoning: str, missing_fields: tuple[str, ...] | None = None) -> str:
        return await chatbi_clarification.generate_clarification_content(self, user_question=user_question, history=history, reasoning=reasoning, missing_fields=missing_fields)

    async def _yield_contextual_clarification(self, *, user_question: str, history: List[Dict[str, str]], reasoning: str, missing_fields: tuple[str, ...] | None = None) -> AsyncGenerator[Dict[str, Any], None]:
        async for _chunk in chatbi_clarification.yield_contextual_clarification(self, user_question=user_question, history=history, reasoning=reasoning, missing_fields=missing_fields):
            yield _chunk

    async def _yield_missing_reusable_result_clarification(self, history: List[Dict[str, str]], *, user_question: str='') -> AsyncGenerator[Dict[str, Any], None]:
        async for _chunk in chatbi_clarification.yield_missing_reusable_result_clarification(self, history, user_question=user_question):
            yield _chunk

    async def _synthesize_from_last_data_result(self, runtime_messages: List[Any], system_prompt: str, user_question: str, last_result: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        async for _chunk in chatbi_synthesis.synthesize_from_last_data_result(self, runtime_messages, system_prompt, user_question, last_result):
            yield _chunk

    async def _synthesize_format_correction(self, runtime_messages: List[Any], system_prompt: str, user_question: str, last_result: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        async for _chunk in chatbi_synthesis.synthesize_format_correction(self, runtime_messages, system_prompt, user_question, last_result):
            yield _chunk

    async def _synthesize_from_history_data_result(self, runtime_messages: List[Any], system_prompt: str, user_question: str, history: List[Dict[str, str]]) -> AsyncGenerator[Dict[str, Any], None]:
        async for _chunk in chatbi_synthesis.synthesize_from_history_data_result(self, runtime_messages, system_prompt, user_question, history):
            yield _chunk

    async def _synthesize_from_cached_sql_result(self, *, runtime_messages: List[Any], system_prompt: str, user_question: str, state: _DataRunState) -> AsyncGenerator[Dict[str, Any], None]:
        async for _chunk in chatbi_synthesis.synthesize_from_cached_sql_result(self, runtime_messages=runtime_messages, system_prompt=system_prompt, user_question=user_question, state=state):
            yield _chunk

    async def _build_system_content(self, *, context_action_result: Optional[Dict[str, Any]]=None, include_context_action: bool=False) -> str:
        return await chatbi_system_prompt.build_system_content(self, context_action_result=context_action_result, include_context_action=include_context_action)

    @staticmethod
    def _data_run_state_to_pending_state(state: _DataRunState, stream_meta: Dict[str, Any]) -> Dict[str, Any]:
        return chatbi_state.data_run_state_to_pending_state(state, stream_meta)

    @staticmethod
    def _sync_pending_data_run_state(state: _DataRunState, pending_state: Dict[str, Any]) -> None:
        return chatbi_state.sync_pending_data_run_state(state, pending_state)

    @staticmethod
    def _build_stream_state(state: _DataRunState, stream_meta: Dict[str, Any]) -> Dict[str, Any]:
        return chatbi_state.build_stream_state(state, stream_meta)

    @staticmethod
    def _pending_state_to_data_run_state(pending_state: Dict[str, Any]) -> tuple[_DataRunState, Dict[str, Any]]:
        return chatbi_state.pending_state_to_data_run_state(pending_state)

    async def _stream_agentscope_events(self, *, event_stream: Any, agent: Any | None=None, tools: list[RuntimeToolSpec], native_model: Any, state: _DataRunState | None=None, stream_meta: Dict[str, Any] | None=None, emit_final_guard: bool=True) -> AsyncGenerator[Dict[str, Any], None]:
        async for _chunk in chatbi_react_stream.stream_agentscope_events(self, event_stream=event_stream, agent=agent, tools=tools, native_model=native_model, state=state, stream_meta=stream_meta, emit_final_guard=emit_final_guard):
            yield _chunk

    @staticmethod
    def _is_schema_fatal(state: _DataRunState) -> bool:
        return chatbi_schema_fatal.is_schema_fatal(state)

    def _schema_fatal_response(self, state: _DataRunState) -> tuple[str, str]:
        return chatbi_schema_fatal.schema_fatal_response(state)

    async def _yield_sql_fatal_abort(self, state: _DataRunState) -> AsyncGenerator[Dict[str, Any], None]:
        async for _chunk in chatbi_react_stream.yield_sql_fatal_abort(self, state):
            yield _chunk

    async def _yield_schema_fatal_abort(self, state: _DataRunState, details: Any='') -> AsyncGenerator[Dict[str, Any], None]:
        async for _chunk in chatbi_react_stream.yield_schema_fatal_abort(self, state, details):
            yield _chunk

    async def _emit_final_guard(self, state: _DataRunState) -> AsyncGenerator[Dict[str, Any], None]:
        async for _chunk in chatbi_react_stream.emit_final_guard(self, state):
            yield _chunk

    async def _retract_provisional_content_before_repair(self, state: _DataRunState, *, reason: str) -> AsyncGenerator[Dict[str, Any], None]:
        async for _chunk in chatbi_react_stream.retract_provisional_content_before_repair(self, state, reason=reason):
            yield _chunk

    def _current_repair_kind(self, state: _DataRunState) -> str:
        return chatbi_repair.current_repair_kind(state)

    def _repair_budget_exhausted(self, state: _DataRunState) -> bool:
        return chatbi_repair.repair_budget_exhausted(state)

    def _record_repair_attempt(self, state: _DataRunState) -> None:
        return chatbi_repair.record_repair_attempt(state)

    def _build_repair_message(self, state: _DataRunState) -> str:
        return chatbi_repair.build_repair_message(state, semantic_intent=self._semantic_intent)

    def _reset_state_for_repair(self, state: _DataRunState) -> None:
        return chatbi_repair.reset_state_for_repair(state)

    def _resolve_force_execute_sql_tool_choice(self, state: _DataRunState) -> Any | None:
        return chatbi_repair.resolve_force_execute_sql_tool_choice(state)

    def _resolve_initial_tool_choice(self, state: _DataRunState) -> Any | None:
        return chatbi_repair.resolve_initial_tool_choice(state)

    def _resolve_repair_tool_choice(self, state: _DataRunState) -> Any | None:
        return chatbi_repair.resolve_repair_tool_choice(state)

    def _build_repair_title(self, state: _DataRunState) -> str:
        return chatbi_repair.build_repair_title(state)

    def _has_sql_plan(self, text: str) -> bool:
        return chatbi_sql_gates.has_sql_plan(text)

    def _should_require_sql_plan(self, user_question: str) -> bool:
        return chatbi_sql_gates.should_require_sql_plan(user_question)

    def _format_sql_result_for_display(self, output: Any, *, max_rows: int=_SQL_RESULT_DISPLAY_MAX_ROWS) -> str:
        return chatbi_tool_result_handlers.format_sql_result_for_display(self, output, max_rows=max_rows)

    def _build_sql_error_tool_details(self, output: Any, tool_args: dict[str, Any] | None) -> str:
        return chatbi_tool_result_handlers.build_sql_error_tool_details(self, output, tool_args)

    def _format_tool_details(self, tool_name: str, output: Any, state: _DataRunState, tool_args: dict[str, Any] | None=None) -> str:
        return chatbi_tool_result_handlers.format_tool_details(self, tool_name, output, state, tool_args)

    @staticmethod
    def _is_schema_service_unavailable(tool_output: Any) -> bool:
        return chatbi_sql_gates.is_schema_service_unavailable(tool_output)

    def _apply_schema_tool_result(self, state: _DataRunState, output: Any) -> None:
        return chatbi_tool_result_handlers.apply_schema_tool_result(self, state, output)

    def _apply_sql_tool_result(self, state: _DataRunState, *, tool_args: dict[str, Any], output: Any) -> tuple[Any, bool]:
        return chatbi_tool_result_handlers.apply_sql_tool_result(self, state, tool_args=tool_args, output=output)

    @staticmethod
    def _should_replace_generic_empty_failure_reply(state: _DataRunState) -> bool:
        return chatbi_empty_filter.should_replace_generic_empty_failure_reply(state)

    async def _maybe_run_empty_filter_diagnostics(self, state: _DataRunState, *, tool_args: dict[str, Any]):
        return await chatbi_empty_filter.maybe_run_empty_filter_diagnostics(self, state, tool_args=tool_args)

    async def _maybe_run_where_condition_diagnostics(self, state: _DataRunState, *, tool_args: dict[str, Any]):
        return await chatbi_where_condition_probe.maybe_run_where_condition_diagnostics(
            self, state, tool_args=tool_args
        )

    def _apply_auto_retry_sql_result(self, state: _DataRunState, *, sql_text: str, output: Any, parsed_output: Any) -> bool:
        return chatbi_empty_filter.apply_auto_retry_sql_result(self, state, sql_text=sql_text, output=output, parsed_output=parsed_output)

    def _schema_needs_refinement(self, tool_output: Any, *, similarity_threshold: float) -> bool:
        text = str(tool_output or '').strip()
        if not text:
            return False
        if self._is_no_relevant_schema(tool_output):
            return False
        if 'Available Datasets (Please provide keywords' in text:
            return True
        confidence_values = self._extract_schema_confidence_values(tool_output)
        if not confidence_values:
            return False
        max_confidence = max(confidence_values)
        strong_threshold = self._schema_strong_confidence_threshold(similarity_threshold)
        if max_confidence < strong_threshold:
            return True
        return False

    @staticmethod
    def _detect_schema_ambiguity(tool_output: Any) -> tuple[bool, str]:
        from app.services.schema_chunk_format import detect_schema_ambiguity
        return detect_schema_ambiguity(tool_output)

    @staticmethod
    def _is_diagnostic_sql(sql: str) -> bool:
        return chatbi_sql_gates.is_diagnostic_sql(sql)

    @staticmethod
    def _detect_sql_static_risk(sql: str) -> str:
        return chatbi_sql_gates.detect_sql_static_risk(sql)

    @staticmethod
    def _is_rag_not_synced(tool_output: Any) -> bool:
        return chatbi_sql_gates.is_rag_not_synced(tool_output)

    def _try_parse_json_output(self, tool_output: Any) -> Any:
        return chatbi_sql_result.try_parse_json_output(tool_output)

    def _extract_result_row_lists(self, parsed: Any, depth: int=0) -> list[list[Any]]:
        return chatbi_sql_result.extract_result_row_lists(parsed, depth)

    def _result_has_data_rows(self, parsed: Any) -> bool:
        return chatbi_sql_result.result_has_data_rows(parsed)

    def _detect_empty_result(self, parsed: Any) -> str | None:
        return chatbi_sql_result.detect_empty_result(parsed)

    def _is_no_authorized_schema(self, tool_output: Any) -> bool:
        return chatbi_sql_gates.is_no_authorized_schema(tool_output)

    def _is_no_relevant_schema(self, tool_output: Any) -> bool:
        return chatbi_sql_gates.is_no_relevant_schema(tool_output)

    def _is_structured_sql_result(self, parsed: Any) -> bool:
        return chatbi_sql_result.is_structured_sql_result(parsed)

    def _detect_sql_error(self, output: Any) -> tuple[bool, str]:
        return chatbi_sql_result.detect_sql_error(output)

    def _is_trusted_empty_result(self, sql: str, state: _DataRunState) -> bool:
        return chatbi_sql_result.is_trusted_empty_result(sql, state)

    @staticmethod
    def _is_sql_fatal_error(text: str) -> bool:
        return chatbi_sql_gates.is_sql_fatal_error(text)

    @staticmethod
    def _extract_column_names(parsed: dict[str, Any]) -> list[str]:
        return chatbi_sql_result.extract_column_names(parsed)

    def _iter_named_result_rows(self, parsed: Any, depth: int=0) -> list[dict[str, Any]]:
        return chatbi_sql_result.iter_named_result_rows(parsed, depth)

    @staticmethod
    def _is_duration_like_column(column: str) -> bool:
        return chatbi_sql_result.is_duration_like_column(column)

    @staticmethod
    def _is_delay_like_column(column: str) -> bool:
        return chatbi_sql_result.is_delay_like_column(column)

    def _detect_duration_anomaly(self, parsed: Any) -> tuple[bool, str]:
        return chatbi_sql_result.detect_duration_anomaly(parsed)

    @staticmethod
    def _detect_ratio_anomaly(parsed: Any) -> tuple[bool, str]:
        return chatbi_sql_result.detect_ratio_anomaly(parsed)

    async def _resolve_pending_runtime(self, pending: Any) -> tuple[Any, list[RuntimeToolSpec], Any, _DataRunState, Dict[str, Any]]:
        return await chatbi_resume_stream.resolve_pending_runtime(self, pending)

    async def _resume_agentscope_native_stream(self, *, pending: Any, resume_event: Any) -> AsyncGenerator[Dict[str, Any], None]:
        async for chunk in chatbi_resume_stream.resume_agentscope_native_stream(self, pending=pending, resume_event=resume_event):
            yield chunk

    async def resume_agentscope_native_confirmation(self, pending: Any, *, confirmed: bool) -> AsyncGenerator[Dict[str, Any], None]:
        async for chunk in chatbi_resume_stream.resume_agentscope_native_confirmation(self, pending, confirmed=confirmed):
            yield chunk

    async def resume_agentscope_external_execution(self, pending: Any, *, execution_results: List[Any]) -> AsyncGenerator[Dict[str, Any], None]:
        async for chunk in chatbi_resume_stream.resume_agentscope_external_execution(self, pending, execution_results=execution_results):
            yield chunk
