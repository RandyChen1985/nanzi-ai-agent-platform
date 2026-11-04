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
from app.core.context import (
    get_current_agent_context,
    AgentContext,
    execution_options_context,
    set_agent_context,
    set_execution_options_context,
)
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
    """把平台侧工具名换算为模型侧调用名（内置别名 + MCP 别名）。

    委派白名单最终要与运行时 ``RuntimeToolSpec.name`` 比较，而 MCP 工具在运行时
    注册的是别名；这里必须用同一口径，否则子智能体的 MCP 工具会被静默过滤。
    """
    from app.services.ai.tools.model_tool_name import to_model_tool_name
    from app.services.ai.tools.registry import AGENTSCOPE_BUILTIN_TOOL_ALIASES

    model_name = to_model_tool_name(name)
    return AGENTSCOPE_BUILTIN_TOOL_ALIASES.get(model_name, model_name)


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


async def load_delegable_system_agent_candidates(session: Any) -> List[Any]:
    """加载「未配置白名单时」的可委派候选：已启用的系统级智能体。

    配置了 ``delegable_agent_ids`` 的智能体不再走这里——它按名单里的 ID 直接取行
    （见 ``load_agents_by_delegation_ids``），从而能把用户自建智能体也纳入候选。
    无论哪条路，最终都汇聚到 ``resolve_effective_delegable_agents`` 这一个出口。

    不要在调用方各自复用 ``AgentManagerService.list_agents``：那是 **UI 可见性**口径，
    按 ``created_by`` 归属过滤，而工具/后台链路普遍拿不到 user，会退化成
    「created_by 必须为空字符串」，把全部系统级智能体（``created_by='admin'``）
    整批剃成空列表。
    """
    from sqlalchemy import select

    from app.models.agent import AIAgent

    stmt = select(AIAgent).where(
        AIAgent.is_enabled == True,
        AIAgent.is_system == True,
    )
    return list((await session.execute(stmt)).scalars().all())


DELEGATION_TOOL_NAME = "sub_agent_call"
DELEGATION_BATCH_TOOL_NAME = "sub_agent_batch_call"

# 名单只有一份（见 ``resolve_delegation_allowlist``），批量委派内部复用
# ``sub_agent_call`` 时不再需要「当前是谁在调」的 ContextVar——那套机制存在的前提
# 是两个工具各有一份名单，现在前提没了，留着只会让后来者以为还能分开配。


def _delegable_ids_of_tool_config(item: Any) -> tuple[bool, Optional[List[str]]]:
    """从单个工具配置项里读调度白名单，返回 ``(是否显式配置, 名单)``。

    配置项有三种历史形态：``str``（老格式只存名字）、``dict``（版本表 JSON）、
    ``ToolConfigItem``（运行时）。三种都要认，否则老智能会读成「未配置」。
    """
    if item is None:
        return False, None
    if isinstance(item, dict):
        raw = item.get("delegable_agent_ids")
    else:
        raw = getattr(item, "delegable_agent_ids", None)
    if raw is None:
        return False, None
    cleaned: List[str] = []
    seen: set[str] = set()
    for value in raw:
        agent_id = str(value or "").strip()
        if agent_id and agent_id not in seen:
            seen.add(agent_id)
            cleaned.append(agent_id)
    return True, cleaned


def _configured_tool_name(item: Any) -> str:
    """取配置项的工具名，兼容 str / dict / ToolConfigItem 三种形态。"""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return str(item.get("name") or "")
    return str(getattr(item, "name", "") or "")


def ensure_delegation_tool_pair(tools: Any, *, as_dict: bool = False) -> List[Any]:
    """让两个委派工具在配置里始终成对出现，并清掉 batch 上遗留的名单字段。

    页面只暴露 ``sub_agent_call`` 一个开关：勾选它 = 同时拥有串行与并行委派。
    但配置层必须成对——只存 ``sub_agent_call`` 的话，模型就没有
    ``sub_agent_batch_call`` 这个入口，「并发汇总」场景直接不可用；反过来只存
    batch 的老版本，串行委派又会消失。

    历史版本可能只勾了其中一个（甚至两个都勾、各带一份名单），这里统一补齐/清理：
    名单只认 ``sub_agent_call`` 那份，batch 上的 ``delegable_agent_ids`` 丢弃。

    ``as_dict=True`` 时补出来的项写成 ``{"name": ...}``（落库形态）；否则补字符串，
    与 ``get_tools`` 的输入约定一致。
    """
    items = list(tools or [])
    names = {_configured_tool_name(item) for item in items}
    if not names & {DELEGATION_TOOL_NAME, DELEGATION_BATCH_TOOL_NAME}:
        return items

    result: List[Any] = []
    for item in items:
        if isinstance(item, dict) and _configured_tool_name(item) == DELEGATION_BATCH_TOOL_NAME:
            item = {k: v for k, v in item.items() if k != "delegable_agent_ids"}
        result.append(item)

    for missing in (DELEGATION_TOOL_NAME, DELEGATION_BATCH_TOOL_NAME):
        if missing not in names:
            result.append({"name": missing} if as_dict else missing)
    return result


