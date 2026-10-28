"""Todo 清单终态语义（settled）：终结后不再展示「进行中」，但不伪造完成。"""

import pytest

from app.services.ai.agent_service import _finalize_todo_terminal
from app.services.ai.runtime.agentscope.process_timeline_snapshot import (
    _apply_todo_update,
    finalize_process_timeline,
)


pytestmark = pytest.mark.no_infrastructure


def _state(status: str = "in_progress"):
    return [{
        "kind": "todo",
        "id": "todo_current",
        "title": "任务清单",
        "todos": [{"content": "未完成步骤", "status": status}],
        "counts": {
            "pending": 0,
            "in_progress": 1 if status == "in_progress" else 0,
            "completed": 1 if status == "completed" else 0,
        },
    }]


@pytest.mark.parametrize("execution_status", ["error", "cancelled", "rejected", "failed"])
def test_terminal_failure_settles_checklist_without_completing_it(execution_status):
    """异常终结只打「已结束」标记，未完成项保持原状态。"""
    state = _state()

    event = _finalize_todo_terminal(state, execution_status=execution_status)

    assert event["settled"] is True
    assert event["settledStatus"] == execution_status
    assert event["todos"][0]["status"] == "in_progress"
    assert state[0]["settled"] is True
    assert state[0]["settledStatus"] == execution_status


@pytest.mark.parametrize(
    "execution_status",
    ["awaiting_permission", "awaiting_external_execution", "awaiting_user", "interrupted"],
)
def test_awaiting_states_keep_checklist_resumable(execution_status):
    """挂起态不是终结：恢复后仍要继续展示「进行中」。"""
    state = _state()

    assert _finalize_todo_terminal(state, execution_status=execution_status) is None
    assert "settled" not in state[0]


def test_success_finalization_completes_and_settles_checklist():
    state = _state()

    event = _finalize_todo_terminal(state, execution_status="success")

    assert event["todos"][0]["status"] == "completed"
    assert event["settled"] is True
    assert event["settledStatus"] == "success"


def test_settled_flags_survive_timeline_finalization():
    """落库快照必须保留 settled，否则刷新后前端又把它当进行中。"""
    state = _state()
    _finalize_todo_terminal(state, execution_status="cancelled")

    item = finalize_process_timeline(state)[0]

    assert item["settled"] is True
    assert item["settledStatus"] == "cancelled"


def test_todo_update_chunk_preserves_settled_flags():
    """实时事件折叠进时间线时不能丢掉 settled。"""
    state = []

    _apply_todo_update(
        state,
        {
            "todos": [{"content": "未完成步骤", "status": "in_progress"}],
            "settled": True,
            "settledStatus": "error",
        },
    )

    assert state[0]["settled"] is True
    assert state[0]["settledStatus"] == "error"


def test_running_chunk_without_settled_leaves_flag_unset():
    """执行中的更新不得写入 settled。"""
    state = []

    _apply_todo_update(state, {"todos": [{"content": "执行步骤", "status": "in_progress"}]})

    assert "settled" not in state[0]
