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
        "running_items": [],
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


def test_resume_items_are_deduplicated_by_conversation_target():
    payload = _payload(
        conversation_items=[
            {
                "id": "turn-old",
                "business_key": "conversation:turn-old",
                "type": "conversation",
                "title": "费用趋势",
                "occurred_at": "2026-07-18T09:00:00",
                "action": "open_conversation",
                "target": {"conversation_id": "c1"},
            },
            {
                "id": "turn-new",
                "business_key": "conversation:turn-new",
                "type": "conversation",
                "title": "费用趋势（继续）",
                "occurred_at": "2026-07-18T10:00:00",
                "action": "open_conversation",
                "target": {"conversation_id": "c1"},
            },
        ]
    )

    assert len(payload["resume_items"]) == 1
    assert payload["resume_items"][0]["id"] == "turn-new"


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

    assert len(payload["latest_results"]) == 4
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
        "running": "empty",
    }


def test_personal_resources_are_normalized_into_payload():
    payload = _payload(
        personal_resources=[
            {
                "key": "tokens",
                "label": "我的 Token",
                "value": 12345,
                "unit": "本月",
                "tab": "tokens",
                "status": "ok",
            },
            {
                "key": "skills",
                "label": "我的技能",
                "value": 0,
                "unit": "个",
                "tab": "skills",
                "status": "empty",
            },
        ]
    )

    # 顺序即工作台卡片顺序：资源入口（知识库/数据集）在前，过程性数据在后。
    assert [item["key"] for item in payload["personal_resources"]] == [
        "knowledge",
        "datasets",
        "memory",
        "tokens",
        "data",
        "skills",
        "mcp",
        "tasks",
        "inbox",
    ]
    tokens = next(item for item in payload["personal_resources"] if item["key"] == "tokens")
    assert tokens["value"] == 12345
    assert tokens["unit"] == "本月"
    assert tokens["tab"] == "tokens"
    skills = next(item for item in payload["personal_resources"] if item["key"] == "skills")
    assert skills["status"] == "empty"


def test_personal_resources_default_to_empty_shell_when_missing():
    payload = _payload()
    assert len(payload["personal_resources"]) == 9
    assert payload["personal_resources"][0]["key"] == "knowledge"
    assert payload["personal_resources"][-1]["key"] == "inbox"
    assert all(item["tab"] for item in payload["personal_resources"])
    # 没有数据时统一是空壳卡片，不能显示 0 以外的占位或漏掉单位
    assert all(item["value"] == 0 and item["status"] == "empty" for item in payload["personal_resources"])

    payload = _payload(
        running_items=[
            {
                "id": "run:1",
                "business_key": "saved-report-run:1",
                "type": "saved_report_run",
                "title": "经营日报",
                "subtitle": "正在生成报表",
                "occurred_at": "2026-07-18T10:20:00",
                "status": "running",
                "severity": "info",
                "action": "open_report",
                "source": "saved_report_run",
                "target": {"report_id": "report-1", "run_id": 1},
            },
            {
                "id": "run:2",
                "business_key": "saved-report-run:2",
                "type": "saved_report_run",
                "title": "库存日报",
                "subtitle": "正在生成报表",
                "occurred_at": "2026-07-18T10:10:00",
                "status": "running",
                "severity": "info",
                "action": "open_report",
                "source": "saved_report_run",
                "target": {"report_id": "report-2", "run_id": 2},
            },
        ]
    )

    assert [item["id"] for item in payload["running_items"]] == ["run:1", "run:2"]
    assert payload["running_items"][0]["source"] == "saved_report_run"
    assert payload["running_items"][0]["target"]["run_id"] == 1


@pytest.mark.asyncio
async def test_running_loader_uses_persistent_saved_report_runs():
    from app.services import workbench_home_service as svc

    run = SimpleNamespace(
        id=31,
        report_id="report-7",
        user_id=7,
        started_at=datetime(2026, 7, 18, 10, 20),
        finished_at=None,
        status="running",
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(all=lambda: [(run, "经营日报")]))

    report_items = await svc._load_saved_report_running_items(db, 7)

    assert report_items[0]["source"] == "saved_report_run"
    assert report_items[0]["target"] == {"report_id": "report-7", "run_id": 31}
    report_items, source_status = await svc._load_running_items(db, 7)
    assert report_items[0]["source"] == "saved_report_run"
    assert source_status == "ok"


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
                "status": "scheduled",
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
    # 最近任务列来自执行历史，与任务配置列表解耦
    assert payload["recent_tasks"] == []


