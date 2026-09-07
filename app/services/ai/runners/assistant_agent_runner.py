import time
import uuid
import json
import inspect
import logging
import re
import asyncio
import contextlib
import os
import posixpath
from dataclasses import replace
from typing import List, Dict, Any, AsyncGenerator, Optional, Set
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
from app.services.ai.grounding.ledger import EvidenceLedger
from app.services.ai.grounding.models import EvidenceType, FactFreshness
from app.services.ai.grounding.policy import (
    FactRequirement,
    GroundingRiskLevel,
    contains_grounding_fact_signal,
    is_non_concrete_execution_summary,
    requires_complete_result_evidence,
    resolve_fact_requirement,
)
from app.services.ai.grounding.service import GroundingService
from app.services.ai.intent_service import (
    IntentType,
    looks_like_dynamic_public_fact_query,
    looks_like_knowledge_query,
    looks_like_public_profile_lookup,
    looks_like_runtime_diagnostic_query,
    looks_like_strong_business_data_request,
    looks_like_web_search_query,
)
from app.services.ai.skill_resolver import is_main_general_agent
from app.services.ai.request_decision import (
    RequestCapability,
    RequestDecision,
    RequestSource,
    resolve_request_decision,
)
from app.services.ai.turn_decision import TurnDecision
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
from app.services.ai.runtime.agentscope.tool_result import (
    build_final_tool_result_context,
    build_tool_result_envelope,
    extract_tool_result_error_reason,
    is_tool_result_error,
    normalize_tool_result_state,
    tool_call_id_from_metadata,
)
from app.services.ai.runtime.agentscope import process_narration as process_narration_events
from app.services.ai.runtime.agentscope.text_sanitize import sanitize_assistant_stream_text
from app.services.ai.runtime.agentscope.stream_reconcile import (
    build_tool_review_lines,
    GENERIC_SYNTHESIS_EMPTY_FALLBACK,
    compute_stream_reconcile_gap,
    needs_tool_synthesis_fallback,
    truncate_for_display,
)
from app.services.ai.runtime.agentscope.session_lock import (
    SessionLockTimeout,
    agentscope_session_lock,
)
from app.services.ai.runtime.agentscope.workspace import (
    bind_configured_tools_to_workspace,
    get_local_workspace,
    get_workspace_execution_backend,
    get_workspace_offloader,
)
from app.services.ai.runtime.agentscope.errors import extract_tool_loop_fuse_message
from app.services.ai.runtime.agentscope.tools import (
    RuntimeToolSpec,
    runtime_tool_spec_from_legacy_tool,
)
from app.services.ai.runtime.agentscope.tool_choice_compat import (
    tool_choice_for_model,
)
from app.services.ai.runtime.agentscope.tools import build_toolkit
from app.services.ai.tool_capability import (
    AgentScopeToolConsumer,
    RegistryToolProvider,
    resolve_tool_capabilities,
)
from app.services.ai.runtime.tool_loop_detector import ToolLoopDetector
from app.services.ai.time_anchor import filter_redundant_time_tools

logger = logging.getLogger(__name__)


_FILE_TOOL_OPERATIONS = {
    "Read": "read",
    "Write": "write",
    "Edit": "edit",
    "Glob": "search",
    "Grep": "search",
}


def _extract_agentscope_tool_call_input(agent: Any, tool_id: str) -> Any:
    """Read the final tool input saved in AgentScope's assistant context.

    AgentScope 2.x streams tool arguments as deltas, but the authoritative
    ``ToolCallBlock`` is also saved in ``agent.state.context`` before the tool
    runs.  The context is a useful fallback when a provider emits an empty or
    malformed argument delta while still executing the parsed tool call.
    """
    if agent is None or not tool_id:
        return None
    context = getattr(getattr(agent, "state", None), "context", None)
    if not isinstance(context, (list, tuple)):
        return None

    for message in reversed(context):
        blocks: Any = None
        get_content_blocks = getattr(message, "get_content_blocks", None)
        if callable(get_content_blocks):
            try:
                blocks = get_content_blocks("tool_call")
            except Exception:
                blocks = None
        elif isinstance(message, dict):
            blocks = message.get("content")
        if not isinstance(blocks, (list, tuple)):
            blocks = [blocks] if blocks is not None else []

        for block in reversed(blocks):
            block_id = (
                block.get("id")
                if isinstance(block, dict)
                else getattr(block, "id", None)
            )
            if str(block_id or "") != str(tool_id):
                continue
            return (
                block.get("input")
                if isinstance(block, dict)
                else getattr(block, "input", None)
            )
    return None


def _parse_tool_args_object(value: Any) -> Dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _resolve_agentscope_tool_args(
    agent: Any,
    tool_id: str,
    streamed_args: Any,
) -> Dict[str, Any]:
    """Resolve tool args without changing the model-facing tool contract."""
    streamed = _parse_tool_args_object(streamed_args)
    saved = _parse_tool_args_object(
        _extract_agentscope_tool_call_input(agent, tool_id)
    )
    if streamed:
        return streamed
    if saved is not None:
        return saved
    if streamed is not None:
        return streamed
    return {"input": streamed_args} if streamed_args else {}


def _logical_file_tool_path(raw_path: Any) -> str | None:
    """Convert an authorized backend file-tool path to a safe logical path."""
    raw = str(raw_path or "").strip().replace("\\", "/")
    if not raw:
        return None
    normalized = posixpath.normpath(raw)
    if normalized in {".", ".."} or normalized.startswith("../"):
        return None
    if normalized == "/workspace" or normalized.startswith("/workspace/"):
        return normalized

    parts = [part for part in normalized.split("/") if part and part != "."]
    if not parts:
        return None

    # Public docs/skills and user workspaces may arrive as backend-service
    # paths (for example /app/data/docs or /app/data/agent_workspaces/<key>).
    for index in range(len(parts) - 1):
        if parts[index : index + 2] == ["data", "docs"]:
            suffix = parts[index + 2 :]
            return posixpath.join("/workspace/public/docs", *suffix) if suffix else "/workspace/public/docs"
        if parts[index : index + 2] == ["data", "skills"]:
            suffix = parts[index + 2 :]
            return posixpath.join("/workspace/skills", *suffix) if suffix else "/workspace/skills"

    if "agent_workspaces" in parts:
        index = parts.index("agent_workspaces")
        suffix = parts[index + 2 :]
        allowed_roots = {"docs", "sessions", "uploads", "skills", ".trash"}
        if not suffix:
            return "/workspace"
        if suffix[0] in allowed_roots:
            return posixpath.join("/workspace", *suffix)
        return None

    # Relative file-tool arguments are relative to the user workspace. Keep
    # them in the same logical namespace instead of dropping the metadata.
    if not normalized.startswith("/"):
        return posixpath.join("/workspace", normalized)

    # Service-root help files are intentionally not mounted into Bash, but
    # /app/<name>.md is the documented backend path for Read/Glob/Grep.
    if normalized.startswith("/app/") and normalized.count("/") == 2 and normalized.endswith(".md"):
        return normalized
    return None


def _build_file_tool_metadata(
    tool_name: str,
    tool_args: Dict[str, Any],
    tool_output: Any = None,
) -> Dict[str, Any] | None:
    """Build safe, logical-path metadata for workspace file tools."""
    tool_name = str(tool_name or "")
    document_type = (
        "word"
        if tool_name.startswith("word_document_")
        else "excel"
        if tool_name.startswith("excel_document_")
        else None
    )
    if document_type:
        action = str(tool_args.get("action") or "")
        raw_path = str(tool_args.get("output_filename") or tool_args.get("path") or "").strip()
        if not raw_path:
            return None
        output = tool_output if isinstance(tool_output, dict) else {}
        artifact = output.get("artifact") if isinstance(output.get("artifact"), dict) else {}
        changes = output.get("changes") if isinstance(output.get("changes"), dict) else {}
        metadata: Dict[str, Any] = {
            "operation": "read" if tool_name.endswith("_read") else "write",
            "document_type": document_type,
            "action": action,
            "target_type": "file",
            "path": os.path.basename(raw_path),
            "file_name": os.path.basename(raw_path),
        }
        extension = os.path.splitext(raw_path)[1]
        if extension:
            metadata["file_extension"] = extension
        if document_type == "word" and tool_name.endswith("_read") and action == "read_content":
            metadata["paragraph_range"] = {
                "start": tool_args.get("start", 0),
                "limit": tool_args.get("limit", 20),
            }
        if document_type == "excel":
            if tool_args.get("sheet_name"):
                metadata["sheet_name"] = str(tool_args["sheet_name"])
            if tool_args.get("cell_range"):
                metadata["cell_range"] = str(tool_args["cell_range"])
        if changes:
            metadata["changes"] = changes
        if artifact.get("size") is not None:
            metadata["size_bytes"] = artifact["size"]
        if artifact.get("mime_type"):
            metadata["mime_type"] = artifact["mime_type"]
        return metadata

    operation = _FILE_TOOL_OPERATIONS.get(tool_name)
    if operation is None:
        return None

    path_key = "file_path" if tool_name in {"Read", "Write", "Edit"} else "path"
    raw_path = _logical_file_tool_path(tool_args.get(path_key))
    if raw_path is None:
        return None

    metadata: Dict[str, Any] = {
        "operation": operation,
        "path": raw_path,
        "target_type": "file" if path_key == "file_path" else "directory",
        "file_name": os.path.basename(raw_path) if path_key == "file_path" else None,
    }
    if path_key == "file_path":
        extension = os.path.splitext(raw_path)[1]
        if extension:
            metadata["file_extension"] = extension
        if tool_name == "Read":
            offset = tool_args.get("offset")
            limit = tool_args.get("limit")
            if offset is not None or limit is not None:
                metadata["range"] = {
                    key: value
                    for key, value in (("start", offset), ("limit", limit))
                    if value is not None
                }
    else:
        if tool_args.get("pattern"):
            metadata["pattern"] = str(tool_args["pattern"])
        if tool_args.get("glob"):
            metadata["glob"] = str(tool_args["glob"])
    return metadata


def _is_grounding_bufferable_chunk(chunk: Dict[str, Any]) -> bool:
    """Only buffer legacy untyped answer text that still needs grounding review.

    Typed answer_delta / retraction / promote events are already the user
    visible stream. Buffering them would hide token-by-token output until the
    model call finished. Grounding still audits the accumulated text after
    the stream, and appends a warning without delaying the answer.
    """
    chunk_type = str(chunk.get("type") or "")
    if chunk_type in {
        "process_narration",
        "process_narration_commit",
        "process_narration_promote",
        "answer_delta",
        "retraction",
    }:
        return False
    return not chunk_type and "content" in chunk


def _is_strict_evidence_bufferable_chunk(chunk: Dict[str, Any]) -> bool:
    """在当前轮证据门禁下暂存所有可能成为最终正文的文本事件。"""
    chunk_type = str(chunk.get("type") or "")
    if chunk_type in {
        "answer_delta",
        "process_narration_promote",
        "retraction",
    }:
        return True
    return not chunk_type and "content" in chunk


