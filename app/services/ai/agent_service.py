import logging
import time
import uuid
import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Any, Optional, AsyncGenerator, Collection

from app.schemas.agent import AgentExecutionStep, ChatConfig
from app.services.ai.agent_manager import AgentManagerService
from app.services.ai.audit import AuditManager
from app.services.ai.config import AgentConfigProvider, RuntimeModelInfo, resolve_runtime_model_info
from app.services.ai.context_manager import AgentContextManager
from app.services.ai.route_progress import RouteProgressCallback, emit_route_stage
from app.services.ai.dispatcher import AgentDispatcher
from app.services.ai.memory_service import memory_service
from app.services.ai.skills import SkillInjector
from app.services.ai.context import (
    ContextCompactor,
    apply_context_snapshot as _apply_context_snapshot,
    window_for_context as _window_for_context,
)
from app.services.ai.agent_prompts import AgentServicePrompts
from app.services.ai.agent_types import AgentType
from app.services.ai.error_response_service import (
    build_error_presentation,
    sanitize_error_text,
)
from app.services.ai.prompt_assembler import (
    PromptAssemblyInput,
    assemble_system_prompt,
    resolve_prompt_assembler_flags,
)
from app.services.ai.runtime.session_run_lane import (
    ConversationRunBusyError,
    conversation_run_lane,
)
from app.services.ai.runtime.conversation_run_registry import track_conversation_run
from app.services.ai.executors.common import _attachment_abs_path, extract_tokens_from_message
from app.services.ai.runtime.agentscope.text_sanitize import sanitize_assistant_stream_text
from app.services.ai.runtime.agentscope.compat import HumanMessage, SystemMessage
from app.services.ai.runtime.execution_observability import ExecutionPerformanceTracker
from app.core.orm import AsyncSessionLocal
from app.services.ai.grounding.policy import resolve_fact_requirement
from app.services.ai.request_decision import (
    RequestCapability,
    RequestDecision,
    RequestSource,
)
from app.services.ai.turn_decision import (
    TurnDecision,
    default_thought_expanded,
    should_inject_ltm,
    should_inject_memory_recall_hint,
    should_inject_user_context,
    should_run_active_memory_preload,
    turn_kind_label,
)
from app.services.ai.intent_service import looks_like_current_model_query
from app.services.ai.business_context import sanitize_injected_context
from app.services.ai.conversation_identity import require_user_id
from app.services.schema_chunk_format import estimate_text_tokens

logger = logging.getLogger(__name__)

_LLM_DIGEST_TASKS: set[asyncio.Task] = set()

AWAITING_RESUME_STATUSES = frozenset(
    {"awaiting_permission", "awaiting_external_execution", "awaiting_user"}
)
NO_TOOL_EXECUTION_MESSAGE = "自动任务未实际调用任何工具"


def _format_execution_error_for_user(
    exc: BaseException,
    *,
    model_name: Optional[str] = None,
) -> str:
    """Use safe, actionable text for sandbox failures without leaking internals."""
    from app.services.ai.runtime.agentscope.workspace import DockerSandboxUnavailableError

    if isinstance(exc, DockerSandboxUnavailableError):
        return exc.user_message
    from app.services.ai.multimodal_support import format_execution_error

    return format_execution_error(exc, model_name=model_name)


async def _enrich_terminal_error_chunk(
    chunk: Dict[str, Any],
    *,
    config: Optional[ChatConfig] = None,
    model_name: Optional[str] = None,
    source_exception: Optional[BaseException] = None,
) -> Dict[str, Any]:
    """只为终端 error 事件补充友好正文；步骤级日志错误保持原样。"""

    if not isinstance(chunk, dict) or chunk.get("type") != "error":
        return chunk
    if chunk.get("error_detail"):
        return chunk

    raw_error = chunk.get("error") or chunk.get("message") or chunk.get("content")
    error_for_presentation = (
        source_exception
        if source_exception is not None
        else RuntimeError(str(raw_error or "未知错误"))
    )
    presentation = await build_error_presentation(
        error_for_presentation,
        config=config,
        model_name=model_name,
        tool_name=chunk.get("tool_name"),
        stage=chunk.get("phase") or chunk.get("category"),
        operation=chunk.get("operation") or chunk.get("title"),
    )
    return {
        **chunk,
        "content": presentation.content,
        "error_detail": presentation.as_error_detail(),
    }


async def _persist_assistant_message_and_summary(
    *,
    user_id: Any,
    conversation_id: str,
    content: str,
    trace_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    agent_type: Optional[str] = None,
    agent_display_name: Optional[str] = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    has_data_output: Optional[bool] = None,
    reusable_result_id: Optional[str] = None,
    reusable_result_status: Optional[str] = None,
    reasoning_content: Optional[str] = None,
    process_timeline: Optional[List[Dict[str, Any]]] = None,
    tool_run_text: Optional[str] = None,
    merge_summary: bool = False,
    defer_summary: bool = False,
    status: Optional[str] = None,
) -> None:
    """按顺序持久化 assistant，并按需合并摘要。

    摘要不能和 assistant 写入并发启动，否则 merge 可能读取不到本轮回答，
    或在多次恢复请求之间以旧游标覆盖新状态。聊天主链路可通过
    ``defer_summary`` 在 assistant 写入后立即返回，把摘要放到后台执行。
    """
    try:
        await memory_service.add_message(
            user_id,
            conversation_id,
            "assistant",
            content,
            trace_id=trace_id,
            agent_name=agent_name,
            agent_type=agent_type,
            agent_display_name=agent_display_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            has_data_output=has_data_output,
            reusable_result_id=reusable_result_id,
            reusable_result_status=reusable_result_status,
            reasoning_content=reasoning_content,
            process_timeline=process_timeline,
            tool_run_text=tool_run_text,
            status=status,
        )
    except Exception as exc:
        logger.warning(
            "[AgentService] Assistant persistence failed; summary skipped: %s",
            exc,
        )
        return

    if merge_summary and user_id and content:
        from app.services.ai.session_summary_service import SessionSummaryService

        async def _merge_summary() -> None:
            try:
                await SessionSummaryService.merge_session_summary(
                    str(user_id), conversation_id, content
                )
            except Exception as exc:
                logger.warning("[AgentService] Session summary task failed: %s", exc)

        if defer_summary:
            from app.core.cancellation import spawn_detached

            spawn_detached(
                _merge_summary(),
                name=f"merge-session-summary-{conversation_id}",
            )
        else:
            await _merge_summary()


def _public_agent_type(agent_config: Any) -> str:
    """Return a JSON-safe primary type, including compatibility for test/runtime shims."""
    raw_type = getattr(agent_config, "agent_type", AgentType.GENERAL)
    if isinstance(raw_type, AgentType):
        return raw_type.value
    if isinstance(raw_type, str):
        try:
            return AgentType(raw_type).value
        except ValueError:
            pass
    return AgentType.GENERAL.value


def build_current_model_answer(info: RuntimeModelInfo) -> str:
    """Build a user-facing answer from non-sensitive runtime model metadata."""
    phase_labels = {
        "primary_agent": "主模型",
        "synthesis": "合成模型",
        "fallback": "fallback 模型",
    }
    phase_label = phase_labels.get(info.phase, info.phase)
    if info.resolution_status == "registry_unresolved":
        return (
            f"本轮{phase_label}的配置标识是 **{info.configured_model}**，"
            "但模型注册表暂时不可用，无法确认最终解析后的模型 ID。"
        )
    if info.configured_model != info.effective_model_id:
        return (
            f"本轮使用的是 **{info.effective_model_id}**（{phase_label}，"
            f"配置名称：**{info.configured_model}**）。"
        )
    return f"本轮使用的是 **{info.effective_model_id}**（{phase_label}）。"


def _accumulate_stream_content(full: str, chunk: Dict[str, Any]) -> str:
    """合并 SSE chunk 到会话正文；retraction 表示用新正文整体替换。"""
    from app.services.ai.runtime.agentscope.process_narration import accumulate_visible_answer

    return accumulate_visible_answer(full, chunk)


def _accumulate_reasoning_content(full: str, chunk: Dict[str, Any]) -> str:
    """合并独立的模型推理 SSE 事件，不把推理混入可见正文。"""
    if chunk.get("type") == "reasoning_content":
        return full + str(chunk.get("content") or "")
    return full


def _filter_current_turn_download_urls(content: str) -> str:
    """Remove generated-file URLs that were not issued by this execution chain."""
    from app.core.context import get_current_agent_context
    from app.services.ai.tools.generated_file_service import filter_untrusted_download_urls

    context = get_current_agent_context()
    allowed_urls = getattr(context, "published_download_urls", []) if context else []
    return filter_untrusted_download_urls(content, allowed_urls=set(allowed_urls))


def _track_process_timeline(state: Optional[List[Dict[str, Any]]], chunk: Dict[str, Any]) -> None:
    if state is None or not isinstance(chunk, dict):
        return
    from app.services.ai.runtime.agentscope.process_timeline_snapshot import apply_stream_chunk

    apply_stream_chunk(state, chunk)


def _final_process_timeline(state: Optional[List[Dict[str, Any]]]):
    from app.services.ai.runtime.agentscope.process_timeline_snapshot import finalize_process_timeline

    return finalize_process_timeline(state)


def _should_persist_turn_history(
    content: Optional[str],
    process_timeline: Optional[List[Dict[str, Any]]],
    reasoning_content: Optional[str] = None,
) -> bool:
    """只要本轮产生正文、推理或思考卡片，就保留本轮历史。"""
    return bool(
        str(content or "").strip()
        or process_timeline
        or str(reasoning_content or "").strip()
    )


def _finalize_todo_success(
    state: Optional[List[Dict[str, Any]]],
    *,
    execution_status: str,
) -> Optional[Dict[str, Any]]:
    """仅对成功结束的当前轮 Todo 做后端收尾。"""
    if execution_status != "success":
        return None
    from app.services.ai.runtime.agentscope.process_timeline_snapshot import complete_todo_items

    event = complete_todo_items(state)
    if event:
        logger.info(
            "[Todo] Backend finalized checklist after successful execution: completed=%d",
            int((event.get("counts") or {}).get("completed", 0)),
        )
    return event


