"""Bounded, JSON-safe copies used for execution-audit persistence and responses."""

from __future__ import annotations

import json
import math
from typing import Any


DEFAULT_AUDIT_PAYLOAD_BYTES = 64 * 1024

# 对话历史（ai_agent_execution_history）的长文本列在 MySQL 上是 MEDIUMTEXT
# （16,777,215 字节），但仍要留一道保底：MySQL 的 max_allowed_packet 可能只有
# 4MB，且超长 bind 参数会先撞上包上限而不是列上限。1MiB 远高于正常轮次
# （数万字的思考约百 KB 级），只在极端内容上生效。
MAX_HISTORY_TEXT_BYTES = 1024 * 1024
# 首次写入被数据库判为超长时的降级阈值：宁可少存一部分，也不能丢整轮历史。
FALLBACK_HISTORY_TEXT_BYTES = 256 * 1024
HISTORY_TRUNCATION_MARKER = "\n...(truncated)"


def _json_bytes(value: Any) -> bytes:
    # SQLAlchemy's JSON serializer uses ASCII escaping by default. Use the
    # conservative representation here so the database bind value cannot
    # exceed the advertised byte budget for CJK/emoji text.
    return json.dumps(value, ensure_ascii=True, default=str, allow_nan=False).encode("utf-8")


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _truncate_utf8(value: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def truncate_text_bytes(
    value: str | None,
    *,
    max_bytes: int,
    marker: str = HISTORY_TRUNCATION_MARKER,
) -> tuple[str | None, int]:
    """按 UTF-8 字节上限截断文本，返回 ``(截断后文本, 原始字节数)``。

    超限时保留前缀并追加 ``marker``，截断点对齐到字符边界（不会留下半个汉字）。
    数据库侧的超限是**整行失败**，所以这里宁可少存尾部，也不能让 INSERT 打回。
    """
    if value is None:
        return None, 0

    encoded = value.encode("utf-8")
    original_bytes = len(encoded)
    if original_bytes <= max_bytes:
        return value, original_bytes

    marker_bytes = marker.encode("utf-8")
    if len(marker_bytes) >= max_bytes:
        # 预算连标记都放不下：直接按字节裁，保证绝不越界。
        return _truncate_utf8(value, max_bytes), original_bytes

    prefix = _truncate_utf8(value, max_bytes - len(marker_bytes))
    return prefix + marker, original_bytes


def bound_audit_payload(value: Any, *, max_bytes: int = DEFAULT_AUDIT_PAYLOAD_BYTES) -> Any:
    """Return an independent payload whose serialized JSON is at most ``max_bytes``.

    The runtime keeps using the original value. This function is intentionally
    called only while creating or returning an audit representation.
    """
    if max_bytes < 128:
        raise ValueError("max_bytes must be at least 128")

    copied = _json_safe(value)
    original_bytes = len(_json_bytes(copied))
    if original_bytes <= max_bytes:
        return copied

    metadata = {
        "truncated": True,
        "original_bytes": original_bytes,
        "max_bytes": max_bytes,
    }
    source_preview = _json_bytes(copied).decode("utf-8", errors="replace")
    wrapper_overhead = len(_json_bytes({"__audit_trace": metadata, "preview": ""}))
    preview = _truncate_utf8(source_preview, max_bytes - wrapper_overhead)

    # The first estimate can lose a byte or two at a UTF-8 boundary or because
    # the escaped preview length changes. Reduce until the hard bound holds.
    result = {"__audit_trace": metadata, "preview": preview}
    while len(_json_bytes(result)) > max_bytes and preview:
        preview = _truncate_utf8(preview, max(0, len(preview.encode("utf-8")) - 16))
        result["preview"] = preview
    return result
