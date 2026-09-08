from typing import List, Dict, Any, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select

from app.core.orm import get_db_session
from app.core.dependencies import require_api_key
from app.schemas.task import (
    TaskCreate,
    TaskUpdate,
    TaskResponse,
    TaskLogResponse,
    TaskExecutionHistoryItem,
)
from app.services.task_center_service import TaskCenterService
from app.schemas.response import StandardResponse, ListResponse
from app.models.saved_report import PortalSavedReport, PortalSavedReportRun, PortalSavedReportSubscription
from app.models.task import AgentScheduledTask
from app.models.user import User
from app.services.resource_scope_normalizer import (
    has_any_resource,
    normalize_resource_scope_for_user,
)
from app.services.task_execution_options import (
    normalize_reasoning_effort,
    normalize_temperature,
    normalize_thinking_enable,
)

router = APIRouter()


async def _task_owner_info(db: AsyncSession, owner_user_id: Any) -> Dict[str, Any]:
    """任务按所有者身份执行，资源范围也必须按所有者的授权目录校验。"""
    try:
        parsed_user_id = int(owner_user_id)
    except (TypeError, ValueError):
        parsed_user_id = owner_user_id
    owner = (await db.execute(select(User).where(User.id == parsed_user_id))).scalar_one_or_none()
    if owner is None:
        raise HTTPException(status_code=404, detail="任务所有者不存在")
    return {
        "user_id": owner.id,
        "user_name": owner.user_name,
        "real_name": owner.real_name,
        "role": owner.role,
    }


async def _sanitize_task_config(
    db: AsyncSession,
    owner_info: Dict[str, Any],
    config: Any,
) -> Any:
    """收敛资源范围，并只保留合法的任务级模型思考与温度覆盖值。"""
    if not isinstance(config, dict):
        return config
    sanitized = dict(config)
    if "thinking_enable" in sanitized:
        thinking_enable = normalize_thinking_enable(sanitized.get("thinking_enable"))
        if thinking_enable is None:
            sanitized.pop("thinking_enable", None)
        else:
            sanitized["thinking_enable"] = thinking_enable
            if thinking_enable is False:
                sanitized.pop("reasoning_effort", None)
    if "reasoning_effort" in sanitized:
        reasoning_effort = normalize_reasoning_effort(sanitized.get("reasoning_effort"))
        if reasoning_effort is None:
            sanitized.pop("reasoning_effort", None)
        else:
            sanitized["reasoning_effort"] = reasoning_effort
    if "temperature" in sanitized:
        temperature = normalize_temperature(sanitized.get("temperature"))
        if temperature is None:
            sanitized.pop("temperature", None)
        else:
            sanitized["temperature"] = temperature
    if "resource_scope" not in sanitized:
        return sanitized
    normalized = await normalize_resource_scope_for_user(
        db, owner_info, sanitized.get("resource_scope") or {}
    )
    if has_any_resource(normalized) or normalized.get("project_name"):
        sanitized["resource_scope"] = normalized
    else:
        sanitized.pop("resource_scope", None)
    return sanitized


