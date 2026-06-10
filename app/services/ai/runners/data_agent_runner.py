from __future__ import annotations

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
from app.services.ai.data_query_turn_classifier import (
    DataQueryTurnType,
    data_query_turn_type_label,
    resolve_data_query_turn_classification,
)
from app.services.ai.config import AgentConfigProvider
from app.services.ai.executors.base import BaseExecutor
from app.services.ai.executors.common import (
    convert_history_to_messages,
    extract_tokens_from_message,
    normalize_messages_for_llm,
)
from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.ai.multimodal_support import (
    ensure_multimodal_compatible,
    resolve_runtime_model_name,
)
from app.services.ai.runtime.agentscope.chat import (
    compat_to_runtime_messages,
    to_agentscope_messages,
)
from app.services.ai.runtime.agentscope.agent_runtime import (
    build_model_config,
    build_tools_fingerprint,
    load_context_config,
)
from app.services.ai.runtime.agentscope.event_stream import (
    is_interrupt_sse_chunk,
    map_standard_agentscope_event,
)
from app.services.ai.runtime.agentscope.stream_reconcile import (
    collapse_repeated_reply,
    truncate_for_context,
)
from app.services.ai.runtime.agentscope.session_lock import (
    SessionLockTimeout,
    agentscope_session_lock,
)
from app.services.ai.runtime.agentscope.state_store import agent_state_store
from app.services.ai.runtime.agentscope.workspace import get_local_workspace
from app.services.ai.runtime.agentscope.compat import AIMessage, HumanMessage, SystemMessage
from app.services.ai.runtime.agentscope.data_runtime import DATA_QUERY_MAX_STEPS_CAP
from app.services.ai.runtime.agentscope.data_tools import build_chatbi_toolkit
from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec
from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)

SCHEMA_GATE_PREFIX = "[SCHEMA_GATE]"
SQL_REPEAT_GATE_PREFIX = "[SQL_REPEAT_GATE]"
MAX_DATA_REPAIR_ROUNDS = 2
_SQL_RESULT_DISPLAY_MAX_ROWS = 15
_SQL_RESULT_ROW_KEYS = ("items", "rows", "data", "records")
_SQL_TOOL_RESULT_DELIMITER = "--- 结果 ---"
_SQL_TOOL_ERROR_DELIMITER = "--- 错误 ---"


