from typing import Dict, Any, Optional, List
from dataclasses import replace
import inspect
import re
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
from app.services.ai.tools.notification_tools import send_dingtalk_message, send_email, send_wechat_work_message
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
from app.services.ai.tools.agent_delegate_tool import sub_agent_call
from app.services.ai.tools.excel_document_tool import excel_document_read, excel_document_write
from app.services.ai.tools.word_document_tool import word_document_read, word_document_write
from app.models.tool import SysApiTool
from app.models.mcp import McpToolCache
from app.core.orm import AsyncSessionLocal
from sqlalchemy import select

logger = logging.getLogger(__name__)

from app.services.ai.grounding.models import EvidenceType


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

OFFICE_TOOL_PERMISSION_SCOPES = {
    "excel_document_read": "read",
    "excel_document_write": "ask",
    "word_document_read": "read",
    "word_document_write": "ask",
}

# Tool capability metadata. Grounding policy consumes abstract evidence types only;
# adding a tool does not require changing request or response rules.
TOOL_EVIDENCE_TYPES = {
    "get_dataset_schema": frozenset({EvidenceType.INTERNAL_DATA}),
    "execute_sql_query": frozenset({EvidenceType.INTERNAL_DATA}),
    "search_knowledge_base": frozenset({EvidenceType.INTERNAL_KNOWLEDGE}),
    "jira_search": frozenset({EvidenceType.INTERNAL_KNOWLEDGE}),
    "jira_get_projects": frozenset({EvidenceType.INTERNAL_KNOWLEDGE}),
    "fetch_static_web_url": frozenset({EvidenceType.PUBLIC_WEB}),
    "web_search_baidu": frozenset({EvidenceType.PUBLIC_WEB}),
    "system_http_request": frozenset({EvidenceType.PUBLIC_WEB}),
    "get_current_time": frozenset({EvidenceType.RUNTIME_STATE}),
    "get_my_tasks": frozenset({EvidenceType.RUNTIME_STATE}),
    "list_process": frozenset({EvidenceType.RUNTIME_STATE}),
    "manage_process": frozenset({EvidenceType.RUNTIME_STATE}),
    "exec_command": frozenset({EvidenceType.RUNTIME_STATE}),
    "read_file": frozenset({EvidenceType.USER_FILE}),
    "search_text": frozenset({EvidenceType.USER_FILE}),
    "excel_document_read": frozenset({EvidenceType.USER_FILE}),
    "word_document_read": frozenset({EvidenceType.USER_FILE}),
    "memory_search": frozenset({EvidenceType.CONVERSATION_MEMORY}),
    "fetch_user_long_term_memory": frozenset({EvidenceType.CONVERSATION_MEMORY}),
}

# 工具取证策略：
# "non_empty"（默认）：仅在工具返回非空结果时记录凭证。
# "allow_empty_success"：成功调用即使返回空结果也记录；错误和失败始终不记录
#        （记忆检索、知识库搜索、文件读取 —— 未找到也应允许模型如实回答）。
TOOL_EVIDENCE_POLICY: dict[str, str] = {
    # 数据查询：查询成功但返回空行 = "暂无数据"，属于合法事实依据
    "execute_sql_query": "allow_empty_success",
    "get_dataset_schema": "allow_empty_success",
    # 记忆/知识/文件检索：查无结果 = "未找到"，属于合法事实依据
    "memory_search": "allow_empty_success",
    "fetch_user_long_term_memory": "allow_empty_success",
    "search_knowledge_base": "allow_empty_success",
    "jira_search": "allow_empty_success",
    "read_file": "allow_empty_success",
    "search_text": "allow_empty_success",
    "excel_document_read": "allow_empty_success",
    "word_document_read": "allow_empty_success",
}


