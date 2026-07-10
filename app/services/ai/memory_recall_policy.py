"""Heuristics and prompt hints for cross-session memory_search tool usage."""
import re
from typing import Any, List, Optional

CROSS_SESSION_MEMORY_SYSTEM_HINT = """[跨会话记忆检索]
当用户询问「今天/最近/上次/之前/我们聊了什么」「有没有讨论过」「回顾历史对话」等跨会话或过往对话内容时：
1. 如果当前工具集中提供 memory_search，必须先调用该工具（scope=summary，query 填用户问题的关键词；需要原文明细再用 scope=history 并带上 conversation_id）。
2. 仅依据 memory_search 的返回组织回答；若工具返回为空，如实说明「暂无跨会话摘要记录」，不要声称从未聊过。
3. 如果当前工具集中没有 memory_search，不要声称已经调用或检查了 memory_search，也不要编造历史内容；应如实说明当前无法检索跨会话记忆。
4. 不要把「当前会话 messages 为空」等同于「用户从未与你对话」——跨会话摘要可能在其他 conversation_id 中。"""

MEMORY_SEARCH_CORRECTION_MSG = (
    "【必须执行】用户正在询问跨会话或历史对话内容。"
    "请先调用 memory_search：scope 用 summary，query 用用户问题的关键词（例如「今天」「最近」）。"
    "在未获得工具返回前，禁止回答「从未聊过」「第一次对话」或编造历史内容。"
)

# 回顾跨会话 / 历史对话意图（中文口语）
_RECALL_QUERY_PATTERNS = [
    re.compile(r"今天.{0,12}聊", re.I),
    re.compile(r"最近.{0,12}聊", re.I),
    re.compile(r"上次.{0,12}聊", re.I),
    re.compile(r"上次.{0,12}讨论", re.I),
    re.compile(r"之前.{0,12}讨论", re.I),
    re.compile(r"之前.{0,12}聊", re.I),
    re.compile(r"我们.{0,8}聊", re.I),
    re.compile(r"咱俩.{0,8}聊", re.I),
    re.compile(r"聊了(些)?啥", re.I),
    re.compile(r"聊(了|过)(些)?什么", re.I),
    re.compile(r"说(了|过)(些)?什么", re.I),
    re.compile(r"讨论(了|过)(些)?什么", re.I),
    re.compile(r"有没有.{0,6}聊", re.I),
    re.compile(r"回顾.{0,8}(对话|聊天|会话)", re.I),
    re.compile(r"历史.{0,6}(对话|聊天|会话)", re.I),
    re.compile(r"过往.{0,6}(对话|聊天|会话)", re.I),
    re.compile(r"记得.{0,10}(聊|说|讨论)", re.I),
    re.compile(r"(?:上次|之前).{0,12}(?:说过|提过|偏好|要求|约定)", re.I),
]


def looks_like_cross_session_recall_query(text: Optional[str]) -> bool:
    if not text or not str(text).strip():
        return False
    normalized = str(text).strip()
    return any(p.search(normalized) for p in _RECALL_QUERY_PATTERNS)


def last_user_message_text(messages: List[Any]) -> str:
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            return (m.get("content") or "").strip()
        role = getattr(m, "type", None) or getattr(m, "role", None)
        if role in ("human", "user"):
            content = getattr(m, "content", None)
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                parts = [
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                return " ".join(parts).strip()
    return ""


def tools_include_memory_search(tools: List[Any]) -> bool:
    for t in tools or []:
        if getattr(t, "name", None) == "memory_search":
            return True
    return False
