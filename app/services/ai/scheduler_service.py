import logging
import json
import uuid
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.orm import AsyncSessionLocal, engine
from app.core import redis
from app.models.task import AgentScheduledTask
from app.models.user import User

logger = logging.getLogger(__name__)

async def _scheduled_task_wrapper(task_id: int, is_manual: bool = False):
    """
    Top-level wrapper function for task execution to avoid APScheduler serialization issues.
    """
    # Delay import to avoid circular dependencies
    from app.services.ai.agent_service import agent_service
    
    # 1. Distributed Lock
    lock_key = f"lock:task_exec:{task_id}:{datetime.now().strftime('%Y%m%d%H%M')}"
    if not is_manual:
        if not await redis.redis_client.set(lock_key, "locked", ex=300, nx=True):
            logger.warning(f"⏩ Task {task_id} skipped: already running on another node (Locked).")
            return

    logger.info(f"🔔 Triggering {'MANUAL ' if is_manual else 'SCHEDULED '}task {task_id}")
    
    async with AsyncSessionLocal() as session:
        # 2. Fetch Task Details
        stmt = select(AgentScheduledTask).where(AgentScheduledTask.id == task_id)
        result = await session.execute(stmt)
        task = result.scalar_one_or_none()
        
        # If manual, we allow running even if paused (status=0)
        if not task or (task.status != 1 and not is_manual):
            logger.warning(f"⏩ Task {task_id} skipped: Not found or not active (Status: {task.status if task else 'N/A'}).")
            return

        # 3. User Impersonation
        user_stmt = select(User).where(User.id == task.user_id)
        user_result = await session.execute(user_stmt)
        user = user_result.scalar_one_or_none()
        
        if not user:
            logger.error(f"Task {task_id} failed: User {task.user_id} not found.")
            return

        # 3.1 Fetch Agent Name for Forced Routing
        from app.models.agent import AIAgent
        agent_stmt = select(AIAgent.display_name).where(AIAgent.id == task.agent_id)
        agent_res = await session.execute(agent_stmt)
        agent_display_name = agent_res.scalar_one_or_none() or task.agent_id

        user_info = {
            "user_id": user.id,
            "user_name": user.user_name,
            "real_name": user.real_name,
            "role": user.role,
            "is_scheduled_task": True,
            "task_name": task.name
        }

        # 4. Execute via Agent Service
        try:
            # Add structured prefix with @AgentName for forced routing
            full_prompt = f"【自动化指令-任务ID: {task_id}】@{agent_display_name} {task.prompt}"

            logger.info(f"🚀 Executing task {task_id} ('{task.name}') | Agent: {task.agent_id} | ConvID: {task.conversation_id}")
            
            # NOTE: We don't generate trace_id here, we let agent_service generate it 
            # and capture it from the response to ensure consistency with Audit Logs.
            result = await agent_service.chat_completion(
                messages=[{"role": "user", "content": full_prompt}],
                agent_id=task.agent_id,
                conversation_id=task.conversation_id, # This MUST be the ID from DB
                user_info=user_info,
                enable_multi_agent=True
            )
            
            trace_id = result.get('trace_id')
            content_preview = result.get('content', '')[:100]
            logger.info(f"✅ Task {task_id} finished. Trace: {trace_id}. Response: {content_preview}...")
            
            # 5. Update Task Metadata (Atomic update)
            await session.execute(
                update(AgentScheduledTask)
                .where(AgentScheduledTask.id == task_id)
                .values(
                    last_run_id=trace_id, 
                    last_run_at=datetime.now(),
                    run_count=AgentScheduledTask.run_count + 1
                )
            )
            await session.commit()
            logger.info(f"📊 Updated run_count and last_run_id for task {task_id}")
            
            # Allow logs to flush
            await asyncio.sleep(0.5)
            
        except Exception as e:
            logger.error(f"❌ Task {task_id} execution failed: {e}", exc_info=True)


