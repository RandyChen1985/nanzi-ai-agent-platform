"""Business data confirmation helpers (SSE payload + message contract)."""
from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any

BUSINESS_CONFIRMATION_TOOL_NAME = "request_user_confirmation"
BUSINESS_CONFIRMATION_MESSAGE_PREFIX = "【业务确认】"
BUSINESS_CONFIRMATION_CANCEL_MARKER = f"{BUSINESS_CONFIRMATION_MESSAGE_PREFIX}用户已取消"
BUSINESS_CONFIRMATION_CONFIRM_MARKER = f"{BUSINESS_CONFIRMATION_MESSAGE_PREFIX}用户已确定"

_cancel_gate_armed: ContextVar[bool] = ContextVar(
    "business_confirmation_cancel_gate",
    default=False,
)


def _starts_with_marker(text: str | None, marker: str) -> bool:
    """必须是行首前缀，不能是子串包含。

    回执是前端卡片拼出来的固定文案，一定以 ``【业务确认】`` 开头。改成行首判定是为了
    收窄误判面：原先用子串包含，任何**提及**这两个标记的发言（例如把一张旧回执粘进
    对话框、或让模型解释这段协议文案）都会被当成回执，preflight 随后直接走取消闸门，
    用户这条真实提问被静默吞掉。

    注意行首判定并不能覆盖「用户自己就用这两个字开头」的情况——那串文案本身就是协议
    前缀，只能靠它足够特殊来规避；这也是 ``is_user_question_receipt_message`` 的口径。
    """
    return str(text or "").lstrip().startswith(marker)


def is_business_confirmation_cancel_message(text: str | None) -> bool:
    return _starts_with_marker(text, BUSINESS_CONFIRMATION_CANCEL_MARKER)


def is_business_confirmation_confirm_message(text: str | None) -> bool:
    return _starts_with_marker(text, BUSINESS_CONFIRMATION_CONFIRM_MARKER)


def is_business_confirmation_receipt_message(text: str | None) -> bool:
    """True for confirm/cancel receipts produced by the business confirmation card."""
    return is_business_confirmation_cancel_message(text) or is_business_confirmation_confirm_message(
        text
    )


def arm_cancel_confirmation_gate(user_message: str | None) -> bool:
    """Arm per-turn gate when the latest user message is a cancel confirmation."""
    armed = is_business_confirmation_cancel_message(user_message)
    _cancel_gate_armed.set(armed)
    return armed


def is_cancel_confirmation_gate_armed() -> bool:
    return bool(_cancel_gate_armed.get())


def cancel_gate_block_payload() -> dict[str, Any]:
    return {
        "status": "error",
        "error": "business_confirmation_cancelled",
        "message": (
            "用户刚取消业务确认：禁止再次调用 request_user_confirmation，不要重新弹确认卡。"
            "请只用文字确认已取消，并询问用户是否修改后重试或放弃。"
        ),
    }


def _as_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def parse_confirmation_tool_output(tool_output: Any) -> dict[str, Any] | None:
    """Parse successful tool output into a confirmation payload dict."""
    payload = _as_dict(tool_output)
    if not payload:
        # Nested text/data_blocks shape from some runners
        if isinstance(tool_output, dict) and "text" in tool_output:
            payload = _as_dict(tool_output.get("text"))
    if not payload:
        return None
    if payload.get("status") != "awaiting_user":
        return None
    confirmation_id = str(payload.get("confirmation_id") or "").strip()
    ui = payload.get("ui")
    if not confirmation_id or not isinstance(ui, dict):
        return None
    fields = ui.get("fields")
    if not isinstance(fields, list) or not fields:
        return None
    return payload


def build_business_confirmation_sse(
    *,
    tool_name: str,
    tool_output: Any,
    tool_call_id: str = "",
) -> dict[str, Any] | None:
    """Build frontend SSE event for a business confirmation card."""
    if tool_name != BUSINESS_CONFIRMATION_TOOL_NAME:
        return None
    if is_cancel_confirmation_gate_armed():
        return None
    payload = parse_confirmation_tool_output(tool_output)
    if not payload:
        return None
    ui = payload["ui"]
    return {
        "type": "business_confirmation",
        "tool_call_id": tool_call_id,
        "confirmation_id": str(payload["confirmation_id"]),
        "title": str(ui.get("title") or "请确认以下信息"),
        "summary": str(ui.get("summary") or ""),
        "fields": ui.get("fields") or [],
        "confirm_label": str(ui.get("confirm_label") or "确定"),
        "cancel_label": str(ui.get("cancel_label") or "取消"),
        "risk_note": str(ui.get("risk_note") or ""),
        "status": "pending",
    }


def format_field_snapshot_lines(fields: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or "").strip()
        label = str(field.get("label") or key or "字段").strip()
        value = field.get("value")
        if value is None:
            value_text = ""
        else:
            value_text = str(value)
        if key:
            lines.append(f"- {label} ({key}): {value_text}")
        else:
            lines.append(f"- {label}: {value_text}")
    return lines


def build_user_confirmation_message(
    *,
    confirmed: bool,
    confirmation_id: str,
    fields: list[dict[str, Any]],
) -> str:
    """Build the plain user message sent after confirm/cancel click."""
    snapshot = "\n".join(format_field_snapshot_lines(fields))
    cid = confirmation_id.strip() or "unknown"
    if confirmed:
        body = (
            f"{BUSINESS_CONFIRMATION_MESSAGE_PREFIX}用户已确定\n"
            f"confirmation_id: {cid}\n"
            "请根据以下已确认字段继续执行（如需写入请调用相应工具）：\n"
            f"{snapshot}"
        )
    else:
        body = (
            f"{BUSINESS_CONFIRMATION_MESSAGE_PREFIX}用户已取消\n"
            f"confirmation_id: {cid}\n"
            "请立即终止本次录入/变更："
            "不要调用写入类工具；"
            "禁止再次调用 request_user_confirmation（不要重新弹确认卡）。"
            "只用文字确认已取消，并询问用户是否修改后重试或放弃。"
            "仅当用户随后明确提供新的/修改后的数据并要求继续时，才可再次请求确认。\n"
            f"当时字段快照：\n{snapshot}"
        )
    return body.strip()