def has_delegation_tool(agent_config: Any) -> bool:
    """当前智能体到底有没有委派能力：配置里勾了委派工具，或主助手运行时隐式挂载。

    ``list_available_agents`` 是**隐式工具**，任何智能体都能调。它若不看这一层，
    就会出现「列出一堆可委派智能体 → 用户挑一个 → 模型说自己没工具」的自相矛盾：
    目录暗示了能力，运行时却没有。所有「能不能委派」的判断都应走这里。
    """
    if agent_config is None:
        return False

    for item in getattr(agent_config, "tools", None) or []:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = item.get("name")
        else:
            name = getattr(item, "name", None)
        if str(name or "").strip() in (DELEGATION_TOOL_NAME, DELEGATION_BATCH_TOOL_NAME):
            return True

    # 主助手（通用对话兜底 Agent）由 AssistantAgentRunner 在运行时隐式挂载委派工具，
    # 它的 tools 配置里通常没有这一项，不能因此判定为「没有委派能力」。
    try:
        from app.services.ai.skill_resolver import is_main_general_agent

        return is_main_general_agent(agent_config)
    except Exception:
        return False


def resolve_delegation_allowlist(
    configured_tools: Iterable[Any] | None,
    *,
    tool_name: str = DELEGATION_TOOL_NAME,
) -> tuple[bool, Optional[List[str]]]:
    """解析当前智能体的调度白名单。

    语义（见 docs/superpowers/specs/2026-11-03-sub-agent-delegation-allowlist-design.md）：
    - ``(False, None)``：未配置 → 系统级全量、自建不可委派（与历史行为一致）
    - ``(True, [])``：显式清空 → 任何智能体都不许委派
    - ``(True, [...])``：只许名单内的

    **两个委派工具共用同一份名单**：``sub_agent_call`` 与 ``sub_agent_batch_call``
    是同一件事的串行/并行两种形态，配置入口也只有一个。早先允许各配一份，结果是
    「只配 batch」时 batch 收窄、``sub_agent_call`` 反而按未配置走系统级全量——
    同一份名单在两个工具上语义相反：既让批量名单形同虚设（绕过白名单），也让
    「团里配好了成员却调不到」无解（自建成员只有名单生效才进候选）。

    现在 ``sub_agent_call`` 是唯一事实源；``sub_agent_batch_call`` 上历史遗留的
    ``delegable_agent_ids`` 只作为兜底读取，保证存量配置不丢，保存时由前端清掉。
    ``tool_name`` 参数保留只为兼容既有调用方，不再影响解析结果。
    """
    for candidate in (DELEGATION_TOOL_NAME, DELEGATION_BATCH_TOOL_NAME):
        own: Any = None
        for item in configured_tools or []:
            if _configured_tool_name(item) == candidate:
                own = item
                break
        configured, ids = _delegable_ids_of_tool_config(own)
        if configured:
            return True, ids
    return False, None


async def load_agents_by_delegation_ids(session: Any, agent_ids: Iterable[str]) -> List[Any]:
    """按白名单里的 ID 直接取智能体行（含用户自建，不做 is_system 过滤）。"""
    from sqlalchemy import select

    from app.models.agent import AIAgent

    ids = [str(a or "").strip() for a in agent_ids or [] if str(a or "").strip()]
    if not ids:
        return []
    stmt = select(AIAgent).where(AIAgent.id.in_(ids))
    return list((await session.execute(stmt)).scalars().all())


