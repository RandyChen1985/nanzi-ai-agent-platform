import logging
import time
import uuid
import asyncio
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
from app.services.ai.context_compaction_log_service import context_compaction_log_service
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
from app.services.ai.runtime.agentscope.tool_result_context import is_trusted_tool_result_context
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


def _history_messages_for_context(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """仅把模型需要的消息字段放回上下文，历史展示元数据不参与模型请求。

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


def _trusted_tool_run_text(message: Dict[str, Any]) -> str:
    """只为上下文预算统计读取已标记版本的最终工具结果。"""

    if not is_trusted_tool_result_context(message):
        return ""
    return str(message.get("tool_run_text") or "")


def _window_for_context(
    server_history: List[Dict[str, Any]],
    max_context_messages: int,
    max_tokens: int,
) -> List[Dict[str, Any]]:
    """C 项：用 token 预算主导窗口，条数上限仅作绝对兜底。

    从历史尾部（最近）倒序累计每条消息的估算 token（``content`` 与工具摘要
    ``tool_run_text``（仅最终成功工具结果）均计入，与
    convert_history_to_messages 实际注入模型的内容一致）。

    **优先级（token 优先）：**
    1. 首选由 token 预算 ``max_tokens``（默认 64k）决定保留多少历史 —— 这是主约束；
    2. 条数上限 ``max_context_messages`` 仅作为最后兜底，防止在极端情况下（例如
       单条消息 token 估算严重偏低、工具元数据条数异常多）窗口无限累积膨胀。

    返回保持历史顺序的窗口切片。

    仅用于上下文选择层，不触碰展示路径（get_conversation_history 仍原样返回）；
    与 :meth:`_maybe_compact_overflow` 的 ``len(full) <= len(window)`` 溢出判断天然兼容
    —— 被 token 预算丢弃的尾部旧消息同样会被压缩成 system 摘录。
    """
    if not server_history:
        return []
    total_tokens = 0
    kept = 0
    cut_index: Optional[int] = None
    for idx in range(len(server_history) - 1, -1, -1):
        msg = server_history[idx]
        if not isinstance(msg, dict):
            continue
        content = str(msg.get("content") or "")
        tool_text = _trusted_tool_run_text(msg)
        total_tokens += estimate_text_tokens(content + tool_text)
        kept += 1
        # 主判据：token 预算超限即截断（token 优先）
        if total_tokens > max_tokens:
            cut_index = idx
            break
        # 兜底判据：条数超限（仅在 token 未超限时触发，防止极端膨胀）
        if kept > max_context_messages:
            cut_index = idx
            break
    if cut_index is None:
        return server_history
    return server_history[cut_index + 1:]


def _client_prefix_history_len(messages: List[Dict[str, Any]]) -> int:
    """统计客户端提交的真实对话前缀，忽略 UI 分隔用的 system 消息。"""
    return sum(
        1
        for message in messages[:-1]
        if isinstance(message, dict)
        and message.get("role") in {"user", "assistant"}
    )


async def _apply_context_snapshot(
    history: List[Dict[str, Any]],
    *,
    user_id: str,
    conversation_id: str,
) -> List[Dict[str, Any]]:
    """将手动压缩快照与快照创建后的新消息合并，原始历史保持不变。"""
    snapshot = await memory_service.get_context_snapshot(user_id, conversation_id)
    if not isinstance(snapshot, dict):
        return history
    return memory_service.merge_context_snapshot(history, snapshot)


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
        context_size = info.context_size or "未返回"
        output_tokens = info.max_output_tokens or "未返回"
        return (
            f"{label}：{info.effective_model_id}（配置：{info.configured_model}，"
            f"来源：{info.source}，上下文：{context_size}，"
            f"最大输出：{output_tokens}，状态：{info.resolution_status}）"
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
        """尽力记录压缩事件；Redis 故障不能影响当前 SSE 主链路。"""
        if not user_id or not conversation_id or not event:
            return
        try:
            await asyncio.wait_for(
                context_compaction_log_service.append_event(
                    event=event,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                    source=source,
                    stage=stage,
                    agent_name=agent_name,
                    model_name=model_name,
                ),
                timeout=context_compaction_log_service.APPEND_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.warning(
                "[AgentService] Failed to persist context compaction event",
                exc_info=True,
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

    async def _resolve_skill_full_load_policy(self) -> Dict[str, Any]:
        import asyncio

        from app.services.config_service import ConfigService

        (
            enabled_raw,
            min_score_raw,
            max_count_raw,
            max_bytes_raw,
        ) = await asyncio.gather(
            ConfigService.get("skill_auto_full_load_enabled", "true"),
            ConfigService.get("skill_auto_full_load_min_score", "0.75"),
            ConfigService.get("skill_auto_full_load_max_count", "1"),
            ConfigService.get("skill_auto_full_load_max_bytes", "65536"),
        )
        return {
            "enabled": self._parse_bool_config(enabled_raw, True),
            "min_score": self._parse_float_config(min_score_raw, 0.75),
            "max_count": self._parse_int_config(max_count_raw, 1, min_value=0, max_value=3),
            "max_bytes": self._parse_int_config(max_bytes_raw, 65536, min_value=1024, max_value=262144),
        }

    @staticmethod
    def _should_preload_skill_full_instruction(
        *,
        match_source: str,
        match_score: Any = None,
        policy: Dict[str, Any],
        loaded_count: int,
    ) -> bool:
        if not policy.get("enabled"):
            return False
        if loaded_count >= int(policy.get("max_count") or 0):
            return False
        if match_source in {"mounted", "mention"}:
            return True
        if match_source == "scan":
            try:
                return float(match_score) >= float(policy.get("min_score") or 0.75)
            except (TypeError, ValueError):
                return False
        return False

    @staticmethod
    def _is_new_session_first_user_turn(messages: Optional[List[Dict[str, Any]]]) -> bool:
        """Whether the current context only contains the first user turn."""
        if not messages:
            return False
        conversation_roles = [
            str(m.get("role") or "").strip().lower()
            for m in messages
            if str(m.get("role") or "").strip().lower() in {"user", "assistant", "agent"}
        ]
        return conversation_roles == ["user"]

    @classmethod
    def _should_force_preload_scanned_skill(
        cls,
        *,
        skill_id: str,
        messages: Optional[List[Dict[str, Any]]],
    ) -> bool:
        return (
            skill_id == cls.USING_SUPERPOWERS_SKILL_ID
            and cls._is_new_session_first_user_turn(messages)
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
        """Ensure using-superpowers is considered on the first user turn (any agent)."""
        if not cls._is_new_session_first_user_turn(messages):
            return scanned_skills
        excluded = exclude_ids or set()
        if cls.USING_SUPERPOWERS_SKILL_ID in excluded:
            return scanned_skills
        if any(skill.get("id") == cls.USING_SUPERPOWERS_SKILL_ID for skill in scanned_skills):
            return scanned_skills

        for skill in available_skills:
            if skill.get("id") != cls.USING_SUPERPOWERS_SKILL_ID:
                continue
            item = dict(skill)
            item["match_score"] = 1.0
            item["match_source"] = "scan"
            item["force_first_turn"] = True
            return [item] + scanned_skills
        return scanned_skills

    def _append_first_turn_superpowers(
        self,
        *,
        messages: Optional[List[Dict[str, Any]]],
        agent_config: Any,
        user_info: Optional[Dict[str, Any]],
        skills_injection: List[str],
        mounted_skill_ids: set[str],
        full_load_policy: Dict[str, Any],
        full_loaded_count: int,
        skills_log_callback: Optional[callable] = None,
    ) -> int:
        """所有智能体：新会话首轮强制预载 using-superpowers（完整指令）。"""
        if not self._is_new_session_first_user_turn(messages):
            return full_loaded_count
        if self.USING_SUPERPOWERS_SKILL_ID in mounted_skill_ids:
            return full_loaded_count

        from app.services.ai.skill_resolver import (
            list_skill_metas,
            load_skill_md_content,
            skill_filter_kwargs_from_config,
        )

        skill_filter = skill_filter_kwargs_from_config(agent_config)
        available_skills = list_skill_metas(user_info=user_info, **skill_filter)
        skill_meta = next(
            (
                skill
                for skill in available_skills
                if skill.get("id") == self.USING_SUPERPOWERS_SKILL_ID
            ),
            None,
        )
        # 首轮门禁：即便 skills_custom 白名单未包含，也尽量从全局技能库加载
        if skill_meta is None and skill_filter.get("skills_custom"):
            available_skills = list_skill_metas(
                user_info=user_info,
                skills_custom=False,
                allowed_global_skills=None,
            )
            skill_meta = next(
                (
                    skill
                    for skill in available_skills
                    if skill.get("id") == self.USING_SUPERPOWERS_SKILL_ID
                ),
                None,
            )
        if not skill_meta:
            return full_loaded_count

        skill_id = self.USING_SUPERPOWERS_SKILL_ID
        skill_name = skill_meta.get("name") or skill_id
        description = skill_meta.get("description") or ""
        full_instruction = load_skill_md_content(
            skill_id,
            max_bytes=int(full_load_policy["max_bytes"]),
            user_info=user_info,
            scope=skill_meta.get("scope"),
            skill_md_path=skill_meta.get("skill_md_path"),
        )
        if full_instruction:
            full_loaded_count += 1
        skills_injection.append(
            self._build_skill_injection(
                skill_name=skill_name,
                skill_id=skill_id,
                description=description,
                full_instruction=full_instruction,
            )
        )
        mounted_skill_ids.add(skill_id)
        logger.info(
            "[Skills] First-turn gate preloaded %s for agent=%s (%s).",
            skill_id,
            getattr(agent_config, "agent_id", None) or getattr(agent_config, "agent_name", None),
            "full instruction" if full_instruction else "summary only",
        )
        if skills_log_callback:
            if full_instruction:
                details_msg = (
                    f"新会话首轮门禁已强制启用；已预载「{skill_name}」(ID: {skill_id}) "
                    "完整 SKILL.md 指令，本轮可直接按该流程执行。"
                )
            else:
                details_msg = (
                    f"新会话首轮门禁已启用「{skill_name}」(ID: {skill_id})，"
                    "但未能读取完整指令；模型须调用 read_skill_instruction。"
                )
            skills_log_callback(skill_id, skill_name, details_msg)
        return full_loaded_count

    @staticmethod
    def _build_skill_injection(
        *,
        skill_name: str,
        skill_id: str,
        description: str,
        full_instruction: Optional[str] = None,
    ) -> str:
        if full_instruction:
            return AgentServicePrompts.skill_full_instruction_block(
                skill_name,
                skill_id,
                description,
                full_instruction,
            )
        return AgentServicePrompts.skill_summary_injection_block(
            skill_name,
            skill_id,
            description,
        )

    @staticmethod
    def _build_skill_log_chunk(skill_id: str, skill_name: str, details_msg: str) -> Dict[str, Any]:
        details = details_msg or (
            f"已识别候选流程「{skill_name}」(ID: {skill_id})。"
            "当前仅加载流程摘要；若本轮确需执行，系统会读取完整流程说明后再处理。"
        )
        is_full_enabled = "已预载完整" in details or "可直接按该流程执行" in details
        if is_full_enabled:
            return {
                "type": "log",
                "id": f"skill_enabled_{skill_id}",
                "title": f"已启用流程: {skill_name}",
                "details": details,
                "status": "success",
            }

        user_facing_details = details
        if "read_skill_instruction" in user_facing_details:
            user_facing_details = (
                f"已识别候选流程「{skill_name}」(ID: {skill_id})。"
                "当前仅加载流程摘要；若本轮确需执行，系统会读取完整流程说明后再处理。"
            )
        return {
            "type": "log",
            "id": f"skill_candidate_{skill_id}",
            "title": f"已识别候选流程: {skill_name}",
            "details": user_facing_details,
            "status": "success",
        }

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
        agent_config = None
        user_query = ""
        full_response_content = ""
        user_question_cancelled = False
        shared_state = {
            "agent_config": None,
            "execution_status": "success",
            "process_timeline": [],
            "preparation_started_at": None,
            "preparation_ready": False,
        }

        # 1. Initial Identity Chunk
        yield {"trace_id": trace_id, "status": "init"}

        lane_user_id = required_user_id

        if user_info:
            quota_block = await self._quota_block_message(user_info)
            if quota_block:
                yield {
                    "type": "error",
                    "status": "quota_exceeded",
                    "content": quota_block,
                    "trace_id": trace_id,
                }
                return

        waiting_log_emitted = False
        queue_start_time = asyncio.get_running_loop().time()
        if conversation_id and await conversation_run_lane.is_locked(user_id=lane_user_id, conversation_id=conversation_id):
            waiting_log_emitted = True
            yield {
                "type": "log",
                "id": "session:queue_wait",
                "title": "等待上一次会话任务完成",
                "details": "检测到当前会话有未结束的任务，正在排队等待释放资源...",
                "status": "pending",
                "category": "system",
            }

        try:
            async with track_conversation_run(
                lane_user_id, conversation_id
            ) as run_handle, conversation_run_lane.hold(
                user_id=lane_user_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
            ):
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
                from app.services.ai.executors.common import sanitize_client_messages_for_identity
                from app.services.ai.reusable_result import (
                    CLICKED_REPLY_MARKER,
                    prepare_reusable_route_input,
                )

                messages = sanitize_client_messages_for_identity(messages)
                # 快捷按钮会把被点击的整段 AI 回复附在当前 user content 后面。
                # 这段内容只用于客户端展示/定位，不能进入历史、附件授权、技能注入或执行器。
                if messages:
                    raw_latest_user_content = str(
                        messages[-1].get("content") or ""
                    ) if isinstance(messages[-1], dict) else ""
                    messages, cleaned_user_query = prepare_reusable_route_input(
                        messages,
                        raw_latest_user_content,
                    )
                    if CLICKED_REPLY_MARKER.lower() in raw_latest_user_content.lower():
                        shared_state["clicked_reusable_reply"] = True

                # AI 提问卡的回执必须先由服务端按 user/conversation/question 校验，
                # 不能把客户端拼接的选项直接交给模型自行解释。
                incoming_user_message = messages[-1] if messages else None
                incoming_content = (
                    incoming_user_message.get("content")
                    if isinstance(incoming_user_message, dict)
                    else None
                )
                from app.services.ai.user_question import (
                    is_user_question_receipt_message,
                    metadata_dataset_ids_from_user_question_record,
                    parse_user_question_receipt,
                )

                if is_user_question_receipt_message(incoming_content):
                    receipt = parse_user_question_receipt(incoming_content)
                    if not receipt or not conversation_id:
                        yield {
                            "type": "error",
                            "status": "error",
                            "content": "用户回答格式无效或当前会话无法恢复问题，请重新发起问题。",
                            "trace_id": trace_id,
                        }
                        return
                    from app.services.ai.user_question_store import UserQuestionStore

                    try:
                        question_store = await UserQuestionStore.from_runtime()
                        submitted_question = await question_store.submit_answer(
                            user_id=lane_user_id,
                            conversation_id=conversation_id,
                            question_id=receipt["question_id"],
                            selected_option_ids=receipt["selected_option_ids"],
                            custom_input=receipt["custom_input"],
                            cancelled=receipt["cancelled"],
                        )
                        user_question_cancelled = bool(receipt["cancelled"])
                        restored_dataset_ids = metadata_dataset_ids_from_user_question_record(
                            submitted_question
                        )
                        if restored_dataset_ids:
                            metadata_dataset_ids = restored_dataset_ids
                            debug_options["metadata_dataset_scope"] = {
                                "source": "user_question",
                                "request_ids": restored_dataset_ids,
                            }
                    except (PermissionError, ValueError) as exc:
                        yield {
                            "type": "error",
                            "status": "error",
                            "content": f"用户回答未通过校验：{exc}",
                            "trace_id": trace_id,
                        }
                        return
                    except Exception:
                        logger.exception("Failed to validate user-question receipt")
                        yield {
                            "type": "error",
                            "status": "error",
                            "content": "当前无法验证用户回答，请稍后重试。",
                            "trace_id": trace_id,
                        }
                        return

                # 请求级前置校验已经在 API 层完成；这里将结果以安全摘要写入
                # 原有执行时间线，供 EmbedChat 和 AgentDebug 共同展示。
                shared_state["preparation_started_at"] = asyncio.get_running_loop().time()
                preparation_parent_log = _build_preparation_parent_log(status="pending")
                _track_process_timeline(shared_state["process_timeline"], preparation_parent_log)
                yield preparation_parent_log
                request_validation_log = _build_request_validation_log(
                    user_info=user_info,
                    conversation_id=conversation_id,
                    request_observability=request_observability,
                )
                _track_process_timeline(shared_state["process_timeline"], request_validation_log)
                yield request_validation_log

                # --- Memory Integration ---
                # If conversation_id is provided, we use server-side history
                user_msg = (
                    messages[-1]
                    if messages
                    and isinstance(messages[-1], dict)
                    and messages[-1].get("role") == "user"
                    else None
                )
                context_source_history_count = 0
                context_selected_history_count = 0
                context_window_history_count = 0
                context_trimmed_history_count = 0
                context_history_budget: Optional[int] = None
                context_max_messages: Optional[int] = None
                context_compaction_applied = False
                context_request_history_count = _client_prefix_history_len(messages)

                if conversation_id:
                    u_id = lane_user_id
                    server_history = _regular_completion_history(
                        await memory_service.get_history(u_id, conversation_id),
                        messages,
                    )
                    server_history = await _apply_context_snapshot(
                        server_history, user_id=u_id, conversation_id=conversation_id
                    )
                    context_source_history_count = len(
                        _history_messages_for_context(server_history)
                    )

                    # 路由前只使用平台兜底预算；真实目标 agent/model 在内部 runner
                    # 完成路由后再按最终模型重建一次上下文。
                    runtime_max_tokens = await self._resolve_pre_route_context_budget()

                    if user_msg and user_msg.get("role") == "user":
                        await memory_service.add_message(
                            u_id,
                            conversation_id,
                            "user",
                            user_msg["content"],
                            files=user_msg.get("files"),
                        )

                        from app.services.config_service import ConfigService
                        max_context = await ConfigService.get("agent_max_context_messages", "60")
                        try:
                            max_context = int(max_context)
                        except ValueError:
                            max_context = 60
                        history_max_tokens = await self._resolve_history_context_budget(
                            runtime_max_tokens
                        )
                        context_history_budget = history_max_tokens
                        context_max_messages = max_context
                        if shared_state is not None:
                            shared_state["context_source_history"] = list(server_history or [])
                            shared_state["context_user_message"] = user_msg
                            shared_state["context_history_budget"] = history_max_tokens
                        window_hidden = _window_for_context(
                            server_history if server_history else [],
                            max_context,
                            history_max_tokens,
                        )
                        context_history = _history_messages_for_context(window_hidden)
                        context_window_history_count = len(context_history)
                        context_full_history = _history_messages_for_context(server_history)
                        context_trimmed_history_count = max(
                            0,
                            context_source_history_count - context_window_history_count,
                        )
                        ctx_event: dict = {}
                        context_history = await self._maybe_compact_overflow(
                            context_full_history,
                            context_history,
                            user_id=lane_user_id,
                            conversation_id=conversation_id,
                            agent_id=agent_id,
                            agent_name=agent_name,
                            version_id=version_id,
                            out=ctx_event,
                            token_budget=history_max_tokens,
                            enable_llm_summary=False,
                            physical_window=runtime_max_tokens,
                        )
                        if ctx_event:
                            context_compaction_applied = True
                            ctx_event = dict(ctx_event)
                            ctx_event["type"] = "context_summarized"
                            await self._persist_context_compaction_event(
                                ctx_event,
                                user_id=lane_user_id,
                                conversation_id=conversation_id,
                                trace_id=trace_id,
                                source="platform",
                                stage="pre_route",
                                agent_name=agent_name,
                            )
                            yield ctx_event
                        messages = context_history + [user_msg]
                        context_selected_history_count = len(context_history)
                    else:
                        from app.services.config_service import ConfigService
                        max_context = await ConfigService.get("agent_max_context_messages", "60")
                        try:
                            max_context = int(max_context)
                        except ValueError:
                            max_context = 60
                        history_max_tokens = await self._resolve_history_context_budget(
                            runtime_max_tokens
                        )
                        context_history_budget = history_max_tokens
                        context_max_messages = max_context
                        if shared_state is not None:
                            shared_state["context_source_history"] = list(server_history or [])
                            shared_state["context_user_message"] = None
                            shared_state["context_history_budget"] = history_max_tokens
                        window = _history_messages_for_context(
                            _window_for_context(
                                server_history if server_history else [],
                                max_context,
                                history_max_tokens,
                            )
                        )
                        context_window_history_count = len(window)
                        context_trimmed_history_count = max(
                            0,
                            context_source_history_count - context_window_history_count,
                        )
                        ctx_event: dict = {}
                        messages = await self._maybe_compact_overflow(
                            _history_messages_for_context(server_history),
                            window,
                            user_id=lane_user_id,
                            conversation_id=conversation_id,
                            agent_id=agent_id,
                            agent_name=agent_name,
                            version_id=version_id,
                            out=ctx_event,
                            token_budget=history_max_tokens,
                            enable_llm_summary=False,
                            physical_window=runtime_max_tokens,
                        )
                        if ctx_event:
                            context_compaction_applied = True
                            ctx_event = dict(ctx_event)
                            ctx_event["type"] = "context_summarized"
                            await self._persist_context_compaction_event(
                                ctx_event,
                                user_id=lane_user_id,
                                conversation_id=conversation_id,
                                trace_id=trace_id,
                                source="platform",
                                stage="pre_route",
                                agent_name=agent_name,
                            )
                            yield ctx_event
                        context_selected_history_count = len(messages)

                if not conversation_id:
                    context_selected_history_count = context_request_history_count

                context_history_log = _build_context_history_log(
                    conversation_id=conversation_id,
                    source_history_count=context_source_history_count,
                    selected_history_count=context_selected_history_count,
                    trimmed_history_count=context_trimmed_history_count,
                    history_token_budget=context_history_budget,
                    max_context_messages=context_max_messages,
                    compaction_applied=context_compaction_applied,
                    request_history_count=context_request_history_count,
                )
                _track_process_timeline(shared_state["process_timeline"], context_history_log)
                yield context_history_log

                from app.utils.skill_metadata import enrich_messages_with_skill_meta

                enrich_messages_with_skill_meta(messages)

                user_query = str(user_msg.get("content") or "").strip() if user_msg else ""

                if user_question_cancelled:
                    cancellation_message = "已取消本次提问，本次任务已停止。"
                    full_response_content = cancellation_message
                    execution_status = "cancelled"
                    shared_state["execution_status"] = "cancelled"
                    resolved_agent_name = "sys_question_cancel"
                    resolved_display_name = "系统助手"
                    if conversation_id:
                        u_id = lane_user_id
                        asyncio.create_task(
                            memory_service.add_message(
                                u_id,
                                conversation_id,
                                "assistant",
                                cancellation_message,
                                trace_id=trace_id,
                                agent_name=resolved_agent_name,
                                agent_type="system",
                                agent_display_name=resolved_display_name,
                                process_timeline=_final_process_timeline(
                                    shared_state.get("process_timeline")
                                ),
                            )
                        )
                    yield {
                        "type": "meta",
                        "agent_name": resolved_agent_name,
                        "agent_display_name": resolved_display_name,
                        "agent_type": "system",
                    }
                    yield {
                        "content": cancellation_message,
                        "status": "success",
                        "trace_id": trace_id,
                    }
                    return

                # --- Handle explicit @mention in text ---
                import re
                if user_query and not (agent_id or agent_name):
                    mention_match = re.match(r'^[@＠]([^\s]+)\s+(.*)$', user_query, re.DOTALL)
                    if mention_match:
                        agent_name = mention_match.group(1)
                        user_query = mention_match.group(2).strip()
                        if messages:
                            messages[-1]["content"] = user_query
                        logger.info(f"Intercepted explicit @mention, routing directly to agent: {agent_name}")

                if not messages:
                    yield {"content": AgentServicePrompts.EMPTY_REQUEST}
                    return

                gen = self._run_chat_turn_stream(
                    messages=messages,
                    user_query=user_query,
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
                    trace_id=trace_id,
                    trace_buffer=trace_buffer,
                    start_time=asyncio.get_running_loop().time(),
                    shared_state=shared_state,
                    agent_max_toolcall_timeout_seconds=agent_max_toolcall_timeout_seconds,
                )
                try:
                    async for chunk in gen:
                        if run_handle is not None and run_handle.cancelled:
                            raise asyncio.CancelledError
                        if isinstance(chunk, dict):
                            full_response_content = _accumulate_stream_content(
                                full_response_content,
                                chunk,
                            )
                            _track_process_timeline(shared_state.get("process_timeline"), chunk)
                        yield chunk
                finally:
                    await gen.aclose()
                agent_config = shared_state["agent_config"]
                execution_status = shared_state["execution_status"]
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
        """路由完成后按目标模型重新构造真正发送给 executor 的上下文。"""
        if not conversation_id or not shared_state:
            return messages
        source_history = shared_state.get("context_source_history")
        if not isinstance(source_history, list):
            return messages

        target_history_budget = await self._history_budget_for_runtime_model_info(
            runtime_model_info
        )
        if synthesis_runtime_model_info is not None:
            target_history_budget = min(
                target_history_budget,
                await self._history_budget_for_runtime_model_info(
                    synthesis_runtime_model_info
                ),
            )
        model_key = ":".join(
            filter(
                None,
                (
                    runtime_model_info.effective_model_id,
                    (
                        synthesis_runtime_model_info.effective_model_id
                        if synthesis_runtime_model_info is not None
                        else None
                    ),
                ),
            )
        )
        if (
            shared_state.get("context_finalized_model") == model_key
            and shared_state.get("context_history_budget") == target_history_budget
        ):
            return messages

        from app.services.config_service import ConfigService

        max_context_raw = await ConfigService.get("agent_max_context_messages", "60")
        try:
            max_context = int(max_context_raw)
        except (TypeError, ValueError):
            max_context = 60
        user_message = shared_state.get("context_user_message")
        window = _history_messages_for_context(
            _window_for_context(
                source_history,
                max_context,
                target_history_budget,
            )
        )
        candidate_windows = [
            value
            for value in (
                self._configured_model_window(runtime_model_info),
                self._configured_model_window(synthesis_runtime_model_info)
                if synthesis_runtime_model_info is not None
                else 0,
            )
            if value > 0
        ]
        physical_window = (
            min(candidate_windows)
            if candidate_windows
            else await self._resolve_pre_route_context_budget()
        )
        completion_reserve = max(
            self._configured_model_output(runtime_model_info),
            self._configured_model_output(synthesis_runtime_model_info)
            if synthesis_runtime_model_info is not None
            else 0,
        )
        final_ctx_event: dict = {}
        compacted = await self._maybe_compact_overflow(
            _history_messages_for_context(source_history),
            window,
            user_id=(user_info or {}).get("user_id"),
            conversation_id=conversation_id,
            agent_id=agent_id,
            agent_name=agent_name,
            version_id=version_id,
            token_budget=target_history_budget,
            enable_llm_summary=True,
            out=final_ctx_event,
            physical_window=physical_window,
            completion_reserve_tokens=completion_reserve,
        )
        shared_state["context_finalized_model"] = model_key
        shared_state["context_history_budget"] = target_history_budget
        if final_ctx_event:
            final_ctx_event = dict(final_ctx_event)
            final_ctx_event["type"] = "context_summarized"
            shared_state["context_final_compaction_event"] = final_ctx_event
        if isinstance(user_message, dict) and user_message.get("role") == "user":
            return compacted + [user_message]
        return compacted

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
        """超出上下文窗口时，把被丢弃的旧消息压缩成一条 system 摘录注入窗口最前。

        由配置 ``agent_context_compaction_enabled`` 控制（默认开启）。

        E 项：发生真实溢出且 ``agent_context_llm_summary_enabled`` 开启（默认）时，
        会用「当前会话模型」尝试生成更高质量的语义摘要替换确定性摘录正文；
        任何失败（配置关闭、模型解析失败、无可用 client、LLM 超时/报错）都会
        优雅降级到确定性拼装摘录，不影响主链路。

        B 项：当提供 ``user_id`` / ``conversation_id`` 时，摘录会持久化到独立 Redis key
        并在下轮回读合并（跨轮累积早期事实），避免窗口滑动导致最古早信息断档。

        ``agent_id`` / ``agent_name`` / ``version_id``：当前会话显式指定的 agent 身份，
        用于确定式解析「当前会话模型」做语义摘要（有显式身份时不触发路由开销）。

        ``out``：可选观测输出容器。当发生真实溢出压缩（非提前返回）时，会向其写入
        ``{"dropped", "kept", "origin", "preview", "title"}``，供调用方（SSE 生成器）
        据此发射与 AgentScope ``context_compression`` 语义无关的独立前端摘录卡片事件。
        """
        if not full_history or len(full_history) <= len(window):
            return window
        try:
            from app.services.config_service import ConfigService

            enabled_raw = await ConfigService.get("agent_context_compaction_enabled", "true")
            if str(enabled_raw or "").strip().lower() not in {"1", "true", "yes", "on"}:
                return window
            max_chars_raw = await ConfigService.get("agent_context_compaction_max_chars", "1200")
            try:
                max_chars = max(200, int(max_chars_raw))
            except (TypeError, ValueError):
                max_chars = 1200

            prev_digest = None
            context_revision: Optional[int] = None
            source_seq = max(
                (
                    int(message.get("seq") or 0)
                    for message in full_history
                    if isinstance(message, dict)
                ),
                default=0,
            )
            if user_id and conversation_id:
                from app.services.ai.memory_service import MemoryService

                memory = MemoryService()
                try:
                    prev_digest = await memory.get_digest(user_id, conversation_id)
                except Exception as exc:
                    logger.warning(
                        "[Compaction] Failed to read persisted digest: %s", exc
                    )
                    prev_digest = None
                try:
                    context_revision = await memory.get_context_revision(
                        user_id, conversation_id
                    )
                except Exception as exc:
                    logger.warning(
                        "[Compaction] Failed to read context revision: %s", exc
                    )
                    context_revision = None
                try:
                    # 当前轮用户消息已经先写入 Redis，但不在 full_history 快照中。
                    # 将当前计数器作为摘要版本边界，避免摘要刚写入就因本轮用户消息
                    # 的 seq 更大而被误判为旧快照。
                    source_seq = max(
                        source_seq,
                        await memory.get_current_seq(user_id, conversation_id),
                    )
                except Exception as exc:
                    logger.warning(
                        "[Compaction] Failed to read current seq: %s", exc
                    )

            from app.services.ai.context_compaction import (
                COMPACTION_MARKER,
                apply_context_compaction,
            )

            compacted = apply_context_compaction(
                full_history=full_history,
                window=window,
                max_chars=max_chars,
                prev_digest=prev_digest,
            )
            if len(compacted) == len(window):
                return compacted

            # 有真实溢出。E 项（异步化）：本次立即用确定性摘录注入窗口，主链路零阻塞，
            # 不增加首 token 延迟；同时把「当前会话模型」的语义摘要作为后台任务生成，
            # 完成后写回 Redis digest，供下一轮真实溢出时经 prev_digest 自然合入
            # （提升后续几轮的摘要质量，而非阻塞本轮）。任何失败/无 client 都会
            # 优雅降级到确定性摘录，不影响主链路。
            digest_origin = "deterministic"
            if enable_llm_summary and user_id and conversation_id:
                self._spawn_llm_digest_task(
                    full_history,
                    window,
                    max_chars=max_chars,
                    prev_digest=prev_digest,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    version_id=version_id,
                    source_seq=source_seq,
                    source_revision=context_revision,
                )

            logger.info(
                "[Compaction] Injected overflow digest: dropped=%d kept=%d origin=%s",
                len(full_history) - len(window),
                len(window),
                digest_origin,
            )
            # F 项：真实溢出压缩时向观测容器写入摘录卡片所需信息（供 SSE 生成器发射）。
            if out is not None and isinstance(compacted, list):
                self._emit_compaction_card(
                    out,
                    compacted,
                    full_history,
                    window,
                    digest_origin,
                    token_budget,
                    physical_window,
                    completion_reserve_tokens,
                )
            if user_id and conversation_id:
                try:
                    digest_content = None
                    head = compacted[0]
                    if (
                        isinstance(head, dict)
                        and head.get("role") == "system"
                        and COMPACTION_MARKER in str(head.get("content", ""))
                    ):
                        digest_content = str(head.get("content"))
                    await MemoryService().set_digest_if_current(
                        user_id,
                        conversation_id,
                        digest_content or "",
                        source_seq=source_seq,
                        source_revision=context_revision,
                        quality=0,
                        allow_newer_seq=True,
                    )
                except Exception as exc:
                    logger.warning(
                        "[Compaction] Failed to persist digest: %s", exc
                    )
            return compacted
        except Exception as exc:
            logger.warning("[Compaction] Failed to compact overflow history: %s", exc)
            return window

    async def manual_compact_conversation(
        self,
        user_id: str,
        conversation_id: str,
        *,
        retain_ratio: float = 0.5,
        mode: str = "fast",
    ) -> Dict[str, Any]:
        """由用户显式触发上下文压缩；默认快速模式，不改写原始历史。"""
        retain_ratio = float(retain_ratio)
        if retain_ratio not in {0.25, 0.5, 0.75}:
            raise ValueError("retain_ratio 只能是 0.25、0.5 或 0.75")
        if mode not in {"fast", "smart"}:
            raise ValueError("mode 只能是 fast 或 smart")
        history = await memory_service.get_history(user_id, conversation_id)
        history = await _apply_context_snapshot(
            history or [], user_id=user_id, conversation_id=conversation_id
        )
        full_history = _history_messages_for_context(history or [])
        if len(full_history) < 2:
            return {"compacted": False, "reason": "history_too_short", "dropped": 0, "kept": len(full_history)}

        from app.services.config_service import ConfigService

        max_context_raw = await ConfigService.get("agent_max_context_messages", "60")
        try:
            max_context = max(1, int(max_context_raw))
        except (TypeError, ValueError):
            max_context = 60
        runtime_budget = await self._resolve_pre_route_context_budget()
        history_budget = await self._resolve_history_context_budget(runtime_budget)
        keep_count = max(1, int(len(full_history) * retain_ratio))
        automatic_window = _window_for_context(full_history, max_context, history_budget)
        window = full_history[-keep_count:]
        if len(automatic_window) < len(window):
            window = automatic_window

        before_tokens = sum(
            estimate_text_tokens(
                str(message.get("content") or "") + _trusted_tool_run_text(message)
            )
            for message in full_history
        )

        event: Dict[str, Any] = {}
        compacted = await self._maybe_compact_overflow(
            full_history,
            window,
            user_id=user_id,
            conversation_id=conversation_id,
            out=event,
            token_budget=history_budget,
            enable_llm_summary=False,
            physical_window=runtime_budget,
        )
        if not event:
            return {"compacted": False, "reason": "no_compactable_history", "dropped": 0, "kept": len(full_history)}

        if mode == "smart":
            smart_digest = await self._try_llm_overflow_digest(
                full_history,
                window,
                prev_digest=None,
            )
            if smart_digest and smart_digest.get("content"):
                compacted = [smart_digest, *window]
                event["origin"] = "llm"

        source_seq = max((int(message.get("seq") or 0) for message in history), default=0)
        await memory_service.set_context_snapshot(
            user_id,
            conversation_id,
            {"schema_version": 1, "source_seq": source_seq, "messages": compacted},
        )
        after_tokens = sum(
            estimate_text_tokens(
                str(message.get("content") or "") + _trusted_tool_run_text(message)
            )
            for message in compacted
        )
        event = dict(event)
        event["type"] = "context_summarized"
        event["saved_tokens"] = max(0, before_tokens - after_tokens)
        event["saved_percent"] = round(
            max(0, before_tokens - after_tokens) / before_tokens * 100, 1
        ) if before_tokens else 0
        await self._persist_context_compaction_event(
            event,
            user_id=user_id,
            conversation_id=conversation_id,
            trace_id=f"manual-{uuid.uuid4().hex}",
            source="platform",
            stage="manual",
        )
        return {
            "compacted": True,
            "dropped": int(event.get("dropped") or 0),
            "kept": int(event.get("kept") or len(window)),
            "origin": event.get("origin") or "deterministic",
            "preview": event.get("preview") or "",
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "saved_tokens": max(0, before_tokens - after_tokens),
            "saved_percent": round(max(0, before_tokens - after_tokens) / before_tokens * 100, 1) if before_tokens else 0,
            "retain_ratio": retain_ratio,
            "mode": mode,
        }

    @staticmethod
    def _emit_compaction_card(
        out: dict,
        compacted: List[Dict[str, Any]],
        full_history: List[Dict[str, Any]],
        window: List[Dict[str, Any]],
        origin: str,
        token_budget: Optional[int] = None,
        physical_window: Optional[int] = None,
        completion_reserve_tokens: Optional[int] = None,
    ) -> None:
        """F 项：把真实溢出压缩的观测信息写入 ``out`` 容器，供 SSE 生成器发射卡片。

        只在真实压缩发生时调用（提前返回路径不会走到这里）。摘录正文取自
        ``compacted[0]``（平台注入的 system 摘录），用 ``context_compaction`` 的
        ``_extract_digest_body`` 剥离 marker 与前导说明行，得到可直接展示的要点预览。

        与 AgentScope ``context_compression``（``event_stream`` 那条 summary 噪声）无关，
        这里描述的是链路 A 真正喂给 LLM 的摘录内容。
        """
        try:
            from app.services.ai.context_compaction import _extract_digest_body

            dropped = len(full_history) - len(window)
            kept = len(window)
            preview = ""
            head = compacted[0] if compacted else None
            if isinstance(head, dict) and head.get("role") == "system":
                preview = _extract_digest_body(str(head.get("content") or ""))
            if not preview:
                preview = str(head.get("content") or "") if isinstance(head, dict) else ""
            # 卡片只展示简短预览，全长仍以配置 max_chars 为准。
            preview = preview.strip()
            if len(preview) > 300:
                preview = preview[:300].rstrip() + "……"
            out["dropped"] = dropped
            out["kept"] = kept
            out["origin"] = origin
            out["preview"] = preview
            out["title"] = "对话上下文已压缩（平台摘录）"
            # F 项增强：上下文使用率。token_used = 全量历史估算 token，
            # token_budget = 扣除输出与运行时开销后的历史预算。
            # 估算值来自 estimate_text_tokens（近似，非模型精确计数）。
            try:
                token_used = sum(
                    estimate_text_tokens(
                        str(msg.get("content") or "")
                        + _trusted_tool_run_text(msg)
                    )
                    for msg in full_history
                    if isinstance(msg, dict)
                )
            except Exception:
                token_used = 0
            out["token_used"] = int(token_used)
            if isinstance(token_budget, int) and token_budget > 0:
                out["token_budget"] = token_budget
            else:
                out["token_budget"] = None
            out["history_budget"] = out["token_budget"]
            out["physical_window"] = (
                physical_window if isinstance(physical_window, int) and physical_window > 0
                else None
            )
            out["completion_reserve_tokens"] = (
                completion_reserve_tokens
                if isinstance(completion_reserve_tokens, int)
                and completion_reserve_tokens > 0
                else 0
            )
            if out["physical_window"] is not None and out["history_budget"] is not None:
                out["overhead_reservation_tokens"] = max(
                    0,
                    out["physical_window"] - out["history_budget"],
                )
            else:
                out["overhead_reservation_tokens"] = None
        except Exception as exc:  # 观测信息失败不应影响主链路返回。
            logger.warning("[Compaction] Failed to build compaction card: %s", exc)

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
        """E 项（异步）：后台生成 LLM 语义摘要，替代「同步阻塞首 token」。

        ``_maybe_compact_overflow`` 在本轮立即用确定性摘录注入窗口（零阻塞），
        同时本方法把语义摘要生成降级为后台任务：命中时调用
        ``_try_llm_overflow_digest``，成功后把含 ``COMPACTION_MARKER`` 的正文
        写回 Redis digest，供下一轮真实溢出时经 ``prev_digest`` 自然合入
        （滞后一轮生效，提升后续几轮的摘要质量）。

        作为权衡的边界：携带 ``user_id`` / ``conversation_id`` 才有 digest 落点，
        否则不生成（返回 ``None``）。后台任务必须持有引用（返回给调用方）、并在
        协程内部全量捕获异常，避免 ``Task exception was never retrieved``。
        """
        if not user_id or not conversation_id:
            return None

        async def _run() -> None:
            try:
                llm_digest = await self._try_llm_overflow_digest(
                    full_history,
                    window,
                    max_chars=max_chars,
                    prev_digest=prev_digest,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    version_id=version_id,
                )
                if not llm_digest or not isinstance(llm_digest, dict):
                    return  # 命中失败，保持本轮写入的确定性摘录。
                content = llm_digest.get("content")
                if not content:
                    return
                from app.services.ai.memory_service import MemoryService

                write_kwargs = {"source_seq": source_seq}
                write_kwargs["quality"] = 1
                write_kwargs["allow_newer_seq"] = True
                if source_revision is not None:
                    write_kwargs["source_revision"] = source_revision
                written = await MemoryService().set_digest_if_current(
                    user_id,
                    conversation_id,
                    str(content),
                    **write_kwargs,
                )
                if not written:
                    logger.info(
                        "[Compaction] Async LLM digest skipped for stale conversation=%s",
                        conversation_id,
                    )
                    return
                logger.info(
                    "[Compaction] Async LLM digest persisted for conversation=%s",
                    conversation_id,
                )
            except Exception as exc:  # 后台任务绝不外抛，避免未取回异常告警。
                logger.warning(
                    "[Compaction] Async LLM digest task failed: %s", exc
                )

        # 进程级持有引用，直到任务完成；协程内部已经捕获所有业务异常。
        task = asyncio.get_running_loop().create_task(_run())
        _LLM_DIGEST_TASKS.add(task)
        task.add_done_callback(_LLM_DIGEST_TASKS.discard)
        return task

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
        """E 项：尝试用「当前会话模型」生成语义摘要，替代确定性拼装摘录。

        返回 ``{"role": "system", "content": ...}`` 或 ``None``。任何失败都返回
        ``None``，由调用方降级到确定性 ``apply_context_compaction`` 结果。

        模型优先级：显式 agent 身份 → ``get_configured_llm``（当前会话模型）；
        否则 → ``get_fallback_llm``（系统默认 ``llm_model_name``）。
        """
        try:
            from app.services.config_service import ConfigService

            llm_summary_raw = await ConfigService.get(
                "agent_context_llm_summary_enabled", "true"
            )
            if str(llm_summary_raw or "").strip().lower() not in {
                "1",
                "true",
                "yes",
                "on",
            }:
                return None

            from app.services.ai.context_compaction import (
                COMPACTION_MARKER,
                _condense,
                _extract_digest_body,
                _flatten_content,
            )

            dropped = full_history[: len(full_history) - len(window)]
            transcript_parts: List[str] = []
            prev_body = prev_digest and _extract_digest_body(str(prev_digest))
            if prev_body:
                transcript_parts.append(f"〔更早轮次对话要点〕\n{prev_body}")
            role_label = {
                "user": "用户",
                "assistant": "助手",
                "system": "系统",
            }
            for msg in dropped or []:
                role = (msg.get("role") or "").strip()
                text = _flatten_content(msg.get("content"))
                # 最终工具结果（tool_run_text）同样会注入模型上下文，摘要也应看到，
                # 否则工具返回的结论/数据在语义摘要里会缺失；过程日志和思考卡片
                # 不在此字段内。
                tool_text = _flatten_content(_trusted_tool_run_text(msg))
                if tool_text:
                    text = f"{text} · 工具结果：{tool_text}".strip(
                        " ·"
                    ) if text else tool_text
                if not text:
                    continue
                transcript_parts.append(
                    f"{role_label.get(role, role or '未知')}：{text}"
                )
            if not transcript_parts:
                return None
            transcript = "\n".join(transcript_parts)

            # 解析摘要模型：优先走显式 agent 身份（无路由额外模型调用）；否则系统默认。
            llm = None
            if any((agent_id, agent_name, version_id)):
                try:
                    from app.services.ai.config import AgentConfigProvider
                    from app.services.ai.context_manager import AgentContextManager

                    agent_config, _ = await AgentContextManager.resolve_agent_config(
                        window,
                        agent_id=agent_id,
                        agent_name=agent_name,
                        version_id=version_id,
                        enable_multi_agent=False,
                        force_data_query=False,
                    )
                    if agent_config is not None:
                        llm = await AgentConfigProvider.get_configured_llm(
                            streaming=False, config=agent_config
                        )
                except Exception as exc:
                    logger.warning(
                        "[Compaction][LLM digest] Failed to resolve current-agent model: %s",
                        exc,
                    )
                    llm = None
            if llm is None:
                try:
                    from app.services.ai.config import AgentConfigProvider

                    llm = await AgentConfigProvider.get_fallback_llm(streaming=False)
                except Exception as exc:
                    logger.warning(
                        "[Compaction][LLM digest] No fallback LLM available: %s", exc
                    )
                    return None
            if llm is None:
                logger.warning(
                    "[Compaction][LLM digest] No available LLM, fallback to deterministic"
                )
                return None

            from app.services.ai.conversation_summarizer import ConversationSummarizer
            from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
            from app.services.ai.runtime.agentscope.messages import (
                RuntimeContentBlock,
                RuntimeMessage,
            )

            system_prompt = (
                "你是上下文压缩助手。会话多轮上下文超过窗口后，请把下面"
                "「更早轮次无法直接保留的对话」压缩成一段简洁的中文要点，仅输出正文，"
                "不要输出 JSON、代码块或标题符号。要点需尽量覆盖：关键事实、已确认决策、"
                "未完成事项、与后续轮次相关的核心对象/术语。不要编造对话中未出现的信息。"
                f"全文控制在 {max_chars} 字以内。"
            )
            chat_client = chat_client_from_handle(llm)
            llm_messages = [
                RuntimeMessage(
                    role="system",
                    content=[RuntimeContentBlock(type="text", text=system_prompt)],
                ),
                RuntimeMessage(
                    role="user",
                    content=[RuntimeContentBlock(type="text", text=transcript)],
                ),
            ]
            # 语义摘要属于增强项，整体加 15s 超时；超时/报错都优雅降级到确定性摘录。
            raw = ""
            async with asyncio.timeout(15):
                raw = await ConversationSummarizer._generate_with_retry(
                    chat_client, llm_messages, max_retries=2
                )
            body = (raw or "").strip()
            if not body:
                return None
            # 代码层硬截断：模型返回可能忽略 system 提示的字数约束（超时/长度不受控），
            # 这里用与确定性摘录一致的 `_condense` 把正文压到 max_chars 以内，
            # 避免语义摘要反而撑爆上下文窗口。
            body = _condense(body, max_chars)
            content = (
                f"{COMPACTION_MARKER}\n"
                "以下是更早轮次对话的要点（已由模型压缩，仅供理解上下文与指代，不要逐条复述）：\n"
                f"{body}"
            )
            return {"role": "system", "content": content}
        except Exception as exc:
            logger.warning(
                "[Compaction][LLM digest] Semantic summary failed, fallback to deterministic: %s",
                exc,
            )
            return None

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
        """挂载与自动匹配技能，返回 skills_injection。"""
        active_skills = []
        if messages and "files" in messages[-1] and messages[-1]["files"]:
            for file_obj in messages[-1]["files"]:
                if file_obj.get("type") == "skill":
                    active_skills.append(file_obj)

        scoped_skill_items = [
            item for item in (resource_scope or {}).get("skills", [])
            if isinstance(item, dict) and item.get("id")
        ]
        if scoped_skill_items:
            scoped_ids = {str(item["id"]) for item in scoped_skill_items}
            active_skills = [skill for skill in active_skills if str(skill.get("url") or "") in scoped_ids]
            mounted_ids = {str(item.get("url") or "") for item in active_skills}
            active_skills.extend(
                {
                    "type": "skill",
                    "url": str(item["id"]),
                    "filename": item.get("name") or str(item["id"]),
                    "skillMeta": item,
                }
                for item in scoped_skill_items
                if str(item["id"]) not in mounted_ids
            )

        mounted_skill_ids = {s.get("url") for s in active_skills if s.get("url")}
        skills_injection = []
        full_load_policy = await self._resolve_skill_full_load_policy()
        full_loaded_count = 0

        if active_skills:
            import os
            from app.core.config import settings
            from app.services.ai.skill_resolver import (
                get_user_personal_skills_dir,
                load_skill_md_content,
            )
            from app.utils.skill_metadata import parse_skill_frontmatter

            for skill_obj in active_skills:
                skill_id = skill_obj.get("url")
                if not skill_id:
                    continue
                meta_override = skill_obj.get("skillMeta") or skill_obj.get("skill_meta")
                skill_scope = None
                explicit_skill_md_path = None
                if meta_override and isinstance(meta_override, dict):
                    skill_name = str(meta_override.get("name") or skill_id)
                    description = str(meta_override.get("description") or "")
                    skill_scope = str(meta_override.get("scope") or "").strip().lower() or None
                    explicit_skill_md_path = meta_override.get("skill_md_path") or meta_override.get("skillMdPath")
                else:
                    skill_name = skill_obj.get("filename", skill_id).replace(" (技能)", "")
                    description = ""

                skill_scope = skill_scope or str(skill_obj.get("scope") or "").strip().lower() or None
                candidate_paths: list[str] = []
                if explicit_skill_md_path:
                    candidate_paths.append(str(explicit_skill_md_path))
                if skill_scope == "personal":
                    personal_dir = get_user_personal_skills_dir(user_info)
                    if personal_dir:
                        candidate_paths.append(os.path.join(personal_dir, skill_id, "SKILL.md"))
                candidate_paths.append(os.path.join(settings.SKILLS_DIR, skill_id, "SKILL.md"))

                skill_md_path = next((p for p in candidate_paths if os.path.exists(p)), candidate_paths[-1])
                if not (meta_override and isinstance(meta_override, dict)) and os.path.exists(skill_md_path):
                    meta = parse_skill_frontmatter(skill_id, skill_md_path)
                    skill_name = meta.get("name") or skill_obj.get("filename", skill_id).replace(" (技能)", "")
                    description = meta.get("description") or ""
                elif not (meta_override and isinstance(meta_override, dict)):
                    logger.warning("[Skills] Skill markdown not found at %s", skill_md_path)

                full_instruction = None
                if self._should_preload_skill_full_instruction(
                    match_source="mounted",
                    policy=full_load_policy,
                    loaded_count=full_loaded_count,
                ):
                    full_instruction = load_skill_md_content(
                        skill_id,
                        max_bytes=int(full_load_policy["max_bytes"]),
                        user_info=user_info,
                        scope=skill_scope,
                        skill_md_path=skill_md_path if os.path.exists(skill_md_path) else None,
                    )
                    if full_instruction:
                        full_loaded_count += 1

                skills_injection.append(
                    self._build_skill_injection(
                        skill_name=skill_name,
                        skill_id=skill_id,
                        description=description,
                        full_instruction=full_instruction,
                    )
                )
                logger.info(
                    "[Skills] Matched mounted skill %s (%s).",
                    skill_id,
                    "full instruction preloaded" if full_instruction else "summary only",
                )

        if user_query and not scoped_skill_items:
            try:
                from app.services.ai.skill_resolver import (
                    load_skill_md_content,
                    resolve_skills_from_query,
                    skill_filter_kwargs_from_config,
                )

                skill_filter = skill_filter_kwargs_from_config(agent_config)
                for skill_meta in resolve_skills_from_query(
                    user_query,
                    user_info=user_info,
                    **skill_filter,
                ):
                    skill_id = skill_meta.get("id")
                    if not skill_id or skill_id in mounted_skill_ids:
                        continue
                    skill_name = skill_meta.get("name") or skill_id
                    description = skill_meta.get("description") or ""
                    full_instruction = None
                    if self._should_preload_skill_full_instruction(
                        match_source=str(skill_meta.get("match_source") or "mention"),
                        match_score=skill_meta.get("match_score"),
                        policy=full_load_policy,
                        loaded_count=full_loaded_count,
                    ):
                        full_instruction = load_skill_md_content(
                            skill_id,
                            max_bytes=int(full_load_policy["max_bytes"]),
                            user_info=user_info,
                            scope=skill_meta.get("scope"),
                            skill_md_path=skill_meta.get("skill_md_path"),
                        )
                        if full_instruction:
                            full_loaded_count += 1
                    skills_injection.append(
                        self._build_skill_injection(
                            skill_name=skill_name,
                            skill_id=skill_id,
                            description=description,
                            full_instruction=full_instruction,
                        )
                    )
                    mounted_skill_ids.add(skill_id)
                    logger.info(
                        "[Skills] Auto-resolved skill %s from query (%s).",
                        skill_id,
                        "full instruction preloaded" if full_instruction else "summary only",
                    )
                    if skills_log_callback:
                        details_msg = ""
                        if full_instruction:
                            details_msg = (
                                f"已从本轮问题匹配「{skill_name}」(ID: {skill_id})。"
                                "已预载完整 SKILL.md 指令，本轮可直接按该流程执行。"
                            )
                        skills_log_callback(skill_id, skill_name, details_msg)
            except Exception as resolve_err:
                logger.warning("[Skills] Failed to auto-resolve skills from query: %s", resolve_err)

        if user_query and not skills_injection:
            try:
                from app.services.ai.skill_resolver import (
                    is_main_general_agent,
                    list_skill_metas,
                    load_skill_md_content,
                    scan_relevant_skills,
                    should_scan_skills_for_query,
                    skill_filter_kwargs_from_config,
                )
                from app.services.config_service import ConfigService

                if is_main_general_agent(agent_config):
                    skill_filter = skill_filter_kwargs_from_config(agent_config)
                    scan_enabled_raw = await ConfigService.get("skill_auto_scan_enabled", "true")
                    scan_enabled = str(scan_enabled_raw or "true").strip().lower() in {
                        "1", "true", "yes", "on",
                    }
                    if scan_enabled:
                        min_score_raw = await ConfigService.get("skill_auto_scan_min_score", "0.45")
                        try:
                            min_score = float(min_score_raw) if min_score_raw is not None else 0.45
                        except (TypeError, ValueError):
                            min_score = 0.45
                        max_results_raw = await ConfigService.get("skill_auto_scan_max_results", "1")
                        try:
                            max_scan_results = int(max_results_raw) if max_results_raw is not None else 1
                        except (TypeError, ValueError):
                            max_scan_results = 1
                        max_scan_results = max(1, min(max_scan_results, 3))

                        scanned_skills = []
                        if should_scan_skills_for_query(user_query):
                            scanned_skills = scan_relevant_skills(
                                user_query,
                                user_info=user_info,
                                exclude_ids=mounted_skill_ids,
                                max_results=max_scan_results,
                                min_score=min_score,
                                **skill_filter,
                            )
                        available_skills = list_skill_metas(
                            user_info=user_info,
                            **skill_filter,
                        )
                        scanned_skills = self._ensure_first_turn_superpowers_candidate(
                            scanned_skills=scanned_skills,
                            available_skills=available_skills,
                            messages=messages,
                            exclude_ids=mounted_skill_ids,
                        )
                        scanned_skills = scanned_skills[:max_scan_results]

                        for skill_meta in scanned_skills:
                            skill_id = skill_meta.get("id")
                            if not skill_id or skill_id in mounted_skill_ids:
                                continue
                            skill_name = skill_meta.get("name") or skill_id
                            description = skill_meta.get("description") or ""
                            match_score = skill_meta.get("match_score")
                            full_instruction = None
                            force_full_instruction = self._should_force_preload_scanned_skill(
                                skill_id=skill_id,
                                messages=messages,
                            )
                            if force_full_instruction or self._should_preload_skill_full_instruction(
                                match_source=str(skill_meta.get("match_source") or "scan"),
                                match_score=match_score,
                                policy=full_load_policy,
                                loaded_count=full_loaded_count,
                            ):
                                full_instruction = load_skill_md_content(
                                    skill_id,
                                    max_bytes=int(full_load_policy["max_bytes"]),
                                    user_info=user_info,
                                    scope=skill_meta.get("scope"),
                                    skill_md_path=skill_meta.get("skill_md_path"),
                                )
                                if full_instruction:
                                    full_loaded_count += 1
                            skills_injection.append(
                                self._build_skill_injection(
                                    skill_name=skill_name,
                                    skill_id=skill_id,
                                    description=description,
                                    full_instruction=full_instruction,
                                )
                            )
                            mounted_skill_ids.add(skill_id)
                            logger.info(
                                "[Skills] Scanned skill %s from query (score=%s, %s).",
                                skill_id,
                                match_score,
                                "full instruction preloaded" if full_instruction else "summary only",
                            )
                            if skills_log_callback:
                                score_hint = f"（相关度 {match_score}）" if match_score is not None else ""
                                if full_instruction:
                                    force_hint = "新会话首轮门禁已强制启用；" if force_full_instruction else ""
                                    details_msg = (
                                        f"已根据本轮问题扫描技能库并匹配「{skill_name}」(ID: {skill_id}){score_hint}。"
                                        f"{force_hint}已预载完整 SKILL.md 指令，本轮可直接按该流程执行。"
                                    )
                                else:
                                    details_msg = (
                                        f"已根据本轮问题扫描技能库并匹配「{skill_name}」(ID: {skill_id}){score_hint}。"
                                        f"已注入摘要；模型须调用 read_skill_instruction 读取 SKILL.md 全文后再执行。"
                                    )
                                skills_log_callback(skill_id, skill_name, details_msg)
            except Exception as scan_err:
                logger.warning("[Skills] Failed to scan skills from query: %s", scan_err)

        # 所有智能体：新会话首轮强制预载 using-superpowers（主助手扫描路径若已注入则跳过）
        try:
            full_loaded_count = self._append_first_turn_superpowers(
                messages=messages,
                agent_config=agent_config,
                user_info=user_info,
                skills_injection=skills_injection,
                mounted_skill_ids=mounted_skill_ids,
                full_load_policy=full_load_policy,
                full_loaded_count=full_loaded_count,
                skills_log_callback=skills_log_callback,
            )
        except Exception as first_turn_err:
            logger.warning(
                "[Skills] Failed to preload first-turn using-superpowers: %s",
                first_turn_err,
            )

        if skills_injection:
            MAX_PRELOAD_SKILLS = 5
            if len(skills_injection) > MAX_PRELOAD_SKILLS:
                logger.info(f"[Skills] Too many skills ({len(skills_injection)}), truncating to top {MAX_PRELOAD_SKILLS}")
                skills_injection = skills_injection[:MAX_PRELOAD_SKILLS]
                skills_injection.append(
                    "=== [已截断] 系统中已挂载或解析出更多可用技能，出于上下文性能优化，其余技能摘要未全部载入。如有需要，模型应通过调用 list_available_skills 工具获取其余技能详细摘要 ==="
                )

        # 统计激活情况
        if mounted_skill_ids:
            try:
                from app.services.ai.skills_stats_service import skills_stats_service
                await skills_stats_service.record_activations(mounted_skill_ids)
            except Exception as stats_err:
                logger.error(f"[SkillsStats] Auto-recording skill activations failed: {stats_err}")

        return skills_injection

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


    async def _run_chat_turn_stream(
        self,
        *,
        messages: List[Dict[str, str]],
        user_query: str,
        agent_id: Optional[str],
        agent_name: Optional[str],
        version_id: Optional[str],
        conversation_id: Optional[str],
        user_info: Optional[Dict[str, Any]],
        api_key: Optional[str],
        enable_multi_agent: bool,
        debug_options: Optional[Dict[str, Any]],
        permission_options: Optional[Dict[str, Any]],
        knowledge_dataset_ids: Optional[List[str]],
        metadata_dataset_ids: Optional[List[str]],
        trace_id: str,
        trace_buffer: List[AgentExecutionStep],
        start_time: float,
        shared_state: Optional[Dict[str, Any]] = None,
        reusable_result_id: Optional[str] = None,
        quick_context: Optional[Dict[str, Any]] = None,
        request_observability: Optional[Dict[str, Any]] = None,
        agent_max_toolcall_timeout_seconds: Optional[float] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Internal turn runner; must be called inside conversation run lane when enabled."""
        agent_config = None
        full_response_content = ""
        full_reasoning_content = ""
        execution_status = "success"
        has_data_output = False
        executor = None
        tool_run_text = None
        published_download_urls: List[str] = []
        lane_user_id = (user_info or {}).get("user_id") or (user_info or {}).get("id")
        performance_tracker = ExecutionPerformanceTracker()

        from app.services.ai.quick_result_context import normalize_quick_result_context

        normalized_quick_context = normalize_quick_result_context(quick_context)
        # 只有快捷按钮明确声明为“需要新数据”，且请求没有显式选择专家时，
        # 才改变入口路由。上下文本身不进入模型消息，也不替代本轮工具凭证。
        quick_result_followup = bool(
            normalized_quick_context
            and not (agent_id or agent_name or version_id)
        )

        try:
            reusable_decision_query = user_query
            if quick_result_followup:
                # ChatBI 快捷追问明确要求重新取数，不能因为当前问题与旧结果
                # 相似而进入 reusable-result 复用路径；历史仍保留给模型理解指代。
                from app.services.ai.reusable_result import ReusableResultDecision

                reusable_result_decision = ReusableResultDecision(
                    mode="none",
                    reason="quick_context_requires_fresh_data",
                )
            else:
                if (shared_state or {}).get("clicked_reusable_reply"):
                    # 正文和执行器只接收清理后的动作文本；这个内部标记仅用于保留
                    # “点击旧回复”带来的复用意图，不会被送入模型上下文。
                    from app.services.ai.reusable_result import CLICKED_REPLY_MARKER

                    reusable_decision_query = f"{user_query}\n{CLICKED_REPLY_MARKER}"
                reusable_result_decision = await self._resolve_reusable_result_decision(
                    user_info=user_info,
                    conversation_id=conversation_id,
                    user_query=reusable_decision_query,
                    preferred_result_id=reusable_result_id,
                )
            from app.services.ai.reusable_result import (
                build_reusable_result_status_event,
                prepare_reusable_route_input,
            )

            route_messages, route_user_query = prepare_reusable_route_input(
                messages,
                user_query,
            )

            # 1. Resolve and Verify Agent Configuration and Permissions
            route_events: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
            resolve_task = self._start_route_resolution(
                route_events=route_events,
                resolve_kwargs={
                    "messages": route_messages,
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "version_id": version_id,
                    "enable_multi_agent": enable_multi_agent,
                    "user_info": user_info,
                    "trace_buffer": trace_buffer,
                    "user_query": route_user_query,
                    "quick_result_followup": quick_result_followup,
                    "conversation_id": conversation_id,
                },
            )
            try:
                while True:
                    if not route_events.empty():
                        yield await route_events.get()
                        continue
                    if resolve_task.done():
                        break
                    route_event_task = asyncio.create_task(route_events.get())
                    try:
                        done, _ = await asyncio.wait(
                            (resolve_task, route_event_task),
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if route_event_task in done:
                            yield route_event_task.result()
                    finally:
                        if not route_event_task.done():
                            route_event_task.cancel()
                        await asyncio.gather(route_event_task, return_exceptions=True)

                while not route_events.empty():
                    yield await route_events.get()

                agent_config, route_details, route_elapsed_ms, err_msg = await resolve_task
                performance_tracker.mark("route_resolution")
            except asyncio.CancelledError:
                if not resolve_task.done():
                    resolve_task.cancel()
                await asyncio.gather(resolve_task, return_exceptions=True)
                raise

            if agent_config and shared_state is not None:
                shared_state["agent_config"] = agent_config

            if not agent_config:
                preparation_elapsed_ms = (
                    asyncio.get_running_loop().time() - (shared_state or {}).get("preparation_started_at", start_time)
                ) * 1000
                if quick_result_followup:
                    yield _build_preparation_parent_log(
                        status="success",
                        details="快捷分析需要重新查询实时数据，但当前没有可用的数据查询智能体。",
                        execution_time_ms=preparation_elapsed_ms,
                    )
                    yield {
                        "content": (
                            "这条快捷分析需要重新查询实时数据，但当前没有可用的数据查询智能体。"
                            "请稍后重试，或选择一个支持数据查询的智能体后再继续。"
                        ),
                        "status": "success",
                    }
                    return
                yield _build_preparation_parent_log(
                    status="error",
                    details="鉴权及上下文与能力准备失败：未找到可用目标专家。",
                    execution_time_ms=preparation_elapsed_ms,
                )
                yield {"content": AgentServicePrompts.NO_AGENT_CONFIG}
                return

            from app.services.ai.session_mcp_tools import apply_session_mcp_tools_to_agent_config

            apply_session_mcp_tools_to_agent_config(
                agent_config,
                (debug_options or {}).get("resource_scope"),
            )

            runtime_model_info = await self._resolve_runtime_model_info_safe(
                config=agent_config,
                debug_options=debug_options,
            )
            synthesis_runtime_model_info = None
            synthesis_model_name = str(
                getattr(agent_config, "synthesis_model_name", "") or ""
            ).strip()
            if synthesis_model_name:
                synthesis_runtime_model_info = await self._resolve_runtime_model_info_safe(
                    config=agent_config,
                    debug_options=debug_options,
                    model_override=synthesis_model_name,
                    phase="synthesis",
                )
            performance_tracker.mark("runtime_model_metadata")
            yield _build_model_config_log(
                runtime_model_info,
                synthesis_runtime_model_info,
            )
            if looks_like_current_model_query(user_query):
                response = build_current_model_answer(runtime_model_info)
                agent_config.model_name = runtime_model_info.configured_model
                full_response_content = response
                yield {
                    "type": "meta",
                    "agent_name": agent_config.agent_name,
                    "agent_display_name": agent_config.agent_display_name or agent_config.agent_name,
                    "agent_type": _public_agent_type(agent_config),
                    "model": runtime_model_info.effective_model_id,
                    "runtime_model_info": runtime_model_info.public_dict(),
                }
                performance_tracker.observe_chunk({"content": response})
                yield {"content": response, "status": "success"}
                if conversation_id:
                    u_id = require_user_id(user_info)
                    asyncio.create_task(
                        memory_service.add_message(
                            u_id,
                            conversation_id,
                            "assistant",
                            response,
                            trace_id=trace_id,
                            agent_name=agent_config.agent_name,
                            agent_type=_public_agent_type(agent_config),
                            agent_display_name=(agent_config.agent_display_name or agent_config.agent_name),
                        )
                    )
                if shared_state is not None:
                    shared_state["preparation_ready"] = True
                yield _build_preparation_parent_log(
                    status="success",
                    details="请求校验、会话上下文、专家配置和模型配置已完成。",
                    execution_time_ms=(
                        asyncio.get_running_loop().time()
                        - (shared_state or {}).get("preparation_started_at", start_time)
                    ) * 1000,
                )
                return

            messages = await self._rebuild_context_for_resolved_model(
                messages=messages,
                runtime_model_info=runtime_model_info,
                conversation_id=conversation_id,
                user_info=user_info,
                agent_id=str(getattr(agent_config, "agent_id", "") or "") or None,
                agent_name=getattr(agent_config, "agent_name", None),
                version_id=None,
                shared_state=shared_state,
                synthesis_runtime_model_info=synthesis_runtime_model_info,
            )
            final_context_event = (shared_state or {}).pop(
                "context_final_compaction_event", None
            )
            if final_context_event:
                await self._persist_context_compaction_event(
                    final_context_event,
                    user_id=lane_user_id,
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                    source="platform",
                    stage="resolved_model",
                    agent_name=getattr(agent_config, "agent_name", None),
                    model_name=getattr(agent_config, "model_name", None),
                )
                yield final_context_event

            context_history_budget = (shared_state or {}).get(
                "context_history_budget"
            )
            runtime_context_metadata = await self._runtime_context_metadata(
                runtime_model_info,
                history_budget=context_history_budget,
                synthesis_runtime_model_info=synthesis_runtime_model_info,
            )

            direct_agent_selection = bool(agent_id or agent_name or version_id)
            if route_details is not None:
                if not isinstance(route_details, TurnDecision):
                    raise TypeError("AgentContextManager must return TurnDecision route details")
                turn_decision = route_details.model_copy(
                    update={
                        "stage_timings_ms": {
                            **route_details.stage_timings_ms,
                            "route_resolution": route_elapsed_ms,
                        }
                    }
                )
            elif direct_agent_selection:
                turn_decision = TurnDecision.for_direct_agent_selection(
                    agent_config,
                    stage_timings_ms={"route_resolution": route_elapsed_ms},
                )
            else:
                turn_decision = TurnDecision(
                    route_status="failed",
                    provenance="router_failure",
                    stage_timings_ms={"route_resolution": route_elapsed_ms},
                )

            if quick_result_followup:
                turn_decision = turn_decision.model_copy(
                    update={
                        "quick_result_followup": True,
                        "needs_fresh_data": True,
                        "freshness_requirement": "realtime",
                        "reference_mode": "new_query",
                    }
                )

            # 路由完成后才知道本轮 Runner 的事实域。专用路径只接收自己的结果类型；
            # 通用 Assistant 保留跨类型加工能力（例如数据转图、网页转 Markdown）。
            allowed_reusable_result_types: Collection[str] | None = None
            if turn_decision.turn_kind == "knowledge":
                allowed_reusable_result_types = {"knowledge"}
            elif turn_decision.turn_kind == "data_query":
                allowed_reusable_result_types = {"data"}
            if allowed_reusable_result_types is not None and not quick_result_followup:
                reusable_result_decision = await self._resolve_reusable_result_decision(
                    user_info=user_info,
                    conversation_id=conversation_id,
                    user_query=reusable_decision_query,
                    preferred_result_id=reusable_result_id,
                    allowed_result_types=allowed_reusable_result_types,
                )

            runner_reusable_result_id: str | None = None
            if reusable_result_decision.mode != "none":
                runner_reusable_result_id = (
                    str(reusable_result_decision.result.get("result_id") or "")
                    if reusable_result_decision.result
                    else (
                        str(reusable_result_id or "").strip()
                        if reusable_result_decision.reason.startswith("selected_result_")
                        else None
                    )
                ) or None
                turn_decision = turn_decision.model_copy(
                    update={
                        "reusable_result_mode": reusable_result_decision.mode,
                        "reusable_result_id": runner_reusable_result_id,
                        "reusable_result_reason": reusable_result_decision.reason,
                    }
                )

            def _record_reusable_result_status(chunk: Any) -> None:
                """把本轮复用/生成结果关系带入历史，供刷新后恢复当前消息标记。"""
                if not shared_state or not isinstance(chunk, dict):
                    return
                if chunk.get("type") != "reusable_result_status":
                    return
                status = str(chunk.get("status") or "")
                result_id = str(chunk.get("result_id") or "").strip()
                if status not in {"saved", "reused"} or not result_id:
                    return
                existing = shared_state.get("reusable_result_status")
                if isinstance(existing, dict) and existing.get("status") == "reused" and status == "saved":
                    return
                shared_state["reusable_result_status"] = {
                    "status": status,
                    "result_id": result_id,
                }

            if shared_state is not None:
                shared_state["reusable_result_decision"] = {
                    "mode": reusable_result_decision.mode,
                    "result_id": (
                        str(reusable_result_decision.result.get("result_id") or "")
                        if reusable_result_decision.result
                        else runner_reusable_result_id
                    ),
                    "reason": reusable_result_decision.reason,
                }
                if reusable_result_decision.mode == "reuse" and runner_reusable_result_id:
                    shared_state["reusable_result_status"] = {
                        "status": "reused",
                        "result_id": runner_reusable_result_id,
                    }
            if reusable_result_decision.mode == "reuse":
                yield build_reusable_result_status_event(
                    status="reused",
                    payload=reusable_result_decision.result,
                )
            elif reusable_result_decision.mode == "fallback":
                yield build_reusable_result_status_event(status="fallback")

            if route_details and route_details.provenance == "router":
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
                route_grounding_metadata = _build_route_grounding_metadata(
                    request_source=r_request_source,
                    request_capability=r_request_capability,
                    confidence=r_conf,
                    semantic_intent=r_semantic_intent,
                    semantic_confidence=r_semantic_confidence,
                    semantic_domain=r_semantic_domain,
                    fact_kind=r_fact_kind,
                )
                yield {
                    "type": "router_log",
                    "parent_id": "route:target_config",
                    "thought": r_thought,
                    "confidence": r_conf,
                    "selected_agent": r_agent,
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
                    "grounding_decision": route_grounding_metadata,
                    "status": "success",
                    "execution_time_ms": route_elapsed_ms,
                    "decision_trace": turn_decision.trace_payload(),
                }

            if err_msg:
                preparation_elapsed_ms = (
                    asyncio.get_running_loop().time() - (shared_state or {}).get("preparation_started_at", start_time)
                ) * 1000
                yield _build_preparation_parent_log(
                    status="error",
                    details=f"鉴权及上下文与能力准备失败：{sanitize_error_text(err_msg)}",
                    execution_time_ms=preparation_elapsed_ms,
                )
                yield {"content": err_msg}
                execution_status = "denied"
                return

            from app.services.ai.knowledge_utils import (
                build_rag_retrieval_debug_meta,
                merge_request_knowledge_dataset_ids,
            )

            request_knowledge_dataset_ids = merge_request_knowledge_dataset_ids(
                knowledge_dataset_ids,
                messages,
            )
            configured_agent_dataset_ids = list(
                (agent_config.engine_config or {}).get("dataset_ids") or []
            )
            await AgentContextManager.setup_context(
                config=agent_config,
                debug_options=debug_options,
                user_info=user_info,
                api_key=api_key,
                conversation_id=conversation_id,
                knowledge_dataset_ids=request_knowledge_dataset_ids,
                agent_dataset_ids=configured_agent_dataset_ids,
                metadata_dataset_ids=metadata_dataset_ids,
                authorized_attachment_paths=self._authorized_attachment_paths(messages),
                current_turn_attachment_paths=self._current_turn_attachment_paths(messages),
                trace_buffer=trace_buffer,
                runtime_model_info=runtime_context_metadata,
                published_download_urls=published_download_urls,
                agent_max_toolcall_timeout_seconds=agent_max_toolcall_timeout_seconds,
            )
            performance_tracker.mark("context_setup")

            # 2. Inject Active Skills
            matched_skills_to_log = []
            def skills_log_callback(skill_id, skill_name, details_msg):
                matched_skills_to_log.append((skill_id, skill_name, details_msg))

            skills_injection = await self._inject_skills(
                messages=messages,
                user_query=user_query,
                agent_config=agent_config,
                user_info=user_info,
                skills_log_callback=skills_log_callback,
                resource_scope=(debug_options or {}).get("resource_scope"),
            )
            performance_tracker.mark("skill_injection")

            for skill_id, skill_name, details_msg in matched_skills_to_log:
                yield self._build_skill_log_chunk(skill_id, skill_name, details_msg)

            early_turn_kind = turn_decision.turn_kind
            turn_intent_elapsed_ms = 0.0
            dispatch_turn_decision = turn_decision

            if turn_decision.turn_kind == "data_query":
                turn_display_label = "ChatBI 请求类别分析"
            else:
                turn_display_label = turn_kind_label(turn_decision.turn_kind)

            if turn_decision.turn_kind == "knowledge":
                agent_config = await AgentContextManager.enrich_for_knowledge_turn(
                    agent_config,
                    user_query=user_query,
                )
                await AgentContextManager.setup_context(
                    config=agent_config,
                    debug_options=debug_options,
                    user_info=user_info,
                    api_key=api_key,
                    conversation_id=conversation_id,
                    knowledge_dataset_ids=request_knowledge_dataset_ids,
                    agent_dataset_ids=configured_agent_dataset_ids,
                    metadata_dataset_ids=metadata_dataset_ids,
                    authorized_attachment_paths=self._authorized_attachment_paths(messages),
                    current_turn_attachment_paths=self._current_turn_attachment_paths(messages),
                    require_explicit_dataset=True,
                    trace_buffer=trace_buffer,
                    runtime_model_info=runtime_context_metadata,
                    published_download_urls=published_download_urls,
                    agent_max_toolcall_timeout_seconds=agent_max_toolcall_timeout_seconds,
                )
                performance_tracker.mark("knowledge_context_setup")

            # Prompt inventory must match the tools that the selected executor
            # will expose. The published version's tools are authoritative.
            from app.services.ai.prompt_assembler import (
                resolve_effective_prompt_tool_names_for_turn,
            )

            effective_prompt_tool_names = await resolve_effective_prompt_tool_names_for_turn(
                agent_config,
                current_user_query=user_query,
                turn_decision=turn_decision,
            )

            # 3. Load Memory Context
            ltm_profile, ltm_loaded_data, memory_recall_hint, preloaded_memories_text = await self._load_memory_context(
                user_info=user_info,
                early_turn_kind=early_turn_kind,
                debug_options=debug_options,
                user_query=user_query,
            )
            performance_tracker.mark("memory_load")

            user_profile = None
            if user_info and should_inject_user_context(early_turn_kind):
                id_msg = await self._build_user_context_msg(user_info)
                user_profile = id_msg.get("content")

            accessible_resources = (
                getattr(turn_decision, "accessible_resources", None)
                if early_turn_kind != "data_query"
                else None
            )
            if early_turn_kind != "data_query" and not accessible_resources and user_info:
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
                accessible_resources = await build_accessible_resource_catalog(
                    user_id=resource_user_id,
                    user_name=(
                        user_info.get("user_name")
                        or user_info.get("username")
                    ),
                    is_admin=user_info.get("role") == "admin",
                )

            # --- 主助手或显式配置了 sub_agent_call 的智能体：动态专家清单 + sub_agent_call 通讯录 ---
            agent_system_prompt = agent_config.system_prompt
            sub_agents_context = None
            delegable_agent_count = 0
            roster_loaded = False
            from app.services.ai.skill_resolver import is_main_general_agent
            has_subagent_tool = any(
                (isinstance(t, str) and t in ("sub_agent_call", "sub_agent_batch_call"))
                or (isinstance(t, dict) and t.get("name") in ("sub_agent_call", "sub_agent_batch_call"))
                or (getattr(t, "name", None) in ("sub_agent_call", "sub_agent_batch_call"))
                for t in (agent_config.tools or [])
            )
            if is_main_general_agent(agent_config) or has_subagent_tool:
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
                            current_agent_id=agent_config.agent_id,
                        )
                        delegable_agent_count = len(delegable_agents or [])
                        roster_loaded = True
                        current_agent_row = await session.get(AIAgent, agent_config.agent_id)
                        current_desc = (current_agent_row.description if current_agent_row else "") or ""
                        if AGENT_ROSTER_PLACEHOLDER in (agent_system_prompt or ""):
                            roster_md = format_agent_roster_markdown(
                                delegable_agents,
                                current_display_name=agent_config.agent_display_name or agent_config.agent_name or "主助手",
                                current_description=current_desc,
                            )
                            agent_system_prompt = inject_agent_roster(agent_system_prompt, roster_md)
                        sub_agents_context = build_sub_agents_context(delegable_agents)
                except Exception as sa_err:
                    logger.warning(f"Failed to build main-agent roster/sub-agents context: {sa_err}")

            session_scope = (debug_options or {}).get("resource_scope") or {}
            authorized_scope = (request_observability or {}).get("authorized_resource_scope") or {}
            yield _build_capability_catalog_log(
                knowledge_dataset_count=len({
                    str(item).strip()
                    for item in (request_knowledge_dataset_ids or [])
                    if str(item).strip()
                }),
                configured_dataset_count=len({
                    str(item).strip()
                    for item in configured_agent_dataset_ids
                    if str(item).strip()
                }),
                skill_count=len(skills_injection),
                delegable_agent_count=delegable_agent_count,
                roster_loaded=roster_loaded,
                runtime_tool_count=len(effective_prompt_tool_names),
                metadata_dataset_count=len({
                    str(item).strip()
                    for item in (metadata_dataset_ids or [])
                    if str(item).strip()
                }),
                session_dataset_count=len(session_scope.get("datasets", []) or []),
                session_knowledge_base_count=len(session_scope.get("knowledge_bases", []) or []),
                authorized_dataset_count=authorized_scope.get("datasets"),
                authorized_knowledge_base_count=authorized_scope.get("knowledge_bases"),
            )

            from app.core.config import settings
            cache_boundary_enabled, cache_reorder_enabled = await resolve_prompt_assembler_flags()
            assembled_prompt = assemble_system_prompt(
                PromptAssemblyInput(
                    agent_system_prompt=agent_system_prompt,
                    agent_config=agent_config,
                    engine_type=agent_config.engine_type or "LOCAL",
                    skills_injection=skills_injection,
                    skills_already_loaded=bool(skills_injection),
                    skills_dir=settings.SKILLS_DIR,
                    ltm_profile=ltm_profile,
                    memory_recall_hint=memory_recall_hint,
                    preloaded_memories=preloaded_memories_text,
                    user_profile=user_profile,
                    accessible_resources=accessible_resources,
                    cache_boundary_enabled=cache_boundary_enabled,
                    cache_reorder_enabled=cache_reorder_enabled,
                    sub_agents_context=sub_agents_context,
                    quick_suggestions_forbidden=self._should_forbid_quick_suggestions(user_info),
                    runtime_tool_names=effective_prompt_tool_names,
                    turn_decision=turn_decision,
                )
            )
            agent_config.system_prompt = assembled_prompt.full_text
            performance_tracker.mark("prompt_assembly")
            if debug_options and debug_options.get("return_raw_prompt"):
                debug_options.setdefault("prompt_assembler_meta", {})
                debug_options["prompt_assembler_meta"] = {
                    "stable_chars": len(assembled_prompt.stable_prefix),
                    "dynamic_chars": len(assembled_prompt.dynamic_suffix),
                    "cache_boundary_enabled": assembled_prompt.cache_boundary_enabled,
                    "cache_reorder_enabled": assembled_prompt.cache_reorder_enabled,
                    "section_names": list(assembled_prompt.section_names),
                    "section_char_counts": assembled_prompt.section_char_counts or {},
                }

            # --- Debug Overrides ---
            if debug_options:
                if debug_options.get("system_prompt_override"):
                    logger.info(f"[Debug] Overriding System Prompt for Trace {trace_id}")
                    agent_config.system_prompt = debug_options["system_prompt_override"]
                    yield {
                        "type": "log",
                        "title": "Debug: Prompt Override",
                        "details": "System Prompt 已被调试配置临时覆盖",
                        "status": "success",
                        "isDebug": True
                    }

                if debug_options.get("injected_context"):
                    context_data = sanitize_injected_context(debug_options["injected_context"])
                    logger.info(f"[Debug] Injecting Context: {context_data}")
                    ctx_lines = []
                    for k, v in context_data.items():
                        if k not in ["device_type", "display_hint", "business_context"]:
                            ctx_lines.append(f"- **{k}**: {v}")
                    business_context = context_data.get("business_context")
                    if isinstance(business_context, dict):
                        for k, v in business_context.items():
                            ctx_lines.append(f"- **business_context.{k}**: {v}")
                    device_type = context_data.get("device_type", "Unknown")
                    ui_instr = ""
                    if "移动端" in device_type or "小屏幕" in device_type:
                        ui_instr = AgentServicePrompts.MOBILE_UI_RULES
                    elif "桌面端" in device_type or "大屏幕" in device_type:
                        ui_instr = AgentServicePrompts.DESKTOP_UI_RULES

                    context_str = "\n".join(ctx_lines)
                    injection_msg = {
                        "role": "system",
                        "content": AgentServicePrompts.session_runtime_context(context_str, device_type, ui_instr)
                    }
                    messages.insert(1, injection_msg)

            # 普通历史只提供背景；当前轮由请求最后一条 user 消息决定。
            # 放在调试覆盖之后，确保调试模式也不会丢失这条安全边界。
            agent_config.system_prompt = build_chat_history_boundary_prompt(
                agent_config.system_prompt
            )
            yield _build_prompt_assembly_log(
                assembled_prompt,
                runtime_tool_count=len(effective_prompt_tool_names),
                final_prompt_chars=len(agent_config.system_prompt or ""),
            )
            if shared_state is not None:
                shared_state["preparation_ready"] = True
            yield _build_preparation_parent_log(
                status="success",
                execution_time_ms=(
                    asyncio.get_running_loop().time()
                    - (shared_state or {}).get("preparation_started_at", start_time)
                ) * 1000,
            )

            if debug_options and debug_options.get("return_raw_prompt"):
                raw_messages = []
                raw_messages.extend(messages)
                yield {
                    "type": "debug",
                    "subtype": "raw_prompt",
                    "data": raw_messages
                }

            agent_config.model_name = runtime_model_info.configured_model
            meta_event: Dict[str, Any] = {
                "type": "meta",
                "agent_name": agent_config.agent_name,
                "agent_display_name": agent_config.agent_display_name or agent_config.agent_name,
                "agent_type": _public_agent_type(agent_config),
                "model": runtime_model_info.effective_model_id,
                "configured_model": runtime_model_info.configured_model,
                "effective_model_id": runtime_model_info.effective_model_id,
                "model_source": runtime_model_info.source,
                "model_resolution_status": runtime_model_info.resolution_status,
                "turn_type": turn_decision.turn_kind,
                "turn_type_label": turn_display_label,
                "thought_expanded_default": default_thought_expanded(turn_decision.turn_kind),
                "decision_trace": turn_decision.trace_payload(
                    stage_timings_ms={"intent_resolution": turn_intent_elapsed_ms},
                    executor=_public_agent_type(agent_config),
                ),
            }
            if ltm_profile and ltm_loaded_data:
                meta_event["ltm_applied"] = True
                meta_event["ltm_data"] = ltm_loaded_data
            if (
                turn_decision.turn_kind == "knowledge"
                or request_knowledge_dataset_ids
                or (agent_config.engine_config or {}).get("dataset_ids")
            ):
                try:
                    meta_event["rag_retrieval"] = await build_rag_retrieval_debug_meta()
                except Exception as rag_meta_err:
                    logger.warning("[AgentService] Failed to build rag_retrieval meta: %s", rag_meta_err)
            yield meta_event

            # 4. Dispatch Executor
            secondary_agents = getattr(route_details, "secondary_agents", []) if route_details else []

            if enable_multi_agent and secondary_agents:
                performance_tracker.mark("executor_start")
                async for chunk in self._execute_multi_agent(
                    agent_config,
                    secondary_agents,
                    user_query,
                    messages,
                    trace_id,
                    trace_buffer,
                    debug_options,
                    permission_options,
                    user_info,
                    api_key,
                    conversation_id,
                    dispatch_turn_decision,
                ):
                    chunk = await _enrich_terminal_error_chunk(
                        chunk,
                        config=agent_config,
                        model_name=getattr(agent_config, "model_name", None),
                    )
                    performance_tracker.observe_chunk(chunk)
                    full_response_content = _accumulate_stream_content(full_response_content, chunk)
                    full_reasoning_content = _accumulate_reasoning_content(full_reasoning_content, chunk)
                    execution_status = _apply_turn_status_signal(execution_status, chunk)
                    _record_reusable_result_status(chunk)
                    yield chunk
                performance_tracker.mark("executor_finish")
            else:
                executor = await self._dispatch_executor(
                    agent_config=agent_config,
                    user_query=user_query,
                    messages=messages,
                    trace_id=trace_id,
                    trace_buffer=trace_buffer,
                    debug_options=debug_options,
                    permission_options=permission_options,
                    user_info=user_info,
                    conversation_id=conversation_id,
                    turn_decision=dispatch_turn_decision,
                )
                performance_tracker.mark("executor_start")

                yield _build_turn_execution_log(
                    turn_decision,
                    turn_display_label=turn_display_label,
                    execution_time_ms=turn_intent_elapsed_ms,
                )

                async for chunk in executor.execute(messages):
                    chunk = await _enrich_terminal_error_chunk(
                        chunk,
                        config=agent_config,
                        model_name=getattr(agent_config, "model_name", None),
                    )
                    performance_tracker.observe_chunk(chunk)
                    full_response_content = _accumulate_stream_content(full_response_content, chunk)
                    full_reasoning_content = _accumulate_reasoning_content(full_reasoning_content, chunk)
                    execution_status = _apply_turn_status_signal(execution_status, chunk)
                    _record_reusable_result_status(chunk)
                    yield chunk
                performance_tracker.mark("executor_finish")

                resolve_has_data_output = getattr(executor, "resolve_has_data_output", None)
                if callable(resolve_has_data_output):
                    has_data_output = bool(resolve_has_data_output())

                # A 项：本轮工具调用元数据转录，供跨轮持久化（独立字段，不污染 assistant 展示内容）
                tool_run_text = None
                resolve_tool_run_text = getattr(executor, "resolve_tool_run_text", None)
                if callable(resolve_tool_run_text):
                    tool_run_text = resolve_tool_run_text() or None

            # --- Empty Response Fallback ---
            if (
                execution_status == "success"
                and not (full_response_content or "").strip()
            ):
                fallback_text = await self._maybe_empty_response_fallback()
                if fallback_text:
                    full_response_content = fallback_text
                    yield {"content": fallback_text, "status": "success"}

            requires_tool_execution = bool(
                user_info
                and user_info.get("is_scheduled_task")
                and user_info.get("requires_tool_execution")
            )
            if (
                requires_tool_execution
                and execution_status == "success"
                and not _trace_has_tool_call(trace_buffer)
            ):
                execution_status = "no_tool_execution"
                no_tool_message = (
                    f"{NO_TOOL_EXECUTION_MESSAGE}，本次只产生了模型回复，没有产生工具调用；"
                    "已按未完成处理，请检查任务指令或智能体工具配置。"
                )
                full_response_content = (
                    f"{full_response_content}\n\n{no_tool_message}"
                    if full_response_content
                    else no_tool_message
                )
                no_tool_error = await _enrich_terminal_error_chunk({
                    "type": "error",
                    "status": "error",
                    "content": no_tool_message,
                }, config=agent_config, model_name=getattr(agent_config, "model_name", None))
                yield no_tool_error

            guarded_response_content = _filter_current_turn_download_urls(
                full_response_content
            )
            if guarded_response_content != full_response_content:
                full_response_content = guarded_response_content
                # The response may already have streamed speculative text. Replace
                # the visible body so the client and persisted history converge.
                yield {
                    "type": "retraction",
                    "content": full_response_content,
                }

            todo_completion = _finalize_todo_success(
                (shared_state or {}).get("process_timeline"),
                execution_status=execution_status,
            )
            if todo_completion:
                yield todo_completion

            p_tokens, c_tokens, t_tokens = 0, 0, 0
            try:
                from app.services.ai.audit import aggregate_tokens_from_trace_buffer
                p_tokens, c_tokens, t_tokens = aggregate_tokens_from_trace_buffer(trace_buffer) if trace_buffer else (0, 0, 0)
            except Exception as agg_err:
                logger.warning(f"Failed to aggregate tokens for session: {agg_err}")

            if p_tokens or c_tokens:
                yield {
                    "type": "meta",
                    "prompt_tokens": p_tokens,
                    "completion_tokens": c_tokens,
                    "total_tokens": t_tokens
                }

            if has_data_output and execution_status == "success":
                yield {"type": "meta", "has_data_output": True}

            final_process_timeline = _final_process_timeline(
                (shared_state or {}).get("process_timeline")
            )
            should_persist_history = bool(conversation_id and _should_persist_turn_history(
                full_response_content,
                final_process_timeline,
                full_reasoning_content,
            ))
            # 先通知客户端模型输出已结束，再等待 Redis/摘要持久化，避免 UI 一直显示“思考中”。
            yield {
                "type": "run_status",
                "status": execution_status,
                "trace_id": trace_id,
                "persisting": should_persist_history,
            }
            if should_persist_history:
                u_id = require_user_id(user_info)
                handled_by = getattr(agent_config, "agent_name", None) if agent_config else None
                await _persist_assistant_message_and_summary(
                    user_id=u_id,
                    conversation_id=conversation_id,
                    content=full_response_content,
                    trace_id=trace_id,
                    agent_name=handled_by,
                    agent_type=_public_agent_type(agent_config),
                    agent_display_name=(
                        getattr(agent_config, "agent_display_name", None) or None
                    ),
                    prompt_tokens=p_tokens,
                    completion_tokens=c_tokens,
                    total_tokens=t_tokens,
                    has_data_output=has_data_output or None,
                    reusable_result_id=(shared_state or {}).get("reusable_result_status", {}).get("result_id"),
                    reusable_result_status=(shared_state or {}).get("reusable_result_status", {}).get("status"),
                    reasoning_content=full_reasoning_content or None,
                    process_timeline=final_process_timeline,
                    tool_run_text=tool_run_text,
                    merge_summary=execution_status == "success",
                    defer_summary=True,
                    status=execution_status,
                )

        except asyncio.CancelledError:
            execution_status = "cancelled"
            if shared_state is not None:
                shared_state["execution_status"] = "cancelled"
            raise
        except Exception as e:
            logger.error(f"Execution Error: {str(e)}", exc_info=True)
            execution_status = "error"
            if shared_state.get("preparation_started_at") and not shared_state.get("preparation_ready"):
                preparation_elapsed_ms = (
                    asyncio.get_running_loop().time() - shared_state["preparation_started_at"]
                ) * 1000
                yield _build_preparation_parent_log(
                    status="error",
                    details=f"鉴权及上下文与能力准备失败：{sanitize_error_text(e)}",
                    execution_time_ms=preparation_elapsed_ms,
                )
            model_name = getattr(agent_config, "model_name", None) if agent_config else None
            error_chunk = await _enrich_terminal_error_chunk({
                "type": "error",
                "content": str(e),
                "status": "error",
            }, config=agent_config, model_name=model_name, source_exception=e)
            yield error_chunk
        finally:
            end_time = asyncio.get_running_loop().time()
            duration = (end_time - start_time) * 1000

            is_scheduled_task = bool(user_info and user_info.get("is_scheduled_task"))
            audit_completed = False
            try:
                if (
                    execution_status == "cancelled"
                    and conversation_id
                    and _should_persist_turn_history(
                        full_response_content,
                        _final_process_timeline((shared_state or {}).get("process_timeline")),
                        full_reasoning_content,
                    )
                ):
                    full_response_content = _filter_current_turn_download_urls(
                        full_response_content
                    )
                    from app.core.cancellation import spawn_detached

                    persistence_task = spawn_detached(
                        _persist_assistant_message_and_summary(
                            user_id=require_user_id(user_info),
                            conversation_id=conversation_id,
                            content=full_response_content,
                            trace_id=trace_id,
                            agent_name=(getattr(agent_config, "agent_name", None) if agent_config else None),
                            agent_type=_public_agent_type(agent_config),
                            agent_display_name=(
                                getattr(agent_config, "agent_display_name", None) or None
                            ),
                            reasoning_content=full_reasoning_content or None,
                            process_timeline=_final_process_timeline(
                                (shared_state or {}).get("process_timeline")
                            ),
                            merge_summary=False,
                            defer_summary=True,
                            status=execution_status,
                        ),
                        name=f"persist-cancelled-run-{trace_id}",
                    )
                    if run_handle is not None:
                        run_handle.persistence_task = persistence_task
                if execution_status not in AWAITING_RESUME_STATUSES or is_scheduled_task:
                    from app.core.cancellation import await_unless_cancelling

                    async def _audit_cancelled_run():
                        await AuditManager.log_transaction(
                            trace_id, agent_config, user_query, full_response_content,
                            user_info, execution_status, duration, trace_buffer,
                            conversation_id=conversation_id,
                            reasoning_content=full_reasoning_content or None,
                            process_timeline=_final_process_timeline(
                                (shared_state or {}).get("process_timeline")
                            ),
                            has_data_output=has_data_output if execution_status == "success" else None,
                        )

                    await await_unless_cancelling(
                        _audit_cancelled_run,
                        name=f"audit-run-{trace_id}",
                    )
                    audit_completed = True
            finally:
                if audit_completed:
                    performance_tracker.mark("audit_finish")
                performance_snapshot = performance_tracker.snapshot(
                    trace_buffer=trace_buffer,
                    status=execution_status,
                )
                performance_snapshot["audit_completed"] = audit_completed
                if shared_state is not None:
                    shared_state["execution_performance"] = performance_snapshot
                logger.info(
                    "[AgentPerformance] trace_id=%s metrics=%s",
                    trace_id,
                    performance_snapshot,
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
            "intent": "general_chat", # Simplified, real intent is in stream but not easily exposed here without refactor
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
        from app.services.ai.runtime.agentscope.confirmations import (
            pending_agentscope_confirmations,
        )

        current_user_id = None
        if user_info:
            current_user_id = user_info.get("user_id") or user_info.get("id")

        pending = await pending_agentscope_confirmations.pop_async(
            permission_request_id,
            user_id=current_user_id,
        )
        if not pending:
            yield {
                "type": "error",
                "status": "error",
                "content": "工具确认请求不存在或已过期，请重新发起本轮对话。",
            }
            return

        if pending.user_id and current_user_id and str(current_user_id) != str(pending.user_id):
            yield {
                "type": "error",
                "status": "error",
                "content": "当前用户无权确认该工具调用。",
            }
            return

        if pending.snapshot.kind == "external":
            yield {
                "type": "error",
                "status": "error",
                "content": "该请求为外部执行挂起，请使用 external execution 恢复接口。",
            }
            return

        if confirmed and user_info:
            quota_block = await self._quota_block_message(user_info)
            if quota_block:
                yield {
                    "type": "error",
                    "status": "quota_exceeded",
                    "content": quota_block,
                    "trace_id": pending.trace_id,
                }
                return

        runner = self._build_agentscope_runner_from_pending(pending, user_info=user_info)
        await self._restore_runner_execution_context(
            runner,
            pending,
            user_info=user_info,
        )

        process_timeline_state: List[Dict[str, Any]] = []
        _restore_todo_snapshot_from_pending(process_timeline_state, pending)
        permission_chunk = {
            "type": "permission_result",
            "status": "success" if confirmed else "rejected",
            "permission_request_id": permission_request_id,
            "tool_call_id": getattr(pending.tool_call, "id", None),
        }
        _track_process_timeline(process_timeline_state, permission_chunk)
        yield permission_chunk

        full_response_content = ""
        full_reasoning_content = ""
        execution_status = "success" if confirmed else "rejected"
        start_time = asyncio.get_running_loop().time()
        conversation_id = runner.conversation_id or pending.snapshot.conversation_id
        lane_user_id = current_user_id or pending.user_id

        try:
            async with track_conversation_run(
                lane_user_id, conversation_id
            ) as run_handle, conversation_run_lane.hold(
                user_id=lane_user_id,
                conversation_id=conversation_id,
                trace_id=pending.trace_id,
            ):
                async for chunk in runner.resume_agentscope_native_confirmation(
                    pending,
                    confirmed=confirmed,
                ):
                    if run_handle is not None and run_handle.cancelled:
                        raise asyncio.CancelledError
                    chunk = await _enrich_terminal_error_chunk(
                        chunk,
                        config=runner.config,
                        model_name=getattr(runner.config, "model_name", None),
                    )
                    full_response_content = _accumulate_stream_content(full_response_content, chunk)
                    full_reasoning_content = _accumulate_reasoning_content(full_reasoning_content, chunk)
                    _track_process_timeline(process_timeline_state, chunk)
                    if confirmed:
                        execution_status = _apply_turn_status_signal(execution_status, chunk)
                    yield chunk
        except ConversationRunBusyError:
            yield {
                "type": "error",
                "status": "error",
                "content": "当前会话正在处理中，请稍后再试。",
            }
            return

        guarded_response_content = _filter_current_turn_download_urls(
            full_response_content
        )
        if guarded_response_content != full_response_content:
            full_response_content = guarded_response_content
            yield {
                "type": "retraction",
                "content": full_response_content,
            }

        todo_completion = _finalize_todo_success(
            process_timeline_state,
            execution_status=execution_status,
        )
        if todo_completion:
            yield todo_completion

        p_tokens, c_tokens, t_tokens = 0, 0, 0
        trace_buffer = runner.trace_buffer
        try:
            from app.services.ai.audit import aggregate_tokens_from_trace_buffer
            p_tokens, c_tokens, t_tokens = aggregate_tokens_from_trace_buffer(trace_buffer) if trace_buffer else (0, 0, 0)
        except Exception as agg_err:
            logger.warning(f"Failed to aggregate tokens after permission resume: {agg_err}")

        if p_tokens or c_tokens:
            yield {
                "type": "meta",
                "prompt_tokens": p_tokens,
                "completion_tokens": c_tokens,
                "total_tokens": t_tokens,
            }

        agent_config = runner.config
        conversation_id = runner.conversation_id or pending.snapshot.conversation_id
        user_query = (pending.state or {}).get("user_query") or ""

        final_process_timeline = _final_process_timeline(process_timeline_state)
        should_persist_history = bool(conversation_id and _should_persist_turn_history(
            full_response_content,
            final_process_timeline,
            full_reasoning_content,
        ))
        yield {
            "type": "run_status",
            "status": execution_status,
            "trace_id": pending.trace_id,
            "persisting": should_persist_history,
        }
        if should_persist_history:
            u_id = user_info.get("user_id") if user_info else pending.user_id
            handled_by = getattr(agent_config, "agent_name", None) if agent_config else None
            resolve_tool_run_text = getattr(runner, "resolve_tool_run_text", None)
            tool_run_text = (
                resolve_tool_run_text() or None
                if callable(resolve_tool_run_text)
                else None
            )
            await _persist_assistant_message_and_summary(
                user_id=u_id,
                conversation_id=conversation_id,
                content=full_response_content,
                trace_id=pending.trace_id,
                agent_name=handled_by,
                agent_type=_public_agent_type(agent_config),
                agent_display_name=(
                    getattr(agent_config, "agent_display_name", None) or handled_by
                ),
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
                total_tokens=t_tokens,
                reasoning_content=full_reasoning_content or None,
                process_timeline=final_process_timeline,
                tool_run_text=tool_run_text,
                merge_summary=execution_status == "success",
                defer_summary=True,
                status=execution_status,
            )

        duration = (asyncio.get_running_loop().time() - start_time) * 1000
        await AuditManager.log_transaction(
            pending.trace_id,
            agent_config,
            user_query,
            full_response_content,
            user_info,
            execution_status,
            duration,
            trace_buffer,
            conversation_id=conversation_id,
            reasoning_content=full_reasoning_content or None,
            process_timeline=_final_process_timeline(process_timeline_state),
        )

    async def _restore_runner_execution_context(
        self,
        runner: Any,
        pending: Any,
        *,
        user_info: Optional[Dict[str, Any]] = None,
    ) -> None:
        """工具确认/外部执行恢复前重建 AgentContext，避免 user_id 等会话信息丢失。"""
        effective_user_info = user_info or getattr(runner, "user_info", None)
        if effective_user_info and getattr(runner, "config", None) is not None:
            from app.services.ai.context_manager import AgentContextManager
            runtime_model_info = await self._resolve_runtime_model_info_safe(
                config=runner.config,
                debug_options=dict(getattr(runner, "debug_options", {}) or {}),
            )
            runtime_context_metadata = await self._runtime_context_metadata(
                runtime_model_info
            )

            await AgentContextManager.setup_context(
                config=runner.config,
                debug_options=dict(getattr(runner, "debug_options", {}) or {}),
                user_info=effective_user_info,
                api_key=effective_user_info.get("api_key"),
                conversation_id=(
                    getattr(runner, "conversation_id", None)
                    or pending.snapshot.conversation_id
                ),
                trace_buffer=getattr(runner, "trace_buffer", None) or [],
                runtime_model_info=runtime_context_metadata,
                published_download_urls=_restore_published_download_urls_from_pending(pending),
                agent_max_toolcall_timeout_seconds=(
                    dict(getattr(runner, "debug_options", {}) or {}).get(
                        "_agent_max_toolcall_timeout_seconds"
                    )
                ),
            )
            return
        if hasattr(runner, "_ensure_agent_context"):
            context = runner._ensure_agent_context()
            restored_urls = _restore_published_download_urls_from_pending(pending)
            if restored_urls:
                context.published_download_urls = restored_urls

    def _build_agentscope_runner_from_pending(
        self,
        pending: Any,
        *,
        user_info: Optional[Dict[str, Any]] = None,
    ) -> Any:
        runner = pending.runner
        if runner is not None:
            if user_info:
                runner.user_info = {**(runner.user_info or {}), **user_info}
            return runner

        ctx = pending.snapshot.runner_context or {}
        if ctx.get("runner_type") == "data":
            from app.services.ai.runners.data_agent_runner import DataAgentRunner

            return DataAgentRunner.from_runner_context(
                runner_context=ctx,
                trace_id=pending.trace_id,
                trace_buffer=[],
                user_info=user_info,
                conversation_id=pending.snapshot.conversation_id,
            )

        from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner

        if ctx.get("runner_type") in ("assistant", "general"):
            return AssistantAgentRunner.from_runner_context(
                runner_context=ctx,
                trace_id=pending.trace_id,
                trace_buffer=[],
                user_info=user_info,
                conversation_id=pending.snapshot.conversation_id,
            )

        raise ValueError(f"Unsupported runner_type for resume: {ctx.get('runner_type')!r}")

    @staticmethod
    def _build_external_execution_results(results: List[Dict[str, Any]]) -> List[Any]:
        from agentscope.message import ToolResultBlock, ToolResultState

        state_map = {
            "success": ToolResultState.SUCCESS,
            "error": ToolResultState.ERROR,
            "running": ToolResultState.RUNNING,
            "interrupted": ToolResultState.INTERRUPTED,
            "denied": ToolResultState.DENIED,
        }
        return [
            ToolResultBlock(
                id=str(item.get("id") or ""),
                name=str(item.get("name") or ""),
                output=str(item.get("output") or ""),
                state=state_map.get(str(item.get("state") or "success").lower(), ToolResultState.SUCCESS),
            )
            for item in results
        ]

    async def resume_agentscope_external_execution_stream(
        self,
        *,
        external_execution_request_id: str,
        results: List[Dict[str, Any]],
        user_info: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from app.services.ai.runtime.agentscope.confirmations import (
            pending_agentscope_confirmations,
        )

        current_user_id = None
        if user_info:
            current_user_id = user_info.get("user_id") or user_info.get("id")

        pending = await pending_agentscope_confirmations.pop_async(
            external_execution_request_id,
            user_id=current_user_id,
        )
        if not pending:
            yield {
                "type": "error",
                "status": "error",
                "content": "外部执行请求不存在或已过期，请重新发起本轮对话。",
            }
            return

        if pending.user_id and current_user_id and str(current_user_id) != str(pending.user_id):
            yield {
                "type": "error",
                "status": "error",
                "content": "当前用户无权提交该外部执行结果。",
            }
            return

        if pending.snapshot.kind != "external":
            yield {
                "type": "error",
                "status": "error",
                "content": "该请求不是外部执行挂起，请使用 permission confirm 接口。",
            }
            return

        if user_info:
            quota_block = await self._quota_block_message(user_info)
            if quota_block:
                yield {
                    "type": "error",
                    "status": "quota_exceeded",
                    "content": quota_block,
                    "trace_id": pending.trace_id,
                }
                return

        runner = self._build_agentscope_runner_from_pending(pending, user_info=user_info)
        await self._restore_runner_execution_context(
            runner,
            pending,
            user_info=user_info,
        )
        execution_results = self._build_external_execution_results(results)

        process_timeline_state: List[Dict[str, Any]] = []
        _restore_todo_snapshot_from_pending(process_timeline_state, pending)
        external_chunk = {
            "type": "external_execution_result",
            "status": "success",
            "external_execution_request_id": external_execution_request_id,
            "tool_call_id": getattr(pending.tool_call, "id", None),
        }
        _track_process_timeline(process_timeline_state, external_chunk)
        yield external_chunk

        full_response_content = ""
        full_reasoning_content = ""
        execution_status = "success"
        start_time = asyncio.get_running_loop().time()
        conversation_id = runner.conversation_id or pending.snapshot.conversation_id
        lane_user_id = current_user_id or pending.user_id

        try:
            async with track_conversation_run(
                lane_user_id, conversation_id
            ) as run_handle, conversation_run_lane.hold(
                user_id=lane_user_id,
                conversation_id=conversation_id,
                trace_id=pending.trace_id,
            ):
                async for chunk in runner.resume_agentscope_external_execution(
                    pending,
                    execution_results=execution_results,
                ):
                    if run_handle is not None and run_handle.cancelled:
                        raise asyncio.CancelledError
                    chunk = await _enrich_terminal_error_chunk(
                        chunk,
                        config=runner.config,
                        model_name=getattr(runner.config, "model_name", None),
                    )
                    full_response_content = _accumulate_stream_content(full_response_content, chunk)
                    full_reasoning_content = _accumulate_reasoning_content(full_reasoning_content, chunk)
                    _track_process_timeline(process_timeline_state, chunk)
                    execution_status = _apply_turn_status_signal(execution_status, chunk)
                    yield chunk
        except ConversationRunBusyError:
            yield {
                "type": "error",
                "status": "error",
                "content": "当前会话正在处理中，请稍后再试。",
            }
            return

        guarded_response_content = _filter_current_turn_download_urls(
            full_response_content
        )
        if guarded_response_content != full_response_content:
            full_response_content = guarded_response_content
            yield {
                "type": "retraction",
                "content": full_response_content,
            }

        todo_completion = _finalize_todo_success(
            process_timeline_state,
            execution_status=execution_status,
        )
        if todo_completion:
            yield todo_completion

        p_tokens, c_tokens, t_tokens = 0, 0, 0
        trace_buffer = runner.trace_buffer
        try:
            from app.services.ai.audit import aggregate_tokens_from_trace_buffer
            p_tokens, c_tokens, t_tokens = aggregate_tokens_from_trace_buffer(trace_buffer) if trace_buffer else (0, 0, 0)
        except Exception as agg_err:
            logger.warning(f"Failed to aggregate tokens after external resume: {agg_err}")

        if p_tokens or c_tokens:
            yield {
                "type": "meta",
                "prompt_tokens": p_tokens,
                "completion_tokens": c_tokens,
                "total_tokens": t_tokens,
            }

        agent_config = runner.config
        conversation_id = runner.conversation_id or pending.snapshot.conversation_id
        user_query = (pending.state or {}).get("user_query") or ""

        final_process_timeline = _final_process_timeline(process_timeline_state)
        should_persist_history = bool(conversation_id and _should_persist_turn_history(
            full_response_content,
            final_process_timeline,
            full_reasoning_content,
        ))
        yield {
            "type": "run_status",
            "status": execution_status,
            "trace_id": pending.trace_id,
            "persisting": should_persist_history,
        }
        if should_persist_history:
            u_id = user_info.get("user_id") if user_info else pending.user_id
            handled_by = getattr(agent_config, "agent_name", None) if agent_config else None
            resolve_tool_run_text = getattr(runner, "resolve_tool_run_text", None)
            tool_run_text = (
                resolve_tool_run_text() or None
                if callable(resolve_tool_run_text)
                else None
            )
            await _persist_assistant_message_and_summary(
                user_id=u_id,
                conversation_id=conversation_id,
                content=full_response_content,
                trace_id=pending.trace_id,
                agent_name=handled_by,
                agent_type=_public_agent_type(agent_config),
                agent_display_name=(
                    getattr(agent_config, "agent_display_name", None) or handled_by
                ),
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
                total_tokens=t_tokens,
                reasoning_content=full_reasoning_content or None,
                process_timeline=final_process_timeline,
                tool_run_text=tool_run_text,
                merge_summary=execution_status == "success",
                defer_summary=True,
                status=execution_status,
            )

        duration = (asyncio.get_running_loop().time() - start_time) * 1000
        await AuditManager.log_transaction(
            pending.trace_id,
            agent_config,
            user_query,
            full_response_content,
            user_info,
            execution_status,
            duration,
            trace_buffer,
            conversation_id=conversation_id,
            reasoning_content=full_reasoning_content or None,
            process_timeline=_final_process_timeline(process_timeline_state),
        )

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
        """
        Executes primary and secondary agents in parallel and yields combined results.
        """
        # 1. Resolve Secondary Configs
        secondary_configs = []
        async with AsyncSessionLocal() as session:
            for s_id in secondary_agent_ids:
                s_config = await AgentManagerService.get_active_agent_config(session, agent_id=s_id)
                if s_config:
                    secondary_configs.append(s_config)

        # 2. Setup Executors
        all_configs = [primary_config] + secondary_configs
        executors = []
        for config in all_configs:
            exec = await AgentDispatcher.dispatch(
                config,
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
            executors.append(exec)

        yield {
            "type": "log",
            "title": "多智能体协作",
            "details": f"正在并行调度 {len(executors)} 个专家智能体: " + ", ".join([c.agent_name for c in all_configs]),
            "status": "success"
        }

        # 3. Parallel Execution with Queue-based log streaming
        queue = asyncio.Queue()

        async def run_executor(executor, config):
            full_text = ""
            stream_error = None
            try:
                # We need a clean copy of messages for each executor as they might modify it?
                # Actually most executors just read it.
                async for chunk in executor.execute(messages):
                    chunk_type = chunk.get("type")
                    if chunk_type == "error":
                        stream_error = {**chunk, "agent_name": config.agent_name}
                        await queue.put(stream_error)
                        break
                    full_text = _accumulate_stream_content(full_text, chunk)
                    if chunk_type in {
                        "process_narration",
                        "process_narration_commit",
                    }:
                        # Process narration is part of the user-visible stream.
                        # Tag the expert so the client can keep parallel cards
                        # apart instead of merging every pending delta.
                        await queue.put({**chunk, "agent_name": config.agent_name})
                    elif chunk_type in {
                        "process_narration_promote",
                        "answer_delta",
                        "retraction",
                    }:
                        # Expert answers are input to the final synthesis.
                        # Do not forward them to the main chat.
                        continue
                    elif chunk_type in ["log", "router_log"]:
                        # Prefix log title with Agent Name to identify the source
                        if "title" in chunk:
                             chunk["title"] = f"[{config.agent_name}] {chunk['title']}"
                        await queue.put(chunk)
                    elif chunk_type == "thinking":
                        # Forward thinking status (might overlap, but SSE handles it)
                        await queue.put(chunk)
            except Exception as e:
                logger.error(f"Error in multi-agent sub-task ({config.agent_name}): {e}", exc_info=True)
                await queue.put({
                    "type": "log",
                    "title": f"[{config.agent_name}] 执行异常",
                    "details": sanitize_error_text(e),
                    "status": "error"
                })
                full_text = f"【{config.agent_name} 执行失败】: {sanitize_error_text(e)}"
            if stream_error is not None:
                return {"name": config.agent_name, "content": "", "error": stream_error}
            return {"name": config.agent_name, "content": full_text}

        # Start all tasks
        tasks = [asyncio.create_task(run_executor(exec, conf)) for exec, conf in zip(executors, all_configs)]
        results_task = asyncio.gather(*tasks, return_exceptions=True)
        stream_error = None

        # Stream logs while tasks are running
        while not results_task.done() or not queue.empty():
            try:
                # Use wait_for to check done status frequently
                chunk = await asyncio.wait_for(queue.get(), timeout=0.1)
                if chunk.get("type") == "error" and stream_error is None:
                    stream_error = chunk
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                yield chunk
                queue.task_done()
            except (asyncio.TimeoutError, asyncio.QueueEmpty):
                if results_task.done() and queue.empty():
                    break
                await asyncio.sleep(0.01)

        if stream_error is not None:
            # 流式安全错误已经直接交给前端；取消剩余专家并跳过最终合成，
            # 避免把半截结果再次交给主模型解释或生成新的正文。
            await results_task
            return
        agent_results = await results_task
        agent_outputs = [result for result in agent_results if isinstance(result, dict)]

        # 4. Final Synthesis
        yield {
            "type": "log",
            "title": "结果聚合",
            "details": "正在汇总各专家意见并组织最终回答...",
            "status": "success"
        }

        async for chunk in self._synthesize_multi_agent_results(
            primary_config, user_query, agent_outputs, trace_buffer
        ):
            yield chunk

    async def _synthesize_multi_agent_results(
        self,
        config: ChatConfig,
        user_query: str,
        agent_outputs: List[Dict[str, str]],
        trace_buffer: List[AgentExecutionStep],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Synthesizes multiple agent outputs into a unified response.
        """
        outputs_str = ""
        for out in agent_outputs:
            outputs_str += f"### 专家智能体: {out['name']}\n{out['content']}\n\n"

        system_prompt = AgentServicePrompts.MULTI_AGENT_SYNTHESIS_SYSTEM

        human_content = AgentServicePrompts.multi_agent_synthesis_human(user_query, outputs_str)

        # Use synthesis model from primary agent config
        llm = await AgentConfigProvider.get_synthesis_llm(streaming=True, config=config)

        lc_messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_content)
        ]

        start_synthesis = time.time()
        full_content = ""
        accumulated_msg = None
        async for chunk in llm.astream(lc_messages):
            if accumulated_msg is None:
                accumulated_msg = chunk
            else:
                accumulated_msg += chunk
            content = sanitize_assistant_stream_text(str(chunk.content or ""))
            if content:
                full_content += content
                yield {"type": "answer_delta", "content": content, "phase": "synthesis"}

        tokens = extract_tokens_from_message(accumulated_msg)
        step_number = max((s.step_number for s in trace_buffer), default=0) + 1
        s_model = getattr(llm, "model_name", config.synthesis_model_name or config.model_name)
        s_temp = config.synthesis_temperature or config.temperature
        trace_buffer.append(
            AgentExecutionStep(
                step_number=step_number,
                event_type="synthesis",
                agent_name=config.agent_name,
                model=str(s_model),
                temperature=float(s_temp or 0),
                tool_output={"content": full_content, "multi_agent_synthesis": True},
                raw_log=full_content,
                execution_time_ms=(time.time() - start_synthesis) * 1000,
                prompt_tokens=tokens["prompt_tokens"],
                completion_tokens=tokens["completion_tokens"],
                total_tokens=tokens["total_tokens"],
                timestamp=datetime.fromtimestamp(start_synthesis),
            )
        )

    def __init__(self):
        pass

agent_service = AgentService()
