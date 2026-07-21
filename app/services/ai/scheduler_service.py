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
from app.models.saved_report import (
    PortalSavedReport,
    PortalSavedReportDigestDelivery,
    PortalSavedReportRun,
    PortalSavedReportSubscription,
)

logger = logging.getLogger(__name__)

BUSY_CONVERSATION_MESSAGE = "当前会话正在处理中"
NO_TOOL_EXECUTION_MESSAGE = "自动任务未实际调用任何工具"
TASK_RUN_CONVERSATION_SUFFIX_LEN = len("_run_") + 12
MAX_CONVERSATION_ID_LEN = 50
INCOMPLETE_TASK_STATUSES = frozenset({"awaiting_permission", "awaiting_external_execution"})
TASK_METRICS_KEY = "task_metrics"
TASK_METRIC_DEFAULTS = {
    "trigger_count": 0,
    "success_count": 0,
    "failure_count": 0,
    "skipped_count": 0,
    "consecutive_failures": 0,
    "health_status": "unknown",
    "last_status": None,
    "last_message": None,
    "last_error": None,
    "last_trace_id": None,
    "last_started_at": None,
    "last_finished_at": None,
    "last_alert_at": None,
}


def _task_permission_options() -> Dict[str, Any]:
    return {"approval_mode": "allow"}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _task_config(task: AgentScheduledTask) -> Dict[str, Any]:
    return dict(task.config or {})


def _normalize_task_metrics(config: Dict[str, Any]) -> Dict[str, Any]:
    raw = config.get(TASK_METRICS_KEY)
    metrics = dict(raw) if isinstance(raw, dict) else {}
    normalized = {**TASK_METRIC_DEFAULTS, **metrics}
    for key in ("trigger_count", "success_count", "failure_count", "skipped_count", "consecutive_failures"):
        try:
            normalized[key] = int(normalized.get(key) or 0)
        except (TypeError, ValueError):
            normalized[key] = 0
    return normalized


def _task_metrics(task: AgentScheduledTask) -> Dict[str, Any]:
    return _normalize_task_metrics(_task_config(task))


async def _write_task_metrics(
    session: AsyncSession,
    task: AgentScheduledTask,
    metrics: Dict[str, Any],
) -> None:
    config = _task_config(task)
    config[TASK_METRICS_KEY] = metrics
    await session.execute(
        update(AgentScheduledTask)
        .where(AgentScheduledTask.id == task.id)
        .values(config=config)
    )
    await session.commit()
    task.config = config


async def _mark_task_attempt_started(session: AsyncSession, task: AgentScheduledTask) -> Dict[str, Any]:
    metrics = _task_metrics(task)
    metrics["trigger_count"] += 1
    metrics["last_status"] = "running"
    metrics["last_message"] = "任务已触发，正在执行"
    metrics["last_error"] = None
    metrics["last_started_at"] = _now_iso()
    await _write_task_metrics(session, task, metrics)
    return metrics


async def _mark_task_success(
    session: AsyncSession,
    task: AgentScheduledTask,
    *,
    trace_id: Optional[str],
    message: str,
) -> Dict[str, Any]:
    metrics = _task_metrics(task)
    metrics["success_count"] += 1
    metrics["consecutive_failures"] = 0
    metrics["health_status"] = "healthy"
    metrics["last_status"] = "success"
    metrics["last_message"] = message
    metrics["last_error"] = None
    metrics["last_trace_id"] = trace_id
    metrics["last_finished_at"] = _now_iso()
    await _write_task_metrics(session, task, metrics)
    return metrics


async def _mark_task_failure(
    session: AsyncSession,
    task: AgentScheduledTask,
    *,
    trace_id: Optional[str],
    error: str,
) -> Dict[str, Any]:
    metrics = _task_metrics(task)
    metrics["failure_count"] += 1
    metrics["consecutive_failures"] += 1
    metrics["health_status"] = "error" if metrics["consecutive_failures"] >= 3 else "warning"
    metrics["last_status"] = "failed"
    metrics["last_message"] = "任务执行失败"
    metrics["last_error"] = error
    metrics["last_trace_id"] = trace_id
    metrics["last_finished_at"] = _now_iso()
    await _write_task_metrics(session, task, metrics)
    return metrics


