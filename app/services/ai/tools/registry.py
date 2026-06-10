from typing import Dict, Any, Optional, List
import inspect
import time
import logging
from app.services.ai.tools.data_api import get_dataset_schema, execute_sql_query
from app.services.ai.tools.dashboard_tools import update_dashboard_context
from app.services.ai.tools.generic_api import GenericApiToolFactory
from app.services.ai.tools.mcp_factory import McpToolFactory
from app.services.ai.tools.system_tools import system_http_request, SYSTEM_IMPLICIT_TOOLS
from app.services.ai.tools.knowledge_tool import search_knowledge_base
from app.services.ai.tools.task_manager_tools import (
    create_recurring_task, get_my_tasks, cancel_task, 
    start_task, pause_task, run_task_manually
)
from app.services.ai.tools.notification_tools import send_dingtalk_message, send_email
# Import Jira Tools
from app.services.ai.tools.jira_tools import JiraSearchTool, JiraCreateIssueTool, JiraGetProjectsTool
from app.services.ai.tools.system_executive_tools import (
    read_file, write_file, search_text, exec_command, manage_process, list_process,
    create_skills, list_available_skills, read_skill_instruction
)
from app.services.ai.tools.advanced_auxiliary_tools import (
    sqlite_scratchpad, directory_tree_navigator, web_renderer_and_snapshot, code_syntax_linter,
    fetch_static_web_url, web_search_baidu
)
from app.services.ai.tools.memory_ltm_tools import (
    update_user_preference, fetch_user_long_term_memory, delete_user_preference
)
from app.services.ai.tools.memory_search_tool import memory_search
from app.models.tool import SysApiTool
from app.models.mcp import McpToolCache
from app.core.orm import AsyncSessionLocal
from sqlalchemy import select

logger = logging.getLogger(__name__)


AGENTSCOPE_BUILTIN_TOOL_ALIASES: Dict[str, str] = {
    "exec_command": "Bash",
    "bash": "Bash",
    "Bash": "Bash",
    "read_file": "Read",
    "read": "Read",
    "Read": "Read",
    "write_file": "Write",
    "write": "Write",
    "Write": "Write",
    "edit_file": "Edit",
    "edit": "Edit",
    "Edit": "Edit",
    "search_text": "Grep",
    "grep": "Grep",
    "Grep": "Grep",
    "glob_files": "Glob",
    "glob": "Glob",
    "Glob": "Glob",
}


