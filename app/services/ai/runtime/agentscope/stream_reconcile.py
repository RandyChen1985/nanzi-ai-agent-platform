from __future__ import annotations

import re

# 流式 SSE 已发送正文 vs AgentState 最终 assistant 文本的对齐（通用，不依赖场景 if/else）

DEFAULT_MIN_COMPLETE_CHARS = 32
DEFAULT_TOOL_OUTPUT_MAX_LEN = 4000
DEFAULT_TOOL_LOG_MAX_LEN = 500
BOOKKEEPING_TOOL_NAMES = frozenset({"todo_write"})


def truncate_for_context(text: str, *, max_len: int = DEFAULT_TOOL_OUTPUT_MAX_LEN) -> str:
    """工具结果写入 synthesis / 历史摘要时的通用截断。"""
    raw = str(text or "")
    if len(raw) <= max_len:
        return raw
    return raw[:max_len] + "\n… [输出已截断]"


def truncate_for_display(text: str, *, max_len: int = DEFAULT_TOOL_LOG_MAX_LEN) -> str:
    """截断用户时间线中的日志预览，并明确这是展示层截断。"""

    raw = str(text or "")
    if len(raw) <= max_len:
        return raw
    return raw[:max_len] + "\n… [日志预览已截断]"


def build_tool_review_lines(
    tool_names: dict | None,
    tool_outputs: dict | None,
    *,
    tool_result_states: dict | None = None,
    max_len: int = DEFAULT_TOOL_OUTPUT_MAX_LEN,
) -> list[str]:
    """构造兜底合成输入，排除过程状态和仅用于编排的工具结果。

    ``tool_result_states`` 中有对应调用 ID 时，只接受最终成功状态；缺少对应
    状态的旧事件仍按 payload 做错误过滤，兼容没有 AgentScope 最终状态的回顾调用方。
    """

    names = tool_names or {}
    outputs = tool_outputs or {}
    lines: list[str] = []
    has_final_state_map = isinstance(tool_result_states, dict) and bool(tool_result_states)
    for tool_id, output in outputs.items():
        if isinstance(tool_result_states, dict):
            result_state = tool_result_states.get(tool_id)
            if (
                has_final_state_map
                and tool_id not in tool_result_states
            ):
                continue
            if (
                tool_id in tool_result_states
                and str(result_state or "").strip().lower()
                not in {"success", "succeeded", "finished", "completed"}
            ):
                continue
            # 状态成功仍不能覆盖 payload 自身的错误标记；这里是给兜底
            # synthesis 的模型上下文，也要与 EvidenceLedger 保持同一收口语义。
            from app.services.ai.grounding.ledger import classify_evidence_result
            from app.services.ai.grounding.models import EvidenceStatus

            if classify_evidence_result(output) not in {
                EvidenceStatus.SUCCESS_NON_EMPTY,
                EvidenceStatus.SUCCESS_EMPTY,
            }:
                continue
        tool_name = str(names.get(tool_id) or tool_id or "").strip()
        if not tool_name or tool_name.casefold() in BOOKKEEPING_TOOL_NAMES:
            continue
        if str(output or "").strip():
            lines.append(f"- {tool_name}: {truncate_for_context(output, max_len=max_len)}")
    return lines


_SUBSTANTIAL_OVERLAP_CHARS = 64