_MCP_READ_ACTION_RE = re.compile(
    r"^\s*(?:get|list|search|query|fetch|find|lookup|read|retrieve|inspect|check|"
    r"current|describe|show|view|status|history|detail)s?(?:\b|[_./-])|"
    r"^\s*(?:获取|查询|搜索|检索|读取|列出|查看|检查|详情|状态)",
    re.IGNORECASE,
)
_MCP_MUTATION_ACTION_RE = re.compile(
    r"(?:^|[_./-])(?:create|add|update|edit|patch|delete|remove|write|set|send|book|"
    r"purchase|order|cancel|start|stop|restart|run|execute|invoke|upload|move|"
    r"copy|rename)(?:\b|[_./-])|"
    r"(?:创建|新增|添加|更新|修改|编辑|删除|移除|写入|设置|发送|预订|"
    r"购买|下单|取消|启动|停止|重启|执行|调用|上传|移动|复制|重命名)",
    re.IGNORECASE,
)


def _is_read_only_mcp_tool(*, name: str, description: str) -> bool:
    """Conservatively recognize retrieval-style MCP tools from their metadata.

    MCP annotations are not persisted in the current cache schema, so only an
    explicit retrieval verb is accepted. Mutation verbs take precedence.
    """
    action_name = str(name or "").rsplit(":", 1)[-1]
    description_text = str(description or "")
    candidates = (action_name, description_text)
    if any(_MCP_MUTATION_ACTION_RE.search(candidate) for candidate in candidates):
        return False
    return any(_MCP_READ_ACTION_RE.search(candidate) for candidate in candidates)


def resolve_tool_evidence_types(*names: str) -> frozenset[EvidenceType]:
    """Resolve abstract evidence types for a configured or runtime tool name.

    Lookup order:
    1. Direct key in ``TOOL_EVIDENCE_TYPES`` (e.g. ``exec_command``).
    2. AgentScope builtin native name and every alias that maps to it
       (e.g. ``bash`` / ``Bash`` -> ``exec_command`` -> ``runtime_state``).
    """
    collected: set[EvidenceType] = set()
    visited: set[str] = set()

    def _collect_for_key(key: str) -> None:
        if not key or key in visited:
            return
        visited.add(key)
        evidence_types = TOOL_EVIDENCE_TYPES.get(key)
        if evidence_types:
            collected.update(evidence_types)

    for name in names:
        if not name:
            continue
        _collect_for_key(name)
        native_name = AGENTSCOPE_BUILTIN_TOOL_ALIASES.get(name, name)
        _collect_for_key(native_name)
        for alias, target in AGENTSCOPE_BUILTIN_TOOL_ALIASES.items():
            if target == native_name:
                _collect_for_key(alias)

    return frozenset(collected)


