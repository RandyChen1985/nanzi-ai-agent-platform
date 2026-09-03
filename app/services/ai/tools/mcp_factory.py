import json
import logging
import hashlib
import re
from typing import Dict, Any, List, Optional
from pydantic import create_model, Field
from app.services.ai.tools.tool_compat import StructuredTool
from app.models.mcp import McpToolCache
from app.services.ai.tools.mcp_client import McpClientService
from app.services.ai.grounding.models import EvidenceType
from app.core.context import get_current_agent_context

logger = logging.getLogger(__name__)


def current_mcp_agent_identity() -> tuple[Dict[str, Any], Dict[str, Any]]:
    """从当前后端 AgentContext 提取 MCP 断言所需身份，绝不读取工具参数。"""
    context = get_current_agent_context()
    if context is None:
        return {}, {}

    user_info = dict(context.user_dimensions or {})
    if context.user_id is not None:
        user_info["user_id"] = str(context.user_id)
    agent_info: Dict[str, Any] = {
        "agent_id": context.agent_id,
        "agent_name": context.agent_name,
    }
    if context.agent_version:
        agent_info["agent_version_id"] = context.agent_version
    return user_info, agent_info


def _build_model_tool_name(tool_name: str) -> str:
    """将平台 MCP 标识转换为模型 Function Calling 可接受的稳定工具名。

    平台以 ``server_name:tool_name`` 保存 MCP 工具，用冒号避免跨服务器重名；
    但 OpenAI 兼容模型只允许字母、数字、下划线和连字符。保留可读部分并追加
    原始名称哈希，既避免非法字符，也避免不同原名清洗后发生碰撞。
    """
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(tool_name)).strip("_")
    readable_name = normalized or "mcp_tool"
    name_hash = hashlib.sha256(str(tool_name).encode("utf-8")).hexdigest()[:10]
    # OpenAI Function Calling 通常限制工具名最多 64 个字符，提前截断以兼容该约束。
    max_readable_length = 64 - len("mcp_") - len(name_hash) - 1
    return f"mcp_{readable_name[:max_readable_length]}_{name_hash}"


def _map_schema_type(param_def: dict[str, Any]) -> Any:
    """根据 JSON Schema 定义映射为 Python/Pydantic 字段类型。"""
    raw_type = param_def.get("type")

    # 兼容 type 为列表的情况，如 ["string", "null"]
    if isinstance(raw_type, list):
        non_null_types = [t for t in raw_type if t != "null"]
        type_str = non_null_types[0] if non_null_types else "string"
    elif isinstance(raw_type, str):
        type_str = raw_type
    else:
        if "items" in param_def:
            type_str = "array"
        elif "properties" in param_def:
            type_str = "object"
        elif "enum" in param_def:
            type_str = "string"
        else:
            return Any

    if type_str == "integer":
        return int
    if type_str == "number":
        return float
    if type_str == "boolean":
        return bool
    if type_str == "array":
        return list
    if type_str == "object":
        return dict
    if type_str == "string":
        return str
    return Any


def _schema_allows_null(param_def: dict[str, Any]) -> bool:
    raw_type = param_def.get("type")
    return isinstance(raw_type, list) and "null" in raw_type


class McpToolFactory:
    @staticmethod
    def create_tool(tool_record: McpToolCache) -> StructuredTool:
        """
        Creates a runtime StructuredTool-compatible wrapper from a cached MCP tool record.
        """
        
        # 1. Parse JSON Schema from MCP
        schema_def = json.loads(tool_record.parameter_schema or "{}")
        properties = schema_def.get("properties", {})
        required_fields = set(schema_def.get("required", []))

        fields = {}
        for param_name, param_def in properties.items():
            if not isinstance(param_def, dict):
                param_def = {}
            mapped_type = _map_schema_type(param_def)
            p_desc = param_def.get("description", "")
            is_required = param_name in required_fields

            if is_required:
                p_default = ...
                field_type = Optional[mapped_type] if _schema_allows_null(param_def) else mapped_type
            else:
                p_default = param_def.get("default", None)
                field_type = Optional[mapped_type]

            fields[param_name] = (field_type, Field(default=p_default, description=p_desc))
        
        # Create dynamic Pydantic model for args
        clean_name = re.sub(r"[^a-zA-Z0-9_]+", "_", str(tool_record.tool_name)).strip("_") or "Tool"
        args_schema = create_model(f"Mcp_{clean_name}Args", **fields)
        
        # 2. Define execution logic
        async def _execute(**kwargs) -> Any:
            # Extract raw tool name (remove our prefix)
            # Full name: "server_name:raw_tool_name"
            if ":" in tool_record.tool_name:
                raw_name = tool_record.tool_name.split(":", 1)[1]
            else:
                raw_name = tool_record.tool_name

            user_info, agent_info = current_mcp_agent_identity()
            context = get_current_agent_context()
            identity_kwargs: Dict[str, Any] = {}
            if user_info:
                identity_kwargs["user_info"] = user_info
            if agent_info:
                identity_kwargs["agent_info"] = agent_info
            if context and context.trace_id:
                identity_kwargs["request_id"] = context.trace_id

            return await McpClientService.call_remote_tool(
                server_id=tool_record.server_id,
                tool_name=raw_name,
                arguments=kwargs,
                require_user_context=True,
                **identity_kwargs,
            )
        
        _execute.__doc__ = tool_record.tool_description or f"MCP tool: {tool_record.tool_name}"
        
        # 数据库存储的冒号名称仅作平台标识；模型侧使用合法别名，执行闭包仍保留原始 MCP 名称。
        tool = StructuredTool.from_function(
            func=None,
            coroutine=_execute,
            name=_build_model_tool_name(tool_record.tool_name),
            description=tool_record.tool_description or "",
            args_schema=args_schema
        )
        tool.display_name = tool_record.tool_name

        declared_types = set()
        for value in schema_def.get("x-nanzi-evidence-types") or []:
            try:
                declared_types.add(EvidenceType(value))
            except (TypeError, ValueError):
                logger.warning("Ignoring invalid evidence type %r for %s", value, tool_record.tool_name)
        annotations = schema_def.get("x-nanzi-mcp-annotations") or {}

        # 只读权限与证据推断
        from app.services.ai.tools.registry import _is_read_only_mcp_tool
        annotation_read_only = (
            annotations.get("readOnlyHint") is True
            or annotations.get("read_only_hint") is True
        )
        annotation_mutating = (
            annotations.get("readOnlyHint") is False
            or annotations.get("read_only_hint") is False
        )
        inferred_read_only = _is_read_only_mcp_tool(
            name=tool_record.tool_name,
            description=str(tool_record.tool_description or ""),
        )
        if annotation_mutating:
            read_only = False
            tool.evidence_inference_disabled = True
        else:
            # readOnlyHint 是远端自报的提示，不能覆盖变更动作的保守推断。
            read_only = inferred_read_only

        tool.is_read_only = read_only
        tool.permission_scope = "read" if read_only else "ask"

        if declared_types:
            tool.evidence_types = frozenset(declared_types)
        elif annotation_read_only or read_only:
            tool.evidence_types = frozenset({EvidenceType.EXTERNAL_TOOL})

        if getattr(tool, "evidence_types", None):
            declared_policy = schema_def.get("x-nanzi-evidence-policy")
            tool.evidence_policy = (
                declared_policy
                if declared_policy in {"non_empty", "structured_success", "allow_empty_success"}
                else "allow_empty_success"
            )
        return tool
