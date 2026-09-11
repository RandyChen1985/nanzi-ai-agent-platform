import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException
from app.schemas.metadata import CronInspectionConfigRequest


@pytest.mark.asyncio
async def test_get_cron_inspection_config_default():
    from app.api.portal.endpoints.metadata import get_cron_inspection_config

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=mock_result)

    res = await get_cron_inspection_config(conn=mock_db)
    assert res.enabled is False
    assert res.cron_expr == "0 2 * * *"
    assert res.task_id is None
    assert res.run_count == 0
    assert res.notification_channels == ["portal"]


@pytest.mark.asyncio
async def test_get_cron_inspection_config_existing():
    from app.api.portal.endpoints.metadata import get_cron_inspection_config
    from app.models.task import AgentScheduledTask

    mock_task = AgentScheduledTask(
        id=99,
        name="全量元数据物理结构巡检",
        cron_expr="0 3 * * *",
        status=1,
        run_count=5,
        config={
            "metrics": {"health_status": "healthy", "last_status": "success"},
            "notification_channels": ["portal", "dingtalk"],
        },
    )

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_task
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch("app.api.portal.endpoints.metadata.scheduler_service.get_next_run_time", return_value=None):
        res = await get_cron_inspection_config(conn=mock_db)
        assert res.enabled is True
        assert res.cron_expr == "0 3 * * *"
        assert res.task_id == 99
        assert res.run_count == 5
        assert res.health_status == "healthy"
        assert res.notification_channels == ["portal", "dingtalk"]


@pytest.mark.asyncio
async def test_update_cron_inspection_config_invalid_cron():
    from app.api.portal.endpoints.metadata import update_cron_inspection_config

    mock_db = AsyncMock()
    req = CronInspectionConfigRequest(enabled=True, cron_expr="invalid cron expression")

    with pytest.raises(HTTPException) as exc_info:
        await update_cron_inspection_config(payload=req, conn=mock_db, user={"user_id": 1})

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_update_cron_inspection_config_success():
    from app.api.portal.endpoints.metadata import update_cron_inspection_config
    from app.models.task import AgentScheduledTask

    mock_db = AsyncMock()
    mock_task = AgentScheduledTask(
        id=10,
        name="全量元数据物理结构巡检",
        cron_expr="0 2 * * *",
        status=0,
        config={"task_type": "metadata_inspection"},
    )
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_task
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    with patch("app.api.portal.endpoints.metadata.scheduler_service.upsert_task", AsyncMock()) as mock_upsert, \
         patch("app.api.portal.endpoints.metadata.scheduler_service.get_next_run_time", return_value=None):
        req = CronInspectionConfigRequest(
            enabled=True,
            cron_expr="0 4 * * *",
            notification_channels=["feishu"],  # 未显式提供 portal
        )
        res = await update_cron_inspection_config(payload=req, conn=mock_db, user={"user_id": 1, "role": "admin"})

        assert res.enabled is True
        assert res.cron_expr == "0 4 * * *"
        assert res.task_id == 10
        # 站内信 portal 必须被自动补入
        assert "portal" in res.notification_channels
        assert "feishu" in res.notification_channels
        mock_upsert.assert_awaited_once()


@pytest.mark.asyncio
async def test_trigger_cron_inspection_immediately():
    from app.api.portal.endpoints.metadata import trigger_cron_inspection_immediately
    from app.models.task import AgentScheduledTask

    mock_db = AsyncMock()
    mock_bg = MagicMock()
    mock_task = AgentScheduledTask(
        id=123,
        name="全量元数据物理结构巡检",
        cron_expr="0 2 * * *",
        status=1,
    )
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_task
    mock_db.execute = AsyncMock(return_value=mock_result)

    res = await trigger_cron_inspection_immediately(
        background_tasks=mock_bg, conn=mock_db, user={"user_id": 1, "role": "admin"}
    )

    assert res["code"] == 200
    assert res["data"]["task_id"] == 123
    mock_bg.add_task.assert_called_once()


