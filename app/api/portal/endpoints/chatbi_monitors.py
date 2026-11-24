import hashlib
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.dependencies import require_api_key
from app.core.orm import get_db_session
from app.models.saved_report import PortalSavedReport, PortalSavedReportSubscription
from app.schemas.response import StandardResponse
from app.services.ai.chatbi_result_stack import ChatBIResultRef, resolve_result_reference
from app.services.ai.memory_service import memory_service
from app.services.saved_report_subscription_service import schedule_to_cron

router = APIRouter()


def build_chatbi_monitor_report_id(user_id: str, conversation_id: str, result_id: str) -> str:
    identity = f"{user_id}:{conversation_id}:{result_id}".encode("utf-8")
    return f"chatbi_monitor_{hashlib.sha256(identity).hexdigest()[:32]}"


def validate_alert_condition_fields(condition: Dict[str, Any], rows: list[dict[str, Any]]) -> None:
    condition_type = str(condition.get("type") or "always")
    field = str(condition.get("field") or "").strip()
    if condition_type in {"threshold", "rate_of_change"}:
        available = {str(key) for row in rows if isinstance(row, dict) for key in row}
        if not field or field not in available:
            raise ValueError(f"告警字段无效，可选字段：{', '.join(sorted(available)) or '无'}")


class CreateChatBIMonitorRequest(BaseModel):
    conversation_id: str
    result_id: Optional[str] = None
    title: Optional[str] = None
    schedule_type: str = "daily"
    time_value: str = "09:00"
    weekday: Optional[int] = None
    monthday: Optional[int] = None
    cron_expr: Optional[str] = None
    alert_condition: Optional[Dict[str, Any]] = Field(default_factory=lambda: {"version": 1, "type": "always"})
    notify_on_success: bool = True


@router.post("", summary="把当前 ChatBI 结果转成报表订阅和告警")
async def create_chatbi_monitor(
    body: CreateChatBIMonitorRequest,
    user_info=Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = str(user_info["user_id"])
    stack = await memory_service.get_data_result_stack(user_id, body.conversation_id)
    refs = [ChatBIResultRef.from_dict(item) for item in stack if isinstance(item, dict)]
    reference = resolve_result_reference(refs, body.result_id or "当前结果")
    result = reference.result
    if result is None:
        raise HTTPException(status_code=409, detail="当前会话没有可创建监控的结构化结果")
    sql = str(result.sql or "").strip()
    if not re.match(r"^\s*(?:WITH\b[\s\S]+?\bSELECT\b|SELECT\b)", sql, re.I):
        raise HTTPException(status_code=409, detail="当前结果没有可复用的只读 SQL")
    condition = dict(body.alert_condition or {"version": 1, "type": "always"})
    if str(condition.get("type") or "") not in {"always", "threshold", "rate_of_change", "no_data"}:
        raise HTTPException(status_code=400, detail="告警条件类型无效")
    try:
        validate_alert_condition_fields(condition, result.rows)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        cron_expr = schedule_to_cron(
            body.schedule_type,
            time_value=body.time_value,
            weekday=body.weekday,
            monthday=body.monthday,
            cron_expr=body.cron_expr,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    report_id = build_chatbi_monitor_report_id(user_id, body.conversation_id, result.result_id)
    existing_report = await db.get(PortalSavedReport, report_id)
    if existing_report is not None:
        existing_subscription = (await db.execute(
            select(PortalSavedReportSubscription).where(
                PortalSavedReportSubscription.report_id == report_id,
                PortalSavedReportSubscription.user_id == int(user_id),
            )
        )).scalars().first()
        return StandardResponse(data={
            "report_id": report_id,
            "subscription_id": existing_subscription.id if existing_subscription else None,
            "created": False,
        })
    report = PortalSavedReport(
        id=report_id,
        title=(body.title or f"{result.question}监控")[:100],
        description="由 ChatBI 当前查询结果创建",
        sql_content=sql,
        data_source=result.data_source or "default",
        original_query=result.question,
        mode="static_sql",
        owner_user_id=int(user_id),
        owner_name=user_info.get("real_name") or user_info.get("user_name"),
        visibility="private",
        status="active",
    )
    subscription = PortalSavedReportSubscription(
        report_id=report_id,
        user_id=int(user_id),
        schedule_type=body.schedule_type,
        cron_expr=cron_expr,
        timezone="Asia/Shanghai",
        params={},
        notify_on_success=body.notify_on_success,
        notify_on_failure=True,
        external_channels=[],
        alert_condition=condition,
        alert_state={},
        status="active",
    )
    db.add_all([report, subscription])
    await db.flush()
    from app.services.ai.scheduler_service import scheduler_service
    await scheduler_service.upsert_saved_report_subscription(subscription)
    subscription.next_run_at = scheduler_service.get_saved_report_subscription_next_run_time(
        subscription.id,
        cron_expr=subscription.cron_expr,
        status=subscription.status,
        timezone_name=getattr(subscription, "timezone", None),
    )
    return StandardResponse(data={
        "report_id": report_id,
        "subscription_id": subscription.id,
        "cron_expr": cron_expr,
        "alert_condition": condition,
        "next_run_at": subscription.next_run_at.isoformat() if subscription.next_run_at else None,
        "created": True,
    })
