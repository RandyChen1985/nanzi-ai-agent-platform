from __future__ import annotations

from datetime import datetime, time
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.saved_report import (
    PortalSavedReport,
    PortalSavedReportDigestDelivery,
    PortalSavedReportRun,
    PortalSavedReportShare,
    PortalSavedReportSubscription,
    PortalSavedReportUserPref,
)
from app.models.agent import AIAgent
from app.models.audit import AgentExecutionHistory


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _day_start(now: datetime) -> datetime:
    return datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)


def _report_value(report: Any, name: str, default: Any = None) -> Any:
    return getattr(report, name, default)


def build_home_payload(
    *,
    user_id: int,
    reports: Sequence[Any],
    runs: Sequence[Any],
    subscriptions: Sequence[Any],
    deliveries: Sequence[Any],
    now: datetime,
    conversations: Sequence[Any] = (),
) -> Dict[str, Any]:
    start = _day_start(now)
    report_by_id = {str(report.id): report for report in reports}
    subscription_by_report = {str(item.report_id): item for item in subscriptions}
    run_by_id = {int(item.id): item for item in runs}

    today_runs = [item for item in runs if item.started_at and item.started_at >= start]
    failed_today = [item for item in today_runs if str(item.status).lower() in {"failed", "error"}]
    successful_scheduled_report_ids = {
        str(item.report_id)
        for item in today_runs
        if item.trigger_type == "scheduled" and str(item.status).lower() == "success"
    }
    active_subscriptions = [item for item in subscriptions if item.status == "active"]
    successful_deliveries = [
        item
        for item in deliveries
        if item.created_at and item.created_at >= start and str(item.status).lower() in {"sent", "success"}
    ]
    delivery_by_run: Dict[int, Any] = {}
    for item in successful_deliveries:
        run_id = int(item.run_id)
        current = delivery_by_run.get(run_id)
        if current is None or item.created_at > current.created_at:
            delivery_by_run[run_id] = item
    today_deliveries = list(delivery_by_run.values())

    recent: List[Dict[str, Any]] = []
    for delivery in today_deliveries:
        run = run_by_id.get(int(delivery.run_id))
        if run is None:
            continue
        report = report_by_id.get(str(run.report_id))
        if report is None:
            continue
        recent.append(
            {
                "type": "digest",
                "id": int(delivery.id),
                "report_id": str(report.id),
                "run_id": int(run.id),
                "title": delivery.title or report.title,
                "subtitle": "黄金报表 · AI 智能简报",
                "status": "success",
                "occurred_at": _iso(delivery.created_at),
                "action": "open_digest",
            }
        )
    for run in runs:
        if int(run.id) in delivery_by_run:
            continue
        report = report_by_id.get(str(run.report_id))
        if report is None:
            continue
        recent.append(
            {
                "type": "report_run",
                "id": int(run.id),
                "report_id": str(report.id),
                "run_id": int(run.id),
                "title": report.title,
                "subtitle": f"黄金报表 · {int(run.row_count or 0)} 行",
                "status": str(run.status),
                "occurred_at": _iso(run.finished_at or run.started_at),
                "action": "open_report",
            }
        )
    seen_conversation_ids = set()
    for conversation in conversations:
        conversation_id = str(conversation.conversation_id or "").strip()
        if not conversation_id or conversation_id in seen_conversation_ids:
            continue
        seen_conversation_ids.add(conversation_id)
        title = str(conversation.query or "").strip() or "未命名数据分析"
        recent.append(
            {
                "type": "conversation",
                "id": int(conversation.id),
                "conversation_id": conversation_id,
                "title": title[:80],
                "subtitle": f"ChatBI · {getattr(conversation, 'agent_display_name', None) or '数据分析'}",
                "status": str(conversation.status or "success"),
                "occurred_at": _iso(conversation.created_at),
                "action": "open_conversation",
            }
        )
    recent.sort(key=lambda item: item.get("occurred_at") or "", reverse=True)

    report_items: List[Dict[str, Any]] = []
    for report in reports:
        subscription = subscription_by_report.get(str(report.id))
        report_items.append(
            {
                "id": str(report.id),
                "title": report.title,
                "owner_name": report.owner_name,
                "is_owner": int(report.owner_user_id) == int(user_id),
                "is_favorite": bool(_report_value(report, "is_favorite", False)),
                "pinned": bool(_report_value(report, "pinned_at")),
                "last_run_at": _iso(_report_value(report, "user_last_run_at") or report.last_run_at),
                "last_error": report.last_error,
                "subscription_status": subscription.status if subscription else None,
                "subscription_cron_expr": subscription.cron_expr if subscription else None,
                "subscription_next_run_at": _iso(subscription.next_run_at) if subscription else None,
            }
        )
    report_items.sort(
        key=lambda item: (bool(item["pinned"]), item["last_run_at"] or ""),
        reverse=True,
    )

    latest_failed = max(failed_today, key=lambda item: item.started_at, default=None)
    latest_delivery = max(today_deliveries, key=lambda item: item.created_at, default=None)
    return {
        "attention": {
            "failed_runs_today": len(failed_today),
            "latest_failed_run": (
                {
                    "report_id": str(latest_failed.report_id),
                    "run_id": int(latest_failed.id),
                    "title": report_by_id.get(str(latest_failed.report_id)).title,
                    "error_message": latest_failed.error_message,
                    "occurred_at": _iso(latest_failed.started_at),
                }
                if latest_failed and report_by_id.get(str(latest_failed.report_id))
                else None
            ),
            "digests_today": len(today_deliveries),
            "latest_digest_at": _iso(latest_delivery.created_at) if latest_delivery else None,
            "active_subscriptions": len(active_subscriptions),
            "completed_subscriptions_today": len(
                {report_id for report_id in successful_scheduled_report_ids if report_id in subscription_by_report}
            ),
        },
        "recent_analysis": recent[:6],
        "report_summary": {
            "subscribed": sum(1 for item in report_items if item["subscription_status"]),
            "pinned": sum(1 for item in report_items if item["pinned"]),
            "favorite": sum(1 for item in report_items if item["is_favorite"]),
            "shared": sum(1 for item in report_items if not item["is_owner"]),
            "recent": sum(1 for item in report_items if item["last_run_at"]),
            "items": report_items[:12],
        },
        "generated_at": now.isoformat(),
    }