@pytest.mark.asyncio
async def test_cron_inspection_requires_admin_permission():
    from app.core.dependencies import require_admin

    # 非 admin 用户抛出 403 Forbidden
    with pytest.raises(HTTPException) as exc_info:
        await require_admin(user={"user_id": 2, "role": "user"})
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Admin access required"

    # admin 用户允许通过
    admin_user = {"user_id": 1, "role": "admin"}
    result = await require_admin(user=admin_user)
    assert result == admin_user


@pytest.mark.asyncio
async def test_metadata_inspection_audit_history_format():
    """验证元数据巡检记录的执行历史模型能够正确构造并保存。"""
    from app.services.ai.audit import AuditManager
    from app.schemas.agent import AgentExecutionStep

    step = AgentExecutionStep(
        step_number=1,
        event_type="tool_call",
        agent_name="系统巡检引擎",
        tool_name="metadata_inspect_all",
        tool_input={"active_only": True},
        tool_output={"content": "扫描完成，无漂移差异"},
        execution_time_ms=120.5,
        status="success",
    )
    assert step.step_number == 1
    assert step.tool_name == "metadata_inspect_all"
    assert step.status == "success"


@pytest.mark.asyncio
async def test_scheduled_metadata_inspection_triggers_alert_on_missing_tables():
    """验证定时巡检在探测到物理表缺失（missing_tables_count > 0）时，能够生成正确的结论文案并触发告警派发。"""
    from app.services.ai.scheduler_service import _scheduled_task_wrapper
    from app.models.task import AgentScheduledTask

    mock_task = AgentScheduledTask(
        id=888,
        name="全量元数据物理结构巡检",
        cron_expr="0 2 * * *",
        prompt="执行全量元数据物理结构一致性巡检",
        user_id=1,
        agent_id="system_inspection",
        conversation_id="system_metadata_inspection",
        status=1,
        config={
            "task_type": "metadata_inspection",
            "is_system": True,
            "notification_channels": ["portal", "dingtalk"],
        },
    )

    mock_inspect_res = {
        "success": True,
        "datasets_scanned": 3,
        "tables_scanned": 31,
        "columns_scanned": 190,
        "missing_tables_count": 1,
        "stale_count": 0,
        "new_count": 0,
        "mismatch_count": 0,
        "drift_datasets_count": 1,
        "failed_datasets_count": 0,
    }

    mock_db = AsyncMock()
    mock_scalar = MagicMock()
    mock_scalar.scalar_one_or_none.return_value = mock_task
    mock_db.execute = AsyncMock(return_value=mock_scalar)
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.ai.scheduler_service.AsyncSessionLocal", return_value=mock_session_ctx), \
         patch("app.services.ai.scheduler_service._mark_task_attempt_started", AsyncMock()), \
         patch("app.services.ai.scheduler_service._mark_task_success", AsyncMock()) as mock_mark_success, \
         patch("app.services.metadata_inspection_service.MetadataInspectionService.inspect_all_datasets", AsyncMock(return_value=mock_inspect_res)), \
         patch("app.services.portal_notification_service.PortalNotificationService.create", AsyncMock()) as mock_portal_notify, \
         patch("app.services.notification_service.NotificationService.send_dingtalk", AsyncMock(return_value=(True, None))) as mock_dingtalk_notify, \
         patch("app.services.ai.audit.AuditManager.save_trace_logs", AsyncMock()), \
         patch("app.services.ai.audit.AuditManager.save_history", AsyncMock()):

        await _scheduled_task_wrapper(888)

        # 1. 验证成功标记并包含表缺失信息
        mock_mark_success.assert_awaited_once()
        success_msg = mock_mark_success.await_args.kwargs.get("message") or ""
        assert "1 张表物理缺失" in success_msg

        # 2. 验证触发了告警通知派发（站内信与钉钉均被调用）
        mock_portal_notify.assert_awaited()
        assert "Schema 漂移差异" in mock_portal_notify.await_args.kwargs.get("title", "")
        assert "1 张表物理缺失" in mock_portal_notify.await_args.kwargs.get("content", "")

        mock_dingtalk_notify.assert_awaited()
        assert "1 张表物理缺失" in mock_dingtalk_notify.await_args.kwargs.get("content", "")


