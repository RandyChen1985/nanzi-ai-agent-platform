from __future__ import annotations

import re

_THINK_BLOCK_RE = re.compile(
    r"<\s*think\b[^>]*>[\s\S]*?<\s*/\s*think\s*>",
    re.DOTALL | re.IGNORECASE,
)
_FUNCTION_CALLS_RE = re.compile(
    r"<function_calls>[\s\S]*?</function_calls>",
    re.DOTALL | re.IGNORECASE,
)


def sanitize_assistant_stream_text(text: str) -> str:
    """剥离推理块与 XML 工具块，保留可展示正文。"""
    if not text:
        return ""
    cleaned = _THINK_BLOCK_RE.sub("", text)
    cleaned = _FUNCTION_CALLS_RE.sub("", cleaned)
    return cleaned
