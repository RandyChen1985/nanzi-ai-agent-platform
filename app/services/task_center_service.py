import logging
from typing import List, Optional, Dict, Any
from sqlalchemy import select, update, delete, desc, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
import uuid

from app.models.task import AgentScheduledTask
from app.services.ai.scheduler_service import (
    TASK_METRICS_KEY,
    scheduler_service,
    _task_run_conversation_prefix,
)
from app.services.cron_compat import validate_standard_crontab
from app.models.audit import AgentExecutionHistory

logger = logging.getLogger(__name__)


def attach_task_metrics(task: AgentScheduledTask) -> AgentScheduledTask:
    cfg = task.config if isinstance(task.config, dict) else {}
    metrics = cfg.get("task_metrics") if isinstance(cfg.get("task_metrics"), dict) else {}
    task.trigger_count = int(metrics.get("trigger_count") or 0)
    task.success_count = int(metrics.get("success_count") or task.run_count or 0)
    task.failure_count = int(metrics.get("failure_count") or 0)
    task.skipped_count = int(metrics.get("skipped_count") or 0)
    task.consecutive_failures = int(metrics.get("consecutive_failures") or 0)
    task.health_status = metrics.get("health_status") or ("healthy" if task.run_count else "unknown")
    task.last_status = metrics.get("last_status")
    task.last_message = metrics.get("last_message")
    task.last_error = metrics.get("last_error")
    task.last_attempt_at = metrics.get("last_started_at") or metrics.get("last_finished_at")
    task.last_finished_at = metrics.get("last_finished_at")
    task.last_alert_at = metrics.get("last_alert_at")
    task.last_delivery_status = metrics.get("last_delivery_status")
    task.last_delivery_error = metrics.get("last_delivery_error")
    task.last_delivery_at = metrics.get("last_delivery_at")
    return task

