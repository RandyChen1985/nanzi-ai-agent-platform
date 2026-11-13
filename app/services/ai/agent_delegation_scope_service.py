"""专家团成员（委派调度范围）的就地编辑。

成员名单只存在于版本工具的 ``delegable_agent_ids`` 上，运行时读的是最新
``PUBLISHED`` 版本。所以「在团设置里改成员」不能改一份独立数据，否则又会
出现「团里 5 个、委派实际 3 个」的两套真相。

这里用**隐式新版本**：复制当前已发布的版本，只替换成员名单这一个字段，
其余（提示词 / 模型 / 其他工具 / 技能 / 欢迎语）原样带过去，旧版本归档。
用户因此不必走「新建草稿 → 发布」的流程，但版本历史与回滚仍然成立。

几个刻意的约定：

* 名单只认 ``sub_agent_call``；``sub_agent_batch_call`` 跟随它，不再单独配一份
  （各配一份时只要漏填其中一个，另一个就按「未配置」走全量，自建子专家整批消失）。
* **团主自己（当前智能体）不写进名单**：它是委派的发起方，不是目标。
* ``main``（系统兜底入口）即使被传进来也会被剔除，与前端弹窗的处置一致。
* 空数组是**显式禁止委派**，与 ``null``（未配置 = 全部可委派）是两件事，不能合并。
"""

from __future__ import annotations

import copy
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AIAgent, AIAgentVersion
from app.services.ai.agent_manager import MAIN_GENERAL_AGENT_ID
from app.services.ai.tools.agent_delegate_tool import (
    DELEGATION_BATCH_TOOL_NAME,
    DELEGATION_TOOL_NAME,
    _configured_tool_name,
    _delegable_ids_of_tool_config,
    ensure_delegation_tool_pair,
)

logger = logging.getLogger(__name__)

#: 单份名单的上限。调度时名单会整份进入路由候选，太大既拖慢判断也几乎不可能是本意。
MAX_DELEGATION_MEMBERS = 20

#: 系统兜底入口，永远不能作为委派目标（被传进来就剔除，与前端弹窗一致）。
#: 真实的库主键是 ``sys-agent-chat``；``"main"`` 只是它的 name/slug 之一，
#: 两套都要认——只写 "main" 的话传 id 过来就漏过去了。
MAIN_AGENT_IDS = frozenset({MAIN_GENERAL_AGENT_ID})
MAIN_AGENT_SLUGS = frozenset({"main", "assistant", "general-chat"})


class DelegationScopeError(Exception):
    """调度范围不可写。message 直接面向用户。"""


class AgentNotVisibleError(DelegationScopeError):
    """智能体不存在，或当前用户无权查看它的版本配置。

    两者共用同一个错误：分开返回会变成存在性探测——能区分出
    「这个 id 存在但你没权限」和「这个 id 不存在」本身就是信息泄露。
    """


class NoPublishedVersionError(DelegationScopeError):
    """没有已发布版本：成员名单要存进版本配置，没有可复制的源。"""


class DelegationToolDisabledError(DelegationScopeError):
    """已发布版本没启用委派工具，且调用方没有同意一并启用。"""


class TooManyMembersError(DelegationScopeError):
    def __init__(self, count: int) -> None:
        super().__init__(f"成员最多 {MAX_DELEGATION_MEMBERS} 个，当前 {count} 个")
        self.count = count


@dataclass
class DelegationScopeState:
    exists_published: bool
    version_id: Optional[str] = None
    version_no: Optional[int] = None
    sub_agent_call_enabled: bool = False
    #: 名单是否被**显式配置**过。False = 未限制（全量可委派），不是「一个都不许」。
    #: 这两个状态在后端都是空列表，前端必须靠这一位区分，否则会把「全量」显示成「0 位」。
    scope_configured: bool = False
    delegable_agent_ids: List[str] = field(default_factory=list)
    #: 团主自己，前端据此判断「主专家」那一行，它永远不在名单里
    lead_agent_id: Optional[str] = None
    max_members: int = MAX_DELEGATION_MEMBERS

    def as_dict(self) -> Dict[str, Any]:
        return {
            "exists_published": self.exists_published,
            "version_id": self.version_id,
            "version_no": self.version_no,
            "sub_agent_call_enabled": self.sub_agent_call_enabled,
            "scope_configured": self.scope_configured,
            "delegable_agent_ids": list(self.delegable_agent_ids),
            "lead_agent_id": self.lead_agent_id,
            "max_members": self.max_members,
        }


