"""extra_data 的解析与敏感键脱敏。

`extra_data` 是第三方用户同步写入的 JSON 文本（见 schemas/user_sync.py 的
extra_data_mappings），里面可能带凭据。**同一份内容会经由两条路进入大模型上下文**：

1. `get_myinfo` 工具返回的 payload
2. `<USER_PROFILE>` 画像块（提示词）

两条路必须用同一套脱敏规则，否则后加的那条就是绕过口 —— 之前画像块就是原样注入的。
"""
from __future__ import annotations

import json
from typing import Any

# 键名包含这些片段的，值一律打码
REDACTED_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "private_key",
)

REDACTED_PLACEHOLDER = "[REDACTED]"


def is_sensitive_key(key: Any) -> bool:
    normalized = str(key).casefold().replace("-", "_")
    return any(part in normalized for part in REDACTED_KEY_PARTS)


def sanitize_extra_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                REDACTED_PLACEHOLDER if is_sensitive_key(str(key)) else sanitize_extra_data(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_extra_data(item) for item in value]
    return value


def parse_extra_data(raw_value: Any) -> Any:
    if raw_value is None or raw_value == "":
        return {}
    if isinstance(raw_value, str):
        try:
            return sanitize_extra_data(json.loads(raw_value))
        except json.JSONDecodeError:
            return {"_raw": raw_value, "_parse_error": True}
    return sanitize_extra_data(raw_value)


def redacted_extra_data_text(raw_value: Any, max_chars: int | None = None) -> str:
    """提示词用：解析 → 脱敏 → 压成单行。

    非 JSON 时保留原文（与 parse_extra_data 的 _raw 兜底一致）：任意文本里无法可靠
    识别敏感键，与其猜，不如原样保留并交由单行化/截断控制体积。
    空内容返回空串，调用方据此省略整个字段行。
    """
    parsed = parse_extra_data(raw_value)
    if isinstance(parsed, dict) and parsed.get("_parse_error"):
        return str(parsed.get("_raw") or "")
    if parsed == {}:
        return ""
    text = json.dumps(parsed, ensure_ascii=False)
    if max_chars is not None and len(text) > max_chars:
        return text[:max_chars]
    return text