def resolve_tool_evidence_policy(*names: str) -> str:
    """Resolve evidence policy through configured, native, and reverse aliases."""
    candidates: list[str] = []
    for name in names:
        if not name:
            continue
        native_name = AGENTSCOPE_BUILTIN_TOOL_ALIASES.get(name, name)
        candidates.extend((name, native_name))
        candidates.extend(
            alias
            for alias, target in AGENTSCOPE_BUILTIN_TOOL_ALIASES.items()
            if target == native_name
        )
    for candidate in candidates:
        policy = TOOL_EVIDENCE_POLICY.get(candidate)
        if policy:
            return policy
    return "non_empty"


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
    _wechat_work_tool = send_wechat_work_message()

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
        "send_wechat_work_message": _wechat_work_tool,
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
        "sub_agent_call": sub_agent_call,
        "excel_document_read": excel_document_read,
        "excel_document_write": excel_document_write,
        "word_document_read": word_document_read,
        "word_document_write": word_document_write,
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
            spec = runtime_tool_spec_from_native_agentscope_tool(native_tool, source_type="system")
            return cls._attach_evidence_metadata(name, spec)

        data_tool_spec = cls._create_chatbi_runtime_tool_spec(name)
        if data_tool_spec is not None:
            return cls._attach_evidence_metadata(name, data_tool_spec)

        if name in cls._registry:
            tool = cls._registry[name]
            perm_scope = OFFICE_TOOL_PERMISSION_SCOPES.get(name)
            spec = runtime_tool_spec_from_legacy_tool(
                tool,
                source_type="static",
                permission_scope=perm_scope,
            )
            return cls._attach_evidence_metadata(name, spec)

        db_tool = await cls._load_db_tool_with_source(name)
        if db_tool:
            tool, source_type = db_tool
            spec = runtime_tool_spec_from_legacy_tool(tool, source_type=source_type)
            return cls._attach_evidence_metadata(name, spec)

        tool = await cls.get_tool(name)
        if not tool:
            return None
        spec = runtime_tool_spec_from_legacy_tool(tool, source_type="static")
        return cls._attach_evidence_metadata(name, spec)

    @staticmethod
    def _attach_evidence_metadata(name: str, spec: Any) -> Any:
        spec_name = str(getattr(spec, "name", "") or "")
        existing = frozenset(getattr(spec, "evidence_types", None) or ())
        existing_policy = getattr(spec, "evidence_policy", "non_empty") or "non_empty"

        # 从静态表中查询 policy（name 优先，其次 spec.name）
        policy = resolve_tool_evidence_policy(name, spec_name)
        has_configured_policy = any(
            candidate in TOOL_EVIDENCE_POLICY
            for candidate in (name, spec_name)
            if candidate
        )

        if existing:
            # evidence_types 已由工具自身声明；若 policy 需升级则单独注入
            if has_configured_policy and policy != existing_policy:
                return replace(spec, evidence_policy=policy)
            return spec

        evidence_types = resolve_tool_evidence_types(name, spec_name)
        if (
            not evidence_types
            and getattr(spec, "source_type", None) == "mcp"
            and not bool(getattr(spec, "evidence_inference_disabled", False))
            and _is_read_only_mcp_tool(
                name=spec_name or name,
                description=str(getattr(spec, "description", "") or ""),
            )
        ):
            evidence_types = frozenset({EvidenceType.EXTERNAL_TOOL})
            policy = "allow_empty_success"
        if not evidence_types:
            return spec
        return replace(
            spec,
            evidence_types=evidence_types,
            evidence_policy=policy,
        )

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
            config_item = None
            if isinstance(item, str):
                name = item
            elif isinstance(item, dict):
                name = item.get("name", "")
                try:
                    from app.schemas.agent import ToolConfigItem
                    config_item = ToolConfigItem(**item)
                except Exception as e:
                    logger.warning(f"Failed to parse runtime tool config dict: {e}")
            elif hasattr(item, "name"):
                name = getattr(item, "name")
                config_item = item
            else:
                name = ""

            if not name:
                continue

            spec = await cls.get_runtime_tool(name)
            if spec and config_item:
                spec = cls._configure_runtime_tool_spec(spec, config_item)
            if spec and spec.name not in seen_tool_names:
                runtime_tools.append(spec)
                seen_tool_names.add(spec.name)
        return runtime_tools

    @classmethod
    def _configure_runtime_tool_spec(cls, spec: Any, config: Any):
        from dataclasses import replace

        description = getattr(config, "description_override", None) or spec.description
        if spec.name != "get_dataset_schema":
            return replace(spec, description=description)

        metadata_dataset_ids = list(getattr(config, "metadata_dataset_ids", None) or [])
        original_callable = spec.callable

        async def invoke_schema_with_config(**kwargs: Any) -> Any:
            if metadata_dataset_ids:
                kwargs["metadata_dataset_ids"] = metadata_dataset_ids
            result = original_callable(**kwargs)
            import inspect
            if inspect.isawaitable(result):
                return await result
            return result

        return replace(spec, description=description, callable=invoke_schema_with_config)

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
                    {
                        "keywords": kwargs.get("keywords"),
                        "metadata_dataset_ids": kwargs.get("metadata_dataset_ids"),
                    },
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
            web_search_baidu,
            fetch_static_web_url,
        ]