class ToolRegistry:
    """
    Registry for managing available tools for agents.
    Allows mapping string identifiers (from DB config) to actual runtime tool objects.
    """
    
    # Core tools available by default or for recovery
    DEFAULT_TOOL_SET = ["get_dataset_schema", "execute_sql_query"]

    # Instantiate complex tools once
    _jira_search = JiraSearchTool()
    _jira_create = JiraCreateIssueTool()
    _jira_get_projects = JiraGetProjectsTool()
    _dingtalk_tool = send_dingtalk_message()
    _email_tool = send_email()

    _registry: Dict[str, Any] = {
        "get_dataset_schema": get_dataset_schema,
        "execute_sql_query": execute_sql_query,
        "update_dashboard_context": update_dashboard_context,
        "system_http_request": system_http_request,
        "search_knowledge_base": search_knowledge_base,
        # Register Task Manager Tools
        "create_recurring_task": create_recurring_task,
        "get_my_tasks": get_my_tasks,
        "cancel_task": cancel_task,
        "start_task": start_task,
        "pause_task": pause_task,
        "run_task_manually": run_task_manually,
        "send_dingtalk_message": _dingtalk_tool,
        "send_email": _email_tool,
        # Register Jira Tools
        "jira_search": _jira_search,
        "jira_create_issue": _jira_create,
        "jira_get_projects": _jira_get_projects,
        # Register System Executive Tools
        "read_file": read_file,
        "write_file": write_file,
        "search_text": search_text,
        "exec_command": exec_command,
        "manage_process": manage_process,
        "list_process": list_process,
        "create_skills": create_skills,
        "list_available_skills": list_available_skills,
        "read_skill_instruction": read_skill_instruction,
        # Register Advanced Auxiliary Tools
        "sqlite_scratchpad": sqlite_scratchpad,
        "directory_tree_navigator": directory_tree_navigator,
        "web_renderer_and_snapshot": web_renderer_and_snapshot,
        "code_syntax_linter": code_syntax_linter,
        "fetch_static_web_url": fetch_static_web_url,
        "web_search_baidu": web_search_baidu,
        # Register Memory LTM Tools
        "update_user_preference": update_user_preference,
        "fetch_user_long_term_memory": fetch_user_long_term_memory,
        "delete_user_preference": delete_user_preference,
        "memory_search": memory_search,
    }

    # Cache for DB Tools
    _db_tool_cache: Dict[str, Any] = {}
    _db_tool_source_cache: Dict[str, str] = {}
    _db_tool_cache_ttl = 60.0 # 60 seconds
    _db_tool_ids_fetched_at: Dict[str, float] = {}

    @classmethod
    async def get_tool(cls, name: str) -> Optional[Any]:
        """Retrieve a tool by its name (Async)."""
        # 1. Check static registry
        if name in cls._registry:
            return cls._registry[name]

        # 2. Check Memory Cache
        now = time.time()
        if name in cls._db_tool_cache:
            last_fetched = cls._db_tool_ids_fetched_at.get(name, 0)
            if now - last_fetched < cls._db_tool_cache_ttl:
                return cls._db_tool_cache[name]

        # 3. Check DB for Generic or MCP Tools
        loaded = await cls._load_db_tool_with_source(name)
        return loaded[0] if loaded else None

    @classmethod
    async def _load_db_tool_with_source(cls, name: str) -> Optional[tuple[Any, str]]:
        now = time.time()
        if name in cls._db_tool_cache:
            last_fetched = cls._db_tool_ids_fetched_at.get(name, 0)
            if now - last_fetched < cls._db_tool_cache_ttl:
                return cls._db_tool_cache[name], cls._db_tool_source_cache.get(name, "static")

        try:
            async with AsyncSessionLocal() as session:
                # A. Check MCP Tools first
                mcp_stmt = select(McpToolCache).where(
                    McpToolCache.tool_name == name, 
                    McpToolCache.is_published == True
                )
                mcp_res = await session.execute(mcp_stmt)
                mcp_config = mcp_res.scalar_one_or_none()
                
                if mcp_config:
                    tool_instance = McpToolFactory.create_tool(mcp_config)
                    cls._db_tool_cache[name] = tool_instance
                    cls._db_tool_source_cache[name] = "mcp"
                    cls._db_tool_ids_fetched_at[name] = time.time()
                    return tool_instance, "mcp"

                # B. Check Generic API Tools
                stmt = select(SysApiTool).where(SysApiTool.name == name, SysApiTool.is_active == True)
                gen_res = await session.execute(stmt)
                tool_config = gen_res.scalar_one_or_none()
                
                if tool_config:
                    tool_instance = GenericApiToolFactory.create_tool(tool_config)
                    cls._db_tool_cache[name] = tool_instance
                    cls._db_tool_source_cache[name] = "generic_api"
                    cls._db_tool_ids_fetched_at[name] = time.time()
                    return tool_instance, "generic_api"
        except Exception as e:
            logger.error(f"Error loading tool {name} from DB: {e}")
            pass

        return None

    @classmethod
    async def get_runtime_tool(cls, name: str):
        """
        Retrieve a platform-neutral runtime tool spec by name.

        This is the AgentScope migration entrypoint. The legacy get_tool()
        method remains available until all executors stop consuming legacy tool
        tool objects.
        """
        from app.services.ai.runtime.agentscope.tools import (
            runtime_tool_spec_from_legacy_tool,
            runtime_tool_spec_from_native_agentscope_tool,
        )

        native_tool = cls._create_agentscope_builtin_tool(name)
        if native_tool is not None:
            return runtime_tool_spec_from_native_agentscope_tool(native_tool, source_type="system")

        data_tool_spec = cls._create_chatbi_runtime_tool_spec(name)
        if data_tool_spec is not None:
            return data_tool_spec

        db_tool = await cls._load_db_tool_with_source(name)
        if db_tool:
            tool, source_type = db_tool
            return runtime_tool_spec_from_legacy_tool(tool, source_type=source_type)

        tool = await cls.get_tool(name)
        if not tool:
            return None
        return runtime_tool_spec_from_legacy_tool(tool, source_type="static")

    @classmethod
    async def get_tools(cls, tool_configs: List[Any]) -> list[Any]:
        """
        Retrieve a list of tools, filtering out unknown ones (Async).
        Supports tool_configs as List[str] (legacy) or List[ToolConfigItem] (runtime config).
        """
        from app.schemas.agent import ToolConfigItem
        
        tools = []
        for item in tool_configs:
            name = ""
            config_item: Optional[ToolConfigItem] = None
            
            if isinstance(item, str):
                name = item
            elif isinstance(item, dict):
                name = item.get("name", "")
                try:
                    config_item = ToolConfigItem(**item)
                except Exception as e:
                    logger.warning(f"Failed to parse tool config dict: {e}")
            elif hasattr(item, "name"):
                name = getattr(item, "name")
                config_item = item
            
            if not name:
                continue

            tool = await cls.get_tool(name)
            if not tool:
                continue

            # Apply Runtime Configuration if present (Including engine_config_override for Webhooks)
            if config_item:
                 tool = await cls._configure_tool_runtime(tool, config_item)
            
            tools.append(tool)
        return tools

    @classmethod
    async def get_runtime_tools(cls, tool_configs: List[Any]) -> list[Any]:
        """
        Retrieve runtime tool specs, filtering unknown tools.
        Supports the same config item shapes as get_tools().
        """
        runtime_tools = []
        seen_tool_names = set()
        for item in tool_configs:
            if isinstance(item, str):
                name = item
            elif isinstance(item, dict):
                name = item.get("name", "")
            elif hasattr(item, "name"):
                name = getattr(item, "name")
            else:
                name = ""

            if not name:
                continue

            spec = await cls.get_runtime_tool(name)
            if spec and spec.name not in seen_tool_names:
                runtime_tools.append(spec)
                seen_tool_names.add(spec.name)
        return runtime_tools

    @classmethod
    def _create_agentscope_builtin_tool(cls, configured_name: str) -> Optional[Any]:
        builtin_name = AGENTSCOPE_BUILTIN_TOOL_ALIASES.get(configured_name)
        if not builtin_name:
            return None
        from agentscope.tool import Bash, Edit, Glob, Grep, Read, Write

        builtin_classes = {
            "Bash": Bash,
            "Read": Read,
            "Write": Write,
            "Edit": Edit,
            "Glob": Glob,
            "Grep": Grep,
        }
        return builtin_classes[builtin_name]()

    @staticmethod
    async def _invoke_registry_entry(tool: Any, payload: Dict[str, Any]) -> Any:
        if hasattr(tool, "ainvoke"):
            return await tool.ainvoke(payload)
        if hasattr(tool, "arun"):
            return await tool.arun(**payload)
        if callable(tool):
            result = tool(**payload)
            if inspect.isawaitable(result):
                return await result
            return result
        raise TypeError(f"Registry tool {tool!r} is not callable")

    @classmethod
    def _create_chatbi_runtime_tool_spec(cls, name: str):
        if name not in {"get_dataset_schema", "execute_sql_query", "update_dashboard_context"}:
            return None

        from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

        tool = cls._registry.get(name)
        if tool is None:
            return None

        if name == "get_dataset_schema":
            async def invoke_schema(**kwargs):
                return await cls._invoke_registry_entry(
                    tool,
                    {"keywords": kwargs.get("keywords")},
                )

            return RuntimeToolSpec(
                name="get_dataset_schema",
                description=(
                    "Retrieve authorized dataset schemas, tables, columns, metric definitions, "
                    "relationships, and synonyms for ChatBI SQL planning."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "keywords": {
                            "anyOf": [{"type": "string"}, {"type": "null"}],
                            "default": None,
                            "description": "Business keywords for metadata/schema retrieval.",
                        },
                    },
                },
                source_type="static",
                callable=invoke_schema,
                permission_scope="read",
            )

        if name == "execute_sql_query":
            async def invoke_sql(**kwargs):
                sql = kwargs.get("sql")
                if sql is None:
                    sql = kwargs.get("query")
                if isinstance(sql, str):
                    sql = sql.strip()
                return await cls._invoke_registry_entry(
                    tool,
                    {
                        "sql": sql,
                        "data_source": kwargs.get("data_source"),
                        "dataset_name": kwargs.get("dataset_name"),
                    },
                )

            return RuntimeToolSpec(
                name="execute_sql_query",
                description=(
                    "Execute a read-only SQL SELECT query against a permitted dataset. "
                    "The platform validates dataset permissions and SQL safety before execution."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "sql": {
                            "type": "string",
                            "description": "Read-only SQL SELECT query to execute.",
                        },
                        "data_source": {
                            "type": "string",
                            "description": "Data source identifier, for example mysql_oa.",
                        },
                        "dataset_name": {
                            "type": "string",
                            "description": "Authorized dataset name used for permission validation.",
                        },
                    },
                    "required": ["sql", "data_source", "dataset_name"],
                },
                source_type="static",
                callable=invoke_sql,
                permission_scope="read",
            )

        async def invoke_dashboard_context(**kwargs):
            return await cls._invoke_registry_entry(
                tool,
                {
                    "room_name": kwargs.get("room_name"),
                    "metric_name": kwargs.get("metric_name"),
                    "time_range": kwargs.get("time_range"),
                },
            )

        return RuntimeToolSpec(
            name="update_dashboard_context",
            description=(
                "Update the UI dashboard context for entities mentioned in the ChatBI conversation."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "room_name": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "default": None,
                        "description": "Room or location name mentioned by the user.",
                    },
                    "metric_name": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "default": None,
                        "description": "Metric name mentioned by the user.",
                    },
                    "time_range": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                        "default": None,
                        "description": "Time range mentioned by the user.",
                    },
                },
            },
            source_type="static",
            callable=invoke_dashboard_context,
            permission_scope="read",
        )

    @classmethod
    async def _configure_tool_runtime(cls, tool: Any, config: Any) -> Any:
        """
        Injects runtime configuration (model, temperature) into a tool instance.
        """
        try:
            # 1. Capture original metadata BEFORE copy
            # Handle both Pydantic fields and instance attributes
            orig_name = getattr(tool, "name", None)
            orig_desc = getattr(tool, "description", None)
            
            # If description is missing on instance, try class attribute
            if not orig_desc and hasattr(tool, "__class__"):
                orig_desc = getattr(tool.__class__, "description", None)

            # 2. Perform Shallow Copy
            # We use copy.copy() as it's the most generic way to clone python objects
            # including Pydantic models (it calls __copy__)
            import copy
            tool_copy = copy.copy(tool)
            
            # 3. Restore Metadata (Force Overwrite)
            # This is critical because Pydantic's copy might reset fields to defaults
            if orig_name: 
                tool_copy.name = orig_name
            
            # Ensure description is never None
            if orig_desc:
                tool_copy.description = orig_desc
            else:
                tool_copy.description = f"Tool: {orig_name or 'unknown'}"

            # 4. Inject Runtime Config
            # We attach it as a hidden attribute to avoid Pydantic validation errors
            # if extra fields are forbidden
            object.__setattr__(tool_copy, "_runtime_config", config)
            
            return tool_copy
        except Exception as e:
            logger.error(f"Failed to configure tool runtime: {e}")
            # Fallback: return original tool if copying fails
            return tool

    @classmethod
    def register(cls, name: str, tool: Any):
        """Dynamically register a new tool (In-Memory)."""
        cls._registry[name] = tool

    @classmethod
    def get_system_implicit_tools(cls) -> List[Any]:
        """
        Get the list of system implicit tools that should be available to all agents
        but not necessarily visible in the configuration.
        """
        return SYSTEM_IMPLICIT_TOOLS + [
            update_user_preference,
            fetch_user_long_term_memory,
            delete_user_preference,
            memory_search,
            create_skills,
            list_available_skills,
            read_skill_instruction,
        ]