async def _system_audit_log_maintenance_job():
    """
    System-level background job to auto-expand partitions and prune expired logs.
    """
    logger.info("⏰ Starting system audit log partition maintenance job...")
    try:
        from app.services.partition_service import PartitionService
        from app.services.config_service import ConfigService
        
        async with AsyncSessionLocal() as db_session:
            # 1. 自动扩容未来分区
            await PartitionService.expand_partitions(db_session)
            
            # 2. 自动清理过期日志
            retention_str = await ConfigService.get("audit_log_retention_days", default="90")
            try:
                retention_days = int(retention_str)
            except (ValueError, TypeError):
                retention_days = 90
                
            res = await PartitionService.prune_expired_logs(db_session, retention_days)
            logger.info(f"✅ System audit log partition maintenance completed. Result: {res}")
    except Exception as e:
        logger.error(f"❌ Failed to run system audit log partition maintenance: {e}", exc_info=True)


async def _system_memory_consolidation_job():
    """
    系统级定时任务：每天凌晨对所有活跃用户的相似记忆进行合并整理与降噪。
    """
    logger.info("⏰ Starting system memory consolidation job...")
    
    # 1. 分布式锁 (ex=3600 nx=True)
    lock_key = f"lock:system_memory_consolidation:{datetime.now().strftime('%Y%m%d%H%M')}"
    if not await redis.redis_client.set(lock_key, "locked", ex=3600, nx=True):
        logger.warning("⏩ System memory consolidation skipped: lock already acquired by another node.")
        return
        
    try:
        from app.services.ai.memory_index_service import MemoryIndexService
        
        # 2. 查询所有启用的用户
        async with AsyncSessionLocal() as session:
            stmt = select(User.id).where(User.status == 1)
            result = await session.execute(stmt)
            user_ids = result.scalars().all()
            
        logger.info(f"Loaded {len(user_ids)} active users for memory consolidation.")
        
        # 3. 逐个用户执行记忆降噪合并
        for u_id in user_ids:
            try:
                # 传入 str(u_id) 因为记忆是以 string 作为 user_id 键存储的
                await MemoryIndexService.consolidate_user_memories(str(u_id))
            except Exception as ex:
                logger.error(f"❌ Failed to consolidate memory for user {u_id}: {ex}")
                
        logger.info("✅ System memory consolidation job finished successfully.")
    except Exception as e:
        logger.error(f"❌ Failed to run system memory consolidation job: {e}", exc_info=True)


async def _system_third_party_user_sync_job():
    """系统级定时任务：从第三方数据源同步用户。"""
    logger.info("⏰ Starting third-party user sync job...")

    lock_key = f"lock:system_third_party_user_sync:{datetime.now().strftime('%Y%m%d%H%M')}"
    if not await redis.redis_client.set(lock_key, "locked", ex=3600, nx=True):
        logger.warning("⏩ Third-party user sync skipped: lock already acquired by another node.")
        return

    try:
        from app.services.user_sync_service import UserSyncService

        config = await UserSyncService.get_config()
        if not config.enabled or config.schedule == "off":
            logger.info("Third-party user sync is disabled, skipping.")
            return

        async with AsyncSessionLocal() as session:
            result = await UserSyncService.run_sync(session)
            logger.info(
                "✅ Third-party user sync finished. created=%s updated=%s failed=%s",
                result["created"],
                result["updated"],
                result["failed"],
            )
    except Exception as e:
        logger.error(f"❌ Failed to run third-party user sync job: {e}", exc_info=True)


