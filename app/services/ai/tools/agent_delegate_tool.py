import logging
import asyncio
import re
import uuid
import inspect
from typing import Optional, Dict, Any, List, Tuple, Iterable
from app.services.ai.tools.tool_compat import tool
from app.core.context import get_current_agent_context, AgentContext, set_agent_context
from app.core.orm import AsyncSessionLocal
from app.services.ai.agent_manager import AgentManagerService
from app.services.permission_service import PermissionService

logger = logging.getLogger(__name__)

DEFAULT_DELEGATION_TIMEOUT_SECONDS = 120.0
DEFAULT_DELEGATION_RESULT_MAX_CHARS = 8000
MAX_DELEGATION_CALLS_PER_AGENT = 2

INTERRUPT_SSE_TYPES = frozenset({"permission_required", "external_execution_required"})

EMPTY_DELEGATION_RESULT_MESSAGE = (
    "子智能体已执行完成，但未产生可交付正文（可能仅有内部进度日志或工具中间结果）。"
    "请勿使用相同参数重复委派；请根据上述情况向用户说明，或建议其直接打开对应子智能体对话。"
)

DELEGATION_INTERRUPT_MESSAGES = {
    "permission_required": (
        "错误：子智能体执行过程中需要用户确认工具权限，当前委派模式无法在子流程中完成确认。"
        "请直接打开对应子智能体对话，或联系管理员调整工具自动执行策略。"
    ),
    "external_execution_required": (
        "错误：子智能体需要外部执行确认，当前委派模式不支持。请直接打开对应子智能体对话。"
    ),
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
    from app.services.ai.agent_readiness import evaluate_agent_readiness
    from app.services.ai.agent_types import resolve_agent_type

    permitted = await filter_delegable_system_agents(
        session,
        agents,
        user_id=user_id,
        is_admin=is_admin,
        current_agent_id=current_agent_id,
    )
    runnable: List[Any] = []
    for agent in permitted:
        config = await AgentManagerService.get_active_agent_config(
            session,
            agent_id=str(getattr(agent, "id", "") or ""),
        )
        if not config:
            continue
        readiness = evaluate_agent_readiness(
            agent_type=resolve_agent_type(agent),
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
    cleaned_output = clean_sub_agent_output(full_output)
    if not cleaned_output.strip():
        return EMPTY_DELEGATION_RESULT_MESSAGE
    if len(cleaned_output) > max_chars:
        cleaned_output = (
            cleaned_output[:max_chars]
            + "\n\n...[因数据量过大，子代理回复已被系统自动截断]"
        )
    return cleaned_output


async def _resolve_delegation_timeout_seconds() -> float:
    try:
        from app.services.config_service import ConfigService

        raw = await ConfigService.get(
            "sub_agent_delegation_timeout_seconds",
            str(int(DEFAULT_DELEGATION_TIMEOUT_SECONDS)),
        )
        return max(30.0, float(raw))
    except (TypeError, ValueError):
        return DEFAULT_DELEGATION_TIMEOUT_SECONDS


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
) -> Tuple[str, str | None]:
    """消费子代理流，返回 (正文, 中断类型或 None)。"""
    full_output = ""
    interrupt_type: str | None = None

    async for chunk in sub_stream:
        chunk_type = str(chunk.get("type") or "")
        if chunk_type in INTERRUPT_SSE_TYPES:
            interrupt_type = chunk_type
            logger.warning(
                "[Delegation] Sub-agent '%s' interrupted with %s during delegation",
                sub_display_name,
                chunk_type,
            )
            break

        text = _extract_delegation_text(chunk)
        if text:
            full_output += text
        elif chunk_type == "log" and main_ctx.event_queue:
            title = chunk.get("title", "")
            chunk["title"] = f"[{sub_display_name}] {title}"
            await main_ctx.event_queue.put(chunk)

    return full_output, interrupt_type


@tool
async def sub_agent_call(agent_name: str, query: str) -> str:
    """委派其他专有子智能体执行特定任务（如查数、查手册）。禁止未调用本工具就编造数据或流程。

    Args:
        agent_name: 目标子智能体的英文名称标识（如 data-agent，knowledge-base）
        query: 委派的具体任务指令或查询词
    """
    main_ctx = get_current_agent_context()
    if not main_ctx:
        return "错误：无法获取当前执行上下文，委派失败。"

    # 1. 嵌套深度检查 (Depth Check)
    if main_ctx.delegation_depth >= 1:
        return f"错误：检测到多级智能体嵌套委派调用（当前深度 {main_ctx.delegation_depth}），拒绝执行以防死循环。"

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
                return "错误：主智能体无法委派调用自身。"

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
                return "错误：主智能体无法委派调用自身。"

        if not target_config:
            unavailable_match = next(
                (a for a in permitted_agents if _matches_requested_agent(a, agent_name)),
                None,
            )
            if unavailable_match is not None:
                return (
                    f"错误：智能体 `{unavailable_match.name}`（{unavailable_match.display_name or unavailable_match.name}）"
                    "当前尚未就绪，缺少可加载的发布版本或主类型所需的资源/工具。"
                    "请完成配置并发布后重试。"
                )
            # 无论如何都找不到，只列出当前用户可委派的候选，供模型自我纠错
            candidates = [
                f"`{a.name}` ({a.display_name or a.name})"
                for a in delegable_agents
            ]
            candidates_str = ", ".join(candidates)
            return (
                f"错误：未找到名为 '{agent_name}' 的启用系统智能体。请重新反思问题，并只能从以下当前已启用的系统内置候选智能体列表中选择正确的英文标识 (agent_name) 进行 `sub_agent_call` 调用：{candidates_str}"
            )

    # 4. 构造子代理独立上下文 (Sandbox Isolation)
    sub_history = [{"role": "user", "content": query}]
    sub_display_name = target_config.agent_display_name or target_config.agent_name or agent_name
    repeat_error = _record_delegation_attempt(
        main_ctx,
        agent_name=target_config.agent_name or agent_name,
        display_name=sub_display_name,
        query=query,
    )
    if repeat_error:
        return repeat_error

    # [CR Fix] 继承主上下文已生效的知识库 ID，并与子智能体引擎自身配置的 IDs 合并
    effective_dataset_ids = list(set(main_ctx.dataset_ids or []))
    if target_config.engine_config and target_config.engine_config.get("dataset_ids"):
        from app.services.ai.knowledge_utils import merge_dataset_id_sources
        effective_dataset_ids = merge_dataset_id_sources(
            effective_dataset_ids,
            target_config.engine_config.get("dataset_ids")
        )

    sub_engine_config = dict(target_config.engine_config or {})
    sub_engine_config["dataset_ids"] = effective_dataset_ids

    sub_permission_options = resolve_delegation_permission_options(main_ctx.permission_options)

    # 创建一个专属子上下文，隔离历史，但保留用户信息和 API Key 供子工具鉴权
    sub_ctx = AgentContext(
        agent_id=str(target_config.agent_id),
        agent_name=target_config.agent_name,
        dataset_ids=effective_dataset_ids,
        knowledge_dataset_ids=list(main_ctx.knowledge_dataset_ids or []),
        require_explicit_dataset=False,
        engine_type=target_config.engine_type or "LOCAL",
        engine_config=sub_engine_config,
        user_id=main_ctx.user_id,
        conversation_id=main_ctx.conversation_id,
        is_admin=main_ctx.is_admin,
        api_key=main_ctx.api_key,
        user_dimensions=main_ctx.user_dimensions,
        delegation_depth=main_ctx.delegation_depth + 1,  # 深度加 1
        trace_buffer=main_ctx.trace_buffer,  # 共用 trace 收集物理步骤
        event_queue=main_ctx.event_queue,  # 传递 event_queue 用于流式穿透
        permission_options=sub_permission_options,
        # 共享主 runner 的事实取证账本，使子智能体工具调用产生的取证凭证回流到主链路。
        # 依赖顺序：主 runner._execute_raw 在调用本工具前必须已完成 ctx.grounding_evidence_ledger 初始化。
        grounding_evidence_ledger=main_ctx.grounding_evidence_ledger,
        skills_custom=bool(getattr(target_config, "skills_custom", False)),
        skills=list(getattr(target_config, "skills", None) or []),
    )
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

    sub_executor = await _dispatch_sub_agent_executor(
        target_config,
        query,
        sub_history,
        trace_id=f"sub_{uuid.uuid4().hex[:8]}",
        trace_buffer=main_ctx.trace_buffer,
        permission_options=sub_permission_options,
        user_info=user_info,
        conversation_id=main_ctx.conversation_id,
    )

    # 临时切换到子 Context 运行
    original_ctx = get_current_agent_context()
    set_agent_context(sub_ctx)

    full_output = ""
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
            )

        await asyncio.wait_for(consume_stream(), timeout=delegation_timeout)

    except asyncio.TimeoutError:
        logger.warning(
            "[Delegation] Sub-agent '%s' timed out after %.0f seconds.",
            agent_name,
            delegation_timeout,
        )
        if main_ctx.event_queue:
            await main_ctx.event_queue.put({
                "type": "log",
                "title": f"[{sub_display_name}] 调用超时",
                "details": f"子智能体未能在 {int(delegation_timeout)} 秒内返回数据，强制中断并释放资源。",
                "status": "error"
            })
        return f"错误：调用子智能体 '{sub_display_name}' 响应超时（已达 {int(delegation_timeout)} 秒限制）。"
    except Exception as e:
        logger.error(f"[Delegation] Error executing sub-agent '{agent_name}': {e}", exc_info=True)
        return f"错误：调用子智能体 '{sub_display_name}' 时发生异常：{str(e)}"
    finally:
        if sub_stream and inspect.isasyncgen(sub_stream):
            try:
                await sub_stream.aclose()
            except Exception as close_err:
                logger.warning(f"Failed to close sub-agent generator stream: {close_err}")
        set_agent_context(original_ctx)

    if interrupt_type:
        return DELEGATION_INTERRUPT_MESSAGES.get(
            interrupt_type,
            f"错误：子智能体 '{sub_display_name}' 执行被中断（{interrupt_type}），委派未完成。",
        )

    return finalize_delegation_output(full_output, max_chars=result_max_chars)


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
    )
