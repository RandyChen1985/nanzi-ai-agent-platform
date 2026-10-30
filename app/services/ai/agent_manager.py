import logging
import uuid
from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update, or_, case
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError, OperationalError
from app.core.avatar_assets import adopt_pending_avatar
from app.models.agent import AIAgent, AIAgentVersion
from app.schemas.agent import ChatConfig, AIAgentBase, AIAgentVersionBase
from app.services.ai.agent_system_columns import attach_system_status
from app.services.ai.agent_system_publication_service import AgentSystemPublicationService
from app.services.ai.agent_types import resolve_agent_type
from app.services.ai.welcome_card_service import normalize_welcome_config, safe_welcome_config
import json
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MAIN_GENERAL_AGENT_ID = "sys-agent-chat"
MAIN_GENERAL_AGENT_NAMES = frozenset({"main", "assistant", "general-chat"})


def _is_main_general_agent_record(agent: Any) -> bool:
    """Identify the fixed Main record before mutable metadata is updated."""
    if not agent:
        return False
    return (
        str(getattr(agent, "id", "") or "").strip() == MAIN_GENERAL_AGENT_ID
        or str(getattr(agent, "name", "") or "").strip().lower() in MAIN_GENERAL_AGENT_NAMES
    )


class AgentNotReadyError(ValueError):
    def __init__(self, missing: tuple[str, ...]):
        self.missing = missing
        super().__init__(f"Agent is not ready: {', '.join(missing)}")


@dataclass(frozen=True)
class AgentOnboardingResult:
    agent: AIAgent
    version: AIAgentVersion
    template_fallback: bool
    # 模板里因当前用户无权使用而被跳过的 MCP 工具引用（供前端提示）
    dropped_tool_refs: tuple = ()

async def load_agent_identity_map(
    session: Any, agent_ids: Any
) -> Dict[str, tuple[str | None, str | None, str | None]]:
    """按 ID 批量取智能体身份，**不套可见性过滤**。

    为什么不复用 `list_agents`：那个方法服务于「我能用哪些智能体」，非管理员会被过滤
    成只剩自己创建的那几个（`created_by != username` 一律跳过）。历史消息要的是
    「当时是谁回答的」，这是既成事实——不能因为当前用户看不到那个智能体，就把已经
    发生的对话署名抹掉。实测同一份数据：管理员能查到主助手，普通用户查不到，界面
    上「谁回答的」直接变空白。

    智能体被物理删除时这里也查不到，由调用方继续回落到 Redis 快照。
    """
    ids = {str(i).strip() for i in (agent_ids or []) if str(i or "").strip()}
    if not ids or session is None:
        return {}
    from app.models.agent import AIAgent
    from sqlalchemy import select

    rows = (
        await session.execute(
            select(AIAgent.id, AIAgent.name, AIAgent.display_name, AIAgent.avatar_url).where(
                AIAgent.id.in_(ids)
            )
        )
    ).all()
    return {str(row[0]): (row[1], row[2], row[3]) for row in rows}


def build_agent_identity_map(agents: Any) -> Dict[str, tuple]:
    """构建「智能体 ID → (slug 标识名, 显示名, 头像)」映射。

    历史消息只落库 `agent_id`，展示所需的身份信息要靠这张表回填。头像必须一起带上：
    `/agents/allowed` 会过滤 `is_enabled=True`，已停用智能体的历史消息前端索引不到，
    只有这里能补出来。
    """
    identity_map: Dict[str, tuple] = {}
    for agent in agents or []:
        agent_id = getattr(agent, "id", None)
        if agent_id is None:
            continue
        identity_map[str(agent_id)] = (
            getattr(agent, "name", None),
            getattr(agent, "display_name", None),
            getattr(agent, "avatar_url", None),
        )
    return identity_map


def _agent_name_conflict_detail(name: Any) -> str:
    """物理标识符冲突的用户可读提示（中文、可操作、指明是哪个字段）。"""
    return f"物理标识符 '{AgentManagerService.normalize_agent_name(name)}' 已被占用（智能体标识符全局唯一），请换一个"


