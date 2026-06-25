import time
import uuid
import json
import inspect
import logging
import asyncio
from typing import List, Dict, Any, AsyncGenerator, Optional
from datetime import datetime

from app.services.ai.runtime.agentscope.compat import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from app.schemas.agent import ChatConfig, AgentExecutionStep
from app.core.orm import AsyncSessionLocal
from app.services.ai.tools.registry import ToolRegistry
from app.services.ai.config import AgentConfigProvider
from app.services.ai.agent_manager import AgentManagerService
from app.services.ai.executors.base import BaseExecutor
from app.services.ai.intent_service import (
    IntentType,
    looks_like_strong_business_data_request,
    looks_like_web_search_query,
)
from app.services.ai.skill_resolver import is_main_general_agent
from app.services.ai.turn_classifier import TurnType
from app.services.ai.executors.common import (
    convert_history_to_messages,
    extract_tokens_from_message,
    normalize_messages_for_llm,
    MODEL_STREAM_MAX_RETRIES,
    build_stream_retry_log,
    build_stream_error_log,
    is_retryable_stream_error,
)
from app.services.ai.executors.prompts import AssistantPrompts
from app.services.ai.runtime.agentscope.agent_runtime import (
    build_model_config,
    build_tools_fingerprint,
    load_context_config,
)
from app.services.ai.runtime.agentscope.chat import compat_to_runtime_messages, to_agentscope_messages
from app.services.ai.runtime.agentscope.state_store import agent_state_store
from app.services.ai.runtime.agentscope.event_stream import (
    extract_latest_assistant_text,
    is_interrupt_sse_chunk,
    map_standard_agentscope_event,
    new_native_stream_state,
)
from app.services.ai.runtime.agentscope.text_sanitize import sanitize_assistant_stream_text
from app.services.ai.runtime.agentscope.stream_reconcile import (
    GENERIC_SYNTHESIS_EMPTY_FALLBACK,
    compute_stream_reconcile_gap,
    needs_tool_synthesis_fallback,
    truncate_for_context,
)
from app.services.ai.runtime.agentscope.session_lock import (
    SessionLockTimeout,
    agentscope_session_lock,
)
from app.services.ai.runtime.agentscope.workspace import get_local_workspace
from app.services.ai.runtime.agentscope.errors import ToolLoopFuseError
from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec, runtime_tool_spec_from_legacy_tool
from app.services.ai.runtime.agentscope.tools import build_toolkit
from app.services.ai.runtime.tool_loop_detector import ToolLoopDetector

logger = logging.getLogger(__name__)


DATA_QUERY_SWITCH_CAPABILITY = "data_query"


class _ForcedFirstToolChoiceModel:
    """仅在首个模型调用上注入 tool_choice，强制本轮优先走工具；其后恢复模型原生行为。"""

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


