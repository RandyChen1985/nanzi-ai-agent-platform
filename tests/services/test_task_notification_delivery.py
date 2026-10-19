import pytest
from unittest.mock import AsyncMock, patch

from app.services.task_notification_delivery import (
    assess_delivery_completeness,
    build_task_notification_body,
    channels_missing_delivery,
    compose_scheduler_notification_content,
    ensure_task_notification_deliveries,
    extract_tabular_payload,
    is_provisional_assistant_text,
    notification_tool_succeeded,
    strip_thinking_from_notification_content,
    tabular_payload_to_markdown,
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


def test_build_task_notification_body_truncates_without_meta_prefix():
    """正文不再拼「📨 以下为 TaskCenter 统一投递…」，来源标识由通知标题承载。"""
    long_text = "x" * 7000
    body = build_task_notification_body(long_text)
    assert "TaskCenter" not in body
    assert "统一投递" not in body
    assert not body.startswith("📨")
    assert "自动补发" not in body
    assert len(body) <= 6100
    assert body.endswith("…（内容已截断）")


def test_strip_thinking_blocks_from_notification_content():
    raw = (
        "前置说明。\n"
        "<think>这里是长思考链，不应推送</think>\n"
        "<thought>SQL 计划推演</thought>\n"
        "最终结论：订单量环比上升。"
    )
    cleaned = strip_thinking_from_notification_content(raw)
    assert "<think>" not in cleaned
    assert "<thought>" not in cleaned
    assert "长思考链" not in cleaned
    assert "SQL 计划推演" not in cleaned
    assert "最终结论：订单量环比上升。" in cleaned

    body = build_task_notification_body(raw)
    assert "长思考链" not in body
    assert "最终结论：订单量环比上升。" in body
    assert "统一投递" not in body


def test_strip_embedchat_reasoning_panel_leak_from_notification():
    """对齐 EmbedChat reasoningContent：模型思考折叠面板内容不得进入推送。"""
    reasoning = "先看订单表结构，再按天聚合 GMV，注意过滤退款单。"
    answer = f"{reasoning}\n\n本周订单 GMV 环比上升 12%。"
    cleaned = strip_thinking_from_notification_content(
        answer,
        reasoning_content=reasoning,
    )
    assert "先看订单表结构" not in cleaned
    assert "本周订单 GMV 环比上升 12%。" in cleaned

    body = build_task_notification_body(
        answer,
        reasoning_content=reasoning,
    )
    assert "先看订单表结构" not in body
    assert "本周订单 GMV 环比上升 12%。" in body
    assert "统一投递" not in body


def test_compose_strips_think_without_appending_sql():
    content = compose_scheduler_notification_content(
        "正常话语。<think>内部推理</think>订单分析完成。",
        [
            {
                "columns": ["day", "orders"],
                "rows": [["2026-08-01", 12]],
                "row_count": 1,
            }
        ],
    )
    assert "内部推理" not in content
    assert "订单分析完成。" in content
    assert "| day | orders |" not in content


def test_extract_and_render_sql_payload():
    payload = extract_tabular_payload(
        {
            "columns": [{"name": "day"}, {"name": "orders"}],
            "items": [["2026-08-01", 12], ["2026-08-02", 8]],
            "row_count": 2,
        }
    )
    assert payload is not None
    assert payload["row_count"] == 2
    md = tabular_payload_to_markdown(payload)
    assert "| day | orders |" in md
    assert "12" in md


def test_compose_excludes_sql_table_from_notification():
    content = compose_scheduler_notification_content(
        "查询成功。让我再补充按下单时间维度的汇总分析。",
        [
            {
                "columns": ["day", "orders"],
                "rows": [["2026-08-01", 12]],
                "row_count": 1,
            }
        ],
    )
    assert "查询成功" in content
    assert "### 查询结果" not in content
    assert "| day | orders |" not in content


def test_completeness_rejects_provisional_without_sql():
    ok, reason = assess_delivery_completeness(
        "查询成功。让我再补充按下单时间维度的汇总分析。",
        has_sql_data=False,
        had_sql_tool=True,
        assistant_content="查询成功。让我再补充按下单时间维度的汇总分析。",
    )
    assert ok is False
    assert reason == "sql_without_usable_result"
    assert is_provisional_assistant_text("查询成功。让我再补充按下单时间维度的汇总分析。")


def test_completeness_rejects_provisional_when_sql_succeeded():
    composed = compose_scheduler_notification_content(
        "查询成功。让我再补充分析。",
        [{"columns": ["a"], "rows": [[1]], "row_count": 1}],
    )
    ok, reason = assess_delivery_completeness(
        composed,
        has_sql_data=True,
        had_sql_tool=True,
        assistant_content="查询成功。让我再补充分析。",
    )
    assert ok is False
    assert reason == "provisional"


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
async def test_ensure_scheduler_delivers_portal_and_dingtalk():
    db = AsyncMock()
    with patch(
        "app.services.task_notification_delivery.load_delivered_notification_tools",
        new=AsyncMock(return_value=set()),
    ), patch(
        "app.services.task_notification_delivery.load_sql_tool_artifacts",
        new=AsyncMock(return_value=(False, [])),
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
            content="巡检结论完整，设备均正常，无需人工介入处理。",
        )

    assert ok is True
    assert notes[0].startswith("scheduler_delivered:")
    portal_create.assert_awaited_once()
    send_dingtalk.assert_awaited_once()
    body = portal_create.await_args.kwargs["content"]
    assert "统一投递" not in body
    assert "TaskCenter" not in body
    assert not body.startswith("📨")
    assert "巡检结论完整" in body


@pytest.mark.asyncio
async def test_ensure_rejects_incomplete_provisional_content():
    with patch(
        "app.services.task_notification_delivery.load_delivered_notification_tools",
        new=AsyncMock(return_value=set()),
    ), patch(
        "app.services.task_notification_delivery.load_sql_tool_artifacts",
        new=AsyncMock(return_value=(True, [])),
    ), patch(
        "app.services.task_notification_delivery.PortalNotificationService.create",
        new=AsyncMock(),
    ) as portal_create:
        ok, notes = await ensure_task_notification_deliveries(
            AsyncMock(),
            user_id=1,
            task_name="数据查询测试推送",
            channels=["portal"],
            trace_id="trace-3",
            content="查询成功。让我再补充按下单时间维度的汇总分析。",
        )

    assert ok is False
    assert notes == ["incomplete_content:sql_without_usable_result"]
    portal_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_does_not_include_sql_details():
    db = AsyncMock()
    sql_payload = {
        "columns": ["day", "orders"],
        "rows": [["2026-08-01", 12]],
        "row_count": 1,
    }
    with patch(
        "app.services.task_notification_delivery.load_delivered_notification_tools",
        new=AsyncMock(return_value=set()),
    ), patch(
        "app.services.task_notification_delivery.load_sql_tool_artifacts",
        new=AsyncMock(return_value=(True, [sql_payload])),
    ), patch(
        "app.services.task_notification_delivery.PortalNotificationService.create",
        new=AsyncMock(),
    ) as portal_create:
        ok, notes = await ensure_task_notification_deliveries(
            db,
            user_id=1,
            task_name="数据查询测试推送",
            channels=["portal"],
            trace_id="trace-4",
            content="订单趋势分析完成，本期订单量保持稳定，无需额外处置。",
        )

    assert ok is True
    assert any(n.startswith("scheduler_delivered:") for n in notes)
    assert not any(n.startswith("enriched_sql_sections:") for n in notes)
    body = portal_create.await_args.kwargs["content"]
    assert "| day | orders |" not in body
    assert "12" not in body