async def _mark_task_skipped(
    session: AsyncSession,
    task: AgentScheduledTask,
    *,
    reason: str,
) -> Dict[str, Any]:
    metrics = _task_metrics(task)
    metrics["skipped_count"] += 1
    metrics["health_status"] = "skipped"
    metrics["last_status"] = "skipped"
    metrics["last_message"] = reason
    metrics["last_finished_at"] = _now_iso()
    await _write_task_metrics(session, task, metrics)
    return metrics


def _task_run_conversation_prefix(task_conversation_id: str) -> str:
    base = (task_conversation_id or f"task_conv_{uuid.uuid4().hex[:12]}").strip()
    max_base_len = MAX_CONVERSATION_ID_LEN - TASK_RUN_CONVERSATION_SUFFIX_LEN
    return base[:max_base_len]


def _new_task_run_conversation_id(task_conversation_id: str) -> str:
    return f"{_task_run_conversation_prefix(task_conversation_id)}_run_{uuid.uuid4().hex[:12]}"


def _build_scheduled_task_prompt(
    task_id: int,
    agent_display_name: str,
    prompt: str,
    notification_channels: Optional[List[str]] = None,
) -> str:
    from app.services.task_notification_channels import build_notification_delivery_supplement

    body = (
        f"【自动化指令-任务ID: {task_id}】@{agent_display_name}\n"
        "这是 TaskCenter 自动任务的本次独立触发。请立即实际执行任务，不要只回复计划、准备开始或执行思路。\n"
        "如果任务需要获取系统状态、调用工具、发送通知或写入外部系统，必须调用对应工具完成；"
        "只有工具调用完成后，才能总结执行结果。\n"
        f"任务内容：{prompt}"
    )
    supplement = build_notification_delivery_supplement(notification_channels or [])
    if supplement:
        body = f"{body}\n\n{supplement}"
    return body


def _is_busy_task_result(result: Dict[str, Any]) -> bool:
    status = str((result or {}).get("status") or "").lower()
    content = str((result or {}).get("content") or "")
    return status == "error" and BUSY_CONVERSATION_MESSAGE in content


def _is_no_tool_execution_result(result: Dict[str, Any]) -> bool:
    status = str((result or {}).get("status") or "").lower()
    content = str((result or {}).get("content") or "")
    return status == "error" and NO_TOOL_EXECUTION_MESSAGE in content


def _is_incomplete_task_result(result: Dict[str, Any]) -> bool:
    status = str((result or {}).get("status") or "").lower()
    return (
        status in INCOMPLETE_TASK_STATUSES
        or _is_busy_task_result(result)
        or _is_no_tool_execution_result(result)
    )


def _task_result_error(result: Dict[str, Any]) -> str:
    status = str((result or {}).get("status") or "error")
    content = str((result or {}).get("content") or "").strip()
    if _is_busy_task_result(result):
        return "当前会话正在处理中，本次触发已跳过"
    if _is_no_tool_execution_result(result):
        return "自动任务未实际调用任何工具"
    if status in INCOMPLETE_TASK_STATUSES:
        return f"任务未完成，当前状态：{status}"
    return content[:500] or f"任务执行失败，状态：{status}"


def _should_alert_failure(metrics: Dict[str, Any]) -> bool:
    consecutive = int(metrics.get("consecutive_failures") or 0)
    return consecutive == 1 or consecutive % 3 == 0


