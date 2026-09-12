import logging
import asyncio
import json
import re
import uuid
import inspect
import time
from dataclasses import replace
from typing import Optional, Dict, Any, List, Tuple, Iterable
from pydantic import BaseModel, Field, field_validator
from app.services.ai.tools.tool_compat import tool
from app.services.ai.tools.tool_compat import BaseTool
from app.core.context import get_current_agent_context, AgentContext, set_agent_context
from app.core.orm import AsyncSessionLocal
from app.services.ai.agent_manager import AgentManagerService
from app.services.permission_service import PermissionService
from app.services.ai.subagent_protocol import (
    EMPTY_SUB_AGENT_RESULT_MESSAGE,
    SubAgentRequest,
    SubAgentResult,
    SubAgentResultStatus,
    SubAgentStopReason,
    validate_structured_schema,
    validate_structured_output,
)
from app.services.ai.turn_decision import TurnDecision

logger = logging.getLogger(__name__)

DEFAULT_DELEGATION_RESULT_MAX_CHARS = 8000
MAX_DELEGATION_CALLS_PER_AGENT = 2
MAX_DELEGATION_DEPTH = 1
MAX_BATCH_DELEGATION_CALLS = 4

INTERRUPT_SSE_TYPES = frozenset({"permission_required", "external_execution_required", "error"})

EMPTY_DELEGATION_RESULT_MESSAGE = EMPTY_SUB_AGENT_RESULT_MESSAGE


def _batch_result_status(content: str) -> str:
    """Classify the backward-compatible single-call text for batch counters."""
    normalized = str(content or "").strip()
    if normalized.startswith(("错误：", "错误:")):
        return "failed"
    if normalized.startswith(EMPTY_DELEGATION_RESULT_MESSAGE):
        return "failed"
    return "completed"

DELEGATION_INTERRUPT_MESSAGES = {
    "permission_required": (
        "错误：子智能体执行过程中需要用户确认工具权限，当前委派模式无法在子流程中完成确认。"
        "请直接打开对应子智能体对话，或联系管理员调整工具自动执行策略。"
    ),
    "external_execution_required": (
        "错误：子智能体需要外部执行确认，当前委派模式不支持。请直接打开对应子智能体对话。"
    ),
    "error": "错误：子智能体流式执行失败，委派未完成。",
}


def clean_sub_agent_output(text: str) -> str:
    """滤除 <sql_plan>...</sql_plan> 标签以防上下文污染，支持多行匹配。"""
    if not text:
        return ""
    cleaned = re.sub(r"<sql_plan>.*?</sql_plan>", "", text, flags=re.DOTALL)
    return cleaned.strip()


def _extract_delegation_text(chunk: Dict[str, Any]) -> str:
    """从子 Executor chunk 中提取可交付给主助手的文本（不含 log 进度）。"""
    content = chunk.get("content")
    if content:
        return str(content)
    for key in ("text", "message"):
        value = chunk.get(key)
        if value:
            return str(value)
    return ""


