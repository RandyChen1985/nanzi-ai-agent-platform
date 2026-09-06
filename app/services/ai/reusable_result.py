"""统一的会话级可复用结果协议与复用决策。

该模块只负责确定性的结果规范化和请求判断，不直接访问 Redis，也不调用模型。
这样路由、执行器和测试可以共享同一套协议，同时保持 history 与结果缓存解耦。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Collection, Literal, Mapping

from app.services.ai.grounding.ledger import _is_non_empty_success_result
from app.services.ai.intent_service import (
    looks_like_context_action,
    looks_like_pure_result_followup,
    looks_like_strong_business_data_request,
)


RESULT_TYPES = frozenset({"data", "knowledge", "web", "file", "code", "generic"})
REUSABLE_RESULT_VERSION = "v1"
CLICKED_REPLY_MARKER = "【被点击的 AI 回复】"
USER_MESSAGE_CONTEXT_DIVIDER = "\n\n---\n\n"
MAX_RESULT_TEXT_CHARS = 12_000
MAX_STRUCTURED_JSON_CHARS = 8_000
MAX_TOOL_ARG_TEXT_CHARS = 2_000
_SUB_AGENT_TOOL_NAMES = frozenset({"sub_agent_call", "sub_agent_batch_call"})

_SENSITIVE_ARG_KEYS = frozenset(
    {
        "password",
        "token",
        "secret",
        "api_key",
        "apikey",
        "access_key",
        "private_key",
        "client_secret",
        "authorization",
        "cookie",
        "auth",
        "credential",
    }
)
_SENSITIVE_OUTPUT_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "private_key",
        "privatekey",
        "access_key",
        "accesskey",
        "api_key",
        "apikey",
        "client_secret",
        "clientsecret",
        "auth",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "set_cookie",
        "set-cookie",
    }
)
_BEARER_TOKEN_PATTERN = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+")
_BASIC_AUTH_PATTERN = re.compile(r"(?i)(\bBasic\s+)[A-Za-z0-9+/=]+")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(\b(?:password|passwd|access[\s_-]?token|refresh[\s_-]?token|id[\s_-]?token|"
    r"api[\s_-]?key|access[\s_-]?key|private[\s_-]?key|client[\s_-]?secret|"
    r"authorization|cookie|set-cookie|secret|credential|auth)\b\s*[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^,;\s}\]]+)"
)
_STANDALONE_SECRET_PATTERN = re.compile(
    r"(?i)\b(?:sk-[a-z0-9]{16,}|ak(?:ia|id)[a-z0-9]{12,}|"
    r"gh[pousr]_[a-z0-9_]{20,}|xox[baprs]-[a-z0-9-]{10,})\b"
)
_URL_CREDENTIAL_PATTERN = re.compile(r"(?i)(https?://)([^\s/:@]+):([^\s/@]+)@")
_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----",
    re.IGNORECASE,
)
_JWT_PATTERN = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
_FAILED_STATUSES = frozenset(
    {
        "failed",
        "error",
        "empty",
        "success_empty",
        "timeout",
        "cancelled",
        "canceled",
        "interrupted",
    }
)
_FRESH_DATA_PATTERN = re.compile(
    r"(重新查|再查|重查|再拉|重新拉|刷新(?:数据|结果)?|最新(?:数据|结果)?|实时(?:数据|结果)?|"
    r"重新查询|re-?query|refresh(?:\s+data)?|latest\s+data|real[- ]?time\s+data)",
    re.IGNORECASE,
)
_WEAK_CONTEXT_REF = re.compile(
    r"(这个|这些|这份|这张|这条|上面|上述|前面|刚才|刚刚|之前|上一|前述|同样|继续|"
    r"that|this|above|previous)",
    re.IGNORECASE,
)
_RESULT_REFERENCE = re.compile(
    r"(结果|数据|回复|内容|报告|快照|result|data|response|report|snapshot)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ReusableResultDecision:
    """服务端对本轮是否复用已有结果的确定性决策。"""

    mode: Literal["none", "reuse", "fallback"]
    result: dict[str, Any] | None = None
    reason: str = ""


def _is_sensitive_arg_key(key: str) -> bool:
    normalized = str(key or "").lower().replace("-", "_")
    return normalized in _SENSITIVE_ARG_KEYS or any(
        marker in normalized
        for marker in (
            "password",
            "token",
            "secret",
            "api_key",
            "access_key",
            "private_key",
            "authorization",
            "cookie",
            "credential",
            "auth",
        )
    )


def _truncate_text(value: Any, limit: int = MAX_RESULT_TEXT_CHARS) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 24] + "\n... [内容已截断]"


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    """将工具参数转换为有限、可 JSON 序列化且不含凭据的结构。"""
    if depth > 3:
        return _truncate_text(value, 500)
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_arg_key(key_text):
                safe[key_text] = "[redacted]"
            else:
                safe[key_text] = _safe_value(item, depth=depth + 1)
        return safe
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(item, depth=depth + 1) for item in list(value)[:50]]
    if isinstance(value, str):
        return _truncate_text(value, MAX_TOOL_ARG_TEXT_CHARS)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _truncate_text(value, 500)


def _is_sensitive_output_key(key: Any) -> bool:
    normalized = re.sub(r"[\s-]+", "_", str(key or "").strip().lower())
    compact = normalized.replace("_", "")
    if normalized in _SENSITIVE_OUTPUT_KEYS or compact in {
        item.replace("_", "") for item in _SENSITIVE_OUTPUT_KEYS
    }:
        return True
    return (
        normalized.endswith("_token")
        or normalized.endswith("_secret")
        or normalized.endswith("_password")
        or normalized.endswith("_credential")
        or normalized.startswith("x_api_key")
    )


def _sanitize_output_text(value: Any) -> str:
    """删除工具输出中常见凭据，但保留普通业务数据和 URL。"""
    text = str(value or "")
    text = _PRIVATE_KEY_PATTERN.sub("[redacted private key]", text)
    text = _BEARER_TOKEN_PATTERN.sub(r"\1[redacted]", text)
    text = _BASIC_AUTH_PATTERN.sub(r"\1[redacted]", text)
    text = _URL_CREDENTIAL_PATTERN.sub(r"\1[redacted]@", text)
    text = _JWT_PATTERN.sub("[redacted jwt]", text)
    text = _STANDALONE_SECRET_PATTERN.sub("[redacted secret]", text)
    return _SECRET_ASSIGNMENT_PATTERN.sub(r"\1[redacted]", text)


def _sanitize_output_value(value: Any, *, depth: int = 0) -> Any:
    """递归清理工具返回值，避免敏感字段绕过文本摘要进入 Redis。"""
    if depth > 6:
        return _sanitize_output_text(_truncate_text(value, 2_000))
    if isinstance(value, Mapping):
        return {
            str(key): "[redacted]"
            if _is_sensitive_output_key(key)
            else _sanitize_output_value(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [
            _sanitize_output_value(item, depth=depth + 1)
            for item in list(value)[:200]
        ]
    if isinstance(value, str):
        return _sanitize_output_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _sanitize_output_text(value)


def sanitize_reusable_result_payload(
    payload: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """清理已存储或兼容读取的结果，防止旧快照绕过统一构造入口。"""
    if not isinstance(payload, Mapping):
        return None
    sanitized = _sanitize_output_value(payload)
    return sanitized if isinstance(sanitized, dict) else None


def normalize_legacy_reusable_result(
    payload: Mapping[str, Any] | None,
    *,
    default_result_type: str | None = None,
) -> dict[str, Any] | None:
    """将旧版会话结果快照适配为统一结果对象。"""
    sanitized = sanitize_reusable_result_payload(payload)
    if not sanitized:
        return None

    structured = sanitized.get("structured")
    rows = sanitized.get("rows")
    if structured is None:
        structured = rows
    if rows is None:
        rows = structured

    display_value = structured if structured is not None else rows
    content = str(sanitized.get("content") or sanitized.get("text_excerpt") or "").strip()
    if not content and display_value is not None:
        content = json.dumps(display_value, ensure_ascii=False, default=str)
    if not content:
        return sanitized

    result_id = str(sanitized.get("result_id") or "").strip()
    if not result_id:
        fingerprint = hashlib.sha256(
            json.dumps(sanitized, ensure_ascii=False, sort_keys=True, default=str).encode(
                "utf-8",
                errors="replace",
            )
        ).hexdigest()[:24]
        result_id = f"legacy_{default_result_type or 'result'}_{fingerprint}"

    result_status = str(sanitized.get("result_status") or "").strip().lower()
    status = str(sanitized.get("status") or "").strip() or (
        "failed" if result_status in _FAILED_STATUSES else "completed"
    )
    normalized = {
        **sanitized,
        "version": sanitized.get("version") or REUSABLE_RESULT_VERSION,
        "result_id": result_id,
        "result_type": str(
            sanitized.get("result_type")
            or default_result_type
            or _classify_result_type(
                tool_name=str(sanitized.get("tool_name") or sanitized.get("origin_name") or ""),
                source_type=str(sanitized.get("source_type") or ""),
                tool_args=sanitized.get("tool_args") if isinstance(sanitized.get("tool_args"), Mapping) else None,
                user_question=str(
                    sanitized.get("user_question")
                    or sanitized.get("original_query")
                    or sanitized.get("question")
                    or ""
                ),
            )
        ).strip().lower(),
        "origin_type": sanitized.get("origin_type") or "tool",
        "origin_name": sanitized.get("origin_name") or sanitized.get("tool_name") or "legacy_result",
        "source_type": sanitized.get("source_type") or sanitized.get("data_source") or "system",
        "status": status,
        "content": _truncate_text(content),
        "text_excerpt": _truncate_text(str(sanitized.get("text_excerpt") or content)),
        "structured": structured,
        "rows": rows,
        "user_question": sanitized.get("user_question") or sanitized.get("original_query") or sanitized.get("question") or "",
        "updated_at": sanitized.get("updated_at") or sanitized.get("saved_at") or sanitized.get("created_at"),
    }
    return sanitize_reusable_result_payload(normalized)


def normalize_legacy_data_result(
    payload: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """将旧版 ``last_data_result``/ChatBI 快照适配为统一数据结果对象。"""
    return normalize_legacy_reusable_result(payload, default_result_type="data")


def _normalize_output(tool_output: Any) -> tuple[str, Any | None]:
    if isinstance(tool_output, Mapping) and "data_blocks" in tool_output:
        text = str(tool_output.get("text") or "")
        blocks = tool_output.get("data_blocks")
        return text, {"data_blocks": blocks} if blocks else None
    if isinstance(tool_output, (Mapping, list, tuple)):
        structured = tool_output if isinstance(tool_output, Mapping) else {"items": list(tool_output)}
        try:
            text = json.dumps(tool_output, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(tool_output)
        return text, structured

    text = str(tool_output or "")
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            return text, json.loads(stripped)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return text, None


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_expiry(value: Any) -> tuple[datetime | None, bool]:
    """Parse a business expiry; malformed non-empty values fail closed."""
    if value is None or value == "":
        return None, True
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None, True
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None, False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc), True


def _truncate_structured(value: Any) -> Any | None:
    if value is None:
        return None
    try:
        raw = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {"preview": _truncate_text(value, 2_000)}
    if len(raw) <= MAX_STRUCTURED_JSON_CHARS:
        try:
            return json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {"preview": _truncate_text(raw, 2_000)}
    return {"preview": raw[: MAX_STRUCTURED_JSON_CHARS - 24] + "... [JSON 已截断]"}


def _classify_result_type(
    *,
    tool_name: str,
    source_type: str,
    tool_args: Mapping[str, Any] | None,
    user_question: str,
) -> str:
    args_text = " ".join(str(value) for value in (tool_args or {}).values())
    haystack = " ".join(
        (str(tool_name), str(source_type), args_text, str(user_question))
    ).lower()
    if any(marker in haystack for marker in ("execute_sql", "chatbi", "data_query", "dataset")):
        return "data"
    if any(marker in haystack for marker in ("knowledge", "wiki", "sop", "manual", "handbook")):
        return "knowledge"
    if any(marker in haystack for marker in ("web", "http", "search", "browser", "url")):
        return "web"
    if any(marker in haystack for marker in ("file", "document", "excel", "word", "pdf")):
        return "file"
    if any(marker in haystack for marker in ("code", "python", "shell", "script", "program")):
        return "code"
    return "generic"


def _stable_result_id(
    *,
    tool_output: Any,
    tool_name: str,
    trace_id: str | None,
) -> str:
    if isinstance(tool_output, Mapping):
        candidate = tool_output.get("result_id")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:200]
    if trace_id:
        digest = hashlib.sha256(
            f"{trace_id}:{tool_name}".encode("utf-8", errors="replace")
        ).hexdigest()[:24]
        return f"rr_{digest}"
    return f"rr_{uuid.uuid4().hex}"


def build_reusable_result(
    *,
    tool_name: str,
    tool_output: Any,
    source_type: str,
    tool_args: Mapping[str, Any] | None,
    user_question: str,
    trace_id: str | None,
    origin_type: str | None = None,
) -> dict[str, Any]:
    """构造 Redis 中保存的统一结果对象。"""
    text, structured = _normalize_output(tool_output)
    text = _sanitize_output_text(text)
    structured = _sanitize_output_value(structured)
    now = datetime.now(timezone.utc).isoformat()
    content = _truncate_text(text)
    return {
        "version": REUSABLE_RESULT_VERSION,
        "result_id": _stable_result_id(
            tool_output=tool_output,
            tool_name=tool_name,
            trace_id=trace_id,
        ),
        "result_type": _classify_result_type(
            tool_name=tool_name,
            source_type=source_type,
            tool_args=tool_args,
            user_question=user_question,
        ),
        "origin_type": origin_type or ("sub_agent" if tool_name in _SUB_AGENT_TOOL_NAMES else "tool"),
        "origin_name": str(tool_name or "tool"),
        "source_type": str(source_type or "unknown"),
        "status": "completed",
        "content": content,
        "text_excerpt": content,
        "structured": _truncate_structured(structured),
        "tool_args": _safe_value(tool_args or {}),
        "user_question": _truncate_text(user_question, 500),
        "trace_id": str(trace_id or ""),
        "saved_at": now,
        "updated_at": now,
    }


def _is_valid_candidate(candidate: Any) -> bool:
    if not isinstance(candidate, dict):
        return False
    has_payload = bool(
        str(candidate.get("content") or candidate.get("text_excerpt") or "").strip()
        or candidate.get("structured")
        or candidate.get("rows")
    )
    if not has_payload:
        return False
    result_type = str(candidate.get("result_type") or "").strip().lower()
    if result_type and result_type not in RESULT_TYPES:
        return False
    status = str(candidate.get("status") or "completed").strip().lower()
    if status in _FAILED_STATUSES:
        return False
    result_status = str(candidate.get("result_status") or "").strip().lower()
    if result_status in _FAILED_STATUSES:
        return False
    if "reuse_allowed" in candidate and not _coerce_bool(
        candidate.get("reuse_allowed"), default=True
    ):
        return False
    if _coerce_bool(candidate.get("requires_fresh")):
        return False
    expires_at, expiry_valid = _parse_expiry(candidate.get("expires_at"))
    if not expiry_valid:
        return False
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        return False
    structured_values = [
        value
        for value in (candidate.get("structured"), candidate.get("rows"))
        if value is not None
    ]
    if structured_values and not any(
        _is_non_empty_success_result(value) for value in structured_values
    ):
        return False
    return True


def is_reusable_result_candidate(candidate: Any) -> bool:
    """Return whether a stored result has enough usable, non-empty payload."""
    return _is_valid_candidate(candidate)


def _is_result_type_allowed(
    candidate: Mapping[str, Any],
    allowed_result_types: Collection[str] | None,
) -> bool:
    if not allowed_result_types:
        return True
    allowed = {
        str(item).strip().lower()
        for item in allowed_result_types
        if str(item).strip()
    }
    if not allowed:
        return True
    result_type = str(candidate.get("result_type") or "").strip().lower()
    if result_type:
        return result_type in allowed
    source_type = str(candidate.get("source_type") or "").strip().lower()
    return source_type in allowed


def _client_excerpt(value: Any, limit: int = 500) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 16] + "... [已截断]"


def _client_structured_preview(value: Any) -> dict[str, Any] | None:
    """提取前端理解结果所需的有限结构化信息，不暴露完整 payload。"""
    if not isinstance(value, Mapping):
        return None
    preview: dict[str, Any] = {}
    for key in ("row_count", "total_row_count", "item_count", "columns"):
        item = value.get(key)
        if key == "columns" and isinstance(item, list):
            preview[key] = [str(column)[:80] for column in item[:30]]
        elif isinstance(item, (str, int, float, bool)):
            preview[key] = item
    return preview or None


def build_reusable_result_client_summary(
    payload: Mapping[str, Any] | None,
    *,
    is_current: bool = False,
) -> dict[str, Any] | None:
    """构造只供前端展示的结果摘要，禁止把内部调用参数带出服务端。"""
    if not is_reusable_result_candidate(payload):
        return None
    data = sanitize_reusable_result_payload(payload) or {}
    result_id = str(data.get("result_id") or "").strip()
    if not result_id:
        return None
    return {
        "result_id": result_id,
        "result_type": str(data.get("result_type") or "generic"),
        "origin_type": str(data.get("origin_type") or "tool"),
        "origin_name": str(data.get("origin_name") or data.get("tool_name") or "未知来源"),
        "source_type": str(data.get("source_type") or "unknown"),
        "status": str(data.get("status") or "success"),
        "text_excerpt": _client_excerpt(data.get("text_excerpt") or data.get("content")),
        "structured_preview": _client_structured_preview(data.get("structured")),
        "trace_id": str(data.get("trace_id") or "").strip() or None,
        "created_at": data.get("created_at") or data.get("saved_at"),
        "expires_at": data.get("expires_at"),
        "is_current": bool(is_current),
    }


def build_reusable_result_status_event(
    *,
    status: Literal["saved", "reused", "fallback"],
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """构造前端状态事件，只保留可展示的安全元数据。"""
    summary = build_reusable_result_client_summary(
        payload,
        is_current=status == "saved",
    ) or {}
    event: dict[str, Any] = {
        "type": "reusable_result_status",
        "status": status,
    }
    for key in ("result_id", "result_type", "origin_name", "created_at", "expires_at"):
        if summary.get(key) is not None:
            event[key] = summary[key]
    return event


def _has_reuse_intent(question: str) -> bool:
    if CLICKED_REPLY_MARKER.lower() in question.lower():
        return True
    if looks_like_pure_result_followup(question) or looks_like_context_action(question):
        return True
    if _WEAK_CONTEXT_REF.search(question) and _RESULT_REFERENCE.search(question):
        return not looks_like_strong_business_data_request(question)
    return False


def extract_reusable_action_query(user_question: str) -> str:
    """提取快捷按钮的动作文本，避免把客户端附带的整段回复当成新查询。"""
    question = str(user_question or "")
    marker_index = question.lower().find(CLICKED_REPLY_MARKER.lower())
    if marker_index < 0:
        return question.strip()
    action = question[:marker_index]
    if USER_MESSAGE_CONTEXT_DIVIDER in action:
        action = action.rsplit(USER_MESSAGE_CONTEXT_DIVIDER, 1)[0]
    # 进入 AgentScope 前，消息可能被包装成系统注入附件；该包装同样属于被点击回复，
    # 不能带入回退查询或新鲜度判断。
    normalized_attachment_marker = "<system_injected_attachments>"
    if normalized_attachment_marker in action:
        action = action.split(normalized_attachment_marker, 1)[0]
    return action.strip()


def prepare_reusable_route_input(
    messages: list[Mapping[str, Any]] | None,
    user_question: str,
) -> tuple[list[dict[str, Any]], str]:
    """为入口路由去掉快捷按钮附带的旧回答，同时不改写原始消息。"""
    raw_question = str(user_question or "")
    if CLICKED_REPLY_MARKER.lower() not in raw_question.lower():
        return list(messages or []), raw_question

    route_question = extract_reusable_action_query(raw_question)
    route_messages = [dict(message) for message in (messages or [])]
    for index in range(len(route_messages) - 1, -1, -1):
        message = route_messages[index]
        if (
            str(message.get("role") or "").lower() == "user"
            and CLICKED_REPLY_MARKER.lower() in str(message.get("content") or "").lower()
        ):
            route_messages[index] = {**message, "content": route_question}
            break
    return route_messages, route_question


def resolve_reusable_result(
    user_question: str,
    *,
    current: Mapping[str, Any] | None,
    stack: list[Mapping[str, Any]] | None,
    preferred_result_id: str | None = None,
    allowed_result_types: Collection[str] | None = None,
) -> ReusableResultDecision:
    """决定本轮是否优先使用已有结果；缺失/不足时交给原有路径回退。"""
    question = str(user_question or "").strip()
    intent_question = extract_reusable_action_query(question)
    if _FRESH_DATA_PATTERN.search(intent_question):
        return ReusableResultDecision(mode="fallback", reason="freshness_requested")

    preferred_id = str(preferred_result_id or "").strip()
    if preferred_id:
        selected_candidates: list[Mapping[str, Any]] = []
        if isinstance(current, Mapping):
            selected_candidates.append(current)
        selected_candidates.extend(
            item for item in reversed(stack or []) if isinstance(item, Mapping)
        )
        for candidate in selected_candidates:
            if str(candidate.get("result_id") or "").strip() != preferred_id:
                continue
            if not _is_valid_candidate(candidate):
                return ReusableResultDecision(
                    mode="fallback",
                    reason="selected_result_invalid",
                )
            if not _is_result_type_allowed(candidate, allowed_result_types):
                return ReusableResultDecision(
                    mode="fallback",
                    reason="selected_result_incompatible_type",
                )
            return ReusableResultDecision(
                mode="reuse",
                result=dict(candidate),
                reason="selected_result",
            )
        return ReusableResultDecision(mode="fallback", reason="selected_result_missing")

    if not _has_reuse_intent(question):
        return ReusableResultDecision(mode="none")

    candidates: list[Mapping[str, Any]] = []
    if isinstance(current, Mapping):
        candidates.append(current)
    candidates.extend(item for item in reversed(stack or []) if isinstance(item, Mapping))
    saw_incompatible_type = False
    for candidate in candidates:
        if not _is_valid_candidate(candidate):
            continue
        if not _is_result_type_allowed(candidate, allowed_result_types):
            saw_incompatible_type = True
            continue
        return ReusableResultDecision(
            mode="reuse",
            result=dict(candidate),
            reason="current_result" if candidate is current else "stack_result",
        )

    if saw_incompatible_type:
        return ReusableResultDecision(mode="fallback", reason="incompatible_result_type")
    reason = "insufficient_result" if current else "missing_result"
    return ReusableResultDecision(mode="fallback", reason=reason)


def quick_result_reuse_decision() -> ReusableResultDecision:
    """quick-result（快捷点按触发的新查询）上下文的硬性复用决策。

    契约：只要本轮是 quick_result_followup，就必须跳过对历史 reusable-result 的复用，
    直接当作全新实时查询处理。
    """
    return ReusableResultDecision(mode="none", reason="quick_context_requires_fresh_data")


def should_attempt_reusable_reuse(
    *,
    quick_result_followup: bool,
    allowed_reusable_result_types: Collection[str] | None = None,
) -> bool:
    """quick-result 新查询上下文必须禁用 reusable-result 复用尝试。

    仅当「不是 quick_result_followup」且「确实存在候选结果类型白名单」时，才允许尝试复用。
    这是 reusable-result 类型门禁的纯逻辑部分；是否真正执行复用解析由调用方结合运行时
    能力（如 agent_service._resolve_reusable_result_decision）共同决定。
    """
    return allowed_reusable_result_types is not None and not quick_result_followup


__all__ = [
    "RESULT_TYPES",
    "REUSABLE_RESULT_VERSION",
    "ReusableResultDecision",
    "CLICKED_REPLY_MARKER",
    "build_reusable_result",
    "extract_reusable_action_query",
    "is_reusable_result_candidate",
    "normalize_legacy_data_result",
    "normalize_legacy_reusable_result",
    "prepare_reusable_route_input",
    "quick_result_reuse_decision",
    "resolve_reusable_result",
    "sanitize_reusable_result_payload",
    "should_attempt_reusable_reuse",
]
