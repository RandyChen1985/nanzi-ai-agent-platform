import logging
import uuid
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update, or_
from sqlalchemy.orm import selectinload
from app.models.agent import AIAgent, AIAgentVersion
from app.schemas.agent import ChatConfig, AIAgentBase, AIAgentVersionBase
import json

logger = logging.getLogger(__name__)

class AgentManagerService:
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
        
        # Check if agent with the same name already exists
        existing_query = select(AIAgent).where(AIAgent.name == data.name)
        result = await session.execute(existing_query)
        if result.scalar_one_or_none():
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"Agent with ID/Name '{data.name}' already exists.")

        agent = AIAgent(
            id=str(uuid.uuid4()),
            name=data.name,
            display_name=data.display_name,
            description=data.description,
            avatar_url=data.avatar_url,
            capabilities=data.capabilities,
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

        agent.name = data.name
        agent.display_name = data.display_name
        agent.description = data.description
        agent.avatar_url = data.avatar_url
        agent.capabilities = data.capabilities
        agent.sort_order = data.sort_order if data.sort_order is not None else agent.sort_order
        
        if is_admin and data.is_system is not None:
            agent.is_system = data.is_system
            
        if data.is_enabled is not None:
            agent.is_enabled = data.is_enabled
        
        # Update Engine Config
        if data.engine_type:
            agent.engine_type = data.engine_type
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
