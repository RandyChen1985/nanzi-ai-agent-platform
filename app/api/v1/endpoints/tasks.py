from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm import get_db_session
from app.core.dependencies import require_api_key
from app.schemas.task import TaskCreate, TaskUpdate, TaskResponse, TaskLogResponse
from app.services.task_center_service import TaskCenterService
from app.schemas.response import StandardResponse, ListResponse

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
