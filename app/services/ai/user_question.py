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
        # 委派子代理提问时带上来源，前端据此显示「来自：xxx」徽标；
        # 父自己提问时为空，前端不渲染徽标。
        "source_agent_name": str(payload.get("source_agent_name") or ""),
        "source_agent_display_name": str(
            payload.get("source_agent_display_name") or ""
        ),
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
    """Return whether text **starts with** the reserved user-question receipt prefix.

    必须是行首前缀，不能是子串包含：用户随口问「你刚才那条【用户回答】是什么意思？」
    时，若判成回执，preflight 会因解析失败 yield error 并把执行状态置 error，
    ContextStep 被跳过——这条发言连库都不落，刷新后凭空消失。
    """
    return str(text or "").lstrip().startswith(USER_QUESTION_MESSAGE_PREFIX)


#: 回执里「固定字段区」的起点。问题正文与选项名都在它**之前**被原样拼进同一段文本，
#: 而子代理能控制这两者（读过网页就是不可信输入）。只从锚点往后解析，正文里伪造的
#: `cancelled: true` / `question_id: ...` 就不会生效。
_RECEIPT_ANCHOR = "interaction_type: question"
#: 回执允许的字段。`cancelled` 按前端格式排在 `custom_input` 之后，必须允许。
_RECEIPT_ALLOWED_KEYS = frozenset(
    {"interaction_type", "question_id", "selected_option_ids", "custom_input", "cancelled"}
)
#: 这些键必须出现在 `custom_input` 之前；之后再出现即为注入（前端顺序是固定的）。
_RECEIPT_PRE_CUSTOM_KEYS = frozenset(
    {"interaction_type", "question_id", "selected_option_ids"}
)


#: 真正由前端取消提交时，``cancelled: true`` 后面只会是空白或固定结束语，
#: 不会跟随其它自定义正文字段或常规作答尾注（``请根据以上用户回答继续处理原问题。``）。
_RECEIPT_CANCEL_TAIL_TEXT = "用户取消了本次提问，请停止当前任务，不要再次询问同一个问题。"


def _iter_receipt_fields(raw: str):
    """只产出锚点之后、且顺序合法的回执字段 (key, value)。"""
    started = False
    seen_custom_input = False
    lines = raw.splitlines()
    for index, line in enumerate(lines):
        if not started:
            if line.strip().lower() != _RECEIPT_ANCHOR:
                continue
            started = True
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key = key.strip()
        if key not in _RECEIPT_ALLOWED_KEYS:
            continue
        if seen_custom_input and key in _RECEIPT_PRE_CUSTOM_KEYS:
            # 补充说明里伪造的 question_id / selected_option_ids：丢掉。
            continue
        if key == "cancelled" and seen_custom_input:
            # 用户可能在多行补充说明（custom_input）里输入 ``cancelled: true``，
            # 真正的系统取消字段必须位于补充说明结尾之后，其后续仅允许空白或取消结束语。
            trailing_lines = [item.strip() for item in lines[index + 1 :] if item.strip()]
            if any(item != _RECEIPT_CANCEL_TAIL_TEXT for item in trailing_lines):
                continue
        if key == "custom_input":
            if seen_custom_input:
                continue
            seen_custom_input = True
        yield key, value.strip()


def looks_like_receipt_body(text: str | None) -> bool:
    """行首前缀之外，还要有回执的固定结构，才算一条真正的回执。

    用户完全可能以「【用户回答】」开头提问（问这个格式本身），那种消息没有
    ``interaction_type`` 行。把它当回执会让 preflight 报「格式无效」并短路整轮，
    发言随之丢失。
    """
    raw = str(text or "").lower()
    return _RECEIPT_ANCHOR in raw


def parse_user_question_receipt(text: str | None) -> dict[str, Any] | None:
    """Parse the frontend receipt without trusting its option values."""
    raw = str(text or "")
    if not is_user_question_receipt_message(raw):
        return None
    values: dict[str, str] = {}
    for key, value in _iter_receipt_fields(raw):
        values.setdefault(key, value)
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
    # 来源字段与时间线快照同一口径（见 ``_user_question_log_payload``）：pending 记录
    # 目前只用于回执校验与数据集范围恢复，不参与卡片重建；但两条落库路径留同一份事实，
    # 以后要从这里补建卡片时不必再回头追一遍字段。
    for source_key in ("source_agent_name", "source_agent_display_name"):
        source_value = str(event.get(source_key) or "").strip()
        if source_value:
            payload[source_key] = source_value
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