class TaskSchedulerService:
    _instance = None
    _scheduler: Optional[AsyncIOScheduler] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TaskSchedulerService, cls).__new__(cls)
        return cls._instance

    async def start(self):
        if self._scheduler and self._scheduler.running:
            return

        db_url = f"mysql+pymysql://{settings.MYSQL_USER}:{settings.MYSQL_PASSWORD}@{settings.MYSQL_HOST}:{settings.MYSQL_PORT}/{settings.MYSQL_DB}"
        # Use a custom table name to ensure a clean slate and match project conventions
        job_stores = {
            'default': SQLAlchemyJobStore(url=db_url, tablename='ai_agent_scheduler_jobs')
        }
        from pytz import timezone
        tz = timezone('Asia/Shanghai')

        self._scheduler = AsyncIOScheduler(jobstores=job_stores, timezone=tz)
        self._scheduler.start()

        # 注册系统日志与分区清理常驻任务，每日凌晨 2:00 运行
        self._scheduler.add_job(
            _system_audit_log_maintenance_job,
            CronTrigger(hour=2, minute=0, timezone=tz),
            id="system_audit_log_maintenance",
            replace_existing=True
        )

        # 注册系统记忆降噪合并任务，每日凌晨 3:00 运行
        self._scheduler.add_job(
            _system_memory_consolidation_job,
            CronTrigger(hour=3, minute=0, timezone=tz),
            id="system_memory_consolidation",
            replace_existing=True
        )

        await self.reschedule_third_party_user_sync()

        now = datetime.now(tz)
        logger.info(f"🚀 Agent Task Scheduler started (Fixed Serialization). Current Scheduler Time: {now}")
        await self.reload_tasks()

    async def stop(self):
        if self._scheduler:
            if self._scheduler.running:
                self._scheduler.shutdown()
            self._scheduler = None
            logger.info("🛑 Agent Task Scheduler stopped.")

    async def reload_tasks(self):
        async with AsyncSessionLocal() as session:
            stmt = select(AgentScheduledTask).where(AgentScheduledTask.status == 1)
            result = await session.execute(stmt)
            tasks = result.scalars().all()
            for task in tasks:
                await self._add_job_to_memory(task)
        logger.info(f"Loaded {len(tasks)} active tasks into scheduler.")

    async def _add_job_to_memory(self, task: AgentScheduledTask):
        if not self._scheduler:
            return
            
        job_id = f"task_{task.id}"
        
        # Defensive cleanup: remove if exists
        if self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)
            logger.info(f"Removed stale job {job_id} from memory")

        try:
            # Use top-level wrapper function
            self._scheduler.add_job(
                _scheduled_task_wrapper,
                CronTrigger.from_crontab(task.cron_expr),
                id=job_id,
                args=[task.id],
                replace_existing=True,
                misfire_grace_time=3600
            )
            next_run = self.get_next_run_time(task.id)
            logger.info(f"✅ Successfully scheduled task {task.id} ('{task.name}'). Next run: {next_run}")
        except Exception as e:
            logger.error(f"❌ Failed to schedule task {task.id}: {e}", exc_info=True)

    async def run_task(self, task_id: int, is_manual: bool = False):
        """External entry point for manual triggering."""
        await _scheduled_task_wrapper(task_id, is_manual=is_manual)

    async def upsert_task(self, task: AgentScheduledTask):
        if not self._scheduler:
            logger.warning(f"⚠️ Scheduler not running, skipping upsert for task {task.id}")
            return

        if task.status == 1:
            await self._add_job_to_memory(task)
        else:
            job_id = f"task_{task.id}"
            if self._scheduler.get_job(job_id):
                self._scheduler.remove_job(job_id)

    def get_next_run_time(self, task_id: int) -> Optional[datetime]:
        if not self._scheduler:
            return None
        job = self._scheduler.get_job(f"task_{task_id}")
        return job.next_run_time if job else None

    async def reschedule_third_party_user_sync(self, config=None):
        """根据第三方用户同步配置注册/移除定时任务。"""
        if not self._scheduler:
            return

        from pytz import timezone
        from app.services.user_sync_service import UserSyncService

        tz = timezone("Asia/Shanghai")
        job_id = "system_third_party_user_sync"

        if self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)

        cfg = config or await UserSyncService.get_config()
        if not cfg.enabled or cfg.schedule == "off":
            logger.info("Third-party user sync scheduler: disabled")
            return

        cron_kwargs = UserSyncService.schedule_to_cron(cfg.schedule)
        if not cron_kwargs:
            return

        self._scheduler.add_job(
            _system_third_party_user_sync_job,
            CronTrigger(timezone=tz, **cron_kwargs),
            id=job_id,
            replace_existing=True,
        )
        logger.info("Third-party user sync scheduler registered: schedule=%s", cfg.schedule)

scheduler_service = TaskSchedulerService()