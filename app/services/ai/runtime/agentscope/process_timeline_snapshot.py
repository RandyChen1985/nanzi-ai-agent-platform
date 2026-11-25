"""Compact thinking-card snapshot from SSE chunks for history replay."""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Sequence

DETAILS_MAX_CHARS = 2000
TODO_STATUSES = {"pending", "in_progress", "completed"}
_AGENT_LIFECYCLE_TITLE = re.compile(r"^(?:\[.*?\]\s*)?[✨\s]*(?:Agent 回复开始|Agent 回复结束)\s*$")
_SYNTHETIC_LOG_ID_RE = re.compile(r"^log_\d+$")


def _normalize_text(text: str, trim_boundary: bool = False) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    while "\n\n\n" in normalized:
        normalized = normalized.replace("\n\n\n", "\n\n")
    if trim_boundary:
        normalized = normalized.lstrip(" \t").lstrip("\n").rstrip(" \t").rstrip("\n")
    return normalized


def _append_text(existing: str, piece: str) -> str:
    combined = _normalize_text(f"{existing or ''}{piece or ''}")
    return combined if existing else combined.lstrip(" \t").lstrip("\n")


def _source_id(chunk: Dict[str, Any]) -> Optional[str]:
    name = str(chunk.get("agent_name") or "").strip()
    return name or None


