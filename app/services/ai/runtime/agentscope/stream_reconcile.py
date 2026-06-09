from __future__ import annotations

import re

# 流式 SSE 已发送正文 vs AgentState 最终 assistant 文本的对齐（通用，不依赖场景 if/else）

DEFAULT_MIN_COMPLETE_CHARS = 32
DEFAULT_TOOL_OUTPUT_MAX_LEN = 4000
DEFAULT_TOOL_LOG_MAX_LEN = 500


def truncate_for_context(text: str, *, max_len: int = DEFAULT_TOOL_OUTPUT_MAX_LEN) -> str:
    """工具结果写入 synthesis / 日志时的通用截断。"""
    raw = str(text or "")
    if len(raw) <= max_len:
        return raw
    return raw[:max_len] + "\n… [输出已截断]"


def compute_stream_reconcile_gap(streamed: str, agent_text: str) -> str:
    """
    计算 AgentState 中相对已流式发送内容多出的可展示正文。
    返回应追加到 SSE 的片段；无缺口则返回空字符串。
    """
    streamed_raw = streamed or ""
    agent_raw = (agent_text or "").strip()
    if not agent_raw:
        return ""

    streamed_stripped = streamed_raw.strip()
    if not streamed_stripped:
        return agent_raw

    if agent_raw.startswith(streamed_stripped):
        extra = agent_raw[len(streamed_stripped) :]
        return extra if extra.strip() else ""

    if streamed_stripped in agent_raw:
        idx = agent_raw.find(streamed_stripped)
        extra = agent_raw[idx + len(streamed_stripped) :]
        return extra if extra.strip() else ""

    if len(agent_raw) > len(streamed_stripped) + 20:
        return agent_raw

    return ""


def effective_reply_length(streamed: str, agent_text: str) -> int:
    """取 streamed 与 agent 文本中较长者作为有效回答长度。"""
    streamed_len = len((streamed or "").strip())
    agent_len = len((agent_text or "").strip())
    return max(streamed_len, agent_len)


def needs_tool_synthesis_fallback(
    streamed: str,
    agent_text: str,
    *,
    used_tools: bool,
    min_complete_chars: int = DEFAULT_MIN_COMPLETE_CHARS,
) -> bool:
    """工具链跑完后，若用户可见正文过短，则走 synthesis LLM（仅按 text 块判定，不含 thinking）。"""
    if not used_tools:
        return False
    streamed_len = len((streamed or "").strip())
    if streamed_len >= min_complete_chars:
        return False
    agent_len = len((agent_text or "").strip())
    return max(streamed_len, agent_len) < min_complete_chars


GENERIC_SYNTHESIS_EMPTY_FALLBACK = (
    "未能生成完整回答，请查看上方工具执行日志，或简化问题后重试。"
)


def _normalize_reply_for_compare(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def collapse_repeated_reply(text: str, *, min_half_len: int = 200) -> str:
    """
    若模型将同一段回答几乎原样输出两遍，保留前半段。
    用于复用上一轮结果的 synthesis 等直出路径。
    """
    raw = text or ""
    stripped = raw.strip()
    if len(stripped) < min_half_len * 2:
        return raw

    midpoint = len(stripped) // 2
    first_half = stripped[:midpoint].strip()
    second_half = stripped[midpoint:].strip()
    if len(first_half) < min_half_len:
        return raw

    norm_first = _normalize_reply_for_compare(first_half)
    norm_second = _normalize_reply_for_compare(second_half)
    if not norm_first or not norm_second:
        return raw

    if norm_first == norm_second:
        return first_half

    prefix_len = min(len(norm_first), 500)
    if prefix_len >= min_half_len and norm_second.startswith(norm_first[:prefix_len]):
        return first_half

    anchor = norm_first[: min(240, len(norm_first))]
    if len(anchor) >= min_half_len // 2:
        repeat_at = stripped.find(anchor, len(first_half))
        if repeat_at > len(first_half):
            return stripped[:repeat_at].rstrip()

    return raw
