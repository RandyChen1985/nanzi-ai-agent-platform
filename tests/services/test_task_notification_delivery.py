import pytest
from unittest.mock import AsyncMock, patch

from app.services.task_notification_delivery import (
    build_task_notification_body,
    channels_missing_delivery,
    ensure_task_notification_deliveries,
    notification_tool_succeeded,
)


pytestmark = pytest.mark.no_infrastructure


def test_notification_tool_succeeded_detects_success_and_errors():
    assert notification_tool_succeeded(
        "send_portal_notification",
        "Successfully sent portal notification (id=1): 标题",
    )
    assert not notification_tool_succeeded(
        "send_dingtalk_message",
        "Error: DingTalk Webhook URL not configured.",
    )
    assert not notification_tool_succeeded("execute_sql_query", "rows: 3")


def test_channels_missing_delivery():
    delivered = {"send_portal_notification"}
    assert channels_missing_delivery(["portal", "dingtalk"], delivered) == ["dingtalk"]
    assert channels_missing_delivery(["portal"], delivered) == []


def test_build_task_notification_body_truncates_and_marks_fallback():
    long_text = "x" * 7000
    body = build_task_notification_body(long_text, fallback=True)
    assert "自动补发" in body
    assert len(body) <= 6100


@pytest.mark.asyncio
async def test_ensure_skips_when_agent_already_sent():
    with patch(
        "app.services.task_notification_delivery.load_delivered_notification_tools",
        new=AsyncMock(return_value={"send_portal_notification", "send_dingtalk_message"}),
    ):
        ok, notes = await ensure_task_notification_deliveries(
            AsyncMock(),
            user_id=1,
            task_name="动环巡检",
            channels=["portal", "dingtalk"],
            trace_id="trace-1",
            content="报告正文",
        )
    assert ok is True
    assert notes == ["all_channels_already_delivered_by_agent"]


@pytest.mark.asyncio
async def test_ensure_fallback_portal_and_dingtalk():
    db = AsyncMock()
    with patch(
        "app.services.task_notification_delivery.load_delivered_notification_tools",
        new=AsyncMock(return_value=set()),
    ), patch(
        "app.services.task_notification_delivery.PortalNotificationService.create",
        new=AsyncMock(),
    ) as portal_create, patch(
        "app.services.task_notification_delivery.NotificationService.send_dingtalk",
        new=AsyncMock(return_value=(True, "")),
    ) as send_dingtalk:
        ok, notes = await ensure_task_notification_deliveries(
            db,
            user_id=9,
            task_name="动环巡检",
            channels=["portal", "dingtalk"],
            trace_id="trace-2",
            content="巡检结论",
        )

    assert ok is True
    assert notes[0].startswith("fallback_delivered:")
    portal_create.assert_awaited_once()
    send_dingtalk.assert_awaited_once()