def _as_tool_list(tools: Any) -> List[Any]:
    """把版本里的 ``tools`` 归一成列表。

    版本表是 JSON 列，正常读出来是 list；但存量数据里出现过字符串形态
    （``summarize_version_capabilities`` 也为此单独做了 ``json.loads`` 兼容）。
    直接 ``for item in tools`` 会按**字符**遍历，得到一堆无意义的单字符，
    成员名单因此被读成空——而且不报错，只在用户发现「团里没人」时才暴露。
    """
    if isinstance(tools, str):
        try:
            parsed = json.loads(tools)
        except (ValueError, TypeError):
            return []
        return parsed if isinstance(parsed, list) else []
    return list(tools or []) if isinstance(tools, (list, tuple)) else []


def _read_delegation_config(tools: Any) -> tuple[bool, bool, List[str]]:
    """读 ``(是否启用 sub_agent_call, 是否显式配置过名单, 名单)``。

    **三态**，与 ``resolve_delegation_allowlist`` 严格一致：

    - ``configured=False`` → 未配置：运行时是「系统级全量可委派」，不是「一个都不许」；
    - ``configured=True`` 且名单为空 → 显式禁止委派任何子专家；
    - ``configured=True`` 且名单非空 → 只许名单内。

    前两种在后端都表现为「名单是空列表」，但含义相反。少了 ``configured`` 这一位，
    前端只能把「全量可委派」渲染成「还没有成员」，用户顺手加一个人保存，
    就把全量能力静默收窄成了那一个人。

    名单只认 ``sub_agent_call``；存量数据可能只配在 batch 上，作为兜底读取，
    但**是否回退取决于 sub_agent_call 有没有显式配置**，不能拿名单本身判真假——
    空列表是假值，``[] or batch_ids`` 会把「显式禁止」读回 batch 的历史名单。
    """
    single_enabled = False
    single_configured = False
    single_ids: List[str] = []
    batch_configured = False
    batch_ids: List[str] = []
    for item in _as_tool_list(tools):
        name = _configured_tool_name(item)
        configured, ids = _delegable_ids_of_tool_config(item)
        if name == DELEGATION_TOOL_NAME:
            single_enabled = True
            if configured:
                single_configured = True
                single_ids = list(ids or [])
        elif name == DELEGATION_BATCH_TOOL_NAME and configured:
            batch_configured = True
            batch_ids = list(ids or [])

    if single_configured:
        return single_enabled, True, single_ids
    if batch_configured:
        # 存量兜底：老版本只把名单配在 batch 上
        return single_enabled, True, batch_ids
    return single_enabled, False, []


def _apply_scope_to_tools(
    tools: Any,
    member_ids: List[str],
    *,
    enable_tool: bool,
) -> List[Any]:
    """返回一份新的工具配置：只有委派相关的两项被改，其余保持原样。

    ``enable_tool`` 为真且当前没有 ``sub_agent_call`` 时追加该工具；随后由
    ``ensure_delegation_tool_pair`` 补齐 ``sub_agent_batch_call`` 并清掉 batch 上的
    历史名单字段——成对规则只有一处实现，不在这里复制一遍。
    """
    result: List[Any] = [copy.deepcopy(item) for item in _as_tool_list(tools)]

    has_single = any(
        _configured_tool_name(item) == DELEGATION_TOOL_NAME for item in result
    )
    if enable_tool and not has_single:
        result.append({"name": DELEGATION_TOOL_NAME})

    for index, item in enumerate(result):
        if _configured_tool_name(item) != DELEGATION_TOOL_NAME:
            continue
        base = item if isinstance(item, dict) else {"name": DELEGATION_TOOL_NAME}
        result[index] = {
            **base,
            "name": DELEGATION_TOOL_NAME,
            # 空列表是「显式禁止委派」，必须真的写进去，不能省略
            "delegable_agent_ids": list(member_ids),
        }

    return ensure_delegation_tool_pair(result, as_dict=True)