@router.post("/", response_model=StandardResponse[TaskResponse])
async def create_task(
    task_in: TaskCreate,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Create a new scheduled task.
    """
    raw_user_id = user_info.get("user_id") or user_info.get("id")
    user_id = int(raw_user_id) if raw_user_id is not None else None
    owner_info = await _task_owner_info(db, user_id)
    config = await _sanitize_task_config(db, owner_info, task_in.config)
    task = await TaskCenterService.create_task(
        db, user_id, task_in.name, task_in.agent_id, task_in.cron_expr, task_in.prompt, source="web", config=config
    )
    return StandardResponse(data=TaskResponse.from_orm(task))

@router.get("/", response_model=StandardResponse[List[TaskResponse]])
async def list_tasks(
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    List all scheduled tasks for the current user.
    """
    raw_user_id = user_info.get("user_id") or user_info.get("id")
    user_id = int(raw_user_id) if raw_user_id is not None else None
    is_admin = user_info.get("role") == "admin"
    tasks = await TaskCenterService.list_tasks(db, user_id, is_admin)
    return StandardResponse(data=[TaskResponse.from_orm(t) for t in tasks])


@router.get("/report-subscriptions")
async def list_report_subscriptions(
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id = int(user_info["user_id"])
    stmt = (
        select(PortalSavedReportSubscription, PortalSavedReport, User)
        .join(PortalSavedReport, PortalSavedReport.id == PortalSavedReportSubscription.report_id)
        .join(User, User.id == PortalSavedReportSubscription.user_id)
    )
    if user_info.get("role") != "admin":
        stmt = stmt.where(PortalSavedReportSubscription.user_id == user_id)
    rows = (await db.execute(stmt.order_by(PortalSavedReportSubscription.created_at.desc()))).all()
    from app.services.ai.scheduler_service import scheduler_service
    items = []
    for subscription, report, owner in rows:
        counts = (await db.execute(select(
            func.count(PortalSavedReportRun.id),
            func.sum(PortalSavedReportRun.status == "success"),
            func.sum(PortalSavedReportRun.status == "error"),
        ).where(PortalSavedReportRun.task_id == subscription.id))).one()
        trigger_count, success_count, failure_count = (int(value or 0) for value in counts)
        items.append({
            "id": -int(subscription.id),
            "subscription_id": int(subscription.id),
            "task_type": "saved_report",
            "name": report.title,
            "user_id": int(subscription.user_id),
            "creator_name": owner.real_name or owner.user_name,
            "agent_id": "saved_report",
            "agent_name": "黄金报表",
            "conversation_id": "",
            "source": "saved_report",
            "cron_expr": subscription.cron_expr,
            "prompt": report.description or f"定时运行黄金报表：{report.title}",
            "status": 1 if subscription.status == "active" else 0,
            "run_count": success_count,
            "trigger_count": trigger_count,
            "success_count": success_count,
            "failure_count": failure_count,
            "skipped_count": 0,
            "consecutive_failures": int(subscription.consecutive_failures or 0),
            "health_status": "error" if subscription.status == "error" else ("warning" if subscription.consecutive_failures else ("healthy" if success_count else "unknown")),
            "last_status": "error" if subscription.last_error else ("success" if subscription.last_run_id else None),
            "last_error": subscription.last_error,
            "last_attempt_at": subscription.last_run_at.isoformat() if subscription.last_run_at else None,
            "last_run_at": subscription.last_run_at,
            "last_run_id": str(subscription.last_run_id) if subscription.last_run_id else None,
            "next_run_at": scheduler_service.get_saved_report_subscription_next_run_time(subscription.id) or subscription.next_run_at,
            "created_at": subscription.created_at,
            "updated_at": subscription.updated_at,
            "report_id": report.id,
        })
    return StandardResponse(data=items)


async def _owned_report_subscription(db: AsyncSession, subscription_id: int, user_info: Dict[str, Any]):
    row = (await db.execute(select(PortalSavedReportSubscription).where(
        PortalSavedReportSubscription.id == subscription_id
    ))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="报表订阅不存在")
    if int(row.user_id) != int(user_info["user_id"]):
        raise HTTPException(status_code=403, detail="只有订阅所有者可以执行此操作")
    return row


@router.patch("/report-subscriptions/{subscription_id}/status")
async def update_report_subscription_status(subscription_id: int, payload: Dict[str, Any],
                                            user_info=Depends(require_api_key), db: AsyncSession = Depends(get_db_session)):
    row = await _owned_report_subscription(db, subscription_id, user_info)
    row.status = "active" if bool(payload.get("active")) else "paused"
    from app.services.ai.scheduler_service import scheduler_service
    if row.status == "active":
        await scheduler_service.upsert_saved_report_subscription(row)
        row.next_run_at = scheduler_service.get_saved_report_subscription_next_run_time(row.id)
    else:
        await scheduler_service.remove_saved_report_subscription(row.id)
        row.next_run_at = None
    await db.flush()
    return StandardResponse(data={"success": True, "status": row.status})


@router.post("/report-subscriptions/{subscription_id}/run")
async def run_report_subscription(subscription_id: int, user_info=Depends(require_api_key), db: AsyncSession = Depends(get_db_session)):
    await _owned_report_subscription(db, subscription_id, user_info)
    import asyncio
    from app.services.ai.scheduler_service import _saved_report_subscription_wrapper
    asyncio.create_task(_saved_report_subscription_wrapper(subscription_id, is_manual=True))
    return StandardResponse(data={"message": "报表订阅已触发"})


@router.delete("/report-subscriptions/{subscription_id}")
async def delete_report_subscription(subscription_id: int, user_info=Depends(require_api_key), db: AsyncSession = Depends(get_db_session)):
    row = await _owned_report_subscription(db, subscription_id, user_info)
    from app.services.ai.scheduler_service import scheduler_service
    await scheduler_service.remove_saved_report_subscription(row.id)
    await db.delete(row)
    await db.flush()
    return StandardResponse(data={"success": True})


@router.get(
    "/execution-history",
    response_model=StandardResponse[ListResponse[TaskExecutionHistoryItem]],
    summary="任务执行记录（管理员看全部，普通用户仅看自己的）",
)
async def list_execution_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, description="执行状态，如 success / failed"),
    task_id: Optional[int] = Query(None, description="限定某个定时任务"),
    q: Optional[str] = Query(None, description="关键词：任务名/创建人/摘要/Trace"),
    start_at: Optional[datetime] = Query(None, description="开始时间（含）"),
    end_at: Optional[datetime] = Query(None, description="结束时间（含）"),
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    is_admin = user_info.get("role") == "admin"
    raw_user_id = user_info.get("user_id") or user_info.get("id")
    owner_user_id = int(raw_user_id) if raw_user_id is not None and not is_admin else None
    items, total = await TaskCenterService.list_execution_history(
        db,
        page=page,
        page_size=page_size,
        status=status,
        task_id=task_id,
        q=q,
        start_at=start_at,
        end_at=end_at,
        owner_user_id=owner_user_id,
        is_admin=is_admin,
    )
    return StandardResponse(
        data=ListResponse(
            items=[TaskExecutionHistoryItem.model_validate(item) for item in items],
            total=total,
            page=page,
            page_size=page_size,
        )
    )


def _check_task_ownership(task: AgentScheduledTask, user_info: Dict[str, Any]) -> None:
    if user_info.get("role") == "admin":
        return
    raw_user_id = user_info.get("user_id") or user_info.get("id")
    if raw_user_id is None or int(task.user_id) != int(raw_user_id):
        raise HTTPException(status_code=403, detail="Permission denied")


@router.get("/{task_id}", response_model=StandardResponse[TaskResponse])
async def get_task(
    task_id: int,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get task details.
    """
    task = await TaskCenterService.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    _check_task_ownership(task, user_info)
    return StandardResponse(data=TaskResponse.from_orm(task))

@router.patch("/{task_id}", response_model=StandardResponse[TaskResponse])
async def update_task(
    task_id: int,
    task_in: TaskUpdate,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Update a task definition or status.
    """
    task = await TaskCenterService.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    _check_task_ownership(task, user_info)

    payload = task_in.model_dump(exclude_unset=True)
    if "config" in payload:
        owner_info = await _task_owner_info(db, task.user_id)
        payload["config"] = await _sanitize_task_config(db, owner_info, payload["config"])

    updated = await TaskCenterService.update_task(db, task_id, payload)
    return StandardResponse(data=TaskResponse.from_orm(updated))

@router.delete("/{task_id}", response_model=StandardResponse[Dict[str, bool]])
async def delete_task(
    task_id: int,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Delete a scheduled task.
    """
    task = await TaskCenterService.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    _check_task_ownership(task, user_info)
        
    await TaskCenterService.delete_task(db, task_id)
    return StandardResponse(data={"success": True})

@router.post("/{task_id}/run", response_model=StandardResponse[Dict[str, str]])
async def run_task_immediately(
    task_id: int,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Manually trigger a task execution now.
    """
    from app.services.ai.scheduler_service import scheduler_service
    
    task = await TaskCenterService.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    _check_task_ownership(task, user_info)
    
    # Trigger async
    import asyncio
    asyncio.create_task(scheduler_service.run_task(task_id, is_manual=True))
    
    return StandardResponse(data={"message": "Task triggered successfully"})

@router.get("/{task_id}/logs", response_model=StandardResponse[ListResponse[TaskLogResponse]])
async def get_task_logs(
    task_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get execution history for a specific task.
    """
    task = await TaskCenterService.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    _check_task_ownership(task, user_info)
        
    logs, total = await TaskCenterService.get_task_logs(db, task_id, page, page_size)
    items = [TaskLogResponse.from_orm(l) for l in logs]
    
    return StandardResponse(data=ListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size
    ))