async def _send_task_failure_alert(
    user_id: int,
    task: AgentScheduledTask,
    *,
    trace_id: Optional[str],
    error: str,
    metrics: Dict[str, Any],
) -> None:
    try:
        from app.services.notification_service import NotificationService
        import time
        import hmac
        import hashlib
        import base64
        import urllib.parse
        import httpx

        async with AsyncSessionLocal() as db:
            record = await NotificationService.get_config_by_type_raw(db, user_id, "dingtalk")
            if not record or not record.config_json:
                return
            cfg = json.loads(record.config_json)
            if not cfg.get("is_enabled") or not cfg.get("webhook_url"):
                return

            webhook_url = cfg.get("webhook_url")
            secret = cfg.get("secret")
            target_url = webhook_url
            if secret:
                timestamp = str(round(time.time() * 1000))
                string_to_sign = f"{timestamp}\n{secret}"
                hmac_code = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
                sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
                target_url = f"{webhook_url}&timestamp={timestamp}&sign={sign}"

            title = f"TaskCenter 任务失败：{task.name}"
            content = (
                f"### {title}\n\n"
                f"- 任务ID：{task.id}\n"
                f"- 任务名称：{task.name}\n"
                f"- Trace：{trace_id or '-'}\n"
                f"- 连续失败：{metrics.get('consecutive_failures', 0)} 次\n"
                f"- 错误原因：{error}\n"
                f"- 时间：{_now_iso()}"
            )
            payload = {"msgtype": "markdown", "markdown": {"title": title, "text": content}}
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(target_url, json=payload)
                data = resp.json()
                if data.get("errcode") != 0:
                    logger.warning("Task failure alert failed for task %s: %s", task.id, data)
                    return

        metrics["last_alert_at"] = _now_iso()
        async with AsyncSessionLocal() as db:
            latest = (await db.execute(select(AgentScheduledTask).where(AgentScheduledTask.id == task.id))).scalar_one_or_none()
            if latest:
                await _write_task_metrics(db, latest, metrics)
    except Exception as alert_err:
        logger.warning("Task failure alert failed for task %s: %s", task.id, alert_err, exc_info=True)


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
            async with AsyncSessionLocal() as session:
                task = (await session.execute(select(AgentScheduledTask).where(AgentScheduledTask.id == task_id))).scalar_one_or_none()
                if task:
                    await _mark_task_skipped(session, task, reason="同一分钟内已有节点正在执行，本次触发已跳过")
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
            metrics = await _mark_task_failure(
                session,
                task,
                trace_id=None,
                error=f"任务用户不存在：{task.user_id}",
            )
            if _should_alert_failure(metrics):
                await _send_task_failure_alert(task.user_id, task, trace_id=None, error=f"任务用户不存在：{task.user_id}", metrics=metrics)
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
            "task_name": task.name,
            "requires_tool_execution": True,
        }

        # 4. Execute via Agent Service
        try:
            await _mark_task_attempt_started(session, task)

            # Add structured prefix with @AgentName for forced routing.
            from app.services.task_notification_channels import channels_from_task_config

            full_prompt = _build_scheduled_task_prompt(
                task_id,
                agent_display_name,
                task.prompt,
                notification_channels=channels_from_task_config(_task_config(task)),
            )
            run_conversation_id = _new_task_run_conversation_id(task.conversation_id)

            logger.info(
                "🚀 Executing task %s ('%s') | Agent: %s | TaskConvID: %s | RunConvID: %s",
                task_id,
                task.name,
                task.agent_id,
                task.conversation_id,
                run_conversation_id,
            )
            
            # NOTE: We don't generate trace_id here, we let agent_service generate it 
            # and capture it from the response to ensure consistency with Audit Logs.
            result = await agent_service.chat_completion(
                messages=[{"role": "user", "content": full_prompt}],
                agent_id=task.agent_id,
                conversation_id=run_conversation_id,
                user_info=user_info,
                enable_multi_agent=True,
                permission_options=_task_permission_options(),
            )
            
            trace_id = result.get('trace_id')
            content_preview = result.get('content', '')[:100]
            logger.info(f"✅ Task {task_id} finished. Trace: {trace_id}. Response: {content_preview}...")

            if _is_incomplete_task_result(result):
                error = _task_result_error(result)
                logger.warning(
                    "⏸️ Task %s skipped run metadata update because execution did not complete. status=%s trace=%s error=%s",
                    task_id,
                    result.get("status"),
                    trace_id,
                    error,
                )
                if _is_busy_task_result(result):
                    await _mark_task_skipped(session, task, reason=error)
                else:
                    metrics = await _mark_task_failure(session, task, trace_id=trace_id, error=error)
                    if _should_alert_failure(metrics):
                        await _send_task_failure_alert(task.user_id, task, trace_id=trace_id, error=error, metrics=metrics)
                return

            notification_channels = channels_from_task_config(_task_config(task))
            if notification_channels:
                from app.services.task_notification_delivery import ensure_task_notification_deliveries

                await asyncio.sleep(0.5)
                delivery_ok, delivery_notes = await ensure_task_notification_deliveries(
                    session,
                    user_id=task.user_id,
                    task_name=task.name,
                    channels=notification_channels,
                    trace_id=trace_id,
                    content=str(result.get("content") or ""),
                )
                logger.info(
                    "📬 Task %s notification delivery trace=%s ok=%s notes=%s",
                    task_id,
                    trace_id,
                    delivery_ok,
                    delivery_notes,
                )
                if not delivery_ok:
                    error = (
                        "任务已生成结果，但结果通知未全部送达："
                        + "; ".join(delivery_notes)
                    )
                    metrics = await _mark_task_failure(session, task, trace_id=trace_id, error=error)
                    if _should_alert_failure(metrics):
                        await _send_task_failure_alert(
                            task.user_id, task, trace_id=trace_id, error=error, metrics=metrics
                        )
                    return
            
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
            await _mark_task_success(
                session,
                task,
                trace_id=trace_id,
                message="任务执行成功",
            )
            logger.info(f"📊 Updated run_count and last_run_id for task {task_id}")
            
            # Allow logs to flush
            await asyncio.sleep(0.5)
            
        except Exception as e:
            logger.error(f"❌ Task {task_id} execution failed: {e}", exc_info=True)
            metrics = await _mark_task_failure(session, task, trace_id=None, error=str(e))
            if _should_alert_failure(metrics):
                await _send_task_failure_alert(task.user_id, task, trace_id=None, error=str(e), metrics=metrics)


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


