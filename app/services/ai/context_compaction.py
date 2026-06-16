"""会话上下文压缩（compaction）。

当历史消息超过上下文窗口（``agent_max_context_messages``）时，旧消息原本会被直接丢弃，
导致多轮指代/事实断档。本模块用**确定性、零额外 LLM 调用**的方式，把被丢弃的旧消息
压缩成一段简短摘录，作为 system 消息注入到上下文最前面（由 ``normalize_messages_for_llm``
合并到系统区），在不增加延迟的前提下尽量保留对话连续性。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

COMPACTION_MARKER = "[早前对话摘录]"

# 单条消息在摘录中的最大字符数，超过则截断。
_DEFAULT_PER_MESSAGE_CHARS = 120
# 整段摘录的最大字符数。
_DEFAULT_MAX_CHARS = 1200

_ROLE_LABELS = {
    "user": "用户",
    "assistant": "助手",
    "system": "系统",
}


def _flatten_content(content: Any) -> str:
    """将可能为多模态结构的 content 归一为纯文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text" and item.get("text"):
                    parts.append(str(item["text"]))
                elif item.get("type") == "image_url":
                    parts.append("[图片]")
            elif isinstance(item, str):
                parts.append(item)
        return " ".join(p for p in parts if p)
    return str(content)


def _condense(text: str, limit: int) -> str:
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(1, limit - 1)].rstrip() + "…"


def build_overflow_digest(
    dropped_messages: List[Dict[str, Any]],
    *,
    max_chars: int = _DEFAULT_MAX_CHARS,
    per_message_chars: int = _DEFAULT_PER_MESSAGE_CHARS,
) -> Optional[Dict[str, str]]:
    """把被丢弃的旧消息压缩为一条 system 摘录消息。

    返回 ``{"role": "system", "content": ...}``；若无可用内容则返回 ``None``。
    纯文本拼接，不调用任何模型。最新的旧消息排在摘录末尾（更贴近当前上下文）。
    """
    if not dropped_messages:
        return None

    lines: List[str] = []
    for msg in dropped_messages:
        role = (msg.get("role") or "").strip()
        if role not in _ROLE_LABELS:
            continue
        text = _condense(_flatten_content(msg.get("content")), per_message_chars)
        if not text:
            continue
        lines.append(f"- {_ROLE_LABELS[role]}：{text}")

    if not lines:
        return None

    # 从最新往回累加，保证保留的是离当前最近的旧消息；最终再恢复时间顺序。
    selected: List[str] = []
    used = 0
    for line in reversed(lines):
        cost = len(line) + 1
        if selected and used + cost > max_chars:
            break
        selected.append(line)
        used += cost
    selected.reverse()

    if not selected:
        return None

    body = "\n".join(selected)
    content = (
        f"{COMPACTION_MARKER}\n"
        "以下是更早轮次对话的要点（已压缩，仅供理解上下文与指代，不要逐条复述）：\n"
        f"{body}"
    )
    return {"role": "system", "content": content}


def apply_context_compaction(
    *,
    full_history: List[Dict[str, Any]],
    window: List[Dict[str, Any]],
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> List[Dict[str, Any]]:
    """在窗口前注入溢出摘录。

    ``full_history``：完整历史；``window``：截断后保留的窗口（不含本轮新消息）。
    若没有溢出（full_history 未超过 window）则原样返回 window。
    """
    if not full_history or len(full_history) <= len(window):
        return window
    dropped = full_history[: len(full_history) - len(window)]
    digest = build_overflow_digest(dropped, max_chars=max_chars)
    if not digest:
        return window
    return [digest] + window