class DataPortalHomeService:
    @staticmethod
    async def build(
        db: AsyncSession,
        *,
        user_id: int,
        role_ids: Optional[Iterable[int]] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        role_ids = [int(value) for value in (role_ids or [])]
        visible_conditions = [PortalSavedReport.owner_user_id == user_id]
        visible_conditions.append(
            PortalSavedReport.shares.any(
                (PortalSavedReportShare.target_type == "user")
                & (PortalSavedReportShare.target_id == user_id)
            )
        )
        if role_ids:
            visible_conditions.append(
                PortalSavedReport.shares.any(
                    (PortalSavedReportShare.target_type == "role")
                    & (PortalSavedReportShare.target_id.in_(role_ids))
                )
            )

        report_result = await db.execute(
            select(PortalSavedReport)
            .options(selectinload(PortalSavedReport.shares))
            .where(or_(*visible_conditions), PortalSavedReport.status == "active")
            .order_by(desc(PortalSavedReport.updated_at))
        )
        reports = list(report_result.scalars().unique().all())
        report_ids = [str(item.id) for item in reports]
        owned_ids = [str(item.id) for item in reports if int(item.owner_user_id) == user_id]
        shared_ids = [str(item.id) for item in reports if int(item.owner_user_id) != user_id]

        prefs = {}
        if report_ids:
            pref_result = await db.execute(
                select(PortalSavedReportUserPref).where(
                    PortalSavedReportUserPref.user_id == user_id,
                    PortalSavedReportUserPref.report_id.in_(report_ids),
                )
            )
            prefs = {str(item.report_id): item for item in pref_result.scalars().all()}
        for report in reports:
            pref = prefs.get(str(report.id))
            report.is_favorite = bool(pref.is_favorite) if pref else False
            report.pinned_at = pref.pinned_at if pref else None
            report.user_last_run_at = pref.last_run_at if pref else None

        run_conditions = []
        if owned_ids:
            run_conditions.append(PortalSavedReportRun.report_id.in_(owned_ids))
        if shared_ids:
            run_conditions.append(
                and_(PortalSavedReportRun.report_id.in_(shared_ids), PortalSavedReportRun.user_id == user_id)
            )
        runs = []
        if run_conditions:
            run_result = await db.execute(
                select(PortalSavedReportRun)
                .where(or_(*run_conditions))
                .order_by(desc(PortalSavedReportRun.started_at))
                .limit(30)
            )
            runs = list(run_result.scalars().all())

        subscriptions = []
        if report_ids:
            subscription_result = await db.execute(
                select(PortalSavedReportSubscription).where(
                    PortalSavedReportSubscription.user_id == user_id,
                    PortalSavedReportSubscription.report_id.in_(report_ids),
                )
            )
            subscriptions = list(subscription_result.scalars().all())
        run_ids = [int(item.id) for item in runs]
        deliveries = []
        if run_ids:
            delivery_result = await db.execute(
                select(PortalSavedReportDigestDelivery)
                .where(PortalSavedReportDigestDelivery.run_id.in_(run_ids))
                .order_by(desc(PortalSavedReportDigestDelivery.created_at))
                .limit(20)
            )
            deliveries = list(delivery_result.scalars().all())

        agent_result = await db.execute(select(AIAgent).where(AIAgent.is_enabled.is_(True)))
        data_agents = {
            str(agent.id): agent
            for agent in agent_result.scalars().all()
            if "data_query" in {str(value).lower() for value in (agent.capabilities or [])}
        }
        conversations = []
        if data_agents:
            conversation_result = await db.execute(
                select(AgentExecutionHistory)
                .where(
                    AgentExecutionHistory.user_id == str(user_id),
                    AgentExecutionHistory.agent_id.in_(list(data_agents)),
                    AgentExecutionHistory.conversation_id.isnot(None),
                )
                .order_by(desc(AgentExecutionHistory.created_at))
                .limit(30)
            )
            conversations = list(conversation_result.scalars().all())
            for conversation in conversations:
                agent = data_agents.get(str(conversation.agent_id))
                conversation.agent_display_name = agent.display_name if agent else "数据分析"

        return build_home_payload(
            user_id=user_id,
            reports=reports,
            runs=runs,
            subscriptions=subscriptions,
            deliveries=deliveries,
            conversations=conversations,
            now=now or datetime.now(),
        )