async def load_accessible_personal_agents(
    session: Any,
    *,
    user_name: str | None,
    is_admin: bool = False,
) -> List[Any]:
    """用户自建的、已启用的智能体（管理员看全部，普通用户只看本人创建的）。"""
    from sqlalchemy import select

    from app.models.agent import AIAgent

    conditions = [
        AIAgent.is_system.is_(False),
        AIAgent.is_enabled.is_(True),
    ]
    if not is_admin:
        if not (user_name or "").strip():
            return []
        conditions.append(AIAgent.created_by == user_name)

    stmt = select(AIAgent).where(*conditions)
    return list((await session.execute(stmt)).scalars().all())


async def filter_delegable_agents(
    session: Any,
    agents: Iterable[Any],
    *,
    user_id: int | str | None,
    is_admin: bool,
    user_name: str | None,
    current_agent_id: str | None,
    allow_personal: bool = False,
) -> List[Any]:
    """权限层过滤。

    系统级要求 ``agent:<id>`` 显式授权（走 PermissionService）；用户自建要求
    ``created_by`` 是本人。``allow_personal=False`` 时自建智能体一律不进结果——
    这是未配置白名单时的历史行为，自建只有被显式写进白名单才「开通」。

    刻意**不**在这里排除 ``is_expert_team_lead``（专家团主专家）：智能委派（routingMode
    为 auto 时由主助手发起）面对的是「一个智能体」，团主被委派进来后因
    ``MAX_DELEGATION_DEPTH = 1`` 无法再调度自己的成员，此时他就是一个普通专家——
    这是设计语义，不是漏洞。要用团的完整能力得由用户在输入框选定团主（深度 0 起步）；
    白名单里把团主选为子专家也是合法用法（它在别处就是普通专才），此时提示词会按
    深度改口为「只能用个人能力」，不会推它去撞 depth_exceeded。因此**别在这里补一道
    排除**：那会把「未配置白名单 = 系统级全量」和「显式白名单」两个口径同时改窄，
    且没有任何界面提示。
    """
    delegable: List[Any] = []
    for agent in agents or []:
        if not getattr(agent, "is_enabled", False):
            continue
        is_system = bool(getattr(agent, "is_system", False))
        if not is_system and not allow_personal:
            continue
        agent_id = str(getattr(agent, "id", "") or "")
        if current_agent_id and agent_id == str(current_agent_id):
            continue
        if is_system:
            if await can_delegate_to_agent(
                session,
                user_id=user_id,
                is_admin=is_admin,
                target_agent_id=agent_id,
            ):
                delegable.append(agent)
            continue
        # 自建：管理员可跨人配置，普通用户只认本人创建的（分享是副本语义，
        # 对方接受后 created_by 就是他自己，天然满足）。
        created_by = str(getattr(agent, "created_by", "") or "")
        if is_admin or (user_name and created_by == str(user_name)):
            delegable.append(agent)
    return delegable


async def filter_delegable_system_agents(
    session: Any,
    agents: Iterable[Any],
    *,
    user_id: int | str | None,
    is_admin: bool,
    current_agent_id: str | None,
) -> List[Any]:
    """系统级专用过滤（历史入口，保留供旧调用方使用）。"""
    return await filter_delegable_agents(
        session,
        agents,
        user_id=user_id,
        is_admin=is_admin,
        user_name=None,
        current_agent_id=current_agent_id,
        allow_personal=False,
    )



async def _filter_ready_delegable_agents(session: Any, agents: Iterable[Any]) -> List[Any]:
    """就绪层过滤：必须有可加载的发布版本，且主类型所需资源/工具齐全。

    与候选来源解耦——系统级候选和「白名单点名」的候选走同一套就绪判定，
    否则白名单里的自建智能体会绕过发布版本检查，委派到半残配置上。
    """
    import json
    from app.services.ai.agent_readiness import evaluate_agent_readiness
    from app.services.ai.agent_types import resolve_agent_type
    from app.services.ai.knowledge_utils import load_system_default_dataset_ids

    permitted = list(agents or [])
    if not permitted:
        return []

    # 系统级默认知识库数据集：KNOWLEDGE_BASE 类型智能体未显式绑定时的运行时兜底，
    # 就绪判定必须认同一口径，否则内置「知识库助手」会出现"能用但不能委派"。
    default_dataset_ids = await load_system_default_dataset_ids()

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
                default_dataset_ids=default_dataset_ids,
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
                # 关键：capabilities / engine_config 只存在于 ai_agents，版本表没有这两列。
                # 早期实现从 version 上 getattr 取，恒为 {}，导致 KNOWLEDGE_BASE 类型
                # 永远缺 knowledge_base_binding、永远无法被委派。
                capabilities=getattr(agent, "capabilities", None) or [],
                engine_config=getattr(agent, "engine_config", None) or {},
                tools=tools_list or [],
                has_published_version=True,
                default_dataset_ids=default_dataset_ids,
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
                default_dataset_ids=default_dataset_ids,
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


