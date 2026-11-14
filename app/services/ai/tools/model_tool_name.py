"""模型侧工具调用名归一化的单一来源。

平台内部用 ``server_name:tool_name`` 作为 MCP 工具唯一标识（避免跨服务器重名），
但 OpenAI 兼容的 Function Calling 只接受 ``^[a-zA-Z0-9_-]+$``。因此注册给模型的
名字必须是合法别名。工具清单、提示词和日志建议使用相同的调用名；模型还可以通过
用户在本轮明确给出的标识找到已经挂载的 MCP 工具。

本模块只依赖标准库，便于提示词组装等热路径引用。
"""

from __future__ import annotations

import hashlib
import re

# OpenAI Function Calling 通常限制工具名最多 64 个字符。
MODEL_TOOL_NAME_MAX_LENGTH = 64
_MODEL_TOOL_NAME_PREFIX = "mcp_"
# 平台 MCP 工具的命名空间分隔符：``server_name:tool_name``。
PLATFORM_MCP_TOOL_SEPARATOR = ":"
_MODEL_MCP_TOOL_NAME = re.compile(rf"{_MODEL_TOOL_NAME_PREFIX}[0-9a-f]{{20}}")
_TOOL_REFERENCE_CHAR = r"[a-zA-Z0-9_.:/+-]"


def build_model_tool_name(platform_tool_name: str) -> str:
    """注册稳定的模型别名。外部工具名称不作为模型输入。"""
    name_hash = hashlib.sha256(str(platform_tool_name).encode("utf-8")).hexdigest()[:20]
    return f"{_MODEL_TOOL_NAME_PREFIX}{name_hash}"


def is_model_mcp_tool_name(tool_name: str) -> bool:
    return _MODEL_MCP_TOOL_NAME.fullmatch(tool_name) is not None


def mcp_tool_reference_from_query(
    platform_name: str | None,
    model_name: str,
    user_query: str,
) -> str | None:
    """仅取用户本轮已给出、且与已解析 MCP 调用名一致的完整标识。"""
    if not user_query or not platform_name or len(platform_name) > 255:
        return None
    server_name, _, tool_name = platform_name.partition(PLATFORM_MCP_TOOL_SEPARATOR)
    if (
        not server_name.strip()
        or not tool_name.strip()
        or not platform_name.isprintable()
        or any(char in platform_name for char in "<>`")
        or build_model_tool_name(platform_name) != model_name
    ):
        return None
    match = re.search(
        rf"(?<!{_TOOL_REFERENCE_CHAR}){re.escape(platform_name)}(?!{_TOOL_REFERENCE_CHAR})",
        user_query,
    )
    return match[0] if match else None


def is_platform_namespaced_tool_name(tool_name: str | None) -> bool:
    """判断名字是否为平台命名空间工具标识（``server_name:tool_name``）。"""
    return PLATFORM_MCP_TOOL_SEPARATOR in str(tool_name or "")


def to_runtime_tool_names(platform_tool_name: str | None) -> tuple[str, ...]:
    """平台侧工具名 → 运行时应被视为「同一个工具」的全部名字。

    禁用名单里存的是平台名（``jira_search`` / ``server:tool``），而 MCP 工具在运行时
    注册的是 ``build_model_tool_name()`` 生成的模型调用名（``mcp_<hash>``）。
    拦截判定必须两侧都能命中，否则会出现「勾了禁用却拦不住」——策略形同虚设。
    内置工具名不受影响（运行时就是原名，额外变体只可能命中同名 MCP 工具，与
    按名字拦截的语义一致）。
    """
    name = str(platform_tool_name or "").strip()
    if not name:
        return ()
    return (name, build_model_tool_name(name))


def to_model_tool_name(tool_name: str | None) -> str:
    """把任意平台侧工具名归一化为模型调用名；非命名空间名字原样返回。"""
    name = str(tool_name or "").strip()
    if is_platform_namespaced_tool_name(name):
        return build_model_tool_name(name)
    return name
