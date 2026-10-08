"""未注册工具调用（ghost tool）的拦截说明构造。

运行时白名单只认模型侧调用名：MCP 工具注册的是别名，而平台内部标识是
``server_name:tool_name``。模型若照抄平台内部标识（提示词清单、历史消息或技能文档
里出现过），拦截说明必须点明口径差异，否则"未在本智能体注册"会误导排障。
"""

from __future__ import annotations

from typing import Any, Iterable

_GHOST_TOOL_TITLE_PREFIX = "⚠️ 工具调用已拦截"


def registered_model_name_for(tool_name: str, tools: Iterable[Any] | None) -> str:
    """返回与该平台标识对应的已注册模型调用名；未注册时返回空串。"""
    if not tool_name:
        return ""
    for tool in tools or ():
        if str(getattr(tool, "display_name", "") or "") == tool_name:
            return str(getattr(tool, "name", "") or "")
    return ""


def describe_unregistered_tool_call(
    tool_name: str,
    tools: Iterable[Any] | None,
) -> dict[str, str]:
    """构造拦截卡片的标题与详情。"""
    name = str(tool_name or "")
    model_name = registered_model_name_for(name, tools)
    if model_name:
        details = (
            f"工具 `{name}` 是本平台 MCP 工具的内部标识，不是模型调用名；"
            f"本轮实际注册的调用名是 `{model_name}`。"
            f"调用已按名称口径不一致拦截，模型已收到错误反馈，将重新生成回答。"
        )
    else:
        details = (
            f"工具 `{name}` 未在本轮注册的工具名单中（可能未绑定、未发布、已下线或名称有误）。"
            f"模型已收到错误反馈，将重新生成回答。"
        )
    return {"title": f"{_GHOST_TOOL_TITLE_PREFIX}: {name}", "details": details}
