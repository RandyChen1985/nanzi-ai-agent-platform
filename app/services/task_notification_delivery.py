"""TaskCenter 结果通知：调度侧统一投递助手最终正文。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm import AsyncSessionLocal
from app.models.audit import AgentExecutionTrace
from app.services.notification_service import NotificationService
from app.services.portal_notification_service import PortalNotificationService
from app.services.task_notification_channels import CHANNEL_SPECS, normalize_notification_channels

logger = logging.getLogger(__name__)

MAX_NOTIFICATION_BODY_CHARS = 6000
MAX_SQL_RESULT_ROWS = 40
_TRACE_POLL_ATTEMPTS = 10
_TRACE_POLL_INTERVAL_SEC = 0.3

_NOTIFICATION_TOOL_NAMES = frozenset(spec[0] for spec in CHANNEL_SPECS.values())
_SQL_TOOL_NAMES = frozenset({"execute_sql_query"})

_PROVISIONAL_PATTERNS = (
    "让我再",
    "让我补充",
    "再补充",
    "接下来",
    "稍后",
    "稍等",
    "正在查询",
    "正在分析",
    "准备开始",
    "先看一下",
    "继续分析",
    "补充按",
    "待会",
    "马上为您",
)

_EXTERNAL_SENDERS = {
    "dingtalk": "send_dingtalk",
    "wechat_work": "send_wechat_work",
    "feishu": "send_feishu",
    "email": "send_email",
}


def _tool_output_text(tool_output: Any) -> str:
    if tool_output is None:
        return ""
    if isinstance(tool_output, str):
        return tool_output
    if isinstance(tool_output, dict):
        for key in ("content", "result", "output", "message"):
            if key in tool_output and tool_output[key] is not None:
                return str(tool_output[key])
        try:
            return json.dumps(tool_output, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(tool_output)
    return str(tool_output)


def notification_tool_succeeded(tool_name: Optional[str], tool_output: Any) -> bool:
    name = str(tool_name or "").strip()
    if name not in _NOTIFICATION_TOOL_NAMES:
        return False
    text = _tool_output_text(tool_output).strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered.startswith("error") or "failed to send" in lowered:
        return False
    return "successfully sent" in lowered or "已成功" in text


def build_task_notification_title(task_name: str) -> str:
    cleaned = str(task_name or "定时任务").strip() or "定时任务"
    return f"TaskCenter：{cleaned}"


def strip_thinking_from_notification_content(
    content: str,
    *,
    reasoning_content: str | None = None,
) -> str:
    """剥离 EmbedChat「模型思考推理」折叠面板对应内容，避免进入任务推送。"""
    from app.services.ai.runtime.agentscope.text_sanitize import strip_model_reasoning_from_answer

    return strip_model_reasoning_from_answer(
        content,
        reasoning_content=reasoning_content,
    )


def build_task_notification_body(
    content: str,
    *,
    fallback: bool,
    reasoning_content: str | None = None,
) -> str:
    body = strip_thinking_from_notification_content(
        content,
        reasoning_content=reasoning_content,
    )
    if not body:
        body = "（任务已执行，但无可展示的文本摘要。）"
    if len(body) > MAX_NOTIFICATION_BODY_CHARS:
        body = body[: MAX_NOTIFICATION_BODY_CHARS - 20] + "\n\n…（内容已截断）"
    if fallback:
        return (
            "📨 以下为 TaskCenter 统一投递的任务结果。\n\n"
            f"{body}"
        )
    return body


def is_provisional_assistant_text(text: str) -> bool:
    cleaned = str(text or "").strip()
    if not cleaned:
        return True
    return any(token in cleaned for token in _PROVISIONAL_PATTERNS)


def _try_parse_json(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    # 工具日志常见「--- 结果 ---\n{json}」
    marker = "--- 结果 ---"
    if marker in text:
        text = text.split(marker, 1)[1].strip()
    try:
        return json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
        if not match:
            return None
        try:
            return json.loads(match.group(1))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None


def extract_tabular_payload(tool_output: Any) -> Optional[Dict[str, Any]]:
    """从 execute_sql_query 工具输出中提取可表格化的结构化结果。"""
    out = tool_output
    if isinstance(out, str):
        parsed = _try_parse_json(out)
        if parsed is None:
            return None
        out = parsed
    if isinstance(out, dict) and "raw" in out:
        raw_val = out.get("raw")
        if isinstance(raw_val, str):
            parsed = _try_parse_json(raw_val)
            out = parsed if parsed is not None else raw_val
        else:
            out = raw_val
    if isinstance(out, dict) and "content" in out and not (
        out.get("columns") or out.get("items") or out.get("rows")
    ):
        nested = _try_parse_json(out.get("content"))
        if nested is not None:
            out = nested

    if isinstance(out, list) and out:
        if all(isinstance(row, dict) for row in out):
            columns = list(out[0].keys())
            rows = [[row.get(col) for col in columns] for row in out]
            return {"columns": columns, "rows": rows, "row_count": len(rows)}
        return None

    if not isinstance(out, dict):
        return None

    columns = out.get("columns")
    if not isinstance(columns, list) or not columns:
        return None

    row_keys = ("items", "rows", "data", "records")
    rows = None
    for key in row_keys:
        candidate = out.get(key)
        if isinstance(candidate, list):
            rows = candidate
            break
    if rows is None:
        return None
    if not rows:
        return {"columns": columns, "rows": [], "row_count": 0}

    normalized_rows: List[List[Any]] = []
    col_names = [
        (col.get("name") if isinstance(col, dict) else str(col))
        for col in columns
    ]
    for row in rows:
        if isinstance(row, dict):
            normalized_rows.append([row.get(name) for name in col_names])
        elif isinstance(row, (list, tuple)):
            normalized_rows.append(list(row))
        else:
            continue
    return {
        "columns": columns,
        "rows": normalized_rows,
        "row_count": int(out.get("row_count") or len(normalized_rows)),
    }


def tabular_payload_to_markdown(payload: Dict[str, Any], *, max_rows: int = MAX_SQL_RESULT_ROWS) -> str:
    columns = payload.get("columns") or []
    rows = payload.get("rows") or []
    if not columns:
        return ""
    headers = [
        str(col.get("name") if isinstance(col, dict) else col).replace("|", "/")
        for col in columns
    ]
    total = len(rows)
    display_rows = rows[: max(1, int(max_rows))]
    if not display_rows:
        return "（查询成功，但结果为空。）"
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in display_rows:
        cells = []
        values = list(row) if isinstance(row, (list, tuple)) else [row]
        for idx in range(len(headers)):
            val = values[idx] if idx < len(values) else ""
            cells.append(str(val).replace("\n", " ").replace("|", "/") if val is not None else "")
        lines.append("| " + " | ".join(cells) + " |")
    if total > len(display_rows):
        lines.append(f"\n（仅展示前 {len(display_rows)} 行，共 {total} 行）")
    return "\n".join(lines)


def compose_scheduler_notification_content(
    assistant_content: str,
    sql_payloads: List[Dict[str, Any]],
    *,
    reasoning_content: str | None = None,
) -> str:
    summary = strip_thinking_from_notification_content(
        assistant_content,
        reasoning_content=reasoning_content,
    )
    return summary.strip()


def assess_delivery_completeness(
    content: str,
    *,
    has_sql_data: bool,
    had_sql_tool: bool,
    assistant_content: str = "",
) -> Tuple[bool, str]:
    """判断是否适合投递。查数任务必须以可用数据或完整分析支撑。"""
    text = str(content or "").strip()
    assistant = str(assistant_content or "").strip()
    if not text:
        return False, "empty"

    provisional = is_provisional_assistant_text(assistant or text)

    if had_sql_tool and not has_sql_data and provisional:
        return False, "sql_without_usable_result"
    if provisional:
        return False, "provisional"
    if has_sql_data:
        return True, "ok_with_sql"
    if len(text) < 20:
        return False, "too_short"
    return True, "ok"


async def load_delivered_notification_tools(trace_id: Optional[str]) -> Set[str]:
    """从审计 trace 中读取已成功送达的通知工具名。"""
    if not trace_id:
        return set()

    delivered: Set[str] = set()
    for attempt in range(_TRACE_POLL_ATTEMPTS):
        async with AsyncSessionLocal() as session:
            stmt = (
                select(AgentExecutionTrace)
                .where(
                    AgentExecutionTrace.trace_id == trace_id,
                    AgentExecutionTrace.event_type == "tool_call",
                )
                .order_by(AgentExecutionTrace.step_number.asc())
            )
            rows = (await session.execute(stmt)).scalars().all()
            for row in rows:
                if notification_tool_succeeded(row.tool_name, row.tool_output):
                    delivered.add(str(row.tool_name))

        if delivered or attempt == _TRACE_POLL_ATTEMPTS - 1:
            break
        await asyncio.sleep(_TRACE_POLL_INTERVAL_SEC)

    return delivered


async def load_sql_tool_artifacts(trace_id: Optional[str]) -> Tuple[bool, List[Dict[str, Any]]]:
    """
    读取 trace 中的查数工具调用。
    返回 (是否出现过 SQL 工具, 成功解析出的表格 payload 列表)。
    """
    if not trace_id:
        return False, []

    had_sql = False
    payloads: List[Dict[str, Any]] = []
    for attempt in range(_TRACE_POLL_ATTEMPTS):
        had_sql = False
        payloads = []
        async with AsyncSessionLocal() as session:
            from app.models.audit import AgentExecutionHistory

            history_stmt = select(AgentExecutionHistory).where(
                AgentExecutionHistory.trace_id == trace_id
            )
            history = (await session.execute(history_stmt)).scalars().first()
            if history and history.conversation_id and history.user_id:
                try:
                    from app.services.ai.memory_service import memory_service

                    cached = await memory_service.get_current_data_result(
                        str(history.user_id), str(history.conversation_id)
                    )
                    if isinstance(cached, dict) and str(cached.get("trace_id") or "") == trace_id:
                        cached_payload = extract_tabular_payload(
                            cached.get("rows") or cached.get("structured")
                        )
                        if cached_payload is not None:
                            return True, [cached_payload]
                except Exception as exc:
                    logger.debug("Failed to load cached SQL result for trace %s: %s", trace_id, exc)

            stmt = (
                select(AgentExecutionTrace)
                .where(
                    AgentExecutionTrace.trace_id == trace_id,
                    AgentExecutionTrace.event_type == "tool_call",
                )
                .order_by(AgentExecutionTrace.step_number.asc())
            )
            rows = (await session.execute(stmt)).scalars().all()
            for row in rows:
                name = str(row.tool_name or "").strip()
                if name not in _SQL_TOOL_NAMES:
                    continue
                had_sql = True
                payload = extract_tabular_payload(row.tool_output)
                if payload is not None:
                    payloads.append(payload)

        if had_sql or attempt == _TRACE_POLL_ATTEMPTS - 1:
            break
        await asyncio.sleep(_TRACE_POLL_INTERVAL_SEC)

    return had_sql, payloads


def channels_missing_delivery(
    channels: List[str],
    delivered_tools: Set[str],
) -> List[str]:
    missing: List[str] = []
    for channel in normalize_notification_channels(channels):
        tool_name = CHANNEL_SPECS[channel][0]
        if tool_name not in delivered_tools:
            missing.append(channel)
    return missing


async def _deliver_channel(
    db: AsyncSession,
    *,
    user_id: int,
    channel: str,
    title: str,
    body: str,
    trace_id: Optional[str],
    task_name: str,
) -> Tuple[bool, str]:
    if channel == "portal":
        try:
            await PortalNotificationService.create(
                db,
                user_id=user_id,
                title=title,
                content=body,
                level="info",
                category="task_center",
                resource_type="scheduled_task",
                resource_id=(trace_id or "")[:64] or None,
                metadata={
                    "source": "task_notification_delivery_scheduler",
                    "trace_id": trace_id,
                    "task_name": task_name,
                },
            )
            return True, "portal:ok"
        except Exception as exc:
            logger.warning("TaskCenter portal delivery failed: %s", exc, exc_info=True)
            return False, f"portal:{exc}"

    sender_name = _EXTERNAL_SENDERS.get(channel)
    if sender_name is None:
        return False, f"{channel}:unsupported"
    sender = getattr(NotificationService, sender_name, None)
    if sender is None:
        return False, f"{channel}:unsupported"
    ok, err = await sender(db, user_id, title, body)
    if ok:
        return True, f"{channel}:ok"
    return False, f"{channel}:{err or 'send failed'}"


async def ensure_task_notification_deliveries(
    db: AsyncSession,
    *,
    user_id: int,
    task_name: str,
    channels: List[str],
    trace_id: Optional[str],
    content: str,
    reasoning_content: Optional[str] = None,
) -> Tuple[bool, List[str]]:
    """
    确保各勾选渠道至少送达一次。

    - 若智能体已成功调用对应通知工具则跳过（避免重复）。
    - 否则由调度侧统一投递助手总结；查数结果仅用于完整性校验。
    - 正文与 EmbedChat 一致：不含模型思考折叠面板（reasoning_content）。
    - 内容不完整（半截话且无可用数据）时拒绝投递并返回失败。
    """
    normalized = normalize_notification_channels(channels)
    if not normalized:
        return True, []

    delivered_tools = await load_delivered_notification_tools(trace_id)
    missing = channels_missing_delivery(normalized, delivered_tools)
    notes: List[str] = []

    if not missing:
        notes.append("all_channels_already_delivered_by_agent")
        return True, notes

    had_sql_tool, sql_payloads = await load_sql_tool_artifacts(trace_id)
    cleaned_assistant = strip_thinking_from_notification_content(
        content,
        reasoning_content=reasoning_content,
    )
    composed = compose_scheduler_notification_content(
        cleaned_assistant,
        sql_payloads,
        reasoning_content=reasoning_content,
    )
    complete, reason = assess_delivery_completeness(
        composed,
        has_sql_data=bool(sql_payloads),
        had_sql_tool=had_sql_tool,
        assistant_content=cleaned_assistant,
    )
    if not complete:
        notes.append(f"incomplete_content:{reason}")
        return False, notes

    title = build_task_notification_title(task_name)
    body = build_task_notification_body(
        composed,
        fallback=True,
        reasoning_content=reasoning_content,
    )

    all_ok = True
    for channel in missing:
        ok, note = await _deliver_channel(
            db,
            user_id=user_id,
            channel=channel,
            title=title,
            body=body,
            trace_id=trace_id,
            task_name=task_name,
        )
        notes.append(note)
        if not ok:
            all_ok = False

    if all_ok:
        notes.insert(0, f"scheduler_delivered:{','.join(missing)}")
    return all_ok, notes
