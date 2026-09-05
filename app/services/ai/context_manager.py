import logging
from typing import Optional, List, Dict, Any
from app.core.orm import AsyncSessionLocal
from app.services.ai.agent_manager import AgentManagerService
from app.services.ai.router_service import RouterService
from app.core.context import (
    get_current_agent_context,
    set_debug_context,
    set_agent_context,
    AgentContext,
)
from app.schemas.agent import ChatConfig
from app.services.ai.agent_prompts import ContextManagerPrompts
from app.services.ai.turn_decision import TurnDecision
from app.services.ai.route_progress import RouteProgressCallback

logger = logging.getLogger(__name__)

DEFAULT_MAIN_AGENT_ID = "sys-agent-chat"
DEFAULT_MAIN_AGENT_NAMES = ("main", "assistant", "general-chat")


def select_data_query_agent_id(agents: List[Any]) -> Optional[str]:
    """Return the first enabled agent that can execute ChatBI data queries."""
    for agent in agents:
        capabilities = {str(value).strip().casefold() for value in (getattr(agent, "capabilities", None) or [])}
        if bool(getattr(agent, "is_enabled", False)) and "data_query" in capabilities:
            return str(getattr(agent, "id", "") or "") or None
    return None


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
        force_data_query: bool = False,
        quick_result_followup: bool = False,
        conversation_id: Optional[str] = None,
        on_progress: Optional[RouteProgressCallback] = None,
    ):
        """
        Resolve the appropriate AgentConfig based on inputs or routing.
        Returns attributes needed for execution.

        Returns:
            Tuple[Optional[ChatConfig], Optional[Any]]: The config and optional routing details.
        """
        agent_config = None
        route_details = None
        has_explicit_agent = bool(version_id or agent_id or agent_name)

        async with AsyncSessionLocal() as session:
            if version_id:
                agent_config = await AgentManagerService.get_version_config(session, version_id)
            elif agent_id:
                agent_config = await AgentManagerService.get_active_agent_config(session, agent_id=agent_id)
            elif agent_name:
                agent_config = await AgentManagerService.get_active_agent_config(session, agent_name=agent_name)
            elif quick_result_followup:
                # 快捷结果追问只提供“上一轮来自查数结果”的路由提示，不能把结果本身
                # 当作证据。这里仅在调用方没有显式指定专家时选择数据查询专家，后续
                # 仍由权限校验、DataQueryExecutor 和本轮证据门禁完成真正的安全判断。
                visible_agents = await AgentManagerService.list_allowed_agents(session, user_info)
                data_agent_id = select_data_query_agent_id(visible_agents)
                if data_agent_id:
                    agent_config = await AgentManagerService.get_active_agent_config(
                        session,
                        agent_id=data_agent_id,
                    )
                if agent_config:
                    route_details = TurnDecision.for_direct_agent_selection(agent_config)
                    logger.info(
                        "Resolved quick-result follow-up to data-query agent: %s",
                        getattr(agent_config, "agent_id", None),
                    )
                else:
                    # 不允许快捷上下文失效后静默回退 Main，否则会重新出现“看似查数、
                    # 实际未调用工具”的风险。调用方会返回统一的无可用专家提示。
                    logger.warning("No enabled data-query agent available for quick-result follow-up")
                    return None, None
            else:
                agent_config = await AgentManagerService.get_active_agent_config(
                    session, agent_id=DEFAULT_MAIN_AGENT_ID
                )
                if not agent_config:
                    for fallback_name in DEFAULT_MAIN_AGENT_NAMES:
                        agent_config = await AgentManagerService.get_active_agent_config(
                            session, agent_name=fallback_name
                        )
                        if agent_config:
                            break
                if agent_config:
                    route_details = TurnDecision.for_default_main_delegation(agent_config)
                    logger.info(
                        "Resolved default Main agent without semantic router: %s",
                        getattr(agent_config, "agent_name", None),
                    )

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

        if not has_explicit_agent and route_details is None:
            route_details = TurnDecision.for_default_main_delegation(agent_config)

        return agent_config, route_details

    @staticmethod
    async def enrich_for_knowledge_turn(
        config: ChatConfig,
        user_query: str = "",
    ) -> ChatConfig:
        """
        KNOWLEDGE 轮次：只补齐当前智能体/本轮显式携带的 dataset_ids。
        不从其他系统智能体回退合并工具或知识库配置，避免 Main 隐式获得未配置能力。
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
        agent_dataset_ids: Optional[List[str]] = None,
        metadata_dataset_ids: Optional[List[str]] = None,
        authorized_attachment_paths: Optional[List[str]] = None,
        current_turn_attachment_paths: Optional[List[str]] = None,
        require_explicit_dataset: bool = False,
        trace_buffer: Optional[List[Any]] = None,
        runtime_model_info: Optional[Dict[str, Any]] = None,
        published_download_urls: Optional[List[str]] = None,
        agent_max_toolcall_timeout_seconds: Optional[float] = None,
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
        if config.agent_dataset_ids is None:
            config.agent_dataset_ids = merge_dataset_id_sources(engine_config.get("dataset_ids"))
        request_dataset_ids = merge_dataset_id_sources(knowledge_dataset_ids)
        previous_context = get_current_agent_context()
        previous_agent_dataset_ids = (
            previous_context.agent_dataset_ids
            if previous_context and previous_context.agent_id == config.agent_id
            else None
        )
        configured_agent_dataset_ids = merge_dataset_id_sources(
            agent_dataset_ids
            if agent_dataset_ids is not None
            else (
                previous_agent_dataset_ids
                if previous_agent_dataset_ids is not None
                else config.agent_dataset_ids
            )
        )
        if request_dataset_ids:
            # 用户显式选择是硬范围，不能被智能体默认知识库扩展。
            effective_dataset_ids = request_dataset_ids
        else:
            # 无显式选择时，智能体绑定知识库与当前用户可访问知识库合并。
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
                configured_agent_dataset_ids,
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
            agent_version=config.agent_version,
            dataset_ids=effective_dataset_ids,
            knowledge_dataset_ids=request_dataset_ids,
            agent_dataset_ids=configured_agent_dataset_ids,
            metadata_dataset_ids=list(metadata_dataset_ids or []),
            require_explicit_dataset=require_explicit_dataset,
            engine_type=config.engine_type,
            engine_config=engine_config,
            rag_params=_normalize_rag_params(engine_config),
            user_id=u_id_val,
            conversation_id=conversation_id,
            browser_session_id=(debug_options or {}).get("browser_session_id")
            or (previous_context.browser_session_id if previous_context else None),
            is_admin=is_admin_val,
            api_key=api_key_val,
            user_dimensions=user_dims,
            authorized_attachment_paths=list(authorized_attachment_paths or []),
            current_turn_attachment_paths=list(current_turn_attachment_paths or []),
            agent_max_toolcall_timeout_seconds=agent_max_toolcall_timeout_seconds,
            trace_buffer=trace_buffer or [],
            skills_custom=bool(getattr(config, "skills_custom", False)),
            skills=list(getattr(config, "skills", None) or []),
            runtime_model_info=dict(runtime_model_info or {}),
            published_download_urls=(
                published_download_urls
                if published_download_urls is not None
                else []
            ),
        ))
