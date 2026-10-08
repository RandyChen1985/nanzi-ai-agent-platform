"""模型侧工具调用名归一化的单一来源。

平台内部用 ``server_name:tool_name`` 作为 MCP 工具唯一标识（避免跨服务器重名），
但 OpenAI 兼容的 Function Calling 只接受 ``^[a-zA-Z0-9_-]+$``。因此注册给模型的
名字必须是合法别名，而所有"给模型看的工具名"（工具清单、提示词、日志建议）都必须
使用同一个别名，否则模型会照抄平台内部标识并被运行时白名单拦截。

本模块只依赖标准库，便于提示词组装等热路径引用。
"""

from __future__ import annotations

import hashlib
import re

# OpenAI Function Calling 通常限制工具名最多 64 个字符。
MODEL_TOOL_NAME_MAX_LENGTH = 64
_MODEL_TOOL_NAME_PREFIX = "mcp_"
_READABLE_NAME_FALLBACK = "mcp_tool"
# 平台 MCP 工具的命名空间分隔符：``server_name:tool_name``。
PLATFORM_MCP_TOOL_SEPARATOR = ":"


def build_model_tool_name(platform_tool_name: str) -> str:
    """把平台工具标识转换为模型 Function Calling 可接受的稳定工具名。

    保留可读部分并追加原始名称哈希，既避免非法字符，也避免不同原名清洗后发生碰撞。
    """
    raw_name = str(platform_tool_name)
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", raw_name).strip("_")
    readable_name = normalized or _READABLE_NAME_FALLBACK
    name_hash = hashlib.sha256(raw_name.encode("utf-8")).hexdigest()[:10]
    max_readable_length = (
        MODEL_TOOL_NAME_MAX_LENGTH - len(_MODEL_TOOL_NAME_PREFIX) - len(name_hash) - 1
    )
    return f"{_MODEL_TOOL_NAME_PREFIX}{readable_name[:max_readable_length]}_{name_hash}"


def is_platform_namespaced_tool_name(tool_name: str | None) -> bool:
    """判断名字是否为平台命名空间工具标识（``server_name:tool_name``）。"""
    return PLATFORM_MCP_TOOL_SEPARATOR in str(tool_name or "")


def to_model_tool_name(tool_name: str | None) -> str:
    """把任意平台侧工具名归一化为模型调用名；非命名空间名字原样返回。"""
    name = str(tool_name or "").strip()
    if is_platform_namespaced_tool_name(name):
        return build_model_tool_name(name)
    return name
