from __future__ import annotations

from datetime import datetime
import inspect
import logging
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession


logger = logging.getLogger(__name__)


SOURCE_NAMES = (
    "notifications",
    "tasks",
    "reports",
    "conversations",
    "agents",
    "scenarios",
)

SEVERITY_ORDER = {"critical": 3, "warning": 2, "info": 1}


def _item_value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _notification_payload(item: Any) -> Dict[str, Any]:
    raw = _item_value(item, "payload", {})
    return raw if isinstance(raw, dict) else {}


def normalize_notifications(notifications: Iterable[Any]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in notifications:
        if bool(_item_value(item, "is_read", False)) or _item_value(item, "read_at") is not None:
            continue
        notification_type = str(
            _item_value(
                item,
                "notification_type",
                _item_value(item, "type", _item_value(item, "category", "")),
            )
            or ""
        ).lower()
        payload = _notification_payload(item) or (
            _item_value(item, "meta_info", {})
            if isinstance(_item_value(item, "meta_info", {}), dict)
            else {}
        )
        level = str(_item_value(item, "level", payload.get("severity") or "info")).lower()
        resource_type = str(_item_value(item, "resource_type", "") or "")
        task_failed = notification_type in {"task_failed", "task_failure", "scheduled_task_failed"} or (
            resource_type == "scheduled_task" and level in {"error", "critical"}
        )
        report_run = resource_type == "saved_report_run"
        action = (
            "open_task_log"
            if task_failed
            else "open_report"
            if report_run and level in {"error", "critical"}
            else "open_digest"
            if report_run
            else str(payload.get("action") or "open_notification")
        )
        if action not in {"open_task_log", "open_digest", "open_report", "open_conversation"}:
            continue
        target = {
            key: payload[key]
            for key in ("task_id", "run_id", "report_id", "conversation_id", "agent_id")
            if payload.get(key) is not None
        }
        if report_run and _item_value(item, "resource_id") is not None:
            target["run_id"] = str(_item_value(item, "resource_id"))
        created_at = _item_value(item, "created_at")
        normalized.append(
            {
                "id": f"notification:{_item_value(item, 'id')}",
                "business_key": str(payload.get("business_key") or f"notification:{_item_value(item, 'id')}"),
                "type": notification_type or "notification",
                "title": str(_item_value(item, "title", "待处理事项")),
                "subtitle": str(payload.get("subtitle") or "站内通知"),
                "occurred_at": created_at.isoformat() if isinstance(created_at, datetime) else str(created_at or ""),
                "status": "failed" if task_failed else str(payload.get("status") or "unread"),
                "severity": "critical" if task_failed or level in {"error", "critical"} else ("warning" if level == "warning" else "info"),
                "action": action,
                "target": target,
            }
        )
    return normalized


def _dedupe(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen = set()
    for item in items:
        key = str(item.get("business_key") or item.get("id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(dict(item))
    return result


def _sort_limit(
    items: Iterable[Dict[str, Any]],
    *,
    limit: int,
    severity_first: bool = False,
) -> List[Dict[str, Any]]:
    def key(item: Dict[str, Any]):
        occurred_at = str(item.get("occurred_at") or "")
        if severity_first:
            return (SEVERITY_ORDER.get(str(item.get("severity") or "info"), 0), occurred_at)
        return (occurred_at,)

    return sorted(_dedupe(items), key=key, reverse=True)[:limit]


def _next_scheduled(task_items: Sequence[Dict[str, Any]]) -> Dict[str, Any] | None:
    scheduled = [item for item in task_items if item.get("next_run_at")]
    return min(scheduled, key=lambda item: str(item.get("next_run_at"))) if scheduled else None


def _complete_source_status(source_status: Mapping[str, str]) -> Dict[str, str]:
    return {name: str(source_status.get(name) or "empty") for name in SOURCE_NAMES}


def build_workbench_payload(
    *,
    now: datetime,
    notifications: Sequence[Any],
    task_items: Sequence[Dict[str, Any]],
    report_items: Sequence[Dict[str, Any]],
    conversation_items: Sequence[Dict[str, Any]],
    agent_items: Sequence[Dict[str, Any]],
    scenario_items: Sequence[Dict[str, Any]],
    source_status: Mapping[str, str],
) -> Dict[str, Any]:
    attention = normalize_notifications(notifications)
    attention.extend(item for item in task_items if item.get("needs_attention"))
    attention = _sort_limit(attention, limit=5, severity_first=True)
    latest_results = _sort_limit(report_items, limit=6)
    resume_items = _sort_limit(conversation_items, limit=4)
    has_history = bool(attention or latest_results or resume_items)
    mode = "active" if attention else ("quiet" if has_history else "new_user")

    return {
        "mode": mode,
        "attention": attention,
        "latest_results": latest_results,
        "resume_items": resume_items,
        "favorite_agents": list(agent_items[:6]),
        "recommended_scenarios": [
            item for item in scenario_items if item.get("available", True)
        ][:4],
        "next_scheduled_item": _next_scheduled(task_items),
        "source_status": _complete_source_status(source_status),
        "generated_at": now.isoformat(),
    }


def _status(items: Sequence[Any]) -> str:
    return "ok" if items else "empty"


async def _safe_load(name: str, loader) -> tuple[Any, str]:
    try:
        value = loader()
        if inspect.isawaitable(value):
            value = await value
        return value, _status(value if isinstance(value, Sequence) else [value] if value else [])
    except Exception:
        logger.warning("Workbench source %s failed", name, exc_info=True)
        return [], "error"


async def _load_notifications(db: AsyncSession, user_id: int) -> List[Any]:
    from app.services.portal_notification_service import PortalNotificationService

    return list(
        await PortalNotificationService.list_for_user(
            db, user_id, limit=20, offset=0, unread_only=True
        )
    )


async def _load_tasks(
    db: AsyncSession,
    user_id: int,
    user: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    from app.models.task import AgentScheduledTask

    menus = (user.get("permissions") or {}).get("menus") or []
    if user.get("role") != "admin" and "menu:task_center" not in menus:
        return []

    result = await db.execute(
        select(AgentScheduledTask)
        .where(AgentScheduledTask.user_id == user_id)
        .order_by(desc(AgentScheduledTask.updated_at))
        .limit(20)
    )
    items: List[Dict[str, Any]] = []
    for row in result.scalars().all():
        is_failed = int(row.status or 0) == 2
        items.append(
            {
                "id": f"task:{row.id}",
                "business_key": f"task:{row.id}:status:{row.status}",
                "type": "task_failure" if is_failed else "scheduled_task",
                "title": row.name,
                "subtitle": "定时任务执行异常" if is_failed else "定时任务",
                "occurred_at": (row.last_run_at or row.updated_at or row.created_at).isoformat(),
                "status": "failed" if is_failed else "active",
                "severity": "critical" if is_failed else "info",
                "action": "open_task_log" if is_failed else "open_task",
                "target": {"task_id": row.id, "run_id": row.last_run_id},
                "needs_attention": is_failed,
                "next_run_at": row.next_run_at.isoformat() if row.next_run_at else None,
            }
        )
    return items


async def _load_portal_activity(
    db: AsyncSession,
    user_id: int,
    role_ids: Sequence[int],
    now: datetime,
) -> Dict[str, List[Dict[str, Any]]]:
    from app.services.data_portal_home_service import DataPortalHomeService

    payload = await DataPortalHomeService.build(
        db, user_id=user_id, role_ids=role_ids, now=now
    )
    reports: List[Dict[str, Any]] = []
    conversations: List[Dict[str, Any]] = []
    for raw in payload.get("recent_analysis", []):
        item = dict(raw)
        item["business_key"] = (
            f"report-run:{item.get('run_id')}"
            if item.get("run_id") is not None
            else f"conversation:{item.get('conversation_id')}"
        )
        item["target"] = {
            key: item[key]
            for key in ("report_id", "run_id", "conversation_id")
            if item.get(key) is not None
        }
        item["id"] = f"{item.get('type')}:{item.get('id')}"
        if item.get("type") == "conversation":
            conversations.append(item)
        else:
            reports.append(item)
    return {"reports": reports, "conversations": conversations}


async def _load_agents(db: AsyncSession, user: Mapping[str, Any]) -> List[Dict[str, Any]]:
    from app.services.ai.agent_manager import AgentManagerService

    agents = await AgentManagerService.list_allowed_agents(db, user=user)
    return [
        {
            "id": str(agent.id),
            "name": str(agent.display_name or agent.name),
            "description": str(agent.description or ""),
            "execution_count": int(getattr(agent, "execution_count", 0) or 0),
            "action": "open_agent",
            "target": {"agent_id": str(agent.id)},
        }
        for agent in sorted(
            agents,
            key=lambda value: (
                int(getattr(value, "execution_count", 0) or 0),
                int(getattr(value, "sort_order", 0) or 0),
            ),
            reverse=True,
        )
    ]


def _load_scenarios(user: Mapping[str, Any]) -> List[Dict[str, Any]]:
    from app.services.scenario_template_service import ScenarioTemplateService

    # 与工作台 / 智能助手同权即可浏览推荐场景；安装仍由场景交付接口校验写权限
    menus = (user.get("permissions") or {}).get("menus") or []
    if user.get("role") != "admin" and "menu:ai_chat" not in menus and "menu:agent_management" not in menus:
        return []

    items: List[Dict[str, Any]] = []
    for summary in ScenarioTemplateService.list_templates():
        raw = summary.model_dump() if hasattr(summary, "model_dump") else dict(summary)
        items.append(
            {
                "id": str(raw["id"]),
                "name": str(raw["name"]),
                "description": str(raw.get("description") or ""),
                "category": str(raw.get("category") or ""),
                "recommended": bool(raw.get("recommended")),
                "available": True,
                "action": "open_scenario",
                "target": {"scenario_id": str(raw["id"])},
            }
        )
    return sorted(items, key=lambda item: bool(item["recommended"]), reverse=True)


class WorkbenchHomeService:
    @staticmethod
    async def build(
        db: AsyncSession,
        *,
        user_id: int,
        role_ids: Sequence[int] = (),
        user: Mapping[str, Any],
        now: datetime | None = None,
    ) -> Dict[str, Any]:
        current_time = now or datetime.now()
        notifications, notification_status = await _safe_load(
            "notifications", lambda: _load_notifications(db, user_id)
        )
        tasks, task_status = await _safe_load("tasks", lambda: _load_tasks(db, user_id, user))
        portal, portal_status = await _safe_load(
            "reports",
            lambda: _load_portal_activity(db, user_id, role_ids, current_time),
        )
        agents, agent_status = await _safe_load("agents", lambda: _load_agents(db, user))
        scenarios, scenario_status = await _safe_load("scenarios", lambda: _load_scenarios(user))
        portal = portal if isinstance(portal, dict) else {}
        reports = portal.get("reports", [])
        conversations = portal.get("conversations", [])

        return build_workbench_payload(
            now=current_time,
            notifications=notifications,
            task_items=tasks,
            report_items=reports,
            conversation_items=conversations,
            agent_items=agents,
            scenario_items=scenarios,
            source_status={
                "notifications": notification_status,
                "tasks": task_status,
                "reports": portal_status if reports else ("error" if portal_status == "error" else "empty"),
                "conversations": portal_status if conversations else ("error" if portal_status == "error" else "empty"),
                "agents": agent_status,
                "scenarios": scenario_status,
            },
        )
