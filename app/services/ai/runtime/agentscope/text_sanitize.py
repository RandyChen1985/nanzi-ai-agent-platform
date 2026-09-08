from __future__ import annotations

import re

_THINK_BLOCK_RE = re.compile(
    r"<\s*think\b[^>]*>[\s\S]*?<\s*/\s*think\s*>",
    re.DOTALL | re.IGNORECASE,
)
_THINK_UNCLOSED_RE = re.compile(
    r"<\s*think\b[^>]*>[\s\S]*\Z",
    re.DOTALL | re.IGNORECASE,
)
_THOUGHT_BLOCK_RE = re.compile(
    r"<\s*thought\b[^>]*>[\s\S]*?<\s*/\s*thought\s*>",
    re.DOTALL | re.IGNORECASE,
)
_THOUGHT_UNCLOSED_RE = re.compile(
    r"<\s*thought\b[^>]*>[\s\S]*\Z",
    re.DOTALL | re.IGNORECASE,
)
_REASONING_BLOCK_RE = re.compile(
    r"<\s*(?:reasoning|redacted_reasoning)\b[^>]*>[\s\S]*?<\s*/\s*(?:reasoning|redacted_reasoning)\s*>",
    re.DOTALL | re.IGNORECASE,
)
_FUNCTION_CALLS_RE = re.compile(
    r"<function_calls>[\s\S]*?</function_calls>",
    re.DOTALL | re.IGNORECASE,
)
_FUNCTION_CALLS_UNCLOSED_RE = re.compile(
    r"<function_calls>[\s\S]*\Z",
    re.DOTALL | re.IGNORECASE,
)
_THINKING_SECTION_RE = re.compile(
    r"(?im)^\s*(?:思考过程|深度思考|推理过程|Thinking|Reasoning)\s*[:：]?\s*\n"
    r"(?:.*\n)*?(?=^\s*(?:回答|结论|最终|分析结论|Result|Answer)\s*[:：]?|\Z)"
)

# 模型常把调度侧「结果通知说明」复述进正文，推送时需剔除
_TASKCENTER_META_MARKERS = (
    "【结果通知说明】",
    "结果通知说明",
    "TaskCenter 统一投递",
    "由 TaskCenter 统一投递",
    "send_portal_notification",
    "send_dingtalk_message",
    "send_wechat_work_message",
    "send_feishu_message",
    "无需也不应调用",
    "无需、也不应调用",
    "将由系统统一投递",
    "统一投递至站内",
    "统一投递到已勾选渠道",
)


def sanitize_assistant_stream_text(text: str) -> str:
    """剥离推理块与 XML 工具块，保留可展示正文。"""
    if not text:
        return ""
    cleaned = _THINK_BLOCK_RE.sub("", text)
    cleaned = _THINK_UNCLOSED_RE.sub("", cleaned)
    cleaned = _THOUGHT_BLOCK_RE.sub("", cleaned)
    cleaned = _THOUGHT_UNCLOSED_RE.sub("", cleaned)
    cleaned = _REASONING_BLOCK_RE.sub("", cleaned)
    cleaned = _FUNCTION_CALLS_RE.sub("", cleaned)
    cleaned = _FUNCTION_CALLS_UNCLOSED_RE.sub("", cleaned)
    return cleaned


def strip_taskcenter_delivery_meta(content: str) -> str:
    """去掉模型复述的 TaskCenter / 通知工具投递元话术。"""
    text = str(content or "").strip()
    if not text:
        return ""

    chunks = re.split(r"\n\s*\n", text)
    kept: list[str] = []
    for chunk in chunks:
        block = chunk.strip()
        if not block:
            continue
        if any(marker in block for marker in _TASKCENTER_META_MARKERS):
            # 同一段里若混有业务结论，尽量只删含元话术的句子
            sentences = re.split(r"(?<=[。！？；\n])", block)
            filtered = [
                s for s in sentences
                if s.strip() and not any(marker in s for marker in _TASKCENTER_META_MARKERS)
            ]
            block = "".join(filtered).strip()
            if not block:
                continue
        kept.append(block)
    return "\n\n".join(kept).strip()


def strip_model_reasoning_from_answer(
    content: str,
    *,
    reasoning_content: str | None = None,
) -> str:
    """
    对齐 EmbedChat：``reasoningContent``（模型思考折叠面板）与正文分离。

    - 去掉 ``<think>`` / ``<thought>`` 等标签块
    - 若流式推理文本被错误拼进正文，按 ``reasoning_content`` 再剔除一次
    - 去掉 TaskCenter 投递说明复述（不应出现在用户可见推送里）
    """
    text = sanitize_assistant_stream_text(str(content or ""))
    text = _THINKING_SECTION_RE.sub("", text)

    reasoning = str(reasoning_content or "").strip()
    if reasoning and len(reasoning) >= 8:
        if reasoning in text:
            text = text.replace(reasoning, "")
        elif text.startswith(reasoning):
            text = text[len(reasoning) :]
        else:
            # 推理作为正文前缀的常见泄漏（允许尾部少量截断差）
            overlap = min(len(reasoning), len(text))
            prefix_len = 0
            for size in range(overlap, 31, -1):
                if text.startswith(reasoning[:size]):
                    prefix_len = size
                    break
            if prefix_len >= 32:
                text = text[prefix_len:]

    text = strip_taskcenter_delivery_meta(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