def test_recent_tasks_come_from_execution_history_not_task_list():
    payload = _payload(
        task_items=[
            {
                "id": "task:1",
                "business_key": "task:1:status:1",
                "type": "scheduled_task",
                "title": "库存巡检",
                "subtitle": "定时任务 · 已启用",
                "occurred_at": "2026-07-18T08:00:00",
                "status": "scheduled",
                "action": "open_task",
                "target": {"task_id": 1},
                "needs_attention": False,
                "next_run_at": "2026-07-18T20:00:00",
            }
        ],
        recent_task_run_items=[
            {
                "id": f"task-run:{index}",
                "business_key": f"task-run:{index}",
                "type": "task_run",
                "title": f"执行 {index}",
                "subtitle": "完成巡检",
                "occurred_at": f"2026-07-18T09:{index:02d}:00",
                "status": "success",
                "severity": "info",
                "action": "open_task_run",
                "target": {"task_id": 1, "run_id": index},
            }
            for index in range(6)
        ],
    )

    assert len(payload["recent_tasks"]) == 4
    assert payload["recent_tasks"][0]["action"] == "open_task_run"
    assert payload["recent_tasks"][0]["occurred_at"] > payload["recent_tasks"][-1]["occurred_at"]
    assert payload["mode"] == "quiet"
    assert payload["next_scheduled_item"]["title"] == "库存巡检"


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
async def test_load_tasks_available_without_task_center_menu():
    """个人中心「我的任务」不依赖 menu:task_center，工作台同样按归属加载。"""
    from app.services.workbench_home_service import _load_tasks

    class _Scalars:
        def all(self):
            return [
                SimpleNamespace(
                    id=9,
                    name="日报",
                    status=1,
                    last_run_id=3,
                    last_run_at=datetime(2026, 7, 18, 8, 0),
                    updated_at=datetime(2026, 7, 18, 9, 0),
                    created_at=datetime(2026, 7, 17, 9, 0),
                    next_run_at=datetime(2026, 7, 18, 20, 0),
                )
            ]

    class _Result:
        def scalars(self):
            return _Scalars()

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result())

    items = await _load_tasks(
        db,
        7,
        {"role": "user", "permissions": {"menus": ["menu:ai_chat"]}},
    )

    assert len(items) == 1
    assert items[0]["title"] == "日报"
    assert items[0]["action"] == "open_task"
    assert items[0]["status"] == "scheduled"
    assert items[0]["subtitle"] == "定时任务 · 已启用"
    assert items[0]["next_run_at"] == "2026-07-18T20:00:00"


def test_task_run_maps_to_workbench_item():
    from app.services.workbench_home_service import _task_run_to_workbench_item

    item = _task_run_to_workbench_item(
        {
            "id": 88,
            "trace_id": "trace-xyz",
            "query": "生成日报",
            "summary": "已完成",
            "status": "success",
            "execution_time_ms": 29000,
            "created_at": datetime(2026, 8, 5, 10, 0, 0),
            "task_id": 7,
            "task_name": "PUE日报",
            "agent_name": "巡检助手",
        }
    )

    assert item["id"] == "task-run:88"
    assert item["title"] == "PUE日报"
    assert item["subtitle"] == "巡检助手"
    assert item["execution_time_ms"] == 29000.0
    assert item["status"] == "success"
    assert item["action"] == "open_task_run"
    assert item["target"] == {"task_id": 7, "run_id": 88, "trace_id": "trace-xyz"}


def test_task_run_omits_query_summary_from_subtitle():
    from app.services.workbench_home_service import _task_run_to_workbench_item

    item = _task_run_to_workbench_item(
        {
            "id": 9,
            "query": "【🌐 TaskCenter 自动化全局执行规则】1. 无人值守模式：本次为后台自动触发",
            "summary": "## 报告\n\n" + ("很长内容" * 40),
            "status": "success",
            "execution_time_ms": 1250,
            "created_at": datetime(2026, 8, 5, 10, 0, 0),
            "task_id": 1,
            "task_name": "数据查询测试",
            "agent_name": "主助手(Main)",
        }
    )

    assert item["title"] == "数据查询测试"
    assert item["subtitle"] == "主助手(Main)"
    assert "TaskCenter" not in item["subtitle"]
    assert "很长内容" not in item["subtitle"]
    assert item["execution_time_ms"] == 1250.0