class TaskCenterService:
    @staticmethod
    async def create_task(
        db: AsyncSession,
        user_id: int,
        name: str,
        agent_id: str,
        cron_expr: str,
        prompt: str,
        source: str = "web",
        config: Optional[Dict[str, Any]] = None
    ) -> AgentScheduledTask:
        # 服务层是公共入口（Web API 与 AI 工具都经过这里），必须在落库前校验表达式：
        # 否则非法值会先入库、再由调度器打日志跳过，形成「运行中但永不执行」的假成功，
        # AI 调用方还会收到创建成功。
        validate_standard_crontab(cron_expr)

        # Generate a dedicated conversation_id for this task
        conversation_id = f"task_conv_{uuid.uuid4().hex[:12]}"
        int_user_id = int(user_id) if user_id is not None else 0
        
        new_task = AgentScheduledTask(
            name=name,
            user_id=int_user_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            cron_expr=cron_expr,
            prompt=prompt,
            source=source,
            config=config,
            status=1 # Running by default
        )
        
        db.add(new_task)
        await db.commit()
        await db.refresh(new_task)
        
        # Sync to scheduler
        await scheduler_service.upsert_task(new_task)
        # 回填下次执行时间，让 Web 响应与 AI 工具返回值能反映调度是否真的生效
        new_task.next_run_at = scheduler_service.get_next_run_time(new_task.id)
        
        return new_task

    @staticmethod
    async def list_tasks(
        db: AsyncSession,
        user_id: Optional[int] = None,
        is_admin: bool = False
    ) -> List[Any]:
        from app.models.user import User
        from app.models.agent import AIAgent
        
        stmt = (
            select(AgentScheduledTask, User.real_name, User.user_name, AIAgent.display_name)
            .outerjoin(User, AgentScheduledTask.user_id == User.id)
            .outerjoin(AIAgent, AgentScheduledTask.agent_id == AIAgent.id)
        )
        
        if not is_admin and user_id is not None:
            try:
                target_user_id = int(user_id)
                stmt = stmt.where(AgentScheduledTask.user_id == target_user_id)
            except (ValueError, TypeError):
                stmt = stmt.where(AgentScheduledTask.user_id == user_id)
        
        stmt = stmt.order_by(desc(AgentScheduledTask.created_at))
        result = await db.execute(stmt)
        rows = result.all()
        
        tasks = []
        for task_obj, real_name, user_name, agent_name in rows:
            # We attach creator_name and agent_name attribute for Pydantic to pick up
            task_obj.creator_name = real_name or user_name or f"User:{task_obj.user_id}"
            task_obj.agent_name = agent_name or task_obj.agent_id or "Unknown Agent"
            task_obj.next_run_at = scheduler_service.get_next_run_time(task_obj.id)
            attach_task_metrics(task_obj)
            tasks.append(task_obj)
            
        return tasks

    @staticmethod
    async def get_task(db: AsyncSession, task_id: int) -> Optional[AgentScheduledTask]:
        stmt = select(AgentScheduledTask).where(AgentScheduledTask.id == task_id)
        result = await db.execute(stmt)
        task = result.scalar_one_or_none()
        if task:
            task.next_run_at = scheduler_service.get_next_run_time(task.id)
            attach_task_metrics(task)
        return task

    @staticmethod
    async def update_task(
        db: AsyncSession,
        task_id: int,
        updates: Dict[str, Any]
    ) -> Optional[AgentScheduledTask]:
        if "cron_expr" in updates:
            validate_standard_crontab(str(updates.get("cron_expr") or ""))
        if updates.get("status") == 1:
            # 启动前确认库内表达式可调度：历史脏数据若直接启用，调度器只会打日志跳过，
            # 用户（或 AI）会以为「已启动」而任务永远不跑。
            current_cron = (
                await db.execute(
                    select(AgentScheduledTask.cron_expr).where(AgentScheduledTask.id == task_id)
                )
            ).scalar_one_or_none()
            if current_cron:
                validate_standard_crontab(current_cron)

        # 执行指标存于 config.task_metrics（由调度器维护）；前端 PATCH 提交的 config
        # 通常不含该字段，整体覆盖会抹掉执行统计，这里始终以库内指标为准回填。
        if "config" in updates:
            from app.services.ai.scheduler_service import TASK_METRICS_KEY

            existing_config = (
                await db.execute(
                    select(AgentScheduledTask.config).where(AgentScheduledTask.id == task_id)
                )
            ).scalar_one_or_none() or {}
            new_config = dict(updates["config"] or {})
            if TASK_METRICS_KEY in existing_config:
                new_config[TASK_METRICS_KEY] = existing_config[TASK_METRICS_KEY]
            updates = {**updates, "config": new_config}

        stmt = update(AgentScheduledTask).where(AgentScheduledTask.id == task_id).values(**updates)
        await db.execute(stmt)
        await db.commit()
        
        updated_task = await TaskCenterService.get_task(db, task_id)
        if updated_task:
            await scheduler_service.upsert_task(updated_task)
        return updated_task

    @staticmethod
    async def delete_task(db: AsyncSession, task_id: int):
        task = await TaskCenterService.get_task(db, task_id)
        if task:
            # 彻底摘除调度 Job（含未执行的重试 Job）
            await scheduler_service.remove_task(task_id)
            
            # Delete from DB
            await db.execute(delete(AgentScheduledTask).where(AgentScheduledTask.id == task_id))
            await db.commit()

    @staticmethod
    async def get_task_logs(
        db: AsyncSession,
        task_id: int,
        page: int = 1,
        page_size: int = 10
    ):
        task = await TaskCenterService.get_task(db, task_id)
        if not task:
            return [], 0
            
        run_prefix = _task_run_conversation_prefix(task.conversation_id)

        # Scheduled task runs use isolated run conversation IDs, but all runs keep
        # the task's root conversation prefix so the task log drawer can aggregate them.
        stmt = (
            select(AgentExecutionHistory)
            .where(
                or_(
                    AgentExecutionHistory.conversation_id == task.conversation_id,
                    AgentExecutionHistory.conversation_id.like(f"{run_prefix}_run_%"),
                )
            )
            .order_by(desc(AgentExecutionHistory.created_at))
        )
        
        # Pagination
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db.execute(count_stmt)).scalar() or 0
        
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(stmt)
        return result.scalars().all(), total

    @staticmethod
    async def list_execution_history(
        db: AsyncSession,
        *,
        page: int = 1,
        page_size: int = 20,
        status: Optional[str] = None,
        task_id: Optional[int] = None,
        q: Optional[str] = None,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
        owner_user_id: Optional[int] = None,
        is_admin: bool = False,
    ) -> tuple[List[Dict[str, Any]], int]:
        """聚合定时任务会话前缀下的执行记录。

        管理员可看全部；普通用户仅看自己创建的任务。非管理员不会用宽泛的
        ``task_conv_%`` 扫描，避免越权看到他人已删任务残留。
        """
        from app.models.user import User
        from app.models.agent import AIAgent

        task_stmt = (
            select(AgentScheduledTask, User.real_name, User.user_name, AIAgent.display_name)
            .outerjoin(User, AgentScheduledTask.user_id == User.id)
            .outerjoin(AIAgent, AgentScheduledTask.agent_id == AIAgent.id)
        )
        if not is_admin:
            if owner_user_id is None:
                return [], 0
            task_stmt = task_stmt.where(AgentScheduledTask.user_id == int(owner_user_id))
        if task_id is not None:
            task_stmt = task_stmt.where(AgentScheduledTask.id == task_id)
        task_rows = (await db.execute(task_stmt)).all()

        task_meta_by_id: Dict[int, Dict[str, Any]] = {}
        exact_conv_to_task: Dict[str, int] = {}
        prefix_to_task: Dict[str, int] = {}
        for task_obj, real_name, user_name, agent_name in task_rows:
            meta = {
                "task_id": task_obj.id,
                "task_name": task_obj.name,
                "agent_id": task_obj.agent_id,
                "agent_name": agent_name or task_obj.agent_id or "Unknown Agent",
                "user_id": task_obj.user_id,
                "creator_name": real_name or user_name or f"User:{task_obj.user_id}",
                "conversation_id": task_obj.conversation_id,
            }
            task_meta_by_id[task_obj.id] = meta
            if task_obj.conversation_id:
                exact_conv_to_task[task_obj.conversation_id] = task_obj.id
                prefix_to_task[_task_run_conversation_prefix(task_obj.conversation_id)] = task_obj.id

        keyword = (q or "").strip()
        keyword_matched_task_ids: set[int] = set()
        if keyword:
            lowered = keyword.casefold()
            for tid, meta in task_meta_by_id.items():
                blob = " ".join(
                    str(meta.get(key) or "")
                    for key in ("task_name", "creator_name", "agent_name", "agent_id")
                ).casefold()
                if lowered in blob:
                    keyword_matched_task_ids.add(tid)

        conv_filters = []
        if task_id is not None:
            if task_id not in task_meta_by_id:
                return [], 0
            meta = task_meta_by_id[task_id]
            prefix = _task_run_conversation_prefix(meta["conversation_id"] or "")
            conv_filters.append(AgentExecutionHistory.conversation_id == meta["conversation_id"])
            if prefix:
                conv_filters.append(AgentExecutionHistory.conversation_id.like(f"{prefix}_run_%"))
        elif is_admin:
            # 管理员：宽泛前缀，已删任务残留也可看到
            conv_filters.append(AgentExecutionHistory.conversation_id.like("task_conv_%"))
        else:
            # 普通用户：只扫自己仍存在的任务会话
            if not task_meta_by_id:
                return [], 0
            for meta in task_meta_by_id.values():
                conv_filters.append(
                    AgentExecutionHistory.conversation_id == meta["conversation_id"]
                )
                prefix = _task_run_conversation_prefix(meta["conversation_id"] or "")
                if prefix:
                    conv_filters.append(
                        AgentExecutionHistory.conversation_id.like(f"{prefix}_run_%")
                    )

        stmt = select(AgentExecutionHistory).where(or_(*conv_filters))
        if status:
            stmt = stmt.where(AgentExecutionHistory.status == status)
        if start_at is not None:
            stmt = stmt.where(AgentExecutionHistory.created_at >= start_at)
        if end_at is not None:
            stmt = stmt.where(AgentExecutionHistory.created_at <= end_at)
        if keyword:
            like = f"%{keyword}%"
            history_text_match = or_(
                AgentExecutionHistory.query.like(like),
                AgentExecutionHistory.summary.like(like),
                AgentExecutionHistory.trace_id.like(like),
                AgentExecutionHistory.username.like(like),
            )
            if keyword_matched_task_ids:
                matched_conv_filters = []
                for tid in keyword_matched_task_ids:
                    meta = task_meta_by_id[tid]
                    matched_conv_filters.append(
                        AgentExecutionHistory.conversation_id == meta["conversation_id"]
                    )
                    prefix = _task_run_conversation_prefix(meta["conversation_id"] or "")
                    if prefix:
                        matched_conv_filters.append(
                            AgentExecutionHistory.conversation_id.like(f"{prefix}_run_%")
                        )
                stmt = stmt.where(or_(history_text_match, *matched_conv_filters))
            else:
                stmt = stmt.where(history_text_match)

        stmt = stmt.order_by(desc(AgentExecutionHistory.created_at))
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db.execute(count_stmt)).scalar() or 0
        rows = (
            await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))
        ).scalars().all()

        def resolve_task(conversation_id: Optional[str]) -> Optional[Dict[str, Any]]:
            if not conversation_id:
                return None
            tid = exact_conv_to_task.get(conversation_id)
            if tid is not None:
                return task_meta_by_id.get(tid)
            for prefix, mapped_tid in prefix_to_task.items():
                if conversation_id.startswith(f"{prefix}_run_"):
                    return task_meta_by_id.get(mapped_tid)
            return None

        items: List[Dict[str, Any]] = []
        for row in rows:
            meta = resolve_task(row.conversation_id) or {}
            items.append(
                {
                    "id": row.id,
                    "trace_id": row.trace_id,
                    "query": row.query,
                    "summary": row.summary,
                    "status": row.status or "success",
                    "execution_time_ms": row.execution_time_ms,
                    "created_at": row.created_at,
                    "conversation_id": row.conversation_id,
                    "username": row.username,
                    "task_id": meta.get("task_id"),
                    "task_name": meta.get("task_name"),
                    "agent_id": meta.get("agent_id") or row.agent_id,
                    "agent_name": meta.get("agent_name"),
                    "user_id": meta.get("user_id"),
                    "creator_name": meta.get("creator_name"),
                }
            )
        return items, total