def _last_pending_text(
    items: List[Dict[str, Any]],
    text_kind: str,
    source_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    for item in reversed(items):
        if item.get("kind") != "text" or item.get("textKind") != text_kind or not item.get("pending"):
            continue
        if source_id:
            if item.get("sourceId") == source_id:
                return item
            continue
        return item
    return None


def _find_log(items: List[Dict[str, Any]], log_id: Any) -> Optional[Dict[str, Any]]:
    def find_in_logs(logs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        for log in logs:
            if log.get("id") == log_id:
                return log
            found = find_in_logs(log.get("children") or [])
            if found is not None:
                return found

        return None

    for item in items:
        if item.get("kind") == "log":
            found = find_in_logs([item])
            if found is not None:
                return found
        elif item.get("kind") == "text":
            found = find_in_logs(item.get("children") or [])
            if found is not None:
                return found
    return None


def _is_subagent_reasoning_log(log: Optional[Dict[str, Any]]) -> bool:
    """识别子代理思考日志（`subagent_reasoning:{run_id}:{seq}`），避免误认成委派容器。"""
    if not isinstance(log, dict):
        return False
    if log.get("reasoning") is True:
        return True
    if log.get("category") == "reasoning":
        return True
    return str(log.get("id") or "").startswith("subagent_reasoning:")


def _find_subagent_container(
    items: List[Dict[str, Any]],
    subagent: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    target_run_id = subagent.get("run_id")
    target_child_trace_id = subagent.get("child_trace_id")

    def match(log: Dict[str, Any]) -> bool:
        if _is_subagent_reasoning_log(log):
            return False
        if target_run_id and log.get("id") == f"subagent_{target_run_id}":
            return True
        log_sub = log.get("subagent") or {}
        if target_run_id and log_sub.get("run_id") == target_run_id and str(log.get("id", "")).startswith("subagent_"):
            return True
        if target_child_trace_id and log_sub.get("child_trace_id") == target_child_trace_id and str(log.get("id", "")).startswith("subagent_"):
            return True
        if "sub_agent_call" in str(log.get("title") or "") and (not log_sub or log_sub.get("run_id") == target_run_id):
            return True
        return False

    for item in items:
        if item.get("kind") == "log":
            if match(item):
                return item
        elif item.get("kind") == "text":
            for child in item.get("children") or []:
                if match(child):
                    return child
    return None


def _is_tool_log(data: Dict[str, Any]) -> bool:
    category = str(data.get("category") or "").lower()
    if category in {"permission", "external"}:
        return False
    if _AGENT_LIFECYCLE_TITLE.match(str(data.get("title") or "").strip()):
        return False
    if category in {"tool", "sql", "agent", "tool_resolution"}:
        return True
    if category:
        return False
    title = str(data.get("title") or "").lower()
    if any(token in title for token in ("权限", "permission", "确认", "外部执行")):
        return False
    if _is_subagent_reasoning_log(data):
        return False
    id_str = str(data.get("id") or "").lower()
    if id_str.startswith("subagent_"):
        return True
    return "工具" in title or "tool" in title or "子代理" in title


def _update_log(existing: Dict[str, Any], chunk: Dict[str, Any]) -> None:
    if chunk.get("title") is not None:
        existing["title"] = str(chunk.get("title") or existing.get("title") or "处理步骤")
    if "details" in chunk:
        existing["details"] = str(chunk.get("details") or "")
    # 入参（Bash 命令等）只在携带时写入：完成事件不重复带参数，别把已有命令洗成空。
    if chunk.get("tool_args"):
        existing["tool_args"] = str(chunk["tool_args"])
    # 意图摘要同理：起始事件带上后就一直保留（刷新/历史回放仍能看到这条命令在干什么）。
    if chunk.get("tool_summary"):
        existing["tool_summary"] = str(chunk["tool_summary"])
    # 模型 / 温度 / 框架侧结果状态同样只在携带时写入，完成事件不重复上报。
    if chunk.get("model"):
        existing["model"] = str(chunk["model"])
    if chunk.get("temperature") is not None:
        existing["temperature"] = chunk["temperature"]
    if chunk.get("tool_result_state"):
        existing["tool_result_state"] = str(chunk["tool_result_state"])
    # 「本会话已授权」是一次性事实，只在携带时写入，历史回放仍能看到这条免确认凭证。
    if chunk.get("auto_approved"):
        existing["auto_approved"] = True
    if chunk.get("reasoning"):
        existing["reasoning"] = True
    if chunk.get("status") is not None:
        existing["status"] = str(chunk.get("status") or existing.get("status") or "success")
    if chunk.get("category") is not None:
        existing["category"] = chunk.get("category")
    if chunk.get("execution_time_ms") is not None:
        existing["execution_time_ms"] = chunk.get("execution_time_ms")
    if "file_metadata" in chunk:
        existing["file_metadata"] = chunk.get("file_metadata")


def _next_id(items: List[Dict[str, Any]], prefix: str) -> str:
    return f"{prefix}_{len(items) + 1}"


def _iter_logs(items: List[Dict[str, Any]]):
    for item in items:
        if item.get("kind") == "log":
            yield item
        elif item.get("kind") == "text":
            for child in item.get("children") or []:
                if child.get("kind") == "log":
                    yield child


def _last_pending_log(items: List[Dict[str, Any]], category: str) -> Optional[Dict[str, Any]]:
    found = None
    for log in _iter_logs(items):
        if log.get("status") == "pending" and log.get("category") == category:
            found = log
    return found


def _close_pending_logs(items: List[Dict[str, Any]], category: str) -> None:
    for log in _iter_logs(items):
        if log.get("status") == "pending" and log.get("category") == category:
            log["status"] = "success"


def _model_log_count(items: List[Dict[str, Any]]) -> int:
    return sum(1 for log in _iter_logs(items) if log.get("category") == "model")


def _format_duration_ms(duration_ms: Any) -> str:
    try:
        value = float(duration_ms or 0)
    except (TypeError, ValueError):
        value = 0.0
    return str(int(round(value)))


def _normalize_todo_update(chunk: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    raw_todos = chunk.get("todos")
    if not isinstance(raw_todos, list):
        return None

    todos: List[Dict[str, str]] = []
    seen = set()
    for raw_todo in raw_todos:
        if not isinstance(raw_todo, dict):
            return None
        content = raw_todo.get("content")
        status = raw_todo.get("status")
        if not isinstance(content, str) or not content.strip() or status not in TODO_STATUSES:
            return None
        normalized_content = content.strip()
        if normalized_content in seen:
            return None
        seen.add(normalized_content)
        todos.append({"content": normalized_content, "status": status})

    counts = {status: sum(todo["status"] == status for todo in todos) for status in TODO_STATUSES}
    return {"todos": todos, "counts": counts}


def _apply_todo_update(state: List[Dict[str, Any]], chunk: Dict[str, Any]) -> None:
    normalized = _normalize_todo_update(chunk)
    if normalized is None:
        return

    todo_indexes = [index for index, item in enumerate(state) if item.get("kind") == "todo"]
    if not normalized["todos"]:
        for index in reversed(todo_indexes):
            state.pop(index)
        return

    todo_item = {
        "kind": "todo",
        "id": "todo_current",
        "title": "任务清单",
        "todos": normalized["todos"],
        "counts": normalized["counts"],
    }
    # 终态标记随同一次更新到达：写进快照，刷新后前端才能区分「早就结束了」与
    # 「真的还在跑」。执行中的普通更新不带该字段，重建对象时天然清除旧标记。
    if chunk.get("settled") is True:
        todo_item["settled"] = True
        todo_item["settledStatus"] = str(chunk.get("settledStatus") or "")
    if todo_indexes:
        state[todo_indexes[0]] = todo_item
        for index in reversed(todo_indexes[1:]):
            state.pop(index)
        return
    state.append(todo_item)


def complete_todo_items(state: Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """将当前轮最后一份 Todo 快照收尾，并返回实时更新事件。"""
    for item in reversed(state or []):
        if item.get("kind") != "todo":
            continue
        normalized = _normalize_todo_update(item)
        if normalized is None or not normalized["todos"]:
            return None
        if all(todo["status"] == "completed" for todo in normalized["todos"]):
            return None

        todos = [
            {"content": todo["content"], "status": "completed"}
            for todo in normalized["todos"]
        ]
        counts = {
            status: sum(todo["status"] == status for todo in todos)
            for status in TODO_STATUSES
        }
        item["todos"] = todos
        item["counts"] = counts
        return {"type": "todo_update", "todos": todos, "counts": counts}
    return None


def mark_todo_settled(
    state: Optional[List[Dict[str, Any]]],
    execution_status: str,
) -> Optional[Dict[str, Any]]:
    """就地把当前轮清单标记为已终结，返回快照；不改变任何 todo 的 status。"""
    for item in reversed(state or []):
        if item.get("kind") != "todo":
            continue
        normalized = _normalize_todo_update(item)
        if normalized is None or not normalized["todos"]:
            return None
        item["settled"] = True
        item["settledStatus"] = str(execution_status or "")
        return normalized
    return None


def settle_todo_items(
    state: Optional[List[Dict[str, Any]]],
    *,
    execution_status: str,
) -> Optional[Dict[str, Any]]:
    """终结态收尾：保留原有完成度，仅补「已结束」标记并返回实时更新事件。

    与 ``complete_todo_items`` 的区别是它**不**把未完成项改成 completed —— 失败、
    取消、异常中断的任务并没有被执行，伪造完成会误导用户。前端据此把卡片从
    「进行中」降级为「已结束 · N 项未完成」。
    """
    snapshot = mark_todo_settled(state, execution_status)
    if snapshot is None:
        return None
    return {
        "type": "todo_update",
        "todos": snapshot["todos"],
        "counts": snapshot["counts"],
        "settled": True,
        "settledStatus": str(execution_status or ""),
    }


def _user_question_log_payload(chunk: Dict[str, Any]) -> Dict[str, Any] | None:
    """Extract the replayable question card carried by a ``user_question`` chunk.

    Only well-formed cards are persisted (stable ``question_id``, a question text
    and at least two options); anything else returns ``None`` so the timeline
    entry degrades to a plain log line and stays compatible with snapshots
    written before the card itself was stored.
    """
    question_id = str(chunk.get("question_id") or "").strip()
    question = str(chunk.get("question") or "").strip()
    raw_options = chunk.get("options")
    if not question_id or not question or not isinstance(raw_options, list):
        return None

    options: List[Dict[str, Any]] = []
    for option in raw_options:
        if not isinstance(option, dict):
            continue
        option_id = str(option.get("id") or "").strip()
        if not option_id:
            continue
        label = str(option.get("label") or option_id).strip()
        item: Dict[str, Any] = {"id": option_id, "label": label}
        description = str(option.get("description") or "").strip()
        if description:
            item["description"] = description
        # 与实时卡片的 ``UserQuestionOption`` 保持同一口径：回放时卡片若仍在等待
        # 回答，置灰提示与点击动作必须一起还原，否则刷新后选项看着可选、点了没反应。
        if option.get("disabled") is True:
            item["disabled"] = True
        action = str(option.get("action") or "").strip()
        if action:
            item["action"] = action
        options.append(item)

    if len(options) < 2:
        return None

    payload: Dict[str, Any] = {
        "question_id": question_id,
        "question": question,
        "options": options,
        "is_multi_select": bool(chunk.get("is_multi_select", False)),
        "allow_custom_input": bool(chunk.get("allow_custom_input", True)),
        "context": str(chunk.get("context") or ""),
        "purpose": str(chunk.get("purpose") or ""),
    }
    tool_call_id = str(chunk.get("tool_call_id") or "").strip()
    if tool_call_id:
        payload["tool_call_id"] = tool_call_id
    # 委派子代理提问时带来源，回放后前端才能继续渲染「来自：xxx」徽标——
    # 实时 SSE 有这两个字段、历史快照没有，用户刷新一次就看到两种样子。
    # 父代理自己提问时它们是空串，不写，免得快照里堆一串空键。
    for source_key in ("source_agent_name", "source_agent_display_name"):
        source_value = str(chunk.get(source_key) or "").strip()
        if source_value:
            payload[source_key] = source_value
    return payload


def _business_confirmation_log_payload(
    chunk: Dict[str, Any],
) -> Dict[str, Any] | None:
    """Extract the replayable business-confirmation card carried by its chunk.

    与 ``user_question`` 同理：实时路径把卡片挂在消息对象上、并不落库，历史回放
    只能依赖 process_timeline。不把字段一起存下来，刷新后只剩一行「需要确认」，
    用户既看不到待确认的数据，也无法继续那一轮。

    只保留可重建卡片的最小字段集；``status`` **一律写 pending**。业务确认卡没有
    user_question 那样的「终态回写快照」机制（提问轮会有后端把 submitted/cancelled
    写回自己那份快照），所以时间线里这张卡永远停在 pending。前端回放时会强制把它
    标成 stale：它可以用来**看**当时要确认的数据，但不能点——业务确认回执会触发真实
    写入，放行一张陈旧卡片等于开了一个重复执行的入口。
    """
    confirmation_id = str(chunk.get("confirmation_id") or "").strip()
    title = str(chunk.get("title") or "").strip()
    raw_fields = chunk.get("fields")
    if not confirmation_id or not isinstance(raw_fields, list) or not raw_fields:
        return None

    fields: List[Dict[str, Any]] = []
    for field in raw_fields:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or "").strip()
        if not key:
            continue
        item: Dict[str, Any] = {
            "key": key,
            "label": str(field.get("label") or key).strip(),
            "value": field.get("value", ""),
        }
        if field.get("editable") is False:
            item["editable"] = False
        value_type = str(field.get("value_type") or "").strip()
        if value_type:
            item["value_type"] = value_type
        fields.append(item)

    if not fields:
        return None

    return {
        "confirmation_id": confirmation_id,
        "title": title or "请确认以下信息",
        "summary": str(chunk.get("summary") or ""),
        "fields": fields,
        "confirm_label": str(chunk.get("confirm_label") or "确定"),
        "cancel_label": str(chunk.get("cancel_label") or "取消"),
        "risk_note": str(chunk.get("risk_note") or ""),
        "status": "pending",
        "tool_call_id": str(chunk.get("tool_call_id") or ""),
    }


def apply_stream_chunk(state: List[Dict[str, Any]], chunk: Dict[str, Any]) -> None:
    """Mutate ``state`` with one user-visible thinking-card event."""
    if not isinstance(chunk, dict):
        return
    event_type = str(chunk.get("type") or "")
    source_id = _source_id(chunk)

    if event_type == "todo_update":
        _apply_todo_update(state, chunk)
        return

    if event_type == "process_narration":
        piece = str(chunk.get("content") or "")
        if not piece.strip():
            return
        current = _last_pending_text(state, "narration", source_id)
        if current:
            current["content"] = _append_text(str(current.get("content") or ""), piece)
            return
        state.append({
            "kind": "text",
            "id": _next_id(state, "narration"),
            "textKind": "narration",
            "content": _append_text("", piece),
            "pending": True,
            "children": [],
            "sourceId": source_id,
            "sourceLabel": source_id,
        })
        return

    if event_type == "process_narration_commit":
        piece = _normalize_text(str(chunk.get("content") or ""), trim_boundary=True)
        current = _last_pending_text(state, "narration", source_id)
        if current:
            if piece:
                current["content"] = piece
            current["pending"] = False
            current.setdefault("children", [])
            return
        if piece:
            state.append({
                "kind": "text",
                "id": _next_id(state, "narration"),
                "textKind": "narration",
                "content": piece,
                "pending": False,
                "children": [],
                "sourceId": source_id,
                "sourceLabel": source_id,
            })
        return

    if event_type == "process_narration_promote":
        current = _last_pending_text(state, "narration", source_id)
        if current and current.get("pending"):
            state.remove(current)
        return

    if event_type == "model_call":
        phase = str(chunk.get("phase") or "")
        reply_id = str(chunk.get("reply_id") or "model")
        if phase == "start":
            _close_pending_logs(state, "model")
            seq = _model_log_count(state)
            apply_stream_chunk(state, {
                "type": "log",
                "id": f"model_call_{reply_id}_{seq}",
                "title": f"模型调用: {chunk.get('model_name') or 'unknown'}",
                "details": "等待模型响应...",
                "status": "pending",
                "category": "model",
            })
            return
        if phase == "end":
            pending = _last_pending_log(state, "model")
            model_name = str(chunk.get("model_name") or "").strip()
            input_tokens = int(chunk.get("input_tokens") or 0)
            output_tokens = int(chunk.get("output_tokens") or 0)
            duration_ms = chunk.get("duration_ms") or 0
            apply_stream_chunk(state, {
                "type": "log",
                "id": pending.get("id") if pending else f"model_call_{reply_id}_orphan",
                "title": (pending or {}).get("title") or (
                    f"模型调用: {model_name}" if model_name else "模型调用完成"
                ),
                "details": (
                    f"输入 {input_tokens} / 输出 {output_tokens} tokens，"
                    f"耗时 {_format_duration_ms(duration_ms)} ms"
                ),
                "status": "success",
                "category": "model",
                "execution_time_ms": duration_ms,
            })
        return

    if event_type == "router_log":
        thought = str(chunk.get("thought") or "No reasoning provided.")
        agent_name = str(chunk.get("selected_agent") or "Unknown")
        confidence = chunk.get("confidence")
        conf_text = f"（置信度: {confidence}）" if confidence is not None else ""
        existing_selection = _find_log(state, "route:target_selection")
        selection_title = (
            existing_selection.get("title")
            if existing_selection and existing_selection.get("title")
            else "智能路由决策"
        )
        selection_duration = (
            existing_selection.get("execution_time_ms")
            if existing_selection and existing_selection.get("execution_time_ms") is not None
            else chunk.get("execution_time_ms")
        )
        apply_stream_chunk(state, {
            "type": "log",
            "id": "route:target_selection",
            "parent_id": chunk.get("parent_id") or "route:target_config",
            "title": selection_title,
            "details": f"思考过程:\n{thought}\n\n最终选择: {agent_name} {conf_text}".rstrip(),
            "status": chunk.get("status") or "success",
            "category": "router",
            "execution_time_ms": selection_duration,
        })
        return

    if event_type == "user_question":
        apply_stream_chunk(state, {
            "type": "log",
            "id": f"user_question_{chunk.get('question_id') or _next_id(state, 'question')}",
            "title": "需要用户回答",
            "details": str(chunk.get("question") or ""),
            "status": "pending",
            "category": "user_question",
            # SSE 实时路径把完整卡片挂在消息对象上（msg.userQuestion），并不落库；
            # 历史会话回放只能依赖 process_timeline，因此这里随日志一并持久化
            # 选项与交互配置，否则历史里只剩一行标题、卡片无法重建。
            "user_question": _user_question_log_payload(chunk),
        })
        return

    if event_type == "business_confirmation":
        apply_stream_chunk(state, {
            "type": "log",
            "id": f"business_confirmation_{chunk.get('confirmation_id') or _next_id(state, 'confirmation')}",
            "title": str(chunk.get("title") or "需要确认业务数据"),
            "details": str(chunk.get("summary") or ""),
            "status": "pending",
            "category": "business_confirmation",
            # 与 user_question 同构：SSE 实时路径把卡片挂在消息对象上、不落库，
            # 不随日志存下字段与按钮文案，历史回放就只剩一行标题。
            "business_confirmation": _business_confirmation_log_payload(chunk),
        })
        return

    if event_type == "permission_result":
        apply_stream_chunk(state, {
            "type": "log",
            "id": f"permission_{chunk.get('permission_request_id') or _next_id(state, 'permission')}",
            "title": (
                "已拒绝工具调用"
                if chunk.get("status") == "rejected"
                else (
                    "已允许工具调用（本会话）"
                    if (chunk.get("session_grant") or {}).get("granted")
                    else "已允许工具调用"
                )
            ),
            "details": f"确认请求: {chunk.get('permission_request_id') or ''}",
            "status": "success",
            "category": "permission",
        })
        return

    if event_type == "external_execution_result":
        apply_stream_chunk(state, {
            "type": "log",
            "id": f"external_result_{chunk.get('external_execution_request_id') or _next_id(state, 'external')}",
            "title": "外部执行失败" if chunk.get("status") == "error" else "外部执行结果已提交",
            "details": str(chunk.get("external_execution_request_id") or ""),
            "status": "error" if chunk.get("status") == "error" else "success",
            "category": "external",
        })
        return

    if event_type != "log":
        return

    log_id = chunk.get("id")
    if log_id is None:
        log_id = _next_id(state, "log")
    existing = _find_log(state, log_id)
    if existing:
        _update_log(existing, chunk)
        return

    title_str = str(chunk.get("title") or "")
    # Deduplicate sub_agent_call tool completion into existing subagent container if already present
    if "sub_agent_call" in title_str:
        for item in reversed(state):
            if (
                item.get("kind") == "log"
                and not _is_subagent_reasoning_log(item)
                and str(item.get("id") or "").startswith("subagent_")
            ):
                _update_log(item, chunk)
                return
            if item.get("kind") == "text":
                for c in item.get("children") or []:
                    if not _is_subagent_reasoning_log(c) and str(c.get("id") or "").startswith("subagent_"):
                        _update_log(c, chunk)
                        return

    log = {
        "kind": "log",
        "id": log_id,
        "title": str(chunk.get("title") or "处理步骤"),
        "details": str(chunk.get("details") or ""),
        "status": str(chunk.get("status") or "success"),
        "category": chunk.get("category"),
        "file_metadata": chunk.get("file_metadata"),
        "user_question": chunk.get("user_question"),
        "execution_time_ms": chunk.get("execution_time_ms"),
        "subagent": chunk.get("subagent"),
        "isExpanded": False,
        "children": [],
    }
    if chunk.get("tool_args"):
        log["tool_args"] = str(chunk["tool_args"])
    if chunk.get("tool_summary"):
        log["tool_summary"] = str(chunk["tool_summary"])
    if chunk.get("model"):
        log["model"] = str(chunk["model"])
    if chunk.get("temperature") is not None:
        log["temperature"] = chunk["temperature"]
    if chunk.get("tool_result_state"):
        log["tool_result_state"] = str(chunk["tool_result_state"])
    if chunk.get("auto_approved"):
        log["auto_approved"] = True
    # 子代理思考：保留标记，历史回放才能继续用 Markdown 渲染并归到子代理容器里。
    if chunk.get("reasoning"):
        log["reasoning"] = True

    parent_id = chunk.get("parent_id")
    if parent_id is not None and parent_id != log_id:
        parent = _find_log(state, parent_id)
        if parent is not None:
            parent.setdefault("children", []).append(log)
            parent.setdefault("childrenExpanded", True)
            return
        # 恢复流等局部快照里父节点可能位于审批前的前序时间线中：
        # 暂存孤儿节点的 parent_id，供落库合并阶段把子步骤挂回原父节点下方。
        log["parent_id"] = parent_id

    # If this is an inner step of a subagent
    is_subagent_container = (
        not _is_subagent_reasoning_log(chunk)
        and (str(log_id).startswith("subagent_") or "sub_agent_call" in title_str)
    )
    subagent_meta = chunk.get("subagent")
    if isinstance(subagent_meta, dict) and not is_subagent_container:
        container = _find_subagent_container(state, subagent_meta)
        if container is not None:
            container.setdefault("children", []).append(log)
            return

    parent = None
    if _is_tool_log(chunk) and state:
        # 与前端 attachableNarrationParent 保持同一口径：只认「最后一条顶层条目且是已提交旁白」，
        # 避免跨阶段（例如权限确认恢复或准备步骤之后）把新工具吸回旧旁白下方造成时间倒挂。
        last_item = state[-1]
        if (
            last_item.get("kind") == "text"
            and last_item.get("textKind") == "narration"
            and not last_item.get("pending")
        ):
            parent = last_item
    if parent is not None:
        parent.setdefault("children", []).append(log)
        return
    state.append(log)


def _truncate_details(text: Any) -> str:
    value = str(text or "")
    if len(value) <= DETAILS_MAX_CHARS:
        return value
    return value[: DETAILS_MAX_CHARS - 1] + "…"


def _finalize_log(item: Dict[str, Any]) -> Dict[str, Any]:
    copied = {
        "kind": "log",
        "id": item.get("id"),
        "title": str(item.get("title") or "处理步骤"),
        "details": _truncate_details(item.get("details")),
        "status": str(item.get("status") or "success"),
        "isExpanded": False,
    }
    if item.get("category") is not None:
        copied["category"] = item.get("category")
    if item.get("execution_time_ms") is not None:
        copied["execution_time_ms"] = item.get("execution_time_ms")
    if item.get("subagent") is not None:
        copied["subagent"] = item.get("subagent")
    if item.get("file_metadata") is not None:
        copied["file_metadata"] = item.get("file_metadata")
    # 提问卡快照必须随定稿一起落库，否则历史回放无从重建卡片。
    if item.get("user_question") is not None:
        copied["user_question"] = item.get("user_question")
    # 命令同样要落库，否则刷新/回放后只剩工具输出。
    if item.get("tool_args"):
        copied["tool_args"] = _truncate_details(item.get("tool_args"))
    # 意图摘要与命令同进退：定稿时丢掉就等于回放时又变成「工具完成 · Bash」。
    if item.get("tool_summary"):
        copied["tool_summary"] = _truncate_details(item.get("tool_summary"))
    if item.get("model") is not None:
        copied["model"] = item.get("model")
    if item.get("temperature") is not None:
        copied["temperature"] = item.get("temperature")
    if item.get("tool_result_state") is not None:
        copied["tool_result_state"] = item.get("tool_result_state")
    # 「本会话已授权」是审计留痕：定稿丢掉的话，刷新/回放就看不到这条免确认凭证。
    if item.get("auto_approved") is True:
        copied["auto_approved"] = True
    if item.get("reasoning") is True:
        copied["reasoning"] = True
    if item.get("parent_id") is not None:
        copied["parent_id"] = item.get("parent_id")
    if item.get("children"):
        copied["children"] = [_finalize_log(child) for child in item["children"]]
    return copied


def finalize_process_timeline(state: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    """Drop pending body candidates and compact tool payloads for persistence."""
    items: List[Dict[str, Any]] = []
    for item in list(state or []):
        if item.get("kind") == "text" and item.get("textKind") == "narration" and item.get("pending"):
            copied_pending = {
                "kind": "text",
                "id": item.get("id"),
                "textKind": "narration",
                "content": str(item.get("content") or ""),
                "pending": False,
                "interrupted": True,
                "children": [_finalize_log(child) for child in (item.get("children") or [])],
            }
            if item.get("sourceId"):
                copied_pending["sourceId"] = item.get("sourceId")
                copied_pending["sourceLabel"] = item.get("sourceLabel") or item.get("sourceId")
            items.append(copied_pending)
            continue
        if item.get("kind") == "log":
            copied = _finalize_log(item)
            # knowledge 是「检索完成、正在组织回答」的过程项，异常中断时若仍留 pending，
            # 历史回放会永远显示「进行中」。permission / external 是「等待用户操作、
            # 机器没在跑」的类别（对应前端 NON_LIVE_TIMER_CATEGORIES），必须保持待处理，
            # 故只按需扩展，不写成"收尾所有 pending"。
            if copied.get("status") == "pending" and copied.get("category") in ("model", "knowledge"):
                copied["status"] = "success"
            items.append(copied)
            continue
        if item.get("kind") == "todo":
            normalized = _normalize_todo_update(item)
            if normalized is None or not normalized["todos"]:
                continue
            persisted_todo = {
                "kind": "todo",
                "id": item.get("id") or "todo_current",
                "title": str(item.get("title") or "任务清单"),
                "todos": normalized["todos"],
                "counts": normalized["counts"],
            }
            # 终态标记必须落库：刷新后前端靠它区分「已结束」与「进行中」。
            # `settledStatus` 刻意用 camelCase：前端 `hydrateHistoryProcessTimeline` 按同名 key
            # 读取，改成 snake_case 会静默丢掉标记（log 项的 snake_case 字段与本字段无关）。
            if item.get("settled") is True:
                persisted_todo["settled"] = True
                persisted_todo["settledStatus"] = str(item.get("settledStatus") or "")
            items.append(persisted_todo)
            continue
        if item.get("kind") != "text":
            continue
        copied: Dict[str, Any] = {
            "kind": "text",
            "id": item.get("id"),
            "textKind": item.get("textKind") or "narration",
            "content": str(item.get("content") or ""),
            "pending": False,
            "children": [_finalize_log(child) for child in (item.get("children") or [])],
        }
        if item.get("sourceId"):
            copied["sourceId"] = item.get("sourceId")
            copied["sourceLabel"] = item.get("sourceLabel") or item.get("sourceId")
        items.append(copied)
    return items or None


def _merge_log_node(existing: Dict[str, Any], incoming: Dict[str, Any]) -> None:
    """将恢复阶段的同 id 日志字段合并到已有节点上，保留前序已记录的入参/摘要等只增字段。"""
    if incoming.get("title"):
        existing["title"] = str(incoming["title"])
    if incoming.get("status"):
        existing["status"] = str(incoming["status"])
    # 后阶段非空详情覆盖前序详情（例如工具输出覆盖占位/空串）；空串不洗掉已有详情。
    incoming_details = incoming.get("details")
    if isinstance(incoming_details, str) and incoming_details:
        existing["details"] = incoming_details
    if incoming.get("execution_time_ms") is not None:
        existing["execution_time_ms"] = incoming["execution_time_ms"]
    if incoming.get("category") is not None:
        existing["category"] = incoming["category"]
    if incoming.get("file_metadata") is not None:
        existing["file_metadata"] = copy.deepcopy(incoming["file_metadata"])
    if incoming.get("user_question") is not None:
        existing["user_question"] = copy.deepcopy(incoming["user_question"])
    if incoming.get("subagent") is not None:
        existing["subagent"] = copy.deepcopy(incoming["subagent"])
    if incoming.get("tool_args"):
        existing["tool_args"] = str(incoming["tool_args"])
    if incoming.get("tool_summary"):
        existing["tool_summary"] = str(incoming["tool_summary"])
    if incoming.get("model"):
        existing["model"] = str(incoming["model"])
    if incoming.get("temperature") is not None:
        existing["temperature"] = incoming["temperature"]
    if incoming.get("tool_result_state"):
        existing["tool_result_state"] = str(incoming["tool_result_state"])
    if incoming.get("auto_approved") is True:
        existing["auto_approved"] = True
    if incoming.get("reasoning") is True:
        existing["reasoning"] = True

    incoming_children = incoming.get("children")
    if isinstance(incoming_children, list) and incoming_children:
        target_children = existing.setdefault("children", [])
        for child in incoming_children:
            if not isinstance(child, dict):
                continue
            child_copy = copy.deepcopy(child)
            child_copy.pop("parent_id", None)
            child_id = child_copy.get("id")
            existing_child = _find_log(target_children, child_id) if child_id is not None else None
            if existing_child is not None:
                _merge_log_node(existing_child, child_copy)
            else:
                target_children.append(child_copy)


def _allocate_unique_text_id(items: List[Dict[str, Any]], text_kind: str) -> str:
    used_ids = {
        str(entry.get("id"))
        for entry in items
        if isinstance(entry, dict) and entry.get("id") is not None
    }
    prefix = text_kind or "narration"
    index = len(items) + 1
    while f"{prefix}_{index}" in used_ids:
        index += 1
    return f"{prefix}_{index}"


def _collect_all_ids(items: List[Dict[str, Any]]) -> set[str]:
    used: set[str] = set()

    def walk(nodes: List[Dict[str, Any]]) -> None:
        for entry in nodes:
            if not isinstance(entry, dict):
                continue
            raw_id = entry.get("id")
            if raw_id is not None:
                used.add(str(raw_id))
            children = entry.get("children")
            if isinstance(children, list) and children:
                walk(children)

    walk(items)
    return used


def _allocate_unique_log_id(items: List[Dict[str, Any]]) -> str:
    used_ids = _collect_all_ids(items)
    index = len(items) + 1
    while f"log_{index}" in used_ids:
        index += 1
    return f"log_{index}"


def _merge_log_into_timeline(merged: List[Dict[str, Any]], raw_log: Dict[str, Any]) -> None:
    log_copy = copy.deepcopy(raw_log)
    log_id = log_copy.get("id")
    if log_id is not None:
        existing = _find_log(merged, log_id)
        if existing is not None:
            # 仅对 _next_id(state, "log") 自动生成的兜底合成 id（log_1/log_2…）在标题不同时
            # 重分配唯一 id，避免两段快照各自从空列表起算而误合并两个无关步骤；
            # 真实业务 id（工具/权限/子代理等）在挂起前后标题本来就不同，必须按 id 原位更新。
            if (
                isinstance(log_id, str)
                and _SYNTHETIC_LOG_ID_RE.match(log_id)
                and str(existing.get("title") or "") != str(log_copy.get("title") or "")
            ):
                log_copy["id"] = _allocate_unique_log_id(merged)
                log_id = log_copy["id"]
            else:
                _merge_log_node(existing, log_copy)
                return

    parent_id = log_copy.get("parent_id")
    if parent_id is not None and parent_id != log_id:
        parent = _find_log(merged, parent_id)
        if parent is not None:
            log_copy.pop("parent_id", None)
            parent_children = parent.setdefault("children", [])
            existing_child = _find_log(parent_children, log_id) if log_id is not None else None
            if existing_child is not None:
                _merge_log_node(existing_child, log_copy)
            else:
                parent_children.append(log_copy)
            return

    merged.append(log_copy)


def merge_process_timelines(
    base: Optional[List[Dict[str, Any]]],
    incoming: Optional[List[Dict[str, Any]]],
) -> Optional[List[Dict[str, Any]]]:
    """将挂起前（`base`）与恢复阶段（`incoming`）的终稿时间线做结构合并。

    规则：
    - `log`：先全树递归按 `id` 查找，命中则原位更新状态/结果/耗时并递归合并 `children`
      （保留前序已记录的 `tool_args`/`tool_summary`）；未命中且带 `parent_id` 时尝试挂回
      前序父节点（例如恢复轮才拉起的 `workspace:sandbox:<bash_id>`）；否则追加末尾。
    - `todo`：后阶段清单视为最新快照，原位替换旧清单并保证最多保留一份。
    - `text`：同 `(id, textKind, content)` 视为同一段旁白（保证多次审批归并幂等）；
      不同内容的旁白若因各自从空列表起算而撞 id，则重分配唯一 id 追加末尾，绝不覆盖旧旁白。
    """
    merged: List[Dict[str, Any]] = [
        copy.deepcopy(item) for item in (base or []) if isinstance(item, dict)
    ]
    for raw_item in incoming or []:
        if not isinstance(raw_item, dict):
            continue
        kind = raw_item.get("kind")
        if kind == "todo":
            todo_copy = copy.deepcopy(raw_item)
            todo_indexes = [i for i, entry in enumerate(merged) if entry.get("kind") == "todo"]
            if todo_indexes:
                merged[todo_indexes[0]] = todo_copy
                for index in reversed(todo_indexes[1:]):
                    merged.pop(index)
            else:
                merged.append(todo_copy)
            continue

        if kind == "log":
            _merge_log_into_timeline(merged, raw_item)
            continue

        if kind == "text":
            text_copy = copy.deepcopy(raw_item)
            text_kind = str(text_copy.get("textKind") or "narration")
            content = str(text_copy.get("content") or "")
            raw_id = text_copy.get("id")
            children = [c for c in (text_copy.get("children") or []) if isinstance(c, dict)]

            existing_text = next(
                (
                    entry
                    for entry in merged
                    if entry.get("kind") == "text"
                    and entry.get("id") == raw_id
                    and str(entry.get("textKind") or "narration") == text_kind
                    and str(entry.get("content") or "") == content
                ),
                None,
            )
            if existing_text is not None:
                if not text_copy.get("interrupted"):
                    existing_text.pop("interrupted", None)
                existing_children = existing_text.setdefault("children", [])
                for child in children:
                    child_id = child.get("id")
                    hit = _find_log(merged, child_id) if child_id is not None else None
                    if hit is not None:
                        _merge_log_node(hit, child)
                    else:
                        child.pop("parent_id", None)
                        existing_children.append(child)
                continue

            # 若新旁白自带的子工具在前面已建过卡（极少见），原位更新并去重，防止整棵树出现双卡。
            remaining_children: List[Dict[str, Any]] = []
            for child in children:
                child_id = child.get("id")
                hit = _find_log(merged, child_id) if child_id is not None else None
                if hit is not None:
                    _merge_log_node(hit, child)
                else:
                    remaining_children.append(child)
            text_copy["children"] = remaining_children

            id_collision = raw_id is not None and any(
                entry.get("kind") == "text" and entry.get("id") == raw_id for entry in merged
            )
            if id_collision or raw_id is None:
                text_copy["id"] = _allocate_unique_text_id(merged, text_kind)
            merged.append(text_copy)
            continue

        merged.append(copy.deepcopy(raw_item))

    return merged or None


def merge_process_timeline_sequence(
    timelines: Sequence[Optional[List[Dict[str, Any]]]],
) -> Optional[List[Dict[str, Any]]]:
    """按先后顺序依次归并同一轮多次挂起/恢复的时间线快照。"""
    combined: Optional[List[Dict[str, Any]]] = None
    for timeline in timelines:
        if not timeline:
            continue
        combined = merge_process_timelines(combined, timeline)
    return combined


def merge_reasoning_contents(contents: Sequence[Optional[str]]) -> Optional[str]:
    """按顺序幂等拼接同一轮多次挂起/恢复的深度思考正文。"""
    acc = ""
    for raw in contents:
        piece = str(raw or "").strip()
        if not piece:
            continue
        if not acc:
            acc = piece
        elif piece == acc or acc.endswith(piece):
            continue
        elif piece.startswith(acc):
            acc = piece
        else:
            acc = f"{acc}\n\n{piece}"
    return acc or None


def merge_text_contents(contents: Sequence[Optional[str]]) -> str:
    """按顺序幂等拼接同一轮多次挂起/恢复的助手回答正文（保留原文换行格式）。"""
    acc = ""
    for raw in contents:
        piece = str(raw or "")
        piece_stripped = piece.strip()
        if not piece_stripped:
            continue
        if not acc:
            acc = piece
            continue
        acc_stripped = acc.strip()
        if piece_stripped == acc_stripped or acc_stripped.endswith(piece_stripped):
            continue
        if piece_stripped.startswith(acc_stripped):
            acc = piece
            continue
        separator = "" if (acc.endswith("\n") or piece.startswith("\n")) else "\n\n"
        acc = f"{acc}{separator}{piece}"
    return acc
