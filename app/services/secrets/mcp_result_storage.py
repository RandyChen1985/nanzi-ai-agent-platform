"""保留平台按 JSONPath 选中的结果；其余持久化字段继续走通用扫描。"""
from __future__ import annotations

from typing import Any

from app.services.secrets.mcp_model_result import ProjectedMcpResult

MCP_JSONPATH_RESULT = "mcp_jsonpath_result"


def projected_result_metadata(result: Any) -> dict[str, str]:
    """来源标记只由本地投影类型生成，不接受业务 JSON 自报可见性。"""
    return {MCP_JSONPATH_RESULT: "v1"} if isinstance(result, ProjectedMcpResult) else {}


def is_projected_result_metadata(metadata: Any) -> bool:
    return isinstance(metadata, dict) and metadata.get(MCP_JSONPATH_RESULT) == "v1"


def restore_projected_state_outputs(original: Any, scanned: Any) -> Any:
    """只还原 AgentScope 已标记工具结果的 output，参数和普通消息不豁免。"""
    if not isinstance(original, dict) or not isinstance(scanned, dict):
        return scanned
    messages = original.get("context")
    safe_messages = scanned.get("context")
    if not isinstance(messages, list) or not isinstance(safe_messages, list):
        return scanned
    for message, safe_message in zip(messages, safe_messages):
        if not isinstance(message, dict) or not isinstance(safe_message, dict):
            continue
        blocks, safe_blocks = message.get("content"), safe_message.get("content")
        if not isinstance(blocks, list) or not isinstance(safe_blocks, list):
            continue
        for block, safe_block in zip(blocks, safe_blocks):
            if (isinstance(block, dict) and isinstance(safe_block, dict)
                    and block.get("type") == "tool_result"
                    and is_projected_result_metadata(block.get("metadata"))
                    and "output" in block):
                safe_block["output"] = block["output"]
    return scanned


def restore_projected_timeline_outputs(original: Any, scanned: Any) -> Any:
    """时间线只保留已标记日志的结果 details，tool_args 等字段仍用扫描值。"""
    if not isinstance(original, list) or not isinstance(scanned, list):
        return scanned
    for item, safe_item in zip(original, scanned):
        if not isinstance(item, dict) or not isinstance(safe_item, dict):
            continue
        if (item.get("kind") == "log" and is_projected_result_metadata(item)
                and "details" in item):
            safe_item["details"] = item["details"]
        restore_projected_timeline_outputs(item.get("children"), safe_item.get("children"))
    return scanned
