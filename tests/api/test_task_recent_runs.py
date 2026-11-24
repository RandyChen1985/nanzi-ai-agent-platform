"""契约：列表接口为每个任务带上「最近 N 次执行」的状态序列。

列表页要画「最近 7 次」色块，但 `GET /tasks` 原本只落聚合计数（成功率），
看不出「成功率 98% 但最近三次全失败」。逐次结果在 ai_agent_execution_history，
按任务会话前缀聚合（与执行记录页同一口径）。这里验证纯分组逻辑，不碰数据库。
"""

from datetime import datetime, timedelta

from app.schemas.task import TaskResponse
from app.services.task_center_service import TaskCenterService


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """只回放预置行，并记录是否真的发了查询。"""

    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    async def execute(self, stmt):
        self.calls += 1
        return _FakeResult(self.rows)


class _FakeTask:
    def __init__(self, task_id, conversation_id):
        self.id = task_id
        self.conversation_id = conversation_id
        self.recent_runs = []


def _row(conversation_id, status, minutes_ago, base=None):
    base = base or datetime(2026, 11, 23, 12, 0)
    return (conversation_id, status, base - timedelta(minutes=minutes_ago))


async def test_groups_runs_by_task_and_returns_chronological_order():
    """查询按时间倒序返回，展示要「旧 → 新」，所以每组内部必须翻转。"""
    tasks = [_FakeTask(1, "task_conv_a"), _FakeTask(2, "task_conv_b")]
    rows = [
        _row("task_conv_a", "success", 1),
        _row("task_conv_b", "failed", 2),
        _row("task_conv_a", "failed", 3),
        _row("task_conv_b", "success", 4),
        _row("task_conv_a", "success", 5),
    ]

    await TaskCenterService.attach_recent_runs(_FakeSession(rows), tasks)

    assert [run["status"] for run in tasks[0].recent_runs] == ["success", "failed", "success"]
    assert [run["status"] for run in tasks[1].recent_runs] == ["success", "failed"]


async def test_keeps_only_the_latest_slots_per_task():
    """limit 是逐任务的：一个任务刷了 10 次，不能把别的任务的名额吃掉。"""
    tasks = [_FakeTask(1, "task_conv_a"), _FakeTask(2, "task_conv_b")]
    rows = [_row("task_conv_a", "failed" if i % 2 else "success", i) for i in range(1, 11)]
    rows.append(_row("task_conv_b", "success", 30))

    await TaskCenterService.attach_recent_runs(_FakeSession(rows), tasks, limit=7)

    assert len(tasks[0].recent_runs) == 7
    # 倒序前 7 条是最新 7 次（minutes_ago 1..7），翻转后最旧的是 minutes_ago=7
    assert tasks[0].recent_runs[0]["at"].startswith("2026-11-23T11:53")
    assert len(tasks[1].recent_runs) == 1


async def test_matches_run_scoped_conversations_by_prefix():
    """定时任务每次执行用独立 run 会话（<root>_run_<n>），要归到根会话对应的任务。"""
    tasks = [_FakeTask(7, "task_conv_c")]
    rows = [
        _row("task_conv_c_run_9", "success", 1),
        _row("task_conv_c_run_8", "failed", 2),
    ]

    await TaskCenterService.attach_recent_runs(_FakeSession(rows), tasks)

    assert [run["status"] for run in tasks[0].recent_runs] == ["failed", "success"]


async def test_task_without_conversation_id_gets_empty_list():
    tasks = [_FakeTask(3, None)]
    session = _FakeSession([_row("task_conv_x", "success", 1)])

    await TaskCenterService.attach_recent_runs(session, tasks)

    assert tasks[0].recent_runs == []
    assert session.calls == 0  # 没有可匹配的会话就不该发查询


async def test_empty_task_list_short_circuits():
    session = _FakeSession([])

    await TaskCenterService.attach_recent_runs(session, [])

    assert session.calls == 0


def test_response_schema_exposes_recent_runs():
    """挂到 ORM 对象上的 recent_runs 必须能被 TaskResponse.from_orm 带出来。"""
    task = _FakeTask(1, "task_conv_a")
    task.recent_runs = [{"status": "success", "at": "2026-11-23T11:00:00"}]

    payload = TaskResponse.model_validate(
        {
            "id": 1,
            "user_id": 1,
            "conversation_id": "task_conv_a",
            "cron_expr": "0 9 * * *",
            "prompt": "巡检",
            "status": 1,
            "name": "巡检",
            "agent_id": "main",
            "created_at": "2026-11-23T00:00:00",
            "updated_at": "2026-11-23T00:00:00",
            "recent_runs": task.recent_runs,
        },
        from_attributes=True,
    )

    assert payload.recent_runs[0].status == "success"
    assert payload.recent_runs[0].at == "2026-11-23T11:00:00"


def test_response_defaults_to_empty_recent_runs():
    """没有执行记录的任务要拿到空数组，前端据此显示留白而不是假装健康。"""
    payload = TaskResponse.model_validate(
        {
            "id": 1,
            "user_id": 1,
            "conversation_id": "task_conv_a",
            "cron_expr": "0 9 * * *",
            "prompt": "巡检",
            "status": 1,
            "name": "巡检",
            "agent_id": "main",
            "created_at": "2026-11-23T00:00:00",
            "updated_at": "2026-11-23T00:00:00",
        },
        from_attributes=True,
    )

    assert payload.recent_runs == []