def _normalize_member_ids(raw: Any, *, lead_agent_id: str) -> List[str]:
    """去重、去空、剔除团主自己与系统兜底入口，保持传入顺序。"""
    cleaned: List[str] = []
    seen: set[str] = set()
    for value in raw or []:
        agent_id = str(value or "").strip()
        if not agent_id or agent_id in seen:
            continue
        if agent_id == lead_agent_id:
            continue
        # 主助手：库里主键是 sys-agent-chat，但存量和前端都可能传 name/slug
        if agent_id in MAIN_AGENT_IDS or agent_id.lower() in MAIN_AGENT_SLUGS:
            continue
        seen.add(agent_id)
        cleaned.append(agent_id)
    return cleaned


def _assert_writable(agent: AIAgent, user: Any) -> None:
    """与 publish_version 同一道门：管理员，或智能体所有者且不是系统级内容。"""
    if isinstance(user, dict):
        is_admin = user.get("role", "") == "admin"
        username = user.get("user_name", "")
    else:
        is_admin = bool(user and getattr(user, "role", "") == "admin")
        username = getattr(user, "user_name", "") if user else ""

    if not is_admin and agent.created_by != username:
        raise DelegationScopeError("只有智能体所有者或管理员可以修改调度范围")
    if agent.is_system and not is_admin:
        raise DelegationScopeError("系统级智能体的线上配置只能由管理员修改")