@pytest.mark.asyncio
async def test_load_recent_task_runs_uses_execution_history(monkeypatch):
    from app.services import workbench_home_service as svc

    async def fake_list(db, **kwargs):
        assert kwargs.get("owner_user_id") == 7
        assert kwargs.get("is_admin") is False
        assert kwargs.get("page_size") == 8
        return [
            {
                "id": 3,
                "trace_id": "t-1",
                "query": "跑一下",
                "summary": "ok",
                "status": "success",
                "created_at": datetime(2026, 8, 5, 12, 0, 0),
                "task_id": 9,
                "task_name": "周报",
                "agent_name": "助手",
            }
        ], 1

    monkeypatch.setattr(
        "app.services.task_center_service.TaskCenterService.list_execution_history",
        fake_list,
    )
    items = await svc._load_recent_task_runs(
        AsyncMock(),
        7,
        {"role": "user"},
    )
    assert len(items) == 1
    assert items[0]["title"] == "周报"
    assert items[0]["action"] == "open_task_run"

@pytest.mark.asyncio
async def test_portal_activity_excludes_chatbi_conversations(monkeypatch):
    """门户活动只留报表类条目；会话改由工作台自己的全智能体查询提供。"""
    from app.services import workbench_home_service as svc

    captured = {}

    async def fake_build(db, **kwargs):
        captured.update(kwargs)
        return {
            "recent_analysis": [
                {
                    "type": "conversation",
                    "id": 1,
                    "conversation_id": "c1",
                    "title": "查一下费用趋势",
                    "occurred_at": "2026-10-15T10:00:00",
                    "status": "success",
                },
                {
                    "type": "report_run",
                    "id": 2,
                    "report_id": "r1",
                    "run_id": 2,
                    "title": "经营日报",
                    "occurred_at": "2026-10-15T09:00:00",
                    "status": "success",
                },
            ]
        }

    monkeypatch.setattr(
        "app.services.data_portal_home_service.DataPortalHomeService.build", fake_build
    )

    payload = await svc._load_portal_activity(
        AsyncMock(), 7, [1], datetime(2026, 7, 18, 10, 0)
    )

    assert captured["include_conversations"] is False
    assert [item["type"] for item in payload["reports"]] == ["report_run"]
    assert payload["reports"][0]["id"] == "report_run:2"
    assert "conversations" not in payload


@pytest.mark.asyncio
async def test_build_resume_items_come_from_workbench_conversations(monkeypatch):
    """resume_items 来自全智能体会话装载器，不再取自数据门户。"""
    from app.services import workbench_home_service as svc

    async def _empty_list(*args, **kwargs):
        return []

    async def _empty_portal(*args, **kwargs):
        return {"reports": []}

    async def _running(*args, **kwargs):
        return [], "empty"

    async def _conversations(*args, **kwargs):
        return [
            {
                "id": "conversation:1",
                "business_key": "conversation:c1",
                "type": "conversation",
                "title": "我有哪些技能",
                "subtitle": "数据智能助手",
                "occurred_at": "2026-10-15T10:00:00",
                "status": "success",
                "action": "open_conversation",
                "target": {"conversation_id": "c1", "agent_id": "agent-data"},
            }
        ]

    monkeypatch.setattr(svc, "_load_notifications", _empty_list)
    monkeypatch.setattr(svc, "_load_tasks", _empty_list)
    monkeypatch.setattr(svc, "_load_recent_task_runs", _empty_list)
    monkeypatch.setattr(svc, "_load_portal_activity", _empty_portal)
    monkeypatch.setattr(svc, "_load_conversations", _conversations)
    monkeypatch.setattr(svc, "_load_running_items", _running)
    monkeypatch.setattr(svc, "_load_agents", _empty_list)
    monkeypatch.setattr(svc, "_load_scenarios", lambda user: [])
    monkeypatch.setattr(svc, "_load_personal_resources", _empty_list)

    payload = await svc.WorkbenchHomeService.build(
        AsyncMock(), user_id=7, user={"role": "user", "user_name": "alice"}
    )

    assert len(payload["resume_items"]) == 1
    assert payload["resume_items"][0]["subtitle"] == "数据智能助手"
    assert payload["resume_items"][0]["target"]["conversation_id"] == "c1"
    assert payload["source_status"]["conversations"] == "ok"
    assert payload["latest_results"] == []
