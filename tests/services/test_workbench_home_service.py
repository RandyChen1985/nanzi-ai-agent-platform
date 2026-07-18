from datetime import datetime
from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock


pytestmark = pytest.mark.no_infrastructure


def _payload(**overrides):
    from app.services.workbench_home_service import build_workbench_payload

    values = {
        "now": datetime(2026, 7, 18, 10, 30),
        "notifications": [],
        "task_items": [],
        "report_items": [],
        "conversation_items": [],
        "agent_items": [],
        "scenario_items": [],
        "source_status": {},
    }
    values.update(overrides)
    return build_workbench_payload(**values)


def test_active_mode_prioritizes_unread_attention():
    payload = _payload(
        notifications=[
            SimpleNamespace(
                id=1,
                title="库存巡检失败",
                notification_type="task_failed",
                created_at=datetime(2026, 7, 18, 9, 12),
                is_read=False,
                payload={"task_id": 17, "run_id": 3},
            )
        ]
    )

    assert payload["mode"] == "active"
    assert payload["attention"][0]["action"] == "open_task_log"
    assert payload["attention"][0]["target"] == {"task_id": 17, "run_id": 3}


def test_quiet_mode_keeps_resumable_work_without_zero_cards():
    payload = _payload(
        conversation_items=[
            {
                "id": "conversation:c1",
                "type": "conversation",
                "title": "费用趋势",
                "occurred_at": "2026-07-17T16:20:00",
                "action": "open_conversation",
                "target": {"conversation_id": "c1"},
            }
        ]
    )

    assert payload["mode"] == "quiet"
    assert payload["attention"] == []
    assert payload["resume_items"][0]["target"]["conversation_id"] == "c1"


def test_new_user_mode_uses_available_scenarios():
    payload = _payload(
        scenario_items=[
            {
                "id": "finance-expense-analysis",
                "name": "财务费用分析助手",
                "description": "查看费用和预算",
                "available": True,
            },
            {
                "id": "unavailable",
                "name": "不可用场景",
                "description": "缺少资源",
                "available": False,
            },
        ]
    )

    assert payload["mode"] == "new_user"
    assert [item["id"] for item in payload["recommended_scenarios"]] == [
        "finance-expense-analysis"
    ]


def test_items_are_deduplicated_sorted_and_capped():
    report_items = [
        {
            "id": f"run:{index}",
            "business_key": "report-run:shared" if index >= 6 else f"report-run:{index}",
            "type": "digest" if index == 0 else "report_run",
            "title": f"结果 {index}",
            "occurred_at": f"2026-07-18T09:{index:02d}:00",
            "action": "open_digest" if index == 0 else "open_report",
            "target": {"run_id": index},
        }
        for index in range(8)
    ]

    payload = _payload(report_items=report_items)

    assert len(payload["latest_results"]) == 6
    assert len(
        [item for item in payload["latest_results"] if item["business_key"] == "report-run:shared"]
    ) == 1
    assert payload["latest_results"][0]["occurred_at"] > payload["latest_results"][-1]["occurred_at"]


def test_source_status_is_completed_for_all_sources():
    payload = _payload(source_status={"notifications": "error", "tasks": "empty"})

    assert payload["source_status"] == {
        "notifications": "error",
        "tasks": "empty",
        "reports": "empty",
        "conversations": "empty",
        "agents": "empty",
        "scenarios": "empty",
    }


def test_saved_report_notification_uses_report_run_target():
    payload = _payload(
        notifications=[
            SimpleNamespace(
                id=9,
                title="报表运行失败：经营日报",
                category="saved_report",
                level="error",
                resource_type="saved_report_run",
                resource_id="31",
                meta_info={"report_id": "report-7"},
                read_at=None,
                created_at=datetime(2026, 7, 18, 9, 20),
            )
        ]
    )

    item = payload["attention"][0]
    assert item["action"] == "open_report"
    assert item["target"] == {"report_id": "report-7", "run_id": "31"}
    assert item["severity"] == "critical"


def test_scenario_recommendations_available_to_chat_users(monkeypatch):
    from app.services import workbench_home_service as svc

    class _Template:
        def model_dump(self):
            return {
                "id": "finance-expense-analysis",
                "name": "财务费用分析助手",
                "description": "查看费用",
                "category": "数据分析",
                "recommended": True,
            }

    monkeypatch.setattr(
        "app.services.scenario_template_service.ScenarioTemplateService.list_templates",
        lambda: [_Template()],
    )

    assert [
        item["id"]
        for item in svc._load_scenarios({"role": "user", "permissions": {"menus": ["menu:ai_chat"]}})
    ] == ["finance-expense-analysis"]
    assert svc._load_scenarios({"role": "user", "permissions": {"menus": []}}) == []
    assert svc._load_scenarios({"role": "admin"})


def test_next_scheduled_item_exposes_next_run_at():
    payload = _payload(
        task_items=[
            {
                "id": "task:1",
                "business_key": "task:1:status:1",
                "type": "scheduled_task",
                "title": "库存巡检",
                "subtitle": "定时任务",
                "occurred_at": "2026-07-18T08:00:00",
                "status": "active",
                "severity": "info",
                "action": "open_task",
                "target": {"task_id": 1},
                "needs_attention": False,
                "next_run_at": "2026-07-18T20:00:00",
            }
        ],
        conversation_items=[
            {
                "id": "conversation:c1",
                "type": "conversation",
                "title": "费用趋势",
                "occurred_at": "2026-07-17T16:20:00",
                "action": "open_conversation",
                "target": {"conversation_id": "c1"},
            }
        ],
    )

    assert payload["mode"] == "quiet"
    assert payload["next_scheduled_item"]["title"] == "库存巡检"
    assert payload["next_scheduled_item"]["next_run_at"] == "2026-07-18T20:00:00"
    assert payload["next_scheduled_item"]["action"] == "open_task"

def test_unactionable_notification_does_not_create_dead_attention_card():
    payload = _payload(
        notifications=[
            SimpleNamespace(
                id=11,
                title="品牌配置已更新",
                category="branding",
                level="info",
                resource_type=None,
                resource_id=None,
                meta_info={},
                read_at=None,
                created_at=datetime(2026, 7, 18, 9, 30),
            )
        ]
    )

    assert payload["attention"] == []
    assert payload["mode"] == "new_user"


@pytest.mark.asyncio
async def test_tasks_are_hidden_when_task_center_is_not_authorized():
    from app.services.workbench_home_service import _load_tasks

    items = await _load_tasks(
        AsyncMock(),
        7,
        {"role": "user", "permissions": {"menus": ["menu:ai_chat"]}},
    )

    assert items == []