async def resolve_runnable_delegable_system_agents(
    session: Any,
    agents: Iterable[Any],
    *,
    user_id: int | str | None,
    is_admin: bool,
    current_agent_id: str | None,
) -> List[Any]:
    """Return permitted system agents that have a loadable, ready runtime."""
    permitted = await filter_delegable_system_agents(
        session,
        agents,
        user_id=user_id,
        is_admin=is_admin,
        current_agent_id=current_agent_id,
    )
    return await _filter_ready_delegable_agents(session, permitted)


async def resolve_accessible_agents_for_user(
    session: Any,
    *,
    user_id: int | str | None,
    is_admin: bool,
    user_name: str | None = None,
) -> List[Any]:
    """「这个用户能碰到哪些智能体」，与当前智能体的委派工具/白名单**无关**。

    必须和「我能委派哪些」分开：用户问「我有那些智能体权限」问的是授权，
    不该因为当前这个智能体没装委派工具就答成 0 个。后者由
    ``resolve_delegable_agent_stages`` 在前面再叠一层工具与名单。
    """
    system_agents = await load_delegable_system_agent_candidates(session)
    personal_agents = await load_accessible_personal_agents(
        session,
        user_name=user_name,
        is_admin=is_admin,
    )
    return await filter_delegable_agents(
        session,
        list(system_agents) + list(personal_agents),
        user_id=user_id,
        is_admin=is_admin,
        user_name=user_name,
        # 传 None 表示不剔除自身，由调用方用 is_current 标注。
        current_agent_id=None,
        allow_personal=True,
    )


async def resolve_delegable_agent_stages(
    session: Any,
    *,
    user_id: int | str | None,
    is_admin: bool,
    user_name: str | None = None,
    current_agent_id: str | None = None,
    allowlist_configured: bool = False,
    allowlist: Optional[Iterable[str]] = None,
) -> tuple[List[Any], List[Any], List[Any]]:
    """按「白名单 → 权限 → 就绪」三段过滤，返回三层结果。

    返回 ``(名单层候选, 权限层通过, 就绪层可用)``。``sub_agent_call`` 需要中间层
    才能把「不在名单」和「无权限」区分成不同错误码，因此这里不做封装；对外统一
    用 ``resolve_effective_delegable_agents`` 取第三层，保证三处消费方同源。
    """
    if allowlist_configured:
        agent_ids = [str(a or "").strip() for a in (allowlist or []) if str(a or "").strip()]
        # 显式清空：任何智能体都不许委派，不必再查库
        candidates = await load_agents_by_delegation_ids(session, agent_ids) if agent_ids else []
    else:
        candidates = await load_delegable_system_agent_candidates(session)

    permitted = await filter_delegable_agents(
        session,
        candidates,
        user_id=user_id,
        is_admin=is_admin,
        user_name=user_name,
        current_agent_id=current_agent_id,
        allow_personal=allowlist_configured,
    )
    ready = await _filter_ready_delegable_agents(session, permitted)
    return candidates, permitted, ready