def _compact_reply(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _visible_suffix_after_whitespace_prefix(prefix: str, text: str) -> str | None:
    """If text starts with prefix ignoring whitespace, return the leftover suffix.

    Empty string means they match. None means prefix is not a prefix of text.
    """
    i = 0
    j = 0
    n_prefix = len(prefix)
    n_text = len(text)

    while i < n_prefix and j < n_text:
        while i < n_prefix and prefix[i].isspace():
            i += 1
        while j < n_text and text[j].isspace():
            j += 1
        if i >= n_prefix or j >= n_text:
            break
        if prefix[i] != text[j]:
            return None
        i += 1
        j += 1

    while i < n_prefix and prefix[i].isspace():
        i += 1
    if i < n_prefix:
        return None
    return text[j:]


def compute_stream_reconcile_gap(streamed: str, agent_text: str) -> str:
    """
    计算 AgentState 中相对已流式发送内容多出的可展示正文。
    返回应追加到 SSE 的片段；无缺口则返回空字符串。

    空白差异或 AgentState 拼接前缀不应把已流式正文整段再发一遍。
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

    whitespace_suffix = _visible_suffix_after_whitespace_prefix(streamed_stripped, agent_raw)
    if whitespace_suffix is not None:
        return whitespace_suffix if whitespace_suffix.strip() else ""

    streamed_norm = _normalize_reply_for_compare(streamed_stripped)
    agent_norm = _normalize_reply_for_compare(agent_raw)
    if streamed_norm and agent_norm:
        if agent_norm == streamed_norm:
            return ""
        if streamed_norm.startswith(agent_norm):
            return ""
        if streamed_norm in agent_norm or agent_norm in streamed_norm:
            return ""

    compact_streamed = _compact_reply(streamed_stripped)
    compact_agent = _compact_reply(agent_raw)
    if compact_streamed and compact_agent and len(compact_streamed) >= _SUBSTANTIAL_OVERLAP_CHARS:
        if compact_agent == compact_streamed:
            return ""
        if compact_agent.startswith(compact_streamed):
            suffix = _visible_suffix_after_whitespace_prefix(streamed_stripped, agent_raw)
            return suffix if suffix and suffix.strip() else ""
        if (
            compact_streamed in compact_agent
            or compact_agent in compact_streamed
            or compact_streamed.startswith(compact_agent)
        ):
            return ""

    # AgentScope 会把工具环多轮输出的过程旁白与最终正文拼进同一条 assistant 文本
    # （extract_latest_assistant_text 按 text block """.join""）。此时 agent 相对
    # streamed 多出的往往只是开头的过程话术，而最终正文已全部流式展示。若去除空白后
    # agent 以 streamed 结尾，说明正文无任何新增，再整段补发会把已展示正文与旁白重复发一遍。
    compact_agent = _compact_reply(agent_raw)
    compact_streamed = _compact_reply(streamed_stripped)
    if (
        compact_agent
        and compact_streamed
        and len(compact_agent) > len(compact_streamed)
        and compact_agent.endswith(compact_streamed)
    ):
        return ""

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
    tool_names: dict | None = None,
    tool_outputs: dict | None = None,
    tool_result_states: dict | None = None,
    min_complete_chars: int = DEFAULT_MIN_COMPLETE_CHARS,
) -> bool:
    """Only synthesize when tools ran but no usable final text was streamed."""
    if not used_tools:
        return False
    if (streamed or "").strip() or (agent_text or "").strip():
        return False
    return bool(
        build_tool_review_lines(
            tool_names,
            tool_outputs,
            tool_result_states=tool_result_states,
        )
    )


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


_QUICK_TARGET = r"[^()\n]*(?:\([^()\n]*\)[^()\n]*)*"
_QUICK_LINK = rf"\[[^\]]+\]\(\s*quick:{_QUICK_TARGET}\)"

_QUICK_SECTION_BLOCK = re.compile(
    r"(###\s*[^\n]*(?:您可能还想了解|您可以这样继续)[^\n]*\s*"
    r"(?:---\s*)?"
    rf"(?:\n\s*- { _QUICK_LINK })+)",
    re.IGNORECASE | re.MULTILINE,
)
_QUICK_MARKDOWN_LINK = re.compile(_QUICK_LINK, re.IGNORECASE)
_QUICK_PROTOCOL = re.compile(rf"\(?\s*quick:{_QUICK_TARGET}\)?", re.IGNORECASE)
_QUICK_SECTION_TITLE = re.compile(
    r"^\s*#{2,6}\s*(?:💬\s*)?(?:您可能还想了解|您可以这样继续|一键继续)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def move_quick_suggestions_to_end(text: str) -> str:
    """将 quick 追问建议区块移动到全文末尾（位于图表、数据来源说明之后）。"""
    raw = text or ""
    match = _QUICK_SECTION_BLOCK.search(raw)
    if not match:
        return raw

    quick_block = match.group(1).strip()
    quick_start, quick_end = match.span(1)
    tail_after_quick = raw[quick_end:].strip()
    if not tail_after_quick:
        return raw
    if not re.search(r"```chart|###\s|[^\s]", tail_after_quick, flags=re.IGNORECASE):
        return raw

    without_quick = (raw[:quick_start] + raw[quick_end:]).strip()
    without_quick = re.sub(r"\n{3,}", "\n\n", without_quick)
    return f"{without_quick}\n\n{quick_block}\n"


def suppress_quick_suggestions(text: str) -> str:
    """Remove interactive quick protocol from non-interactive delivery output."""
    cleaned = _QUICK_SECTION_BLOCK.sub("", text or "")
    cleaned = re.sub(
        rf"^\s*[-*]\s*{_QUICK_LINK}\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    cleaned = _QUICK_MARKDOWN_LINK.sub("", cleaned)
    cleaned = _QUICK_PROTOCOL.sub("", cleaned)
    cleaned = _QUICK_SECTION_TITLE.sub("", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def finalize_visible_reply(text: str, *, collapse_duplicates: bool = True) -> str:
    """统一整理用户可见正文：去重后确保 quick 建议位于最后。"""
    normalized = collapse_repeated_reply(text) if collapse_duplicates else (text or "")
    return move_quick_suggestions_to_end(normalized)