class _ForcedFirstToolChoiceModel:
    """仅在首个模型调用上注入 tool_choice，强制本轮优先走工具；其后恢复模型原生行为。"""

    def __init__(self, inner: Any, tool_choice: Any):
        self._inner = inner
        self._tool_choice = tool_choice_for_model(inner, tool_choice)
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
        turn_decision: Optional[TurnDecision] = None,
        current_user_query: Optional[str] = None,
    ):
        super().__init__(config, trace_id, trace_buffer, debug_options, user_info, conversation_id, permission_options)
        self.turn_decision = turn_decision or TurnDecision(
            route_status="failed",
            provenance="missing_turn_decision",
        )
        # A 项：本轮工具调用元数据（tool_names/args/outputs/data）流结束后落于此，
        # 供保存点读取并跨轮持久化，避免污染 assistant 展示内容。
        self._last_turn_tool_meta: Optional[Dict[str, Any]] = None
        self._execution_backend: str | None = None
        self.current_user_query = current_user_query

    def _runtime_user_id(self) -> str | None:
        if not self.user_info:
            return None
        return str(self.user_info.get("user_id") or self.user_info.get("id") or "") or None

    def _runtime_user_name(self) -> str | None:
        if not self.user_info:
            return None
        raw_name = self.user_info.get("user_name") or self.user_info.get("username")
        if not raw_name:
            return None
        name = str(raw_name).strip()
        return name or None

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
            "turn_decision": (
                self.turn_decision.model_dump(mode="json")
                if self.turn_decision is not None
                else None
            ),
            "current_user_query": self.current_user_query,
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
            turn_decision=TurnDecision.model_validate(runner_context["turn_decision"]),
            current_user_query=runner_context.get("current_user_query"),
        )
        return runner

    @staticmethod
    def _extract_last_user_query(history: List[Dict[str, str]]) -> str:
        for msg in reversed(history or []):
            if msg.get("role") == "user":
                return str(msg.get("content") or "")
        return ""

    def _current_user_query(self, history: List[Dict[str, str]]) -> str:
        """Return the request submitted in this turn, with history as fallback only."""
        if self.current_user_query is not None:
            return str(self.current_user_query)
        return self._extract_last_user_query(history)

    def _is_direct_agent_selection(self) -> bool:
        """专家模式 / @提及 / 显式 agent_id 直达，不走主专家自动委派。"""
        return bool(
            self.turn_decision is not None
            and self.turn_decision.provenance == "direct_agent_selection"
        )

    def _should_run_data_hallucination_guard(self, user_query: str) -> bool:
        """仅主助手自动委派链路 + 明确查数诉求时启用，防止无 DB 连接时编造业务数据。"""
        if not is_main_general_agent(self.config):
            return False
        if self._is_direct_agent_selection():
            return False
        if looks_like_web_search_query(user_query):
            return False

        semantic_intent = str(self.turn_decision.semantic_intent or "").strip().upper()
        if self.turn_decision.turn_kind == "data_query" or semantic_intent == IntentType.DATA_QUERY.value:
            return True
        if self.turn_decision.turn_kind == "general" and semantic_intent in {
            "",
            IntentType.GENERAL.value,
            IntentType.UNKNOWN.value,
        }:
            return False
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

    @classmethod
    def _build_sub_agent_candidates_by_capability(cls, agents: Any) -> Dict[str, List[str]]:
        candidates: Dict[str, List[str]] = {}
        ordered_agents = sorted(
            agents or [],
            key=lambda agent: (
                -int(cls._agent_field(agent, "sort_order", 0) or 0),
                str(cls._agent_field(agent, "id", "") or ""),
            ),
        )
        for agent in ordered_agents:
            agent_name = str(cls._agent_field(agent, "name", "") or "").strip()
            if not agent_name:
                continue
            for capability in cls._agent_field(agent, "capabilities", []) or []:
                capability_key = str(capability or "").strip()
                if not capability_key:
                    continue
                names = candidates.setdefault(capability_key, [])
                if agent_name not in names:
                    names.append(agent_name)
        return candidates

    @classmethod
    def _build_sub_agent_targets_by_capability(cls, agents: Any) -> Dict[str, str]:
        """兼容旧调用：每个 capability 仍返回排序后的首个候选。"""
        return {
            capability: names[0]
            for capability, names in cls._build_sub_agent_candidates_by_capability(agents).items()
            if names
        }

    async def _resolve_available_sub_agent_delegation_info(self) -> tuple[Optional[Set[str]], Dict[str, List[str]]]:
        if not is_main_general_agent(self.config):
            return None, {}
        try:
            from app.services.ai.tools.agent_delegate_tool import (
                delegable_agent_name_aliases,
                resolve_runnable_delegable_system_agents,
            )

            raw_user_id = None
            is_admin = False
            if self.user_info:
                raw_user_id = self.user_info.get("user_id") or self.user_info.get("id")
                is_admin = self.user_info.get("role") == "admin"
            async with AsyncSessionLocal() as session:
                agents = await AgentManagerService.list_agents(session)
                delegable_agents = await resolve_runnable_delegable_system_agents(
                    session,
                    agents,
                    user_id=raw_user_id,
                    is_admin=is_admin,
                    current_agent_id=self.config.agent_id,
                )
            return (
                delegable_agent_name_aliases(delegable_agents),
                self._build_sub_agent_candidates_by_capability(delegable_agents),
            )
        except Exception as exc:
            logger.warning("[AssistantAgentRunner] Failed to resolve sub-agent availability: %s", exc)
            return None, {}

    async def _resolve_available_sub_agent_names(self) -> Optional[Set[str]]:
        names, _ = await self._resolve_available_sub_agent_delegation_info()
        return names

    def _resolve_grounding_request_decision(self, user_query: str) -> RequestDecision:
        if self.turn_decision is not None:
            decision = self.turn_decision.to_request_decision()
            if getattr(decision, "knowledge_catalog_status", None):
                return decision
            if decision.source not in {RequestSource.UNKNOWN, RequestSource.GENERAL}:
                return decision
            inferred = resolve_request_decision(
                user_query,
                semantic_intent=self.turn_decision.semantic_intent,
                semantic_confidence=self.turn_decision.semantic_confidence,
                semantic_domain=self.turn_decision.semantic_domain,
                semantic_operation=self.turn_decision.semantic_operation,
                fact_kind=self.turn_decision.fact_kind,
                freshness_requirement=self.turn_decision.freshness_requirement,
                time_scope=self.turn_decision.time_scope,
                reference_mode=self.turn_decision.reference_mode,
                needs_fresh_data=self.turn_decision.needs_fresh_data,
                max_age_seconds=self.turn_decision.max_age_seconds,
                requires_source_timestamp=self.turn_decision.requires_source_timestamp,
            )
            if inferred.source is not RequestSource.UNKNOWN:
                return inferred
            if self._is_direct_agent_selection() and looks_like_strong_business_data_request(
                user_query
            ):
                return resolve_request_decision(
                    user_query,
                    semantic_intent=IntentType.DATA_QUERY,
                    semantic_confidence=1.0,
                )
            return decision
        return RequestDecision(
            source=RequestSource.UNKNOWN,
            capability=RequestCapability.ANSWER,
            confidence=0.0,
            reasoning="missing turn decision",
        )

    @staticmethod
    def _parse_grounding_retry_evidence_types(action: Any) -> frozenset[EvidenceType]:
        if not isinstance(action, dict) or action.get("type") != "retry":
            return frozenset()
        evidence_types: set[EvidenceType] = set()
        for value in action.get("required_evidence_types") or []:
            try:
                evidence_types.add(EvidenceType(value))
            except (TypeError, ValueError):
                continue
        return frozenset(evidence_types)

    @staticmethod
    def _select_grounding_retry_tool(
        tools: List[Any],
        required_types: frozenset[EvidenceType],
    ) -> Any:
        if not required_types:
            return None
        return next(
            (
                tool
                for tool in tools
                if bool(
                    frozenset(getattr(tool, "evidence_types", None) or ())
                    & required_types
                )
            ),
            None,
        )

    @staticmethod
    def _should_buffer_grounding_output(
        requirement: FactRequirement,
        *,
        run_data_guard: bool,
    ) -> bool:
        return bool(
            run_data_guard
            or requirement.required
            or requirement.scrutinize_unknown_output
        )

    def _should_enable_candidate_answer_streaming(
        self,
        *,
        grounding_enabled: bool,
        grounding_requirement: FactRequirement,
        run_data_guard: bool,
        evidence_contracts: tuple[dict[str, Any], ...],
    ) -> bool:
        """仅为没有当轮证据责任的一般对话放开候选正文。

        判断基于路由和已建立的证据合同，而非文本启发式；任何无法确认
        责任边界的旧入口都保持保守协议。是否缓冲 grounding 输出在此处
        收敛推导，调用方无需重复计算。
        """
        decision = self.turn_decision
        if not (
            decision is not None
            and str(decision.turn_kind or "").strip().lower() == "general"
        ):
            return False
        buffer_output = (
            grounding_enabled
            and self._should_buffer_grounding_output(
                grounding_requirement,
                run_data_guard=run_data_guard,
            )
        )
        return bool(
            not grounding_requirement.required
            and not grounding_requirement.scrutinize_unknown_output
            and not buffer_output
            and not evidence_contracts
        )

    @staticmethod
    def _normalize_grounding_block_mode(value: Any) -> str:
        mode = str(value or "strict_buffer").strip().lower()
        return mode if mode in {"strict_buffer", "stream_with_retraction"} else "strict_buffer"

    @staticmethod
    def _grounding_decision_metadata(requirement: FactRequirement) -> Dict[str, Any]:
        return {
            "decision_origin": requirement.decision_origin,
            "decision_confidence": requirement.decision_confidence,
            "evidence_mode": requirement.evidence_mode,
            "accepted_evidence_types": sorted(
                evidence_type.value for evidence_type in requirement.accepted_types
            ),
            "decision_conflicts": list(requirement.decision_conflicts),
        }

    @staticmethod
    def _resolve_current_turn_evidence_types(
        tool_name: str,
        tools: List[Any],
    ) -> frozenset[EvidenceType]:
        """Return evidence types that make a mounted tool a current-turn source."""
        normalized_name = str(tool_name or "").strip()
        target_tool = next(
            (
                tool
                for tool in tools or []
                if str(getattr(tool, "name", "") or "").strip() == normalized_name
            ),
            None,
        )
        if target_tool is None:
            return frozenset()

        from app.services.ai.tool_policy import resolve_tool_metadata

        evidence_types = frozenset(getattr(target_tool, "evidence_types", None) or ())
        permission_scope = str(
            getattr(target_tool, "permission_scope", "") or ""
        ).strip().lower()
        try:
            metadata = resolve_tool_metadata(target_tool)
        except Exception:
            # 预检异常时仍依据运行时的最小安全声明判断证据类型，避免异常路径
            # 把严格门禁一并绕过。
            return evidence_types if permission_scope == "read" else frozenset()
        if getattr(metadata, "nudge_mode", "") != "evidence":
            return frozenset()
        return evidence_types

    @staticmethod
    def _build_tool_preflight_evidence_metadata(
        tool_nudge: Any,
        tools: List[Any],
        evidence_contracts: Optional[List[Any]] = None,
        *,
        grounding_enabled: bool = True,
    ) -> Dict[str, Any]:
        """Return safe metadata for a nudge that requires current-turn evidence."""
        evidence_types = AssistantAgentRunner._resolve_current_turn_evidence_types(
            str(getattr(tool_nudge, "tool_name", "") or ""),
            tools,
        )
        required = bool(
            grounding_enabled
            and getattr(tool_nudge, "should_force_first_call", False)
            and evidence_types
        )
        metadata: Dict[str, Any] = {
            "current_turn_evidence_required": required,
            "required_evidence_types": sorted(
                str(getattr(item, "value", item)) for item in evidence_types
            )
            if required
            else [],
        }
        if evidence_contracts:
            metadata["evidence_contracts"] = [
                contract.to_dict()
                if hasattr(contract, "to_dict")
                else dict(contract)
                for contract in evidence_contracts
            ]
        return metadata

    @staticmethod
    def _parse_evidence_contracts(raw_contracts: Any) -> tuple[dict[str, Any], ...]:
        if not isinstance(raw_contracts, list):
            return ()
        contracts: list[dict[str, Any]] = []
        for raw in raw_contracts:
            if not isinstance(raw, dict):
                continue
            tool_name = str(raw.get("tool_name") or "").strip()
            evidence_types: list[str] = []
            for value in raw.get("required_evidence_types") or []:
                try:
                    evidence_types.append(EvidenceType(value).value)
                except (TypeError, ValueError):
                    continue
            if tool_name and evidence_types:
                contracts.append(
                    {
                        "tool_name": tool_name,
                        "required_evidence_types": sorted(set(evidence_types)),
                        "freshness": str(raw.get("freshness") or "current_turn"),
                    }
                )
        return tuple(contracts)

    @staticmethod
    def _resolve_contract_freshness(value: Any) -> FactFreshness | None:
        normalized = str(value or "current_turn").strip().lower()
        if normalized == "current_turn":
            return FactFreshness.DYNAMIC
        try:
            return FactFreshness(normalized)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _resolve_resume_evidence_contracts(
        state: Dict[str, Any],
        tools: List[Any] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """只恢复首次预检保存的合同，不在 resume 热路径重新规划。

        ``tools`` 参数保留是为了兼容已有测试和外部调用方，但有意不使用。
        旧快照没有合同字段时按空合同继续，由当前轮已有的工具结果/grounding
        门禁负责校验，避免工具集变更后凭用户原话生成一套不一致的新合同。
        """
        if "evidence_contracts" in state:
            return AssistantAgentRunner._parse_evidence_contracts(
                state.get("evidence_contracts")
            )
        return ()

    @staticmethod
    def _evidence_contracts_satisfied(
        contracts: tuple[dict[str, Any], ...],
        *,
        candidate_text: str,
        ledger: EvidenceLedger,
    ) -> tuple[bool, str]:
        if not contracts:
            return True, ""
        try:
            has_concrete_claim = (
                contains_grounding_fact_signal(candidate_text)
                and not is_non_concrete_execution_summary(candidate_text)
            )
        except Exception:
            has_concrete_claim = True
        allow_truncated = not requires_complete_result_evidence(candidate_text)
        for contract in contracts:
            try:
                required_types = frozenset(
                    EvidenceType(value)
                    for value in contract["required_evidence_types"]
                )
            except (TypeError, ValueError, KeyError):
                return False, "evidence contract contains an invalid evidence type"
            freshness = AssistantAgentRunner._resolve_contract_freshness(
                contract.get("freshness")
            )
            if freshness is None:
                return False, "evidence contract contains an invalid freshness"
            producer = contract["tool_name"]
            if not ledger.has_fresh_evidence_from_producer(
                producer,
                required_types,
                freshness=freshness,
                allow_reuse=freshness is FactFreshness.REUSE_PREVIOUS,
                allow_truncated=allow_truncated,
            ):
                return False, f"evidence contract missing fresh receipt from {producer}"
            if has_concrete_claim and not ledger.has_candidate_overlap_from_producer(
                candidate_text,
                producer,
                required_types,
                freshness=freshness,
                allow_reuse=freshness is FactFreshness.REUSE_PREVIOUS,
                allow_truncated=allow_truncated,
            ):
                return False, f"answer is not correlated with evidence contract {producer}"
        return True, ""

    @staticmethod
    def _should_block_current_turn_evidence(
        *,
        grounding_decision: Any,
        contracts_satisfied: bool,
        contracts_reason: str,
        required_evidence_types: frozenset[EvidenceType],
    ) -> bool:
        """按风险等级决定阻断当前轮具体结论还是降级提示。

        没有成功收据、证据过期、来源冲突和合同格式错误意味着事实没有可靠
        来源，必须阻断。若对应外部工具已经成功返回，仅因模型改写后无法通过
        marker 关联校验，则保留回答并提示用户核对，避免把合理的自然语言改写
        当成硬错误。内部数据和知识库仍属于高风险事实域，不走该降级路径。
        """
        reason = str(contracts_reason or "").strip().lower()
        correlation_only = reason.startswith(
            "answer is not correlated with evidence contract"
        ) or str(getattr(grounding_decision, "reason", "")).strip().lower() == (
            "matching evidence type exists but the answer is not correlated with its content"
        )
        if correlation_only and not (
            required_evidence_types
            & {EvidenceType.INTERNAL_DATA, EvidenceType.INTERNAL_KNOWLEDGE}
        ):
            return False

        if not contracts_satisfied:
            return True

        risk_level = getattr(grounding_decision, "risk_level", None)
        try:
            return GroundingRiskLevel(risk_level) is GroundingRiskLevel.HIGH
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _build_downgraded_grounding_warning(
        *,
        grounding_audit: Any,
        grounding_decision: Any,
        contracts_reason: str,
    ) -> Dict[str, Any]:
        """为已取证但关联度不足的回答生成中风险提示。"""
        warning_chunk = getattr(grounding_audit, "warning_chunk", None)
        warning_risk = (
            warning_chunk.get("grounding_risk")
            if isinstance(warning_chunk, dict)
            else None
        )
        if not isinstance(warning_risk, dict) or warning_risk.get("level") == "high":
            return GroundingService.warning_chunk(
                risk_level=GroundingRiskLevel.MEDIUM,
                reason=(
                    contracts_reason
                    or "matching evidence exists but answer correlation is incomplete"
                ),
                required_types=getattr(
                    grounding_decision, "required_evidence_types", frozenset()
                ),
                available_types=getattr(
                    grounding_decision, "available_evidence_types", frozenset()
                ),
            )
        return warning_chunk

    @staticmethod
    def _current_turn_grounding_ledger(ledger: EvidenceLedger) -> EvidenceLedger:
        """构造只含当前轮可复核收据的审计视图。"""
        reusable_freshness = FactFreshness.REUSE_PREVIOUS.value
        snapshot = ledger.to_snapshot()
        current_turn_snapshot = [
            receipt
            for receipt in snapshot
            if receipt.get("freshness") != reusable_freshness
        ]
        if len(current_turn_snapshot) == len(snapshot):
            return ledger
        return EvidenceLedger.from_snapshot(
            current_turn_snapshot,
            user_id=ledger.user_id,
            conversation_id=ledger.conversation_id,
        )

    def _resolve_turn_grounding_requirement(
        self,
        user_query: str,
        ctx: Any,
    ) -> FactRequirement:
        requirement = resolve_fact_requirement(
            self._resolve_grounding_request_decision(user_query)
        )
        grounding_action = (
            self.debug_options.get("grounding_action")
            if self._grounding_enabled()
            else None
        )
        if isinstance(grounding_action, dict) and grounding_action.get("type") == "method":
            return FactRequirement(
                required=False,
                accepted_types=frozenset(),
                scrutinize_unknown_output=True,
                evidence_mode="optional",
                decision_origin="explicit",
                decision_confidence=1.0,
            )
        retry_types = self._parse_grounding_retry_evidence_types(
            grounding_action
        )
        if retry_types:
            return FactRequirement(
                required=True,
                accepted_types=retry_types,
                evidence_mode="required",
                decision_origin="explicit",
                decision_confidence=1.0,
            )
        current_turn_paths = getattr(ctx, "current_turn_attachment_paths", None) or []
        references_file = bool(
            re.search(r"(?:附件|文件|文档|表格|工作簿|日志)(?:里|中|内|的|内容|数据)?", user_query or "", re.I)
        )
        references_attachment_continuation = bool(
            re.search(
                r"(?:第?\s*[一二三四五六七八九十百两\d]+\s*页|上一页|下一页|"
                r"第?\s*[一二三四五六七八九十百两\d]+\s*(?:个)?(?:sheet|工作表)|"
                r"sheet\s*\d+)",
                user_query or "",
                re.I,
            )
        )
        has_relevant_attachment = bool(current_turn_paths) or bool(
            getattr(ctx, "authorized_attachment_paths", None)
            and (references_file or references_attachment_continuation)
        )
        if has_relevant_attachment:
            return replace(
                requirement,
                required=True,
                accepted_types=(
                    requirement.accepted_types
                    | frozenset({EvidenceType.USER_FILE})
                ),
            )
        return requirement

    @staticmethod
    def _refine_unknown_requirement_from_evidence(
        requirement: FactRequirement,
        *,
        user_query: str,
        ledger: EvidenceLedger,
    ) -> FactRequirement:
        if not requirement.scrutinize_unknown_output:
            return requirement

        from app.services.ai.memory_recall_policy import (
            looks_like_cross_session_recall_query,
        )

        signal_contracts = (
            (looks_like_strong_business_data_request(user_query), EvidenceType.INTERNAL_DATA),
            (
                looks_like_web_search_query(user_query)
                or looks_like_dynamic_public_fact_query(user_query)
                or looks_like_public_profile_lookup(user_query),
                EvidenceType.PUBLIC_WEB,
            ),
            (looks_like_runtime_diagnostic_query(user_query), EvidenceType.RUNTIME_STATE),
            (looks_like_knowledge_query(user_query), EvidenceType.INTERNAL_KNOWLEDGE),
            (
                looks_like_cross_session_recall_query(user_query),
                EvidenceType.CONVERSATION_MEMORY,
            ),
        )
        for has_signal, evidence_type in signal_contracts:
            if has_signal and ledger.has_valid_evidence({evidence_type}):
                return replace(
                    requirement,
                    required=True,
                    accepted_types=frozenset({evidence_type}),
                    evidence_mode="required",
                    decision_origin="lexical",
                    decision_confidence=1.0,
                )
        return requirement

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
        user_query = self._current_user_query(history)
        grounding_enabled = self._grounding_enabled()
        run_data_guard = (
            grounding_enabled
            and self._should_run_data_hallucination_guard(user_query)
        )
        ctx = self._ensure_agent_context()
        shared_ledger = (
            getattr(ctx, "grounding_evidence_ledger", None)
            if getattr(ctx, "delegation_depth", 0) > 0
            else None
        )
        self._evidence_ledger = shared_ledger or EvidenceLedger(
            user_id=self._runtime_user_id(),
            conversation_id=self.conversation_id,
        )
        ctx.grounding_evidence_ledger = self._evidence_ledger
        grounding_requirement = (
            self._resolve_turn_grounding_requirement(user_query, ctx)
            if grounding_enabled
            else FactRequirement(required=False, accepted_types=frozenset())
        )
        buffer_output = (
            grounding_enabled
            and self._should_buffer_grounding_output(
                grounding_requirement,
                run_data_guard=run_data_guard,
            )
        )

        chunks_buffer = []
        full_text = ""
        has_attempted_tool = False
        interrupted = False
        strict_tool_evidence_required = False
        strict_tool_evidence_types = frozenset()
        strict_evidence_contracts: tuple[dict[str, Any], ...] = ()
        grounding_block_mode = self._normalize_grounding_block_mode(
            self.debug_options.get("grounding_block_mode")
        )

        from app.core.context import set_agent_context
        import asyncio

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
                except asyncio.CancelledError:
                    await out_queue.put(("cancelled", None))
                    raise
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
                        elif tag == "cancelled":
                            raise asyncio.CancelledError()
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
                for pending in (t1, t2):
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await pending
                try:
                    await event_queue.put("DONE")
                except Exception:
                    pass

        async for chunk in merge_streams(self._execute_core(history)):
            if is_interrupt_sse_chunk(chunk):
                interrupted = True

            if (
                chunk.get("type") == "log"
                and chunk.get("category") == "tool_preflight"
                and grounding_enabled
                and chunk.get("current_turn_evidence_required") is True
            ):
                parsed_types = set()
                for raw_type in chunk.get("required_evidence_types") or []:
                    try:
                        parsed_types.add(EvidenceType(raw_type))
                    except (TypeError, ValueError):
                        continue
                if parsed_types:
                    strict_tool_evidence_required = True
                    strict_tool_evidence_types = frozenset(parsed_types)
                    strict_evidence_contracts = self._parse_evidence_contracts(
                        chunk.get("evidence_contracts")
                    )
                    grounding_block_mode = self._normalize_grounding_block_mode(
                        chunk.get("grounding_block_mode", grounding_block_mode)
                    )
                    buffer_output = True
                    grounding_requirement = replace(
                        grounding_requirement,
                        required=True,
                        accepted_types=strict_tool_evidence_types,
                        block_unsupported_facts=True,
                        freshness=FactFreshness.DYNAMIC,
                        allow_conversation_reuse=False,
                        evidence_mode="required",
                        decision_origin="tool_preflight",
                        decision_confidence=1.0,
                    )

            if self._chunk_indicates_tool_attempt(chunk):
                has_attempted_tool = True

            if buffer_output:
                full_text = process_narration_events.accumulate_visible_answer(full_text, chunk)
            should_buffer_chunk = buffer_output and (
                _is_grounding_bufferable_chunk(chunk)
                or (
                    strict_tool_evidence_required
                    and _is_strict_evidence_bufferable_chunk(chunk)
                )
            )
            stream_strict_chunk = (
                strict_tool_evidence_required
                and grounding_block_mode == "stream_with_retraction"
                and _is_strict_evidence_bufferable_chunk(chunk)
            )
            if should_buffer_chunk and not stream_strict_chunk:
                chunks_buffer.append(chunk)
            else:
                yield chunk

        if not buffer_output:
            return

        # 权限确认/外部执行挂起时，当前轮尚未完成，不能提前把暂存文本判定为无证据。
        # 恢复入口会根据挂起的工具重新建立同一轮证据门禁。
        if interrupted:
            return

        should_intercept = (
            run_data_guard
            and not has_attempted_tool
            and self._is_hallucinated_data_reply(full_text)
        )

        audit_ledger = (
            self._current_turn_grounding_ledger(self._evidence_ledger)
            if strict_tool_evidence_required
            else self._evidence_ledger
        )
        evaluated_requirement = self._refine_unknown_requirement_from_evidence(
            grounding_requirement,
            user_query=user_query,
            ledger=audit_ledger,
        )
        grounding_audit = GroundingService.audit(
            requirement=evaluated_requirement,
            candidate_text=full_text,
            ledger=audit_ledger,
        )
        grounding_decision = grounding_audit.decision
        contracts_satisfied, contracts_reason = self._evidence_contracts_satisfied(
            strict_evidence_contracts,
            candidate_text=full_text,
            ledger=audit_ledger,
        )
        current_turn_evidence_blocked = self._should_block_current_turn_evidence(
            grounding_decision=grounding_decision,
            contracts_satisfied=contracts_satisfied,
            contracts_reason=contracts_reason,
            required_evidence_types=strict_tool_evidence_types,
        )

        if strict_tool_evidence_required and (
            grounding_audit.should_warn or not contracts_satisfied
        ):
            if current_turn_evidence_blocked:
                logger.warning(
                    "[AssistantAgentRunner] Current-turn evidence gate blocked answer: %s",
                    contracts_reason or grounding_decision.reason,
                )
                guidance = GroundingService.guided_response(
                    candidate_text=full_text,
                    reason=grounding_decision.reason,
                    required_types=frozenset(
                        strict_tool_evidence_types
                        or grounding_decision.required_evidence_types
                    ),
                    available_types=grounding_decision.available_evidence_types,
                    contracts_reason=contracts_reason,
                )
                yield {
                    "type": "log",
                    "id": f"grounding_guidance_{uuid.uuid4().hex[:8]}",
                    "title": "已切换为安全说明",
                    "details": (
                        "当前没有足够可核对依据，未展示未经核实的具体结论；"
                        "可以补充查询条件后继续。"
                    ),
                    "status": "warning",
                    "category": "grounding",
                    "grounding_downgraded": True,
                    "grounding_decision": self._grounding_decision_metadata(
                        evaluated_requirement
                    ),
                }
                if grounding_block_mode == "stream_with_retraction":
                    yield {
                        "type": "retraction",
                        "content": guidance.content,
                        "grounding_downgraded": True,
                        "final": True,
                    }
                else:
                    yield {
                        "type": "answer_delta",
                        "content": guidance.content,
                        "phase": "synthesis",
                        "grounding_downgraded": True,
                    }
            else:
                logger.warning(
                    "[AssistantAgentRunner] Current-turn evidence gate downgraded to warning: %s",
                    contracts_reason or grounding_decision.reason,
                )
                for chunk in chunks_buffer:
                    yield chunk
                yield {
                    "type": "log",
                    "id": f"grounding_warning_{uuid.uuid4().hex[:8]}",
                    "title": "事实来源风险提示已追加",
                    "details": (
                        "本轮已获得工具结果，但回答与结果的关联度不足，"
                        "已保留回答并提示核对原始来源。"
                    ),
                    "status": "warning",
                    "category": "grounding",
                    "grounding_blocked": False,
                    "grounding_downgraded": True,
                    "grounding_decision": self._grounding_decision_metadata(
                        evaluated_requirement
                    ),
                }
                yield self._build_downgraded_grounding_warning(
                    grounding_audit=grounding_audit,
                    grounding_decision=grounding_decision,
                    contracts_reason=contracts_reason,
                )
            return

        if should_intercept:
            logger.warning(
                f"[AssistantAgentRunner] Potential hallucination retained with risk warning. "
                f"run_data_guard={run_data_guard}, has_attempted_tool={has_attempted_tool}. "
                f"looks_hallucinated={self._is_hallucinated_data_reply(full_text)}. "
                f"Generated: {full_text[:200]}..."
            )
            for chunk in chunks_buffer:
                yield chunk
            yield {
                "type": "log",
                "id": f"data_general_guard_{uuid.uuid4().hex[:8]}",
                "title": "事实来源风险提示已追加",
                "details": "检测到回答包含尚未完全核实的内部数据表述，已保留正文并追加风险提示。",
                "status": "warning",
                "category": "grounding",
                "grounding_decision": self._grounding_decision_metadata(
                    evaluated_requirement
                ),
            }
            yield GroundingService.warning_chunk(
                risk_level=GroundingRiskLevel.HIGH,
                reason="legacy data hallucination signal matched without a trusted tool attempt",
                required_types=frozenset({EvidenceType.INTERNAL_DATA}),
                available_types=self._evidence_ledger.available_evidence_types,
            )
        elif grounding_audit.should_warn:
            logger.warning(
                "[AssistantAgentRunner] Ungrounded factual response retained with warning: %s",
                grounding_decision.reason,
            )
            for chunk in chunks_buffer:
                yield chunk
            yield {
                "type": "log",
                "id": f"grounding_{uuid.uuid4().hex[:8]}",
                "title": "事实来源风险提示已追加",
                "details": grounding_decision.reason,
                "status": "warning",
                "category": "grounding",
                "grounding_decision": self._grounding_decision_metadata(
                    evaluated_requirement
                ),
            }
            yield grounding_audit.warning_chunk
        else:
            for chunk in chunks_buffer:
                yield chunk

    async def _execute_core(
        self,
        history: List[Dict[str, str]]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from app.services.ai.multimodal_support import (
            resolve_runtime_model_name,
            run_multimodal_gate,
        )

        user_query = self._current_user_query(history)

        model_name = resolve_runtime_model_name(self.config, prefer_synthesis=True)
        async for chunk in run_multimodal_gate(
            history,
            model_name,
            user_id=self._runtime_user_id(),
            conversation_id=self.conversation_id,
        ):
            yield chunk
            if chunk.get("status") == "error" and not chunk.get("type"):
                return

        # 1. Prepare LLM
        tools = await self._resolve_runtime_tools_from_config()
        tools = self._apply_knowledge_fallback_budget(tools)
        for event in self._tool_resolution_log_events():
            yield event

        # 2. Build Messages
        system_content = self.config.system_prompt or ""
        grounding_action = (
            self.debug_options.get("grounding_action")
            if self._grounding_enabled()
            else None
        )
        if isinstance(grounding_action, dict) and grounding_action.get("type") == "method":
            system_content = (
                "【安全回答模式】本轮只提供查询步骤、分析框架或排查方法；"
                "不得输出未经工具核实的具体数据、状态、排名或动态事实。\n\n"
                f"{system_content}"
            )
        route_hint = AssistantPrompts.turn_decision_context(self.turn_decision)
        if route_hint:
            system_content = f"{route_hint}\n\n{system_content}"

        from app.services.ai.session_tool_artifact import (
            build_session_tool_artifact_context_message,
            filter_tools_for_reusable_result,
            insert_session_tool_artifact_context,
            load_session_tool_artifact,
        )

        _user_q_for_artifact = user_query
        _preferred_reusable_result_id = str(
            getattr(self.turn_decision, "reusable_result_id", None) or ""
        ).strip() or None
        _force_reuse = (
            str(getattr(self.turn_decision, "reusable_result_mode", "none") or "none")
            .strip()
            .lower()
            == "reuse"
        )
        _session_artifact = await load_session_tool_artifact(
            self._runtime_user_id(),
            self.conversation_id,
            preferred_result_id=_preferred_reusable_result_id,
        )
        session_artifact_context = build_session_tool_artifact_context_message(
            _session_artifact,
            user_question=_user_q_for_artifact,
            force_reuse=_force_reuse,
        )
        tools = filter_tools_for_reusable_result(
            tools,
            user_question=_user_q_for_artifact,
            artifact=_session_artifact,
            force_reuse=_force_reuse,
        )

        from app.services.ai.time_anchor import append_time_anchor_for_user_question

        system_content = append_time_anchor_for_user_question(
            system_content,
            user_query,
        )
        tools = filter_redundant_time_tools(tools, system_content)

        # 3. Execution Mode Selection
        if not tools:
            # --- Simple Mode (No Tools) ---
            # AgentService 已按最终模型的 history_budget 完成窗口选择和摘录。
            # 这里不能再次按固定条数截断，否则会把平台注入的早前对话摘录丢掉。
            pruned_history = history
            runtime_messages = [SystemMessage(content=system_content)]
            runtime_messages.extend(convert_history_to_messages(pruned_history, strip_thought=True))
            if session_artifact_context:
                runtime_messages = insert_session_tool_artifact_context(
                    runtime_messages,
                    HumanMessage(content=session_artifact_context),
                )
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
                            yield {
                                "type": "answer_delta",
                                "content": chunk.content,
                                "phase": "synthesis",
                            }
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
            user_name=self._runtime_user_name(),
            user_info=self.user_info,
            conversation_id=self.conversation_id,
            tools=tools,
        )
        # AgentService 已按最终模型的 history_budget 完成窗口选择和摘录；
        # runner 只消费这份上下文，不再维护另一套固定条数上限。
        pruned_history = history
        runtime_messages = [SystemMessage(content=system_content)]
        runtime_messages.extend(convert_history_to_messages(pruned_history, strip_thought=True))
        if session_artifact_context:
            runtime_messages = insert_session_tool_artifact_context(
                runtime_messages,
                HumanMessage(content=session_artifact_context),
            )
        runtime_messages = normalize_messages_for_llm(runtime_messages)

        # --- ReAct Mode (With Tools) ---
        from app.services.config_service import ConfigService
        max_steps_str = await ConfigService.get("agent_max_iterations")
        MAX_STEPS = int(max_steps_str) if max_steps_str else 5

        from app.services.ai.memory_recall_policy import (
            MEMORY_SEARCH_CORRECTION_MSG,
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
                    user_query
                )
            except Exception:
                recall_query_pending = looks_like_cross_session_recall_query(
                    user_query
                )

        native_system_content = system_content
        if recall_query_pending:
            native_system_content = (
                f"{MEMORY_SEARCH_CORRECTION_MSG}\n\n"
                f"{native_system_content}"
            )

        # 工具预检（Tool Preflight）：由本轮已绑定工具的 name+description 与问题相关度驱动，
        # 识别该不该用工具、用哪个，并按配置力度促发模型调用（记忆类有专门便签，故跳过）。
        # 模式 agent_tool_preflight_mode：off=关闭；soft=普通工具注入便签；hard=便签+首步强制调用。
        # 证据型只读工具即使在 soft 模式也必须首步取证，避免模型凭上下文直接生成动态事实。
        preflight_tool_choice = None
        _preflight_user_query = user_query
        from app.services.ai.tool_nudge_policy import (
            is_automatic_delivery_context,
            is_tool_meta_query,
            looks_like_explicit_user_question_request,
        )

        _preflight_ctx = self._ensure_agent_context()
        grounding_request_decision = self._resolve_grounding_request_decision(_preflight_user_query)
        grounding_enabled = self._grounding_enabled()
        turn_grounding_requirement = (
            self._resolve_turn_grounding_requirement(_preflight_user_query, _preflight_ctx)
            if grounding_enabled
            else FactRequirement(required=False, accepted_types=frozenset())
        )
        grounding_requires_tool = (
            turn_grounding_requirement.required
            and not is_tool_meta_query(_preflight_user_query)
        )
        retry_evidence_types = (
            self._parse_grounding_retry_evidence_types(
                self.debug_options.get("grounding_action")
            )
            if grounding_enabled
            else frozenset()
        )
        interactive_question_allowed = not is_automatic_delivery_context(
            self.user_info,
            self.debug_options,
        )
        explicit_user_question_requested = (
            interactive_question_allowed
            and looks_like_explicit_user_question_request(_preflight_user_query)
            and any(tool.name == "ask_user_question" for tool in tools)
        )
        preflight_mode = "soft"
        grounding_block_mode = self._normalize_grounding_block_mode(
            self.debug_options.get("grounding_block_mode")
        )
        evidence_contracts: tuple[dict[str, Any], ...] = ()
        try:
            if explicit_user_question_requested or not recall_query_pending:
                preflight_mode = str(
                    await ConfigService.get("agent_tool_preflight_mode", "soft") or "soft"
                ).strip().lower()
                if (
                    grounding_enabled
                    and "grounding_block_mode" not in self.debug_options
                ):
                    try:
                        grounding_block_mode = self._normalize_grounding_block_mode(
                            await ConfigService.get(
                                "agent_grounding_block_mode",
                                "strict_buffer",
                            )
                        )
                    except Exception:
                        grounding_block_mode = "strict_buffer"
                if (
                    explicit_user_question_requested
                    or grounding_requires_tool
                    or preflight_mode not in {"off", "false", "0", "none"}
                ):
                    from app.services.ai.tool_nudge_policy import (
                        ToolNudge,
                        resolve_tool_nudge,
                        resolve_tool_nudge_plan,
                    )

                    (
                        available_sub_agent_names,
                        sub_agent_candidates_by_capability,
                    ) = await self._resolve_available_sub_agent_delegation_info()
                    matching_retry_tool = self._select_grounding_retry_tool(
                        tools,
                        retry_evidence_types,
                    )
                    evidence_plan = None
                    if matching_retry_tool is not None:
                        tool_nudge = ToolNudge(
                            tool_name=matching_retry_tool.name,
                            score=1.0,
                            message=(
                                "【重新取证要求】必须先调用该工具取得匹配的外部事实凭证，"
                                "再根据真实结果回答；工具失败时不得补写具体事实。"
                            ),
                            force_first_call=True,
                        )
                    else:
                        evidence_plan = resolve_tool_nudge_plan(
                            _preflight_user_query,
                            tools,
                        )
                        if evidence_plan is not None:
                            evidence_contracts = tuple(
                                contract.to_dict()
                                for contract in evidence_plan.evidence_contracts
                            )
                            tool_nudge = replace(
                                evidence_plan.primary,
                                message=evidence_plan.message,
                            )
                        else:
                            tool_nudge = resolve_tool_nudge(
                                user_query,
                                tools,
                                available_sub_agent_names=available_sub_agent_names,
                                sub_agent_candidates_by_capability=sub_agent_candidates_by_capability,
                                semantic_intent=getattr(self.turn_decision, "semantic_intent", None),
                                semantic_confidence=getattr(self.turn_decision, "semantic_confidence", None),
                                turn_intent=self.turn_decision.semantic_intent,
                                request_decision=grounding_request_decision,
                                turn_decision=self.turn_decision,
                                exclude_tools=(
                                    {"ask_user_question"}
                                    if not interactive_question_allowed
                                    else None
                                ),
                                allow_explicit_question=interactive_question_allowed,
                            )
                    if tool_nudge is None and grounding_requires_tool:
                        capability_name = next(
                            (
                                capability
                                for evidence_type, capability in {
                                    EvidenceType.INTERNAL_DATA: "data_query",
                                    EvidenceType.INTERNAL_KNOWLEDGE: "knowledge_base",
                                    EvidenceType.PUBLIC_WEB: "web_search",
                                    EvidenceType.RUNTIME_STATE: "runtime_tool",
                                    EvidenceType.USER_FILE: "file_read",
                                    EvidenceType.CONVERSATION_MEMORY: "memory_search",
                                }.items()
                                if evidence_type in turn_grounding_requirement.accepted_types
                            ),
                            None,
                        ) or {
                            RequestCapability.DATA_QUERY: "data_query",
                            RequestCapability.KNOWLEDGE_SEARCH: "knowledge_base",
                        }.get(grounding_request_decision.capability)
                        raw_candidates = (
                            sub_agent_candidates_by_capability.get(capability_name)
                            if capability_name
                            else None
                        )
                        candidates: List[str] = []
                        if isinstance(raw_candidates, list):
                            candidates = [str(name).strip() for name in raw_candidates if str(name).strip()]
                        elif raw_candidates:
                            name = str(raw_candidates).strip()
                            if name:
                                candidates = [name]
                        has_delegate_tool = any(tool.name == "sub_agent_call" for tool in tools)
                        if candidates and has_delegate_tool and capability_name in {
                            "data_query",
                            "knowledge_base",
                        }:
                            from app.services.ai.tool_nudge_policy import (
                                build_semantic_sub_agent_nudge_message,
                            )

                            intent_label = (
                                "ChatBI 业务数据、指标或资产查询"
                                if capability_name == "data_query"
                                else "内部制度、SOP或操作规程查询"
                            )
                            tool_nudge = ToolNudge(
                                tool_name="sub_agent_call",
                                score=1.0,
                                message=(
                                    "【事实取证要求】本轮回答依赖外部事实。"
                                    + build_semantic_sub_agent_nudge_message(
                                        capability=capability_name,
                                        candidates=candidates,
                                        intent_label=intent_label,
                                    )
                                ),
                                force_first_call=True,
                            )
                    if tool_nudge is not None:
                        native_system_content = f"{tool_nudge.message}\n\n{native_system_content}"
                        force_applied = False
                        if (
                            grounding_requires_tool
                            or preflight_mode == "hard"
                            or tool_nudge.should_force_first_call
                        ):
                            preflight_tool_choice = self._build_preflight_tool_choice(
                                tool_nudge.recommended_force_mode()
                            )
                            force_applied = preflight_tool_choice is not None
                        evidence_metadata = self._build_tool_preflight_evidence_metadata(
                            tool_nudge,
                            tools,
                            evidence_contracts=(
                                list(evidence_plan.evidence_contracts)
                                if evidence_plan is not None
                                else None
                            ),
                            grounding_enabled=grounding_enabled,
                        )
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
                            "force_first_call": force_applied,
                            "grounding_block_mode": grounding_block_mode,
                            **evidence_metadata,
                            "tool_metadata": (
                                tool_nudge.metadata.to_dict()
                                if tool_nudge.metadata is not None
                                else None
                            ),
                        }
        except Exception as preflight_err:
            logger.warning("[ToolPreflight] Failed to resolve tool preflight: %s", preflight_err)
            from app.services.ai.tool_nudge_policy import resolve_evidence_tool_fallback_nudge

            fallback_nudge = resolve_evidence_tool_fallback_nudge(
                _preflight_user_query,
                tools,
            )
            if fallback_nudge is not None:
                native_system_content = (
                    f"{fallback_nudge.message}\n\n{native_system_content}"
                )
                preflight_tool_choice = self._build_preflight_tool_choice(
                    fallback_nudge.recommended_force_mode()
                )
                force_applied = preflight_tool_choice is not None
                evidence_metadata = self._build_tool_preflight_evidence_metadata(
                    fallback_nudge,
                    tools,
                    grounding_enabled=grounding_enabled,
                )
                yield {
                    "type": "log",
                    "id": f"tool_preflight_fallback_{uuid.uuid4().hex[:8]}",
                    "title": "工具预检异常：已启用证据保护",
                    "details": (
                        f"工具预检暂时异常，已锁定证据工具「{fallback_nudge.tool_name}」；"
                        "必须先取得本轮工具结果，再回答动态事实。"
                    ),
                    "status": "warning",
                    "category": "tool_preflight",
                    "force_first_call": force_applied,
                    "grounding_block_mode": grounding_block_mode,
                    "preflight_fallback": True,
                    **evidence_metadata,
                    "tool_metadata": (
                        fallback_nudge.metadata.to_dict()
                        if fallback_nudge.metadata is not None
                        else None
                    ),
                }

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
                    grounding_block_mode=grounding_block_mode,
                    evidence_contracts=evidence_contracts,
                    candidate_answer_enabled=self._should_enable_candidate_answer_streaming(
                        grounding_enabled=grounding_enabled,
                        grounding_requirement=turn_grounding_requirement,
                        run_data_guard=self._should_run_data_hallucination_guard(
                            _preflight_user_query
                        ),
                        evidence_contracts=evidence_contracts,
                    ),
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

        (
            enabled_raw,
            threshold_raw,
            ping_pong_raw,
            global_limit_raw,
        ) = await asyncio.gather(
            ConfigService.get("agent_tool_loop_detection_enabled", "true"),
            ConfigService.get("agent_tool_loop_fuse_threshold", "3"),
            ConfigService.get(
                "agent_tool_loop_ping_pong_threshold",
                str(DEFAULT_PING_PONG_THRESHOLD),
            ),
            ConfigService.get(
                "agent_tool_loop_global_limit",
                str(DEFAULT_GLOBAL_LIMIT),
            ),
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
        grounding_block_mode: str = "strict_buffer",
        evidence_contracts: tuple[dict[str, Any], ...] = (),
        candidate_answer_enabled: bool = False,
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
                    candidate_answer_enabled=candidate_answer_enabled,
                )
                state["execution_backend"] = self._execution_backend
                state["grounding_block_mode"] = self._normalize_grounding_block_mode(
                    grounding_block_mode
                )
                state["evidence_contracts"] = [
                    dict(contract) for contract in evidence_contracts
                ]
                self._session_artifact_turn = {
                    "user_question": "",
                    "trace_id": self.trace_id,
                    "best": None,
                }
                state["user_query"] = next(
                    (
                        str(getattr(message, "content", ""))
                        for message in reversed(runtime_messages)
                        if isinstance(message, HumanMessage)
                    ),
                    "",
                )
                self._session_artifact_turn["user_question"] = state["user_query"]
                from app.services.ai.business_confirmation import arm_cancel_confirmation_gate

                arm_cancel_confirmation_gate(state["user_query"])
                interrupted = False
                try:
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
                except Exception as stream_exc:
                    fuse_message = extract_tool_loop_fuse_message(stream_exc)
                    if fuse_message is None:
                        raise
                    async for chunk in self._stream_tool_loop_fuse_convergence(
                        state=state,
                        native_model=native_model,
                        fuse_message=fuse_message,
                    ):
                        yield chunk

                # A 项：流结束后捕获本轮工具元数据，供保存点跨轮持久化。
                self._last_turn_tool_meta = state

                if self.conversation_id:
                    from app.services.ai.session_tool_artifact import persist_turn_artifact_candidate
                    from app.services.ai.reusable_result import build_reusable_result_status_event

                    saved_meta = await persist_turn_artifact_candidate(
                        user_id=self._runtime_user_id(),
                        conversation_id=self.conversation_id,
                        turn_state=getattr(self, "_session_artifact_turn", None),
                        clear_if_empty=not interrupted,
                    )
                    if saved_meta:
                        yield build_reusable_result_status_event(
                            status="saved",
                            payload=saved_meta,
                        )
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
        from app.services.ai.runtime.agentscope.agent_runtime import (
            build_runtime_middlewares,
            load_injection_config,
        )

        context_config = await load_context_config()
        model_config = await build_model_config(
            config=self.config,
            primary_model_name=primary_model_name,
        )
        injection_config = await load_injection_config()
        workspace = await get_local_workspace(
            user_id=self._runtime_user_id(),
            user_name=self._runtime_user_name(),
            user_info=self.user_info,
            conversation_id=self.conversation_id,
            skills_custom=bool(getattr(self.config, "skills_custom", False)),
            allowed_global_skills=list(getattr(self.config, "skills", None) or []),
        )
        # 仅挂载 agent 后端配置的工具；已配置的 Bash/Read 等换成会话 workdir 版本，不额外注入未绑定的内置工具。
        tools = await bind_configured_tools_to_workspace(
            workspace,
            tools,
            user_info=self.user_info,
        )
        self._execution_backend = get_workspace_execution_backend(workspace)
        toolkit = AgentScopeToolConsumer(builder=build_toolkit).consume_specs(
            tools,
            approval_mode=self.permission_options.get("approval_mode"),
            loop_detector=loop_detector,
            user_id=self._runtime_user_id(),
        )
        middlewares = build_runtime_middlewares(
            user_id=self._runtime_user_id(),
            conversation_id=self.conversation_id,
            agent_name=self._runtime_agent_name(),
            trace_id=self.trace_id,
        )
        return Agent(
            name=self._runtime_agent_name(),
            system_prompt=system_content,
            model=native_model,
            toolkit=toolkit,
            state=restored_state,
            offloader=get_workspace_offloader(workspace),
            model_config=model_config,
            context_config=context_config,
            injection_config=injection_config,
            react_config=ReActConfig(max_iters=max_steps),
            middlewares=middlewares,
        )

    @staticmethod
    def _latest_user_runtime_messages(
        runtime_messages: List[BaseMessage],
    ) -> List[BaseMessage]:
        latest: List[BaseMessage] = []
        for message in reversed(runtime_messages):
            if isinstance(message, HumanMessage):
                latest.append(message)
                continue
            if latest:
                break
        return list(reversed(latest))

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
            process_narration_events.on_tool_result_end(state)
            tool_id = getattr(event, "tool_call_id", "")
            tool_name = tool_names.get(tool_id, "")

            # 工具封装层先生成内部凭证 ID，AgentScope 结果事件才带有模型侧
            # tool_call_id。此处完成一次性对齐，避免账本收据与执行链路脱节。
            internal_call_id = tool_call_id_from_metadata(event)
            if internal_call_id and internal_call_id != tool_id:
                ledger = getattr(self, "_evidence_ledger", None)
                if ledger is not None:
                    ledger.rebind_call_id(internal_call_id, str(tool_id or ""))

            # 方案二：ghost 工具检测 —— 若该工具在 TOOL_CALL_START 时已被标记为未知工具，
            # 跳过正常的 observation 处理，只给用户输出错误提示。
            # LLM 侧已由 AgentScope 框架在内部把 TOOL_RESULT_END 写入 context，
            # 下一轮 LLM 会看到工具调用失败，此处不需要额外注入。
            ghost_tool_ids: set = state.get("ghost_tool_ids") or set()
            if tool_id in ghost_tool_ids:
                logger.warning(
                    "[ToolGuard] Ghost tool result received for tool='%s' tool_id=%s, suppressing observation.",
                    tool_name,
                    tool_id,
                )
                yield {
                    "type": "log",
                    "id": tool_id,
                    "title": f"⚠️ 工具调用已拦截: {tool_name}",
                    "details": (
                        f"工具 `{tool_name}` 未在本智能体注册，调用已被平台拦截。"
                        f"模型已收到错误反馈，将重新生成回答。"
                    ),
                    "status": "error",
                    "category": "tool",
                }
                return

            raw_args = tool_args_text.get(tool_id, "")
            tool_args = _resolve_agentscope_tool_args(agent, tool_id, raw_args)
            if tool_name == "browser_fill" and isinstance(tool_args, dict):
                from app.services.ai.browser.browser_policy import redact_browser_arguments

                tool_args = redact_browser_arguments({**tool_args, "sensitive": True})
            output = tool_outputs.get(tool_id, "")
            from app.services.ai.runtime.agentscope.browser_events import (
                build_browser_refresh_event,
                build_browser_session_event,
            )

            browser_event = build_browser_session_event(tool_name, output)
            if browser_event:
                yield browser_event
            browser_refresh_event = build_browser_refresh_event(tool_name, output)
            if browser_refresh_event:
                yield browser_refresh_event
            if tool_data.get(tool_id):
                output = {
                    "text": output,
                    "data_blocks": tool_data.get(tool_id, []),
                }
            tool_result_state = (
                state.get("tool_result_states", {}).get(tool_id)
                or getattr(event, "state", None)
            )
            duration_ms = (time.time() - tool_started_at.get(tool_id, time.time())) * 1000
            target_tool = next((t for t in tools if t.name == tool_name), None)
            result = self._build_tool_observation(
                tool_id=tool_id,
                tool_name=tool_name,
                tool_args=tool_args,
                tool_output=output,
                duration_tool=duration_ms,
                target_tool=target_tool,
                tool_index=0,
                tool_result_state=tool_result_state,
            )
            if result.get("log"):
                yield result["log"]
            if result.get("business_confirmation"):
                yield result["business_confirmation"]
            if result.get("user_question"):
                from app.services.ai.user_question import persist_user_question_event

                question_event = result["user_question"]
                try:
                    await persist_user_question_event(
                        event=question_event,
                        user_id=self._runtime_user_id(),
                        conversation_id=self.conversation_id or "",
                    )
                except Exception:
                    logger.exception("Failed to persist pending user question")
                    yield {
                        "type": "error",
                        "status": "error",
                        "content": "无法保存待回答问题，请稍后重试。",
                    }
                    return
                yield question_event
            if result.get("citation"):
                yield result["citation"]
            if result.get("trace"):
                self.trace_buffer.append(result["trace"])



        async def on_text_block_delta(event: Any) -> AsyncGenerator[Dict[str, Any], None]:
            delta = sanitize_assistant_stream_text(str(getattr(event, "delta", "")))
            if not delta:
                return
            for chunk in process_narration_events.on_text_delta(state, delta):
                async for item in self._yield_process_narration_chunk(state, chunk):
                    yield item

        async for event in event_stream:
            event_type = str(getattr(event, "type", ""))
            if event_type == "MODEL_CALL_START":
                for chunk in process_narration_events.on_model_call_start(state):
                    async for item in self._yield_process_narration_chunk(state, chunk):
                        yield item
            elif event_type == "TOOL_CALL_START":
                for chunk in process_narration_events.on_tool_call_start(
                    state,
                    tool_name=getattr(event, "tool_call_name", None),
                ):
                    async for item in self._yield_process_narration_chunk(state, chunk):
                        yield item
            elif event_type == "MODEL_CALL_END":
                self._record_agent_scope_model_call(
                    event,
                    state=state,
                    native_model=native_model,
                )
                for chunk in process_narration_events.on_model_call_end(state):
                    async for item in self._yield_process_narration_chunk(state, chunk):
                        yield item
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

    async def _yield_process_narration_chunk(
        self,
        state: Dict[str, Any],
        chunk: Dict[str, Any],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        if chunk.get("type") == "answer_delta" and str(chunk.get("content") or "").strip():
            if state.get("used_tools") and not state.get("synthesis_log_emitted"):
                state["synthesis_log_emitted"] = True
                yield {
                    "type": "log",
                    "id": f"synthesis_native_{uuid.uuid4().hex[:8]}",
                    "title": "📝 汇总工具结果",
                    "details": "已获取所需数据，正在组织语言...",
                    "status": "success",
                }
            if not state.get("gen_start_emitted"):
                state["gen_start_emitted"] = True
                yield {
                    "type": "log",
                    "id": f"gen_start_{uuid.uuid4().hex[:8]}",
                    "title": "✨ 开始生成回复",
                    "status": "success",
                }
        if chunk.get("type") == "process_narration_promote" and str(chunk.get("content") or "").strip():
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
            if not state.get("gen_start_emitted"):
                state["gen_start_emitted"] = True
                yield {
                    "type": "log",
                    "id": f"gen_start_{uuid.uuid4().hex[:8]}",
                    "title": "✨ 开始生成回复",
                    "status": "success",
                }
        yield chunk

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
        if not state.get("gen_start_emitted"):
            state["gen_start_emitted"] = True
            yield {
                "type": "log",
                "id": f"gen_start_{uuid.uuid4().hex[:8]}",
                "title": "✨ 开始生成回复",
                "status": "success",
            }
        state["full_content"] = (state.get("full_content") or "") + text
        yield {"type": "answer_delta", "content": text, "phase": "synthesis"}

    async def _reconcile_reply_after_stream(
        self,
        *,
        agent: Any,
        state: Dict[str, Any],
        native_model: Any,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """流结束后：AgentState 与已发送 SSE 对齐，不足则 synthesis。"""
        from app.core.cancellation import current_task_cancelling

        if current_task_cancelling():
            return
        for chunk in process_narration_events.on_model_call_end(state):
            async for item in self._yield_process_narration_chunk(state, chunk):
                yield item
        streamed = state.get("full_content") or ""
        agent_text = (
            extract_latest_assistant_text(agent, include_thinking=False)
            if agent is not None
            else ""
        )

        synthesis_agent_text = process_narration_events.extract_agent_answer_after_process_narration(
            state,
            agent_text,
        )
        gap = compute_stream_reconcile_gap(streamed, synthesis_agent_text)
        if gap.strip():
            logger.info(
                "[AssistantAgentRunner] Stream reconcile gap chars=%d streamed=%d agent=%d "
                "streamed_head=%r streamed_tail=%r agent_head=%r agent_tail=%r narration=%d",
                len(gap),
                len(streamed),
                len(agent_text),
                streamed[:90].replace("\n", "\\n"),
                streamed[-90:].replace("\n", "\\n"),
                synthesis_agent_text[:90].replace("\n", "\\n"),
                synthesis_agent_text[-90:].replace("\n", "\\n"),
                len(str(state.get("process_narration") or "")),
            )
            async for chunk in self._emit_reply_text_chunks(state, gap):
                yield chunk
            return

        if not needs_tool_synthesis_fallback(
            streamed,
            synthesis_agent_text,
            used_tools=bool(state.get("used_tools")),
            tool_names=state.get("tool_names"),
            tool_outputs=state.get("tool_outputs"),
            tool_result_states=state.get("tool_result_states"),
        ):
            if not streamed.strip() and not agent_text.strip():
                if (
                    state.get("used_tools")
                    and not build_tool_review_lines(
                        state.get("tool_names"),
                        state.get("tool_outputs"),
                        tool_result_states=state.get("tool_result_states"),
                    )
                ):
                    async for chunk in self._stream_general_synthesis_fallback(
                        state=state,
                        native_model=native_model,
                    ):
                        yield chunk
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

    async def _stream_tool_loop_fuse_convergence(
        self,
        *,
        state: Dict[str, Any],
        native_model: Any,
        fuse_message: str,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """工具环熔断后：禁止继续调工具，强制一轮文本收敛回答。"""
        yield {
            "type": "log",
            "id": f"tool_loop_fuse_{uuid.uuid4().hex[:8]}",
            "title": "工具循环已熔断",
            "details": "已停止继续调用工具，改为基于已有信息直接回答。",
            "status": "warning",
            "category": "tool",
        }
        tool_names: Dict[str, str] = state.get("tool_names", {})
        tool_outputs: Dict[str, str] = state.get("tool_outputs", {})
        review_lines = build_tool_review_lines(
            tool_names,
            tool_outputs,
            tool_result_states=state.get("tool_result_states"),
        )
        constraint = (
            "【系统约束·工具循环熔断】禁止再调用任何工具。"
            "请仅使用系统提示（含时间锚点，若有）与下列已有执行结果直接回答用户；"
            "信息不足时请明确说明限制，不要猜测未核实的事实。\n"
            f"熔断原因：{str(fuse_message or '').strip()[:500]}"
        )
        if review_lines:
            execution_review = f"{constraint}\n\n【执行过程回顾】\n" + "\n".join(review_lines)
        else:
            execution_review = (
                f"{constraint}\n\n【执行过程回顾】\n"
                "- 本轮尚未取得有效工具结果；请优先引用系统提示中的时间锚点与已知上下文作答。"
            )

        user_query = str(state.get("user_query") or "")
        if not state.get("synthesis_fb_log_emitted"):
            state["synthesis_fb_log_emitted"] = True
            yield {
                "type": "log",
                "id": f"synthesis_fb_{uuid.uuid4().hex[:8]}",
                "title": "📝 汇总已有信息",
                "details": "正在基于已有信息生成最终回答...",
                "status": "success",
            }

        emitted_any = False
        try:
            llm = await AgentConfigProvider.get_synthesis_llm(streaming=True, config=self.config)
            messages = normalize_messages_for_llm([
                SystemMessage(content=str(state.get("system_content") or self.config.system_prompt or "")),
                HumanMessage(
                    content=self._build_synthesis_user_message(user_query, execution_review)
                ),
            ])
            async for chunk in llm.astream(messages):
                content = sanitize_assistant_stream_text(str(getattr(chunk, "content", None) or ""))
                if not content:
                    continue
                emitted_any = True
                if not state.get("content_emitted"):
                    state["content_emitted"] = True
                    yield {
                        "type": "log",
                        "id": f"gen_start_{uuid.uuid4().hex[:8]}",
                        "title": "✨ 开始生成回复",
                        "status": "success",
                    }
                state["full_content"] = (state.get("full_content") or "") + content
                yield {"type": "answer_delta", "content": content, "phase": "synthesis"}
        except Exception as synthesis_err:
            logger.error(
                "[AssistantAgentRunner] Tool-loop fuse convergence failed: %s",
                synthesis_err,
                exc_info=True,
            )

        if not emitted_any and not str(state.get("full_content") or "").strip():
            fallback = (
                "检测到工具调用出现循环并已安全中止。"
                "请换一种问法重试，或直接说明你需要的具体日期范围。"
            )
            state["full_content"] = fallback
            yield {"type": "answer_delta", "content": fallback, "phase": "synthesis"}

    async def _stream_general_synthesis_fallback(
        self,
        *,
        state: Dict[str, Any],
        native_model: Any,
        append_after_partial: bool = False,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from app.core.cancellation import current_task_cancelling

        if current_task_cancelling():
            return
        tool_names: Dict[str, str] = state.get("tool_names", {})
        tool_outputs: Dict[str, str] = state.get("tool_outputs", {})
        review_lines = build_tool_review_lines(
            tool_names,
            tool_outputs,
            tool_result_states=state.get("tool_result_states"),
        )
        if not review_lines:
            logger.warning("[AssistantAgentRunner] Synthesis skipped: no tool review lines")
            state["full_content"] = (state.get("full_content") or "") + GENERIC_SYNTHESIS_EMPTY_FALLBACK
            yield {
                "type": "answer_delta",
                "content": GENERIC_SYNTHESIS_EMPTY_FALLBACK,
                "phase": "synthesis",
            }
            return

        user_query = str(state.get("user_query") or "")
        execution_review = "【执行过程回顾】\n" + "\n".join(review_lines)
        logger.warning(
            "[AssistantAgentRunner] Synthesis fallback after stream reconcile tools=%d",
            len(review_lines),
        )

        if append_after_partial:
            yield {"type": "answer_delta", "content": "\n\n", "phase": "synthesis"}

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
                yield {"type": "answer_delta", "content": content, "phase": "synthesis"}
        except Exception as exc:
            logger.error("[AssistantAgentRunner] Synthesis fallback failed: %s", exc, exc_info=True)

        if not emitted_any:
            logger.warning("[AssistantAgentRunner] Synthesis produced no visible text")
            state["full_content"] = (state.get("full_content") or "") + GENERIC_SYNTHESIS_EMPTY_FALLBACK
            yield {
                "type": "answer_delta",
                "content": GENERIC_SYNTHESIS_EMPTY_FALLBACK,
                "phase": "synthesis",
            }
            return

        synthesis_tokens = extract_tokens_from_message(last_synthesis_chunk)
        if synthesis_tokens["prompt_tokens"] or synthesis_tokens["completion_tokens"]:
            # 本函数已自行记录一条 synthesis trace；必须置位该标志，
            # 否则外层记录器（_stream_agentscope_native_events 末尾）会再补一条内容相同的
            # synthesis step，造成同一轮合成被记录两遍、step_counter 重复累加。
            state["synthesis_recorded"] = True
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
        tools = self._apply_knowledge_fallback_budget(tools)
        native_model_handle = await AgentConfigProvider.get_configured_llm(
            streaming=True,
            config=self.config,
        )
        native_model = getattr(native_model_handle, "native_model", None)
        if native_model is None:
            raise RuntimeError("当前模型适配器未提供 AgentScope native_model，无法恢复挂起执行。")

        from agentscope.state import AgentState

        restored_state = AgentState.model_validate(pending.snapshot.agent_state)
        system_content = str(ctx.get("system_content", ""))
        tools = filter_redundant_time_tools(tools, system_content)
        agent = await self._build_native_agent(
            native_model=native_model,
            tools=tools,
            system_content=system_content,
            max_steps=int(ctx.get("max_steps", 5)),
            restored_state=restored_state,
            primary_model_name=str(getattr(native_model, "model", self.config.model_name) or ""),
            loop_detector=loop_detector,
        )
        state = pending.state or dict(pending.snapshot.stream_state or {})
        state.setdefault("execution_backend", self._execution_backend)
        if "tool_data" not in state:
            state["tool_data"] = {}
        return agent, tools, native_model, state

    async def _resolve_runtime_tools_from_config(self) -> List[RuntimeToolSpec]:
        configured_tools = self.config.tools or []
        provider = RegistryToolProvider(
            legacy_converter=runtime_tool_spec_from_legacy_tool,
            evidence_attacher=ToolRegistry._attach_evidence_metadata,
        )
        system_tools = list(ToolRegistry.get_system_implicit_tools())
        if is_main_general_agent(self.config):
            sub_agent_tool = await provider.get_implicit_tool("sub_agent_call")
            if sub_agent_tool:
                system_tools.append(sub_agent_tool)
                batch_sub_agent_tool = await provider.get_implicit_tool("sub_agent_batch_call")
                if batch_sub_agent_tool:
                    system_tools.append(batch_sub_agent_tool)
                todo_tool = await provider.get_implicit_tool("todo_write")
                if todo_tool:
                    system_tools.append(todo_tool)
        resolved = await resolve_tool_capabilities(
            configured_tools,
            implicit_tools=system_tools,
            provider=provider,
        )
        self._last_tool_resolution = resolved
        from app.services.ai.runtime.agentscope.tool_timeout import (
            apply_configured_agent_tool_timeout,
        )

        tools = await apply_configured_agent_tool_timeout(
            resolved.specs,
            agent_timeout=getattr(self.config, "toolcall_timeout_seconds", None),
        )
        from app.core.context import get_current_agent_context
        from app.services.ai.tools.session_status import runtime_tool_capability_from_spec

        context = get_current_agent_context()
        if context is not None:
            context.runtime_tool_capabilities = [
                runtime_tool_capability_from_spec(spec) for spec in tools
            ]
        return tools

    def _apply_knowledge_fallback_budget(
        self,
        tools: List[RuntimeToolSpec],
    ) -> List[RuntimeToolSpec]:
        """对目录低置信兜底路径的知识库工具施加单轮一次调用预算。"""
        if not bool(getattr(self.turn_decision, "knowledge_fallback_allowed", False)):
            return tools

        limited: List[RuntimeToolSpec] = []
        used = False
        for tool in tools or []:
            if tool.name != "search_knowledge_base":
                limited.append(tool)
                continue

            original_callable = tool.callable

            async def invoke_once(
                _original_callable=original_callable,
                **kwargs: Any,
            ) -> Any:
                nonlocal used
                if used:
                    return (
                        '{"status":"error","message":"本轮只允许调用一次 '
                        'search_knowledge_base，已跳过重复检索。"}'
                    )
                used = True
                result = _original_callable(**kwargs)
                if inspect.isawaitable(result):
                    result = await result
                return result

            limited.append(replace(tool, callable=invoke_once))
        return limited

    @staticmethod
    def _record_external_execution_evidence(
        *,
        ledger: EvidenceLedger,
        tools: List[RuntimeToolSpec],
        execution_results: List[Any],
    ) -> None:
        """Record successful client-side tool results using server-owned metadata."""
        tools_by_name = {tool.name: tool for tool in tools}
        for result in execution_results:
            state = getattr(result, "state", "")
            state_value = getattr(state, "value", state)
            if str(state_value or "").strip().lower() != "success":
                continue
            tool_name = str(getattr(result, "name", "") or "")
            tool = tools_by_name.get(tool_name)
            if tool is None or not tool.evidence_types:
                continue
            envelope = build_tool_result_envelope(
                call_id=str(getattr(result, "id", "") or f"{tool_name}:{uuid.uuid4().hex}"),
                producer=tool_name,
                result=getattr(result, "output", None),
                evidence_policy=tool.evidence_policy,
                result_state=state_value,
            )
            ledger.record_envelope(
                envelope,
                evidence_types=tool.evidence_types,
                policy=tool.evidence_policy,
            )

    async def _resume_agentscope_native_stream(
        self,
        *,
        pending: Any,
        resume_event: Any,
        external_execution_results: List[Any] | None = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from app.core.context import set_agent_context

        ctx = self._ensure_agent_context()
        ledger = getattr(self, "_evidence_ledger", None)
        snapshot_receipts = getattr(
            getattr(pending, "snapshot", None),
            "evidence_receipts",
            [],
        )
        if ledger is None and snapshot_receipts:
            ledger = EvidenceLedger.from_snapshot(
                snapshot_receipts,
                user_id=self._runtime_user_id(),
                conversation_id=self.conversation_id,
            )
        if ledger is None and ctx is not None:
            ledger = getattr(ctx, "grounding_evidence_ledger", None)
        if ledger is None:
            ledger = EvidenceLedger(
                user_id=self._runtime_user_id(),
                conversation_id=self.conversation_id,
            )
        self._evidence_ledger = ledger
        if ctx is not None:
            ctx.grounding_evidence_ledger = ledger
            set_agent_context(ctx)
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
                pending_tool_call = getattr(pending, "tool_call", None)
                pending_tool_name = (
                    pending_tool_call.get("name")
                    if isinstance(pending_tool_call, dict)
                    else getattr(pending_tool_call, "name", "")
                )
                grounding_block_mode = self._normalize_grounding_block_mode(
                    state.get(
                        "grounding_block_mode",
                        self.debug_options.get("grounding_block_mode"),
                    )
                )
                strict_tool_evidence_types = self._resolve_current_turn_evidence_types(
                    str(pending_tool_name or ""),
                    tools,
                )
                if external_execution_results:
                    self._record_external_execution_evidence(
                        ledger=ledger,
                        tools=tools,
                        execution_results=external_execution_results,
                    )
                interrupted = False
                user_query = str(state.get("user_query") or "")
                strict_evidence_contracts = self._resolve_resume_evidence_contracts(
                    state,
                    tools,
                )
                self._session_artifact_turn = {
                    "user_question": user_query,
                    "trace_id": self.trace_id,
                    "best": None,
                }
                grounding_enabled = self._grounding_enabled()
                strict_tool_evidence_required = bool(
                    grounding_enabled and strict_tool_evidence_types
                )
                requirement = (
                    self._resolve_turn_grounding_requirement(user_query, ctx)
                    if grounding_enabled
                    else FactRequirement(required=False, accepted_types=frozenset())
                )
                if strict_tool_evidence_required:
                    requirement = replace(
                        requirement,
                        required=True,
                        accepted_types=strict_tool_evidence_types,
                        block_unsupported_facts=True,
                        freshness=FactFreshness.DYNAMIC,
                        allow_conversation_reuse=False,
                        evidence_mode="required",
                        decision_origin="tool_preflight",
                        decision_confidence=1.0,
                    )
                buffer_output = (
                    strict_tool_evidence_required
                    or (
                        grounding_enabled
                        and self._should_buffer_grounding_output(
                            requirement,
                            run_data_guard=False,
                        )
                    )
                )
                buffered_content: List[Dict[str, Any]] = []
                grounding_candidate_text = ""
                try:
                    async for chunk in self._stream_agentscope_native_events(
                        event_stream=agent.reply_stream(resume_event),
                        agent=agent,
                        tools=tools,
                        native_model=native_model,
                        state=state,
                    ):
                        if is_interrupt_sse_chunk(chunk):
                            interrupted = True
                        if buffer_output:
                            grounding_candidate_text = (
                                process_narration_events.accumulate_visible_answer(
                                    grounding_candidate_text,
                                    chunk,
                                )
                            )
                        if (
                            buffer_output
                            and (
                                _is_grounding_bufferable_chunk(chunk)
                                or (
                                    strict_tool_evidence_required
                                    and _is_strict_evidence_bufferable_chunk(chunk)
                                )
                            )
                            and chunk.get("type") not in {"error"}
                        ):
                            stream_strict_chunk = (
                                strict_tool_evidence_required
                                and grounding_block_mode == "stream_with_retraction"
                                and _is_strict_evidence_bufferable_chunk(chunk)
                            )
                            if not stream_strict_chunk:
                                buffered_content.append(chunk)
                            else:
                                yield chunk
                        else:
                            yield chunk
                except Exception as stream_exc:
                    fuse_message = extract_tool_loop_fuse_message(stream_exc)
                    if fuse_message is None:
                        raise
                    async for chunk in self._stream_tool_loop_fuse_convergence(
                        state=state,
                        native_model=native_model,
                        fuse_message=fuse_message,
                    ):
                        yield chunk
                    self._last_turn_tool_meta = state
                    return

                # Resume 也必须更新同一份最终工具元数据；否则保存点可能继续
                # 使用恢复前的旧摘要，把失败/中间结果带入下一轮上下文。
                self._last_turn_tool_meta = state

                if (buffered_content or grounding_candidate_text) and not interrupted:
                    candidate_text = grounding_candidate_text or "".join(
                        str(chunk.get("content") or "")
                        for chunk in buffered_content
                        if not chunk.get("type")
                    )
                    ledger = getattr(
                        self,
                        "_evidence_ledger",
                        EvidenceLedger(
                            user_id=self._runtime_user_id(),
                            conversation_id=self.conversation_id,
                        ),
                    )
                    audit_ledger = (
                        self._current_turn_grounding_ledger(ledger)
                        if strict_tool_evidence_required
                        else ledger
                    )
                    evaluated_requirement = self._refine_unknown_requirement_from_evidence(
                        requirement,
                        user_query=user_query,
                        ledger=audit_ledger,
                    )
                    grounding_audit = GroundingService.audit(
                        requirement=evaluated_requirement,
                        candidate_text=candidate_text,
                        ledger=audit_ledger,
                    )
                    decision = grounding_audit.decision
                    contracts_satisfied, contracts_reason = self._evidence_contracts_satisfied(
                        strict_evidence_contracts,
                        candidate_text=candidate_text,
                        ledger=audit_ledger,
                    )
                    current_turn_evidence_blocked = self._should_block_current_turn_evidence(
                        grounding_decision=decision,
                        contracts_satisfied=contracts_satisfied,
                        contracts_reason=contracts_reason,
                        required_evidence_types=strict_tool_evidence_types,
                    )
                    if strict_tool_evidence_required and (
                        grounding_audit.should_warn or not contracts_satisfied
                    ):
                        if current_turn_evidence_blocked:
                            guidance = GroundingService.guided_response(
                                candidate_text=candidate_text,
                                reason=decision.reason,
                                required_types=frozenset(
                                    strict_tool_evidence_types
                                    or decision.required_evidence_types
                                ),
                                available_types=decision.available_evidence_types,
                                contracts_reason=contracts_reason,
                            )
                            yield {
                                "type": "log",
                                "id": f"grounding_guidance_{uuid.uuid4().hex[:8]}",
                                "title": "已切换为安全说明",
                                "details": (
                                    "当前没有足够可核对依据，未展示未经核实的具体结论；"
                                    "可以补充查询条件后继续。"
                                ),
                                "status": "warning",
                                "category": "grounding",
                                "grounding_downgraded": True,
                                "grounding_decision": self._grounding_decision_metadata(
                                    evaluated_requirement
                                ),
                            }
                            if grounding_block_mode == "stream_with_retraction":
                                yield {
                                    "type": "retraction",
                                    "content": guidance.content,
                                    "grounding_downgraded": True,
                                    "final": True,
                                }
                            else:
                                yield {
                                    "type": "answer_delta",
                                    "content": guidance.content,
                                    "phase": "synthesis",
                                    "grounding_downgraded": True,
                                }
                        else:
                            for buffered_chunk in buffered_content:
                                yield buffered_chunk
                            yield {
                                "type": "log",
                                "id": f"grounding_resume_warning_{uuid.uuid4().hex[:8]}",
                                "title": "事实来源风险提示已追加",
                                "details": (
                                    "本轮已获得工具结果，但回答与结果的关联度不足，"
                                    "已保留回答并提示核对原始来源。"
                                ),
                                "status": "warning",
                                "category": "grounding",
                                "grounding_blocked": False,
                                "grounding_downgraded": True,
                                "grounding_decision": self._grounding_decision_metadata(
                                    evaluated_requirement
                                ),
                            }
                            yield self._build_downgraded_grounding_warning(
                                grounding_audit=grounding_audit,
                                grounding_decision=decision,
                                contracts_reason=contracts_reason,
                            )
                        return
                    if grounding_audit.should_warn:
                        for buffered_chunk in buffered_content:
                            yield buffered_chunk
                        yield {
                            "type": "log",
                            "id": f"grounding_resume_{uuid.uuid4().hex[:8]}",
                            "title": "事实来源风险提示已追加",
                            "details": decision.reason,
                            "status": "warning",
                            "category": "grounding",
                            "grounding_decision": self._grounding_decision_metadata(
                                evaluated_requirement
                            ),
                        }
                        yield grounding_audit.warning_chunk
                    else:
                        for buffered_chunk in buffered_content:
                            yield buffered_chunk

                if self.conversation_id:
                    from app.services.ai.session_tool_artifact import persist_turn_artifact_candidate
                    from app.services.ai.reusable_result import build_reusable_result_status_event

                    saved_meta = await persist_turn_artifact_candidate(
                        user_id=self._runtime_user_id(),
                        conversation_id=self.conversation_id,
                        turn_state=getattr(self, "_session_artifact_turn", None),
                        clear_if_empty=not interrupted,
                    )
                    if saved_meta:
                        yield build_reusable_result_status_event(
                            status="saved",
                            payload=saved_meta,
                        )
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
            external_execution_results=execution_results,
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
        tool_result_state: Any = None,
    ) -> Dict[str, Any]:
        is_error = is_tool_result_error(
            tool_name,
            tool_output,
            result_state=tool_result_state,
        )
        error_reason = extract_tool_result_error_reason(
            tool_name,
            tool_output,
            result_state=tool_result_state,
        )

        if not is_error and target_tool is not None:
            from app.services.ai.session_tool_artifact import consider_turn_artifact_candidate

            consider_turn_artifact_candidate(
                getattr(self, "_session_artifact_turn", None),
                tool_name=tool_name,
                tool_args=tool_args,
                tool_output=tool_output,
                source_type=str(getattr(target_tool, "source_type", "static")),
                permission_scope=str(getattr(target_tool, "permission_scope", "ask")),
            )

        runtime_cfg = getattr(target_tool, "_runtime_config", None)
        t_model = getattr(runtime_cfg, "model_name", self.config.model_name)
        t_temp = getattr(runtime_cfg, "temperature", self.config.temperature)

        # Ensure types for AgentExecutionStep validation (especially when using Mocks in tests)
        if not isinstance(t_model, str): t_model = str(self.config.model_name)
        if not isinstance(t_temp, (int, float)): t_temp = float(self.config.temperature or 0)

        trace_step = AgentExecutionStep(
            step_number=self._increment_step(), event_type="tool_call", agent_name=self.config.agent_name,
            model=t_model, temperature=t_temp, tool_name=tool_name, tool_input=tool_args,
            tool_output=tool_output if isinstance(tool_output, (dict, list)) else {"raw": str(tool_output)},
            execution_time_ms=duration_tool, status="success" if not is_error else "error",
            timestamp=datetime.fromtimestamp(time.time() - duration_tool / 1000)
        )

        if tool_name == "search_knowledge_base" and not is_error:
            from app.services.ai.knowledge_utils import format_knowledge_tool_log_display

            display_output = format_knowledge_tool_log_display(tool_output, max_len=1200)
        else:
            display_output = truncate_for_display(str(tool_output), max_len=500)
        log_event = {
            "type": "log",
            "id": tool_id,
            "title": f"工具完成: {tool_name} ({duration_tool:.0f}ms)",
            "details": display_output,
            "status": "success" if not is_error else "error",
            "category": "tool",
            "model": t_model,
            "temperature": t_temp,
        }
        file_metadata = _build_file_tool_metadata(tool_name, tool_args, tool_output)
        if file_metadata:
            log_event["file_metadata"] = file_metadata
        normalized_result_state = normalize_tool_result_state(tool_result_state)
        if normalized_result_state:
            log_event["tool_result_state"] = normalized_result_state
        if error_reason:
            log_event["error_reason"] = error_reason

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

        from app.services.ai.business_confirmation import build_business_confirmation_sse
        from app.services.ai.user_question import build_user_question_sse

        confirmation_output = tool_output
        if isinstance(tool_output, dict) and "text" in tool_output:
            confirmation_output = tool_output.get("text", tool_output)

        return {
            "index": tool_index,
            "final_tool_message_content": final_tool_message_content,
            "trace": trace_step,
            "log": log_event,
            "citation": citation_event,
            "business_confirmation": None
            if is_error
            else build_business_confirmation_sse(
                tool_name=tool_name,
                tool_output=confirmation_output,
                tool_call_id=tool_id,
            ),
            "user_question": None
            if is_error
            else build_user_question_sse(
                tool_name=tool_name,
                tool_output=confirmation_output,
                tool_call_id=tool_id,
            ),
        }

    def resolve_has_tool_meta(self) -> bool:
        """本轮是否存在可安全跨轮持久化的最终工具结果。"""
        return bool(self.resolve_tool_run_text())

    def resolve_tool_run_text(self, *, max_total_chars: int = 4000) -> str:
        """仅持久化本轮已收到最终成功状态的工具结果。"""
        return build_final_tool_result_context(
            getattr(self, "_last_turn_tool_meta", None),
            max_total_chars=max_total_chars,
        )