async def resolve_effective_delegable_agents(
    session: Any,
    *,
    user_id: int | str | None,
    is_admin: bool,
    user_name: str | None = None,
    current_agent_id: str | None = None,
    allowlist_configured: bool = False,
    allowlist: Optional[Iterable[str]] = None,
) -> List[Any]:
    """本次请求**真正可委派**的智能体集合：白名单 ∧ 权限 ∧ 就绪。

    这是委派链的**唯一出口**，三处消费方必须都走它，否则会出现
    「通讯录列了 A → 模型调 A → 被白名单挡掉」的事故：
    1. ``sub_agent_call`` / ``sub_agent_batch_call`` 的运行时校验；
    2. ``build_sub_agents_context`` 注入的可委派通讯录；
    3. ``list_available_agents`` 目录工具。

    ``allowlist_configured`` 为 False 时保持历史口径（仅系统级）；为 True 时以名单
    为准，名单里的自建智能体才「开通」。
    """
    _, _, ready = await resolve_delegable_agent_stages(
        session,
        user_id=user_id,
        is_admin=is_admin,
        user_name=user_name,
        current_agent_id=current_agent_id,
        allowlist_configured=allowlist_configured,
        allowlist=allowlist,
    )
    return ready



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
        if chunk_type == "tool_progress":
            # 进度是瞬态装饰：按 tool_call_id 定位主聊天里**已存在**的工具卡。
            # 必须在这里拦下且放在正文提取之前 —— 进度帧带 ``message``（进度文案），
            # 而 ``_extract_delegation_text`` 恰好会读 ``message``，漏过去就会把
            # 「检索数据集 3/10」拼成子代理的最终回答。
            if main_ctx.event_queue:
                await main_ctx.event_queue.put(chunk)
            continue
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
        elif chunk_type == "citation" and main_ctx.event_queue:
            # 引用详情必须原样穿透到主聊天流：它不属于委派正文
            # （_extract_delegation_text 只认 content/text/message），不在这里转发就会被
            # 静默丢弃，主助手回答里的 [ID:n] 便没有任何切片可展开——这正是
            # 「委派后引用列表整块消失、点序号只提示引用详情加载中」的根因。
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
        # 调度白名单来自「当前智能体生效版本」的工具配置。主助手在运行时会隐式挂载
        # sub_agent_call，只有 tools 里真实存在该项才认白名单，否则一律按未配置处理
        # （= 系统级全量），保证老智能体的行为不变。
        caller_config = await AgentManagerService.get_active_agent_config(
            session, agent_id=str(main_ctx.agent_id)
        )
        # 名单只有一份：串行与批量委派都从 sub_agent_call 读（batch 上的历史字段兜底）。
        allowlist_configured, allowlist = resolve_delegation_allowlist(
            getattr(caller_config, "tools", None) if caller_config else None
        )
        caller_user_name = str((main_ctx.user_dimensions or {}).get("user_name") or "") or None

        # 自委派用上下文里的标识直接比对，不再回查 AIAgent 行：
        # 少一次查询，也不会因为测试替身缺 session.get 而误判。
        requested_key = _normalize_agent_name(agent_name)
        self_keys = {
            _normalize_agent_name(getattr(main_ctx, "agent_name", None)),
            _normalize_agent_name(getattr(main_ctx, "agent_id", None)),
        }
        if requested_key and requested_key in self_keys:
            return SubAgentResult(
                status=SubAgentResultStatus.FAILED,
                target_agent_name=(agent_name or "").strip() or None,
                content="错误：主智能体无法委派调用自身。",
                run_id=run_id,
                parent_trace_id=parent_trace_id,
                parent_conversation_id=main_ctx.conversation_id,
                error_code="self_delegation",
            ).to_tool_text()

        # 三层结果都要：可委派的、通过权限但未就绪的、连权限都没过的，分别对应三种错误。
        candidate_agents, permitted_agents, delegable_agents = await resolve_delegable_agent_stages(
            session,
            user_id=main_ctx.user_id,
            is_admin=main_ctx.is_admin,
            user_name=caller_user_name,
            current_agent_id=main_ctx.agent_id,
            allowlist_configured=allowlist_configured,
            allowlist=allowlist,
        )

        # 名字可能和上下文里的 agent_name 不同（同一智能体被改名、或按别名调用），
        # 所以还要拿候选行按 id 再比一次，否则「调自己」会被当成「无权访问」。
        self_by_candidate = next(
            (a for a in candidate_agents if _matches_requested_agent(a, agent_name)),
            None,
        )
        if self_by_candidate is not None and str(
            getattr(self_by_candidate, "id", "")
        ) == str(main_ctx.agent_id):
            return SubAgentResult(
                status=SubAgentResultStatus.FAILED,
                target_agent_name=(agent_name or "").strip() or None,
                content="错误：主智能体无法委派调用自身。",
                run_id=run_id,
                parent_trace_id=parent_trace_id,
                parent_conversation_id=main_ctx.conversation_id,
                error_code="self_delegation",
            ).to_tool_text()

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
            # 命中候选池但被权限层挡掉：白名单在内、权限不在；或系统级没有 agent 授权。
            blocked_match = next(
                (a for a in candidate_agents if _matches_requested_agent(a, agent_name)),
                None,
            )
            if blocked_match is not None:
                blocked_display = getattr(blocked_match, "display_name", None) or blocked_match.name
                return SubAgentResult(
                    status=SubAgentResultStatus.PERMISSION_DENIED,
                    target_agent_id=str(getattr(blocked_match, "id", "") or "") or None,
                    target_agent_name=str(blocked_display),
                    content=(
                        f"错误：当前用户无权使用智能体 `{blocked_match.name}`（{blocked_display}）。"
                        "系统级智能体需要管理员在角色权限中授予该智能体的访问权，"
                        "你自己的智能体则须由本人创建。请联系管理员开通后再试。"
                    ),
                    run_id=run_id,
                    parent_trace_id=parent_trace_id,
                    parent_conversation_id=main_ctx.conversation_id,
                    error_code="agent_permission_denied",
                ).to_tool_text()

            # 连候选池都没命中。配置了白名单就明确说「不在名单里」，
            # 这比笼统的 not_found 有用得多——用户能直接去改配置。
            if allowlist_configured:
                available = ", ".join(
                    f"`{a.name}` ({a.display_name or a.name})" for a in delegable_agents
                ) or "（当前名单内没有可用智能体）"
                return SubAgentResult(
                    status=SubAgentResultStatus.FAILED,
                    target_agent_name=(agent_name or "").strip() or None,
                    content=(
                        f"错误：智能体 `{agent_name}` 未被加入本智能体的委派名单，因此不可调用。"
                        f"如确需使用，请在本智能体的 `sub_agent_call` 工具配置「调度范围」中加入它；"
                        f"当前名单内可用：{available}。"
                    ),
                    run_id=run_id,
                    parent_trace_id=parent_trace_id,
                    parent_conversation_id=main_ctx.conversation_id,
                    error_code="agent_not_in_allowlist",
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
                    f"错误：未找到名为 '{agent_name}' 的已启用智能体。请重新反思问题，并只能从以下当前可委派的候选智能体列表中选择正确的英文标识 (agent_name) 进行 `sub_agent_call` 调用：{candidates_str}"
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
        # 共享主会话的引用台账。`new_turn_knowledge_citation_ledger` 在 depth>0 时
        # 只在 ctx 上已有实例时沿用，这里不传就等于让子代理另起一套发号空间，
        # 从 [ID:1] 重新开始，与主会话已有的编号撞车。
        knowledge_citation_ledger=main_ctx.knowledge_citation_ledger,
        published_download_urls=main_ctx.published_download_urls,
        skills_custom=bool(getattr(target_config, "skills_custom", False)),
        skills=list(getattr(target_config, "skills", None) or []),
    )
    # Pydantic may copy list fields while validating the model; explicitly keep
    # the same mutable registry so URLs issued by the child flow back to main.
    sub_ctx.published_download_urls = main_ctx.published_download_urls
    # 同理：`trace_buffer=...` 传进构造函数只是 Pydantic 复制的副本，注释声称的
    # 「共用 trace 收集物理步骤」并不成立，子代理写的步骤会落在随后被丢弃的 list 上。
    sub_ctx.trace_buffer = main_ctx.trace_buffer
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

    # 临时切换到子 Context 运行。execution_options 也要一并保存：
    # 子代理 setup_context 会把它覆盖成 {"delegated_sub_agent": True}，
    # 只恢复 agent_context 会让主智能体在同一 task 的后续轮次被误判成无人值守，
    # 连它自己的 ask_user_question 都会被拒。
    original_ctx = get_current_agent_context()
    original_execution_options = dict(execution_options_context.get() or {})
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
        set_execution_options_context(original_execution_options)

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
        # 委派链没有「向用户提问」的通道：子代理发出的事件只会被
        # _consume_sub_agent_stream 的白名单过滤掉（user_question 既不在中断类型里，
        # 也不在转发类型里），而且子代理跑在 child_session_{run_id} 独立会话上，
        # 就算卡片冒到前端，回执也查不到登记记录。打上标记让下游别再尝试交互。
        execution_options={"delegated_sub_agent": True},
        permission_options=permission_options,
        user_info=user_info,
        conversation_id=conversation_id,
        turn_decision=turn_decision,
    )