class AssistantAgentRunner(BaseExecutor):
    def __init__(
        self,
        config: ChatConfig,
        trace_id: str,
        trace_buffer: List[AgentExecutionStep],
        debug_options: Dict[str, Any] = None,
        user_info: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[str] = None,
        permission_options: Dict[str, Any] = None,
        route_hints: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(config, trace_id, trace_buffer, debug_options, user_info, conversation_id, permission_options)
        self.intent_info = None
        self.intent_elapsed_ms = 0.0
        self.turn_classification = None
        self.route_hints = route_hints or {}

    def _runtime_user_id(self) -> str | None:
        if not self.user_info:
            return None
        return str(self.user_info.get("user_id") or self.user_info.get("id") or "") or None

    def _runtime_agent_name(self) -> str:
        return self.config.agent_name or "AssistantAgent"

    def _runner_context(self, *, system_content: str, max_steps: int) -> Dict[str, Any]:
        return {
            "runner_type": "assistant",
            "config": self.config.model_dump(),
            "debug_options": self.debug_options,
            "permission_options": self.permission_options,
            "system_content": system_content,
            "max_steps": max_steps,
            "route_hints": self.route_hints,
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
    ) -> "AssistantAgentRunner":
        config = ChatConfig(**runner_context["config"])
        runner = cls(
            config=config,
            trace_id=trace_id,
            trace_buffer=trace_buffer or [],
            debug_options=runner_context.get("debug_options"),
            permission_options=runner_context.get("permission_options"),
            user_info=user_info,
            conversation_id=conversation_id,
            route_hints=runner_context.get("route_hints"),
        )
        return runner

    @staticmethod
    def _extract_last_user_query(history: List[Dict[str, str]]) -> str:
        for msg in reversed(history or []):
            if msg.get("role") == "user":
                return str(msg.get("content") or "")
        return ""

    def _is_direct_agent_selection(self) -> bool:
        """专家模式 / @提及 / 显式 agent_id 直达，不走自动路由。"""
        return bool(self.route_hints.get("direct_agent_selection"))

    def _should_run_data_hallucination_guard(self, user_query: str) -> bool:
        """仅主助手 + 自动路由 + 明确查数诉求时启用，防止无 DB 连接时编造业务数据。"""
        if not is_main_general_agent(self.config):
            return False
        if self._is_direct_agent_selection():
            return False
        if looks_like_web_search_query(user_query):
            return False

        if self.turn_classification is not None:
            if self.turn_classification.turn_type == TurnType.DATA_QUERY_REQUEST:
                return True
            if self.turn_classification.intent == IntentType.DATA_QUERY:
                return True
            if (
                self.turn_classification.turn_type == TurnType.GENERAL
                and self.turn_classification.intent == IntentType.GENERAL
            ):
                return False
        if self.intent_info is not None and self.intent_info.intent == IntentType.DATA_QUERY:
            return True
        return looks_like_strong_business_data_request(user_query)

    @staticmethod
    def _chunk_indicates_tool_attempt(chunk: Dict[str, Any]) -> bool:
        """本轮是否已发起/待确认工具调用（含 permission 待确认，不含 memory_search）。"""
        if chunk.get("type") == "log" and chunk.get("category") == "tool":
            tool_name = str(chunk.get("title", "") or "")
            return "memory_search" not in tool_name
        chunk_type = str(chunk.get("type") or "")
        if chunk_type in {"permission_required", "external_execution_required"}:
            return True
        tool_call = chunk.get("tool_call")
        if isinstance(tool_call, dict) and tool_call.get("name"):
            return str(tool_call.get("name")) != "memory_search"
        return False

    def _is_hallucinated_data_reply(self, text: str) -> bool:
        text_clean = text.strip()
        if not text_clean:
            return False
        import re
        # 1. 假装已自动转派 ChatBI 或正在检索内部数据集
        fake_data_process_patterns = (
            r"自动.{0,12}(衔接|接入|调用|转交).{0,12}(ChatBI|数据智能助手|数据分析专家)",
            r"我将.{0,12}(衔接|接入|调用|转交).{0,12}(ChatBI|数据智能助手|数据分析专家)",
            r"(正在|开始|为您).{0,12}(检索|查询|查找).{0,12}(数据集|业务数据|指标)",
            r"数据查询未成功",
        )
        if any(re.search(pattern, text_clean, re.IGNORECASE) for pattern in fake_data_process_patterns):
            return True
        # 2. 表格 + 内网 IP：典型编造资产/主机清单（联网搜索摘要表格通常无内网 IP）
        has_markdown_table = "|" in text_clean and "---" in text_clean
        ip_pattern = r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"
        if has_markdown_table and re.search(ip_pattern, text_clean):
            return True
        # 3. 表格 + 内部业务对象字段：区分于联网资料整理类表格
        internal_table_signals = (
            "主机名", "资产", "工单", "告警", "设备清单", "机房", "机柜",
            "数据集", "字段名", "表结构", "sql",
        )
        if has_markdown_table and any(sig in text_clean for sig in internal_table_signals):
            return True
        return False

    @staticmethod
    def _agent_field(agent: Any, field: str, default: Any = None) -> Any:
        if isinstance(agent, dict):
            return agent.get(field, default)
        return getattr(agent, field, default)

    @classmethod
    def _agent_has_capability(cls, agent: Any, capability: str) -> bool:
        capabilities = cls._agent_field(agent, "capabilities", []) or []
        return capability in capabilities

    async def _resolve_switch_agent_for_capability(self, capability: str) -> Optional[Dict[str, str]]:
        """Find a user-allowed agent that can satisfy the missing capability."""
        if not self.user_info:
            return None
        try:
            async with AsyncSessionLocal() as session:
                agents = await AgentManagerService.list_allowed_agents(session, user=self.user_info)
        except Exception as exc:
            logger.warning(
                "[AssistantAgentRunner] Failed to resolve switch target for capability=%s: %s",
                capability,
                exc,
            )
            return None

        for agent in agents or []:
            if not self._agent_has_capability(agent, capability):
                continue
            agent_id = self._agent_field(agent, "id")
            if not agent_id:
                continue
            display_name = (
                self._agent_field(agent, "display_name")
                or self._agent_field(agent, "name")
                or str(agent_id)
            )
            return {"id": str(agent_id), "display_name": str(display_name)}
        return None

    async def _build_data_guard_refusal_content(self) -> str:
        switch_agent = await self._resolve_switch_agent_for_capability(DATA_QUERY_SWITCH_CAPABILITY)
        base = (
            "⚠️ 抱歉，检测到您正在查询系统内部数据或资产列表。通用助手未连接数据库，"
            "为保证数据真实性已拦截该回答。"
        )
        if not switch_agent:
            return f"{base}请切换到具备数据查询能力的智能体后继续查询。"

        display_name = switch_agent["display_name"]
        agent_id = switch_agent["id"]
        return (
            f"{base}请切换到 **{display_name}** 后继续查询。\n\n"
            f"- [⚡ 切换到 {display_name}](quick:/switch_agent_expert?agent_id={agent_id})"
        )

    async def execute(
        self,
        history: List[Dict[str, str]],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from app.services.ai.runtime.agentscope.trace_context import TraceSpanContext
        async with TraceSpanContext(
            trace_buffer=self.trace_buffer,
            event_type="agent_execution",
            span_name="AssistantAgentRunner",
        ):
            async for chunk in self._execute_raw(history):
                yield chunk

    async def _execute_raw(
        self,
        history: List[Dict[str, str]]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        user_query = self._extract_last_user_query(history)
        run_data_guard = self._should_run_data_hallucination_guard(user_query)

        chunks_buffer = []
        full_text = ""
        has_attempted_tool = False

        from app.core.context import set_agent_context
        import asyncio
        ctx = self._ensure_agent_context()
        event_queue = asyncio.Queue()
        ctx.event_queue = event_queue

        async def merge_streams(core_stream):
            out_queue = asyncio.Queue()

            async def read_core():
                if ctx:
                    set_agent_context(ctx)
                try:
                    async for chunk in core_stream:
                        await out_queue.put(("core", chunk))
                except Exception as e:
                    await out_queue.put(("error", e))
                finally:
                    await out_queue.put(("core_done", None))

            async def read_queue():
                try:
                    while True:
                        item = await event_queue.get()
                        if item == "DONE":
                            break
                        await out_queue.put(("queue", item))
                        event_queue.task_done()
                except asyncio.CancelledError:
                    pass

            t1 = asyncio.create_task(read_core())
            t2 = asyncio.create_task(read_queue())

            core_done = False
            try:
                while not core_done or not out_queue.empty():
                    try:
                        tag, val = await asyncio.wait_for(out_queue.get(), timeout=0.05)
                        if tag == "core":
                            yield val
                        elif tag == "queue":
                            yield val
                        elif tag == "core_done":
                            core_done = True
                        elif tag == "error":
                            raise val
                        out_queue.task_done()
                    except asyncio.TimeoutError:
                        if core_done and out_queue.empty():
                            break
            finally:
                t1.cancel()
                t2.cancel()
                try:
                    await event_queue.put("DONE")
                except Exception:
                    pass

        async for chunk in merge_streams(self._execute_core(history)):
            if self._chunk_indicates_tool_attempt(chunk):
                has_attempted_tool = True

            if "content" in chunk:
                full_text += chunk["content"]
                chunks_buffer.append(chunk)
            else:
                yield chunk

        should_intercept = (
            run_data_guard
            and not has_attempted_tool
            and self._is_hallucinated_data_reply(full_text)
        )

        if should_intercept:
            logger.warning(
                f"[AssistantAgentRunner] Hallucination detected! "
                f"run_data_guard={run_data_guard}, has_attempted_tool={has_attempted_tool}. "
                f"looks_hallucinated={self._is_hallucinated_data_reply(full_text)}. "
                f"Generated: {full_text[:200]}..."
            )
            yield {
                "type": "log",
                "id": f"data_general_guard_{uuid.uuid4().hex[:8]}",
                "title": "引导切换数据智能体",
                "details": "检测到您正在查询系统内部数据，当前助手未连接数据库，已生成可执行的智能体切换建议。",
                "status": "warning",
            }
            refusal_content = await self._build_data_guard_refusal_content()
            yield {"content": refusal_content}
        else:
            for chunk in chunks_buffer:
                yield chunk

    async def _execute_core(
        self,
        history: List[Dict[str, str]]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from app.services.ai.multimodal_support import (
            ensure_multimodal_compatible,
            resolve_runtime_model_name,
        )

        model_name = resolve_runtime_model_name(self.config, prefer_synthesis=True)
        incompatible_msg = await ensure_multimodal_compatible(history, model_name)
        if incompatible_msg:
            yield {"content": incompatible_msg, "status": "error"}
            return

        # 1. Prepare LLM
        tools = await self._resolve_runtime_tools_from_config()

        # 2. Build Messages
        system_content = self.config.system_prompt or ""
        route_hint = AssistantPrompts.route_hints(self.route_hints)
        if route_hint:
            system_content = f"{route_hint}\n\n{system_content}"

        # 3. Execution Mode Selection
        if not tools:
            # --- Simple Mode (No Tools) ---
            # 仅保留最近 10 轮原始历史（20 条消息），防止长对话 Token 无限累积
            pruned_history = history[-20:] if history else history
            runtime_messages = [SystemMessage(content=system_content)]
            runtime_messages.extend(convert_history_to_messages(pruned_history, strip_thought=True))
            runtime_messages = normalize_messages_for_llm(runtime_messages)

            start_synthesis = time.time()
            yield {"type": "log", "id": f"syn_s_{uuid.uuid4().hex[:8]}", "title": "📝 准备回答", "details": "正在生成回答...", "status": "success"}
            
            # Use Synthesizer for simple mode
            llm = await AgentConfigProvider.get_synthesis_llm(streaming=True, config=self.config)
            
            full_content = ""
            content_emitted = False
            accumulated_msg = None
            stream_succeeded = False
            for stream_attempt in range(MODEL_STREAM_MAX_RETRIES):
                accumulated_msg = None
                try:
                    async for chunk in llm.astream(normalize_messages_for_llm(runtime_messages)):
                        if accumulated_msg is None:
                            accumulated_msg = chunk
                        else:
                            accumulated_msg += chunk
                        if chunk.content:
                            if not content_emitted:
                                yield {"type": "log", "id": f"gen_s_{uuid.uuid4().hex[:8]}", "title": "✨ 开始生成回复", "status": "success"}
                            content_emitted = True
                            full_content += chunk.content
                            yield {"content": chunk.content}
                    stream_succeeded = True
                    break
                except Exception as stream_err:
                    logger.error(
                        f"[AssistantAgentRunner] Simple mode stream failed "
                        f"(attempt {stream_attempt + 1}/{MODEL_STREAM_MAX_RETRIES}): {stream_err}"
                    )
                    if (
                        stream_attempt < MODEL_STREAM_MAX_RETRIES - 1
                        and not content_emitted
                        and is_retryable_stream_error(stream_err)
                    ):
                        yield build_stream_retry_log(stream_err, stream_attempt)
                        await asyncio.sleep(2 ** stream_attempt)
                        continue
                    yield build_stream_error_log(stream_err)
                    return
            if not stream_succeeded:
                return
            
            tokens = extract_tokens_from_message(accumulated_msg)
            
            # Record final answer as a trace step
            self._increment_step()
            self.trace_buffer.append(AgentExecutionStep(
                step_number=self.step_counter,
                event_type="synthesis",
                agent_name=self.config.agent_name,
                model=self.config.model_name,
                temperature=self.config.temperature,
                tool_output={"content": full_content},
                raw_log=full_content,
                execution_time_ms=(time.time() - start_synthesis) * 1000,
                prompt_tokens=tokens["prompt_tokens"],
                completion_tokens=tokens["completion_tokens"],
                total_tokens=tokens["total_tokens"],
                timestamp=datetime.fromtimestamp(start_synthesis)
            ))
            return

        from app.services.ai.runtime.agentscope.workspace import (
            append_session_workspace_sandbox_to_system_prompt,
        )

        system_content = await append_session_workspace_sandbox_to_system_prompt(
            system_content,
            user_id=self._runtime_user_id(),
            conversation_id=self.conversation_id,
            tools=tools,
        )
        pruned_history = history[-20:] if history else history
        runtime_messages = [SystemMessage(content=system_content)]
        runtime_messages.extend(convert_history_to_messages(pruned_history, strip_thought=True))
        runtime_messages = normalize_messages_for_llm(runtime_messages)

        # --- ReAct Mode (With Tools) ---
        from app.services.config_service import ConfigService
        max_steps_str = await ConfigService.get("agent_max_iterations")
        MAX_STEPS = int(max_steps_str) if max_steps_str else 5

        from app.services.ai.memory_recall_policy import (
            MEMORY_SEARCH_CORRECTION_MSG,
            last_user_message_text,
            looks_like_cross_session_recall_query,
            tools_include_memory_search,
        )
        from app.services.memory_config_service import MemoryConfigService

        memory_search_available = tools_include_memory_search(tools)
        recall_query_pending = False
        if memory_search_available:
            try:
                enabled = await MemoryConfigService.get_bool("memory_service_enabled", True)
                recall_query_pending = enabled and looks_like_cross_session_recall_query(
                    last_user_message_text(history)
                )
            except Exception:
                recall_query_pending = looks_like_cross_session_recall_query(
                    last_user_message_text(history)
                )

        native_system_content = system_content
        if recall_query_pending:
            native_system_content = (
                f"{MEMORY_SEARCH_CORRECTION_MSG}\n\n"
                f"{native_system_content}"
            )

        # 工具预检（Tool Preflight）：由本轮已绑定工具的 name+description 与问题相关度驱动，
        # 识别该不该用工具、用哪个，并按配置力度促发模型调用（记忆类有专门便签，故跳过）。
        # 模式 agent_tool_preflight_mode：off=关闭；soft=仅注入强约束便签；hard=便签+首步强制调用。
        preflight_tool_choice = None
        try:
            if not recall_query_pending:
                preflight_mode = str(
                    await ConfigService.get("agent_tool_preflight_mode", "soft") or "soft"
                ).strip().lower()
                if preflight_mode not in {"off", "false", "0", "none"}:
                    from app.services.ai.tool_nudge_policy import resolve_tool_nudge

                    tool_nudge = resolve_tool_nudge(
                        self._extract_last_user_query(history),
                        tools,
                    )
                    if tool_nudge is not None:
                        native_system_content = f"{tool_nudge.message}\n\n{native_system_content}"
                        force_applied = False
                        if preflight_mode == "hard":
                            preflight_tool_choice = self._build_preflight_tool_choice(
                                tool_nudge.recommended_force_mode()
                            )
                            force_applied = preflight_tool_choice is not None
                        logger.info(
                            "[ToolPreflight] mode=%s tool=%s score=%s forced=%s",
                            preflight_mode,
                            tool_nudge.tool_name,
                            tool_nudge.score,
                            force_applied,
                        )
                        yield {
                            "type": "log",
                            "id": f"tool_preflight_{uuid.uuid4().hex[:8]}",
                            "title": "工具预检：建议调用工具" if not force_applied else "工具预检：本轮优先调用工具",
                            "details": (
                                f"本轮问题与已绑定工具「{tool_nudge.tool_name}」相关"
                                f"（相关度 {tool_nudge.score}），"
                                + ("已要求模型先调用工具再作答。" if force_applied else "已提示模型优先调用该工具。")
                            ),
                            "status": "success",
                            "category": "tool_preflight",
                        }
        except Exception as preflight_err:
            logger.warning("[ToolPreflight] Failed to resolve tool preflight: %s", preflight_err)

        if (
            tools
            and all(isinstance(t, RuntimeToolSpec) for t in tools)
        ):
            native_model_handle = await AgentConfigProvider.get_configured_llm(streaming=True, config=self.config)
            native_model = getattr(native_model_handle, "native_model", None)
            if native_model is not None:
                async for chunk in self._execute_with_agentscope_native_agent(
                    native_model=native_model,
                    tools=tools,
                    system_content=native_system_content,
                    runtime_messages=runtime_messages,
                    max_steps=MAX_STEPS,
                    initial_tool_choice=preflight_tool_choice,
                ):
                    yield chunk
                return
            yield {
                "type": "error",
                "status": "error",
                "content": "当前模型适配器未提供 AgentScope native_model，无法执行 AgentScope 原生工具链。",
            }
            return

        yield {
            "type": "error",
            "status": "error",
            "content": "Assistant 工具链必须使用 AgentScope RuntimeToolSpec；旧 ReAct fallback 已移除。",
        }

    @staticmethod
    def _build_preflight_tool_choice(force_mode: str) -> Any:
        """根据预检建议构造 AgentScope ToolChoice；模型不支持时返回 None 以回退软促发。"""
        if not force_mode:
            return None
        try:
            from agentscope.tool import ToolChoice

            return ToolChoice(mode=force_mode)
        except Exception as exc:
            logger.warning("[ToolPreflight] Build ToolChoice failed (fallback to soft): %s", exc)
            return None

    @staticmethod
    async def _create_tool_loop_detector() -> ToolLoopDetector:
        from app.services.config_service import ConfigService
        from app.services.ai.runtime.tool_loop_detector import (
            DEFAULT_GLOBAL_LIMIT,
            DEFAULT_PING_PONG_THRESHOLD,
        )

        enabled_raw = await ConfigService.get("agent_tool_loop_detection_enabled", "true")
        threshold_raw = await ConfigService.get("agent_tool_loop_fuse_threshold", "3")
        ping_pong_raw = await ConfigService.get(
            "agent_tool_loop_ping_pong_threshold", str(DEFAULT_PING_PONG_THRESHOLD)
        )
        global_limit_raw = await ConfigService.get(
            "agent_tool_loop_global_limit", str(DEFAULT_GLOBAL_LIMIT)
        )
        enabled = str(enabled_raw or "").strip().lower() in {"1", "true", "yes", "on"}
        try:
            threshold = max(1, int(threshold_raw))
        except (TypeError, ValueError):
            threshold = 3
        try:
            ping_pong_threshold = max(0, int(ping_pong_raw))
        except (TypeError, ValueError):
            ping_pong_threshold = DEFAULT_PING_PONG_THRESHOLD
        try:
            global_limit = max(0, int(global_limit_raw))
        except (TypeError, ValueError):
            global_limit = DEFAULT_GLOBAL_LIMIT
        return ToolLoopDetector(
            threshold=threshold,
            enabled=enabled,
            ping_pong_threshold=ping_pong_threshold,
            global_limit=global_limit,
        )

    async def _execute_with_agentscope_native_agent(
        self,
        *,
        native_model: Any,
        tools: List[RuntimeToolSpec],
        system_content: str,
        runtime_messages: List[BaseMessage],
        max_steps: int,
        initial_tool_choice: Any = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        agent_name = self._runtime_agent_name()
        tools_fingerprint = build_tools_fingerprint(self.config, tools)
        model_name = getattr(native_model, "model", self.config.model_name)
        loop_detector = await self._create_tool_loop_detector()
        try:
            async with agentscope_session_lock.hold(
                user_id=self._runtime_user_id(),
                conversation_id=self.conversation_id,
                agent_name=agent_name,
                ttl_seconds=300,
            ):
                persisted = await agent_state_store.load(
                    self._runtime_user_id(),
                    self.conversation_id,
                    agent_name,
                )
                restored_state = None
                if persisted:
                    if persisted.matches(
                        tools_fingerprint=tools_fingerprint,
                        agent_name=agent_name,
                    ):
                        try:
                            from agentscope.state import AgentState

                            restored_state = AgentState.model_validate(persisted.state)
                        except Exception as exc:
                            logger.warning("[AssistantAgentRunner] Failed to restore AgentState: %s", exc)
                    else:
                        logger.warning(
                            "[AssistantAgentRunner] Tools fingerprint mismatch for agent=%s (stored=%s, current=%s). "
                            "Resetting conversation state to prevent tool call conflicts.",
                            agent_name, persisted.tools_fingerprint, tools_fingerprint
                        )
                        yield {
                            "type": "log",
                            "id": f"state_reset_{uuid.uuid4().hex[:8]}",
                            "title": "智能体配置变更：历史会话状态已重置",
                            "details": "检测到绑定的工具集或模型配置发生改变，为防工具调用崩溃，已重置运行时状态。",
                            "status": "warning",
                        }

                agent = await self._build_native_agent(
                    native_model=native_model,
                    tools=tools,
                    system_content=system_content,
                    max_steps=max_steps,
                    restored_state=restored_state,
                    primary_model_name=str(model_name or ""),
                    loop_detector=loop_detector,
                )
                # hard 预检：仅在「首步模型调用」注入 tool_choice，强制本轮先走工具，
                # 之后的 ReAct 步骤恢复模型自主决策，避免死循环或答非所问。
                if initial_tool_choice is not None and getattr(agent, "model", None) is not None:
                    agent.model = _ForcedFirstToolChoiceModel(agent.model, initial_tool_choice)
                if restored_state and restored_state.context:
                    latest_user_messages = self._latest_user_runtime_messages(runtime_messages)
                    inputs = to_agentscope_messages(compat_to_runtime_messages(latest_user_messages))
                else:
                    inputs = to_agentscope_messages(compat_to_runtime_messages(runtime_messages[1:]))

                state = new_native_stream_state(
                    system_content=system_content,
                    max_steps=max_steps,
                )
                state["user_query"] = next(
                    (
                        str(getattr(message, "content", ""))
                        for message in reversed(runtime_messages)
                        if isinstance(message, HumanMessage)
                    ),
                    "",
                )
                interrupted = False
                async for chunk in self._stream_agentscope_native_events(
                    event_stream=agent.reply_stream(inputs),
                    agent=agent,
                    tools=tools,
                    native_model=native_model,
                    state=state,
                ):
                    if is_interrupt_sse_chunk(chunk):
                        interrupted = True
                    yield chunk

                if not interrupted and self.conversation_id:
                    await agent_state_store.save(
                        user_id=self._runtime_user_id(),
                        conversation_id=self.conversation_id,
                        agent_name=agent_name,
                        agent_version=self.config.agent_version,
                        tools_fingerprint=tools_fingerprint,
                        model_name=str(model_name) if model_name else None,
                        state=agent.state,
                    )
        except SessionLockTimeout:
            yield {
                "type": "error",
                "status": "error",
                "content": "当前会话正在处理中，请稍后再试。",
            }
        except ToolLoopFuseError as fuse_err:
            yield {
                "type": "error",
                "status": "error",
                "content": f"检测到工具重复调用循环，已停止继续执行。{fuse_err}",
            }

    async def _build_native_agent(
        self,
        *,
        native_model: Any,
        tools: List[RuntimeToolSpec],
        system_content: str,
        max_steps: int,
        restored_state: Any = None,
        primary_model_name: str,
        loop_detector: ToolLoopDetector | None = None,
    ) -> Any:
        from agentscope.agent import Agent, ReActConfig
        from app.services.ai.runtime.agentscope.middleware import ModelCallStatsMiddleware

        context_config = await load_context_config()
        model_config = await build_model_config(
            config=self.config,
            primary_model_name=primary_model_name,
        )
        workspace = await get_local_workspace(
            user_id=self._runtime_user_id(),
            conversation_id=self.conversation_id,
        )
        # 仅挂载 agent 后端配置的工具；workspace 只作 offloader，不自动注入 Grep/Read/Bash 等内置工具。
        toolkit = build_toolkit(
            tools,
            approval_mode=self.permission_options.get("approval_mode"),
            loop_detector=loop_detector,
        )
        middlewares = []
        if self.conversation_id:
            middlewares.append(
                ModelCallStatsMiddleware(
                    user_id=self._runtime_user_id(),
                    conversation_id=self.conversation_id,
                    agent_name=self._runtime_agent_name(),
                    trace_id=self.trace_id,
                )
            )
        return Agent(
            name=self._runtime_agent_name(),
            system_prompt=system_content,
            model=native_model,
            toolkit=toolkit,
            state=restored_state,
            offloader=workspace,
            model_config=model_config,
            context_config=context_config,
            react_config=ReActConfig(max_iters=max_steps),
            middlewares=middlewares,
        )

    @staticmethod
    def _latest_user_runtime_messages(
        runtime_messages: List[BaseMessage],
    ) -> List[BaseMessage]:
        for message in reversed(runtime_messages):
            if isinstance(message, HumanMessage):
                return [message]
        return []

    async def _stream_agentscope_native_events(
        self,
        *,
        event_stream: Any,
        agent: Any,
        tools: List[RuntimeToolSpec],
        native_model: Any,
        state: Dict[str, Any],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        tool_names: Dict[str, str] = state["tool_names"]
        tool_args_text: Dict[str, str] = state["tool_args_text"]
        tool_outputs: Dict[str, str] = state["tool_outputs"]
        tool_data: Dict[str, List[Dict[str, Any]]] = state.setdefault("tool_data", {})
        tool_started_at: Dict[str, float] = state["tool_started_at"]

        async def on_tool_result_end(event: Any) -> AsyncGenerator[Dict[str, Any], None]:
            tool_id = getattr(event, "tool_call_id", "")
            tool_name = tool_names.get(tool_id, "")
            raw_args = tool_args_text.get(tool_id, "") or "{}"
            try:
                tool_args = json.loads(raw_args)
            except Exception:
                tool_args = {"input": raw_args}
            output = tool_outputs.get(tool_id, "")
            if tool_data.get(tool_id):
                output = {
                    "text": output,
                    "data_blocks": tool_data.get(tool_id, []),
                }
            duration_ms = (time.time() - tool_started_at.get(tool_id, time.time())) * 1000
            result = self._build_tool_observation(
                tool_id=tool_id,
                tool_name=tool_name,
                tool_args=tool_args,
                tool_output=output,
                duration_tool=duration_ms,
                target_tool=next((t for t in tools if t.name == tool_name), None),
                tool_index=0,
            )
            if result.get("log"):
                yield result["log"]
            if result.get("citation"):
                yield result["citation"]
            if result.get("trace"):
                self.trace_buffer.append(result["trace"])

        async def on_text_block_delta(event: Any) -> AsyncGenerator[Dict[str, Any], None]:
            delta = sanitize_assistant_stream_text(str(getattr(event, "delta", "")))
            if not delta:
                return
            if state["used_tools"] and not state["synthesis_log_emitted"]:
                state["synthesis_log_emitted"] = True
                yield {
                    "type": "log",
                    "id": f"synthesis_native_{uuid.uuid4().hex[:8]}",
                    "title": "📝 汇总工具结果",
                    "details": "已获取所需数据，正在组织语言...",
                    "status": "success",
                }
                yield {"type": "thinking", "status": "continuing"}
            if not state["content_emitted"]:
                state["content_emitted"] = True
                yield {
                    "type": "log",
                    "id": f"gen_start_{uuid.uuid4().hex[:8]}",
                    "title": "✨ 开始生成回复",
                    "status": "success",
                }
            state["full_content"] += delta
            yield {"content": delta}

        async for event in event_stream:
            event_type = str(getattr(event, "type", ""))
            if event_type == "MODEL_CALL_END":
                self._record_agent_scope_model_call(
                    event,
                    state=state,
                    native_model=native_model,
                )
            async for chunk in map_standard_agentscope_event(
                event,
                state=state,
                on_tool_result_end=on_tool_result_end,
                on_text_block_delta=on_text_block_delta,
                agent=agent,
                runner=self,
                tools=tools,
                native_model=native_model,
                agent_name=self._runtime_agent_name(),
            ):
                yield chunk
                if is_interrupt_sse_chunk(chunk):
                    return

        async for chunk in self._reconcile_reply_after_stream(
            agent=agent,
            state=state,
            native_model=native_model,
        ):
            yield chunk

        if state["full_content"] and not state["synthesis_recorded"]:
            state["synthesis_recorded"] = True
            self._increment_step()
            self.trace_buffer.append(AgentExecutionStep(
                step_number=self.step_counter,
                event_type="synthesis",
                agent_name=self.config.agent_name,
                model=getattr(native_model, "model", self.config.model_name),
                temperature=self.config.synthesis_temperature or self.config.temperature,
                tool_output={"content": state["full_content"]},
                raw_log=state["full_content"],
                execution_time_ms=(time.time() - state["start_synthesis"]) * 1000,
                timestamp=datetime.fromtimestamp(state["start_synthesis"]),
            ))

    async def _emit_reply_text_chunks(
        self,
        state: Dict[str, Any],
        text: str,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        if state.get("used_tools") and not state.get("synthesis_log_emitted"):
            state["synthesis_log_emitted"] = True
            yield {
                "type": "log",
                "id": f"synthesis_native_{uuid.uuid4().hex[:8]}",
                "title": "📝 汇总工具结果",
                "details": "已获取所需数据，正在组织语言...",
                "status": "success",
            }
            yield {"type": "thinking", "status": "continuing"}
        if not state.get("content_emitted"):
            state["content_emitted"] = True
            yield {
                "type": "log",
                "id": f"gen_start_{uuid.uuid4().hex[:8]}",
                "title": "✨ 开始生成回复",
                "status": "success",
            }
        state["full_content"] = (state.get("full_content") or "") + text
        yield {"content": text}

    async def _reconcile_reply_after_stream(
        self,
        *,
        agent: Any,
        state: Dict[str, Any],
        native_model: Any,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """流结束后：AgentState 与已发送 SSE 对齐，不足则 synthesis。"""
        streamed = state.get("full_content") or ""
        agent_text = (
            extract_latest_assistant_text(agent, include_thinking=False)
            if agent is not None
            else ""
        )

        gap = compute_stream_reconcile_gap(streamed, agent_text)
        if gap.strip():
            logger.info(
                "[AssistantAgentRunner] Stream reconcile gap chars=%d streamed=%d agent=%d",
                len(gap),
                len(streamed),
                len(agent_text),
            )
            async for chunk in self._emit_reply_text_chunks(state, gap):
                yield chunk
            return

        if not needs_tool_synthesis_fallback(
            streamed,
            agent_text,
            used_tools=bool(state.get("used_tools")),
        ):
            if not streamed.strip() and not agent_text.strip():
                logger.warning(
                    "[AssistantAgentRunner] Reply ended without assistant text"
                )
            return

        append_sep = bool(streamed.strip())
        async for chunk in self._stream_general_synthesis_fallback(
            state=state,
            native_model=native_model,
            append_after_partial=append_sep,
        ):
            yield chunk

    def _build_synthesis_user_message(self, user_query: str, execution_review: str) -> str:
        return AssistantPrompts.synthesis_user_message(user_query, execution_review)

    async def _stream_general_synthesis_fallback(
        self,
        *,
        state: Dict[str, Any],
        native_model: Any,
        append_after_partial: bool = False,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        tool_names: Dict[str, str] = state.get("tool_names", {})
        tool_outputs: Dict[str, str] = state.get("tool_outputs", {})
        review_lines = [
            f"- {tool_names[tool_id]}: {truncate_for_context(tool_outputs.get(tool_id, ''))}"
            for tool_id in tool_names
        ]
        if not review_lines:
            logger.warning("[AssistantAgentRunner] Synthesis skipped: no tool review lines")
            state["full_content"] = (state.get("full_content") or "") + GENERIC_SYNTHESIS_EMPTY_FALLBACK
            yield {"content": GENERIC_SYNTHESIS_EMPTY_FALLBACK}
            return

        user_query = str(state.get("user_query") or "")
        execution_review = "【执行过程回顾】\n" + "\n".join(review_lines)
        logger.warning(
            "[AssistantAgentRunner] Synthesis fallback after stream reconcile tools=%d",
            len(review_lines),
        )

        if append_after_partial:
            yield {"content": "\n\n"}

        if not state.get("synthesis_fb_log_emitted"):
            state["synthesis_fb_log_emitted"] = True
            yield {
                "type": "log",
                "id": f"synthesis_fb_{uuid.uuid4().hex[:8]}",
                "title": "📝 汇总工具结果",
                "details": "正在基于工具结果生成最终回答...",
                "status": "success",
            }

        emitted_any = False
        last_synthesis_chunk = None
        try:
            llm = await AgentConfigProvider.get_synthesis_llm(streaming=True, config=self.config)
            messages = normalize_messages_for_llm([
                SystemMessage(content=str(state.get("system_content") or self.config.system_prompt or "")),
                HumanMessage(
                    content=self._build_synthesis_user_message(user_query, execution_review)
                ),
            ])
            async for chunk in llm.astream(messages):
                last_synthesis_chunk = chunk
                content = sanitize_assistant_stream_text(str(getattr(chunk, "content", None) or ""))
                if not content:
                    continue
                emitted_any = True
                if not state.get("content_emitted"):
                    state["content_emitted"] = True
                    yield {
                        "type": "log",
                        "id": f"gen_fb_{uuid.uuid4().hex[:8]}",
                        "title": "✨ 开始生成回复",
                        "status": "success",
                    }
                state["full_content"] = (state.get("full_content") or "") + content
                yield {"content": content}
        except Exception as exc:
            logger.error("[AssistantAgentRunner] Synthesis fallback failed: %s", exc, exc_info=True)

        if not emitted_any:
            logger.warning("[AssistantAgentRunner] Synthesis produced no visible text")
            state["full_content"] = (state.get("full_content") or "") + GENERIC_SYNTHESIS_EMPTY_FALLBACK
            yield {"content": GENERIC_SYNTHESIS_EMPTY_FALLBACK}
            return

        synthesis_tokens = extract_tokens_from_message(last_synthesis_chunk)
        if synthesis_tokens["prompt_tokens"] or synthesis_tokens["completion_tokens"]:
            self._increment_step()
            self.trace_buffer.append(
                AgentExecutionStep(
                    step_number=self.step_counter,
                    event_type="synthesis",
                    agent_name=self.config.agent_name,
                    model=str(getattr(llm, "model_name", self.config.synthesis_model_name or self.config.model_name) or ""),
                    temperature=float(self.config.synthesis_temperature or self.config.temperature or 0),
                    tool_name="synthesis_fallback",
                    tool_output={"content": state.get("full_content") or ""},
                    prompt_tokens=synthesis_tokens["prompt_tokens"],
                    completion_tokens=synthesis_tokens["completion_tokens"],
                    total_tokens=synthesis_tokens["total_tokens"],
                    timestamp=datetime.now(),
                )
            )

    async def _resolve_pending_runtime(
        self,
        pending: Any,
        *,
        loop_detector: ToolLoopDetector | None = None,
    ) -> tuple[Any, List[RuntimeToolSpec], Any, Dict[str, Any]]:
        if pending.agent is not None and pending.tools and pending.native_model is not None:
            return pending.agent, pending.tools, pending.native_model, pending.state

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
        agent = await self._build_native_agent(
            native_model=native_model,
            tools=tools,
            system_content=str(ctx.get("system_content", "")),
            max_steps=int(ctx.get("max_steps", 5)),
            restored_state=restored_state,
            primary_model_name=str(getattr(native_model, "model", self.config.model_name) or ""),
            loop_detector=loop_detector,
        )
        state = pending.state or dict(pending.snapshot.stream_state or {})
        if "tool_data" not in state:
            state["tool_data"] = {}
        return agent, tools, native_model, state

    async def _resolve_runtime_tools_from_config(self) -> List[RuntimeToolSpec]:
        configured_tools = self.config.tools or []
        tools: List[RuntimeToolSpec] = []
        if configured_tools:
            tools = await ToolRegistry.get_runtime_tools(configured_tools)

        system_tools = list(ToolRegistry.get_system_implicit_tools())
        if is_main_general_agent(self.config):
            sub_agent_tool = await ToolRegistry.get_tool("sub_agent_call")
            if sub_agent_tool:
                system_tools.append(sub_agent_tool)

        if system_tools:
            tools.extend(
                runtime_tool_spec_from_legacy_tool(tool, source_type="system")
                for tool in system_tools
            )
        return tools

    async def _resume_agentscope_native_stream(
        self,
        *,
        pending: Any,
        resume_event: Any,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        self._ensure_agent_context()
        agent_name = self._runtime_agent_name()
        loop_detector = await self._create_tool_loop_detector()
        try:
            async with agentscope_session_lock.hold(
                user_id=self._runtime_user_id(),
                conversation_id=self.conversation_id,
                agent_name=agent_name,
                ttl_seconds=300,
            ):
                agent, tools, native_model, state = await self._resolve_pending_runtime(
                    pending,
                    loop_detector=loop_detector,
                )
                interrupted = False
                async for chunk in self._stream_agentscope_native_events(
                    event_stream=agent.reply_stream(resume_event),
                    agent=agent,
                    tools=tools,
                    native_model=native_model,
                    state=state,
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
        except ToolLoopFuseError as fuse_err:
            yield {
                "type": "error",
                "status": "error",
                "content": f"检测到工具重复调用循环，已停止继续执行。{fuse_err}",
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

    def _build_tool_observation(
        self,
        *,
        tool_id: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        tool_output: Any,
        duration_tool: float,
        target_tool: Any,
        tool_index: int,
    ) -> Dict[str, Any]:
        is_error = "Error" in str(tool_output)

        runtime_cfg = getattr(target_tool, "_runtime_config", None)
        t_model = getattr(runtime_cfg, "model_name", self.config.model_name)
        t_temp = getattr(runtime_cfg, "temperature", self.config.temperature)
        
        # Ensure types for AgentExecutionStep validation (especially when using Mocks in tests)
        if not isinstance(t_model, str): t_model = str(self.config.model_name)
        if not isinstance(t_temp, (int, float)): t_temp = float(self.config.temperature or 0)

        trace_step = AgentExecutionStep(
            step_number=self.step_counter, event_type="tool_call", agent_name=self.config.agent_name,
            model=t_model, temperature=t_temp, tool_name=tool_name, tool_input=tool_args,
            tool_output=tool_output if isinstance(tool_output, (dict, list)) else {"raw": str(tool_output)},
            execution_time_ms=duration_tool, status="success" if not is_error else "error",
            timestamp=datetime.fromtimestamp(time.time() - duration_tool / 1000)
        )
        
        if tool_name == "search_knowledge_base" and not is_error:
            from app.services.ai.knowledge_utils import format_knowledge_tool_log_display

            display_output = format_knowledge_tool_log_display(tool_output, max_len=1200)
        else:
            display_output = truncate_for_context(str(tool_output), max_len=500)
        log_event = {
            "type": "log",
            "id": tool_id,
            "title": f"工具完成: {tool_name} ({duration_tool:.0f}ms)",
            "details": display_output,
            "status": "success" if not is_error else "error",
            "model": t_model,
            "temperature": t_temp,
        }
        
        # --- [NEW: Citation Extraction & Multi-Track Unpacking] ---
        citation_event = None
        final_tool_message_content = str(tool_output)

        if tool_name in ["search_knowledge_base", "jira_search"] and not is_error:
            try:
                # 1. Parse JSON Result
                parsed_res = None
                if isinstance(tool_output, str):
                    try:
                        parsed_res = json.loads(tool_output)
                    except:
                        pass
                elif isinstance(tool_output, dict):
                    parsed_res = tool_output

                if isinstance(parsed_res, dict):
                    # Unpack Multi-Track Format
                    # 'content' goes to LLM, 'citations' goes to Frontend
                    if "content" in parsed_res:
                        final_tool_message_content = parsed_res["content"]
                    
                    chunks = parsed_res.get("citations")
                    if isinstance(chunks, list) and len(chunks) > 0:
                        citation_event = {
                            "type": "citation",
                            "tool_call_id": tool_id,
                            "data": chunks
                        }
                elif isinstance(parsed_res, list) and len(parsed_res) > 0:
                    # Backward compatibility for direct array return
                    citation_event = {
                        "type": "citation",
                        "tool_call_id": tool_id,
                        "data": parsed_res
                    }
            except Exception as e:
                logger.warning(f"Failed to extract citations from {tool_name}: {e}")

        return {
            "index": tool_index,
            "final_tool_message_content": final_tool_message_content,
            "trace": trace_step, 
            "log": log_event,
            "citation": citation_event
        }
