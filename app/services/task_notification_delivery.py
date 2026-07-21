"""TaskCenter 结果通知：根据 trace 判断是否已由智能体发送，未发送则调度侧兜底代发。"""

from __future__ import annotations

import asyncio
import json
import logging
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
_TRACE_POLL_ATTEMPTS = 10
_TRACE_POLL_INTERVAL_SEC = 0.3

_NOTIFICATION_TOOL_NAMES = frozenset(spec[0] for spec in CHANNEL_SPECS.values())

_EXTERNAL_SENDERS = {
    "dingtalk": NotificationService.send_dingtalk,
    "wechat_work": NotificationService.send_wechat_work,
    "email": NotificationService.send_email,
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


def build_task_notification_body(content: str, *, fallback: bool) -> str:
    body = str(content or "").strip()
    if not body:
        body = "（任务已执行，但无可展示的文本摘要。）"
    if len(body) > MAX_NOTIFICATION_BODY_CHARS:
        body = body[: MAX_NOTIFICATION_BODY_CHARS - 20] + "\n\n…（内容已截断）"
    if fallback:
        return (
            "⚙️ 本消息由 TaskCenter 自动补发（智能体未调用对应通知工具）。\n\n"
            f"{body}"
        )
    return body


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
                    "source": "task_notification_delivery_fallback",
                    "trace_id": trace_id,
                    "task_name": task_name,
                },
            )
            return True, "portal:ok"
        except Exception as exc:
            logger.warning("TaskCenter portal fallback failed: %s", exc, exc_info=True)
            return False, f"portal:{exc}"

    sender = _EXTERNAL_SENDERS.get(channel)
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
) -> Tuple[bool, List[str]]:
    """
    确保各勾选渠道至少送达一次。
    若 trace 中已有对应工具成功记录则跳过；否则由调度侧代发。
    返回 (是否全部成功, 说明列表)。
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

    title = build_task_notification_title(task_name)
    body = build_task_notification_body(content, fallback=True)

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
        notes.insert(0, f"fallback_delivered:{','.join(missing)}")
    return all_ok, notes
