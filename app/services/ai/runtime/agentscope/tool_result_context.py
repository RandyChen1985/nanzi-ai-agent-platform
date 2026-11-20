"""工具最终结果跨模块使用的轻量上下文合同。"""

from __future__ import annotations

from typing import Any


TOOL_RESULT_CONTEXT_VERSION = "final_tool_result_v2"
TOOL_CALL_ID_METADATA_KEY = "nanzi_call_id"
#: 工具产出「计量信息」时挂在 ToolChunk 元数据上的键（图像生成的张数/尺寸/上游 token）。
#: 走元数据而不是塞进结果正文，是因为结果正文会整段进模型上下文：用量数字对模型没用，
#: 留着只会白花 token，还可能被复述进回答。
IMAGE_USAGE_METADATA_KEY = "image_usage"


def is_trusted_tool_result_context(message: Any) -> bool:
    """只信任当前版本生成的最终工具结果转录。"""

    return (
        isinstance(message, dict)
        and str(message.get("tool_run_text_version") or "").strip()
        == TOOL_RESULT_CONTEXT_VERSION
    )


def attach_tool_call_id_metadata(result: Any, call_id: str) -> Any:
    """把内部凭证 ID 放到 AgentScope ToolChunk 元数据，不改变业务 payload。"""

    if not call_id:
        return result
    if isinstance(result, (list, tuple)):
        return type(result)(attach_tool_call_id_metadata(item, call_id) for item in result)
    if hasattr(result, "metadata") and hasattr(result, "state"):
        try:
            metadata = dict(getattr(result, "metadata", None) or {})
            metadata.setdefault(TOOL_CALL_ID_METADATA_KEY, str(call_id))
            result.metadata = metadata
        except Exception:
            # 元数据仅用于关联，不应阻断工具原始结果回传。
            return result
    return result


def tool_call_id_from_metadata(value: Any) -> str:
    metadata = getattr(value, "metadata", None)
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get(TOOL_CALL_ID_METADATA_KEY) or "").strip()


def image_usage_from_metadata(value: Any) -> dict[str, Any] | None:
    """从工具结果对象上取计量信息；没有就返回 None。

    与 ``tool_call_id_from_metadata`` 一样只认元数据，不理会正文。
    """
    metadata = getattr(value, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    usage = metadata.get(IMAGE_USAGE_METADATA_KEY)
    return usage if isinstance(usage, dict) and usage else None
