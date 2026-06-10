import logging
import json
from typing import Optional, List
from app.services.ai.tools.tool_compat import tool
from app.core.context import get_current_agent_context
from app.services.task_center_service import TaskCenterService
from app.core.orm import AsyncSessionLocal

logger = logging.getLogger(__name__)

@tool
async def create_recurring_task(name: str, cron: str, prompt: str) -> str:
    """
    Create a new recurring scheduled task.
    
    Args:
        name: A descriptive name for the task (e.g., 'Daily PUE Report').
        cron: The standard Cron expression (e.g., '0 8 * * *' for daily 8 AM).
        prompt: The specific instruction for the AI to execute periodically.
    """
    ctx = get_current_agent_context()
    if not ctx or not ctx.user_id:
        return "Error: User context not found. Cannot create task."

    try:
        async with AsyncSessionLocal() as db:
            task = await TaskCenterService.create_task(
                db=db,
                user_id=ctx.user_id,
                name=name,
                agent_id=ctx.agent_id,
                cron_expr=cron,
                prompt=prompt,
                source="agent"
            )
            return f"Successfully created scheduled task '{name}' (ID: {task.id}). Next run: {task.next_run_at}"
    except Exception as e:
        logger.error(f"Failed to create task via tool: {e}", exc_info=True)
        return f"Error creating task: {str(e)}"

@tool
async def get_my_tasks() -> str:
    """
    List all active scheduled tasks owned by the current user.
    """
    ctx = get_current_agent_context()
    if not ctx or not ctx.user_id:
        return "Error: User context not found."

    try:
        async with AsyncSessionLocal() as db:
            tasks = await TaskCenterService.list_tasks(db, user_id=ctx.user_id, is_admin=ctx.is_admin)
            if not tasks:
                return "You have no active scheduled tasks."
            
            result = []
            for t in tasks:
                result.append({
                    "id": t.id,
                    "name": t.name,
                    "cron": t.cron_expr,
                    "prompt": t.prompt,
                    "status": "Running" if t.status == 1 else "Stopped",
                    "next_run": str(t.next_run_at) if t.next_run_at else "N/A"
                })
            return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error fetching tasks: {str(e)}"

@tool
async def cancel_task(task_id: int) -> str:
    """
    Cancel and delete a scheduled task by its ID.
    
    Args:
        task_id: The unique numeric ID of the task to cancel.
    """
    ctx = get_current_agent_context()
    if not ctx or not ctx.user_id:
        return "Error: User context not found."

    try:
        async with AsyncSessionLocal() as db:
            task = await TaskCenterService.get_task(db, task_id)
            if not task:
                return f"Task with ID {task_id} not found."
            
            if task.user_id != ctx.user_id and not ctx.is_admin:
                return "Permission denied: You can only cancel your own tasks."
            
            await TaskCenterService.delete_task(db, task_id)
            return f"Successfully cancelled and deleted task {task_id}."
    except Exception as e:
        return f"Error cancelling task: {str(e)}"

@tool
async def start_task(task_id: int) -> str:
    """
    Start or resume a paused scheduled task.
    
    Args:
        task_id: The unique numeric ID of the task to start.
    """
    ctx = get_current_agent_context()
    if not ctx or not ctx.user_id:
        return "Error: User context not found."

    try:
        async with AsyncSessionLocal() as db:
            task = await TaskCenterService.get_task(db, task_id)
            if not task:
                return f"Task with ID {task_id} not found."
            
            if task.user_id != ctx.user_id and not ctx.is_admin:
                return "Permission denied."
            
            await TaskCenterService.update_task(db, task_id, {"status": 1})
            return f"Successfully started task {task_id} ('{task.name}')."
    except Exception as e:
        return f"Error starting task: {str(e)}"

@tool
async def pause_task(task_id: int) -> str:
    """
    Pause an existing active scheduled task.
    
    Args:
        task_id: The unique numeric ID of the task to pause.
    """
    ctx = get_current_agent_context()
    if not ctx or not ctx.user_id:
        return "Error: User context not found."

    try:
        async with AsyncSessionLocal() as db:
            task = await TaskCenterService.get_task(db, task_id)
            if not task:
                return f"Task with ID {task_id} not found."
            
            if task.user_id != ctx.user_id and not ctx.is_admin:
                return "Permission denied."
            
            await TaskCenterService.update_task(db, task_id, {"status": 0})
            return f"Successfully paused task {task_id} ('{task.name}')."
    except Exception as e:
        return f"Error pausing task: {str(e)}"

@tool
async def run_task_manually(task_id: int) -> str:
    """
    Trigger a scheduled task immediately and manually.
    
    Args:
        task_id: The unique numeric ID of the task to run.
    """
    ctx = get_current_agent_context()
    if not ctx or not ctx.user_id:
        return "Error: User context not found."

    try:
        from app.services.ai.scheduler_service import scheduler_service
        async with AsyncSessionLocal() as db:
            task = await TaskCenterService.get_task(db, task_id)
            if not task:
                return f"Task with ID {task_id} not found."
            
            # Check permission
            if task.user_id != ctx.user_id and not ctx.is_admin:
                return "Permission denied."
            
            # Use scheduler_service to run it in background or directly
            # Here we call it directly as it's an async wrapper
            await scheduler_service.run_task(task_id, is_manual=True)
            return f"Successfully triggered task {task_id} ('{task.name}') manually."
    except Exception as e:
        logger.error(f"Error running task {task_id} manually: {e}", exc_info=True)
        return f"Error triggering task: {str(e)}"
