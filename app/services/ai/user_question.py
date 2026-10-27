"""Protocol helpers for AI-initiated user questions."""
from __future__ import annotations

import json
from typing import Any

USER_QUESTION_TOOL_NAME = "ask_user_question"
USER_QUESTION_MESSAGE_PREFIX = "【用户回答】"
CHATBI_DATASET_SELECTION_PURPOSE = "chatbi_dataset_selection"


def _as_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value.strip())
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def parse_user_question_tool_output(tool_output: Any) -> dict[str, Any] | None:
    """Parse a valid `ask_user_question` result into a UI payload."""
    payload = _as_dict(tool_output)
    if not payload and isinstance(tool_output, dict) and "text" in tool_output:
        payload = _as_dict(tool_output.get("text"))
    if not payload or payload.get("status") != "awaiting_user":
        return None
    if payload.get("interaction_type") != "question":
        return None
    question_id = str(payload.get("question_id") or "").strip()
    question = str(payload.get("question") or "").strip()
    options = payload.get("options")
    if not question_id or not question or not isinstance(options, list) or len(options) < 2:
        return None
    return payload


def build_user_question_sse(
    *,
    tool_name: str,
    tool_output: Any,
    tool_call_id: str = "",
) -> dict[str, Any] | None:
    """Build the SSE payload for an AI-initiated question."""
    if tool_name != USER_QUESTION_TOOL_NAME:
        return None
    payload = parse_user_question_tool_output(tool_output)
    if payload is None:
        return None
    return {
        "type": "user_question",
        "tool_call_id": tool_call_id,
        "question_id": str(payload["question_id"]),
        "question": str(payload["question"]),
        "options": payload["options"],
        "is_multi_select": bool(payload.get("is_multi_select", False)),
        "allow_custom_input": bool(payload.get("allow_custom_input", True)),
        "context": str(payload.get("context") or ""),
        "purpose": str(payload.get("purpose") or ""),
        "status": "pending",
    }


def metadata_dataset_ids_from_user_question_record(record: Any) -> list[str] | None:
    """只从服务端已校验的数据集选择卡回执恢复 metadata scope。"""
    if not isinstance(record, dict):
        return None
    if record.get("status") != "submitted":
        return None
    if str(record.get("purpose") or "").strip() != CHATBI_DATASET_SELECTION_PURPOSE:
        return None
    selected = record.get("selected_option_ids")
    options = record.get("options")
    if not isinstance(selected, list) or not isinstance(options, list) or not selected:
        return None
    allowed = {
        str(option.get("id") or "").strip()
        for option in options
        if isinstance(option, dict) and str(option.get("id") or "").strip()
    }
    normalized: list[str] = []
    for item in selected:
        value = str(item or "").strip()
        if value not in allowed or not value.isdigit() or int(value) <= 0:
            return None
        canonical = str(int(value))
        if canonical not in normalized:
            normalized.append(canonical)
    return normalized or None


def build_user_question_receipt(
    *,
    question_id: str,
    selected_option_ids: list[str],
    custom_input: str = "",
    cancelled: bool = False,
) -> str:
    """Build the structured user message sent after a question-card answer."""
    selected = json.dumps(
        [str(item).strip() for item in selected_option_ids if str(item).strip()],
        ensure_ascii=False,
    )
    lines = [
        f"{USER_QUESTION_MESSAGE_PREFIX}\n"
        "interaction_type: question\n"
        f"question_id: {str(question_id).strip()}\n"
        f"selected_option_ids: {selected}\n"
        f"custom_input: {str(custom_input or '').strip()}"
    ]
    if cancelled:
        lines.append("cancelled: true")
    return "\n".join(lines).strip()


def is_user_question_receipt_message(text: str | None) -> bool:
    """Return whether text uses the reserved user-question receipt prefix."""
    return USER_QUESTION_MESSAGE_PREFIX in str(text or "")


def parse_user_question_receipt(text: str | None) -> dict[str, Any] | None:
    """Parse the frontend receipt without trusting its option values."""
    raw = str(text or "")
    if not is_user_question_receipt_message(raw):
        return None
    values: dict[str, str] = {}
    for line in raw.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            values[key.strip()] = value.strip()
    question_id = values.get("question_id", "").strip()
    if not question_id or values.get("interaction_type") != "question":
        return None
    try:
        selected = json.loads(values.get("selected_option_ids", "[]"))
    except json.JSONDecodeError:
        return None
    if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
        return None
    return {
        "question_id": question_id,
        "selected_option_ids": [item.strip() for item in selected if item.strip()],
        "custom_input": values.get("custom_input", "").strip(),
        "cancelled": values.get("cancelled", "").lower() == "true",
    }


def _iter_timeline_items(timeline: Any):
    """按执行顺序深度遍历时间线（含子代理的 children）。"""
    if not isinstance(timeline, list):
        return
    for item in timeline:
        if not isinstance(item, dict):
            continue
        yield item
        children = item.get("children")
        if isinstance(children, list):
            yield from _iter_timeline_items(children)


def user_question_text_from_timeline(timeline: Any) -> str:
    """从 process_timeline 提取最近一条提问卡的问题原文。

    提问轮模型的可见正文通常为空，问题只存在于这张卡里；落库前用它补一条正文，
    模型下一轮才能看到「我问过什么」，而不是一条空的 assistant 消息。
    """
    found = ""
    for item in _iter_timeline_items(timeline):
        if str(item.get("category") or "") != "user_question":
            continue
        payload = item.get("user_question")
        text = ""
        if isinstance(payload, dict):
            text = str(payload.get("question") or "").strip()
        if not text:
            text = str(item.get("details") or "").strip()
        if text:
            found = text
    return found


def build_user_question_fallback_content(timeline: Any) -> str:
    """提问轮正文为空时的补全正文；没有提问卡时返回空串。"""
    question = user_question_text_from_timeline(timeline)
    if not question:
        return ""
    return f"我需要你补充确认：{question}"


async def persist_user_question_event(
    *,
    event: dict[str, Any],
    user_id: int | str,
    conversation_id: str,
) -> dict[str, Any]:
    """Persist a pending question before its interrupt event reaches the client."""
    from app.services.ai.user_question_store import UserQuestionStore

    if not str(conversation_id or "").strip():
        raise ValueError("主动提问必须绑定会话")
    store = await UserQuestionStore.from_runtime()
    return await store.create_pending(
        user_id=user_id,
        conversation_id=conversation_id,
        question_id=str(event["question_id"]),
        payload={
            "question": str(event.get("question") or ""),
            "options": event.get("options") or [],
            "is_multi_select": bool(event.get("is_multi_select", False)),
            "allow_custom_input": bool(event.get("allow_custom_input", True)),
            "context": str(event.get("context") or ""),
            "purpose": str(event.get("purpose") or ""),
            "tool_call_id": str(event.get("tool_call_id") or ""),
        },
    )