class AgentManagerService:
    _ONBOARDING_TEMPLATE_AGENT_NAMES = {
        "GENERAL": "main",
        "CHATBI": "chat-bi",
        "KNOWLEDGE_BASE": "knowledge-base",
    }
    _ONBOARDING_FALLBACKS = {
        "GENERAL": {
            "system_prompt": "你是一个通用智能助手。请准确理解用户问题，在不确定时明确说明，并安全使用已配置工具。",
            "tools": [],
        },
        "CHATBI": {
            "system_prompt": "你是数据分析助手。必须基于真实数据集结构和查询结果回答，不得编造数据。",
            "tools": ["get_dataset_schema", "execute_sql_query"],
        },
        "KNOWLEDGE_BASE": {
            "system_prompt": "你是知识库助手。必须先检索已绑定知识库，再基于检索结果回答，不得编造内部资料。",
            "tools": ["search_knowledge_base"],
        },
    }

    @staticmethod
    def _validate_engine_config(engine_type: Any, engine_config: Optional[Dict[str, Any]]) -> None:
        normalized_engine = str(getattr(engine_type, "value", engine_type) or "LOCAL").upper()
        config = engine_config or {}
        if normalized_engine == "RAGFLOW" and not str(config.get("app_id") or "").strip():
            raise ValueError("RAGFlow 模式必须填写 App ID")
        if normalized_engine == "OPENCLAW" and (
            not str(config.get("base_url") or "").strip()
            or not str(config.get("model") or "").strip()
        ):
            raise ValueError("OpenClaw 模式必须填写地址和机器人 ID")

    @staticmethod
    async def drop_inaccessible_mcp_tools(
        session: AsyncSession,
        tools: Any,
        user: Any,
    ) -> Tuple[Any, List[Dict[str, str]]]:
        """剔除「源配置里有、但当前用户无权使用」的 MCP 工具引用。

        新建（套模板）和复制都会把别人的工具配置整份搬过来，而「源的所有者能用」
        不等于「搬过来的人能用」：管理员用自己私有 MCP 服务配好的智能体被普通用户
        套用后，那些引用对用户永久不可见——工具选择器不渲染它们，用户既看不见也
        取消不了，只能在列表页看到一堆「无权访问」告警。

        只剔 ``server_forbidden``：服务被临时禁用、工具下线属于源侧状态问题，
        照旧保留，否则搬一次就悄悄丢配置，比告警更难发现。

        返回 (过滤后的工具列表, 被剔除的明细)，明细供接口层回带给前端。
        """
        if not tools:
            return tools, []

        from app.services.mcp.tool_ref_health import find_stale_tool_refs

        stale_refs = await find_stale_tool_refs(session, tools, user=user)
        forbidden_items = [
            item for item in stale_refs if item.get("reason") == "server_forbidden"
        ]
        if not forbidden_items:
            return tools, []

        forbidden = {str(item.get("name")) for item in forbidden_items}
        filtered = [
            item
            for item in tools
            if (item if isinstance(item, str) else (item or {}).get("name")) not in forbidden
        ]
        dropped = [
            {"name": str(item.get("name")), "reason_label": item.get("reason_label")}
            for item in forbidden_items
        ]
        logger.info(
            "[agent] 剔除 %d 个当前用户无权使用的 MCP 工具引用", len(dropped)
        )
        return filtered, dropped

    @staticmethod
    async def _resolve_onboarding_template(
        session: AsyncSession,
        agent_type: Any,
        user: Any = None,
    ) -> Dict[str, Any]:
        type_value = getattr(agent_type, "value", agent_type)
        template_name = AgentManagerService._ONBOARDING_TEMPLATE_AGENT_NAMES[str(type_value)]
        config = await AgentManagerService.get_active_agent_config(
            session,
            agent_name=template_name,
        )
        if config:
            # 模板是拿某个真实智能体配的，里面的 MCP 引用可能只对模板作者可用
            # （典型是管理员的私有服务）。套模板的人看不到那些服务，必须在这里剔掉，
            # 否则新建出来的智能体一保存就挂着「无权访问该 MCP 服务」的告警。
            template_tools, dropped_tools = await AgentManagerService.drop_inaccessible_mcp_tools(
                session,
                AgentManagerService._normalize_tools_for_db(config.tools),
                user,
            )
            return {
                "model_name": config.model_name,
                "temperature": config.temperature or 0.0,
                "system_prompt": config.system_prompt,
                "tools": template_tools,
                "skills_custom": bool(config.skills_custom),
                "skills": list(config.skills or []),
                "template_fallback": False,
                "dropped_tool_refs": dropped_tools,
            }

        from fastapi import HTTPException

        from app.services.ai.default_model import (
            DefaultModelNotConfigured,
            require_default_llm_model,
        )

        fallback = AgentManagerService._ONBOARDING_FALLBACKS[str(type_value)]

        try:
            model_name = await require_default_llm_model()
        except DefaultModelNotConfigured as exc:
            # 不能伪造模型名落库：否则该智能体之后每次调用都会拿一个目标环境
            # 并不存在的模型去要 API Key，报错会被误诊成「密钥缺失」。
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return {
            "model_name": model_name,
            "temperature": 0.0,
            "system_prompt": fallback["system_prompt"],
            "tools": list(fallback["tools"]),
            "skills_custom": False,
            "skills": [],
            "template_fallback": True,
            "dropped_tool_refs": [],
        }

    @staticmethod
    @staticmethod
    def _reject_manual_system_flag(data: Any) -> None:
        """「是否系统级」不接受新建/编辑表单设置。

        系统级是「生产发布的基线」：只能通过「发布为系统级」动作或管理员审核通过获得，
        每一次都要落事件、发通知。允许表单直接勾选就等于绕开整条流程与全部留痕。
        """
        from fastapi import HTTPException

        if data is not None and getattr(data, "is_system", None):
            raise HTTPException(
                status_code=400,
                detail=(
                    "「是否系统级」不能通过新建或编辑设置：请保存后用卡片菜单的「发布为系统级」，"
                    "或提交发布申请由管理员审核"
                ),
            )

    async def create_agent_onboarding(
        session: AsyncSession,
        data: AIAgentBase,
        *,
        onboarding_key: str,
        user: Any = None,
    ) -> AgentOnboardingResult:
        AgentManagerService._reject_manual_system_flag(data)

        if isinstance(user, dict):
            created_by = user.get("user_name")
            is_admin = user.get("role", "") == "admin"
        else:
            created_by = getattr(user, "user_name", None) if user else None
            is_admin = bool(user and getattr(user, "role", "") == "admin")

        existing_result = await session.execute(
            select(AIAgent).where(
                AIAgent.created_by == created_by,
                AIAgent.onboarding_key == onboarding_key,
            )
        )
        existing_agent = existing_result.scalar_one_or_none()
        if existing_agent:
            version_result = await session.execute(
                select(AIAgentVersion)
                .where(AIAgentVersion.agent_id == existing_agent.id)
                .order_by(AIAgentVersion.version_number.asc())
                .limit(1)
            )
            existing_version = version_result.scalar_one_or_none()
            if existing_version:
                return AgentOnboardingResult(
                    agent=existing_agent,
                    version=existing_version,
                    template_fallback=False,
                    dropped_tool_refs=(),
                )

        conflict = await AgentManagerService.find_agent_name_conflict(session, data.name)
        if conflict:
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail=_agent_name_conflict_detail(data.name))

        template = await AgentManagerService._resolve_onboarding_template(
            session, data.agent_type, user
        )
        from app.services.ai.agent_types import (
            normalize_agent_capabilities_for_agent,
            resolve_agent_type_for_engine,
        )

        resolved_agent_type = resolve_agent_type_for_engine(data.engine_type, data.agent_type)
        new_agent_id = str(uuid.uuid4())
        agent = AIAgent(
            id=new_agent_id,
            name=data.name,
            display_name=data.display_name,
            description=data.description,
            # 新建流程先传头像再建智能体，这里把待绑定文件转正，避免被 TTL 回收
            avatar_url=adopt_pending_avatar(data.avatar_url, new_agent_id),
            capabilities=normalize_agent_capabilities_for_agent(
                engine_type=data.engine_type,
                agent_type=resolved_agent_type,
                values=data.capabilities,
            ),
            agent_type=resolved_agent_type.value,
            onboarding_key=onboarding_key,
            onboarding_step="VERSION",
            # 系统级只能通过「发布为系统级」获得（有事件、有流程），新建表单不再接受该字段
            is_system=False,
            sort_order=data.sort_order or 0,
            is_enabled=data.is_enabled if data.is_enabled is not None else True,
            created_by=created_by,
            engine_type=data.engine_type,
            engine_config=data.engine_config,
        )
        version = AIAgentVersion(
            id=str(uuid.uuid4()),
            agent_id=agent.id,
            version_number=1,
            model_name=template["model_name"],
            temperature=template["temperature"],
            system_prompt=template["system_prompt"],
            tools=template["tools"],
            skills_custom=template["skills_custom"],
            skills=template["skills"],
            allow_model_switch=True,
            welcome_config=None,
            status="DRAFT",
            comment="Initial onboarding template snapshot",
        )
        session.add(agent)
        session.add(version)
        try:
            await session.flush()
            await session.commit()
        except IntegrityError:
            # A retry with the same onboarding key may win between the initial
            # lookup and commit. Return that committed pair instead of leaking
            # a duplicate-key database error to the user.
            await session.rollback()
            winner_result = await session.execute(
                select(AIAgent).where(
                    AIAgent.created_by == created_by,
                    AIAgent.onboarding_key == onboarding_key,
                )
            )
            winner = winner_result.scalar_one_or_none()
            if winner:
                winner_version_result = await session.execute(
                    select(AIAgentVersion)
                    .where(AIAgentVersion.agent_id == winner.id)
                    .order_by(AIAgentVersion.version_number.asc())
                    .limit(1)
                )
                winner_version = winner_version_result.scalar_one_or_none()
                if winner_version:
                    return AgentOnboardingResult(
                        agent=winner,
                        version=winner_version,
                        template_fallback=False,
                    )
            raise
        await session.refresh(agent)
        await session.refresh(version)

        from app.services.ai.router_service import router_service

        router_service.invalidate_cache()
        return AgentOnboardingResult(
            agent=agent,
            version=version,
            template_fallback=bool(template["template_fallback"]),
            dropped_tool_refs=tuple(template.get("dropped_tool_refs") or ()),
        )
    @staticmethod
    def _tool_entry_name(tool: Any) -> str:
        if isinstance(tool, dict):
            return str(tool.get("name") or "").strip()
        return str(tool or "").strip()

    @staticmethod
    def _is_mcp_tool_name(name: str) -> bool:
        # MCP 工具在平台内统一命名为 server_name:tool_name
        return ":" in name

    @classmethod
    def _count_bound_metadata_datasets(cls, tools: List[Any]) -> Optional[int]:
        """从 get_dataset_schema 工具配置读取**实际生效**的元数据集数；未绑定则 None。

        口径与 ``get_dataset_schema`` 入口一致（共用 ``normalize_bound_dataset_ids``）：
        绑定值写成数据集名称时运行时会丢弃，这里也不能计入，否则横幅会显示一个
        实际不存在的绑定数量。
        """
        from app.services.ai.metadata_dataset_scope import normalize_bound_dataset_ids

        for tool in tools:
            if not isinstance(tool, dict):
                continue
            if cls._tool_entry_name(tool) != "get_dataset_schema":
                continue
            cleaned = normalize_bound_dataset_ids(tool.get("metadata_dataset_ids") or [])
            return len(cleaned) if cleaned else None
        return None

    @staticmethod
    def _count_bound_knowledge_bases(engine_config: Optional[Dict[str, Any]]) -> Optional[int]:
        """从 engine_config.dataset_ids 统计显式绑定知识库数；未绑定则 None（走全局策略）。"""
        from app.services.ai.knowledge_utils import normalize_dataset_ids

        ids = normalize_dataset_ids((engine_config or {}).get("dataset_ids"))
        return len(ids) if ids else None

    @classmethod
    def summarize_version_capabilities(
        cls,
        version: Optional[AIAgentVersion],
        engine_config: Optional[Dict[str, Any]] = None,
        *,
        enabled_global_skill_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        """统计 tool / mcp / skills，以及显式绑定的数据集 / 知识库数量。"""
        knowledge_base_count = cls._count_bound_knowledge_bases(engine_config)
        if version is None:
            return {
                "tool_count": None,
                "mcp_count": None,
                "skill_count": None,
                "skills_custom": None,
                "metadata_dataset_count": None,
                "knowledge_base_count": knowledge_base_count,
            }

        tools_raw = version.tools or []
        if isinstance(tools_raw, str):
            try:
                tools_raw = json.loads(tools_raw)
            except Exception:
                tools_raw = []
        if not isinstance(tools_raw, list):
            tools_raw = []

        tool_count = 0
        mcp_count = 0
        for tool in tools_raw:
            name = cls._tool_entry_name(tool)
            if not name:
                continue
            if cls._is_mcp_tool_name(name):
                mcp_count += 1
            else:
                tool_count += 1

        skills_custom = bool(getattr(version, "skills_custom", False))
        skills_raw = getattr(version, "skills", None) or []
        if isinstance(skills_raw, str):
            try:
                skills_raw = json.loads(skills_raw)
            except Exception:
                skills_raw = []
        if not isinstance(skills_raw, list):
            skills_raw = []
        # skills_custom=False：使用全部公共技能，skill_count 为启用中的公共技能总数
        if skills_custom:
            skill_count = len([s for s in skills_raw if str(s).strip()])
        else:
            skill_count = (
                int(enabled_global_skill_count)
                if enabled_global_skill_count is not None
                else 0
            )

        return {
            "tool_count": tool_count,
            "mcp_count": mcp_count,
            "skill_count": skill_count,
            "skills_custom": skills_custom,
            "metadata_dataset_count": cls._count_bound_metadata_datasets(tools_raw),
            "knowledge_base_count": knowledge_base_count,
        }

    @staticmethod
    async def _latest_published_versions_by_agent(
        session: AsyncSession,
        agent_ids: List[str],
    ) -> Dict[str, AIAgentVersion]:
        if not agent_ids:
            return {}
        rows = (
            await session.execute(
                select(AIAgentVersion)
                .where(
                    AIAgentVersion.agent_id.in_(agent_ids),
                    AIAgentVersion.status == "PUBLISHED",
                )
                .order_by(AIAgentVersion.agent_id, AIAgentVersion.version_number.desc())
            )
        ).scalars().all()
        latest: Dict[str, AIAgentVersion] = {}
        for version in rows:
            if version.agent_id not in latest:
                latest[version.agent_id] = version
        return latest

    @staticmethod
    async def get_active_agent_config(
        session: AsyncSession, 
        agent_id: Optional[str] = None, 
        agent_name: Optional[str] = None
    ) -> Optional[ChatConfig]:
        """
        Fetches the active configuration for an agent.
        - For LOCAL: uses the latest PUBLISHED version.
        - For RAGFLOW: uses the agent's main config (no local version required).
        """
        try:
            # 1. Fetch the Agent Base record first
            stmt = select(AIAgent)
            if agent_id:
                stmt = stmt.where(or_(AIAgent.id == agent_id, AIAgent.name == agent_id))
            elif agent_name:
                stmt = stmt.where(AIAgent.name == agent_name)
            else:
                stmt = stmt.where(AIAgent.name == 'chat-bi')
            
            result = await session.execute(stmt)
            agent = result.scalar_one_or_none()
            
            if not agent:
                logger.warning(f"No agent found for id={agent_id}, name={agent_name}")
                return None
            
            if not agent.is_enabled:
                logger.warning(f"Agent {agent.name} is disabled.")
                return None

            # 2. Handle RAGFLOW & OPENCLAW Engine (Bypass Versioning)
            if agent.engine_type in ['RAGFLOW', 'OPENCLAW']:
                return ChatConfig(
                    agent_id=agent.id,
                    agent_name=agent.name,
                    agent_display_name=agent.display_name or agent.name,
                    agent_type=resolve_agent_type(agent),
                    description=agent.description or "",
                    agent_version="managed",
                    model_name=agent.engine_config.get("model", "OpenClaw-Remote") if agent.engine_type == 'OPENCLAW' else "RAGFlow-Remote",
                    temperature=0.0,
                    system_prompt=f"[Managed by {agent.engine_type}]",
                    tools=[],
                    capabilities=agent.capabilities or [],
                    engine_type=agent.engine_type,
                    engine_config=agent.engine_config
                )

            # 3. Handle LOCAL Engine (Require PUBLISHED version)
            query = select(AIAgentVersion).where(
                AIAgentVersion.agent_id == agent.id,
                AIAgentVersion.status == 'PUBLISHED'
            ).order_by(AIAgentVersion.version_number.desc()).limit(1)
            
            v_res = await session.execute(query)
            version = v_res.scalar_one_or_none()
            
            if not version:
                logger.warning(f"No published version found for local agent {agent.name}")
                return None
            
            # Resolve tools
            tools_list = version.tools
            if isinstance(tools_list, str):
                try:
                    tools_list = json.loads(tools_list)
                except:
                    tools_list = []
            
            return ChatConfig(
                agent_id=agent.id,
                agent_name=agent.name,
                agent_display_name=agent.display_name or agent.name,
                agent_type=resolve_agent_type(agent),
                description=agent.description or "",
                agent_version=f"v{version.version_number}",
                model_name=version.model_name,
                temperature=version.temperature,
                synthesis_model_name=version.synthesis_model_name,
                synthesis_temperature=version.synthesis_temperature,
                allow_model_switch=bool(getattr(version, "allow_model_switch", True)),
                system_prompt=version.system_prompt,
                tools=tools_list or [],
                toolcall_timeout_seconds=getattr(version, "toolcall_timeout_seconds", None),
                skills_custom=bool(getattr(version, "skills_custom", False)),
                skills=AgentManagerService._normalize_skills_list(getattr(version, "skills", None)),
                welcome_config=safe_welcome_config(getattr(version, "welcome_config", None)),
                capabilities=agent.capabilities or [],
                engine_type=agent.engine_type,
                engine_config=agent.engine_config
            )
            
        except OperationalError as e:
            error_text = str(e)
            if "toolcall_timeout_seconds" in error_text and "Unknown column" in error_text:
                # 兼容未执行 V133/V33 迁移的环境，避免元数据推荐前刷整段堆栈。
                logger.warning(
                    "读取智能体配置失败：数据库缺少 ai_agent_versions.toolcall_timeout_seconds，"
                    "请执行对应迁移 db-prod/V133 或 db-prod-pg/V33；本次将使用默认模型配置降级继续。"
                )
                return None
            logger.error(f"Error fetching agent config: {e}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"Error fetching agent config: {e}", exc_info=True)
            return None

    @staticmethod
    def can_use_agent_version(version: Any, agent: Any, user: Any) -> bool:
        """该版本能否被当前用户加载执行。

        - 未传 user（定时任务 / 内部调用）：跳过校验，保持原有行为
        - 管理员：全部可用
        - 作者本人：草稿和已发布版本都能调试
        - 其他人：只能跑已发布版本，草稿一律拒绝
        """
        if user is None:
            return True
        is_admin, username = AgentManagerService._resolve_actor(user)
        if is_admin:
            return True
        status = str(getattr(version, "status", "") or "")
        owner = getattr(agent, "created_by", None) if agent is not None else None
        if owner and username and owner == username:
            return True
        return status == "PUBLISHED"

    @staticmethod
    async def get_version_config(
        session: AsyncSession,
        version_id: str,
        user: Any = None,
    ) -> Optional[ChatConfig]:
        """Fetches the configuration for a SPECIFIC version ID.

        `user` 不为空时会校验该版本是否对调用者可用：草稿只有作者本人和管理员能跑，
        别人只能跑已发布版本。缺了这层校验，任何人拿到 version_id 就能把别人的草稿
        当线上配置跑起来，提示词和工具配置等于直接暴露。
        """
        try:
            # 1. Try to find the version record
            query = select(AIAgentVersion).join(AIAgent).options(selectinload(AIAgentVersion.agent)).where(AIAgentVersion.id == version_id)
            result = await session.execute(query)
            version = result.scalar_one_or_none()
            
            if version:
                if not AgentManagerService.can_use_agent_version(version, version.agent, user):
                    logger.warning(
                        "version config denied: version=%s agent=%s user=%s",
                        version.id,
                        version.agent_id,
                        (user or {}).get("user_name") if isinstance(user, dict) else getattr(user, "user_name", None),
                    )
                    return None
                tools_list = version.tools
                if isinstance(tools_list, str):
                    try:
                        tools_list = json.loads(tools_list)
                    except:
                        tools_list = []
                
                return ChatConfig(
                    agent_id=version.agent_id,
                    agent_name=version.agent.name,
                    agent_display_name=version.agent.display_name or version.agent.name,
                    agent_type=resolve_agent_type(version.agent),
                    agent_version=f"v{version.version_number}",
                    model_name=version.model_name,
                    temperature=version.temperature,
                    synthesis_model_name=version.synthesis_model_name,
                    synthesis_temperature=version.synthesis_temperature,
                    allow_model_switch=bool(getattr(version, "allow_model_switch", True)),
                    system_prompt=version.system_prompt,
                    tools=tools_list or [],
                    toolcall_timeout_seconds=getattr(version, "toolcall_timeout_seconds", None),
                    skills_custom=bool(getattr(version, "skills_custom", False)),
                    skills=AgentManagerService._normalize_skills_list(getattr(version, "skills", None)),
                    welcome_config=safe_welcome_config(getattr(version, "welcome_config", None)),
                    capabilities=version.agent.capabilities or [],
                    engine_type=version.agent.engine_type,
                    engine_config=version.agent.engine_config
                )
            
            # 2. Fallback: Check if version_id is actually an Agent ID (for RAGFlow shortcut)
            agent_query = select(AIAgent).where(AIAgent.id == version_id)
            a_res = await session.execute(agent_query)
            agent = a_res.scalar_one_or_none()
            
            if agent and agent.engine_type == 'RAGFLOW':
                return await AgentManagerService.get_active_agent_config(session, agent_id=agent.id)

            return None
        except Exception as e:
            logger.error(f"Error fetching version config: {e}", exc_info=True)
            return None

    @staticmethod
    async def list_agents(session: AsyncSession, user: Any = None) -> List[Any]:
        """
        List all available agents based on user visibility.
        - Admin: See all.
        - User: See active (public/system) + own created.
        """
        # 1. Fetch Agents: fixed Main first, then enabled agents, then disabled agents.
        main_first = case(
            (or_(AIAgent.id == MAIN_GENERAL_AGENT_ID, AIAgent.name.in_(MAIN_GENERAL_AGENT_NAMES)), 0),
            else_=1,
        )
        enabled_first = case((AIAgent.is_enabled == True, 0), else_=1)
        system_first = case((AIAgent.is_system == True, 0), else_=1)
        query = select(AIAgent).order_by(
            main_first,
            enabled_first,
            system_first,
            AIAgent.sort_order.desc(),
            AIAgent.display_name,
        )
        result = await session.execute(query)
        agents = result.scalars().all()

        # Determine user info first
        is_admin = user.get('role', '') == 'admin' if isinstance(user, dict) else getattr(user, 'role', '') == 'admin'
        username = user.get('user_name', '') if isinstance(user, dict) else getattr(user, 'user_name', '')
        # Fix: AuthService returns 'user_id', checking both for compatibility
        user_id = user.get('user_id', user.get('id', '')) if isinstance(user, dict) else getattr(user, 'id', '')

        # 2. Fetch Execution Counts
        from app.models.audit import AgentExecutionHistory
        from sqlalchemy import func
        
        count_query = select(AgentExecutionHistory.agent_id, func.count(AgentExecutionHistory.id))
        
        # Filter counts by user if not admin
        if not is_admin and user_id:
             count_query = count_query.where(AgentExecutionHistory.user_id == user_id)
             
        count_query = count_query.group_by(AgentExecutionHistory.agent_id)
        count_res = await session.execute(count_query)
        counts = dict(count_res.fetchall())
        
        # Determine visibility and editability
        visible_agents = []

        for agent in agents:
            # 可见性：非管理员只看自己创建的。别人创建的智能体（无论是否系统级）都不出现：
            # 系统智能体由管理员维护，普通用户既管不了，也不该看到它的提示词与工具配置——
            # 复制接口会把整份配置带出来，看到即等于可复刻。
            # 按 created_by 而不是 is_system 判定：自己创建的智能体被管理员提升为系统级后
            # created_by 不变，本人仍应看得到。
            if not is_admin and agent.created_by != username:
                continue

            # Editability Check
            is_owner = agent.created_by == username
            agent.is_editable = True if is_admin or is_owner or (not agent.created_by and is_admin) else False
            
            # Special case: System agents are read-only for non-admins (metadata only)
            if agent.is_system and not is_admin:
                 agent.is_editable = False

            # 版本内容与元数据分开授权：系统级智能体的所有者仍可建版本、改草稿、存草稿，
            # 只是「发布上线」这条路必须由管理员走。只读会把所有者逼去「复制一份」，
            # 代价是版本历史断裂；真正要守的是「线上基线只能管理员变更」。
            agent.can_edit_versions = bool(is_admin or is_owner)
            agent.can_publish_version = not (agent.is_system and not is_admin)
            
            # Set execution count (dynamically attached to object, Pydantic will pick it up)
            agent.execution_count = counts.get(agent.id, 0)
                 
            visible_agents.append(agent)

        # 创建者显示名：卡片上只显示 user_name 对管理员不直观（chenxiaolong vs 陈小龙），
        # 批量反查一次用户表补上 real_name，没有则回落到 user_name。
        creator_keys = {a.created_by for a in visible_agents if getattr(a, "created_by", None)}
        creator_names: dict[str, str] = {}
        if creator_keys:
            from app.models.user import User

            # 顺带取 status：权属人离职/停用后，这个智能体就没人能编辑了。
            # 管理员得能一眼筛出来，否则只能靠人想起来去转交。
            name_rows = await session.execute(
                select(User.user_name, User.real_name, User.status).where(
                    User.user_name.in_(creator_keys)
                )
            )
            rows = name_rows.all()
            creator_names = {row[0]: (row[1] or row[0]) for row in rows if row and row[0]}
            creator_active = {
                row[0]: (row[2] == 1) for row in rows if row and row[0]
            }
        else:
            creator_active = {}
        for agent in visible_agents:
            key = getattr(agent, "created_by", None)
            agent.created_by_name = creator_names.get(key, key) if key else None
            # 查不到对应用户时按「正常」处理：历史数据里 created_by 可能是脚本写入的
            # 用户名，不该因此把智能体标成孤儿。
            agent.owner_active = creator_active.get(key, True) if key else True

        # 批量附带已发布版本的 tool / mcp / skills 摘要（仅 LOCAL 引擎有版本工具配置）
        local_ids = [a.id for a in visible_agents if (a.engine_type or "LOCAL") == "LOCAL"]
        published_by_agent = await AgentManagerService._latest_published_versions_by_agent(
            session, local_ids
        )
        from app.services.ai.skill_resolver import count_enabled_global_skills

        enabled_global_skill_count = count_enabled_global_skills()
        from app.services.ai.knowledge_utils import load_system_default_dataset_ids

        # 系统级默认知识库数据集：KNOWLEDGE_BASE 智能体未显式绑定时的运行时兜底，
        # 就绪角标必须与 runtime/委派判定同口径（整表只读一次配置）。
        default_dataset_ids = await load_system_default_dataset_ids()
        for agent in visible_agents:
            engine_config = agent.engine_config if isinstance(agent.engine_config, dict) else None
            published_version = published_by_agent.get(agent.id)
            if (agent.engine_type or "LOCAL") != "LOCAL":
                caps = AgentManagerService.summarize_version_capabilities(
                    None,
                    engine_config=engine_config,
                    enabled_global_skill_count=enabled_global_skill_count,
                )
            else:
                caps = AgentManagerService.summarize_version_capabilities(
                    published_version,
                    engine_config=engine_config,
                    enabled_global_skill_count=enabled_global_skill_count,
                )
            agent.published_version_number = (
                getattr(published_version, "version_number", None) if published_version else None
            )
            agent.tool_count = caps["tool_count"]
            agent.mcp_count = caps["mcp_count"]
            agent.skill_count = caps["skill_count"]
            agent.skills_custom = caps["skills_custom"]
            agent.metadata_dataset_count = caps["metadata_dataset_count"]
            agent.knowledge_base_count = caps["knowledge_base_count"]
            from app.services.ai.agent_readiness import evaluate_agent_readiness
            from app.services.ai.agent_types import resolve_agent_type

            readiness = evaluate_agent_readiness(
                agent_type=resolve_agent_type(agent),
                capabilities=agent.capabilities,
                engine_config=engine_config,
                tools=(published_version.tools if published_version else []),
                has_published_version=(
                    published_version is not None
                    or (agent.engine_type or "LOCAL") != "LOCAL"
                ),
                default_dataset_ids=default_dataset_ids,
            )
            agent.readiness_ready = readiness.ready
            agent.readiness_missing = list(readiness.missing)

        # 模型健康：只校验 LOCAL 引擎的已启用智能体。OPENCLAW 的 model 是机器人 ID，
        # RAGFLOW 不走平台 LLM，拿模型表去比对只会误报。
        # 先统一置空：写在循环外会让 visible_agents 为空时引用到未绑定的循环变量
        # （UnboundLocalError），而「空列表」恰恰是权限过滤后的正常结果。
        for agent in visible_agents:
            agent.model_issue = None
        local_enabled = [
            agent
            for agent in visible_agents
            if (agent.engine_type or "LOCAL") == "LOCAL" and agent.is_enabled
        ]
        if local_enabled:
            from app.services.ai.agent_model_health import (
                evaluate_model_issue,
                load_global_llm_api_key,
                load_model_registry,
            )

            model_registry = await load_model_registry(session)
            has_global_api_key = await load_global_llm_api_key()
            for agent in local_enabled:
                published_version = published_by_agent.get(agent.id)
                if published_version is None:
                    # 无已发布版本由 readiness 的 published_version 项覆盖，这里不重复报。
                    continue
                issue = evaluate_model_issue(
                    getattr(published_version, "model_name", None),
                    registry=model_registry,
                    has_global_api_key=has_global_api_key,
                )
                if issue is not None:
                    agent.model_issue = issue.to_dict()

        # 工具失效：运行时用已发布版本，所以只校验它；没有已发布版本的由 readiness 覆盖。
        # 判定口径与版本编辑器的 tool-health 完全一致，避免列表说没问题、点进去一堆红。
        for agent in visible_agents:
            agent.tool_issue = None
        tool_targets = [
            (agent, published_by_agent.get(agent.id))
            for agent in visible_agents
            if (agent.engine_type or "LOCAL") == "LOCAL"
            and agent.is_enabled
            and published_by_agent.get(agent.id) is not None
        ]
        if tool_targets:
            from app.services.mcp.tool_ref_health import find_stale_tool_refs_bulk

            stale_lists = await find_stale_tool_refs_bulk(
                session,
                [version.tools for _agent, version in tool_targets],
                user=user if isinstance(user, dict) else {
                    "user_id": getattr(user, "user_id", None),
                    "user_name": getattr(user, "user_name", None),
                },
            )
            for (agent, version), stale in zip(tool_targets, stale_lists):
                if not stale:
                    continue
                # 只列前 3 个名字：横幅是给人扫的，全量明细在版本编辑器里
                labels = [
                    f"{item['name']}（{item['reason_label']}）" for item in stale[:3]
                ]
                suffix = "" if len(stale) <= 3 else f" 等 {len(stale)} 个"
                agent.tool_issue = {
                    "code": "stale_tools",
                    "message": "、".join(labels) + suffix,
                    "count": len(stale),
                    "names": [item["name"] for item in stale],
                    "version_id": version.id,
                }

        # 系统级流程状态（审核中徽标 / 编辑冻结提示）。ORM 未映射这两列，
        # 这里一条 SQL 批量挂到对象上供响应序列化，未迁移时静默跳过。
        await attach_system_status(session, visible_agents)

        return visible_agents

    @staticmethod
    def _extract_user_identity(user: Any) -> tuple[bool, str, str]:
        if isinstance(user, dict):
            is_admin = user.get("role", "") == "admin"
            username = user.get("user_name", "") or ""
            user_id = str(user.get("user_id", user.get("id", "")) or "")
        else:
            is_admin = getattr(user, "role", "") == "admin"
            username = getattr(user, "user_name", "") or ""
            user_id = str(getattr(user, "id", "") or "")
        return bool(is_admin), username, user_id

    @staticmethod
    async def _user_can_execute_agent(session: AsyncSession, agent: AIAgent, user: Any) -> bool:
        """Whether the user may chat with this agent (same rules as list_allowed_agents)."""
        if not user or not agent:
            return False
        is_admin, username, user_id = AgentManagerService._extract_user_identity(user)
        if is_admin:
            return True
        if not agent.is_system:
            return bool(username) and agent.created_by == username
        from app.services.permission_service import PermissionService

        perm_service = PermissionService(session)
        try:
            uid_int = int(user_id)
            perms = await perm_service.get_user_permissions(uid_int)
            allowed_ids = perms.permissions.agents or []
        except (ValueError, TypeError):
            allowed_ids = []
        return agent.id in allowed_ids

    @staticmethod
    async def resolve_embed_agent_access(
        session: AsyncSession,
        agent_key: str,
        user: Any = None,
    ) -> AIAgent:
        """
        Resolve EmbedChat ?agent_id= deep-link access.
        Raises LookupError when missing/disabled; PermissionError when unauthorized.
        """
        key = str(agent_key or "").strip()
        if not key:
            raise LookupError("agent_not_found")

        stmt = select(AIAgent).where(or_(AIAgent.id == key, AIAgent.name == key)).limit(1)
        agent = (await session.execute(stmt)).scalar_one_or_none()
        if not agent or not agent.is_enabled:
            raise LookupError("agent_not_found")

        if not await AgentManagerService._user_can_execute_agent(session, agent, user):
            raise PermissionError("agent_forbidden")
        return agent

    @staticmethod
    async def list_allowed_agents(
        session: AsyncSession,
        user: Any = None,
        keyword: Optional[str] = None,
        include_disabled: bool = False,
    ) -> List[Any]:
        """
        List ONLY agents that the user has permission to EXECUTE/CHAT with.
        - Admin: All
        - User: System Agents + Owned Agents + Permitted Agents

        ``include_disabled=True`` 会一并返回已停用的智能体，仅用于历史数据筛选
        （例如聊天日志按智能体过滤），不应作为「可选用智能体」来源。
        """
        if not user:
            return []

        # User Info extraction
        is_admin, username, user_id = AgentManagerService._extract_user_identity(user)

        stmt = select(AIAgent)
        if not include_disabled:
            stmt = stmt.where(AIAgent.is_enabled == True)

        if not is_admin:
            # Normal User Logic
            from app.services.permission_service import PermissionService
            perm_service = PermissionService(session)
            try:
                uid_int = int(user_id)
                perms = await perm_service.get_user_permissions(uid_int)
                allowed_ids = perms.permissions.agents
            except (ValueError, TypeError):
                allowed_ids = []

            from sqlalchemy import and_
            stmt = stmt.where(
                or_(
                    and_(AIAgent.is_system == True, AIAgent.id.in_(allowed_ids)),
                    and_(AIAgent.is_system == False, AIAgent.created_by == username)
                )
            )
        
        # Keyword Filter
        if keyword:
            stmt = stmt.where(or_(
                AIAgent.name.ilike(f"%{keyword}%"),
                AIAgent.display_name.ilike(f"%{keyword}%")
            ))

        # Main is the fixed entry point and must always appear first.
        main_first = case(
            (or_(AIAgent.id == MAIN_GENERAL_AGENT_ID, AIAgent.name.in_(MAIN_GENERAL_AGENT_NAMES)), 0),
            else_=1,
        )
        stmt = stmt.order_by(main_first, AIAgent.sort_order.desc(), AIAgent.is_system.desc(), AIAgent.display_name)

        return list((await session.execute(stmt)).scalars().all())

    @classmethod
    async def attach_bound_metadata_dataset_counts(
        cls,
        session: AsyncSession,
        agents: List[Any],
    ) -> List[Any]:
        """给智能体对象附加「已发布版本里 get_dataset_schema 绑定的数据集数量」。

        ``/agents/allowed`` 直接返回 ORM 对象（不像 ``list_agents`` 走
        ``summarize_version_capabilities``），前端拿不到该字段就无法在卡片与输入框
        上方提示「这个智能体绑定了数据集」。这里按已发布版本一次性补齐，避免前端
        逐条回查；未绑定或无已发布版本一律为 None（表示不限定）。
        """
        agent_list = [agent for agent in (agents or []) if getattr(agent, "id", None)]
        if not agent_list:
            return list(agents or [])

        versions = await cls._latest_published_versions_by_agent(
            session, [str(agent.id) for agent in agent_list]
        )
        for agent in agent_list:
            version = versions.get(agent.id)
            count: Optional[int] = None
            if version is not None:
                tools_raw = getattr(version, "tools", None) or []
                if isinstance(tools_raw, str):
                    try:
                        tools_raw = json.loads(tools_raw)
                    except Exception:
                        tools_raw = []
                if not isinstance(tools_raw, list):
                    tools_raw = []
                count = cls._count_bound_metadata_datasets(tools_raw)
            setattr(agent, "metadata_dataset_count", count)
        return list(agents or [])

    @staticmethod
    async def attach_published_model_switch_flags(
        session: AsyncSession,
        agents: List[Any],
    ) -> List[Any]:
        """
        给智能体对象附加已发布版本的 allow_model_switch（非独立查询接口使用）。

        - 取每个智能体版本号最大的 PUBLISHED 版本；无已发布版本时按 True（允许切换）处理。
        - 失败/缺失一律降级为允许，保证历史数据与未迁移环境的行为与旧版一致。
        """
        agent_list = [agent for agent in (agents or []) if getattr(agent, "id", None)]
        if not agent_list:
            return list(agents or [])

        flags: Dict[str, bool] = {}
        try:
            rows = await session.execute(
                select(AIAgentVersion.agent_id, AIAgentVersion.allow_model_switch)
                .where(
                    AIAgentVersion.agent_id.in_([agent.id for agent in agent_list]),
                    AIAgentVersion.status == "PUBLISHED",
                )
                .order_by(AIAgentVersion.agent_id, AIAgentVersion.version_number.desc())
            )
            for agent_id, allow in rows.all():
                # order_by 已保证每个 agent 首次出现即为版本号最大的已发布版本
                flags.setdefault(agent_id, True if allow is None else bool(allow))
        except OperationalError as e:
            if "allow_model_switch" in str(e):
                logger.warning(
                    "读取 ai_agent_versions.allow_model_switch 失败：数据库缺少该列，"
                    "请执行迁移 db-prod/V166 或 db-prod-pg/V67；本次按允许手动切换模型处理。"
                )
            else:
                logger.warning(f"读取智能体版本模型切换开关失败，按允许处理: {e}")
        except Exception as e:  # pragma: no cover - 防御性降级，不影响主流程
            logger.warning(f"读取智能体版本模型切换开关失败，按允许处理: {e}")

        for agent in agent_list:
            setattr(agent, "allow_model_switch", flags.get(agent.id, True))
        return list(agents or [])

    @staticmethod
    async def published_agent_model_name(
        session: AsyncSession,
        agent_id: Optional[str],
        version_id: Optional[str] = None,
    ) -> str:
        """返回该智能体「已发布版本」配置的编排模型名；查不到返回空串。

        仅供聊天模型白名单的豁免判断使用：白名单限制用户手动切换，但不能连
        智能体自己指定的模型都拦掉。异常一律降级为空串，不阻断聊天主流程。
        """
        if not agent_id:
            return ""
        try:
            if version_id:
                stmt = select(AIAgentVersion.model_name).where(
                    AIAgentVersion.id == version_id
                )
            else:
                stmt = (
                    select(AIAgentVersion.model_name)
                    .where(
                        AIAgentVersion.agent_id == agent_id,
                        AIAgentVersion.status == "PUBLISHED",
                    )
                    .order_by(AIAgentVersion.version_number.desc())
                    .limit(1)
                )
            row = (await session.execute(stmt)).first()
            return str(row[0] or "").strip() if row else ""
        except Exception as exc:  # noqa: BLE001 - 豁免查询失败不应阻断聊天
            logger.warning(
                "Failed to resolve published model for agent %s: %s", agent_id, exc
            )
            return ""

    @staticmethod
    async def create_agent(session: AsyncSession, data: AIAgentBase, user: Any = None) -> AIAgent:
        """Create a new agent metadata entry"""
        AgentManagerService._reject_manual_system_flag(data)

        if isinstance(user, dict):
            created_by = user.get('user_name')
            is_admin = user.get('role', '') == 'admin'
        else:
            created_by = getattr(user, 'user_name', None) if user else None
            is_admin = user and getattr(user, 'role', '') == 'admin'

        AgentManagerService._validate_engine_config(data.engine_type, data.engine_config)
        
        # 物理标识符在 ai_agents.name 上全局唯一，命中时给出中文可操作提示
        conflict = await AgentManagerService.find_agent_name_conflict(session, data.name)
        if conflict:
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail=_agent_name_conflict_detail(data.name))

        from app.services.ai.agent_types import (
            normalize_agent_capabilities_for_agent,
            resolve_agent_type_for_engine,
        )

        resolved_agent_type = resolve_agent_type_for_engine(data.engine_type, data.agent_type)
        new_agent_id = str(uuid.uuid4())
        agent = AIAgent(
            id=new_agent_id,
            name=data.name,
            display_name=data.display_name,
            description=data.description,
            # 新建流程先传头像再建智能体，这里把待绑定文件转正，避免被 TTL 回收
            avatar_url=adopt_pending_avatar(data.avatar_url, new_agent_id),
            capabilities=normalize_agent_capabilities_for_agent(
                engine_type=data.engine_type,
                agent_type=resolved_agent_type,
                values=data.capabilities,
            ),
            agent_type=resolved_agent_type.value,
            # 系统级只能通过「发布为系统级」获得（有事件、有流程），新建表单不再接受该字段
            is_system=False,
            sort_order=data.sort_order if data.sort_order is not None else 0,
            is_enabled=data.is_enabled if data.is_enabled is not None else True,
            created_by=created_by,
            engine_type=data.engine_type,
            engine_config=data.engine_config
        )
        session.add(agent)
        await session.commit()
        await session.refresh(agent)
        
        # Invalidate Router Cache
        from app.services.ai.router_service import router_service
        router_service.invalidate_cache()
        
        return agent

    @staticmethod
    async def duplicate_agent(
        session: AsyncSession,
        agent_id: str,
        data: Any,
        user: Any = None,
    ) -> Optional[AIAgent]:
        """把源智能体复制为一个全新的、立即可用的智能体。

        复制范围：
        - 元数据整体复制，但 ``is_system`` 强制 False、``created_by`` 归当前操作人；
        - 版本取源最新 PUBLISHED 版本（无已发布版本时退化为版本号最大的那个），
          复制为新智能体的 1 号版本并 **直接置 PUBLISHED**，保证副本可立即对话；
        - 源无任何版本记录时（RAGFLOW / OPENCLAW 引擎）只建元数据，不硬造版本。
        """
        import copy
        from fastapi import HTTPException

        source = await session.get(AIAgent, agent_id)
        if not source:
            return None

        is_admin, username = AgentManagerService._resolve_actor(user)

        # 复制会把提示词、工具/MCP 绑定、模型参数整份带走，所以只要不是自己的就必须拦。
        # 原先只拦「别人的系统级」，别人的普通智能体可以整份复制走——而列表口径早已
        # 把「别人的智能体」定义为不可见，这两处自相矛盾。id 可能出现在对话 / Embed /
        # 分享链接里，接口必须自己挡，不能指望界面上看不到。
        if not is_admin and source.created_by != username:
            if source.is_system:
                raise HTTPException(status_code=403, detail="系统智能体由管理员维护，不能复制")
            raise HTTPException(status_code=403, detail="只能复制自己创建的智能体")

        new_name = AgentManagerService.normalize_agent_name(getattr(data, "name", None))
        if not new_name:
            raise ValueError("物理标识符不能为空")

        # 物理标识符在 ai_agents.name 上全局唯一，复用创建路径同一套判定与中文提示
        conflict = await AgentManagerService.find_agent_name_conflict(session, new_name)
        if conflict:
            raise HTTPException(status_code=400, detail=_agent_name_conflict_detail(new_name))

        new_display_name = str(getattr(data, "display_name", "") or "").strip() or new_name
        new_agent_id = str(uuid.uuid4())

        agent = AIAgent(
            id=new_agent_id,
            name=new_name,
            display_name=new_display_name,
            description=source.description,
            # 直接复用同一头像 URL；此处不能走 adopt_pending_avatar，
            # 否则会把源智能体正在使用的头像当作待绑定文件移走
            avatar_url=source.avatar_url,
            capabilities=(
                list(source.capabilities)
                if isinstance(source.capabilities, list)
                else source.capabilities
            ),
            agent_type=source.agent_type,
            onboarding_step="COMPLETE",
            is_system=False,  # 复制品永远不是系统智能体
            sort_order=(source.sort_order or 0) + 1,  # 紧邻源智能体而非沉到末尾
            is_enabled=True,
            created_by=username or None,
            engine_type=source.engine_type,
            engine_config=(
                copy.deepcopy(source.engine_config)
                if source.engine_config
                else source.engine_config
            ),
            owner_group=source.owner_group,
        )
        session.add(agent)

        source_version = await AgentManagerService._pick_duplicate_source_version(
            session, source.id
        )
        # 复制会把源版本的工具引用整份带走，但「源的所有者能用」不等于「复制人能用」：
        # 管理员用自己私有 MCP 服务的智能体被别人复制后，那些引用对复制人永久不可见，
        # 工具选择器里既看不到也取消不了，只能在列表页看到一堆「无权访问」告警。
        # 这里提前剔掉，副本从创建那一刻就是干净可用的配置。
        # 只剔 server_forbidden：服务被临时禁用/工具下线是源侧的状态问题，副本照旧保留，
        # 否则复制一次就悄悄丢配置，用户更难察觉。
        copied_tools = copy.deepcopy(source_version.tools) if source_version else None
        copied_tools, dropped_tools = await AgentManagerService.drop_inaccessible_mcp_tools(
            session, copied_tools, user
        )

        if source_version:
            session.add(
                AIAgentVersion(
                    id=str(uuid.uuid4()),
                    agent_id=new_agent_id,
                    version_number=1,
                    model_name=source_version.model_name,
                    temperature=source_version.temperature,
                    synthesis_model_name=source_version.synthesis_model_name,
                    synthesis_temperature=source_version.synthesis_temperature,
                    allow_model_switch=bool(getattr(source_version, "allow_model_switch", True)),
                    system_prompt=source_version.system_prompt,
                    # JSON 字段必须深拷贝，避免两个 ORM 实例共享同一可变对象
                    tools=copied_tools,
                    toolcall_timeout_seconds=source_version.toolcall_timeout_seconds,
                    skills_custom=source_version.skills_custom,
                    skills=copy.deepcopy(source_version.skills),
                    welcome_config=copy.deepcopy(source_version.welcome_config),
                    status="PUBLISHED",  # 复制的是"已发布的配置"，副本因此立即可用
                    comment=(
                        f"复制自 {source.display_name or source.name} "
                        f"v{source_version.version_number}"
                    ),
                )
            )

        await session.commit()
        await session.refresh(agent)

        from app.services.ai.router_service import router_service

        router_service.invalidate_cache()

        # 供接口层回带给前端：「有 N 个工具因为你无权使用而没有复制过来」
        agent.dropped_tool_refs = dropped_tools
        return agent

    @staticmethod
    async def _pick_duplicate_source_version(
        session: AsyncSession,
        agent_id: str,
    ) -> Optional[AIAgentVersion]:
        """挑选复制来源版本：优先最新 PUBLISHED，其次版本号最大的任意版本。"""
        result = await session.execute(
            select(AIAgentVersion)
            .where(AIAgentVersion.agent_id == agent_id)
            .order_by(
                case((AIAgentVersion.status == "PUBLISHED", 0), else_=1),
                AIAgentVersion.version_number.desc(),
            )
            .limit(1)
        )
        return result.scalars().first()

    @staticmethod
    async def reorder_agents(session: AsyncSession, items: List[Any], user: Any = None) -> bool:
        """Batch update sort_order for agents (admin only)."""
        if isinstance(user, dict):
            is_admin = user.get('role', '') == 'admin'
        else:
            is_admin = user and getattr(user, 'role', '') == 'admin'

        if not is_admin:
            return False

        ids = [item.id for item in items]
        if not ids:
            return True

        stmt = select(AIAgent).where(AIAgent.id.in_(ids))
        result = await session.execute(stmt)
        agents = {agent.id: agent for agent in result.scalars().all()}

        if any(_is_main_general_agent_record(agent) for agent in agents.values()):
            return False

        for item in items:
            agent = agents.get(item.id)
            if agent:
                agent.sort_order = item.sort_order

        await session.commit()

        from app.services.ai.router_service import router_service
        router_service.invalidate_cache()

        return True

    @staticmethod
    def normalize_agent_name(raw: Any) -> str:
        """物理标识符归一化：仅去首尾空白。

        标识符是路由与展示用的 slug，带首尾空白没有意义，且 `ai_agents.name`
        是 `String(100)` 唯一索引——预检与创建必须使用同一套归一化口径，
        否则会出现「预检说可用、创建却撞名」。
        """
        return str(raw or "").strip()

    @classmethod
    async def find_agent_name_conflict(
        cls,
        session: AsyncSession,
        name: Any,
        *,
        exclude_agent_id: Optional[str] = None,
    ) -> Optional[AIAgent]:
        """返回占用了该物理标识符的智能体，没有冲突则返回 None。

        创建前预检（`GET /agents/name-availability`）与创建时的兜底校验共用这里，
        保证两条路径判定完全一致。`exclude_agent_id` 用于「改自己名字」的场景。
        """
        normalized = cls.normalize_agent_name(name)
        if not normalized:
            return None
        query = select(AIAgent).where(AIAgent.name == normalized)
        if exclude_agent_id:
            query = query.where(AIAgent.id != exclude_agent_id)
        result = await session.execute(query)
        return result.scalars().first()

    @staticmethod
    def _resolve_actor(user: Any) -> tuple[bool, str]:
        """统一解析调用者身份：返回 (是否管理员, 用户名)。"""
        if isinstance(user, dict):
            return user.get("role", "") == "admin", user.get("user_name", "") or ""
        if not user:
            return False, ""
        return getattr(user, "role", "") == "admin", getattr(user, "user_name", "") or ""

    @staticmethod
    def can_edit_agent_meta(agent: AIAgent, user: Any) -> bool:
        """智能体元数据（名称/描述/头像等）是否可编辑。

        规则：管理员全量可编辑；其余人只能编辑**自己创建的、且非系统内置**的智能体。
        该判定必须与 `PUT /api/portal/agents/{id}` 完全一致，否则会出现
        「能改智能体名称却不能换头像」这类权限漂移。
        """
        is_admin, username = AgentManagerService._resolve_actor(user)
        if is_admin:
            return True
        # created_by 为空（历史/导入数据）时不能「人人可改」：这类智能体在列表里
        # 对所有人都不可见，写权限跟着 fail-open 就成了「看不见但随便改」。
        if agent.created_by != username:
            return False
        if agent.is_system:
            return False
        return True

    @staticmethod
    async def get_agent_for_edit(session: AsyncSession, agent_id: str, user: Any = None) -> Optional[AIAgent]:
        """取出可编辑的智能体；不存在或无权编辑时返回 None。"""
        agent = await session.get(AIAgent, agent_id)
        if not agent or not AgentManagerService.can_edit_agent_meta(agent, user):
            return None
        return agent

    @staticmethod
    async def update_agent(session: AsyncSession, agent_id: str, data: AIAgentBase, user: Any = None) -> Optional[AIAgent]:
        """Update existing agent metadata"""
        agent = await session.get(AIAgent, agent_id)
        if not agent:
            return None

        # Permission Check（与头像上传端点共用同一判定，避免权限漂移）
        if not AgentManagerService.can_edit_agent_meta(agent, user):
            return None # Or raise Forbidden in Endpoint

        is_admin, _actor_username = AgentManagerService._resolve_actor(user)

        # 待审中的所有者不能改（管理员是审核方，豁免），否则审核看到的内容和上架内容不一致
        await AgentSystemPublicationService.assert_not_frozen(session, agent, user)

        # 表单回传当前值（编辑系统级时必然是 true）不算「修改」；只有真想改变它才拦。
        # 主助手固定是系统级，豁免——它没有「取消」这条路径。
        if (
            data.is_system is not None
            and bool(data.is_system) != bool(agent.is_system)
            and not _is_main_general_agent_record(agent)
        ):
            from fastapi import HTTPException

            raise HTTPException(
                status_code=400,
                detail=(
                    "「是否系统级」不能通过编辑修改：请用卡片菜单的「发布为系统级」、「下架（取消系统级）」，"
                    "或提交发布申请由管理员审核"
                ),
            )

        if _is_main_general_agent_record(agent) and data.is_enabled is False:
            raise ValueError("主助手不可禁用")

        original_engine_type = str(agent.engine_type or "LOCAL").upper()
        if "engine_type" in data.model_fields_set:
            requested_engine_type = str(data.engine_type or original_engine_type).upper()
            if requested_engine_type != original_engine_type:
                raise ValueError("执行引擎创建后不可修改；请新建智能体以使用其他引擎")

        # 改名前先校验唯一性（排除自己），否则唯一索引会抛 IntegrityError 变成 500
        if AgentManagerService.normalize_agent_name(data.name) != AgentManagerService.normalize_agent_name(agent.name):
            conflict = await AgentManagerService.find_agent_name_conflict(
                session, data.name, exclude_agent_id=agent.id
            )
            if conflict:
                from fastapi import HTTPException

                raise HTTPException(status_code=400, detail=_agent_name_conflict_detail(data.name))

        _before_meta = {
            "name": agent.name,
            "display_name": agent.display_name,
            "description": agent.description,
            "avatar_url": agent.avatar_url,
            "capabilities": agent.capabilities,
            "sort_order": agent.sort_order,
        }

        agent.name = data.name
        agent.display_name = data.display_name
        agent.description = data.description
        agent.avatar_url = adopt_pending_avatar(data.avatar_url, agent.id)
        from app.services.ai.agent_types import (
            normalize_agent_capabilities_for_agent,
            resolve_agent_type_for_engine,
        )

        # Primary type defines runtime gates and delegation semantics. It is
        # immutable after creation for LOCAL engines; RAGFlow/OpenClaw stay GENERAL.
        original_agent_type = agent.agent_type or "GENERAL"
        resolved_agent_type = resolve_agent_type_for_engine(agent.engine_type, original_agent_type)
        agent.agent_type = resolved_agent_type.value
        agent.capabilities = normalize_agent_capabilities_for_agent(
            engine_type=agent.engine_type,
            agent_type=resolved_agent_type,
            values=data.capabilities,
        )
        agent.sort_order = data.sort_order if data.sort_order is not None else agent.sort_order
        
        # 「是否系统级」不再接受表单设置：只能走「发布为系统级」或审核通过，
        # 这样每次上架都有事件、有流程可查。主助手固定是系统级，保持原样。
        if _is_main_general_agent_record(agent):
            agent.is_system = True

        _enabled_changed = data.is_enabled is not None and bool(data.is_enabled) != bool(agent.is_enabled)
        if data.is_enabled is not None:
            agent.is_enabled = data.is_enabled
        
        # Engine implementation is immutable after creation. Connection and
        # runtime parameters remain editable through engine_config.
        if data.engine_config is not None:
            agent.engine_config = data.engine_config

        # 系统级智能体的元数据变更与启用/禁用都要留痕（普通智能体不记，避免噪音）
        _changed_meta = [
            field for field, previous in _before_meta.items() if getattr(agent, field) != previous
        ]
        if _enabled_changed:
            await AgentSystemPublicationService.record_enabled_change(
                session, agent, bool(agent.is_enabled), user
            )
        if _changed_meta:
            await AgentSystemPublicationService.record_meta_update(
                session, agent, user, _changed_meta
            )

        await session.commit()
        await session.refresh(agent)
        
        # Invalidate Router Cache
        from app.services.ai.router_service import router_service
        router_service.invalidate_cache()
        
        return agent

    @staticmethod
    async def delete_agent(session: AsyncSession, agent_id: str, user: Any = None) -> bool:
        """Delete an agent (system agents cannot be deleted)"""
        agent = await session.get(AIAgent, agent_id)
        if not agent or agent.is_system or _is_main_general_agent_record(agent):
            return False

        # Permission Check
        is_admin, username = AgentManagerService._resolve_actor(user)

        if not is_admin and agent.created_by != username:
            return False

        # 待审期间不能删：申请单还指着一个不存在的智能体，审核页会打不开
        await AgentSystemPublicationService.assert_not_frozen(session, agent, user)

        await session.delete(agent)
        await session.commit()
        
        # Invalidate Router Cache
        from app.services.ai.router_service import router_service
        router_service.invalidate_cache()
        
        return True

    @staticmethod
    async def get_agent_versions(session: AsyncSession, agent_id: str, user: Any = None) -> List[AIAgentVersion]:
        """List all versions for a specific agent (checks visibility)"""
        agent = await session.get(AIAgent, agent_id)
        if not agent:
            return []

        # 版本正文里有完整的 system_prompt / tools / skills，草稿更是没上线的半成品。
        # 原先这里没有任何校验（注释还写着 All users can see），任意登录用户凭
        # agent_id 就能把别人的每一版提示词读走。按「能不能用这个智能体」判定：
        # 管理员、所有者、以及被授权的系统级使用者可见。
        if not await AgentManagerService._can_view_agent_versions(session, agent, user):
            return []

        query = select(AIAgentVersion).where(AIAgentVersion.agent_id == agent_id).order_by(AIAgentVersion.version_number.desc())
        result = await session.execute(query)
        return result.scalars().all()

    @staticmethod
    async def _can_view_agent_versions(
        session: AsyncSession, agent: AIAgent, user: Any
    ) -> bool:
        """谁可以读这个智能体的版本配置。

        不能直接复用 ``_user_can_execute_agent``：它要求系统级智能体「已被授权
        且启用」，那样所有者反而看不到自己刚建的版本。
        """
        if not user or not agent:
            return False
        is_admin, username, user_id = AgentManagerService._extract_user_identity(user)
        if is_admin:
            return True
        # 所有者：自己的智能体（含系统级时自己改草稿）
        if username and agent.created_by == username:
            return True
        if not agent.is_system:
            return False
        # 系统级：被授权使用的普通用户也可以看（调试需要看当前版本配置）
        from app.services.permission_service import PermissionService

        try:
            perms = await PermissionService(session).get_user_permissions(int(user_id))
            allowed_ids = perms.permissions.agents or []
        except (ValueError, TypeError):
            allowed_ids = []
        return agent.id in allowed_ids

    @staticmethod
    def _normalize_tools_for_db(tools: List[Any]) -> List[Any]:
        """
        Converts ToolConfigItem objects to plain dictionaries for JSON serialization.
        """
        if not tools:
            return []
        
        normalized = []
        for item in tools:
            if hasattr(item, "model_dump"): # Pydantic v2
                normalized.append(item.model_dump(exclude_none=True))
            elif hasattr(item, "dict"): # Pydantic v1
                normalized.append(item.dict(exclude_none=True))
            else:
                normalized.append(item)
        return normalized

    @staticmethod
    def _normalize_skills_list(skills: Any) -> List[str]:
        """Normalize skills JSON (list/str) into a clean list of skill ids."""
        if not skills:
            return []
        if isinstance(skills, str):
            try:
                skills = json.loads(skills)
            except Exception:
                return []
        if not isinstance(skills, list):
            return []
        result: List[str] = []
        for item in skills:
            skill_id = str(item or "").strip()
            if skill_id and skill_id not in result:
                result.append(skill_id)
        return result

    @staticmethod
    def _validate_skills_config(skills_custom: bool, skills: List[str]) -> None:
        if skills_custom and not skills:
            raise ValueError("自定义 Skills 开启时至少选择一个公共技能")

    @staticmethod
    async def create_agent_version(session: AsyncSession, agent_id: str, data: AIAgentVersionBase, user: Any = None) -> Optional[AIAgentVersion]:
        """Create a new version for an agent (checks ownership)"""
        agent = await session.get(AIAgent, agent_id)
        if not agent:
            return None

        # Permission Check
        if isinstance(user, dict):
            is_admin = user.get('role', '') == 'admin'
            username = user.get('user_name', '')
        else:
            is_admin = user and getattr(user, 'role', '') == 'admin'
            username = user and getattr(user, 'user_name', '')

        if not is_admin and agent.created_by != username:
            return None # Forbidden

        # 系统级允许所有者建草稿版本，但发布仍被 publish_version 拦死。
        # 待审期间同样**允许**建版本：建草稿不碰线上，把它一起冻住等于要求用户
        # 提交申请后彻底停工，而他往往正是想在这期间继续调。真正要拦的是发布。

        skills_custom = bool(getattr(data, "skills_custom", False))
        skills_list = AgentManagerService._normalize_skills_list(getattr(data, "skills", None)) if skills_custom else []
        AgentManagerService._validate_skills_config(skills_custom, skills_list)

        # Determine next version number
        query = select(AIAgentVersion.version_number).where(AIAgentVersion.agent_id == agent_id).order_by(AIAgentVersion.version_number.desc()).limit(1)
        prev_result = await session.execute(query)
        prev_v = prev_result.scalar_one_or_none()
        next_v = (prev_v or 0) + 1

        version = AIAgentVersion(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            version_number=next_v,
            model_name=data.model_name,
            temperature=data.temperature,
            synthesis_model_name=data.synthesis_model_name,
            synthesis_temperature=data.synthesis_temperature,
            allow_model_switch=bool(getattr(data, "allow_model_switch", True)),
            system_prompt=data.system_prompt,
            tools=AgentManagerService._normalize_tools_for_db(data.tools),
            toolcall_timeout_seconds=data.toolcall_timeout_seconds,
            skills_custom=skills_custom,
            skills=skills_list,
            welcome_config=normalize_welcome_config(getattr(data, "welcome_config", None)),
            status="DRAFT",
            comment=data.comment
        )
        session.add(version)
        await session.commit()
        await session.refresh(version)
        
        # Invalidate Router Cache
        from app.services.ai.router_service import router_service
        router_service.invalidate_cache()
        
        return version


    @staticmethod
    async def _assert_version_not_pending_release(
        session: AsyncSession, version_id: str
    ) -> None:
        """正在申请上线的版本不能被改写或删除。

        审批通过时是按 ``snapshot_version_id`` 现读内容发布的：
        - 改写 → 管理员审的是快照，上线的是改后内容；
        - 删除 → 审批时报 409，而申请单仍停在 PENDING，智能体被
          ``PENDING_REVIEW`` 永久锁住发布，只能人工驳回或撤回才能解开。

        未迁移环境没有这张表，此时应当放行普通草稿操作。
        """
        from fastapi import HTTPException

        from app.models.agent_system import AgentSystemRequest

        try:
            pending = await session.execute(
                select(AgentSystemRequest.id)
                .where(
                    AgentSystemRequest.snapshot_version_id == str(version_id),
                    AgentSystemRequest.status == "PENDING",
                )
                .limit(1)
            )
            has_pending = pending.scalars().first() is not None
        except OperationalError:
            return
        if has_pending:
            raise HTTPException(
                status_code=409,
                detail="该版本正在审核中，暂不能修改或删除；如需调整请新建一个版本再提交申请",
            )

    @staticmethod
    async def update_agent_version(session: AsyncSession, agent_id: str, version_id: str, data: AIAgentVersionBase, user: Any = None) -> Optional[AIAgentVersion]:
        """Update an existing DRAFT version (checks ownership)"""
        agent = await session.get(AIAgent, agent_id)
        version = await session.get(AIAgentVersion, version_id)
        if not agent or not version:
            return None

        # 版本必须属于路径里的这个智能体。少了这一句，攻击者拿自己任何一个智能体的
        # id 就能改别人的草稿版本（归属校验只看了 agent，没看 version）。
        # publish_version 一直有这个判断，改/删两处是漏的。
        if str(version.agent_id) != str(agent_id):
            return None

        # Only DRAFT versions can be modified
        if version.status != "DRAFT":
            return None

        # Permission Check
        if isinstance(user, dict):
            is_admin = user.get('role', '') == 'admin'
            username = user.get('user_name', '')
        else:
            is_admin = user and getattr(user, 'role', '') == 'admin'
            username = user and getattr(user, 'user_name', '')

        if not is_admin and agent.created_by != username:
            return None # Forbidden

        # 系统级允许改草稿（不发布就不影响线上基线），发布在 publish_version 单独拦。
        #
        # 但「正在申请上线的那个版本」不能改：审批通过时是按 version_id 现读内容发布的，
        # 待审期间整份改写等于让管理员审的是 A、上线的是 B，审核就白做了。
        # 只冻这一版，建新版本、改别的草稿都不受影响。
        await AgentManagerService._assert_version_not_pending_release(session, version_id)

        skills_custom = bool(getattr(data, "skills_custom", False))
        skills_list = (
            AgentManagerService._normalize_skills_list(getattr(data, "skills", None))
            if skills_custom
            else []
        )
        AgentManagerService._validate_skills_config(skills_custom, skills_list)

        # Update fields
        version.model_name = data.model_name or version.model_name
        version.temperature = data.temperature if data.temperature is not None else version.temperature
        version.synthesis_model_name = data.synthesis_model_name
        version.synthesis_temperature = data.synthesis_temperature
        # 新字段允许旧客户端省略；只有显式出现在请求里才写，避免把「禁止切换」静默改回允许。
        if "allow_model_switch" in data.model_fields_set:
            version.allow_model_switch = bool(data.allow_model_switch)
        version.system_prompt = data.system_prompt
        version.tools = AgentManagerService._normalize_tools_for_db(data.tools)
        # 新字段允许旧客户端省略；只有显式出现在请求中的 null 才代表清除版本级覆盖。
        if "toolcall_timeout_seconds" in data.model_fields_set:
            version.toolcall_timeout_seconds = data.toolcall_timeout_seconds
        version.skills_custom = skills_custom
        version.skills = skills_list
        # Keep an existing version setting when an older client omits this newly added field.
        if data.welcome_config is not None:
            version.welcome_config = normalize_welcome_config(data.welcome_config)
        version.comment = data.comment or version.comment
        if agent.onboarding_step == "VERSION":
            agent.onboarding_step = "RESOURCE"
        
        await session.commit()
        await session.refresh(version)
        
        # Invalidate Router Cache
        from app.services.ai.router_service import router_service
        router_service.invalidate_cache()
        
        return version

    @staticmethod
    async def publish_version(session: AsyncSession, agent_id: str, version_id: str, user: Any = None) -> bool:
        """Publish a specific version and archive previous active ones (checks ownership)"""
        agent = await session.get(AIAgent, agent_id)
        if not agent:
            return False

        # Permission Check
        if isinstance(user, dict):
            is_admin = user.get('role', '') == 'admin'
            username = user.get('user_name', '')
        else:
            is_admin = user and getattr(user, 'role', '') == 'admin'
            username = user and getattr(user, 'user_name', '')

        if not is_admin and agent.created_by != username:
            return False # Forbidden

        # 系统级内容只读：所有者不能发布新版本，否则等于绕过审核改线上基线
        if agent.is_system and not is_admin:
            return False

        # 待审期间所有者不能发布新版本（管理员豁免）
        await AgentSystemPublicationService.assert_not_frozen(session, agent, user)

        version = await session.get(AIAgentVersion, version_id)
        if not version or str(version.agent_id) != str(agent_id):
            return False

        from app.services.ai.agent_readiness import evaluate_agent_readiness
        from app.services.ai.knowledge_utils import load_system_default_dataset_ids

        readiness = evaluate_agent_readiness(
            agent_type=agent.agent_type or "GENERAL",
            capabilities=agent.capabilities,
            engine_config=agent.engine_config,
            tools=version.tools,
            # The target version becomes the published version in this transaction.
            has_published_version=True,
            # 内置知识库助手不绑定 per-agent 数据集，运行时回退系统级默认数据集；
            # 发布校验必须认同一口径，否则它永远无法重新发布。
            default_dataset_ids=await load_system_default_dataset_ids(),
        )
        if not readiness.ready:
            raise AgentNotReadyError(readiness.missing)

        # 1. Archive current PUBLISHED versions for this agent
        await session.execute(
            update(AIAgentVersion)
            .where(AIAgentVersion.agent_id == agent_id, AIAgentVersion.status == 'PUBLISHED')
            .values(status='ARCHIVED')
        )
        
        # 2. Set target version to PUBLISHED
        await session.execute(
            update(AIAgentVersion)
            .where(AIAgentVersion.id == version_id)
            .values(status='PUBLISHED')
        )
        agent.onboarding_step = "COMPLETE"

        # 「发布新版本」换掉的是线上基线，不管是不是系统级都必须留痕：
        # 否则发布历史里版本号变了，却查不到是谁在什么时候换的。
        # 普通智能体尤其需要——它没有审核环节，发布完全由所有者自行决定。
        from app.services.ai.agent_system_publication_service import (
            ACTION_BASELINE_PUBLISH,
        )

        _, actor_name, actor_id = AgentSystemPublicationService.resolve_actor(user)
        await AgentSystemPublicationService.record_event(
            session,
            agent=agent,
            action=ACTION_BASELINE_PUBLISH,
            actor_name=actor_name,
            actor_id=actor_id,
            version=version,
        )

        await session.commit()

        # Published versions are the source of truth for the next turn. Clear
        # runtime tool-definition caches as well as the router metadata cache
        # so DB-backed MCP/API tools cannot remain stale after publication.
        from app.services.ai.tools.registry import ToolRegistry
        ToolRegistry.clear_db_tool_cache()
        
        # Invalidate Router Cache
        from app.services.ai.router_service import router_service
        router_service.invalidate_cache()
        
        return True

    @staticmethod
    async def save_agent_prompt(
        session: AsyncSession,
        agent_id: str,
        content: str,
        *,
        user: Any = None,
        note: Optional[str] = None,
    ) -> Optional[str]:
        """把「系统提示词」存进智能体版本，返回被写入的 version_id。

        提示词工坊原先绕过这里，用裸 SQL 直接 UPDATE/INSERT ``ai_agent_versions``，
        于是所有权、冻结、字段完整性、缓存失效全部失效——任何有提示词编辑权限的人
        都能改别人的（包括系统级的）草稿，而且不留痕。

        统一收到这个方法里：
        * 有草稿就改草稿（``update_agent_version`` 自带归属与 DRAFT 校验）；
        * 没有草稿就从最新版本复制一份再改——**不能只写 system_prompt 就 INSERT**，
          否则 temperature / 工具 / 技能 / 欢迎语全落回数据库默认值，
          用户会得到「提示词一样、效果却变了」的版本，还查不出原因。
        """
        from app.schemas.agent import AIAgentVersionBase

        agent = await session.get(AIAgent, agent_id)
        if not agent:
            return None

        if isinstance(user, dict):
            is_admin = user.get("role", "") == "admin"
            username = user.get("user_name", "")
        else:
            is_admin = bool(user and getattr(user, "role", "") == "admin")
            username = getattr(user, "user_name", "") if user else ""

        if not is_admin and agent.created_by != username:
            # 明确区分「没权限」和「没变化」：都返回 None 的话，越权会被
            # 伪装成「未检测到内容变更」，用户和日志都看不出被拒了。
            raise PermissionError("无权修改该智能体的提示词")

        draft_row = await session.execute(
            select(AIAgentVersion)
            .where(
                AIAgentVersion.agent_id == agent_id,
                AIAgentVersion.status == "DRAFT",
            )
            .order_by(AIAgentVersion.version_number.desc())
            .limit(1)
        )
        draft = draft_row.scalars().first()

        if draft is not None:
            if (draft.system_prompt or "") == content and (
                note is None or (draft.comment or "") == note
            ):
                return None  # 内容没变，不必制造一个「保存成功」

            payload = AIAgentVersionBase.model_validate(draft, from_attributes=True)
            payload.system_prompt = content
            if note is not None:
                payload.comment = note
            updated = await AgentManagerService.update_agent_version(
                session, agent_id, draft.id, payload, user=user
            )
            return updated.id if updated else None

        # 没有草稿：复制最新版本再改，保证除提示词外的一切都跟当前版本一致
        latest_row = await session.execute(
            select(AIAgentVersion)
            .where(AIAgentVersion.agent_id == agent_id)
            .order_by(AIAgentVersion.version_number.desc())
            .limit(1)
        )
        latest = latest_row.scalars().first()
        if latest is None:
            return None

        payload = AIAgentVersionBase.model_validate(latest, from_attributes=True)
        payload.version_number = None
        payload.status = "DRAFT"
        payload.system_prompt = content
        payload.comment = note if note is not None else "提示词工坊修改"
        created = await AgentManagerService.create_agent_version(
            session, agent_id, payload, user=user
        )
        return created.id if created else None

    @staticmethod
    async def delete_version(session: AsyncSession, agent_id: str, version_id: str, user: Any = None) -> bool:
        """Delete an agent version (only DRAFT or ARCHIVED)"""
        agent = await session.get(AIAgent, agent_id)
        version = await session.get(AIAgentVersion, version_id)

        if not agent or not version:
            return False

        # 同 update_agent_version：版本必须属于这个智能体，否则等于允许跨智能体删除
        if str(version.agent_id) != str(agent_id):
            return False

        # Cannot delete PUBLISHED version
        if version.status == "PUBLISHED":
            return False

        # 正在申请上线的版本同样不能删（否则申请单变孤儿、智能体被审核状态锁死）
        await AgentManagerService._assert_version_not_pending_release(session, version_id)

        # Permission Check
        if isinstance(user, dict):
            is_admin = user.get('role', '') == 'admin'
            username = user.get('user_name', '')
        else:
            is_admin = user and getattr(user, 'role', '') == 'admin'
            username = user and getattr(user, 'user_name', '')

        if not is_admin and agent.created_by != username:
            return False # Forbidden

        # 系统级允许删草稿；已发布版本由下面的状态校验挡住。

        await session.delete(version)
        await session.commit()
        
        # Invalidate Router Cache
        from app.services.ai.router_service import router_service
        router_service.invalidate_cache()
        
        return True

    @staticmethod
    async def get_skill_explicit_bindings(session: AsyncSession) -> Dict[str, Dict[str, Any]]:
        """
        反查公共 skill → 显式白名单绑定的智能体。
        仅 skills_custom=true；每智能体优先 DRAFT，否则 PUBLISHED。
        """
        rows = (
            await session.execute(
                select(AIAgentVersion, AIAgent)
                .join(AIAgent, AIAgent.id == AIAgentVersion.agent_id)
                .where(
                    AIAgentVersion.skills_custom.is_(True),
                    AIAgentVersion.status.in_(("DRAFT", "PUBLISHED")),
                )
            )
        ).all()

        chosen: Dict[str, tuple] = {}
        for version, agent in rows:
            existing = chosen.get(agent.id)
            if existing is None:
                chosen[agent.id] = (version, agent)
                continue
            existing_version = existing[0]
            # DRAFT 优先；同状态取更高版本号
            if version.status == "DRAFT" and existing_version.status != "DRAFT":
                chosen[agent.id] = (version, agent)
            elif (
                version.status == existing_version.status
                and int(version.version_number or 0) > int(existing_version.version_number or 0)
            ):
                chosen[agent.id] = (version, agent)

        bindings: Dict[str, Dict[str, Any]] = {}
        for version, agent in chosen.values():
            skill_ids = AgentManagerService._normalize_skills_list(getattr(version, "skills", None))
            if not skill_ids:
                continue
            agent_item = {
                "id": agent.id,
                "name": agent.display_name or agent.name,
                "version_status": version.status,
                "version_number": int(version.version_number or 0),
            }
            for skill_id in skill_ids:
                entry = bindings.setdefault(skill_id, {"count": 0, "agents": []})
                entry["agents"].append(agent_item)

        for entry in bindings.values():
            entry["agents"].sort(key=lambda a: (a["name"] or "").lower())
            entry["count"] = len(entry["agents"])

        return bindings