async def _system_knowledge_metrics_sync_job():
    """
    系统级定时任务：每天凌晨 3:30 运行知识库及文档被引用和检索指标同步落库。
    """
    logger.info("⏰ Starting system knowledge metrics sync job...")
    
    lock_key = f"lock:system_knowledge_metrics_sync:{datetime.now().strftime('%Y%m%d%H%M')}"
    try:
        if not await redis.redis_client.set(lock_key, "locked", ex=300, nx=True):
            logger.warning("⏩ System knowledge metrics sync skipped: lock already acquired by another node.")
            return
    except Exception as lock_err:
        logger.warning(f"Failed to acquire redis lock: {lock_err}")
        
    try:
        from app.services.knowledge_metrics_service import KnowledgeMetricsService
        await KnowledgeMetricsService.sync_redis_metrics_to_db()
        logger.info("✅ System knowledge metrics sync job finished successfully.")
    except Exception as e:
        logger.error(f"❌ Failed to run system knowledge metrics sync job: {e}", exc_info=True)


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


async def _saved_report_subscription_wrapper(subscription_id: int, is_manual: bool = False):
    from app.api.portal.endpoints.saved_reports import ExecuteReportRequest, _execute_saved_report_impl
    from app.services.portal_notification_service import PortalNotificationService
    from app.services.notification_service import NotificationService
    from app.services.saved_report_digest_service import (
        build_deterministic_digest,
        enrich_digest_with_ai,
        render_mobile_markdown,
    )
    trigger_label = "手动触发" if is_manual else "定时触发"

    async def send_external(db, subscription, user_id, title, content, *, failure: bool):
        if failure and not (subscription.consecutive_failures == 1 or subscription.consecutive_failures % 3 == 0):
            return
        senders = {
            "dingtalk": NotificationService.send_dingtalk,
            "wechat_work": NotificationService.send_wechat_work,
            "email": NotificationService.send_email,
        }
        for channel in subscription.external_channels or []:
            sender = senders.get(channel)
            if sender:
                ok, error = await sender(db, user_id, title, content)
                if not ok:
                    logger.warning("Saved report external notification failed: channel=%s error=%s", channel, error)

    async with AsyncSessionLocal() as db:
        subscription = (await db.execute(select(PortalSavedReportSubscription).where(
            PortalSavedReportSubscription.id == subscription_id
        ))).scalar_one_or_none()
        if subscription is None or (subscription.status != "active" and not is_manual):
            return
        report = (await db.execute(select(PortalSavedReport).where(
            PortalSavedReport.id == subscription.report_id
        ))).scalar_one_or_none()
        user = (await db.execute(select(User).where(User.id == subscription.user_id))).scalar_one_or_none()
        if report is None or user is None or user.status != 1 or int(report.owner_user_id) != int(subscription.user_id):
            subscription.status = "error"
            subscription.last_error = "报表所有权或订阅用户状态已失效"
            await db.commit()
            return

        user_info = {
            "user_id": str(user.id), "user_name": user.user_name, "real_name": user.real_name,
            "role": user.role, "dept_code": user.dept_code, "org_path": user.org_path,
            "extra_data": user.extra_data,
        }
        try:
            await _execute_saved_report_impl(
                report.id, ExecuteReportRequest(params=subscription.params or {}), None, user_info, db,
                trigger_type="scheduled", task_id=subscription.id,
            )
            run = (await db.execute(select(PortalSavedReportRun).where(
                PortalSavedReportRun.report_id == report.id,
                PortalSavedReportRun.task_id == subscription.id,
            ).order_by(PortalSavedReportRun.id.desc()).limit(1))).scalar_one_or_none()
            subscription.last_run_id = run.id if run else None
            subscription.last_run_at = datetime.now()
            subscription.consecutive_failures = 0
            subscription.last_error = None
            from app.services.saved_report_subscription_service import evaluate_alert_condition
            alert_evaluation = evaluate_alert_condition(
                getattr(subscription, "alert_condition", None),
                run.result_snapshot if run else None,
                getattr(subscription, "alert_state", None),
            )
            subscription.alert_state = alert_evaluation.next_state
            if subscription.notify_on_success and alert_evaluation.hit:
                try:
                    async with db.begin_nested():
                        digest = build_deterministic_digest(report, run, subscription.params or {})
                        digest = await enrich_digest_with_ai(
                            digest,
                            enabled=bool(getattr(subscription, "ai_analysis_enabled", True)),
                            analysis_instruction=getattr(subscription, "analysis_instruction", None),
                        )
                        public_base = str(settings.APP_PUBLIC_URL or "").rstrip("/")
                        report_url = (
                            f"{public_base}/dashboard/chat?dataset_portal=1&report_id={report.id}&run_id={run.id}"
                            if public_base and run else ""
                        )
                        title, content = render_mobile_markdown(digest, report_url, "inbox")
                        await PortalNotificationService.create(
                            db, user_id=user.id, title=title, content=content, level="success",
                            resource_type="saved_report_run", resource_id=str(run.id) if run else None,
                            metadata={
                                "report_id": report.id,
                                "report_title": report.title,
                                "subscription_id": subscription.id,
                                "digest_mode": digest.get("generation_mode"),
                                "trigger_evidence": alert_evaluation.evidence,
                            },
                        )
                        if run:
                            db.add(PortalSavedReportDigestDelivery(
                                run_id=run.id, subscription_id=subscription.id, channel="inbox",
                                digest_payload=digest, trigger_evidence=alert_evaluation.evidence,
                                title=title, content=content, status="success",
                                ai_status=digest.get("ai_status") or "disabled", sent_at=datetime.now(),
                            ))
                        senders = {
                            "dingtalk": NotificationService.send_dingtalk,
                            "wechat_work": NotificationService.send_wechat_work,
                            "email": NotificationService.send_email,
                        }
                        for channel in subscription.external_channels or []:
                            sender = senders.get(channel)
                            if sender is None:
                                continue
                            channel_title, channel_content = render_mobile_markdown(digest, report_url, channel)
                            ok, error = await sender(db, user.id, channel_title, channel_content)
                            if run:
                                db.add(PortalSavedReportDigestDelivery(
                                    run_id=run.id, subscription_id=subscription.id, channel=channel,
                                    digest_payload=digest, trigger_evidence=alert_evaluation.evidence,
                                    title=channel_title, content=channel_content,
                                    status="success" if ok else "failed",
                                    error_message=None if ok else error,
                                    ai_status=digest.get("ai_status") or "disabled",
                                    sent_at=datetime.now(),
                                ))
                            if not ok:
                                logger.warning("Saved report digest delivery failed: channel=%s error=%s", channel, error)
                except Exception as digest_error:
                    logger.exception("Saved report digest generation or audit failed: %s", type(digest_error).__name__)
            await db.commit()
        except Exception as exc:
            subscription.last_run_at = datetime.now()
            subscription.consecutive_failures = int(subscription.consecutive_failures or 0) + 1
            subscription.last_error = str(getattr(exc, "detail", exc))[:10000]
            run = (await db.execute(select(PortalSavedReportRun).where(
                PortalSavedReportRun.report_id == report.id,
                PortalSavedReportRun.task_id == subscription.id,
            ).order_by(PortalSavedReportRun.id.desc()).limit(1))).scalar_one_or_none()
            subscription.last_run_id = run.id if run else None
            title = f"报表运行失败：{report.title}"
            failure_content = f"触发方式：{trigger_label}\n{subscription.last_error}"
            await PortalNotificationService.create(
                db, user_id=user.id, title=title,
                content=failure_content, level="error", resource_type="saved_report_run",
                resource_id=str(run.id) if run else None,
                metadata={"report_id": report.id, "report_title": report.title, "subscription_id": subscription.id},
            )
            if subscription.notify_on_failure:
                await send_external(db, subscription, user.id, title, failure_content, failure=True)
            await db.commit()


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

        # 注册知识库运营数据归并同步任务，每日凌晨 3:30 运行
        self._scheduler.add_job(
            _system_knowledge_metrics_sync_job,
            CronTrigger(hour=3, minute=30, timezone=tz),
            id="system_knowledge_metrics_sync",
            replace_existing=True
        )

        await self.reschedule_third_party_user_sync()

        now = datetime.now(tz)
        logger.info(f"🚀 Agent Task Scheduler started (Fixed Serialization). Current Scheduler Time: {now}")
        await self.reload_tasks()
        await self.reload_saved_report_subscriptions()

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

    async def reload_saved_report_subscriptions(self):
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(PortalSavedReportSubscription).where(
                PortalSavedReportSubscription.status == "active"
            ))
            subscriptions = result.scalars().all()
            for subscription in subscriptions:
                await self.upsert_saved_report_subscription(subscription)
        logger.info("Loaded %s active saved report subscriptions into scheduler.", len(subscriptions))

    async def upsert_saved_report_subscription(self, subscription: PortalSavedReportSubscription):
        if not self._scheduler:
            return
        job_id = f"saved_report_subscription_{subscription.id}"
        if self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)
        if subscription.status == "active":
            self._scheduler.add_job(
                _saved_report_subscription_wrapper,
                CronTrigger.from_crontab(subscription.cron_expr, timezone=subscription.timezone or "Asia/Shanghai"),
                id=job_id, args=[subscription.id], replace_existing=True, misfire_grace_time=3600,
            )

    async def remove_saved_report_subscription(self, subscription_id: int):
        if not self._scheduler:
            return
        job_id = f"saved_report_subscription_{subscription_id}"
        if self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)

    def get_saved_report_subscription_next_run_time(self, subscription_id: int) -> Optional[datetime]:
        if not self._scheduler:
            return None
        job = self._scheduler.get_job(f"saved_report_subscription_{subscription_id}")
        return job.next_run_time if job else None

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
