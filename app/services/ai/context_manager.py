import logging
from typing import Optional, List, Dict, Any
from app.core.orm import AsyncSessionLocal
from app.services.ai.agent_manager import AgentManagerService
from app.services.ai.router_service import router_service, RouterService
from app.core.context import set_debug_context, set_agent_context, AgentContext
from app.schemas.agent import ChatConfig
from app.services.ai.agent_prompts import ContextManagerPrompts

logger = logging.getLogger(__name__)

KNOWLEDGE_AGENT_FALLBACK_NAMES = ("knowledge-base",)


def _normalize_rag_params(engine_config: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """将 engine_config 中的扁平 RAG 字段归一化到 rag_params。"""
    if not engine_config:
        return None
    rag_params = dict(engine_config.get("rag_params") or {})
    if engine_config.get("ragflow_similarity_threshold") not in (None, ""):
        rag_params.setdefault("similarity_threshold", engine_config["ragflow_similarity_threshold"])
    if engine_config.get("ragflow_vector_weight") not in (None, ""):
        rag_params.setdefault("vector_similarity_weight", engine_config["ragflow_vector_weight"])
    if engine_config.get("top_k") not in (None, ""):
        rag_params.setdefault("top_k", engine_config["top_k"])
    return rag_params or None


class AgentContextManager:
    """
    Manages the resolution of Agent Configuration and Context Setup.
    """

    @staticmethod
    async def resolve_agent_config(
        messages: List[Dict[str, str]],
        agent_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        version_id: Optional[str] = None,
        enable_multi_agent: bool = True,
        user_info: Optional[Dict[str, Any]] = None,
    ):
        """
        Resolve the appropriate AgentConfig based on inputs or routing.
        Returns attributes needed for execution.
        
        Returns:
            Tuple[Optional[ChatConfig], Optional[Any]]: The config and optional routing details.
        """
        agent_config = None
        route_details = None
        
        async with AsyncSessionLocal() as session:
            if version_id:
                agent_config = await AgentManagerService.get_version_config(session, version_id)
            elif agent_id:
                agent_config = await AgentManagerService.get_active_agent_config(session, agent_id=agent_id)
            elif agent_name:
                agent_config = await AgentManagerService.get_active_agent_config(session, agent_name=agent_name)
            else:
                route_user_id = None
                route_is_admin = False
                if user_info:
                    raw_user_id = user_info.get("user_id") or user_info.get("id")
                    route_user_id = int(raw_user_id) if raw_user_id else None
                    route_is_admin = user_info.get("role") == "admin"

                # Routing Logic
                # Extract last user message and history for routing
                last_user_msg = ""
                history = []
                for i in range(len(messages) - 1, -1, -1):
                    if messages[i]["role"] == "user":
                        last_user_msg = messages[i]["content"]
                        history = messages[:i]
                        break

                # Session affinity: find which agent handled the previous turn
                # so follow-up / coreference queries can stick to it.
                last_agent_name = None
                for msg in reversed(history):
                    if msg.get("role") == "assistant" and msg.get("agent_name"):
                        last_agent_name = msg["agent_name"]
                        break

                route_result = await router_service.route_query(
                    last_user_msg, 
                    history=history,
                    enable_multi_agent=enable_multi_agent,
                    user_id=route_user_id,
                    is_admin=route_is_admin,
                    last_agent_name=last_agent_name,
                )
                route_details = route_result
                
                if route_result and route_result.agent_id:
                    agent_config = await AgentManagerService.get_active_agent_config(session, agent_id=route_result.agent_id)

        # Fallback: try known general-assistant slugs in DB before synthetic config
        if not agent_config:
            async with AsyncSessionLocal() as session:
                for fallback_name in RouterService.FALLBACK_AGENT_NAMES:
                    agent_config = await AgentManagerService.get_active_agent_config(
                        session, agent_name=fallback_name
                    )
                    if agent_config:
                        logger.info("Resolved fallback agent from DB: %s", fallback_name)
                        break

        if not agent_config:
            from app.services.config_service import ConfigService
            default_model = await ConfigService.get("llm_model_name") or "DeepSeek-V3.2"
            fallback_slug = RouterService.FALLBACK_AGENT_NAMES[-1]

            agent_config = ChatConfig(
                agent_id=fallback_slug,
                agent_name="General Chat",
                agent_version="default",
                model_name=default_model,
                temperature=0.7,
                system_prompt=ContextManagerPrompts.GENERAL_CHAT_FALLBACK_SYSTEM_PROMPT,
                tools=[],
                capabilities=["chat"],
                engine_type="LOCAL"
            )

        return agent_config, route_details

    @staticmethod
    async def enrich_for_knowledge_turn(
        config: ChatConfig,
        user_query: str = "",
    ) -> ChatConfig:
        """
        KNOWLEDGE 轮次：补齐 dataset_ids / RAG 参数 / 知识库工具。
        当前智能体未绑定知识库时，回退到 knowledge-base 系统智能体配置。
        """
        from app.services.ai.knowledge_utils import (
            extract_dataset_ids_from_message,
            merge_dataset_id_sources,
        )

        engine_config = dict(config.engine_config or {})
        dataset_ids = merge_dataset_id_sources(
            engine_config.get("dataset_ids"),
            extract_dataset_ids_from_message(user_query),
        )

        capabilities = list(config.capabilities or [])
        tools = list(config.tools or [])
        has_kb_binding = bool(dataset_ids) or "knowledge_base" in capabilities

        if not has_kb_binding:
            async with AsyncSessionLocal() as session:
                for fallback_name in KNOWLEDGE_AGENT_FALLBACK_NAMES:
                    kb_config = await AgentManagerService.get_active_agent_config(
                        session, agent_name=fallback_name
                    )
                    if kb_config:
                        kb_ec = kb_config.engine_config or {}
                        dataset_ids = merge_dataset_id_sources(
                            dataset_ids,
                            kb_ec.get("dataset_ids"),
                        )
                        for key in (
                            "ragflow_similarity_threshold",
                            "ragflow_vector_weight",
                            "top_k",
                            "rag_params",
                        ):
                            if kb_ec.get(key) not in (None, "") and key not in engine_config:
                                engine_config[key] = kb_ec[key]
                        if kb_config.system_prompt and not config.system_prompt:
                            config = config.model_copy(
                                update={"system_prompt": kb_config.system_prompt}
                            )
                        for cap in kb_config.capabilities or []:
                            if cap not in capabilities:
                                capabilities.append(cap)
                        for tool_name in kb_config.tools or []:
                            if tool_name not in tools:
                                tools.append(tool_name)
                        logger.info(
                            "[AgentContextManager] KNOWLEDGE turn enriched from fallback agent: %s",
                            fallback_name,
                        )
                        break

        if dataset_ids:
            engine_config["dataset_ids"] = dataset_ids

        updates: Dict[str, Any] = {"engine_config": engine_config}
        if capabilities != (config.capabilities or []):
            updates["capabilities"] = capabilities
        if tools != (config.tools or []):
            updates["tools"] = tools
        return config.model_copy(update=updates)

    @staticmethod
    async def setup_context(
        config: ChatConfig, 
        debug_options: Dict[str, Any] = {},
        user_info: Optional[Dict[str, Any]] = None,
        api_key: Optional[str] = None,
        conversation_id: Optional[str] = None,
        knowledge_dataset_ids: Optional[List[str]] = None,
        require_explicit_dataset: bool = False,
        trace_buffer: Optional[List[Any]] = None,
    ):
        """
        Setup the execution context (debug options + agent config).
        """
        # 1. Set Debug Context
        set_debug_context(debug_options)
        
        # 2. Set Agent Context
        u_id_val = None
        is_admin_val = False
        api_key_val = api_key
        user_dims = {}
        
        if user_info:
            raw_uid = user_info.get("user_id", user_info.get("id"))
            if raw_uid:
                u_id_val = int(raw_uid)
            is_admin_val = user_info.get("role") == "admin"
            if not api_key_val:
                api_key_val = user_info.get("api_key")
            
            # Extract Dimensions for SQL Rewriter
            user_dims = {
                "id": u_id_val,
                "user_name": user_info.get("user_name"),
                "real_name": user_info.get("real_name"),
                "role": user_info.get("role"),
                "dept_code": user_info.get("dept_code"),
                "org_path": user_info.get("org_path"),
            }
            
            # Flatten extra_data into user_dims
            extra_data = user_info.get("extra_data")
            if extra_data:
                try:
                    import json
                    extra_dict = {}
                    if isinstance(extra_data, str):
                        # Attempt to parse if it's a JSON string
                        extra_dict = json.loads(extra_data)
                    elif isinstance(extra_data, dict):
                        extra_dict = extra_data
                    
                    if isinstance(extra_dict, dict):
                        for k, v in extra_dict.items():
                            # Avoid overwriting core dimensions
                            if k not in user_dims:
                                user_dims[k] = v
                except Exception as e:
                    logger.warning(f"Failed to parse or flatten extra_data: {e}")
            
            # Keep original extra_data for backward compatibility
            user_dims["extra_data"] = extra_data

        from app.services.ai.knowledge_utils import merge_dataset_id_sources

        engine_config = config.engine_config or {}
        request_dataset_ids = merge_dataset_id_sources(knowledge_dataset_ids)
        if request_dataset_ids:
            effective_dataset_ids = request_dataset_ids
        else:
            agent_dataset_ids = merge_dataset_id_sources(engine_config.get("dataset_ids"))
            user_permitted_ids = []
            if u_id_val is not None:
                from app.services.permission_service import PermissionService
                from app.models.knowledge import KnowledgeBaseMetadata
                from sqlalchemy.future import select
                async with AsyncSessionLocal() as session:
                    permission_service = PermissionService(session)
                    access = await permission_service.get_knowledge_base_access(
                        user_id=u_id_val,
                        user_name=user_dims.get("user_name"),
                    )
                    if access.get("is_admin"):
                        stmt = select(KnowledgeBaseMetadata.ragflow_dataset_id).where(
                            KnowledgeBaseMetadata.status != "deleted"
                        )
                        rows = (await session.execute(stmt)).scalars().all()
                        user_permitted_ids = [row for row in rows if row]
                    else:
                        user_permitted_ids = list(access.get("accessible_ids") or [])
            effective_dataset_ids = merge_dataset_id_sources(
                agent_dataset_ids,
                user_permitted_ids,
            )

        # Sync effective_dataset_ids back to config's engine_config to support context re-generation
        if config.engine_config is None:
            config.engine_config = {}
        config.engine_config["dataset_ids"] = effective_dataset_ids
        engine_config = config.engine_config

        set_agent_context(AgentContext(
            agent_id=config.agent_id,
            agent_name=config.agent_name,
            dataset_ids=effective_dataset_ids,
            knowledge_dataset_ids=request_dataset_ids,
            require_explicit_dataset=require_explicit_dataset,
            engine_type=config.engine_type,
            engine_config=engine_config,
            rag_params=_normalize_rag_params(engine_config),
            user_id=u_id_val,
            conversation_id=conversation_id,
            is_admin=is_admin_val,
            api_key=api_key_val,
            user_dimensions=user_dims,
            trace_buffer=trace_buffer or []
        ))