def resolve_delegation_permission_options(
    main_options: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """继承主流程审批策略；默认 ask，避免静默委派绕过写入/外部工具确认。"""
    options = dict(main_options or {})
    options.setdefault("approval_mode", "ask")
    return options


def resolve_delegation_depth(
    current_depth: int,
    requested_max_depth: int | None,
) -> tuple[int, str | None]:
    """Resolve one child depth without allowing a caller to widen the platform cap."""
    child_depth = int(current_depth) + 1
    if requested_max_depth is not None and (
        isinstance(requested_max_depth, bool)
        or not isinstance(requested_max_depth, int)
        or requested_max_depth < 0
    ):
        return child_depth, "depth_exceeded"
    effective_max_depth = MAX_DELEGATION_DEPTH
    if requested_max_depth is not None:
        effective_max_depth = min(effective_max_depth, requested_max_depth)
    if child_depth > effective_max_depth:
        return child_depth, "depth_exceeded"
    return child_depth, None


def _configured_tool_name(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("name") or "").strip()
    return str(getattr(item, "name", "") or "").strip()


def _canonical_tool_name(name: str) -> str:
    from app.services.ai.tools.registry import AGENTSCOPE_BUILTIN_TOOL_ALIASES

    return AGENTSCOPE_BUILTIN_TOOL_ALIASES.get(name, name)


def resolve_delegation_tool_filter(
    configured_tools: Iterable[Any],
    requested_filter: Iterable[str] | None,
) -> tuple[list[str] | None, str | None]:
    """Return a request-scoped tool allowlist that can only narrow configured tools."""
    if requested_filter is None:
        return None, None

    available = {
        _canonical_tool_name(name)
        for name in (_configured_tool_name(item) for item in configured_tools)
        if name
    }
    resolved: list[str] = []
    seen: set[str] = set()
    for raw_name in requested_filter:
        name = _canonical_tool_name(str(raw_name or "").strip())
        if not name:
            return None, "unknown_tool"
        if name not in available:
            return None, "unknown_tool"
        if name not in seen:
            resolved.append(name)
            seen.add(name)
    return resolved, None


def _structured_from_text(text: str) -> Any:
    candidate = str(text or "").strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        lines = candidate.splitlines()
        candidate = "\n".join(lines[1:-1]).strip()
    try:
        return json.loads(candidate)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _annotate_subagent_trace(
    trace_buffer: List[Any],
    start_index: int,
    *,
    metadata: Dict[str, Any],
    stop_reason: str,
) -> None:
    """Attach non-sensitive parent/child metadata to steps emitted by one child run."""
    annotated_metadata = {**metadata, "stop_reason": stop_reason}
    for step in list(trace_buffer)[start_index:]:
        if isinstance(step, dict):
            step_meta = dict(step.get("meta_info") or {})
            step_meta["subagent"] = annotated_metadata
            step["meta_info"] = step_meta
            continue
        step_meta = dict(getattr(step, "meta_info", None) or {})
        step_meta["subagent"] = annotated_metadata
        step.meta_info = step_meta


def _put_subagent_lifecycle_log(
    ctx: AgentContext,
    *,
    log_id: str,
    metadata: Dict[str, Any],
    status: str,
    details: str,
    started_at: int,
    execution_time_ms: int | None = None,
) -> None:
    """Expose one compact delegation lifecycle event to the live thought card."""
    if not ctx.event_queue:
        return
    display_name = metadata.get("display_name") or metadata.get("agent_name") or "子代理"
    payload = {
        "type": "log",
        "id": log_id,
        "title": f"调用子代理: {display_name}",
        "details": details,
        "status": status,
        "category": "agent",
        "started_at": started_at,
        "subagent": dict(metadata),
    }
    if execution_time_ms is not None:
        payload["execution_time_ms"] = execution_time_ms
    ctx.event_queue.put_nowait(payload)


def _normalize_agent_name(value: str | None) -> str:
    return (value or "").lower().replace("-", "_").strip()


def _normalize_delegation_query(value: str | None) -> str:
    return " ".join((value or "").strip().split())


def _delegation_signature(agent_name: str | None, query: str | None) -> str:
    return f"{_normalize_agent_name(agent_name)}:{_normalize_delegation_query(query)}"


def _record_delegation_attempt(
    ctx: AgentContext,
    *,
    agent_name: str,
    display_name: str,
    query: str,
) -> str | None:
    signature = _delegation_signature(agent_name, query)
    call_counts = ctx.delegation_call_counts
    if call_counts.get(signature, 0) >= 1:
        return (
            f"错误：本轮已经对 `{agent_name}`（{display_name}）使用相同问题执行过一次 sub_agent_call。"
            "请勿重复委派；请基于上一次工具结果回答，或向用户说明子智能体结果不足。"
        )

    agent_key = _normalize_agent_name(agent_name)
    agent_counts = ctx.delegation_agent_call_counts
    if agent_counts.get(agent_key, 0) >= MAX_DELEGATION_CALLS_PER_AGENT:
        return (
            f"错误：本轮已多次委派 `{agent_name}`（{display_name}），系统已阻止继续重复调用。"
            "请基于已有子智能体结果回答，或向用户说明需要切换到对应子智能体对话继续处理。"
        )

    call_counts[signature] = call_counts.get(signature, 0) + 1
    agent_counts[agent_key] = agent_counts.get(agent_key, 0) + 1
    return None


def _matches_requested_agent(agent: Any, requested_name: str) -> bool:
    target_clean = _normalize_agent_name(requested_name)
    agent_name = getattr(agent, "name", None)
    if agent_name and _normalize_agent_name(agent_name) == target_clean:
        return True
    display_name = getattr(agent, "display_name", None)
    if display_name and display_name.strip() == requested_name.strip():
        return True
    if display_name and display_name.lower().strip() == requested_name.lower().strip():
        return True
    return False


async def can_delegate_to_agent(
    session: Any,
    *,
    user_id: int | str | None,
    is_admin: bool,
    target_agent_id: str,
) -> bool:
    if not user_id or is_admin:
        return True
    perm_service = PermissionService(session)
    return await perm_service.check_permission(int(user_id), "agent", str(target_agent_id))


async def filter_delegable_system_agents(
    session: Any,
    agents: Iterable[Any],
    *,
    user_id: int | str | None,
    is_admin: bool,
    current_agent_id: str | None,
) -> List[Any]:
    delegable: List[Any] = []
    for agent in agents or []:
        if not getattr(agent, "is_enabled", False) or not getattr(agent, "is_system", False):
            continue
        agent_id = str(getattr(agent, "id", "") or "")
        if current_agent_id and agent_id == str(current_agent_id):
            continue
        if await can_delegate_to_agent(
            session,
            user_id=user_id,
            is_admin=is_admin,
            target_agent_id=agent_id,
        ):
            delegable.append(agent)
    return delegable


async def resolve_runnable_delegable_system_agents(
    session: Any,
    agents: Iterable[Any],
    *,
    user_id: int | str | None,
    is_admin: bool,
    current_agent_id: str | None,
) -> List[Any]:
    """Return permitted system agents that have a loadable, ready runtime."""
    import json
    from app.services.ai.agent_readiness import evaluate_agent_readiness
    from app.services.ai.agent_types import resolve_agent_type

    permitted = await filter_delegable_system_agents(
        session,
        agents,
        user_id=user_id,
        is_admin=is_admin,
        current_agent_id=current_agent_id,
    )
    if not permitted:
        return []

    # 批量读取本地专家的最新发布版本，消除 N 次串行 DB 查询
    local_agents = [
        a for a in permitted
        if getattr(a, "engine_type", None) not in ("RAGFLOW", "OPENCLAW")
    ]
    local_agent_ids = [str(getattr(a, "id", "") or "") for a in local_agents if getattr(a, "id", None)]

    published_versions: Optional[Dict[str, Any]] = {}
    if local_agent_ids and hasattr(AgentManagerService, "_latest_published_versions_by_agent"):
        try:
            published_versions = await AgentManagerService._latest_published_versions_by_agent(
                session, local_agent_ids
            )
        except Exception as exc:
            logger.debug("[agent_delegate_tool] Batch version load fallback: %s", exc)
            published_versions = None
    elif local_agent_ids:
        published_versions = None

    runnable: List[Any] = []
    for agent in permitted:
        if not getattr(agent, "is_enabled", True):
            continue

        agent_type = resolve_agent_type(agent)
        engine_type = getattr(agent, "engine_type", None)

        if engine_type in ("RAGFLOW", "OPENCLAW"):
            readiness = evaluate_agent_readiness(
                agent_type=agent_type,
                capabilities=getattr(agent, "capabilities", None) or [],
                engine_config=getattr(agent, "engine_config", None) or {},
                tools=[],
                has_published_version=True,
            )
            if readiness.ready:
                runnable.append(agent)
            continue

        if published_versions is not None:
            agent_id = str(getattr(agent, "id", "") or "")
            version = published_versions.get(agent_id)
            if not version:
                continue
            tools_list = getattr(version, "tools", None) or []
            if isinstance(tools_list, str):
                try:
                    tools_list = json.loads(tools_list)
                except Exception:
                    tools_list = []
            readiness = evaluate_agent_readiness(
                agent_type=agent_type,
                capabilities=getattr(version, "capabilities", None) or [],
                engine_config=getattr(version, "engine_config", None) or {},
                tools=tools_list or [],
                has_published_version=True,
            )
            if readiness.ready:
                runnable.append(agent)
        else:
            # Fallback 逐个加载配置
            config = await AgentManagerService.get_active_agent_config(
                session,
                agent_id=str(getattr(agent, "id", "") or ""),
            )
            if not config:
                continue
            readiness = evaluate_agent_readiness(
                agent_type=agent_type,
                capabilities=config.capabilities,
                engine_config=config.engine_config,
                tools=config.tools,
                has_published_version=True,
            )
            if readiness.ready:
                runnable.append(agent)

    return sorted(
        runnable,
        key=lambda agent: (
            -int(getattr(agent, "sort_order", 0) or 0),
            str(getattr(agent, "id", "") or ""),
        ),
    )


def delegable_agent_name_aliases(agents: Iterable[Any]) -> set[str]:
    aliases: set[str] = set()
    for agent in agents or []:
        name = getattr(agent, "name", None)
        if name:
            name_str = str(name)
            aliases.add(name_str)
            aliases.add(name_str.replace("_", "-"))
            aliases.add(name_str.replace("-", "_"))
        display_name = getattr(agent, "display_name", None)
        if display_name:
            aliases.add(str(display_name))
    return aliases


def finalize_delegation_output(
    full_output: str,
    *,
    max_chars: int = DEFAULT_DELEGATION_RESULT_MAX_CHARS,
) -> str:
    return finalize_delegation_result(full_output, max_chars=max_chars).to_tool_text()


def finalize_delegation_result(
    full_output: str,
    *,
    target_agent_id: str | None = None,
    target_agent_name: str | None = None,
    capability: str | None = None,
    max_chars: int = DEFAULT_DELEGATION_RESULT_MAX_CHARS,
    run_id: str | None = None,
    parent_trace_id: str | None = None,
    parent_conversation_id: str | None = None,
    child_trace_id: str | None = None,
    child_session_id: str | None = None,
    structured: dict[str, Any] | None = None,
) -> SubAgentResult:
    """Create a typed result after output cleaning and size limiting."""
    cleaned_output = clean_sub_agent_output(full_output)
    if not cleaned_output.strip():
        if structured is not None:
            cleaned_output = json.dumps(structured, ensure_ascii=False)
        else:
            return SubAgentResult(
                status=SubAgentResultStatus.EMPTY,
                target_agent_id=target_agent_id,
                target_agent_name=target_agent_name,
                content=EMPTY_DELEGATION_RESULT_MESSAGE,
                capability=capability,
                run_id=run_id,
                parent_trace_id=parent_trace_id,
                parent_conversation_id=parent_conversation_id,
                child_trace_id=child_trace_id,
                child_session_id=child_session_id,
            )
    if len(cleaned_output) > max_chars:
        cleaned_output = (
            cleaned_output[:max_chars]
            + "\n\n...[因数据量过大，子代理回复已被系统自动截断]"
        )
        return SubAgentResult(
            status=SubAgentResultStatus.COMPLETED,
            target_agent_id=target_agent_id,
            target_agent_name=target_agent_name,
            content=cleaned_output,
            truncated=True,
            capability=capability,
            run_id=run_id,
            parent_trace_id=parent_trace_id,
            parent_conversation_id=parent_conversation_id,
            child_trace_id=child_trace_id,
            child_session_id=child_session_id,
            structured=structured,
        )
    return SubAgentResult(
        status=SubAgentResultStatus.COMPLETED,
        target_agent_id=target_agent_id,
        target_agent_name=target_agent_name,
        content=cleaned_output,
        capability=capability,
        run_id=run_id,
        parent_trace_id=parent_trace_id,
        parent_conversation_id=parent_conversation_id,
        child_trace_id=child_trace_id,
        child_session_id=child_session_id,
        structured=structured,
    )


async def _resolve_delegation_timeout_seconds() -> float:
    current_context = get_current_agent_context()
    snapshot = getattr(current_context, "agent_max_toolcall_timeout_seconds", None)
    if snapshot is not None:
        from app.services.ai.runtime.agentscope.tool_timeout import parse_agent_max_toolcall_timeout

        return parse_agent_max_toolcall_timeout(snapshot)

    from app.services.ai.runtime.agentscope.tool_timeout import load_agent_max_toolcall_timeout

    return await load_agent_max_toolcall_timeout()


async def _resolve_delegation_result_max_chars() -> int:
    try:
        from app.services.config_service import ConfigService

        raw = await ConfigService.get(
            "sub_agent_delegation_result_max_chars",
            str(DEFAULT_DELEGATION_RESULT_MAX_CHARS),
        )
        return max(500, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_DELEGATION_RESULT_MAX_CHARS


async def _consume_sub_agent_stream(
    sub_stream: Any,
    *,
    main_ctx: AgentContext,
    sub_display_name: str,
    structured_result: Dict[str, Any] | None = None,
    subagent_metadata: Dict[str, Any] | None = None,
) -> Tuple[str, str | None]:
    """消费子代理流，返回 (正文, 中断类型或 None)。"""
    full_output = ""
    interrupt_type: str | None = None

    async for chunk in sub_stream:
        chunk_type = str(chunk.get("type") or "")
        if structured_result is not None and isinstance(chunk.get("structured"), dict):
            structured_result["value"] = chunk["structured"]
        if chunk_type in INTERRUPT_SSE_TYPES:
            interrupt_type = chunk_type
            if chunk_type == "error":
                # 错误事件是终止信号，保留其真实内容供上层工具结果展示；
                # 不能继续把后续正文拼成一次成功委派的输出。
                full_output = _extract_delegation_text(chunk)
            logger.warning(
                "[Delegation] Sub-agent '%s' interrupted with %s during delegation",
                sub_display_name,
                chunk_type,
            )
            break

        if chunk_type in {"process_narration", "process_narration_commit"}:
            # 委派对主聊天只穿透工具日志，不转发子代理过程旁白。
            # 旁白若计入工具结果，会与随后的 promote/正文重复。
            continue

        if chunk_type == "retraction":
            # 兼容旧流：retraction 用新正文整体替换已积累内容。
            full_output = str(chunk.get("content") or "")
            continue

        text = _extract_delegation_text(chunk)
        if text:
            full_output += text
        elif chunk_type == "log" and main_ctx.event_queue:
            title = chunk.get("title", "")
            chunk["title"] = f"[{sub_display_name}] {title}"
            if subagent_metadata is not None:
                chunk["subagent"] = dict(subagent_metadata)
            await main_ctx.event_queue.put(chunk)
        elif chunk_type in {"browser_session", "browser_refresh"} and main_ctx.event_queue:
            sub_session_id = chunk.get("session_id")
            if sub_session_id:
                main_ctx.browser_session_id = str(sub_session_id)
            await main_ctx.event_queue.put(chunk)

    return full_output, interrupt_type


@tool
async def sub_agent_call(
    agent_name: str,
    query: str,
    max_depth: int | None = None,
    tool_filter: list[str] | None = None,
    output_schema: dict[str, Any] | None = None,
) -> str:
    """委派其他专有子智能体执行特定任务（如查数、查手册）。禁止未调用本工具就编造数据或流程。

    Args:
        agent_name: 目标子智能体的英文名称标识（如 data-agent，knowledge-base）
        query: 委派的具体任务指令或查询词
    """
    main_ctx = get_current_agent_context()
    if not main_ctx:
        return "错误：无法获取当前执行上下文，委派失败。"

    run_id = f"subrun_{uuid.uuid4().hex}"
    parent_trace_id = getattr(main_ctx, "trace_id", None)
    child_depth, depth_error = resolve_delegation_depth(
        main_ctx.delegation_depth,
        max_depth,
    )
    delegation_request = SubAgentRequest(
        target_agent_name=(agent_name or "").strip(),
        query=_normalize_delegation_query(query),
        caller_agent_id=str(main_ctx.agent_id),
        caller_agent_name=str(main_ctx.agent_name),
        delegation_depth=main_ctx.delegation_depth,
        approval_mode=str(
            (main_ctx.permission_options or {}).get("approval_mode") or "ask"
        ),
        run_id=run_id,
        parent_trace_id=parent_trace_id,
        parent_conversation_id=main_ctx.conversation_id,
        max_depth=max_depth,
        tool_filter=list(tool_filter) if tool_filter is not None else None,
        output_schema=output_schema,
    )

    # 1. 嵌套深度检查 (Depth Check)
    if depth_error:
        return SubAgentResult(
            status=SubAgentResultStatus.DEPTH_EXCEEDED,
            target_agent_name=(agent_name or "").strip() or None,
            content=(
                f"错误：检测到多级智能体嵌套委派调用（当前深度 {main_ctx.delegation_depth}），"
                "拒绝执行以防死循环。"
            ),
            run_id=run_id,
            parent_trace_id=parent_trace_id,
            parent_conversation_id=main_ctx.conversation_id,
            error_code=depth_error,
        ).to_tool_text()

    schema_error = validate_structured_schema(output_schema) if output_schema is not None else None
    if schema_error:
        return SubAgentResult(
            status=SubAgentResultStatus.INVALID_OUTPUT,
            target_agent_name=(agent_name or "").strip() or None,
            content=f"错误：output_schema 无效：{schema_error}。",
            run_id=run_id,
            parent_trace_id=parent_trace_id,
            parent_conversation_id=main_ctx.conversation_id,
            error_code="invalid_schema",
        ).to_tool_text()

    # 2. 校验目标智能体是否存在并加载配置
    target_config = None
    async with AsyncSessionLocal() as session:
        from app.models.agent import AIAgent
        from sqlalchemy import select
        # 强制只查询启用的系统内置智能体 (is_system = True)
        stmt = select(AIAgent).where(AIAgent.is_enabled == True, AIAgent.is_system == True)
        all_active_system = (await session.execute(stmt)).scalars().all()
        for a in all_active_system:
            if str(getattr(a, "id", "") or "") == str(main_ctx.agent_id) and _matches_requested_agent(a, agent_name):
                return SubAgentResult(
                    status=SubAgentResultStatus.FAILED,
                    target_agent_name=(agent_name or "").strip() or None,
                    content="错误：主智能体无法委派调用自身。",
                    run_id=run_id,
                    parent_trace_id=parent_trace_id,
                    parent_conversation_id=main_ctx.conversation_id,
                    error_code="self_delegation",
                ).to_tool_text()

        permitted_agents = await filter_delegable_system_agents(
            session,
            all_active_system,
            user_id=main_ctx.user_id,
            is_admin=main_ctx.is_admin,
            current_agent_id=main_ctx.agent_id,
        )
        delegable_agents = await resolve_runnable_delegable_system_agents(
            session,
            permitted_agents,
            user_id=main_ctx.user_id,
            is_admin=main_ctx.is_admin,
            current_agent_id=main_ctx.agent_id,
        )

        matched_agent = None

        for a in delegable_agents:
            if _matches_requested_agent(a, agent_name):
                matched_agent = a
                break

        if matched_agent:
            # 使用匹配到的正确的英文标识名重新加载配置
            target_config = await AgentManagerService.get_active_agent_config(session, agent_name=matched_agent.name)

            # [CR Fix] 阻止自委派 (matched_agent.id == main_ctx.agent_id)
            if target_config and str(target_config.agent_id) == str(main_ctx.agent_id):
                return SubAgentResult(
                    status=SubAgentResultStatus.FAILED,
                    target_agent_name=(agent_name or "").strip() or None,
                    content="错误：主智能体无法委派调用自身。",
                    run_id=run_id,
                    parent_trace_id=parent_trace_id,
                    parent_conversation_id=main_ctx.conversation_id,
                    error_code="self_delegation",
                ).to_tool_text()

        if not target_config:
            unavailable_match = next(
                (a for a in permitted_agents if _matches_requested_agent(a, agent_name)),
                None,
            )
            if unavailable_match is not None:
                return SubAgentResult(
                    status=SubAgentResultStatus.FAILED,
                    target_agent_id=str(getattr(unavailable_match, "id", "") or "") or None,
                    target_agent_name=str(getattr(unavailable_match, "display_name", None) or unavailable_match.name),
                    content=(
                        f"错误：智能体 `{unavailable_match.name}`（{unavailable_match.display_name or unavailable_match.name}）"
                        "当前尚未就绪，缺少可加载的发布版本或主类型所需的资源/工具。"
                        "请完成配置并发布后重试。"
                    ),
                    run_id=run_id,
                    parent_trace_id=parent_trace_id,
                    parent_conversation_id=main_ctx.conversation_id,
                    error_code="agent_not_ready",
                ).to_tool_text()
            # 无论如何都找不到，只列出当前用户可委派的候选，供模型自我纠错
            candidates = [
                f"`{a.name}` ({a.display_name or a.name})"
                for a in delegable_agents
            ]
            candidates_str = ", ".join(candidates)
            return SubAgentResult(
                status=SubAgentResultStatus.FAILED,
                target_agent_name=(agent_name or "").strip() or None,
                content=(
                    f"错误：未找到名为 '{agent_name}' 的启用系统智能体。请重新反思问题，并只能从以下当前已启用的系统内置候选智能体列表中选择正确的英文标识 (agent_name) 进行 `sub_agent_call` 调用：{candidates_str}"
                ),
                run_id=run_id,
                parent_trace_id=parent_trace_id,
                parent_conversation_id=main_ctx.conversation_id,
                error_code="agent_not_found",
            ).to_tool_text()

    # 4. 构造子代理独立上下文 (Sandbox Isolation)
    sub_history = [{"role": "user", "content": delegation_request.query}]
    sub_display_name = target_config.agent_display_name or target_config.agent_name or agent_name
    from app.services.ai.tools.registry import ToolRegistry

    available_tools = list(target_config.tools or [])
    available_tools.extend(ToolRegistry.get_system_implicit_tools())
    resolved_tool_filter, filter_error = resolve_delegation_tool_filter(
        available_tools,
        tool_filter,
    )
    if filter_error:
        return SubAgentResult(
            status=SubAgentResultStatus.PERMISSION_DENIED,
            target_agent_id=str(target_config.agent_id),
            target_agent_name=sub_display_name,
            content="错误：tool_filter 只能选择目标智能体已配置且可用的工具。",
            run_id=run_id,
            parent_trace_id=parent_trace_id,
            parent_conversation_id=main_ctx.conversation_id,
            error_code=filter_error,
        ).to_tool_text()

    repeat_error = _record_delegation_attempt(
        main_ctx,
        agent_name=target_config.agent_name or delegation_request.target_agent_name,
        display_name=sub_display_name,
        query=delegation_request.query,
    )
    if repeat_error:
        return repeat_error

    child_session_id = f"child_session_{run_id}"

    # [CR Fix] 继承主上下文已生效的知识库 ID，并与子智能体引擎自身配置的 IDs 合并
    from app.services.ai.knowledge_utils import merge_dataset_id_sources
    target_agent_dataset_ids = merge_dataset_id_sources(
        (target_config.engine_config or {}).get("dataset_ids")
    )
    effective_dataset_ids = list(set(main_ctx.dataset_ids or []))
    if target_agent_dataset_ids:
        effective_dataset_ids = merge_dataset_id_sources(
            effective_dataset_ids,
            target_agent_dataset_ids,
        )

    sub_engine_config = dict(target_config.engine_config or {})
    sub_engine_config["dataset_ids"] = effective_dataset_ids

    sub_permission_options = resolve_delegation_permission_options(main_ctx.permission_options)
    if resolved_tool_filter is not None:
        sub_permission_options["delegation_tool_filter"] = list(resolved_tool_filter)

    child_trace_id = f"sub_{uuid.uuid4().hex[:8]}"
    trace_start_index = len(main_ctx.trace_buffer or [])
    lifecycle_log_id = f"subagent_{run_id}"
    lifecycle_started_at = int(time.time() * 1000)
    subagent_metadata = {
        "display_name": sub_display_name,
        "agent_name": target_config.agent_name or agent_name,
        "run_id": run_id,
        "parent_trace_id": parent_trace_id,
        "child_trace_id": child_trace_id,
        "parent_conversation_id": main_ctx.conversation_id,
        "child_session_id": child_session_id,
        "tool_filter": list(resolved_tool_filter) if resolved_tool_filter is not None else None,
    }
    _put_subagent_lifecycle_log(
        main_ctx,
        log_id=lifecycle_log_id,
        metadata=subagent_metadata,
        status="pending",
        details="正在委派子代理处理请求。",
        started_at=lifecycle_started_at,
    )

    # 创建一个专属子上下文，隔离历史，但保留用户信息和 API Key 供子工具鉴权
    sub_ctx = AgentContext(
        agent_id=str(target_config.agent_id),
        agent_name=target_config.agent_name,
        dataset_ids=effective_dataset_ids,
        knowledge_dataset_ids=list(main_ctx.knowledge_dataset_ids or []),
        agent_dataset_ids=target_agent_dataset_ids,
        require_explicit_dataset=False,
        engine_type=target_config.engine_type or "LOCAL",
        engine_config=sub_engine_config,
        user_id=main_ctx.user_id,
        conversation_id=child_session_id,
        parent_conversation_id=main_ctx.conversation_id,
        child_session_id=child_session_id,
        is_admin=main_ctx.is_admin,
        api_key=main_ctx.api_key,
        user_dimensions=main_ctx.user_dimensions,
        delegation_depth=main_ctx.delegation_depth + 1,  # 深度加 1
        trace_id=child_trace_id,
        parent_trace_id=parent_trace_id,
        agent_max_toolcall_timeout_seconds=main_ctx.agent_max_toolcall_timeout_seconds,
        delegation_run_id=run_id,
        delegation_tool_filter=(
            list(resolved_tool_filter)
            if resolved_tool_filter is not None
            else None
        ),
        trace_buffer=main_ctx.trace_buffer,  # 共用 trace 收集物理步骤
        event_queue=main_ctx.event_queue,  # 传递 event_queue 用于流式穿透
        permission_options=sub_permission_options,
        # 共享主 runner 的事实取证账本，使子智能体工具调用产生的取证凭证回流到主链路。
        # 依赖顺序：主 runner._execute_raw 在调用本工具前必须已完成 ctx.grounding_evidence_ledger 初始化。
        grounding_evidence_ledger=main_ctx.grounding_evidence_ledger,
        published_download_urls=main_ctx.published_download_urls,
        skills_custom=bool(getattr(target_config, "skills_custom", False)),
        skills=list(getattr(target_config, "skills", None) or []),
    )
    # Pydantic may copy list fields while validating the model; explicitly keep
    # the same mutable registry so URLs issued by the child flow back to main.
    sub_ctx.published_download_urls = main_ctx.published_download_urls
    if main_ctx.grounding_evidence_ledger is None:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "[sub_agent_call] grounding_evidence_ledger is None when delegating to sub-agent '%s'. "
            "Evidence receipts from the sub-agent will NOT be recorded in the main runner's ledger. "
            "Ensure the main runner has completed _execute_raw initialization before delegation.",
            agent_name,
        )

    # [CR Fix] 从 main_ctx 还原 user_info 并传给 dispatch，避免 session lock 和维度缺失
    user_info = {
        "user_id": main_ctx.user_id,
        "role": "admin" if main_ctx.is_admin else "user",
        "api_key": main_ctx.api_key,
        "user_name": main_ctx.user_dimensions.get("user_name") if main_ctx.user_dimensions else None,
        "real_name": main_ctx.user_dimensions.get("real_name") if main_ctx.user_dimensions else None,
        "dept_code": main_ctx.user_dimensions.get("dept_code") if main_ctx.user_dimensions else None,
        "org_path": main_ctx.user_dimensions.get("org_path") if main_ctx.user_dimensions else None,
        "extra_data": main_ctx.user_dimensions.get("extra_data") if main_ctx.user_dimensions else None,
    } if main_ctx else None

    delegation_timeout = await _resolve_delegation_timeout_seconds()
    result_max_chars = await _resolve_delegation_result_max_chars()
    delegation_request = replace(
        delegation_request,
        capability=next(
            (
                str(capability)
                for capability in (getattr(target_config, "capabilities", None) or [])
                if str(capability) in {"data_query", "knowledge_base"}
            ),
            None,
        ),
        timeout_seconds=delegation_timeout,
        run_id=run_id,
        parent_trace_id=parent_trace_id,
        parent_conversation_id=main_ctx.conversation_id,
        child_session_id=child_session_id,
        max_depth=max_depth,
        tool_filter=list(resolved_tool_filter) if resolved_tool_filter is not None else None,
        output_schema=output_schema,
    )

    sub_executor = await _dispatch_sub_agent_executor(
        target_config,
        delegation_request.query,
        sub_history,
        trace_id=child_trace_id,
        trace_buffer=main_ctx.trace_buffer,
        permission_options=sub_permission_options,
        user_info=user_info,
        conversation_id=child_session_id,
        turn_decision=TurnDecision.for_direct_agent_selection(target_config),
    )

    # 临时切换到子 Context 运行
    original_ctx = get_current_agent_context()
    set_agent_context(sub_ctx)

    full_output = ""
    structured_holder: Dict[str, Any] = {}
    sub_stream = None
    interrupt_type: str | None = None

    try:
        sub_stream = sub_executor.execute(sub_history)

        async def consume_stream():
            nonlocal full_output, interrupt_type
            full_output, interrupt_type = await _consume_sub_agent_stream(
                sub_stream,
                main_ctx=main_ctx,
                sub_display_name=sub_display_name,
                structured_result=structured_holder,
                subagent_metadata=subagent_metadata,
            )

        await asyncio.wait_for(consume_stream(), timeout=delegation_timeout)

    except asyncio.TimeoutError:
        timeout_message = (
            f"错误：调用子智能体 '{sub_display_name}' 响应超时（已达 {int(delegation_timeout)} 秒限制）。"
        )
        logger.warning(
            "[Delegation] Sub-agent '%s' timed out after %.0f seconds.",
            agent_name,
            delegation_timeout,
        )
        if main_ctx.event_queue:
            _put_subagent_lifecycle_log(
                main_ctx,
                log_id=lifecycle_log_id,
                metadata=subagent_metadata,
                status="error",
                details=f"子智能体未能在 {int(delegation_timeout)} 秒内返回数据，强制中断并释放资源。",
                started_at=lifecycle_started_at,
            )
        _annotate_subagent_trace(
            main_ctx.trace_buffer or [],
            trace_start_index,
            metadata=subagent_metadata,
            stop_reason=SubAgentStopReason.TIMEOUT.value,
        )
        return SubAgentResult(
            status=SubAgentResultStatus.TIMEOUT,
            target_agent_id=str(target_config.agent_id),
            target_agent_name=sub_display_name,
            content=timeout_message,
            error_code="timeout",
            capability=delegation_request.capability,
            run_id=run_id,
            parent_trace_id=parent_trace_id,
            parent_conversation_id=main_ctx.conversation_id,
            child_trace_id=child_trace_id,
            child_session_id=child_session_id,
        ).to_tool_text()
    except asyncio.CancelledError:
        _put_subagent_lifecycle_log(
            main_ctx,
            log_id=lifecycle_log_id,
            metadata=subagent_metadata,
            status="error",
            details="子代理执行已取消。",
            started_at=lifecycle_started_at,
        )
        _annotate_subagent_trace(
            main_ctx.trace_buffer or [],
            trace_start_index,
            metadata=subagent_metadata,
            stop_reason=SubAgentStopReason.CANCELLED.value,
        )
        raise
    except Exception as e:
        logger.error(f"[Delegation] Error executing sub-agent '{agent_name}': {e}", exc_info=True)
        _put_subagent_lifecycle_log(
            main_ctx,
            log_id=lifecycle_log_id,
            metadata=subagent_metadata,
            status="error",
            details=f"子智能体执行失败：{str(e)}",
            started_at=lifecycle_started_at,
        )
        _annotate_subagent_trace(
            main_ctx.trace_buffer or [],
            trace_start_index,
            metadata=subagent_metadata,
            stop_reason=SubAgentStopReason.FAILED.value,
        )
        return SubAgentResult(
            status=SubAgentResultStatus.FAILED,
            target_agent_id=str(target_config.agent_id),
            target_agent_name=sub_display_name,
            content=f"错误：调用子智能体 '{sub_display_name}' 时发生异常：{str(e)}",
            error_code="execution_error",
            capability=delegation_request.capability,
            run_id=run_id,
            parent_trace_id=parent_trace_id,
            parent_conversation_id=main_ctx.conversation_id,
            child_trace_id=child_trace_id,
            child_session_id=child_session_id,
        ).to_tool_text()
    finally:
        if sub_stream and inspect.isasyncgen(sub_stream):
            try:
                await sub_stream.aclose()
            except Exception as close_err:
                logger.warning(f"Failed to close sub-agent generator stream: {close_err}")
        set_agent_context(original_ctx)

    if interrupt_type:
        interrupt_status = (
            SubAgentResultStatus.PERMISSION_DENIED
            if interrupt_type == "permission_required"
            else SubAgentResultStatus.INTERRUPTED
        )
        interrupt_reason = (
            SubAgentStopReason.PERMISSION_DENIED
            if interrupt_type == "permission_required"
            else SubAgentStopReason.INTERRUPTED
        )
        _annotate_subagent_trace(
            main_ctx.trace_buffer or [],
            trace_start_index,
            metadata=subagent_metadata,
            stop_reason=interrupt_reason.value,
        )
        if interrupt_type == "error" and full_output.strip():
            interrupt_message = full_output.strip()
            if not interrupt_message.startswith(("错误：", "错误:")):
                interrupt_message = f"错误：{interrupt_message}"
        else:
            interrupt_message = DELEGATION_INTERRUPT_MESSAGES.get(
                interrupt_type,
                f"子智能体执行被中断（{interrupt_type}）。",
            )
        _put_subagent_lifecycle_log(
            main_ctx,
            log_id=lifecycle_log_id,
            metadata=subagent_metadata,
            status="error",
            details=interrupt_message,
            started_at=lifecycle_started_at,
        )
        return SubAgentResult(
            status=interrupt_status,
            target_agent_id=str(target_config.agent_id),
            target_agent_name=sub_display_name,
            content=interrupt_message,
            error_code="stream_error" if interrupt_type == "error" else "interrupted",
            interrupt_type=interrupt_type,
            capability=delegation_request.capability,
            run_id=run_id,
            parent_trace_id=parent_trace_id,
            parent_conversation_id=main_ctx.conversation_id,
            child_trace_id=child_trace_id,
            child_session_id=child_session_id,
            stop_reason=interrupt_reason,
        ).to_tool_text()

    structured = structured_holder.get("value")
    if output_schema is not None and structured is None:
        structured = _structured_from_text(clean_sub_agent_output(full_output))
    if output_schema is not None:
        valid, reason = validate_structured_output(structured, output_schema)
        if not valid:
            _annotate_subagent_trace(
                main_ctx.trace_buffer or [],
                trace_start_index,
                metadata=subagent_metadata,
                stop_reason=SubAgentStopReason.INVALID_OUTPUT.value,
            )
            _put_subagent_lifecycle_log(
                main_ctx,
                log_id=lifecycle_log_id,
                metadata=subagent_metadata,
                status="error",
                details=f"子智能体结构化输出不符合约定：{reason}",
                started_at=lifecycle_started_at,
            )
            return SubAgentResult(
                status=SubAgentResultStatus.INVALID_OUTPUT,
                target_agent_id=str(target_config.agent_id),
                target_agent_name=sub_display_name,
                content=f"错误：子智能体结构化输出不符合约定：{reason}",
                error_code="invalid_output",
                capability=delegation_request.capability,
                run_id=run_id,
                parent_trace_id=parent_trace_id,
                parent_conversation_id=main_ctx.conversation_id,
                child_trace_id=child_trace_id,
                child_session_id=child_session_id,
                stop_reason=SubAgentStopReason.INVALID_OUTPUT,
            ).to_tool_text()

    result = finalize_delegation_result(
        full_output,
        target_agent_id=str(target_config.agent_id),
        target_agent_name=sub_display_name,
        max_chars=result_max_chars,
        capability=delegation_request.capability,
        run_id=run_id,
        parent_trace_id=parent_trace_id,
        parent_conversation_id=main_ctx.conversation_id,
        child_trace_id=child_trace_id,
        child_session_id=child_session_id,
        structured=structured if isinstance(structured, dict) else None,
    )
    _annotate_subagent_trace(
        main_ctx.trace_buffer or [],
        trace_start_index,
        metadata=subagent_metadata,
        stop_reason=result.stop_reason.value if result.stop_reason else SubAgentStopReason.COMPLETED.value,
    )
    _put_subagent_lifecycle_log(
        main_ctx,
        log_id=lifecycle_log_id,
        metadata={
            **subagent_metadata,
            "stop_reason": result.stop_reason.value if result.stop_reason else SubAgentStopReason.COMPLETED.value,
        },
        status="success",
        details="子代理已完成委派任务。",
        started_at=lifecycle_started_at,
        execution_time_ms=int(time.time() * 1000) - lifecycle_started_at,
    )
    logger.info(
        "[Delegation] target=%s status=%s chars=%s truncated=%s",
        sub_display_name,
        result.status.value,
        len(result.content),
        result.truncated,
    )
    return result.to_tool_text()


class BatchSubAgentCall(BaseModel):
    """One independent item in a batch delegation request."""

    agent_name: str = Field(description="目标子智能体名称")
    query: str = Field(description="交给子智能体的独立任务")
    max_depth: int | None = Field(default=None, description="可选的最大委派深度")
    tool_filter: list[str] | None = Field(default=None, description="可选的子代理工具白名单")
    output_schema: dict[str, Any] | None = Field(default=None, description="可选的结构化输出约束")

    @field_validator("agent_name", "query")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("agent_name/query 不能为空")
        return normalized


class BatchSubAgentArgs(BaseModel):
    """Arguments accepted by the parallel delegation tool."""

    calls: list[BatchSubAgentCall] = Field(
        description="彼此独立、可以并行执行的子代理任务，最多 4 个"
    )

    @field_validator("calls")
    @classmethod
    def _bounded_calls(cls, value: list[BatchSubAgentCall]) -> list[BatchSubAgentCall]:
        if not value:
            raise ValueError("calls 不能为空")
        if len(value) > MAX_BATCH_DELEGATION_CALLS:
            raise ValueError(f"calls 最多 {MAX_BATCH_DELEGATION_CALLS} 个")
        return value


class BatchSubAgentTool(BaseTool):
    """Run independent one-shot delegations concurrently and return ordered results."""

    name = "sub_agent_batch_call"
    description = (
        "并行委派 1-4 个彼此独立的子智能体任务。"
        "仅用于任务之间互不依赖的场景；有先后依赖时逐次调用 sub_agent_call。"
        "结果按 calls 输入顺序返回，单个任务失败不会丢弃其他结果。"
    )
    args_schema = BatchSubAgentArgs

    async def ainvoke(self, arguments: dict[str, Any] | None = None) -> str:
        try:
            args = BatchSubAgentArgs.model_validate(arguments or {})
        except Exception as exc:
            return json.dumps(
                {
                    "status": "error",
                    "error": f"批量委派入参无效: {exc}",
                },
                ensure_ascii=False,
            )

        main_ctx = get_current_agent_context()
        if not main_ctx:
            return json.dumps(
                {"status": "error", "error": "错误：无法获取当前执行上下文，批量委派失败。"},
                ensure_ascii=False,
            )

        async def run_one(index: int, call: BatchSubAgentCall) -> dict[str, Any]:
            task_ctx = main_ctx.model_copy(deep=False, update={"trace_buffer": []})
            original_ctx = get_current_agent_context()
            set_agent_context(task_ctx)
            try:
                content = await sub_agent_call.func(
                    **call.model_dump(exclude_none=True)
                )
                return {
                    "index": index,
                    "agent_name": call.agent_name,
                    "status": _batch_result_status(str(content or "")),
                    "content": str(content or ""),
                    "trace_buffer": task_ctx.trace_buffer,
                }
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(
                    "[Delegation] Batch item failed: agent=%s index=%s",
                    call.agent_name,
                    index,
                )
                return {
                    "index": index,
                    "agent_name": call.agent_name,
                    "status": "failed",
                    "content": f"错误：批量委派子智能体 '{call.agent_name}' 失败：{exc}",
                    "trace_buffer": task_ctx.trace_buffer,
                }
            finally:
                set_agent_context(original_ctx)

        tasks = [
            asyncio.create_task(run_one(index, call))
            for index, call in enumerate(args.calls)
        ]
        try:
            raw_results = await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        results: list[dict[str, Any]] = []
        for item in raw_results:
            main_ctx.trace_buffer.extend(item.pop("trace_buffer", []))
            results.append(item)

        failed_count = sum(item["status"] != "completed" for item in results)
        return json.dumps(
            {
                "status": "completed",
                "results": results,
                "completed_count": len(results) - failed_count,
                "failed_count": failed_count,
            },
            ensure_ascii=False,
        )


sub_agent_batch_call = BatchSubAgentTool()


async def _dispatch_sub_agent_executor(
    target_config: Any,
    query: str,
    sub_history: List[Dict[str, str]],
    *,
    trace_id: str,
    trace_buffer: List[Any],
    permission_options: Dict[str, Any],
    user_info: Dict[str, Any] | None,
    conversation_id: str | None,
    turn_decision: TurnDecision,
) -> Any:
    from app.services.ai.dispatcher import AgentDispatcher

    return await AgentDispatcher.dispatch(
        target_config,
        query,
        sub_history,
        trace_id=trace_id,
        trace_buffer=trace_buffer,
        debug_options=None,
        permission_options=permission_options,
        user_info=user_info,
        conversation_id=conversation_id,
        turn_decision=turn_decision,
    )