class _ForcedFirstToolChoiceModel:
    """Inject tool_choice on the first model call of a repair round."""

    def __init__(self, inner: Any, tool_choice: Any):
        self._inner = inner
        self._tool_choice = tool_choice
        self._consumed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if not self._consumed and self._tool_choice is not None:
            kwargs["tool_choice"] = self._tool_choice
            self._consumed = True
        result = self._inner(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result


@dataclass
class _DataRunState:
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
    no_authorized_schema: bool = False
    empty_sql_result: bool = False
    empty_sql_reason: str = ""
    sql_error: bool = False
    sql_error_message: str = ""
    requires_sql_plan: bool = False
    sql_plan_seen: bool = False
    sql_plan_missing: bool = False
    requires_fresh_data: bool = True
    text_window: str = ""
    start_synthesis: float = field(default_factory=time.time)
    ignore_text_block: bool = False
    active_text_block_id: str = ""
    text_blocks_emitted_since_last_tool: int = 0
    current_text_block_emitted: bool = False

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
        if not self.requires_fresh_data:
            return True
        return (
            self.schema_completed
            and self.sql_completed
            and not self.sql_before_schema
            and not self.sql_error
            and not self.empty_sql_result
            and not self.sql_plan_missing
        )


class DataAgentRunner(BaseExecutor):
    """AgentScope-native runner foundation for ChatBI/DataExecutor migration."""

    def __init__(
        self,
        config: ChatConfig,
        trace_id: str,
        trace_buffer: List[AgentExecutionStep],
        debug_options: Dict[str, Any] = None,
        user_info: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[str] = None,
        permission_options: Dict[str, Any] = None,
    ):
        super().__init__(config, trace_id, trace_buffer, debug_options, user_info, conversation_id, permission_options)
        self._last_run_state: _DataRunState | None = None
        self.turn_classification = None
        self.intent_info = None
        self.intent_elapsed_ms = 0.0
        self._requires_fresh_data = True
        self._pending_few_shot_log: Optional[Dict[str, Any]] = None
        self._fewshot_examples: List[Dict[str, Any]] = []
        self._standalone_query = ""
        self._schema_search_keywords = ""

    def _runtime_agent_name(self) -> str:
        return self.config.agent_name or "DataAgent"

    def _runtime_user_id(self) -> str | None:
        user_id = self._current_user_id()
        return str(user_id) if user_id is not None else None

    def _runner_context(self, *, system_content: str, max_steps: int) -> Dict[str, Any]:
        return {
            "runner_type": "data",
            "config": self.config.model_dump(),
            "debug_options": self.debug_options,
            "permission_options": self.permission_options,
            "system_content": system_content,
            "max_steps": max_steps,
            "standalone_query": self._standalone_query,
            "schema_search_keywords": self._schema_search_keywords,
            "requires_fresh_data": self._requires_fresh_data,
        }

    @classmethod
    def from_runner_context(
        cls,
        *,
        runner_context: Dict[str, Any],
        trace_id: str,
        trace_buffer: List[AgentExecutionStep] | None = None,
        user_info: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[str] = None,
    ) -> "DataAgentRunner":
        config = ChatConfig(**runner_context["config"])
        runner = cls(
            config=config,
            trace_id=trace_id,
            trace_buffer=trace_buffer or [],
            debug_options=runner_context.get("debug_options"),
            permission_options=runner_context.get("permission_options"),
            user_info=user_info,
            conversation_id=conversation_id,
        )
        runner._standalone_query = str(runner_context.get("standalone_query") or "")
        runner._schema_search_keywords = str(runner_context.get("schema_search_keywords") or "")
        runner._requires_fresh_data = bool(runner_context.get("requires_fresh_data", True))
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
        raw_user_id = self.user_info.get("user_id") or self.user_info.get("id")
        if not raw_user_id:
            return None
        try:
            return int(raw_user_id)
        except (TypeError, ValueError):
            return None

    async def _resolve_max_steps(self) -> int:
        max_steps_str = await ConfigService.get("agent_max_iterations")
        raw_max = int(max_steps_str) if max_steps_str else 6
        return min(raw_max, DATA_QUERY_MAX_STEPS_CAP)

    async def _load_last_data_result(self) -> Optional[Dict[str, Any]]:
        if not self.conversation_id:
            return None
        user_id = self._current_user_id()
        if not user_id:
            return None
        try:
            from app.services.ai.memory_service import memory_service

            return await memory_service.get_last_data_result(user_id, self.conversation_id)
        except Exception as e:
            logger.warning("[DataAgentRunner] Failed to load last data result: %s", e)
            return None

    async def _save_last_data_result_for_followups(
        self,
        tool_args: Dict[str, Any],
        parsed_tool_output: Any,
    ) -> None:
        if not self.conversation_id or not isinstance(parsed_tool_output, (list, dict)):
            return
        user_id = self._current_user_id()
        if not user_id:
            return

        payload = {
            "sql": tool_args.get("sql") or tool_args.get("query"),
            "data_source": tool_args.get("data_source"),
            "dataset_name": tool_args.get("dataset_name"),
            "rows": parsed_tool_output,
            "saved_at": datetime.now().isoformat(),
            "trace_id": self.trace_id,
        }
        try:
            from app.services.ai.memory_service import memory_service

            await memory_service.set_last_data_result(user_id, self.conversation_id, payload)
        except Exception as e:
            logger.warning("[DataAgentRunner] Failed to save last data result: %s", e)

    async def _resolve_runtime_tools_from_config(self) -> list[RuntimeToolSpec]:
        _, specs = await build_chatbi_toolkit(self.config.tools)
        return specs

    @staticmethod
    def _is_schema_gate_block(output: Any) -> bool:
        return str(output or "").startswith(SCHEMA_GATE_PREFIX)

    @staticmethod
    def _is_sql_repeat_gate_block(output: Any) -> bool:
        return str(output or "").startswith(SQL_REPEAT_GATE_PREFIX)

    def _wrap_tools_with_schema_gate(
        self,
        tools: list[RuntimeToolSpec],
        state: _DataRunState,
    ) -> list[RuntimeToolSpec]:
        if not state.requires_fresh_data:
            return tools

        wrapped: list[RuntimeToolSpec] = []
        for spec in tools:
            if spec.name != "execute_sql_query":
                wrapped.append(spec)
                continue

            original_callable = spec.callable

            async def invoke_sql_gated(*, _original=original_callable, **kwargs: Any) -> Any:
                if not state.schema_completed:
                    return (
                        f"{SCHEMA_GATE_PREFIX} 为保证数据准确性，必须先调用 get_dataset_schema "
                        "获取数据集定义，再执行 execute_sql_query。"
                    )
                if state.has_successful_nonempty_sql:
                    return (
                        f"{SQL_REPEAT_GATE_PREFIX} 本轮已成功获取非空查数结果，禁止再次 execute_sql_query。"
                        "请直接基于现有结果生成回答；如需重新查数，请调整条件后由用户发起新一轮提问。"
                    )
                result = _original(**kwargs)
                if inspect.isawaitable(result):
                    return await result
                return result

            wrapped.append(replace(spec, callable=invoke_sql_gated))
        return wrapped

    async def _build_native_agent(
        self,
        *,
        native_model: Any,
        tools: list[RuntimeToolSpec],
        system_content: str,
        max_steps: int,
        primary_model_name: str,
        restored_state: Any = None,
    ) -> Any:
        # ChatBI 仅挂载查数相关工具；不合并 workspace 的 Grep/Read/Bash，避免模型跳过 get_dataset_schema。
        toolkit, _ = await build_chatbi_toolkit([tool.name for tool in tools])
        workspace = await get_local_workspace(
            user_id=self._current_user_id(),
            conversation_id=self.conversation_id,
        )
        context_config = await load_context_config()
        model_config = await build_model_config(
            config=self.config,
            primary_model_name=primary_model_name,
        )
        kwargs: Dict[str, Any] = {
            "name": self._runtime_agent_name(),
            "system_prompt": system_content,
            "model": native_model,
            "toolkit": toolkit,
            "react_config": ReActConfig(max_iters=max_steps),
        }
        if restored_state is not None:
            kwargs["state"] = restored_state
        if workspace is not None:
            kwargs["offloader"] = workspace
        if model_config is not None:
            kwargs["model_config"] = model_config
        if context_config is not None:
            kwargs["context_config"] = context_config
        return Agent(**kwargs)

    async def execute(
        self,
        history: List[Dict[str, str]],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        model_name = resolve_runtime_model_name(self.config, prefer_synthesis=True)
        incompatible_msg = await ensure_multimodal_compatible(history, model_name)
        if incompatible_msg:
            yield {"content": incompatible_msg, "status": "error"}
            return

        runtime_messages = [
            message
            for message in normalize_messages_for_llm(convert_history_to_messages(history))
            if not isinstance(message, SystemMessage)
        ]
        user_question = next(
            (str(getattr(message, "content", "")) for message in reversed(runtime_messages)),
            "",
        )
        last_data_result_for_turn = await self._load_last_data_result()
        turn_cls, turn_intent_info, turn_elapsed_ms = await resolve_data_query_turn_classification(
            user_question,
            history,
            user_info=self.user_info,
            conversation_id=self.conversation_id,
            has_last_data_result=last_data_result_for_turn is not None,
        )
        self.turn_classification = turn_cls
        self.intent_info = turn_intent_info
        self.intent_elapsed_ms = turn_elapsed_ms
        self._requires_fresh_data = turn_cls.requires_fresh_data
        yield {
            "type": "log",
            "id": f"chatbi_turn_{uuid.uuid4().hex[:8]}",
            "title": "ChatBI 请求类别分析结果",
            "details": f"{data_query_turn_type_label(turn_cls.turn_type)}。{turn_cls.reasoning}",
            "status": "success",
            "category": "intent",
            "turn_type": turn_cls.turn_type.value,
            "execution_time_ms": turn_elapsed_ms,
        }

        if turn_cls.turn_type == DataQueryTurnType.REUSE_PREVIOUS_RESULT:
            if last_data_result_for_turn:
                async for chunk in self._synthesize_from_last_data_result(
                    runtime_messages,
                    self.config.system_prompt or "",
                    user_question,
                    last_data_result_for_turn,
                ):
                    yield chunk
            else:
                yield {
                    "type": "log",
                    "id": f"reuse_miss_{uuid.uuid4().hex[:8]}",
                    "title": "缺少可复用查询结果",
                    "details": "检测到本轮是基于上一轮结果的分析/可视化请求，但当前会话没有保存的结构化查询结果。",
                    "status": "error",
                }
                yield {"content": DataQueryPrompts.NO_REUSABLE_RESULT}
            return

        tools = await self._resolve_runtime_tools_from_config()
        max_steps = await self._resolve_max_steps()
        self._standalone_query = user_question
        if turn_cls.requires_fresh_data:
            self._standalone_query = await self._resolve_standalone_query_for_new_data_query(
                user_question,
                runtime_messages,
            )
        system_content = await self._build_system_content(
            context_action_result=last_data_result_for_turn if not turn_cls.requires_fresh_data else None,
            include_context_action=not turn_cls.requires_fresh_data,
        )
        if turn_cls.requires_few_shot:
            system_content = await self._inject_few_shot_examples(
                system_content,
                user_question=self._standalone_query,
                runtime_messages=runtime_messages,
            )
            if self._pending_few_shot_log:
                yield self._pending_few_shot_log
                self._pending_few_shot_log = None
        else:
            yield {
                "type": "log",
                "id": f"fewshot_search_{uuid.uuid4().hex[:8]}",
                "title": "跳过经验库检索",
                "details": "本轮无需新 SQL 生成，已跳过经验库检索以节省延迟。",
                "status": "success",
                "execution_time_ms": 0,
            }
        if turn_cls.requires_fresh_data and turn_cls.requires_few_shot:
            need_analysis_start = time.time()
            need_analysis_log_id = f"need_analysis_{uuid.uuid4().hex[:8]}"
            yield {
                "type": "log",
                "id": need_analysis_log_id,
                "title": "用户需求分析",
                "details": (
                    "正在结合用户原始问题与经验库案例，生成用于元数据检索的问题关键词..."
                    if self._fewshot_examples
                    else "正在分析用户原始问题，生成用于元数据检索的问题关键词..."
                ),
                "status": "pending",
                "started_at": int(need_analysis_start * 1000),
            }
            self._schema_search_keywords = await self._plan_schema_search_keywords(
                user_question,
                self._standalone_query,
                self._fewshot_examples,
            )
            system_content += (
                "\n\n【Schema 检索词规划】本轮已结合"
                + ("用户原始问题和经验库案例" if self._fewshot_examples else "用户原始问题")
                + f"规划出 get_dataset_schema 的检索词：{self._schema_search_keywords}\n"
                "首次检索数据集定义时，请优先使用这些 keywords；这些词仅用于检索元数据，不代表最终 SQL 表字段已确认。"
                f"\n【独立查数问题】{self._standalone_query}"
            )
            yield {
                "type": "log",
                "id": need_analysis_log_id,
                "title": "用户需求分析",
                "details": (
                    "已完成用户需求分析，并生成问题关键词。"
                    f"\n问题关键词: {self._schema_search_keywords or self._standalone_query or user_question}"
                ),
                "status": "success",
                "execution_time_ms": (time.time() - need_analysis_start) * 1000,
            }

        prefetched_schema_output: str | None = None
        if turn_cls.requires_fresh_data:
            schema_keywords = (
                self._schema_search_keywords
                or self._standalone_query
                or user_question
                or ""
            ).strip()
            async for chunk in self._auto_invoke_get_dataset_schema(
                keywords=schema_keywords,
                tools=tools,
            ):
                if chunk.get("__schema_output__") is not None:
                    prefetched_schema_output = str(chunk["__schema_output__"])
                    continue
                yield chunk
            if prefetched_schema_output:
                prefetch_state = _DataRunState()
                self._apply_schema_tool_result(prefetch_state, prefetched_schema_output)
                if self._is_schema_fatal(prefetch_state):
                    async for chunk in self._yield_schema_fatal_abort(
                        prefetch_state,
                        prefetched_schema_output,
                    ):
                        yield chunk
                    return
                system_content += (
                    "\n\n"
                    + DataQueryPrompts.prefetched_schema_context(
                        schema_keywords,
                        prefetched_schema_output,
                    )
                )

        llm_handle = await AgentConfigProvider.get_configured_llm(
            streaming=True,
            config=self.config,
        )
        native_model = getattr(llm_handle, "native_model", None)
        if native_model is None:
            yield {
                "type": "error",
                "status": "error",
                "content": "当前模型适配器未提供 AgentScope native_model，无法执行 ChatBI AgentScope 原生工具链。",
            }
            return

        agent_name = self._runtime_agent_name()
        tools_fingerprint = build_tools_fingerprint(self.config, tools)
        model_name = getattr(native_model, "model", self.config.model_name)
        try:
            async with agentscope_session_lock.hold(
                user_id=self._runtime_user_id(),
                conversation_id=self.conversation_id,
                agent_name=agent_name,
            ):
                async for chunk in self._run_native_agent_turn(
                    native_model=native_model,
                    tools=tools,
                    tools_fingerprint=tools_fingerprint,
                    model_name=model_name,
                    agent_name=agent_name,
                    system_content=system_content,
                    max_steps=max_steps,
                    llm_handle=llm_handle,
                    runtime_messages=runtime_messages,
                    user_question=user_question,
                    turn_cls=turn_cls,
                    prefetched_schema_output=prefetched_schema_output,
                ):
                    yield chunk
        except SessionLockTimeout:
            yield {
                "type": "error",
                "status": "error",
                "content": "当前会话正在处理中，请稍后再试。",
            }

    async def _run_native_agent_turn(
        self,
        *,
        native_model: Any,
        tools: list[RuntimeToolSpec],
        tools_fingerprint: str,
        model_name: Any,
        agent_name: str,
        system_content: str,
        max_steps: int,
        llm_handle: Any,
        runtime_messages: List[Any],
        user_question: str,
        turn_cls: Any,
        prefetched_schema_output: str | None = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        persisted = await agent_state_store.load(
            self._runtime_user_id(),
            self.conversation_id,
            agent_name,
        )
        restored_state = None
        if persisted and persisted.matches(
            tools_fingerprint=tools_fingerprint,
            agent_name=agent_name,
        ):
            try:
                from agentscope.state import AgentState

                restored_state = AgentState.model_validate(persisted.state)
            except Exception as exc:
                logger.warning("[DataAgentRunner] Failed to restore AgentState: %s", exc)

        state = _DataRunState()
        state.requires_fresh_data = turn_cls.requires_fresh_data
        state.requires_sql_plan = self._should_require_sql_plan(user_question)
        if prefetched_schema_output is not None:
            self._apply_schema_tool_result(state, prefetched_schema_output)
            if self._is_schema_fatal(state):
                async for chunk in self._yield_schema_fatal_abort(state, prefetched_schema_output):
                    yield chunk
                return
        guarded_tools = self._wrap_tools_with_schema_gate(tools, state)

        agent = await self._build_native_agent(
            native_model=native_model,
            tools=guarded_tools,
            system_content=system_content,
            max_steps=max_steps,
            restored_state=restored_state,
            primary_model_name=str(getattr(llm_handle, "model_name", self.config.model_name) or ""),
        )
        if self._standalone_query and self._standalone_query != user_question:
            runtime_messages = [
                *runtime_messages[:-1],
                HumanMessage(content=self._standalone_query),
            ]
        if restored_state and restored_state.context:
            inputs = to_agentscope_messages(
                compat_to_runtime_messages(self._latest_user_runtime_messages(runtime_messages))
            )
        else:
            inputs = to_agentscope_messages(compat_to_runtime_messages(runtime_messages))
        stream_meta = {
            "system_content": system_content,
            "max_steps": max_steps,
        }
        interrupted = False

        async for chunk in self._stream_agentscope_events(
            event_stream=agent.reply_stream(inputs),
            agent=agent,
            tools=guarded_tools,
            native_model=native_model,
            state=state,
            stream_meta=stream_meta,
            emit_final_guard=False,
        ):
            if is_interrupt_sse_chunk(chunk):
                interrupted = True
            yield chunk
        if interrupted:
            return
        if self._is_schema_fatal(state):
            async for chunk in self._yield_schema_fatal_abort(state):
                yield chunk
            return
        if state.full_content:
            if self.conversation_id:
                await agent_state_store.save(
                    user_id=self._runtime_user_id(),
                    conversation_id=self.conversation_id,
                    agent_name=agent_name,
                    agent_version=self.config.agent_version,
                    tools_fingerprint=tools_fingerprint,
                    model_name=str(model_name) if model_name else None,
                    state=agent.state,
                )
            return

        for _ in range(MAX_DATA_REPAIR_ROUNDS):
            repair_message = self._build_repair_message(state)
            if not repair_message:
                break
            repair_tool_choice = self._resolve_repair_tool_choice(state)
            yield {
                "type": "log",
                "id": f"data_repair_{uuid.uuid4().hex[:8]}",
                "title": self._build_repair_title(state),
                "details": repair_message,
                "status": "warning",
            }
            self._reset_state_for_repair(state)
            repair_inputs = to_agentscope_messages(compat_to_runtime_messages(repair_message))
            original_model = agent.model
            if repair_tool_choice is not None:
                agent.model = _ForcedFirstToolChoiceModel(original_model, repair_tool_choice)
            try:
                async for chunk in self._stream_agentscope_events(
                    event_stream=agent.reply_stream(repair_inputs),
                    agent=agent,
                    tools=guarded_tools,
                    native_model=native_model,
                    state=state,
                    stream_meta=stream_meta,
                    emit_final_guard=False,
                ):
                    if is_interrupt_sse_chunk(chunk):
                        interrupted = True
                    yield chunk
            finally:
                agent.model = original_model
            if interrupted:
                return
            if self._is_schema_fatal(state):
                async for chunk in self._yield_schema_fatal_abort(state):
                    yield chunk
                return
            if state.full_content:
                if self.conversation_id:
                    await agent_state_store.save(
                        user_id=self._runtime_user_id(),
                        conversation_id=self.conversation_id,
                        agent_name=agent_name,
                        agent_version=self.config.agent_version,
                        tools_fingerprint=tools_fingerprint,
                        model_name=str(model_name) if model_name else None,
                        state=agent.state,
                    )
                return

        async for chunk in self._emit_final_guard(state):
            yield chunk
        if self.conversation_id and not interrupted:
            await agent_state_store.save(
                user_id=self._runtime_user_id(),
                conversation_id=self.conversation_id,
                agent_name=agent_name,
                agent_version=self.config.agent_version,
                tools_fingerprint=tools_fingerprint,
                model_name=str(model_name) if model_name else None,
                state=agent.state,
            )

    async def _inject_few_shot_examples(
        self,
        system_content: str,
        *,
        user_question: str,
        runtime_messages: List[Any],
    ) -> str:
        try:
            from app.services.chatbi_example_service import ExampleService

            examples = await ExampleService.search_examples(
                user_question,
                dataset_id=None,
                top_k=5,
                history=runtime_messages,
            )
            self._fewshot_examples = examples or []
            if not examples:
                return system_content

            max_sim = max([ex.get("similarity", 0) for ex in examples])
            sim_status = "匹配度极高" if max_sim >= 0.80 else "匹配度一般"
            hit_titles = [
                f"#{ex.get('id', '?')} 「{str(ex.get('question', ''))[:15]}...」 (相似度: {ex.get('similarity', 0):.2f})"
                for ex in examples
            ]
            self.trace_buffer.append(
                AgentExecutionStep(
                    step_number=self._increment_step(),
                    event_type="few_shot",
                    agent_name=self.config.agent_name,
                    model=str(self.config.model_name),
                    temperature=float(self.config.temperature or 0),
                    tool_output={"examples": examples},
                    raw_log="\n".join(hit_titles),
                    execution_time_ms=0,
                    timestamp=datetime.now(),
                )
            )
            few_shot_block = ExampleService.build_few_shot_prompt(examples)
            if few_shot_block:
                system_content = f"{few_shot_block}\n\n---\n\n{system_content}"

            example_ids = [ex["id"] for ex in examples if ex.get("id")]
            similarities = [ex.get("similarity", 0) for ex in examples if ex.get("id")]
            if example_ids:
                await ExampleService.record_usage(example_ids, self.trace_id, similarities=similarities)

            self._pending_few_shot_log = {
                "type": "log",
                "id": f"fewshot_{uuid.uuid4().hex[:6]}",
                "title": f"✨ 命中经验库案例 ({len(examples)}条, {sim_status})",
                "details": (
                    "已匹配到历史优质 SQL 案例：\n"
                    + "\n".join(hit_titles)
                    + f"\n\n当前最高相似度: {max_sim:.2f}。这些案例将作为强制性参考引导模型生成 SQL，以减少冗余迭代。"
                ),
                "status": "success",
            }
            return system_content
        except Exception as e:
            self._fewshot_examples = []
            logger.warning("[DataAgentRunner] Failed to search/inject few-shot examples: %s", e)
            return system_content

    @staticmethod
    def _example_schema_keyword_context(examples: List[Dict[str, Any]], limit: int = 3) -> str:
        if not examples:
            return "无"

        blocks = []
        for idx, ex in enumerate(examples[:limit], 1):
            sql = str(ex.get("sql") or "")
            sql_meta = ex.get("sql_metadata") if isinstance(ex.get("sql_metadata"), dict) else {}
            tables = list(sql_meta.get("tables") or [])
            dimensions = list(sql_meta.get("dimensions") or [])
            if not tables and sql:
                table_matches = re.findall(r"\b(?:FROM|JOIN)\s+([`\w.]+)", sql, flags=re.IGNORECASE)
                tables = [table.strip("`") for table in table_matches]
            column_like_tokens = []
            if sql:
                for token in re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b", sql):
                    if token.upper() in {
                        "SELECT", "FROM", "WHERE", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER",
                        "GROUP", "ORDER", "LIMIT", "SUM", "COUNT", "AVG", "MAX", "MIN", "AND", "OR",
                        "ON", "BY", "AS", "DESC", "ASC", "WITH", "CASE", "WHEN", "THEN", "ELSE", "END",
                    }:
                        continue
                    column_like_tokens.append(token)
            deduped_tokens = list(dict.fromkeys(column_like_tokens))[:20]
            blocks.append(
                "\n".join(
                    [
                        f"案例 {idx}:",
                        f"- 历史问题: {ex.get('question') or ''}",
                        f"- 数据集: {ex.get('dataset_name') or ''}",
                        f"- 核心表: {', '.join(tables[:8]) if tables else ''}",
                        f"- 核心维度: {', '.join(dimensions[:8]) if dimensions else ''}",
                        f"- SQL 关键词: {', '.join(deduped_tokens)}",
                    ]
                )
            )
        return "\n\n".join(blocks)

    async def _plan_schema_search_keywords(
        self,
        user_question: str,
        standalone_query: str,
        examples: List[Dict[str, Any]],
    ) -> str:
        fallback_query = (standalone_query or user_question or "").strip()[:300]
        prompt = (
            "你是 ChatBI 的元数据检索词规划器。你的任务不是生成 SQL，而是为 get_dataset_schema(keywords) "
            "生成最适合检索数据集/表/字段/指标定义的短关键词。\n\n"
            "要求：\n"
            "1. 结合用户原始问题、独立查数问题和命中的历史案例；若没有历史案例，也必须从用户需求中抽取关键词。\n"
            "2. 优先保留业务对象/实体、指标、维度、时间字段含义，以及历史案例中出现过的物理表名/字段名。\n"
            "3. 去掉无助于元数据检索的动作词、礼貌词、排序数量描述和 SQL 生成意图。\n"
            "4. 不要生成 SQL，不要编造案例中没有出现的新物理表名。\n"
            "5. keywords 必须是空格分隔的关键词短语，优先 3 到 10 个词；不要输出完整查询句子。\n"
            "6. 只返回 JSON。示例：{\"keywords\":\"商品 销售额 省份 product_order_detail product_name sales_amount\"}。\n"
            "7. keywords 不能为空，禁止输出 `...`、`关键词`、`N/A` 这类占位符。\n\n"
            f"【用户原始问题】\n{user_question}\n\n"
            f"【独立查数问题】\n{standalone_query}\n\n"
            f"【命中的历史案例线索】\n{self._example_schema_keyword_context(examples)}"
        )
        try:
            model = await AgentConfigProvider.get_configured_llm(streaming=False, config=self.config)
            response = await model.ainvoke([SystemMessage(content=prompt)])
            tokens = extract_tokens_from_message(response)
            self.record_llm_token_usage(
                prompt_tokens=tokens["prompt_tokens"],
                completion_tokens=tokens["completion_tokens"],
                event_type="thought",
                model=str(getattr(model, "model_name", self.config.model_name) or ""),
                tool_name="schema_keyword_planner",
            )
            content = (getattr(response, "content", "") or "").strip()
            data = {}
            try:
                data = json.loads(content)
            except Exception:
                match = re.search(r"\{.*\}", content, flags=re.DOTALL)
                if match:
                    data = json.loads(match.group())
            keywords = str(data.get("keywords") or "").strip()
            if self._is_invalid_schema_search_keywords(keywords):
                return fallback_query
            return keywords[:300]
        except Exception as e:
            logger.warning("[DataAgentRunner] Failed to plan schema search keywords: %s", e)
            return fallback_query

    @staticmethod
    def _is_invalid_schema_search_keywords(keywords: str) -> bool:
        normalized = re.sub(r"\s+", "", str(keywords or "")).strip().lower()
        if not normalized:
            return True
        return normalized in {
            "...", "…", "keyword", "keywords", "关键词", "问题关键词",
            "n/a", "na", "none", "null", "无",
        }

    async def _auto_invoke_get_dataset_schema(
        self,
        *,
        keywords: str,
        tools: list[RuntimeToolSpec],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """新查数路径在 ReAct 开始前平台侧自动执行 get_dataset_schema。"""
        schema_spec = next((tool for tool in tools if tool.name == "get_dataset_schema"), None)
        if schema_spec is None:
            logger.warning("[DataAgentRunner] get_dataset_schema tool missing; skip auto prefetch")
            return

        tool_id = f"schema_prefetch_{uuid.uuid4().hex[:8]}"
        started_at = time.time()
        yield {
            "type": "log",
            "id": tool_id,
            "title": "自动获取数据集定义",
            "details": f"平台自动调用 get_dataset_schema(keywords={keywords or 'None'})",
            "status": "pending",
            "category": "tool",
            "started_at": int(started_at * 1000),
        }

        output = ""
        try:
            result = schema_spec.callable(keywords=keywords or None)
            if inspect.isawaitable(result):
                result = await result
            output = str(result or "")
        except Exception as exc:
            logger.error("[DataAgentRunner] Auto get_dataset_schema failed: %s", exc)
            output = f"[TOOL_ERROR] 自动获取数据集定义失败: {exc}"

        preview_state = _DataRunState()
        self._apply_schema_tool_result(preview_state, output)
        yield {
            "type": "log",
            "id": tool_id,
            "title": "工具完成: get_dataset_schema",
            "details": self._format_tool_details(
                "get_dataset_schema",
                output,
                preview_state,
                {"keywords": keywords},
            ),
            "status": "error" if self._is_schema_fatal(preview_state) else "success",
            "category": "tool",
            "execution_time_ms": (time.time() - started_at) * 1000,
        }
        self._increment_step()
        self.trace_buffer.append(
            AgentExecutionStep(
                step_number=self.step_counter,
                event_type="tool_call",
                agent_name=self.config.agent_name,
                model=self.config.model_name,
                temperature=float(self.config.temperature or 0),
                tool_name="get_dataset_schema",
                tool_input={"keywords": keywords},
                tool_output=output,
                raw_log=output[:4000],
                execution_time_ms=(time.time() - started_at) * 1000,
                timestamp=datetime.fromtimestamp(started_at),
            )
        )
        yield {"__schema_output__": output}

    def _should_rewrite_contextual_new_data_query(
        self,
        user_question: str,
        runtime_messages: List[Any],
    ) -> bool:
        q = (user_question or "").strip()
        if not q:
            return False
        prior_messages = [
            message
            for message in runtime_messages[:-1]
            if isinstance(message, (HumanMessage, AIMessage)) and getattr(message, "content", None)
        ]
        if not prior_messages:
            return False

        q_lower = q.lower()
        context_markers = [
            "那", "这个", "那个", "它", "其", "刚才", "上面", "上一轮", "前面", "之前",
            "本月呢", "上月呢", "今天呢", "昨天呢", "本周呢", "上周呢",
            "再按", "再看", "再查", "换成", "改成", "只看", "只查", "也看", "也查",
            "then", "this", "that", "it", "previous", "last one", "what about",
        ]
        if any(marker in q_lower for marker in context_markers):
            return True
        query_verbs = ["查询", "查", "统计", "列出", "展示", "显示", "获取", "select", "show", "list"]
        return len(q) < 8 and not any(verb in q_lower for verb in query_verbs)

    async def _resolve_standalone_query_for_new_data_query(
        self,
        user_question: str,
        runtime_messages: List[Any],
    ) -> str:
        q = (user_question or "").strip()

        system_prompt = getattr(self.config, "system_prompt", None)
        if not isinstance(system_prompt, str):
            system_prompt = ""
        ltm_match = re.search(r"(\[Memory Profile\][\s\S]*?)(?=\n\n\[|\Z)", system_prompt)
        ltm_context = ltm_match.group(1).strip() if ltm_match else ""
        has_ltm = bool(ltm_context.replace("[Memory Profile]", "").strip()) if ltm_context else False

        need_rewrite = has_ltm or self._should_rewrite_contextual_new_data_query(q, runtime_messages)
        if not q or not need_rewrite:
            return q

        recent_history = []
        for message in runtime_messages[-7:-1]:
            if not isinstance(message, (HumanMessage, AIMessage)):
                continue
            content = getattr(message, "content", "") or ""
            if not isinstance(content, str):
                content = str(content)
            content = re.sub(r"\s+", " ", content).strip()
            if not content:
                continue
            role = "用户" if isinstance(message, HumanMessage) else "助手"
            recent_history.append(f"{role}: {content[:220]}")

        if not recent_history and not has_ltm:
            return q

        instructions = [
            "1. 只补全上下文缺失的查询对象、指标、维度、时间范围或筛选条件。",
            "2. 必须保留最新提问新增或修改的条件。",
            "3. 不要生成 SQL，不要选择表名/字段名，不要解释。",
            "4. 如果无法可靠补全，原样返回最新提问。",
        ]
        ltm_section = ""
        if has_ltm:
            instructions.append("5. 结合【用户个性化偏好与记忆】，将最新提问中的俗称、别名、旧称转换为对应的标准名称。")
            ltm_section = f"\n\n【用户个性化偏好与记忆】\n{ltm_context}"

        prompt = (
            "你是 ChatBI 查询改写器。请根据最近对话和用户偏好，把【最新提问】改写成一句独立、完整、适合检索元数据和历史 SQL 案例的查数问题。\n"
            "要求：\n"
            + "\n".join(instructions)
            + ltm_section
        )
        if recent_history:
            prompt += "\n\n【最近对话】\n" + "\n".join(recent_history)
        prompt += f"\n\n【最新提问】\n{q}\n\n【改写后的独立查数问题】"

        try:
            model = await AgentConfigProvider.get_configured_llm(streaming=False, config=self.config)
            response = await model.ainvoke([SystemMessage(content=prompt)])
            tokens = extract_tokens_from_message(response)
            self.record_llm_token_usage(
                prompt_tokens=tokens["prompt_tokens"],
                completion_tokens=tokens["completion_tokens"],
                event_type="thought",
                model=str(getattr(model, "model_name", self.config.model_name) or ""),
                tool_name="standalone_query_rewrite",
            )
            rewritten = (getattr(response, "content", "") or "").strip().strip('"').strip("'")
            if not rewritten:
                return q
            return rewritten[:300]
        except Exception as e:
            logger.warning("[DataAgentRunner] Failed to rewrite standalone data query: %s", e)
            return q

    async def _synthesize_from_last_data_result(
        self,
        runtime_messages: List[Any],
        system_prompt: str,
        user_question: str,
        last_result: Dict[str, Any],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        start_synthesis = time.time()
        yield {
            "type": "log",
            "id": f"reuse_{uuid.uuid4().hex[:8]}",
            "title": "复用上一轮查询结果",
            "details": "检测到本轮是基于上一轮结果的分析/可视化请求，已跳过重新检索 Schema 与执行 SQL。",
            "status": "success",
        }
        yield {"type": "thinking", "status": "continuing"}

        prompt_without_menu = (system_prompt or "").replace(
            "{dataset_menu}",
            DataQueryPrompts.REUSE_DATASET_MENU_PLACEHOLDER,
        )
        result_json = json.dumps(last_result, ensure_ascii=False, indent=2)
        if len(result_json) > 30000:
            result_json = result_json[:30000] + "\n... [上一轮结果过长已截断]"

        from app.services.ai.runtime.agentscope.compat import HumanMessage

        synthesis_messages = [SystemMessage(content=prompt_without_menu)]
        # 只保留用户追问，不把上一轮 assistant 全文（含图表/表格）塞进 prompt，避免模型照抄两遍。
        synthesis_messages.extend(
            message
            for message in runtime_messages[-6:-1]
            if isinstance(message, HumanMessage) and getattr(message, "content", None)
        )
        synthesis_messages.append(
            HumanMessage(content=DataQueryPrompts.followup_synthesis_user_message(user_question, result_json))
        )

        final_llm = await AgentConfigProvider.get_synthesis_llm(streaming=True, config=self.config)
        full_synthesis_content = ""
        content_emitted = False
        generation_start = None
        gen_log_id = f"gen_{uuid.uuid4().hex[:8]}"
        last_synthesis_chunk = None
        try:
            async for chunk in final_llm.astream(normalize_messages_for_llm(synthesis_messages)):
                last_synthesis_chunk = chunk
                content = str(getattr(chunk, "content", "") or "")
                if not content:
                    continue
                if not content_emitted:
                    generation_start = time.time()
                    content_emitted = True
                    yield {
                        "type": "log",
                        "id": gen_log_id,
                        "title": "✨ 开始生成回复",
                        "status": "pending",
                        "started_at": int(generation_start * 1000),
                    }
                full_synthesis_content += content
                yield {"content": content}
            if generation_start:
                yield {
                    "type": "log",
                    "id": gen_log_id,
                    "title": "✨ 生成回复完成",
                    "status": "success",
                    "execution_time_ms": (time.time() - generation_start) * 1000,
                }
        except Exception as syn_err:
            logger.error("[DataAgentRunner] Follow-up synthesis failed: %s", syn_err)
            fallback = DataQueryPrompts.FOLLOWUP_SYNTHESIS_FALLBACK
            full_synthesis_content = fallback
            yield {
                "type": "log",
                "id": f"syn_err_{uuid.uuid4().hex[:6]}",
                "title": "⚠️ 总结生成失败",
                "details": str(syn_err),
                "status": "error",
            }
            yield {"content": fallback}

        deduped_synthesis = collapse_repeated_reply(full_synthesis_content)
        if deduped_synthesis != full_synthesis_content:
            logger.warning(
                "[DataAgentRunner] Collapsed duplicated follow-up synthesis output (len %s -> %s)",
                len(full_synthesis_content),
                len(deduped_synthesis),
            )
            full_synthesis_content = deduped_synthesis
            if content_emitted:
                yield {"type": "retraction", "content": full_synthesis_content}

        synthesis_tokens = extract_tokens_from_message(last_synthesis_chunk)
        self._increment_step()
        self.trace_buffer.append(
            AgentExecutionStep(
                step_number=self.step_counter,
                event_type="synthesis",
                agent_name=self.config.agent_name,
                model=str(getattr(final_llm, "model_name", self.config.synthesis_model_name or self.config.model_name)),
                temperature=float(self.config.synthesis_temperature or self.config.temperature or 0),
                tool_output={"content": full_synthesis_content, "reused_last_data_result": True},
                raw_log=full_synthesis_content,
                prompt_tokens=synthesis_tokens["prompt_tokens"],
                completion_tokens=synthesis_tokens["completion_tokens"],
                total_tokens=synthesis_tokens["total_tokens"],
                execution_time_ms=(time.time() - start_synthesis) * 1000,
                timestamp=datetime.fromtimestamp(start_synthesis),
            )
        )

    async def _build_system_content(
        self,
        *,
        context_action_result: Optional[Dict[str, Any]] = None,
        include_context_action: bool = False,
    ) -> str:
        system_prompt = self.config.system_prompt or ""
        if "{dataset_menu}" in system_prompt:
            user_id = self.user_info.get("user_id") if self.user_info else None
            is_admin = self.user_info.get("role") == "admin" if self.user_info else False
            dataset_menu = await AgentConfigProvider.get_dataset_menu(user_id=user_id, is_admin=is_admin)
            system_prompt = system_prompt.replace("{dataset_menu}", dataset_menu)
        context_action_prompt = ""
        if include_context_action:
            result_json = ""
            if context_action_result:
                result_json = json.dumps(context_action_result, ensure_ascii=False)
                if len(result_json) > 20000:
                    result_json = result_json[:20000] + "\n... [上一轮结果过长已截断]"
            context_action_prompt = f"\n\n{DataQueryPrompts.context_action_guide(result_json)}"
        return (
            f"{DataQueryPrompts.GLOBAL_GUARDRAILS}\n\n"
            f"{DataQueryPrompts.SQL_PLAN_ENFORCEMENT}\n\n"
            f"{DataQueryPrompts.FOLLOWUP_REUSE_CONSTRAINT}\n\n"
            f"{system_prompt}"
            f"{context_action_prompt}"
        )

    @staticmethod
    def _data_run_state_to_pending_state(
        state: _DataRunState,
        stream_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        from dataclasses import asdict

        return {
            **stream_meta,
            "data_run_state": asdict(state),
        }

    @staticmethod
    def _sync_pending_data_run_state(
        state: _DataRunState,
        pending_state: Dict[str, Any],
    ) -> None:
        from dataclasses import asdict

        pending_state["data_run_state"] = asdict(state)

    @staticmethod
    def _build_stream_state(
        state: _DataRunState,
        stream_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        stream_state = DataAgentRunner._data_run_state_to_pending_state(state, stream_meta)
        stream_state["tool_names"] = state.tool_names
        stream_state["tool_args_text"] = state.tool_args_text
        stream_state["tool_outputs"] = state.tool_outputs
        stream_state["tool_started_at"] = state.tool_started_at
        stream_state.setdefault("tool_data", {})
        return stream_state

    @staticmethod
    def _pending_state_to_data_run_state(pending_state: Dict[str, Any]) -> tuple[_DataRunState, Dict[str, Any]]:
        from dataclasses import fields

        raw = pending_state.get("data_run_state") or {}
        valid_keys = {field.name for field in fields(_DataRunState)}
        kwargs = {key: raw[key] for key in valid_keys if key in raw}
        data_state = _DataRunState(**kwargs)
        stream_meta = {
            key: pending_state[key]
            for key in ("system_content", "max_steps")
            if key in pending_state
        }
        return data_state, stream_meta

    async def _stream_agentscope_events(
        self,
        *,
        event_stream: Any,
        agent: Any | None = None,
        tools: list[RuntimeToolSpec],
        native_model: Any,
        state: _DataRunState | None = None,
        stream_meta: Dict[str, Any] | None = None,
        emit_final_guard: bool = True,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        state = state or _DataRunState()
        stream_meta = stream_meta or {}
        self._last_run_state = state
        stream_state = self._build_stream_state(state, stream_meta)

        async def on_before_pending_interrupt(pending_state: Dict[str, Any]) -> None:
            self._sync_pending_data_run_state(state, pending_state)

        async def on_tool_result_end(event: Any) -> AsyncGenerator[Dict[str, Any], None]:
            tool_id = getattr(event, "tool_call_id", "")
            tool_name = state.tool_names.get(tool_id, "")
            raw_args = state.tool_args_text.get(tool_id, "") or "{}"
            try:
                tool_args = json.loads(raw_args)
            except Exception:
                tool_args = {"input": raw_args}
            output = state.tool_outputs.get(tool_id, "")
            duration_ms = (time.time() - state.tool_started_at.get(tool_id, time.time())) * 1000
            if tool_name == "get_dataset_schema":
                self._apply_schema_tool_result(state, output)
            elif tool_name == "execute_sql_query":
                if self._is_sql_repeat_gate_block(output):
                    pass
                elif self._is_schema_gate_block(output) or not state.schema_completed:
                    state.sql_before_schema = True
                else:
                    if state.requires_sql_plan and not state.sql_plan_seen:
                        state.sql_plan_missing = True
                    state.sql_completed = True
                    parsed_output = self._try_parse_json_output(output)
                    state.empty_sql_reason = self._detect_empty_result(parsed_output) or ""
                    state.empty_sql_result = bool(state.empty_sql_reason)
                    state.sql_error, state.sql_error_message = self._detect_sql_error(output)
                    if not state.sql_error:
                        await self._save_last_data_result_for_followups(tool_args, parsed_output)
            self._sync_pending_data_run_state(state, stream_state)
            self._increment_step()
            self.trace_buffer.append(
                AgentExecutionStep(
                    step_number=self.step_counter,
                    event_type="tool_call",
                    agent_name=self.config.agent_name,
                    model=getattr(native_model, "model", self.config.model_name),
                    temperature=float(self.config.temperature or 0),
                    tool_name=tool_name,
                    tool_input=tool_args,
                    tool_output=output,
                    raw_log=str(output),
                    execution_time_ms=duration_ms,
                    timestamp=datetime.fromtimestamp(state.tool_started_at.get(tool_id, time.time())),
                )
            )
            yield {
                "type": "log",
                "id": tool_id,
                "title": f"工具完成: {tool_name}",
                "details": self._format_tool_details(tool_name, output, state, tool_args),
                "status": "success",
                "execution_time_ms": duration_ms,
            }

        async def on_text_block_delta(event: Any) -> AsyncGenerator[Dict[str, Any], None]:
            block_id = str(getattr(event, "block_id", "") or "")
            if block_id:
                state.active_text_block_id = block_id
            if state.ignore_text_block:
                return
            delta = str(getattr(event, "delta", ""))
            state.text_window = (state.text_window + delta)[-4000:]
            if self._has_sql_plan(state.text_window):
                if not state.sql_plan_seen:
                    state.sql_plan_seen = True
                    self._sync_pending_data_run_state(state, stream_state)
            if not state.ready_to_answer:
                state.blocked_content += delta
                return
            if not state.content_emitted:
                state.content_emitted = True
                yield {
                    "type": "log",
                    "id": f"gen_data_{uuid.uuid4().hex[:8]}",
                    "title": "✨ 开始生成回复",
                    "status": "success",
                }
            state.full_content += delta
            state.current_text_block_emitted = True
            yield {"content": delta}

        async for event in event_stream:
            event_type = str(getattr(event, "type", ""))
            if event_type == "MODEL_CALL_END":
                self._record_agent_scope_model_call(
                    event,
                    state=stream_state,
                    native_model=native_model,
                )
            if event_type == "TOOL_CALL_START":
                state.text_blocks_emitted_since_last_tool = 0
                state.ignore_text_block = False
                state.current_text_block_emitted = False

            if event_type == "TEXT_BLOCK_START":
                block_id = str(getattr(event, "block_id", "") or "")
                if block_id:
                    state.active_text_block_id = block_id
                state.current_text_block_emitted = False
                state.ignore_text_block = (
                    state.ready_to_answer
                    and state.text_blocks_emitted_since_last_tool >= 1
                )
                continue

            if event_type == "TEXT_BLOCK_END":
                if state.current_text_block_emitted and state.ready_to_answer:
                    state.text_blocks_emitted_since_last_tool += 1
                state.current_text_block_emitted = False
                continue

            async for chunk in map_standard_agentscope_event(
                event,
                state=stream_state,
                on_tool_result_end=on_tool_result_end,
                on_text_block_delta=on_text_block_delta,
                on_before_pending_interrupt=on_before_pending_interrupt,
                agent=agent,
                runner=self,
                tools=tools,
                native_model=native_model,
                agent_name=self._runtime_agent_name(),
            ):
                yield chunk
                if is_interrupt_sse_chunk(chunk):
                    return

        if emit_final_guard:
            guard_emitted = False
            async for chunk in self._emit_final_guard(state):
                guard_emitted = True
                yield chunk
            if guard_emitted:
                return

        if state.full_content:
            self._increment_step()
            self.trace_buffer.append(
                AgentExecutionStep(
                    step_number=self.step_counter,
                    event_type="synthesis",
                    agent_name=self.config.agent_name,
                    model=getattr(native_model, "model", self.config.model_name),
                    temperature=float(self.config.temperature or 0),
                    tool_output={"content": state.full_content},
                    raw_log=state.full_content,
                    execution_time_ms=(time.time() - state.start_synthesis) * 1000,
                    timestamp=datetime.fromtimestamp(state.start_synthesis),
                )
            )

    @staticmethod
    def _is_schema_fatal(state: _DataRunState) -> bool:
        return (
            state.schema_service_unavailable
            or state.no_authorized_schema
            or state.rag_not_synced
        )

    def _schema_fatal_response(self, state: _DataRunState) -> tuple[str, str]:
        if state.schema_service_unavailable:
            return (
                "元数据服务不可用",
                DataQueryPrompts.SCHEMA_SERVICE_UNAVAILABLE_CONTENT,
            )
        if state.no_authorized_schema:
            return (
                "无授权数据集",
                DataQueryPrompts.NO_AUTHORIZED_SCHEMA_CONTENT,
            )
        if state.rag_not_synced:
            return (
                "元数据未同步知识库",
                DataQueryPrompts.RAG_NOT_SYNCED_CONTENT,
            )
        return (
            "Schema 获取失败",
            DataQueryPrompts.SCHEMA_SERVICE_UNAVAILABLE_CONTENT,
        )

    async def _yield_schema_fatal_abort(
        self,
        state: _DataRunState,
        details: Any = "",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        title, content = self._schema_fatal_response(state)
        yield {
            "type": "log",
            "id": f"schema_fatal_{uuid.uuid4().hex[:8]}",
            "title": title,
            "details": truncate_for_context(str(details or ""), max_len=1000) or title,
            "status": "error",
        }
        yield {
            "content": content,
            "status": "error",
        }

    async def _emit_final_guard(
        self,
        state: _DataRunState,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        if state.full_content or not state.blocked_content or state.ready_to_answer:
            return
        if self._is_schema_fatal(state):
            _, content = self._schema_fatal_response(state)
        elif state.sql_before_schema:
            content = "为保证数据准确性，必须先调用 get_dataset_schema 获取数据集定义，再执行 SQL 查询。"
        elif state.sql_error:
            content = "SQL 执行失败，必须根据错误信息修正 SQL 并重新执行成功后才能回答。"
        elif state.empty_sql_result:
            content = "SQL 返回空结果，必须先用诊断 SQL 复查筛选条件或 JOIN 条件，再执行最终 SQL 后才能回答。"
        elif state.sql_plan_missing:
            content = "高风险数据查询必须先补充 SQL 计划，再执行 SQL 查询并确认结果后才能回答。"
        else:
            content = "为保证数据准确性，必须先完成数据集定义检索和 SQL 查询后才能回答。"
        yield {
            "type": "log",
            "id": f"data_guard_{uuid.uuid4().hex[:8]}",
            "title": "阻止未查数回答",
            "details": "模型在满足 ChatBI 查数顺序前尝试直接回答，已拦截该输出。",
            "status": "warning",
        }
        yield {
            "content": content,
            "status": "error",
        }

    def _build_repair_message(self, state: _DataRunState) -> str:
        if self._is_schema_fatal(state):
            return ""
        if state.requires_fresh_data and state.sql_before_schema and not state.schema_completed:
            return (
                "【Schema 顺序要求】本轮新数据查询必须先调用 get_dataset_schema 获取数据集定义，"
                "再调用 execute_sql_query。\n"
                f"{DataQueryPrompts.MUST_FETCH_SCHEMA}\n"
                "在获得有效 schema 前禁止生成或执行 SQL，也禁止直接回答用户。"
            )
        if state.schema_miss and not state.no_authorized_schema:
            return (
                "【Schema 重试要求】上一轮 get_dataset_schema 未命中相关数据集定义。"
                "请换用更宽泛的业务关键词重新调用 get_dataset_schema，关键词应包含用户问题中的业务对象、指标、维度，"
                "并可追加：数据集 表 字段 指标 维度 物理表 业务口径。"
                "在获得有效 schema 前禁止生成或执行 SQL，也禁止直接回答用户。"
            )
        if state.sql_plan_missing:
            return (
                "【SQL 计划补充要求】本轮问题属于高风险数据查询（如比率/趋势/排名/分组）。"
                "上一轮 execute_sql_query 前没有提供 <sql_plan>。"
                "请先输出 <thought><sql_plan>{...}</sql_plan></thought>，至少包含 dataset_name、data_source、"
                "grain_keys、time_window、metrics_hit、joins、ratio，然后重新调用 execute_sql_query。"
                "在补充计划并重新执行 SQL 成功前禁止直接回答用户。"
            )
        if state.sql_error:
            return (
                "【SQL 修正要求】上一轮 execute_sql_query 执行失败。"
                f"错误信息：{state.sql_error_message[:800]}\n"
                "请基于已获得的 get_dataset_schema 结果修正 SQL，并再次调用 execute_sql_query。"
                "在 SQL 成功前禁止直接回答用户。"
            )
        if state.empty_sql_result:
            return (
                "【空结果复查要求】上一轮 execute_sql_query 执行成功但返回空结果。"
                f"原因：{state.empty_sql_reason}\n"
                "请先用诊断 SQL 复查筛选值、时间范围、子查询或 JOIN 条件，再执行最终 SQL。"
                "在最终 SQL 返回有效结果前禁止直接回答用户。"
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
            if not state.sql_completed:
                return (
                    "【查数顺序要求】你已获取数据集 Schema，但尚未执行 execute_sql_query。\n"
                    f"{DataQueryPrompts.FORCE_SQL_AFTER_SCHEMA}\n"
                    "禁止直接回答用户，必须先完成 SQL 查数。"
                )
        return ""

    def _reset_state_for_repair(self, state: _DataRunState) -> None:
        state.blocked_content = ""
        state.full_content = ""
        state.content_emitted = False
        state.ignore_text_block = False
        state.active_text_block_id = ""
        state.text_blocks_emitted_since_last_tool = 0
        state.current_text_block_emitted = False
        state.sql_completed = False
        state.sql_error = False
        state.sql_error_message = ""
        state.empty_sql_result = False
        state.empty_sql_reason = ""
        state.sql_plan_missing = False
        state.sql_before_schema = False

    def _resolve_repair_tool_choice(self, state: _DataRunState) -> Any | None:
        from agentscope.tool import ToolChoice

        if state.requires_fresh_data and state.sql_before_schema and not state.schema_completed:
            return ToolChoice(mode="get_dataset_schema")
        if state.schema_miss and not state.no_authorized_schema:
            return ToolChoice(mode="get_dataset_schema")
        if state.sql_plan_missing:
            return None
        if state.sql_error or state.empty_sql_result:
            return ToolChoice(mode="required")
        if (
            state.requires_fresh_data
            and state.blocked_content.strip()
            and not state.ready_to_answer
        ):
            if not state.schema_completed:
                return ToolChoice(mode="get_dataset_schema")
            if not state.sql_completed:
                return ToolChoice(mode="execute_sql_query")
        return None

    def _build_repair_title(self, state: _DataRunState) -> str:
        if state.requires_fresh_data and state.sql_before_schema and not state.schema_completed:
            return "必须先检索数据集定义"
        if state.schema_miss and not state.no_authorized_schema:
            return "重试检索数据集定义"
        if state.sql_plan_missing:
            return "补充 SQL 计划"
        if (
            state.requires_fresh_data
            and state.blocked_content.strip()
            and not state.ready_to_answer
        ):
            if not state.schema_completed:
                return "必须先完成查数流程"
            if not state.sql_completed:
                return "必须先执行 SQL 查数"
        return "修正 SQL 查询"

    def _has_sql_plan(self, text: str) -> bool:
        if not text:
            return False
        return re.search(r"<sql_plan>\s*\{[\s\S]*?\}\s*</sql_plan>", text, flags=re.IGNORECASE) is not None

    def _should_require_sql_plan(self, user_question: str) -> bool:
        question = (user_question or "").strip().lower()
        if not question:
            return False
        high_risk_keywords = [
            "率", "占比", "比例", "比率", "负载", "利用率", "pue", "成功率", "转化率", "人均", "单价",
            "同比", "环比", "趋势", "变化", "增长", "下降",
            "top", "排名", "排行", "分组", "维度", "group", "join",
            "p95", "p90", "分位", "中位", "median", "percentile",
        ]
        if any(keyword in question for keyword in high_risk_keywords):
            return True
        return re.search(r"按.{0,12}(组|类|类型|维度|机房|区域|部门|用户|状态)", question) is not None

    def _format_sql_result_for_display(self, output: Any, *, max_rows: int = _SQL_RESULT_DISPLAY_MAX_ROWS) -> str:
        parsed = self._try_parse_json_output(output)
        if isinstance(parsed, dict) and self._is_structured_sql_result(parsed):
            display: dict[str, Any] = dict(parsed)
            for key in _SQL_RESULT_ROW_KEYS:
                rows = display.get(key)
                if isinstance(rows, list) and len(rows) > max_rows:
                    display[key] = rows[:max_rows]
                    display["_display_note"] = f"仅展示前 {max_rows} 行，共 {len(rows)} 行"
                    break
            try:
                return json.dumps(display, ensure_ascii=False, indent=2)
            except Exception:
                return str(output or "")
        if isinstance(parsed, list):
            if len(parsed) > max_rows:
                payload = {
                    "_display_note": f"仅展示前 {max_rows} 行，共 {len(parsed)} 行",
                    "rows": parsed[:max_rows],
                }
            else:
                payload = parsed
            try:
                return json.dumps(payload, ensure_ascii=False, indent=2)
            except Exception:
                return str(output or "")
        return str(output or "")

    def _build_sql_error_tool_details(self, output: Any, tool_args: dict[str, Any] | None) -> str:
        text = str(output or "")
        marker = "[Executed SQL]:"
        if marker in text:
            error_part, sql_part = text.split(marker, 1)
            error_part = error_part.strip()
            sql_part = sql_part.strip()
        else:
            error_part = text.strip()
            sql_part = ""
            if tool_args:
                raw_sql = tool_args.get("sql") or tool_args.get("query")
                if isinstance(raw_sql, str) and raw_sql.strip():
                    sql_part = raw_sql.strip()
        error_display = truncate_for_context(error_part, max_len=1000)
        if sql_part:
            return f"[Executed SQL]:\n{sql_part}\n\n{_SQL_TOOL_ERROR_DELIMITER}\n{error_display}"
        return error_display

    def _format_tool_details(
        self,
        tool_name: str,
        output: Any,
        state: _DataRunState,
        tool_args: dict[str, Any] | None = None,
    ) -> str:
        if tool_name == "execute_sql_query" and not self._is_schema_gate_block(output):
            parsed = self._try_parse_json_output(output)
            if self._is_structured_sql_result(parsed):
                result_text = self._format_sql_result_for_display(output)
                result_details = truncate_for_context(result_text, max_len=1000)
                details = result_details
                output_text = str(output or "")
                if "[Executed SQL]:" not in output_text and tool_args:
                    raw_sql = tool_args.get("sql") or tool_args.get("query")
                    if isinstance(raw_sql, str) and raw_sql.strip():
                        details = (
                            f"[Executed SQL]:\n{raw_sql.strip()}\n\n"
                            f"{_SQL_TOOL_RESULT_DELIMITER}\n{result_details}"
                        )
            elif state.sql_error:
                details = self._build_sql_error_tool_details(output, tool_args)
            else:
                result_text = self._format_sql_result_for_display(output)
                details = truncate_for_context(result_text, max_len=1000)
        else:
            details = truncate_for_context(str(output or ""), max_len=1000)
        if tool_name == "execute_sql_query" and self._is_schema_gate_block(output):
            details = f"{details}\n\n[系统检测] 已拦截：未先获取数据集定义，SQL 未执行。"
        if tool_name == "execute_sql_query" and self._is_sql_repeat_gate_block(output):
            details = f"{details}\n\n[系统检测] 已有成功非空查数结果，已拦截重复 SQL 执行。"
        if tool_name == "execute_sql_query" and state.empty_sql_reason:
            details = f"{details}\n\n[系统检测] {state.empty_sql_reason}"
        if tool_name == "execute_sql_query" and state.sql_error_message:
            details = f"{details}\n\n[系统检测] SQL 执行异常: {state.sql_error_message[:500]}"
        if tool_name == "get_dataset_schema" and state.schema_miss:
            details = f"{details}\n\n[系统检测] 未命中相关数据集定义。"
        if tool_name == "get_dataset_schema" and state.no_authorized_schema:
            details = f"{details}\n\n[系统检测] 当前用户没有可用授权数据集，已终止查数流程。"
        if tool_name == "get_dataset_schema" and state.schema_service_unavailable:
            details = f"{details}\n\n[系统检测] 元数据服务不可用，已终止查数流程。"
        if tool_name == "get_dataset_schema" and state.rag_not_synced:
            details = f"{details}\n\n[系统检测] 元数据未同步到知识库，已终止查数流程。"
        return details

    @staticmethod
    def _is_schema_service_unavailable(tool_output: Any) -> bool:
        text = str(tool_output or "")
        if "[元数据服务不可用]" in text:
            return True
        normalized = text.lstrip()
        return normalized.startswith("[Tool Error]") or normalized.startswith("[TOOL_ERROR]")

    def _apply_schema_tool_result(self, state: _DataRunState, output: Any) -> None:
        state.schema_service_unavailable = self._is_schema_service_unavailable(output)
        state.no_authorized_schema = self._is_no_authorized_schema(output)
        state.rag_not_synced = self._is_rag_not_synced(output)
        state.schema_miss = self._is_no_relevant_schema(output)
        if self._is_schema_fatal(state) or not str(output or "").strip():
            state.schema_completed = False
            state.sql_before_schema = False
            return
        state.schema_completed = True
        state.sql_before_schema = False

    @staticmethod
    def _is_rag_not_synced(tool_output: Any) -> bool:
        text = str(tool_output or "")
        return "none are synced to RAG knowledge base" in text

    def _try_parse_json_output(self, tool_output: Any) -> Any:
        if isinstance(tool_output, (list, dict)):
            return tool_output
        if not isinstance(tool_output, str):
            return tool_output
        text = tool_output.strip()
        if not text or text[0] not in "[{":
            return tool_output
        try:
            return json.loads(text)
        except Exception:
            try:
                return ast.literal_eval(text)
            except Exception:
                return tool_output

    def _extract_result_row_lists(self, parsed: Any, depth: int = 0) -> list[list[Any]]:
        if depth > 4:
            return []
        if isinstance(parsed, list):
            return [parsed]
        if not isinstance(parsed, dict):
            return []
        row_lists: list[list[Any]] = []
        for key, value in parsed.items():
            if str(key) not in {"items", "rows", "data", "list", "result", "records"}:
                continue
            if isinstance(value, list):
                row_lists.append(value)
            elif isinstance(value, dict):
                row_lists.extend(self._extract_result_row_lists(value, depth + 1))
        return row_lists

    def _detect_empty_result(self, parsed: Any) -> str | None:
        row_lists = self._extract_result_row_lists(parsed)
        if row_lists and not any(len(rows) > 0 for rows in row_lists):
            return "SQL 返回的行容器为空，未命中任何数据行"
        if isinstance(parsed, dict):
            total_like_keys = ("total", "count", "total_count")
            if any(parsed.get(k) == 0 for k in total_like_keys) and row_lists:
                return "SQL 返回 total/count=0，且未命中任何数据行"
        return None

    def _is_no_authorized_schema(self, tool_output: Any) -> bool:
        text = str(tool_output or "")
        return "No authorized datasets found" in text or "未找到相关的授权数据集" in text

    def _is_no_relevant_schema(self, tool_output: Any) -> bool:
        text = str(tool_output or "")
        return (
            "No relevant schema info found" in text
            or "未找到相关数据集定义" in text
            or "未找到相关的元数据" in text
        )

    def _is_structured_sql_result(self, parsed: Any) -> bool:
        """可解析为 columns/items/rows 等形态时视为正常查数结果，不做关键词误判。"""
        if isinstance(parsed, list):
            return True
        if not isinstance(parsed, dict):
            return False
        if any(key in parsed for key in ("columns", "items", "rows", "data", "records")):
            return True
        return bool(self._extract_result_row_lists(parsed))

    def _detect_sql_error(self, output: Any) -> tuple[bool, str]:
        text = str(output or "")
        if not text.strip():
            return False, ""

        error_prefixes = (
            "[TOOL_ERROR]",
            "[Validation Failed]",
            "[Permission Denied]",
            "[Security Error]",
            f"{SCHEMA_GATE_PREFIX}",
            "Error: Dataset",
        )
        if any(text.startswith(prefix) for prefix in error_prefixes):
            return True, text[:1000]

        parsed = self._try_parse_json_output(output)
        if self._is_structured_sql_result(parsed):
            return False, ""

        error_patterns = [
            r"unknown column",
            r"unknown table",
            r"syntax error",
            r"sql syntax",
            r"access denied",
            r"permission denied",
            r"unauthorized",
            r"^报错[:：]",
            r"^错误[:：]",
            r"^异常[:：]",
            r"^失败[:：]",
            r"SQL Syntax Error",
            r"SQL Validation Failed",
        ]
        if any(re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE) for pattern in error_patterns):
            return True, text[:1000]
        return False, ""

    async def _resolve_pending_runtime(
        self,
        pending: Any,
    ) -> tuple[Any, list[RuntimeToolSpec], Any, _DataRunState, Dict[str, Any]]:
        if pending.agent is not None and pending.tools and pending.native_model is not None:
            data_state, stream_meta = self._pending_state_to_data_run_state(pending.state or {})
            guarded_tools = self._wrap_tools_with_schema_gate(pending.tools, data_state)
            return pending.agent, guarded_tools, pending.native_model, data_state, stream_meta

        ctx = pending.snapshot.runner_context
        tools = await self._resolve_runtime_tools_from_config()
        native_model_handle = await AgentConfigProvider.get_configured_llm(
            streaming=True,
            config=self.config,
        )
        native_model = getattr(native_model_handle, "native_model", None)
        if native_model is None:
            raise RuntimeError("当前模型适配器未提供 AgentScope native_model，无法恢复挂起执行。")

        from agentscope.state import AgentState

        restored_state = AgentState.model_validate(pending.snapshot.agent_state)
        data_state, stream_meta = self._pending_state_to_data_run_state(
            pending.state or dict(pending.snapshot.stream_state or {})
        )
        guarded_tools = self._wrap_tools_with_schema_gate(tools, data_state)
        agent = await self._build_native_agent(
            native_model=native_model,
            tools=guarded_tools,
            system_content=str(ctx.get("system_content", "")),
            max_steps=int(ctx.get("max_steps", 5)),
            restored_state=restored_state,
            primary_model_name=str(getattr(native_model, "model", self.config.model_name) or ""),
        )
        return agent, guarded_tools, native_model, data_state, stream_meta

    async def _resume_agentscope_native_stream(
        self,
        *,
        pending: Any,
        resume_event: Any,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        agent_name = self._runtime_agent_name()
        try:
            async with agentscope_session_lock.hold(
                user_id=self._runtime_user_id(),
                conversation_id=self.conversation_id,
                agent_name=agent_name,
            ):
                agent, tools, native_model, data_state, stream_meta = await self._resolve_pending_runtime(pending)
                interrupted = False
                async for chunk in self._stream_agentscope_events(
                    event_stream=agent.reply_stream(resume_event),
                    agent=agent,
                    tools=tools,
                    native_model=native_model,
                    state=data_state,
                    stream_meta=stream_meta,
                    emit_final_guard=True,
                ):
                    if is_interrupt_sse_chunk(chunk):
                        interrupted = True
                    yield chunk

                if not interrupted and self.conversation_id:
                    tools_fingerprint = build_tools_fingerprint(self.config, tools)
                    await agent_state_store.save(
                        user_id=self._runtime_user_id(),
                        conversation_id=self.conversation_id,
                        agent_name=agent_name,
                        agent_version=self.config.agent_version,
                        tools_fingerprint=tools_fingerprint,
                        model_name=str(getattr(native_model, "model", self.config.model_name) or ""),
                        state=agent.state,
                    )
        except SessionLockTimeout:
            yield {
                "type": "error",
                "status": "error",
                "content": "当前会话正在处理中，请稍后再试。",
            }

    async def resume_agentscope_native_confirmation(
        self,
        pending: Any,
        *,
        confirmed: bool,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from agentscope.event import ConfirmResult, UserConfirmResultEvent

        event = UserConfirmResultEvent(
            reply_id=pending.reply_id,
            confirm_results=[
                ConfirmResult(
                    confirmed=confirmed,
                    tool_call=pending.tool_call,
                )
            ],
        )
        async for chunk in self._resume_agentscope_native_stream(
            pending=pending,
            resume_event=event,
        ):
            yield chunk

    async def resume_agentscope_external_execution(
        self,
        pending: Any,
        *,
        execution_results: List[Any],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from agentscope.event import ExternalExecutionResultEvent

        event = ExternalExecutionResultEvent(
            reply_id=pending.reply_id,
            execution_results=execution_results,
        )
        async for chunk in self._resume_agentscope_native_stream(
            pending=pending,
            resume_event=event,
        ):
            yield chunk
