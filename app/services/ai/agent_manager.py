import logging
import uuid
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError
from app.models.agent import AIAgent, AIAgentVersion
from app.schemas.agent import ChatConfig, AIAgentBase, AIAgentVersionBase
import json
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class AgentNotReadyError(ValueError):
    def __init__(self, missing: tuple[str, ...]):
        self.missing = missing
        super().__init__(f"Agent is not ready: {', '.join(missing)}")


@dataclass(frozen=True)
class AgentOnboardingResult:
    agent: AIAgent
    version: AIAgentVersion
    template_fallback: bool

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
    async def _resolve_onboarding_template(
        session: AsyncSession,
        agent_type: Any,
    ) -> Dict[str, Any]:
        type_value = getattr(agent_type, "value", agent_type)
        template_name = AgentManagerService._ONBOARDING_TEMPLATE_AGENT_NAMES[str(type_value)]
        config = await AgentManagerService.get_active_agent_config(
            session,
            agent_name=template_name,
        )
        if config:
            return {
                "model_name": config.model_name,
                "temperature": config.temperature or 0.0,
                "system_prompt": config.system_prompt,
                "tools": AgentManagerService._normalize_tools_for_db(config.tools),
                "skills_custom": bool(config.skills_custom),
                "skills": list(config.skills or []),
                "template_fallback": False,
            }

        from app.services.config_service import ConfigService

        fallback = AgentManagerService._ONBOARDING_FALLBACKS[str(type_value)]
        return {
            "model_name": await ConfigService.get("llm_model_name") or "DeepSeek-V3.2",
            "temperature": 0.0,
            "system_prompt": fallback["system_prompt"],
            "tools": list(fallback["tools"]),
            "skills_custom": False,
            "skills": [],
            "template_fallback": True,
        }

    @staticmethod
    async def create_agent_onboarding(
        session: AsyncSession,
        data: AIAgentBase,
        *,
        onboarding_key: str,
        user: Any = None,
    ) -> AgentOnboardingResult:
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
                )

        duplicate_name = await session.execute(select(AIAgent.id).where(AIAgent.name == data.name))
        if duplicate_name.scalar_one_or_none():
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail=f"Agent with ID/Name '{data.name}' already exists.")

        template = await AgentManagerService._resolve_onboarding_template(session, data.agent_type)
        from app.services.ai.agent_types import (
            normalize_agent_capabilities_for_agent,
            resolve_agent_type_for_engine,
        )

        resolved_agent_type = resolve_agent_type_for_engine(data.engine_type, data.agent_type)
        agent = AIAgent(
            id=str(uuid.uuid4()),
            name=data.name,
            display_name=data.display_name,
            description=data.description,
            avatar_url=data.avatar_url,
            capabilities=normalize_agent_capabilities_for_agent(
                engine_type=data.engine_type,
                agent_type=resolved_agent_type,
                values=data.capabilities,
            ),
            agent_type=resolved_agent_type.value,
            onboarding_key=onboarding_key,
            onboarding_step="VERSION",
            is_system=data.is_system if is_admin and data.is_system is not None else False,
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
        """从 get_dataset_schema 工具配置读取显式绑定的元数据集数；未绑定则 None（走全局）。"""
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            if cls._tool_entry_name(tool) != "get_dataset_schema":
                continue
            raw_ids = tool.get("metadata_dataset_ids") or []
            if not isinstance(raw_ids, list):
                return None
            cleaned = [str(item).strip() for item in raw_ids if str(item).strip()]
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
        # skills_custom=False 表示使用全部公共 Skills，skill_count 置 None 由前端显示「全部」
        skill_count = len([s for s in skills_raw if str(s).strip()]) if skills_custom else None

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
                agent_version=f"v{version.version_number}",
                model_name=version.model_name,
                temperature=version.temperature,
                synthesis_model_name=version.synthesis_model_name,
                synthesis_temperature=version.synthesis_temperature,
                system_prompt=version.system_prompt,
                tools=tools_list or [],
                skills_custom=bool(getattr(version, "skills_custom", False)),
                skills=AgentManagerService._normalize_skills_list(getattr(version, "skills", None)),
                capabilities=agent.capabilities or [],
                engine_type=agent.engine_type,
                engine_config=agent.engine_config
            )
            
        except Exception as e:
            logger.error(f"Error fetching agent config: {e}", exc_info=True)
            return None

    @staticmethod
    async def get_version_config(
        session: AsyncSession, 
        version_id: str
    ) -> Optional[ChatConfig]:
        """
        Fetches the configuration for a SPECIFIC version ID.
        If version_id matches an AIAgent ID and it's RAGFLOW, return managed config.
        """
        try:
            # 1. Try to find the version record
            query = select(AIAgentVersion).join(AIAgent).options(selectinload(AIAgentVersion.agent)).where(AIAgentVersion.id == version_id)
            result = await session.execute(query)
            version = result.scalar_one_or_none()
            
            if version:
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
                    agent_version=f"v{version.version_number}",
                    model_name=version.model_name,
                    temperature=version.temperature,
                    synthesis_model_name=version.synthesis_model_name,
                    synthesis_temperature=version.synthesis_temperature,
                    system_prompt=version.system_prompt,
                    tools=tools_list or [],
                    skills_custom=bool(getattr(version, "skills_custom", False)),
                    skills=AgentManagerService._normalize_skills_list(getattr(version, "skills", None)),
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
        # 1. Fetch Agents (Sort by sort_order DESC, then System)
        query = select(AIAgent).order_by(AIAgent.sort_order.desc(), AIAgent.is_system.desc(), AIAgent.display_name)
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
            # Visibility Check (currently all are visible, but editability differs)
            
            # Editability Check
            is_owner = agent.created_by == username
            agent.is_editable = True if is_admin or is_owner or (not agent.created_by and is_admin) else False
            
            # Special case: System agents are read-only for non-admins
            if agent.is_system and not is_admin:
                 agent.is_editable = False
            
            # Set execution count (dynamically attached to object, Pydantic will pick it up)
            agent.execution_count = counts.get(agent.id, 0)
                 
            visible_agents.append(agent)

        # 批量附带已发布版本的 tool / mcp / skills 摘要（仅 LOCAL 引擎有版本工具配置）
        local_ids = [a.id for a in visible_agents if (a.engine_type or "LOCAL") == "LOCAL"]
        published_by_agent = await AgentManagerService._latest_published_versions_by_agent(
            session, local_ids
        )
        for agent in visible_agents:
            engine_config = agent.engine_config if isinstance(agent.engine_config, dict) else None
            published_version = published_by_agent.get(agent.id)
            if (agent.engine_type or "LOCAL") != "LOCAL":
                caps = AgentManagerService.summarize_version_capabilities(
                    None, engine_config=engine_config
                )
            else:
                caps = AgentManagerService.summarize_version_capabilities(
                    published_version,
                    engine_config=engine_config,
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
            )
            agent.readiness_ready = readiness.ready
            agent.readiness_missing = list(readiness.missing)
            
        return visible_agents

    @staticmethod
    async def list_allowed_agents(session: AsyncSession, user: Any = None, keyword: Optional[str] = None) -> List[Any]:
        """
        List ONLY agents that the user has permission to EXECUTE/CHAT with.
        - Admin: All
        - User: System Agents + Owned Agents + Permitted Agents
        """
        if not user:
            return []

        # User Info extraction
        if isinstance(user, dict):
            is_admin = user.get('role', '') == 'admin'
            username = user.get('user_name', '')
            user_id = user.get('user_id', user.get('id', ''))
        else:
            is_admin = getattr(user, 'role', '') == 'admin'
            username = getattr(user, 'user_name', '')
            user_id = getattr(user, 'id', '')

        stmt = select(AIAgent).where(AIAgent.is_enabled == True)

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

        # Sort: sort_order DESC, then System first (desc), then Alphabetical
        stmt = stmt.order_by(AIAgent.sort_order.desc(), AIAgent.is_system.desc(), AIAgent.display_name)

        return (await session.execute(stmt)).scalars().all()

    @staticmethod
    async def create_agent(session: AsyncSession, data: AIAgentBase, user: Any = None) -> AIAgent:
        """Create a new agent metadata entry"""
        if isinstance(user, dict):
            created_by = user.get('user_name')
            is_admin = user.get('role', '') == 'admin'
        else:
            created_by = getattr(user, 'user_name', None) if user else None
            is_admin = user and getattr(user, 'role', '') == 'admin'

        AgentManagerService._validate_engine_config(data.engine_type, data.engine_config)
        
        # Check if agent with the same name already exists
        existing_query = select(AIAgent).where(AIAgent.name == data.name)
        result = await session.execute(existing_query)
        if result.scalar_one_or_none():
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"Agent with ID/Name '{data.name}' already exists.")

        from app.services.ai.agent_types import (
            normalize_agent_capabilities_for_agent,
            resolve_agent_type_for_engine,
        )

        resolved_agent_type = resolve_agent_type_for_engine(data.engine_type, data.agent_type)
        agent = AIAgent(
            id=str(uuid.uuid4()),
            name=data.name,
            display_name=data.display_name,
            description=data.description,
            avatar_url=data.avatar_url,
            capabilities=normalize_agent_capabilities_for_agent(
                engine_type=data.engine_type,
                agent_type=resolved_agent_type,
                values=data.capabilities,
            ),
            agent_type=resolved_agent_type.value,
            is_system=data.is_system if is_admin and data.is_system is not None else False,
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

        for item in items:
            agent = agents.get(item.id)
            if agent:
                agent.sort_order = item.sort_order

        await session.commit()

        from app.services.ai.router_service import router_service
        router_service.invalidate_cache()

        return True

    @staticmethod
    async def update_agent(session: AsyncSession, agent_id: str, data: AIAgentBase, user: Any = None) -> Optional[AIAgent]:
        """Update existing agent metadata"""
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
            
        if not is_admin and agent.created_by and agent.created_by != username:
            return None # Or raise Forbidden in Endpoint
        
        # System Agent Check
        if agent.is_system and not is_admin:
            return None

        original_engine_type = str(agent.engine_type or "LOCAL").upper()
        if "engine_type" in data.model_fields_set:
            requested_engine_type = str(data.engine_type or original_engine_type).upper()
            if requested_engine_type != original_engine_type:
                raise ValueError("执行引擎创建后不可修改；请新建智能体以使用其他引擎")

        agent.name = data.name
        agent.display_name = data.display_name
        agent.description = data.description
        agent.avatar_url = data.avatar_url
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
        
        if is_admin and data.is_system is not None:
            agent.is_system = data.is_system
            
        if data.is_enabled is not None:
            agent.is_enabled = data.is_enabled
        
        # Engine implementation is immutable after creation. Connection and
        # runtime parameters remain editable through engine_config.
        if data.engine_config is not None:
            agent.engine_config = data.engine_config
        
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
        if not agent or agent.is_system:
            return False

        # Permission Check
        if isinstance(user, dict):
            is_admin = user.get('role', '') == 'admin'
            username = user.get('user_name', '')
        else:
            is_admin = user and getattr(user, 'role', '') == 'admin'
            username = user and getattr(user, 'user_name', '')
            
        if not is_admin and agent.created_by and agent.created_by != username:
            return False

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

        # Permission Check (Visibility)
        # All users can see versions of all agents (read-only), but we could restrict if needed.
        # Currently, consistent with metadata, we allow viewing.
        
        query = select(AIAgentVersion).where(AIAgentVersion.agent_id == agent_id).order_by(AIAgentVersion.version_number.desc())
        result = await session.execute(query)
        return result.scalars().all()

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

        if not is_admin and agent.created_by and agent.created_by != username:
            return None # Forbidden

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
            system_prompt=data.system_prompt,
            tools=AgentManagerService._normalize_tools_for_db(data.tools),
            skills_custom=skills_custom,
            skills=skills_list,
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
    async def update_agent_version(session: AsyncSession, agent_id: str, version_id: str, data: AIAgentVersionBase, user: Any = None) -> Optional[AIAgentVersion]:
        """Update an existing DRAFT version (checks ownership)"""
        agent = await session.get(AIAgent, agent_id)
        version = await session.get(AIAgentVersion, version_id)
        if not agent or not version:
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

        if not is_admin and agent.created_by and agent.created_by != username:
            return None # Forbidden

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
        version.system_prompt = data.system_prompt
        version.tools = AgentManagerService._normalize_tools_for_db(data.tools)
        version.skills_custom = skills_custom
        version.skills = skills_list
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

        if not is_admin and agent.created_by and agent.created_by != username:
            return False # Forbidden

        version = await session.get(AIAgentVersion, version_id)
        if not version or str(version.agent_id) != str(agent_id):
            return False

        from app.services.ai.agent_readiness import evaluate_agent_readiness

        readiness = evaluate_agent_readiness(
            agent_type=agent.agent_type or "GENERAL",
            capabilities=agent.capabilities,
            engine_config=agent.engine_config,
            tools=version.tools,
            # The target version becomes the published version in this transaction.
            has_published_version=True,
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
        
        await session.commit()
        
        # Invalidate Router Cache
        from app.services.ai.router_service import router_service
        router_service.invalidate_cache()
        
        return True

    @staticmethod
    async def delete_version(session: AsyncSession, agent_id: str, version_id: str, user: Any = None) -> bool:
        """Delete an agent version (only DRAFT or ARCHIVED)"""
        agent = await session.get(AIAgent, agent_id)
        version = await session.get(AIAgentVersion, version_id)
        
        if not agent or not version:
            return False
            
        # Cannot delete PUBLISHED version
        if version.status == "PUBLISHED":
            return False

        # Permission Check
        if isinstance(user, dict):
            is_admin = user.get('role', '') == 'admin'
            username = user.get('user_name', '')
        else:
            is_admin = user and getattr(user, 'role', '') == 'admin'
            username = user and getattr(user, 'user_name', '')

        if not is_admin and agent.created_by and agent.created_by != username:
            return False # Forbidden

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