class AgentDelegationScopeService:
    @staticmethod
    async def _latest_published(
        session: AsyncSession, agent_id: str
    ) -> Optional[AIAgentVersion]:
        row = await session.execute(
            select(AIAgentVersion)
            .where(
                AIAgentVersion.agent_id == str(agent_id),
                AIAgentVersion.status == "PUBLISHED",
            )
            .order_by(desc(AIAgentVersion.version_number))
            .limit(1)
        )
        return row.scalars().first()

    @staticmethod
    async def _load_visible_agent(
        session: AsyncSession, agent_id: str, user: Any
    ) -> AIAgent:
        """取智能体并校验可读性；不存在与无权统一报同一个错，避免存在性探测。"""
        from app.services.ai.agent_manager import AgentManagerService

        agent = await session.get(AIAgent, agent_id)
        if agent is None:
            raise AgentNotVisibleError("智能体不存在或无权访问")
        if not await AgentManagerService._can_view_agent_versions(session, agent, user):
            raise AgentNotVisibleError("智能体不存在或无权访问")
        return agent

    @staticmethod
    async def read_scope(
        session: AsyncSession, agent_id: str, user: Any = None
    ) -> DelegationScopeState:
        agent = await AgentDelegationScopeService._load_visible_agent(
            session, agent_id, user
        )
        lead_id = str(agent.id)
        version = await AgentDelegationScopeService._latest_published(session, agent_id)
        if version is None:
            return DelegationScopeState(exists_published=False, lead_agent_id=lead_id)
        enabled, configured, ids = _read_delegation_config(version.tools)
        return DelegationScopeState(
            exists_published=True,
            version_id=str(version.id),
            version_no=int(version.version_number or 0) or None,
            sub_agent_call_enabled=enabled,
            scope_configured=configured,
            delegable_agent_ids=ids,
            lead_agent_id=lead_id,
        )

    @staticmethod
    async def save_scope(
        session: AsyncSession,
        agent_id: str,
        member_ids: Any,
        *,
        enable_tool: bool,
        user: Any,
    ) -> DelegationScopeState:
        """把成员名单写进一份**新发布的版本**，其余字段原样保留。"""
        agent = await session.get(AIAgent, agent_id)
        if agent is None:
            # 与 read_scope 同一口径：不存在走 404，而不是「无权修改」403
            raise AgentNotVisibleError("智能体不存在或无权访问")

        _assert_writable(agent, user)

        # 待审期间线上配置是冻结的：绕过它改名单，等于绕过审核改线上基线
        from app.services.ai.agent_system_publication_service import (
            AgentSystemPublicationService,
        )

        await AgentSystemPublicationService.assert_not_frozen(session, agent, user)

        source = await AgentDelegationScopeService._latest_published(session, agent_id)
        if source is None:
            raise NoPublishedVersionError(
                "该智能体还没有已发布的版本，先发布一个版本再配置成员"
            )

        enabled, _, _ = _read_delegation_config(source.tools)
        if not enabled and not enable_tool:
            raise DelegationToolDisabledError(
                "当前已发布版本没有启用委派工具，无法保存成员名单"
            )

        lead_id = str(agent.id)
        cleaned = _normalize_member_ids(member_ids, lead_agent_id=lead_id)
        if len(cleaned) > MAX_DELEGATION_MEMBERS:
            raise TooManyMembersError(len(cleaned))

        tools = _apply_scope_to_tools(
            source.tools, cleaned, enable_tool=enable_tool and not enabled
        )

        # 就绪度与发布同一口径：换了工具集（例如这次顺带启用了委派工具）也要过一遍
        from app.services.ai.agent_readiness import evaluate_agent_readiness
        from app.services.ai.knowledge_utils import load_system_default_dataset_ids

        readiness = evaluate_agent_readiness(
            agent_type=agent.agent_type or "GENERAL",
            capabilities=agent.capabilities,
            engine_config=agent.engine_config,
            tools=tools,
            has_published_version=True,
            default_dataset_ids=await load_system_default_dataset_ids(),
        )
        if not readiness.ready:
            from app.services.ai.agent_manager import AgentNotReadyError

            raise AgentNotReadyError(readiness.missing)

        # 版本号必须取**全智能体最大值 + 1**，不能拿线上版本 +1：
        # 线上 v2、草稿已经 v3 的情况下，后者会造出第二个 v3，版本列表排序和回滚全乱。
        max_row = await session.execute(
            select(AIAgentVersion.version_number)
            .where(AIAgentVersion.agent_id == str(agent_id))
            .order_by(desc(AIAgentVersion.version_number))
            .limit(1)
        )
        next_version_no = int(max_row.scalar_one_or_none() or 0) + 1

        new_version = AIAgentVersion(
            id=str(uuid.uuid4()),
            agent_id=str(agent_id),
            version_number=next_version_no,
            model_name=source.model_name,
            temperature=source.temperature,
            synthesis_model_name=source.synthesis_model_name,
            synthesis_temperature=source.synthesis_temperature,
            allow_model_switch=bool(getattr(source, "allow_model_switch", True)),
            system_prompt=source.system_prompt,
            tools=tools,
            toolcall_timeout_seconds=source.toolcall_timeout_seconds,
            skills_custom=source.skills_custom,
            skills=copy.deepcopy(source.skills),
            welcome_config=copy.deepcopy(source.welcome_config),
            status="PUBLISHED",
            comment=f"在专家团设置里更新成员（沿用 v{source.version_number}）",
        )

        # 先归档旧的再插入新的，保证任何时刻只有一条 PUBLISHED
        await session.execute(
            update(AIAgentVersion)
            .where(
                AIAgentVersion.agent_id == str(agent_id),
                AIAgentVersion.status == "PUBLISHED",
            )
            .values(status="ARCHIVED")
        )
        session.add(new_version)

        # 换掉的是线上基线，必须留痕：否则发布历史里版本号变了，
        # 却查不到是谁在什么时候换的（与 publish_version 同一口径）。
        from app.services.ai.agent_system_publication_service import (
            ACTION_BASELINE_PUBLISH,
            AgentSystemPublicationService,
        )

        _, actor_name, actor_id = AgentSystemPublicationService.resolve_actor(user)
        await AgentSystemPublicationService.record_event(
            session,
            agent=agent,
            action=ACTION_BASELINE_PUBLISH,
            actor_name=actor_name,
            actor_id=actor_id,
            version=new_version,
        )

        await session.commit()

        # 不失效缓存，运行时会继续用旧名单，表现成「加了却委派不到」。
        # 两个都要清：路由缓存管「选谁」，工具定义缓存管「有哪些工具」——
        # 这次可能顺带启用了委派工具，只清前者会让新工具在下一轮仍然不存在。
        from app.services.ai.router_service import router_service
        from app.services.ai.tools.registry import ToolRegistry

        ToolRegistry.clear_db_tool_cache()
        router_service.invalidate_cache()

        logger.info(
            "[DelegationScope] agent=%s v%s -> v%s members=%d enabled_tool=%s",
            agent_id,
            source.version_number,
            new_version.version_number,
            len(cleaned),
            enabled or enable_tool,
        )

        return await AgentDelegationScopeService.read_scope(session, agent_id, user)