def _restore_todo_snapshot_from_pending(
    process_timeline_state: List[Dict[str, Any]],
    pending: Any,
) -> None:
    """恢复挂起前的 Todo 快照，确保确认/外部执行恢复仍能完成原清单。"""
    pending_state = getattr(pending, "state", None)
    snapshot_state = getattr(getattr(pending, "snapshot", None), "stream_state", None)
    for candidate in (pending_state, snapshot_state):
        if not isinstance(candidate, dict):
            continue
        todo_snapshot = candidate.get("todo_snapshot")
        if isinstance(todo_snapshot, dict):
            _track_process_timeline(process_timeline_state, todo_snapshot)
            return


def _restore_published_download_urls_from_pending(pending: Any) -> List[str]:
    """Restore tool-issued download URLs from an in-process or serialized pending run."""
    pending_state = getattr(pending, "state", None)
    snapshot_state = getattr(getattr(pending, "snapshot", None), "stream_state", None)
    for candidate in (pending_state, snapshot_state):
        if not isinstance(candidate, dict):
            continue
        urls = candidate.get("published_download_urls")
        if isinstance(urls, list):
            return [str(url) for url in urls if str(url).strip()]
    return []


def history_messages_for_llm(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """仅把模型需要的消息字段放回上下文，历史展示元数据不参与模型请求。

    ⚠️ 注意：此函数（进模型用，做 allowed_keys 字段过滤 + cancelled/interrupted 双阶段清理）
    与 context/compactor.py 中的 history_messages_for_token_budget（Token 预算用，字段原样保留）
    语义不同，请勿合并，否则会导致字段泄漏或断轮清理失效。
    注意：agent_name 必须保留，供 context_manager 倒序扫描提取 last_agent_name，
    用于路由的会话粘性判断。该字段不会传给 LLM（convert_history_to_messages 在
    assistant 分支只提取 content 字段构建 AIMessage）。
    agent_display_name：F 项——窗口内保留 agent 元数据，让后续轮 LLM 感知
    上一轮由哪个智能体处理；convert_history_to_messages 会将其短句注入 assistant 消息。
    """
    allowed_keys = (
        "role",
        "content",
        "files",
        "agent_name",
        "agent_display_name",
        "tool_run_text",
        "tool_run_text_version",
        "seq",
    )
    context_messages: List[Dict[str, Any]] = []
    for message in history:
        if not isinstance(message, dict):
            continue
        if (
            message.get("role") == "assistant"
            and str(message.get("status") or "").lower() in {"cancelled", "interrupted"}
        ):
            # 终止轮的 user/assistant 对仍保留在展示历史中，但不能让模型把半截
            # assistant 回复当成正常上下文继续完成。
            if context_messages and context_messages[-1].get("role") == "user":
                context_messages.pop()
            continue
        context_messages.append({key: message[key] for key in allowed_keys if key in message})
    return context_messages


def _client_prefix_history_len(messages: List[Dict[str, Any]]) -> int:
    """统计客户端提交的真实对话前缀，忽略 UI 分隔用的 system 消息。"""
    return sum(
        1
        for message in messages[:-1]
        if isinstance(message, dict)
        and message.get("role") in {"user", "assistant"}
    )


def _regular_completion_history(
    server_history: Optional[List[Dict[str, Any]]],
    _client_messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """普通完成请求始终以服务端会话历史为准，不按客户端展示历史裁剪。"""
    return list(server_history or [])


def build_chat_history_boundary_prompt(system_prompt: Optional[str]) -> str:
    """在最终系统提示中明确区分历史背景和本轮当前请求。"""
    boundary = AgentServicePrompts.CHAT_HISTORY_BOUNDARY_PROMPT.strip()
    existing = str(system_prompt or "").strip()
    return f"{boundary}\n\n{existing}" if existing else boundary


def _trace_has_tool_call(trace_buffer: Optional[List[AgentExecutionStep]]) -> bool:
    return any(getattr(step, "event_type", None) == "tool_call" for step in (trace_buffer or []))


def _turn_status_signal(chunk: Dict[str, Any]) -> Optional[str]:
    """把单个 SSE chunk 映射成轮次终态信号；``None`` 表示该 chunk 不影响终态。

    带 ``type`` 的事件（``log`` / ``meta`` / ``retraction`` 等）只描述单步或辅助信息，
    单步工具失败不代表整轮失败，因此除显式 error 与暂停事件外一律不参与终态判定。
    """
    chunk_type = str(chunk.get("type") or "")
    if chunk_type == "permission_required":
        return "awaiting_permission"
    if chunk_type == "external_execution_required":
        return "awaiting_external_execution"
    if chunk_type == "user_question":
        return "awaiting_user"
    if chunk_type == "error":
        return "error"
    if chunk_type:
        return None
    if chunk.get("status") == "error":
        return "error"
    if chunk.get("content"):
        return "success"
    return None


def _apply_turn_status_signal(current: str, chunk: Dict[str, Any]) -> str:
    """仅最终状态定成败：中途失败可被后续正文覆盖，等待恢复的暂停态不被正文覆盖。"""
    signal = _turn_status_signal(chunk)
    if signal is None:
        return current
    if signal == "success" and current in AWAITING_RESUME_STATUSES:
        return current
    return signal


def _build_route_grounding_metadata(
    *,
    request_source: Optional[str],
    request_capability: Optional[str],
    confidence: float,
    semantic_intent: Optional[str],
    semantic_confidence: Optional[float],
    semantic_domain: Optional[str],
    fact_kind: Optional[str],
) -> Dict[str, Any]:
    """Expose the normalized grounding contract alongside router telemetry."""
    try:
        source = RequestSource(str(request_source or ""))
        capability = RequestCapability(str(request_capability or ""))
    except ValueError:
        requirement = resolve_fact_requirement(None)
        return {
            "decision_origin": requirement.decision_origin,
            "decision_confidence": requirement.decision_confidence,
            "evidence_mode": requirement.evidence_mode,
            "accepted_evidence_types": [],
            "decision_conflicts": list(requirement.decision_conflicts),
        }

    decision = RequestDecision(
        source=source,
        capability=capability,
        confidence=float(confidence or 0.0),
        reasoning="router telemetry",
        semantic_intent=semantic_intent,
        semantic_confidence=float(semantic_confidence or 0.0),
        semantic_domain=semantic_domain,
        fact_kind=fact_kind,
    )
    requirement = resolve_fact_requirement(decision)
    return {
        "decision_origin": requirement.decision_origin,
        "decision_confidence": requirement.decision_confidence,
        "evidence_mode": requirement.evidence_mode,
        "accepted_evidence_types": sorted(
            evidence_type.value for evidence_type in requirement.accepted_types
        ),
        "decision_conflicts": list(requirement.decision_conflicts),
    }


def _build_turn_execution_log(
    turn_decision: TurnDecision,
    *,
    turn_display_label: str,
    execution_time_ms: float,
) -> Dict[str, Any]:
    """Build the pre-execution event without mislabeling Main delegation."""
    if turn_decision.provenance == "automatic_delegation":
        return {
            "type": "log",
            "title": "进入主专家自动委派",
            "details": "未指定专家，主专家将直接回答或按任务需要自动委派其他智能体。",
            "status": "success",
            "category": "intent",
            "turn_type": turn_decision.turn_kind,
            "execution_time_ms": execution_time_ms,
        }

    return {
        "type": "log",
        "title": "分析用户请求并进行意图识别",
        "details": (
            f"{turn_display_label}。"
            f"{turn_decision.request_reasoning or turn_decision.reasoning or '复用统一轮次决策'}"
        ),
        "status": "success",
        "category": "intent",
        "turn_type": turn_decision.turn_kind,
        "execution_time_ms": execution_time_ms,
    }


def _build_request_validation_log(
    *,
    user_info: Optional[Dict[str, Any]],
    conversation_id: Optional[str],
    request_observability: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a safe, user-facing summary of request preflight checks.

    The event deliberately contains status and counts only. Authentication
    credentials, raw request payloads, and resource contents must never enter
    the execution timeline.
    """
    metadata = request_observability or {}
    user_info = user_info or {}
    user_id = str(user_info.get("user_id") or user_info.get("id") or "未知")
    user_name = str(
        user_info.get("real_name")
        or user_info.get("user_name")
        or user_info.get("username")
        or "未知用户"
    )
    role = str(user_info.get("role_name") or user_info.get("role") or "普通用户")

    def _status(key: str, fallback: str) -> str:
        value = metadata.get(key)
        if value is None:
            return fallback
        return "已通过" if bool(value) else "未通过"

    scope = metadata.get("resource_scope") or {}
    turn_scope = metadata.get("turn_resource_scope") or {}
    authorized_scope = metadata.get("authorized_resource_scope") or {}

    def _scope_count(key: str) -> int:
        value = scope.get(key, 0) if isinstance(scope, dict) else 0
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def _scope_count_from(source: Any, key: str) -> Optional[int]:
        if not isinstance(source, dict) or key not in source:
            return None
        value = source.get(key)
        if value is None:
            return None
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return None

    def _resource_pair(source: Any, *, missing_text: str) -> str:
        datasets = _scope_count_from(source, "datasets")
        knowledge_bases = _scope_count_from(source, "knowledge_bases")
        if datasets is None and knowledge_bases is None:
            return missing_text
        dataset_text = str(datasets) if datasets is not None else "暂不可用"
        knowledge_text = str(knowledge_bases) if knowledge_bases is not None else "暂不可用"
        return f"数据集 {dataset_text} 个、知识库 {knowledge_text} 个"

    idempotency_status = metadata.get("idempotency_status")
    if not idempotency_status:
        idempotency_status = "未启用（无客户端请求 ID）"

    session_status = "已加载" if conversation_id else "未绑定会话"
    details = (
        f"鉴权：{_status('authenticated', '已通过')}；"
        f"当前会话用户：{user_name}（ID：{user_id}，角色：{role}）；"
        f"参数校验：{_status('parameters_validated', '已通过')}；"
        f"会话挂载：{session_status}，数据集 {_scope_count('datasets')} 个、"
        f"知识库 {_scope_count('knowledge_bases')} 个、"
        f"Skill {_scope_count('skills')} 个、"
        f"MCP 工具 {_scope_count('mcp_tools')} 个；"
        f"本轮请求：{_resource_pair(turn_scope, missing_text='未单独指定')}；"
        f"当前用户有权限：{_resource_pair(authorized_scope, missing_text='权限目录未统计')}；"
        f"幂等校验：{idempotency_status}。"
    )
    return {
        "type": "log",
        "id": "request:validation",
        "parent_id": "preparation:auth_context_capability",
        "title": "请求校验",
        "details": details,
        "status": "success",
        "category": "system",
    }


def _build_context_history_log(
    *,
    conversation_id: Optional[str],
    source_history_count: int,
    selected_history_count: int,
    trimmed_history_count: int,
    history_token_budget: Optional[int],
    max_context_messages: Optional[int],
    compaction_applied: bool = False,
    request_history_count: int = 0,
) -> Dict[str, Any]:
    """Build a count-only summary of history loading and context-window control."""
    if conversation_id:
        session_text = f"会话 {conversation_id}"
        history_text = f"读取历史 {max(0, source_history_count)} 条"
    else:
        session_text = "未绑定会话"
        history_text = (
            f"未读取服务端历史，请求携带上下文 {max(0, request_history_count)} 条"
        )
    budget_text = (
        f"{history_token_budget} tokens"
        if history_token_budget
        else "未设置"
    )
    max_messages_text = str(max_context_messages) if max_context_messages else "未设置"
    details = (
        f"{session_text}；{history_text}；"
        f"上下文窗口保留 {max(0, selected_history_count)} 条；"
        f"裁剪 {max(0, trimmed_history_count)} 条；"
        f"历史 Token 预算：{budget_text}；"
        f"消息条数上限：{max_messages_text}；"
        f"上下文压缩：{'已触发' if compaction_applied else '未触发'}。"
    )
    return {
        "type": "log",
        "id": "context:history",
        "parent_id": "preparation:auth_context_capability",
        "title": "会话上下文",
        "details": details,
        "status": "success",
        "category": "context",
    }


def _build_model_config_log(
    runtime_model_info: RuntimeModelInfo,
    synthesis_runtime_model_info: Optional[RuntimeModelInfo] = None,
) -> Dict[str, Any]:
    """Build a non-sensitive summary of the resolved runtime model settings."""
    def _model_text(label: str, info: RuntimeModelInfo) -> str:
        context_size = getattr(info, "context_size", None) or "未返回"
        output_tokens = getattr(info, "max_output_tokens", None) or "未返回"
        return (
            f"{label}：{getattr(info, 'effective_model_id', None)}（配置：{getattr(info, 'configured_model', None)}，"
            f"来源：{getattr(info, 'source', None)}，上下文：{context_size}，"
            f"最大输出：{output_tokens}，状态：{getattr(info, 'resolution_status', None)}）"
        )

    details = _model_text("主模型", runtime_model_info)
    if synthesis_runtime_model_info is not None:
        details = f"{details}；{_model_text('合成模型', synthesis_runtime_model_info)}"
    return {
        "type": "log",
        "id": "config:model",
        "parent_id": "preparation:auth_context_capability",
        "title": "模型配置解析",
        "details": details,
        "status": "success",
        "category": "model",
    }


def _build_capability_catalog_log(
    *,
    knowledge_dataset_count: int,
    configured_dataset_count: int,
    skill_count: int,
    delegable_agent_count: int,
    roster_loaded: bool,
    runtime_tool_count: int,
    metadata_dataset_count: int = 0,
    session_dataset_count: int = 0,
    session_knowledge_base_count: int = 0,
    authorized_dataset_count: Optional[int] = None,
    authorized_knowledge_base_count: Optional[int] = None,
) -> Dict[str, Any]:
    """Build a count-only summary of the turn's capability catalog."""
    def _display_count(value: Optional[int]) -> Optional[str]:
        if value is None:
            return None
        try:
            return str(max(0, int(value)))
        except (TypeError, ValueError):
            return None

    def _resource_pair(dataset_count: Optional[int], knowledge_base_count: Optional[int]) -> str:
        dataset_text = _display_count(dataset_count)
        knowledge_text = _display_count(knowledge_base_count)
        if dataset_text is None and knowledge_text is None:
            return "权限目录未统计"
        dataset_text = dataset_text or "暂不可用"
        knowledge_text = knowledge_text or "暂不可用"
        return f"数据集 {dataset_text} 个、知识库 {knowledge_text} 个"

    roster_text = (
        f"已加载 {max(0, delegable_agent_count)} 个"
        if roster_loaded
        else "未加载（当前专家不需要委派清单）"
    )
    details = (
        f"会话挂载：数据集 {max(0, session_dataset_count)} 个、"
        f"知识库 {max(0, session_knowledge_base_count)} 个；"
        f"本轮选用：数据集 {max(0, metadata_dataset_count)} 个、"
        f"知识库 {max(0, knowledge_dataset_count)} 个；"
        f"专家配置知识库 {max(0, configured_dataset_count)} 个；"
        f"当前用户有权限：{_resource_pair(authorized_dataset_count, authorized_knowledge_base_count)}；"
        f"Skill {max(0, skill_count)} 个；"
        f"可委派专家清单：{roster_text}；"
        f"运行时工具 {max(0, runtime_tool_count)} 个。"
    )
    return {
        "type": "log",
        "id": "capability:catalog",
        "parent_id": "preparation:auth_context_capability",
        "title": "知识库和专家清单加载",
        "details": details,
        "status": "success",
        "category": "system",
    }


def _build_prompt_assembly_log(
    assembled_prompt: Any,
    *,
    runtime_tool_count: int,
    final_prompt_chars: Optional[int] = None,
) -> Dict[str, Any]:
    """Build a safe summary of prompt assembly without exposing prompt text."""
    section_names = list(getattr(assembled_prompt, "section_names", ()) or ())
    stable_chars = len(str(getattr(assembled_prompt, "stable_prefix", "") or ""))
    dynamic_chars = len(str(getattr(assembled_prompt, "dynamic_suffix", "") or ""))
    total_chars = (
        final_prompt_chars
        if final_prompt_chars is not None
        else len(str(getattr(assembled_prompt, "full_text", "") or ""))
    )
    return {
        "type": "log",
        "id": "prompt:assembly",
        "parent_id": "preparation:auth_context_capability",
        "title": "Prompt 组装",
        "details": (
            f"已组装 {len(section_names)} 个提示词区块；"
            f"稳定部分 {stable_chars} 字符；动态部分 {dynamic_chars} 字符；"
            f"最终 Prompt {max(0, total_chars)} 字符；"
            f"运行时工具 {max(0, runtime_tool_count)} 个。"
        ),
        "status": "success",
        "category": "system",
    }


def _build_preparation_parent_log(
    *,
    status: str,
    details: Optional[str] = None,
    execution_time_ms: Optional[float] = None,
) -> Dict[str, Any]:
    """Build the shared parent for request/context/capability preparation logs."""
    if details is None:
        details = (
            "正在完成鉴权、会话上下文、专家配置、模型与能力准备。"
            if status == "pending"
            else "鉴权、会话上下文、专家配置、模型与能力准备已完成。"
        )
    event: Dict[str, Any] = {
        "type": "log",
        "id": "preparation:auth_context_capability",
        "title": "鉴权及上下文与能力准备",
        "details": details,
        "status": status,
        "category": "system",
    }
    if execution_time_ms is not None:
        event["execution_time_ms"] = max(1.0, float(execution_time_ms))
    return event


@dataclass
class TurnPreflightContext:
    skills_injection: List[str]
    matched_skills_to_log: List[tuple]
    effective_prompt_tool_names: List[str]
    ltm_profile: Optional[Dict[str, Any]]
    ltm_loaded_data: Optional[Dict[str, Any]]
    memory_recall_hint: Optional[str]
    preloaded_memories_text: Optional[str]
    user_profile: Optional[str]
    accessible_resources: Optional[Dict[str, Any]]
    delegable_agents: Optional[List[Any]]
    delegable_agent_count: int
    roster_loaded: bool
    agent_system_prompt: Optional[str]
    sub_agents_context: Optional[str]


class AgentService:
    USING_SUPERPOWERS_SKILL_ID = "using-superpowers"

    """
    Unified Orchestrator for AI Agent interactions.
    Now refactored to delegate execution to specialized Executors.
    """

    async def generate_greeting(self) -> str:
        """
        Return a fixed welcome message.
        """
        return AgentServicePrompts.GREETING

    async def _persist_context_compaction_event(
        self,
        event: Dict[str, Any],
        *,
        user_id: Any,
        conversation_id: Optional[str],
        trace_id: Optional[str],
        source: str,
        stage: str,
        agent_name: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> None:
        """尽力记录压缩事件；委托至 ContextCompactor。"""
        await ContextCompactor.persist_context_compaction_event(
            event=event,
            user_id=user_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
            source=source,
            stage=stage,
            agent_name=agent_name,
            model_name=model_name,
        )


    async def _build_user_context_msg(self, user_info: Dict[str, Any]) -> Dict[str, str]:
        """
        Builds a read-only system message from verified API Key identity.
        """
        raw_name = user_info.get("user_name") or user_info.get("username", "Unknown User")
        user_id = str(user_info.get("user_id") or user_info.get("id") or "")
        real_name = user_info.get("real_name") or raw_name
        dept = user_info.get("dept_name") or user_info.get("department")
        org_path = user_info.get("org_path")
        dept_code = user_info.get("dept_code")
        role = user_info.get("role_name") or user_info.get("role")

        content = AgentServicePrompts.user_context_message(
            user_id=user_id or "unknown",
            raw_name=raw_name,
            real_name=real_name,
            dept=dept,
            dept_code=dept_code,
            org_path=org_path,
            role=role,
        )
        return {"role": "system", "content": content}

    @staticmethod
    def _should_forbid_quick_suggestions(user_info: Optional[Dict[str, Any]]) -> bool:
        """Only automatic delivery contexts may suppress the interactive quick guidance."""
        if not user_info:
            return False

        def enabled(value: Any) -> bool:
            if isinstance(value, bool):
                return value
            return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

        return any(
            enabled(user_info.get(key))
            for key in (
                "quick_suggestions_forbidden",
                "is_scheduled_task",
                "is_subscription_task",
            )
        )

    @staticmethod
    def _parse_bool_config(value: Any, default: bool) -> bool:
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _parse_int_config(value: Any, default: int, *, min_value: int, max_value: int | None = None) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        parsed = max(min_value, parsed)
        if max_value is not None:
            parsed = min(max_value, parsed)
        return parsed

    @staticmethod
    def _parse_float_config(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    _resolve_skill_full_load_policy = staticmethod(SkillInjector.resolve_skill_full_load_policy)
    _should_preload_skill_full_instruction = staticmethod(
        SkillInjector.should_preload_skill_full_instruction
    )
    _is_new_session_first_user_turn = staticmethod(
        SkillInjector.is_new_session_first_user_turn
    )

    @classmethod
    def _should_force_preload_scanned_skill(
        cls,
        *,
        skill_id: str,
        messages: Optional[List[Dict[str, Any]]],
    ) -> bool:
        return SkillInjector.should_force_preload_scanned_skill(
            skill_id=skill_id, messages=messages
        )

    @classmethod
    def _ensure_first_turn_superpowers_candidate(
        cls,
        *,
        scanned_skills: List[Dict[str, Any]],
        available_skills: List[Dict[str, Any]],
        messages: Optional[List[Dict[str, Any]]],
        exclude_ids: Optional[set[str]] = None,
    ) -> List[Dict[str, Any]]:
        return SkillInjector.ensure_first_turn_superpowers_candidate(
            scanned_skills=scanned_skills,
            available_skills=available_skills,
            messages=messages,
            exclude_ids=exclude_ids,
        )

    def _append_first_turn_superpowers(
        self,
        **kwargs: Any,
    ) -> int:
        return SkillInjector.append_first_turn_superpowers(**kwargs)

    _build_skill_injection = staticmethod(SkillInjector.build_skill_injection)
    _build_skill_log_chunk = staticmethod(SkillInjector.build_skill_log_chunk)

    @staticmethod
    def _authorized_attachment_paths(messages: List[Dict[str, Any]]) -> List[str]:
        """Return server-resolved paths for attachments present in this chat context."""
        paths = {
            _attachment_abs_path(file_obj)
            for message in messages or []
            if message.get("role") == "user"
            for file_obj in message.get("files") or []
            if file_obj.get("url")
        }
        return sorted(path for path in paths if path)

    @staticmethod
    def _current_turn_attachment_paths(messages: List[Dict[str, Any]]) -> List[str]:
        """Return attachment paths carried by the latest user turn only."""
        latest_user_message = next(
            (
                message
                for message in reversed(messages or [])
                if message.get("role") == "user"
            ),
            None,
        )
        if not latest_user_message:
            return []
        paths = {
            _attachment_abs_path(file_obj)
            for file_obj in latest_user_message.get("files") or []
            if file_obj.get("url")
        }
        return sorted(path for path in paths if path)

    @staticmethod
    async def _quota_block_message(user_info: Optional[Dict[str, Any]]) -> Optional[str]:
        if not user_info:
            return None
        from app.services.quota_service import QuotaService

        async with AsyncSessionLocal() as quota_session:
            return await QuotaService(quota_session).check_before_call(user_info)

    async def chat_completion_stream(
        self,
        messages: List[Dict[str, str]],
        agent_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        version_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        user_info: Optional[Dict[str, Any]] = None,
        api_key: Optional[str] = None,
        enable_multi_agent: bool = True,
        debug_options: Optional[Dict[str, Any]] = None,
        permission_options: Optional[Dict[str, Any]] = None,
        knowledge_dataset_ids: Optional[List[str]] = None,
        metadata_dataset_ids: Optional[List[str]] = None,
        reusable_result_id: Optional[str] = None,
        quick_context: Optional[Dict[str, Any]] = None,
        request_observability: Optional[Dict[str, Any]] = None,
        shared_state: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Main entry point for streaming chat.
        """
        debug_options = dict(debug_options or {})
        if "injected_context" in debug_options:
            debug_options["injected_context"] = sanitize_injected_context(
                debug_options["injected_context"]
            )
        from app.services.ai.runtime.agentscope.tool_timeout import (
            load_agent_max_toolcall_timeout,
            parse_agent_max_toolcall_timeout,
        )

        configured_timeout = (request_observability or {}).get(
            "agent_max_toolcall_timeout"
        )
        if configured_timeout is None:
            agent_max_toolcall_timeout_seconds = await load_agent_max_toolcall_timeout()
        else:
            agent_max_toolcall_timeout_seconds = parse_agent_max_toolcall_timeout(
                configured_timeout
            )
        debug_options["_agent_max_toolcall_timeout_seconds"] = (
            agent_max_toolcall_timeout_seconds
        )
        from app.utils.context import current_user_info
        current_user_info.set(user_info)

        # 会话运行 lane、Redis 记忆和后续审计必须绑定真实用户；不能让内部
        # 入口把缺失身份降级为 anonymous 后继续执行。
        required_user_id = require_user_id(user_info)

        trace_id = str(uuid.uuid4())
        trace_buffer: List[AgentExecutionStep] = []
        if shared_state is None:
            shared_state = {}
        shared_state.setdefault("agent_config", None)
        shared_state.setdefault("execution_status", "success")
        shared_state.setdefault("process_timeline", [])
        shared_state.setdefault("preparation_started_at", None)
        shared_state.setdefault("preparation_ready", False)
        performance_tracker = ExecutionPerformanceTracker()
        shared_state["performance_tracker"] = performance_tracker
        lane_user_id = required_user_id

        from app.services.ai.pipeline import PipelineContext, PipelineRunner
        from app.services.ai.pipeline.steps import (
            PreflightStep,
            ContextStep,
            RouteStep,
            AssembleStep,
            ExecutionStep,
            FinalizeStep,
        )

        pipeline_context = PipelineContext(
            messages=messages,
            user_info=user_info,
            agent_id=agent_id,
            agent_name=agent_name,
            version_id=version_id,
            conversation_id=conversation_id,
            api_key=api_key,
            enable_multi_agent=enable_multi_agent,
            debug_options=debug_options,
            permission_options=permission_options,
            knowledge_dataset_ids=knowledge_dataset_ids,
            metadata_dataset_ids=metadata_dataset_ids,
            reusable_result_id=reusable_result_id,
            quick_context=quick_context,
            request_observability=request_observability,
            trace_id=trace_id,
            lane_user_id=lane_user_id,
            trace_buffer=trace_buffer,
            start_time=asyncio.get_running_loop().time(),
            agent_max_toolcall_timeout_seconds=agent_max_toolcall_timeout_seconds,
            shared_state=shared_state,
            performance_tracker=performance_tracker,
        )

        preflight_step = PreflightStep(self)
        async for chunk in preflight_step.check_quota_and_queue(pipeline_context):
            yield chunk

        if pipeline_context.execution_status == "quota_exceeded":
            return

        waiting_log_emitted = False
        queue_start_time = asyncio.get_running_loop().time()
        if conversation_id and await conversation_run_lane.is_locked(
            user_id=lane_user_id, conversation_id=conversation_id
        ):
            waiting_log_emitted = True

        try:
            async with track_conversation_run(
                lane_user_id, conversation_id
            ) as run_handle, conversation_run_lane.hold(
                user_id=lane_user_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
            ):
                pipeline_context.run_handle = run_handle
                if waiting_log_emitted:
                    queue_elapsed_ms = (asyncio.get_running_loop().time() - queue_start_time) * 1000
                    yield {
                        "type": "log",
                        "id": "session:queue_wait",
                        "title": "上一次任务已完成",
                        "details": "会话资源已释放，继续处理当前任务",
                        "status": "success",
                        "category": "system",
                        "execution_time_ms": max(1.0, queue_elapsed_ms),
                    }

                pipeline = PipelineRunner([
                    preflight_step,
                    ContextStep(self),
                    RouteStep(self),
                    AssembleStep(self),
                    ExecutionStep(self),
                    FinalizeStep(self),
                ])

                async for chunk in pipeline.run(pipeline_context):
                    yield chunk

        except ConversationRunBusyError:
            if waiting_log_emitted:
                queue_elapsed_ms = (asyncio.get_running_loop().time() - queue_start_time) * 1000
                yield {
                    "type": "log",
                    "id": "session:queue_wait",
                    "title": "等待上一次会话任务超时",
                    "details": "排队等待超时，上一次任务仍未结束，请稍后再试",
                    "status": "error",
                    "category": "system",
                    "execution_time_ms": max(1.0, queue_elapsed_ms),
                }
            yield {
                "type": "error",
                "status": "error",
                "content": "当前会话正在处理中，请稍后再试。",
            }
            return

    async def _resolve_runtime_context_budget(
        self,
        *,
        debug_options: Optional[Dict[str, Any]],
        agent_id: Optional[str],
        agent_name: Optional[str],
        version_id: Optional[str],
    ) -> int:
        """动态解析截断上下文水位线（token）。

        优先级（与用户需求一致）：
        1. 显式指定的当前模型 context_size（debug_options.model，即输入框切换所选模型），
           经 debug 通道解析。
        2. 发布版本模型的 context_size（按 agent_id / agent_name / version_id 轻量定位
           ChatConfig.model_name 后再解析）。
        3. 兜底：ConfigService.agent_context_max_tokens（默认 65536）。

        仅当模型来源为显式指定（runtime_override / debug_override / agent_config，
        而非 system_default 回落）且注册表解析出有效 context_size 时才采纳模型窗口，
        否则一律回落配置兜底值，避免水位线与模型窗口脱钩导致提前 compat。
        任何 DB / 注册表异常均吞掉并回落兜底，不影响主流程。
        """
        fallback_tokens = await self._resolve_pre_route_context_budget()

        chat_config = None
        try:
            from app.services.ai.agent_manager import AgentManagerService

            session = AsyncSessionLocal()
            try:
                if version_id:
                    chat_config = await AgentManagerService.get_version_config(
                        session, version_id
                    )
                else:
                    chat_config = await AgentManagerService.get_active_agent_config(
                        session,
                        agent_id=agent_id,
                        agent_name=agent_name,
                    )
            finally:
                await session.close()
        except Exception:
            logger.warning(
                "Failed to resolve published model config for runtime context budget; "
                "falling back to agent_context_max_tokens"
            )
            chat_config = None

        info = None
        try:
            info = await resolve_runtime_model_info(
                config=chat_config,
                debug_options=debug_options,
            )
        except Exception:
            logger.warning(
                "Failed to resolve runtime model info for context budget; "
                "falling back to agent_context_max_tokens"
            )
            return fallback_tokens
        if info is not None and info.source in {
            "runtime_override",
            "debug_override",
            "agent_config",
        }:
            try:
                resolved = int(info.context_size) if info.context_size else 0
            except (TypeError, ValueError):
                resolved = 0
            if resolved > 0:
                return resolved
        return fallback_tokens

    async def _resolve_pre_route_context_budget(self) -> int:
        """读取路由前可安全使用的全局上下文预算，不解析任何 agent。"""
        from app.services.config_service import ConfigService

        cfg = await ConfigService.get("agent_context_max_tokens", "65536")
        try:
            fallback_tokens = int(cfg)
        except (TypeError, ValueError):
            fallback_tokens = 65536
        return fallback_tokens if fallback_tokens > 0 else 65536

    async def _resolve_runtime_model_info_safe(
        self,
        *,
        config: Optional[Any],
        debug_options: Optional[Dict[str, Any]],
        model_override: Optional[str] = None,
        phase: str = "primary_agent",
    ) -> RuntimeModelInfo:
        """解析最终模型身份；注册表异常时保留可执行的配置模型兜底。"""
        try:
            return await resolve_runtime_model_info(
                config=config,
                debug_options=debug_options,
                model_override=model_override,
                phase=phase,
            )
        except Exception as exc:
            configured_model = str(
                model_override or getattr(config, "model_name", "") or ""
            ).strip()
            source = "runtime_override" if model_override else (
                "agent_config" if configured_model else "system_default"
            )
            if not configured_model:
                try:
                    from app.services.config_service import ConfigService

                    configured_model = str(
                        await ConfigService.get("llm_model_name", "deepseek-chat")
                        or "deepseek-chat"
                    )
                except Exception:
                    configured_model = "deepseek-chat"
            logger.warning(
                "Failed to resolve final runtime model info; continuing with configured "
                "model=%s: %s",
                configured_model,
                exc,
            )
            return RuntimeModelInfo(
                configured_model=configured_model,
                effective_model_id=configured_model,
                source=source,
                phase=phase,
                resolution_status="registry_unresolved",
            )

    async def _resolve_context_overhead_tokens(self) -> int:
        """读取系统提示、工具 schema 等非历史内容的预留预算。"""
        from app.services.config_service import ConfigService

        try:
            overhead_raw = await ConfigService.get(
                "agent_context_overhead_headroom_tokens", "8192"
            )
            overhead = int(overhead_raw)
        except (TypeError, ValueError):
            overhead = 8192
        return max(0, overhead)

    async def _resolve_history_context_budget(
        self,
        runtime_max_tokens: int,
        *,
        max_output_tokens: Optional[int] = None,
    ) -> int:
        """从模型窗口预算中扣除输出和「非历史 overhead」。

        最终请求的实际组成远不止历史消息：还包括系统提示（含 grounding 安全前缀、
        路由 hint、会话产物、时间锚点）、工具 schema、注入的技能提示、路由注入内容，
        以及本轮用户消息。这些由 runner 在 ``agent_service`` 之外独立组装，
        ``_window_for_context`` 无法对其做 token 预算，若不预留配额，历史消息会把
        模型窗口占满，导致最终请求超窗被模型端截断。

        如果模型配置了 ``max_output_tokens``，它同样属于供应商的总上下文预算，必须
        在历史截断前先扣除。否则 64K 上下文 + 32K 输出时，平台仍可能把输入送到
        57K，最终被供应商按 ``input + completion`` 拒绝。

        未配置输出上限时保持旧的 1/3 最低历史保留策略；配置了输出上限时以总预算安全
        优先，不能为了保留 1/3 历史而重新侵占输出或 overhead 的预留空间。
        """
        runtime_max_tokens = max(1, int(runtime_max_tokens))
        overhead = await self._resolve_context_overhead_tokens()
        try:
            completion_reserve = int(max_output_tokens or 0)
        except (TypeError, ValueError):
            completion_reserve = 0
        if completion_reserve > 0:
            return max(1, runtime_max_tokens - completion_reserve - overhead)
        return max(
            runtime_max_tokens - overhead,
            max(1, runtime_max_tokens // 3),
        )

    async def _history_budget_for_runtime_model_info(
        self,
        runtime_model_info: RuntimeModelInfo,
    ) -> int:
        """把最终模型信息转换成实际可用于历史的 token 预算。"""
        runtime_max_tokens = await self._resolve_pre_route_context_budget()
        if runtime_model_info.source in {
            "runtime_override",
            "debug_override",
            "agent_config",
        }:
            try:
                resolved = int(runtime_model_info.context_size or 0)
            except (TypeError, ValueError):
                resolved = 0
            if resolved > 0:
                runtime_max_tokens = resolved
        return await self._resolve_history_context_budget(
            runtime_max_tokens,
            max_output_tokens=runtime_model_info.max_output_tokens,
        )

    @staticmethod
    def _configured_model_window(runtime_model_info: RuntimeModelInfo) -> int:
        if runtime_model_info.source not in {
            "runtime_override",
            "debug_override",
            "agent_config",
        }:
            return 0
        try:
            value = int(runtime_model_info.context_size or 0)
        except (TypeError, ValueError):
            value = 0
        return value if value > 0 else 0

    @staticmethod
    def _configured_model_output(runtime_model_info: RuntimeModelInfo) -> int:
        try:
            value = int(runtime_model_info.max_output_tokens or 0)
        except (TypeError, ValueError):
            value = 0
        return value if value > 0 else 0

    async def _runtime_context_metadata(
        self,
        runtime_model_info: RuntimeModelInfo,
        *,
        history_budget: Optional[int] = None,
        synthesis_runtime_model_info: Optional[RuntimeModelInfo] = None,
    ) -> Dict[str, Any]:
        """Build the one context-budget contract consumed by runners and tools."""
        fallback_window = await self._resolve_pre_route_context_budget()
        windows = [
            value
            for value in (
                self._configured_model_window(runtime_model_info),
                self._configured_model_window(synthesis_runtime_model_info)
                if synthesis_runtime_model_info is not None
                else 0,
            )
            if value > 0
        ]
        physical_window = min(windows) if windows else fallback_window
        model_pairs = [
            (
                self._configured_model_window(runtime_model_info) or fallback_window,
                self._configured_model_output(runtime_model_info),
            )
        ]
        if synthesis_runtime_model_info is not None:
            model_pairs.append(
                (
                    self._configured_model_window(synthesis_runtime_model_info)
                    or fallback_window,
                    self._configured_model_output(synthesis_runtime_model_info),
                )
            )
        completion_reserves = [
            output for window, output in model_pairs if window > 0 and output > 0
        ]
        request_input_budgets = [
            max(1, window - output)
            for window, output in model_pairs
            if window > 0 and output > 0
        ]
        if history_budget is None:
            history_budget = await self._history_budget_for_runtime_model_info(
                runtime_model_info
            )
        if synthesis_runtime_model_info is not None:
            history_budget = min(
                history_budget,
                await self._history_budget_for_runtime_model_info(
                    synthesis_runtime_model_info
                ),
            )
        prompt_overhead = await self._resolve_context_overhead_tokens()
        completion_reserve = max(completion_reserves, default=0)
        request_input_budget = min(request_input_budgets, default=physical_window)
        return {
            **runtime_model_info.public_dict(),
            "physical_window": physical_window,
            "history_budget": history_budget,
            "completion_reserve_tokens": completion_reserve,
            "request_input_budget": request_input_budget,
            "prompt_overhead_reservation_tokens": prompt_overhead,
            # 兼容旧字段：现在表示历史之外的总预留（输出 + prompt/tool overhead）。
            "overhead_reservation_tokens": max(0, physical_window - history_budget),
        }

    async def _rebuild_context_for_resolved_model(
        self,
        *,
        messages: List[Dict[str, Any]],
        runtime_model_info: RuntimeModelInfo,
        conversation_id: Optional[str],
        user_info: Optional[Dict[str, Any]],
        agent_id: Optional[str],
        agent_name: Optional[str],
        version_id: Optional[str],
        shared_state: Optional[Dict[str, Any]],
        synthesis_runtime_model_info: Optional[RuntimeModelInfo] = None,
    ) -> List[Dict[str, Any]]:
        """路由完成后按目标模型重新构造真正发送给 executor 的上下文；委托至 ContextCompactor。"""
        return await ContextCompactor.rebuild_context_for_resolved_model(
            self,
            messages=messages,
            runtime_model_info=runtime_model_info,
            conversation_id=conversation_id,
            user_info=user_info,
            agent_id=agent_id,
            agent_name=agent_name,
            version_id=version_id,
            shared_state=shared_state,
            synthesis_runtime_model_info=synthesis_runtime_model_info,
        )


    async def _maybe_compact_overflow(
        self,
        full_history: List[Dict[str, Any]],
        window: List[Dict[str, Any]],
        *,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        version_id: Optional[str] = None,
        out: Optional[dict] = None,
        token_budget: Optional[int] = None,
        enable_llm_summary: bool = True,
        physical_window: Optional[int] = None,
        completion_reserve_tokens: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """超出上下文窗口时做截断压缩；委托至 ContextCompactor。"""
        return await ContextCompactor.maybe_compact_overflow(
            full_history,
            window,
            agent_service=self,
            user_id=user_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            agent_name=agent_name,
            version_id=version_id,
            out=out,
            token_budget=token_budget,
            enable_llm_summary=enable_llm_summary,
            physical_window=physical_window,
            completion_reserve_tokens=completion_reserve_tokens,
        )

    async def manual_compact_conversation(
        self,
        user_id: str,
        conversation_id: str,
        *,
        retain_ratio: float = 0.5,
        mode: str = "fast",
    ) -> Dict[str, Any]:
        """由用户显式触发上下文压缩；委托至 ContextCompactor。"""
        return await ContextCompactor.manual_compact_conversation(
            self,
            user_id=user_id,
            conversation_id=conversation_id,
            retain_ratio=retain_ratio,
            mode=mode,
        )

    _emit_compaction_card = staticmethod(ContextCompactor.emit_compaction_card)

    def _spawn_llm_digest_task(
        self,
        full_history: List[Dict[str, Any]],
        window: List[Dict[str, Any]],
        *,
        max_chars: int = 1200,
        prev_digest: Optional[str] = None,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        version_id: Optional[str] = None,
        source_seq: int = 0,
        source_revision: Optional[int] = None,
    ) -> Optional[asyncio.Task]:
        """后台生成 LLM 语义摘要；委托至 ContextCompactor。"""
        return ContextCompactor.spawn_llm_digest_task(
            full_history,
            window,
            agent_service=self,
            max_chars=max_chars,
            prev_digest=prev_digest,
            user_id=user_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            agent_name=agent_name,
            version_id=version_id,
            source_seq=source_seq,
            source_revision=source_revision,
        )

    async def _try_llm_overflow_digest(
        self,
        full_history: List[Dict[str, Any]],
        window: List[Dict[str, Any]],
        *,
        max_chars: int = 1200,
        prev_digest: Optional[str] = None,
        agent_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        version_id: Optional[str] = None,
    ) -> Optional[Dict[str, str]]:
        """尝试用当前会话模型生成语义摘要；委托至 ContextCompactor。"""
        return await ContextCompactor.try_llm_overflow_digest(
            full_history,
            window,
            max_chars=max_chars,
            prev_digest=prev_digest,
            agent_id=agent_id,
            agent_name=agent_name,
            version_id=version_id,
        )

    @staticmethod
    async def _maybe_empty_response_fallback() -> Optional[str]:
        """模型本轮无可见文本时返回兜底话术；由配置开关控制（默认开启）。"""
        try:
            from app.services.config_service import ConfigService

            enabled_raw = await ConfigService.get("agent_empty_response_fallback_enabled", "true")
            if str(enabled_raw or "").strip().lower() not in {"1", "true", "yes", "on"}:
                return None
        except Exception:
            pass
        return AgentServicePrompts.EMPTY_RESPONSE_FALLBACK

    async def _resolve_and_verify_agent(
        self,
        *,
        messages: list[dict[str, str]],
        agent_id: Optional[str],
        agent_name: Optional[str],
        version_id: Optional[str],
        enable_multi_agent: bool,
        user_info: Optional[dict[str, Any]],
        trace_buffer: list[AgentExecutionStep],
        user_query: str,
        force_data_query: bool = False,
        quick_result_followup: bool = False,
        conversation_id: Optional[str] = None,
        route_progress: Optional[RouteProgressCallback] = None,
    ) -> tuple[Any, Any, float, Optional[str]]:
        """解析并校验智能体配置与权限。
        返回: (agent_config, route_details, route_elapsed_ms, permission_denied_err_msg)
        """
        route_start = asyncio.get_running_loop().time()
        await emit_route_stage(
            route_progress,
            "target_config",
            "加载入口专家配置",
            status="pending",
        )
        try:
            agent_config, route_details = await AgentContextManager.resolve_agent_config(
                messages,
                agent_id=agent_id,
                agent_name=agent_name,
                version_id=version_id,
                enable_multi_agent=enable_multi_agent,
                user_info=user_info,
                force_data_query=force_data_query,
                quick_result_followup=quick_result_followup,
                conversation_id=conversation_id,
                on_progress=route_progress,
            )
        except Exception:
            route_elapsed_ms = (asyncio.get_running_loop().time() - route_start) * 1000
            await emit_route_stage(
                route_progress,
                "target_config",
                "加载入口专家配置",
                status="error",
                details="入口专家配置加载失败",
                execution_time_ms=route_elapsed_ms,
            )
            raise
        route_elapsed_ms = (asyncio.get_running_loop().time() - route_start) * 1000

        await emit_route_stage(
            route_progress,
            "target_config",
            "加载入口专家配置",
            status="success" if agent_config else "error",
            details="已完成入口专家配置加载" if agent_config else "未找到可用入口专家",
            execution_time_ms=route_elapsed_ms,
        )

        if not agent_config:
            return None, None, route_elapsed_ms, None

        if route_details and getattr(route_details, "provenance", None) == "router":
            logger.info(f"[Router] Routing decision found: {route_details}")
            from app.services.config_service import ConfigService
            router_model = await ConfigService.get("llm_model_name") or "DeepSeek-V3.2"
            r_thought = getattr(route_details, "reasoning", "No reasoning")
            r_conf = getattr(route_details, "confidence", 0.0)
            r_agent = getattr(route_details, "agent_id", "unknown")
            r_turn_labels = getattr(route_details, "turn_labels", []) or []
            r_relation = getattr(route_details, "relation_to_previous", "unknown")
            r_action_type = getattr(route_details, "user_action_type", "unknown")
            r_semantic_intent = getattr(route_details, "semantic_intent", None)
            r_semantic_confidence = getattr(route_details, "semantic_confidence", None)
            r_semantic_reasoning = getattr(route_details, "semantic_reasoning", None)
            r_request_source = getattr(route_details, "source", None)
            r_request_capability = getattr(route_details, "capability", None)
            r_request_reasoning = getattr(route_details, "request_reasoning", None)
            r_chatbi_mode = getattr(route_details, "chatbi_mode", None)
            r_chatbi_evidence_level = getattr(route_details, "chatbi_evidence_level", "none")
            r_chatbi_reason = getattr(route_details, "chatbi_reason", None)
            r_matched_dataset_ids = getattr(route_details, "matched_dataset_ids", []) or []
            r_semantic_domain = getattr(route_details, "semantic_domain", "unknown")
            r_semantic_operation = getattr(route_details, "semantic_operation", "unknown")
            r_fact_kind = getattr(route_details, "fact_kind", "unknown")
            r_freshness_requirement = getattr(route_details, "freshness_requirement", "unknown")
            r_time_scope = getattr(route_details, "time_scope", None)
            r_reference_mode = getattr(route_details, "reference_mode", "unknown")
            r_needs_fresh_data = getattr(route_details, "needs_fresh_data", False)
            decision_snapshot = route_details

            trace_buffer.append(AgentExecutionStep(
                step_number=0,
                event_type="router",
                agent_name="Smart Router",
                model=router_model,
                tool_name="route_query",
                tool_input={"query": user_query},
                tool_output={
                    "thought": r_thought,
                    "selected_agent": r_agent,
                    "confidence": r_conf,
                    "turn_labels": r_turn_labels,
                    "relation_to_previous": r_relation,
                    "user_action_type": r_action_type,
                    "semantic_intent": r_semantic_intent,
                    "semantic_confidence": r_semantic_confidence,
                    "semantic_reasoning": r_semantic_reasoning,
                    "semantic_domain": r_semantic_domain,
                    "semantic_operation": r_semantic_operation,
                    "fact_kind": r_fact_kind,
                    "freshness_requirement": r_freshness_requirement,
                    "time_scope": r_time_scope,
                    "reference_mode": r_reference_mode,
                    "needs_fresh_data": r_needs_fresh_data,
                    "request_source": r_request_source,
                    "request_capability": r_request_capability,
                    "request_reasoning": r_request_reasoning,
                    "chatbi_mode": r_chatbi_mode,
                    "chatbi_evidence_level": r_chatbi_evidence_level,
                    "chatbi_reason": r_chatbi_reason,
                    "matched_dataset_ids": r_matched_dataset_ids,
                    "decision_trace": decision_snapshot.trace_payload(),
                },
                status="success",
                execution_time_ms=route_elapsed_ms
            ))
        else:
            logger.info(
                "[AgentService] No semantic router decision (direct selection or automatic delegation)"
            )

        permission_started = asyncio.get_running_loop().time()
        await emit_route_stage(
            route_progress,
            "target_permission",
            "校验入口专家权限",
            status="pending",
        )
        if user_info:
            u_role = user_info.get("role", "")
            u_id = user_info.get("user_id", user_info.get("id"))
            if u_role != "admin" and u_id:
                from app.services.permission_service import PermissionService
                async with AsyncSessionLocal() as session:
                    perm_service = PermissionService(session)
                    agent_id_str = str(agent_config.agent_id)
                    has_perm = await perm_service.check_permission(int(u_id), "agent", agent_id_str)
                    if not has_perm:
                        err_msg = AgentServicePrompts.permission_denied(agent_config.agent_name)
                        await emit_route_stage(
                            route_progress,
                            "target_permission",
                            "校验入口专家权限",
                            status="error",
                            details="入口专家权限校验失败",
                            execution_time_ms=(asyncio.get_running_loop().time() - permission_started) * 1000,
                        )
                        return agent_config, route_details, route_elapsed_ms, err_msg

        await emit_route_stage(
            route_progress,
            "target_permission",
            "校验入口专家权限",
            status="success",
            details="已完成入口专家权限校验",
            execution_time_ms=(asyncio.get_running_loop().time() - permission_started) * 1000,
        )

        return agent_config, route_details, route_elapsed_ms, None

    def _start_route_resolution(
        self,
        *,
        route_events: "asyncio.Queue[Dict[str, Any]]",
        resolve_kwargs: Dict[str, Any],
    ) -> "asyncio.Task[tuple[Any, Any, float, Optional[str]]]":
        """Start target resolution while forwarding safe progress events."""

        async def on_progress(event: Dict[str, Any]) -> None:
            # 入口专家配置与权限校验都是准备阶段的平级步骤；旧的路由明细
            # 仍允许挂到 route:target_config，兼容历史自动路由事件结构。
            if isinstance(event, dict) and event.get("type") == "log":
                event = dict(event)
                if event.get("id") in {"route:target_config", "route:target_permission"}:
                    event["parent_id"] = "preparation:auth_context_capability"
                else:
                    event["parent_id"] = "route:target_config"
            await route_events.put(event)

        return asyncio.create_task(
            self._resolve_and_verify_agent(
                **resolve_kwargs,
                route_progress=on_progress,
            )
        )

    async def _inject_skills(
        self,
        *,
        messages: list[dict[str, str]],
        user_query: str,
        agent_config: Any,
        user_info: Optional[dict[str, Any]] = None,
        skills_log_callback: Optional[callable] = None,
        resource_scope: Optional[dict[str, Any]] = None,
    ) -> list[str]:
        """挂载与自动匹配技能，委托给 SkillInjector。"""
        return await SkillInjector.inject_skills(
            messages=messages,
            user_query=user_query,
            agent_config=agent_config,
            user_info=user_info,
            skills_log_callback=skills_log_callback,
            resource_scope=resource_scope,
        )

    async def _load_memory_context(
        self,
        *,
        user_info: Optional[dict[str, Any]],
        early_turn_kind: str,
        debug_options: Optional[dict[str, Any]],
        user_query: str,
    ) -> tuple[Optional[str], Optional[dict], Optional[str], Optional[str]]:
        """加载记忆与 LTM 预加载。
        返回: (ltm_profile, ltm_loaded_data, memory_recall_hint, preloaded_memories_text)
        """
        ltm_profile: Optional[str] = None
        ltm_loaded_data: Optional[dict] = None
        ignore_ltm = False
        if debug_options and debug_options.get("ignore_ltm"):
            ignore_ltm = True

        if not ignore_ltm and should_inject_ltm(early_turn_kind) and user_info:
            u_id = user_info.get("user_id", user_info.get("id"))
            if u_id:
                try:
                    from app.services.ai.memory_service import ltm_service
                    ltm_data = await asyncio.wait_for(ltm_service.fetch_memory(str(u_id)), timeout=0.2)
                    if ltm_data:
                        import json
                        ltm_formatted = json.dumps(ltm_data, ensure_ascii=False, indent=2)
                        ltm_profile = AgentServicePrompts.ltm_memory_profile(ltm_formatted)
                        ltm_loaded_data = ltm_data
                        logger.info(f"[LTM] Successfully loaded memory profile for user {u_id}")
                except Exception as ltm_err:
                    logger.warning(f"[LTM] Failed to inject long-term memory for user {u_id}: {ltm_err}")

        memory_recall_hint: Optional[str] = None
        if should_inject_memory_recall_hint(early_turn_kind):
            try:
                from app.services.memory_config_service import MemoryConfigService
                from app.services.ai.memory_recall_policy import CROSS_SESSION_MEMORY_SYSTEM_HINT

                if await MemoryConfigService.get_bool("memory_service_enabled", True):
                    memory_recall_hint = CROSS_SESSION_MEMORY_SYSTEM_HINT
            except Exception as mem_hint_err:
                logger.warning("[Memory] Failed to inject cross-session recall hint: %s", mem_hint_err)

        preloaded_memories_text: Optional[str] = None
        if should_run_active_memory_preload(early_turn_kind) and user_info and user_query:
            u_id = user_info.get("user_id", user_info.get("id"))
            if u_id:
                try:
                    from app.services.memory_config_service import MemoryConfigService
                    if await MemoryConfigService.get_bool("memory_service_enabled", True):
                        from app.services.ai.tools.memory_search_tool import parse_date_from_query
                        from app.services.ai.daily_summary_service import DailySummaryService
                        from app.services.ai.memory_index_service import MemoryIndexService

                        uid = str(u_id)
                        target_day = parse_date_from_query(user_query)
                        preloaded_memories = []

                        if target_day:
                            d_summary, d_sessions = await asyncio.gather(
                                DailySummaryService.get_daily_summary(uid, target_day),
                                MemoryIndexService.list_session_summaries_for_day(uid, target_day),
                            )
                            if d_summary:
                                preloaded_memories.append(
                                    AgentServicePrompts.daily_summary_section(target_day, d_summary)
                                )
                            if d_sessions:
                                sess_lines = []
                                for idx, s in enumerate(d_sessions, 1):
                                    sess_lines.append(
                                        AgentServicePrompts.session_summary_line(idx, s)
                                    )
                                preloaded_memories.append(
                                    AgentServicePrompts.day_session_records(target_day, sess_lines)
                                )
                        else:
                            is_recall_intent = any(w in user_query for w in AgentServicePrompts.RECALL_INTENT_KEYWORDS)
                            if is_recall_intent:
                                recent_sessions = await MemoryIndexService.list_summaries(uid, limit=3)
                                if recent_sessions:
                                    sess_lines = []
                                    for idx, s in enumerate(recent_sessions, 1):
                                        sess_lines.append(
                                            AgentServicePrompts.session_summary_line(idx, s)
                                        )
                                    preloaded_memories.append(
                                        AgentServicePrompts.recent_sessions_section(sess_lines)
                                    )

                        if preloaded_memories:
                            preloaded_memories_text = AgentServicePrompts.preloaded_memories(preloaded_memories)
                            logger.info(f"[ActiveMemory] Successfully preloaded memory context for user {u_id}")
                except Exception as recall_err:
                    logger.warning(f"[ActiveMemory] Failed to preload memory context: {recall_err}", exc_info=True)

        return ltm_profile, ltm_loaded_data, memory_recall_hint, preloaded_memories_text

    async def _dispatch_executor(
        self,
        *,
        agent_config: Any,
        user_query: str,
        messages: list[dict[str, str]],
        trace_id: str,
        trace_buffer: list[AgentExecutionStep],
        debug_options: Optional[dict[str, Any]],
        permission_options: Optional[dict[str, Any]],
        user_info: Optional[dict[str, Any]],
        conversation_id: Optional[str],
        turn_decision: Optional[TurnDecision] = None,
    ) -> Any:
        """调度并返回执行器实例。"""
        executor = await AgentDispatcher.dispatch(
            agent_config,
            user_query,
            messages,
            trace_id,
            trace_buffer,
            debug_options,
            permission_options,
            user_info,
            conversation_id,
            turn_decision=turn_decision,
        )
        return executor

    async def _resolve_reusable_result_decision(
        self,
        *,
        user_info: Optional[Dict[str, Any]],
        conversation_id: Optional[str],
        user_query: str,
        preferred_result_id: Optional[str] = None,
        allowed_result_types: Collection[str] | None = None,
    ) -> Any:
        """在进入路由/执行器前读取会话结果，Redis 故障时无感降级。"""
        if not user_info or not conversation_id:
            from app.services.ai.reusable_result import ReusableResultDecision

            return ReusableResultDecision(mode="none")
        raw_user_id = user_info.get("user_id") or user_info.get("id")
        if not raw_user_id:
            from app.services.ai.reusable_result import ReusableResultDecision

            return ReusableResultDecision(mode="none")
        try:
            from app.services.ai.reusable_result import resolve_reusable_result

            current, stack = await asyncio.gather(
                memory_service.get_reusable_result(str(raw_user_id), conversation_id),
                memory_service.get_reusable_result_stack(str(raw_user_id), conversation_id),
            )
            return resolve_reusable_result(
                user_query,
                current=current,
                stack=stack,
                preferred_result_id=preferred_result_id,
                allowed_result_types=allowed_result_types,
            )
        except Exception as exc:
            logger.warning("[ReusableResult] pre-route resolve failed: %s", exc)
            from app.services.ai.reusable_result import ReusableResultDecision

            return ReusableResultDecision(mode="none", reason="resolver_unavailable")

    async def _gather_turn_preflight_context(
        self,
        *,
        agent_config: Any,
        user_info: Optional[Dict[str, Any]],
        user_query: str,
        turn_decision: Any,
        messages: List[Dict[str, str]],
        debug_options: Optional[Dict[str, Any]],
    ) -> TurnPreflightContext:
        """并发预取技能、长期记忆、用户画像、权限目录、专家花名册与有效工具清单 (P0 TTFT 并发提速)"""
        matched_skills_to_log: List[tuple] = []
        def skills_log_callback(skill_id: str, skill_name: str, details_msg: str) -> None:
            matched_skills_to_log.append((skill_id, skill_name, details_msg))

        early_turn_kind = getattr(turn_decision, "turn_kind", None)
        accessible_resources = (
            getattr(turn_decision, "accessible_resources", None)
            if early_turn_kind != "data_query"
            else None
        )
        agent_system_prompt = getattr(agent_config, "system_prompt", "") or ""

        async def _fetch_skills() -> List[str]:
            try:
                return await self._inject_skills(
                    messages=messages,
                    user_query=user_query,
                    agent_config=agent_config,
                    user_info=user_info,
                    skills_log_callback=skills_log_callback,
                    resource_scope=(debug_options or {}).get("resource_scope"),
                )
            except Exception as err:
                logger.warning(f"Error in concurrent _inject_skills: {err}")
                return []

        async def _fetch_memory():
            try:
                return await self._load_memory_context(
                    user_info=user_info,
                    early_turn_kind=early_turn_kind,
                    debug_options=debug_options,
                    user_query=user_query,
                )
            except Exception as err:
                logger.warning(f"Error in concurrent _load_memory_context: {err}")
                return None, None, None, None

        async def _fetch_user_context() -> Optional[str]:
            try:
                if user_info and should_inject_user_context(early_turn_kind):
                    id_msg = await self._build_user_context_msg(user_info)
                    return id_msg.get("content")
            except Exception as err:
                logger.warning(f"Error in concurrent _build_user_context_msg: {err}")
            return None

        async def _fetch_catalog() -> Any:
            if early_turn_kind != "data_query" and not accessible_resources and user_info:
                try:
                    from app.services.ai.accessible_resource_catalog import (
                        build_accessible_resource_catalog,
                    )
                    raw_resource_user_id = user_info.get("user_id") or user_info.get("id")
                    resource_user_id = None
                    if raw_resource_user_id is not None:
                        try:
                            resource_user_id = int(raw_resource_user_id)
                        except (TypeError, ValueError):
                            resource_user_id = None
                    return await build_accessible_resource_catalog(
                        user_id=resource_user_id,
                        user_name=(user_info.get("user_name") or user_info.get("username")),
                        is_admin=user_info.get("role") == "admin",
                    )
                except Exception as err:
                    logger.warning(f"Error in concurrent build_accessible_resource_catalog: {err}")
            return accessible_resources

        async def _fetch_roster():
            from app.services.ai.skill_resolver import is_main_general_agent
            has_subagent_tool = any(
                (isinstance(t, str) and t in ("sub_agent_call", "sub_agent_batch_call"))
                or (isinstance(t, dict) and t.get("name") in ("sub_agent_call", "sub_agent_batch_call"))
                or (getattr(t, "name", None) in ("sub_agent_call", "sub_agent_batch_call"))
                for t in (getattr(agent_config, "tools", None) or [])
            )
            if not (is_main_general_agent(agent_config) or has_subagent_tool):
                return None, 0, False, agent_system_prompt, None

            try:
                from app.core.orm import AsyncSessionLocal
                from app.models.agent import AIAgent
                from app.services.ai.agent_roster import (
                    AGENT_ROSTER_PLACEHOLDER,
                    build_sub_agents_context,
                    format_agent_roster_markdown,
                    inject_agent_roster,
                    resolve_delegable_system_agents_for_user,
                )

                async with AsyncSessionLocal() as session:
                    delegable_agents = await resolve_delegable_system_agents_for_user(
                        session,
                        user_info=user_info,
                        current_agent_id=getattr(agent_config, "agent_id", None),
                    )
                    delegable_agent_count = len(delegable_agents or [])
                    roster_loaded = True
                    current_agent_row = await session.get(AIAgent, getattr(agent_config, "agent_id", None))
                    current_desc = (current_agent_row.description if current_agent_row else "") or ""
                    updated_prompt = agent_system_prompt
                    if AGENT_ROSTER_PLACEHOLDER in (agent_system_prompt or ""):
                        roster_md = format_agent_roster_markdown(
                            delegable_agents,
                            current_display_name=getattr(agent_config, "agent_display_name", None) or getattr(agent_config, "agent_name", None) or "主助手",
                            current_description=current_desc,
                        )
                        updated_prompt = inject_agent_roster(agent_system_prompt, roster_md)
                    sub_agents_context = build_sub_agents_context(delegable_agents)
                    return delegable_agents, delegable_agent_count, roster_loaded, updated_prompt, sub_agents_context
            except Exception as sa_err:
                logger.warning(f"Failed to build main-agent roster/sub-agents context: {sa_err}")
                return None, 0, False, agent_system_prompt, None

        async def _fetch_tools() -> List[str]:
            try:
                from app.services.ai.prompt_assembler import (
                    resolve_effective_prompt_tool_names_for_turn,
                )
                return await resolve_effective_prompt_tool_names_for_turn(
                    agent_config,
                    current_user_query=user_query,
                    turn_decision=turn_decision,
                )
            except Exception as err:
                logger.warning(f"Error in concurrent resolve_effective_prompt_tool_names_for_turn: {err}")
                return []

        (
            skills_injection,
            (ltm_profile, ltm_loaded_data, memory_recall_hint, preloaded_memories_text),
            user_profile,
            res_accessible_resources,
            (delegable_agents, delegable_agent_count, roster_loaded, updated_prompt, sub_agents_context),
            effective_prompt_tool_names,
        ) = await asyncio.gather(
            _fetch_skills(),
            _fetch_memory(),
            _fetch_user_context(),
            _fetch_catalog(),
            _fetch_roster(),
            _fetch_tools(),
        )

        return TurnPreflightContext(
            skills_injection=skills_injection,
            matched_skills_to_log=matched_skills_to_log,
            effective_prompt_tool_names=effective_prompt_tool_names,
            ltm_profile=ltm_profile,
            ltm_loaded_data=ltm_loaded_data,
            memory_recall_hint=memory_recall_hint,
            preloaded_memories_text=preloaded_memories_text,
            user_profile=user_profile,
            accessible_resources=res_accessible_resources,
            delegable_agents=delegable_agents,
            delegable_agent_count=delegable_agent_count,
            roster_loaded=roster_loaded,
            agent_system_prompt=updated_prompt,
            sub_agents_context=sub_agents_context,
        )


    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        agent_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        version_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        user_info: Optional[Dict[str, Any]] = None,
        api_key: Optional[str] = None,
        enable_multi_agent: bool = True,
        debug_options: Optional[Dict[str, Any]] = None,
        permission_options: Optional[Dict[str, Any]] = None,
        knowledge_dataset_ids: Optional[List[str]] = None,
        metadata_dataset_ids: Optional[List[str]] = None,
        reusable_result_id: Optional[str] = None,
        quick_context: Optional[Dict[str, Any]] = None,
        request_observability: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Non-streaming wrapper for chat completion.
        Consumes the stream and returns the final result.
        """
        full_content = ""
        full_reasoning_content = ""
        trace_id = ""
        agent_name_resp = ""
        final_status = "success"

        async for chunk in self.chat_completion_stream(
            messages,
            agent_id=agent_id,
            agent_name=agent_name,
            version_id=version_id,
            conversation_id=conversation_id,
            user_info=user_info,
            api_key=api_key,
            enable_multi_agent=enable_multi_agent,
            debug_options=debug_options,
            permission_options=permission_options,
            knowledge_dataset_ids=knowledge_dataset_ids,
            metadata_dataset_ids=metadata_dataset_ids,
            reusable_result_id=reusable_result_id,
            quick_context=quick_context,
            request_observability=request_observability,
        ):
            if "trace_id" in chunk and chunk.get("status") == "init":
                trace_id = chunk["trace_id"]
            final_status = _apply_turn_status_signal(final_status, chunk)
            full_content = _accumulate_stream_content(full_content, chunk)
            full_reasoning_content = _accumulate_reasoning_content(
                full_reasoning_content, chunk
            )
            if "agent_name" in chunk:
                agent_name_resp = chunk["agent_name"]

        if self._should_forbid_quick_suggestions(user_info):
            from app.services.ai.runtime.agentscope.stream_reconcile import suppress_quick_suggestions

            full_content = suppress_quick_suggestions(full_content)

        from app.services.ai.runtime.agentscope.text_sanitize import strip_model_reasoning_from_answer

        # 与 EmbedChat 一致：任务侧只用正文，不含「模型思考推理」折叠面板内容
        full_content = strip_model_reasoning_from_answer(
            full_content,
            reasoning_content=full_reasoning_content or None,
        )

        return {
            "content": full_content,
            "reasoning_content": full_reasoning_content or None,
            "intent": "general_chat",
            "trace_id": trace_id,
            "agent_name": agent_name_resp,
            "status": final_status,
        }

    async def resume_agentscope_permission_stream(
        self,
        *,
        permission_request_id: str,
        confirmed: bool,
        user_info: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """恢复 AgentScope 工具权限确认流；委托至 AgentScopeResumeHandler。"""
        from app.services.ai.runtime.agentscope.resume import AgentScopeResumeHandler

        async for chunk in AgentScopeResumeHandler.resume_permission_stream(
            self,
            permission_request_id=permission_request_id,
            confirmed=confirmed,
            user_info=user_info,
        ):
            yield chunk

    async def _restore_runner_execution_context(
        self,
        runner: Any,
        pending: Any,
        *,
        user_info: Optional[Dict[str, Any]] = None,
    ) -> None:
        from app.services.ai.runtime.agentscope.resume import AgentScopeResumeHandler

        await AgentScopeResumeHandler.restore_runner_execution_context(
            self, runner, pending, user_info=user_info
        )

    def _build_agentscope_runner_from_pending(
        self,
        pending: Any,
        *,
        user_info: Optional[Dict[str, Any]] = None,
    ) -> Any:
        from app.services.ai.runtime.agentscope.resume import AgentScopeResumeHandler

        return AgentScopeResumeHandler.build_agentscope_runner_from_pending(
            pending, user_info=user_info
        )

    @staticmethod
    def _build_external_execution_results(results: List[Dict[str, Any]]) -> List[Any]:
        from app.services.ai.runtime.agentscope.resume import AgentScopeResumeHandler

        return AgentScopeResumeHandler.build_external_execution_results(results)

    async def resume_agentscope_external_execution_stream(
        self,
        *,
        external_execution_request_id: str,
        results: List[Dict[str, Any]],
        user_info: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """恢复 AgentScope 外部工具执行流；委托至 AgentScopeResumeHandler。"""
        from app.services.ai.runtime.agentscope.resume import AgentScopeResumeHandler

        async for chunk in AgentScopeResumeHandler.resume_external_execution_stream(
            self,
            external_execution_request_id=external_execution_request_id,
            results=results,
            user_info=user_info,
        ):
            yield chunk

    async def _execute_multi_agent(
        self,
        primary_config: ChatConfig,
        secondary_agent_ids: List[str],
        user_query: str,
        messages: List[Dict[str, str]],
        trace_id: str,
        trace_buffer: List[AgentExecutionStep],
        debug_options: Dict[str, Any],
        permission_options: Optional[Dict[str, Any]],
        user_info: Optional[Dict[str, Any]],
        api_key: Optional[str],
        conversation_id: Optional[str] = None,
        turn_decision: Optional[TurnDecision] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """并行调度多专家智能体；委托至 MultiAgentOrchestrator。"""
        from app.services.ai.multi_agent_orchestrator import MultiAgentOrchestrator

        async for chunk in MultiAgentOrchestrator.execute_multi_agent(
            self,
            primary_config,
            secondary_agent_ids,
            user_query,
            messages,
            trace_id,
            trace_buffer,
            debug_options,
            permission_options,
            user_info,
            api_key,
            conversation_id=conversation_id,
            turn_decision=turn_decision,
        ):
            yield chunk

    async def _synthesize_multi_agent_results(
        self,
        config: ChatConfig,
        user_query: str,
        agent_outputs: List[Dict[str, str]],
        trace_buffer: List[AgentExecutionStep],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """合成多专家输出；委托至 MultiAgentOrchestrator。"""
        from app.services.ai.multi_agent_orchestrator import MultiAgentOrchestrator

        async for chunk in MultiAgentOrchestrator.synthesize_multi_agent_results(
            config,
            user_query,
            agent_outputs,
            trace_buffer,
        ):
            yield chunk

    def __init__(self):
        pass


agent_service = AgentService()
