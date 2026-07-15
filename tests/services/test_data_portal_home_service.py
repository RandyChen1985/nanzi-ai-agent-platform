from datetime import datetime
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.no_infrastructure


def test_build_home_payload_counts_today_and_orders_recent_activity():
    from app.services.data_portal_home_service import build_home_payload

    now = datetime(2026, 7, 15, 10, 0, 0)
    reports = [
        SimpleNamespace(
            id="rpt_a", title="每日机器负载", owner_user_id=7, owner_name="alice",
            last_run_at=datetime(2026, 7, 15, 9, 0, 0), last_error=None,
            is_favorite=True, pinned_at=datetime(2026, 7, 14, 9, 0, 0),
            user_last_run_at=datetime(2026, 7, 15, 9, 0, 0),
        ),
        SimpleNamespace(
            id="rpt_b", title="库存日报", owner_user_id=8, owner_name="bob",
            last_run_at=datetime(2026, 7, 15, 8, 30, 0), last_error="连接超时",
            is_favorite=False, pinned_at=None,
            user_last_run_at=datetime(2026, 7, 15, 8, 30, 0),
        ),
    ]
    runs = [
        SimpleNamespace(
            id=11, report_id="rpt_a", status="success", trigger_type="scheduled",
            started_at=datetime(2026, 7, 15, 9, 0, 0), finished_at=datetime(2026, 7, 15, 9, 0, 5),
            row_count=128, error_message=None,
        ),
        SimpleNamespace(
            id=12, report_id="rpt_b", status="failed", trigger_type="scheduled",
            started_at=datetime(2026, 7, 15, 8, 30, 0), finished_at=datetime(2026, 7, 15, 8, 30, 2),
            row_count=None, error_message="连接超时",
        ),
        SimpleNamespace(
            id=13, report_id="rpt_b", status="failed", trigger_type="manual",
            started_at=datetime(2026, 7, 14, 18, 0, 0), finished_at=datetime(2026, 7, 14, 18, 0, 2),
            row_count=None, error_message="昨日失败不计入今日",
        ),
    ]
    subscriptions = [
        SimpleNamespace(
            id=3, report_id="rpt_a", status="active", cron_expr="0 9 * * *",
            next_run_at=datetime(2026, 7, 16, 9, 0, 0), last_run_at=datetime(2026, 7, 15, 9, 0, 0),
        )
    ]
    deliveries = [
        SimpleNamespace(
            id=21, run_id=11, status="sent", channel="inbox", title="机器负载智能简报",
            created_at=datetime(2026, 7, 15, 9, 0, 6), ai_status="success",
        ),
        SimpleNamespace(
            id=22, run_id=11, status="sent", channel="dingtalk", title="机器负载智能简报",
            created_at=datetime(2026, 7, 15, 9, 0, 7), ai_status="success",
        ),
    ]

    payload = build_home_payload(
        user_id=7,
        reports=reports,
        runs=runs,
        subscriptions=subscriptions,
        deliveries=deliveries,
        now=now,
    )

    assert payload["attention"]["failed_runs_today"] == 1
    assert payload["attention"]["digests_today"] == 1
    assert payload["attention"]["active_subscriptions"] == 1
    assert payload["attention"]["completed_subscriptions_today"] == 1
    assert payload["recent_analysis"][0]["type"] == "digest"
    assert payload["recent_analysis"][0]["report_id"] == "rpt_a"
    assert sum(item["type"] == "digest" for item in payload["recent_analysis"]) == 1
    assert not any(item["type"] == "report_run" and item["run_id"] == 11 for item in payload["recent_analysis"])
    assert payload["report_summary"]["favorite"] == 1
    assert payload["report_summary"]["pinned"] == 1
    assert payload["report_summary"]["shared"] == 1
    assert payload["report_summary"]["items"][0]["subscription_status"] == "active"


def test_build_home_payload_does_not_invent_attention_when_empty():
    from app.services.data_portal_home_service import build_home_payload

    payload = build_home_payload(
        user_id=7,
        reports=[],
        runs=[],
        subscriptions=[],
        deliveries=[],
        now=datetime(2026, 7, 15, 10, 0, 0),
    )

    assert payload["attention"] == {
        "failed_runs_today": 0,
        "latest_failed_run": None,
        "digests_today": 0,
        "latest_digest_at": None,
        "active_subscriptions": 0,
        "completed_subscriptions_today": 0,
    }
    assert payload["recent_analysis"] == []
    assert payload["report_summary"]["items"] == []


def test_build_home_payload_includes_latest_chatbi_conversation_once():
    from app.services.data_portal_home_service import build_home_payload

    conversations = [
        SimpleNamespace(id=31, conversation_id="conv-1", query="查询最近六个月用报表", summary="返回 12 行", status="success", created_at=datetime(2026, 7, 15, 9, 20, 0), agent_display_name="黄金报表"),
        SimpleNamespace(id=30, conversation_id="conv-1", query="查询用报表", summary="返回 10 行", status="success", created_at=datetime(2026, 7, 15, 9, 0, 0), agent_display_name="黄金报表"),
    ]
    payload = build_home_payload(user_id=7, reports=[], runs=[], subscriptions=[], deliveries=[], conversations=conversations, now=datetime(2026, 7, 15, 10, 0, 0))

    assert payload["recent_analysis"] == [{
        "type": "conversation", "id": 31, "conversation_id": "conv-1", "title": "查询最近六个月用报表",
        "subtitle": "ChatBI · 黄金报表", "status": "success", "occurred_at": "2026-07-15T09:20:00", "action": "open_conversation",
    }]
