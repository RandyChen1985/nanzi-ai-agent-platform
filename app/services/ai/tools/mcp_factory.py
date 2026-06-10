import json
import logging
from typing import Dict, Any
from pydantic import create_model, Field
from app.services.ai.tools.tool_compat import StructuredTool
from app.models.mcp import McpToolCache
from app.services.ai.tools.mcp_client import McpClientService

logger = logging.getLogger(__name__)

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
            p_type = str
            type_str = param_def.get("type", "string")
            if type_str == "integer": p_type = int
            elif type_str == "boolean": p_type = bool
            elif type_str == "number": p_type = float
            
            p_desc = param_def.get("description", "")
            p_default = ... if param_name in required_fields else param_def.get("default", None)
            
            fields[param_name] = (p_type, Field(default=p_default, description=p_desc))
        
        # Create dynamic Pydantic model for args
        args_schema = create_model(f"Mcp_{tool_record.tool_name.replace(':', '_')}Args", **fields)
        
        # 2. Define execution logic
        async def _execute(**kwargs) -> str:
            # Extract raw tool name (remove our prefix)
            # Full name: "server_name:raw_tool_name"
            if ":" in tool_record.tool_name:
                raw_name = tool_record.tool_name.split(":", 1)[1]
            else:
                raw_name = tool_record.tool_name
                
            return await McpClientService.call_remote_tool(
                server_id=tool_record.server_id,
                tool_name=raw_name,
                arguments=kwargs
            )
        
        _execute.__doc__ = tool_record.tool_description or f"MCP tool: {tool_record.tool_name}"
        
        # Tool name should ideally be our full name to avoid collisions
        return StructuredTool.from_function(
            func=None,
            coroutine=_execute,
            name=tool_record.tool_name,
            description=tool_record.tool_description or "",
            args_schema=args_schema
        )
