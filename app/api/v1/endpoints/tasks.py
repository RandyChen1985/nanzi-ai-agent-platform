from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select

from app.core.orm import get_db_session
from app.core.dependencies import require_api_key
from app.schemas.task import TaskCreate, TaskUpdate, TaskResponse, TaskLogResponse
from app.services.task_center_service import TaskCenterService
from app.schemas.response import StandardResponse, ListResponse
from app.models.saved_report import PortalSavedReport, PortalSavedReportRun, PortalSavedReportSubscription
from app.models.user import User

router = APIRouter()

@router.post("/", response_model=StandardResponse[TaskResponse])
async def create_task(
    task_in: TaskCreate,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Create a new scheduled task.
    """
    user_id = user_info.get("user_id")
    task = await TaskCenterService.create_task(
        db, user_id, task_in.name, task_in.agent_id, task_in.cron_expr, task_in.prompt, source="web", config=task_in.config
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
    user_id = user_info.get("user_id")
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
    
    # print(f"DEBUG: task.user_id={task.user_id} ({type(task.user_id)}), user_info.user_id={user_info.get('user_id')} ({type(user_info.get('user_id'))})")
    if str(task.user_id) != str(user_info.get("user_id")) and user_info.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Permission denied")
        
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
    
    # print(f"DEBUG: task.user_id={task.user_id} ({type(task.user_id)}), user_info.user_id={user_info.get('user_id')} ({type(user_info.get('user_id'))})")
    if str(task.user_id) != str(user_info.get("user_id")) and user_info.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Permission denied")
        
    updated = await TaskCenterService.update_task(db, task_id, task_in.model_dump(exclude_unset=True))
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
    
    # print(f"DEBUG: task.user_id={task.user_id} ({type(task.user_id)}), user_info.user_id={user_info.get('user_id')} ({type(user_info.get('user_id'))})")
    if str(task.user_id) != str(user_info.get("user_id")) and user_info.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Permission denied")
        
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
    
    # Check permission
    # print(f"DEBUG: task.user_id={task.user_id} ({type(task.user_id)}), user_info.user_id={user_info.get('user_id')} ({type(user_info.get('user_id'))})")
    if str(task.user_id) != str(user_info.get("user_id")) and user_info.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Permission denied")
    
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
    
    # print(f"DEBUG: task.user_id={task.user_id} ({type(task.user_id)}), user_info.user_id={user_info.get('user_id')} ({type(user_info.get('user_id'))})")
    if str(task.user_id) != str(user_info.get("user_id")) and user_info.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Permission denied")
        
    logs, total = await TaskCenterService.get_task_logs(db, task_id, page, page_size)
    items = [TaskLogResponse.from_orm(l) for l in logs]
    
    return StandardResponse(data=ListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size
    ))
