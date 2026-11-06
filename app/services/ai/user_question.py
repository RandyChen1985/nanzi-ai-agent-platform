"""Protocol helpers for AI-initiated user questions."""
from __future__ import annotations

import json
from typing import Any

USER_QUESTION_TOOL_NAME = "ask_user_question"
USER_QUESTION_MESSAGE_PREFIX = "【用户回答】"
CHATBI_DATASET_SELECTION_PURPOSE = "chatbi_dataset_selection"
# 提问登记时顺手记下当轮生效的数据集范围，回答回执轮据此恢复（见 persist_user_question_event）。
USER_QUESTION_DATASET_IDS_KEY = "metadata_dataset_ids"


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


def _canonical_dataset_ids(raw: Any) -> list[str] | None:
    """把候选范围规整成正整数 ID（去重保序）；出现非法值整条不认。

    提问记录里的范围必须是「能被服务端当数据集 ID 用」的值：名称在运行时会被
    静默丢弃（见 ``normalize_bound_dataset_ids``），混进来只会让日志说「限定了」
    而实际没约束，所以这里直接 fail-closed。
    """
    if isinstance(raw, (str, int)):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        return None
    normalized: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if not text.isdigit() or int(text) <= 0:
            return None
        canonical = str(int(text))
        if canonical not in normalized:
            normalized.append(canonical)
    return normalized or None


def _dataset_ids_from_selection(record: dict[str, Any]) -> list[str] | None:
    """数据集选择卡：只认服务端下发的选项集合里的数字 ID。"""
    selected = record.get("selected_option_ids")
    options = record.get("options")
    if not isinstance(selected, list) or not isinstance(options, list) or not selected:
        return None
    allowed = {
        str(option.get("id") or "").strip()
        for option in options
        if isinstance(option, dict) and str(option.get("id") or "").strip()
    }
    if any(str(item or "").strip() not in allowed for item in selected):
        return None
    return _canonical_dataset_ids(selected)


def _dataset_ids_from_record_scope(record: dict[str, Any]) -> list[str] | None:
    """普通提问：用登记时写下的当轮范围快照。"""
    return _canonical_dataset_ids(record.get(USER_QUESTION_DATASET_IDS_KEY))


def metadata_dataset_ids_from_user_question_record(record: Any) -> list[str] | None:
    """从服务端提问记录恢复回答轮应继承的 metadata scope。

    两条来源，口径不同：
      * ``purpose=chatbi_dataset_selection`` —— 用户在卡片里现选的数据集，以选项为准；
      * 其余提问 —— 用 ``persist_user_question_event`` 在提问那一刻写下的范围快照。

    第二条是必需的：本轮手选的数据集只挂在「发送那一刻」的请求体 / 最后一条 user
    消息的 ``metadata_dataset`` 附件上，而【用户回答】回执是一条全新的用户消息，
    两者都没有。不恢复，答完追问这一轮就退回「用户可访问的全部数据集」，
    委派出去的 ChatBI 拿到的自然是空范围。记录由服务端写入、前端只能回传
    question_id 与选项，所以恢复值不可伪造。
    """
    if not isinstance(record, dict) or record.get("status") != "submitted":
        return None
    if str(record.get("purpose") or "").strip() == CHATBI_DATASET_SELECTION_PURPOSE:
        return _dataset_ids_from_selection(record)
    return _dataset_ids_from_record_scope(record)


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


def current_turn_dataset_scope_ids() -> list[str]:
    """提问那一刻生效的数据集范围（本轮手选优先，其次会话固定）。"""
    try:
        from app.services.ai.metadata_dataset_scope import resolve_mounted_dataset_scope_ids

        raw = resolve_mounted_dataset_scope_ids()
    except Exception:  # noqa: BLE001 - 取不到上下文只是不写快照，不能挡住提问
        return []
    return _canonical_dataset_ids(list(raw)) or []


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
    payload = {
        "question": str(event.get("question") or ""),
        "options": event.get("options") or [],
        "is_multi_select": bool(event.get("is_multi_select", False)),
        "allow_custom_input": bool(event.get("allow_custom_input", True)),
        "context": str(event.get("context") or ""),
        "purpose": str(event.get("purpose") or ""),
        "tool_call_id": str(event.get("tool_call_id") or ""),
    }
    # 本轮挂载的数据集只在「提问这一次请求」里，回答回执是全新的一条用户消息。
    # 这里把范围随问题一起登记，回执轮才能原样恢复（见 preflight_step）。
    scope_ids = current_turn_dataset_scope_ids()
    if scope_ids:
        payload[USER_QUESTION_DATASET_IDS_KEY] = scope_ids
    return await store.create_pending(
        user_id=user_id,
        conversation_id=conversation_id,
        question_id=str(event["question_id"]),
        payload=payload,
    )
