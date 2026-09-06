from types import SimpleNamespace

import pytest

from app.services.ai.agent_service import (
    _accumulate_reasoning_content,
    _accumulate_stream_content,
    _finalize_todo_success,
    _final_process_timeline,
    _should_persist_turn_history,
    _restore_todo_snapshot_from_pending,
    _restore_published_download_urls_from_pending,
    _track_process_timeline,
)
from app.services.ai.runtime.agentscope.event_stream import (
    _sync_published_download_urls_from_context,
    _sync_todo_snapshot_from_context,
)


pytestmark = pytest.mark.no_infrastructure


def test_turn_with_only_thinking_timeline_is_persisted():
    assert _should_persist_turn_history("", [{"kind": "log", "id": "tool_1"}]) is True
    assert _should_persist_turn_history("", None) is False
    assert _should_persist_turn_history("最终回答", None) is True


def test_cancelled_turn_has_a_detached_history_persistence_path():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[3] / "app/services/ai/pipeline/steps/finalize_step.py").read_text(
        encoding="utf-8"
    )
    assert "await_unless_cancelling(" in source
    assert "_persist_assistant_message_and_summary(" in source


def test_chat_stream_emits_terminal_status_before_waiting_for_history_persistence():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    source = (root / "app/services/ai/pipeline/steps/finalize_step.py").read_text(
        encoding="utf-8"
    )
    status_index = source.index('"type": "run_status"')
    persist_index = source.index("await await_unless_cancelling(")
    assert status_index < persist_index

    endpoint_source = (root / "app/api/v1/endpoints/chat.py").read_text(
        encoding="utf-8"
    )
    terminal_index = endpoint_source.index('chunk.get("type") == "run_status"')
    done_index = endpoint_source.index('await queue.put(("done", None))', terminal_index)
    assert terminal_index < done_index
    persistence_source = (root / "app/services/ai/agent_service.py").read_text(encoding="utf-8")
    assert "defer_summary: bool = False" in persistence_source
    assert "if defer_summary:" in persistence_source
    assert 'name=f"merge-session-summary-{conversation_id}"' in persistence_source


def test_final_download_url_guard_retracts_before_terminal_status_and_persistence():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[3] / "app/services/ai/pipeline/steps/finalize_step.py").read_text(
        encoding="utf-8"
    )
    guard_index = source.index("guarded_response_content = _filter_current_turn_download_urls")
    status_index = source.index('"type": "run_status"', guard_index)
    persist_index = source.index("await await_unless_cancelling(", guard_index)
    assert guard_index < status_index < persist_index
    assert '"type": "retraction"' in source[guard_index:status_index]


def test_accumulate_stream_content_excludes_typed_reasoning_events():
    content = _accumulate_stream_content("回答", {
        "type": "reasoning_content",
        "content": "模型推理",
    })

    assert content == "回答"
    assert _accumulate_stream_content(content, {"content": "补充回答"}) == "回答补充回答"


def test_accumulate_stream_content_promotes_process_narration_only():
    content = _accumulate_stream_content("", {
        "type": "process_narration",
        "content": "I'll search first.",
    })
    assert content == ""
    content = _accumulate_stream_content(content, {
        "type": "process_narration_commit",
        "content": "I'll search first.",
    })
    assert content == ""
    content = _accumulate_stream_content(content, {
        "type": "answer_delta",
        "content": "最终报告",
    })
    assert content == "最终报告"
    assert _accumulate_stream_content(content, {"content": "补充"}) == "最终报告补充"


def test_accumulate_stream_content_retracts_speculative_body():
    content = _accumulate_stream_content("", {"content": "让我再搜一次。"})
    assert content == "让我再搜一次。"
    content = _accumulate_stream_content(content, {
        "type": "retraction",
        "content": "",
        "final": False,
    })
    assert content == ""
    content = _accumulate_stream_content(content, {
        "type": "process_narration_commit",
        "content": "让我再搜一次。",
    })
    assert content == ""
    content = _accumulate_stream_content(content, {"content": "最终报告"})
    assert content == "最终报告"


def test_accumulate_stream_content_retracts_typed_answer_delta():
    content = _accumulate_stream_content("", {
        "type": "answer_delta",
        "content": "先查一下",
    })
    assert content == "先查一下"
    content = _accumulate_stream_content(content, {
        "type": "retraction",
        "content": "",
        "final": False,
    })
    assert content == ""
    content = _accumulate_stream_content(content, {
        "type": "answer_delta",
        "content": "最终报告",
    })
    assert content == "最终报告"


def test_chat_turn_persists_finalized_process_timeline_with_memory_and_audit():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[3] / "app/services/ai/agent_service.py").read_text(
        encoding="utf-8"
    )
    assert "apply_stream_chunk" in source
    assert "finalize_process_timeline" in source
    assert "process_timeline=" in source


def test_todo_update_reaches_agent_service_timeline_without_becoming_model_content():
    state = []
    event = {
        "type": "todo_update",
        "todos": [
            {"content": "检索知识库", "status": "completed"},
            {"content": "整理答案", "status": "in_progress"},
        ],
    }

    _track_process_timeline(state, event)

    assert _accumulate_stream_content("", event) == ""
    assert _final_process_timeline(state) == [{
        "kind": "todo",
        "id": "todo_current",
        "title": "任务清单",
        "todos": [
            {"content": "检索知识库", "status": "completed"},
            {"content": "整理答案", "status": "in_progress"},
        ],
        "counts": {"pending": 0, "in_progress": 1, "completed": 1},
    }]


def test_success_finalization_completes_remaining_todos_and_returns_update_event():
    state = [{
        "kind": "todo",
        "id": "todo_current",
        "title": "任务清单",
        "todos": [
            {"content": "已完成步骤", "status": "completed"},
            {"content": "遗漏步骤", "status": "in_progress"},
        ],
        "counts": {"pending": 0, "in_progress": 1, "completed": 1},
    }]

    event = _finalize_todo_success(state, execution_status="success")

    assert event == {
        "type": "todo_update",
        "todos": [
            {"content": "已完成步骤", "status": "completed"},
            {"content": "遗漏步骤", "status": "completed"},
        ],
        "counts": {"pending": 0, "in_progress": 0, "completed": 2},
    }
    assert _final_process_timeline(state)[0]["counts"] == {
        "pending": 0,
        "in_progress": 0,
        "completed": 2,
    }


def test_success_finalization_does_not_duplicate_already_completed_todos():
    state = [{
        "kind": "todo",
        "todos": [{"content": "已完成步骤", "status": "completed"}],
    }]

    assert _finalize_todo_success(state, execution_status="success") is None


@pytest.mark.parametrize(
    "execution_status",
    ["error", "cancelled", "awaiting_permission", "awaiting_external_execution", "awaiting_user"],
)
def test_non_success_finalization_preserves_todo_status(execution_status):
    state = [{
        "kind": "todo",
        "todos": [{"content": "未完成步骤", "status": "in_progress"}],
    }]

    assert _finalize_todo_success(state, execution_status=execution_status) is None
    assert state[0]["todos"][0]["status"] == "in_progress"


def test_todo_snapshot_is_restored_when_a_pending_execution_resumes():
    pending_stream_state = {
        "todo_snapshot": {
            "type": "todo_update",
            "todos": [{"content": "等待确认后继续", "status": "in_progress"}],
            "counts": {"pending": 0, "in_progress": 1, "completed": 0},
        },
    }
    pending = SimpleNamespace(
        state={},
        snapshot=SimpleNamespace(stream_state=pending_stream_state),
    )
    process_timeline_state = []

    _restore_todo_snapshot_from_pending(process_timeline_state, pending)

    assert process_timeline_state == [{
        "kind": "todo",
        "id": "todo_current",
        "title": "任务清单",
        "todos": [{"content": "等待确认后继续", "status": "in_progress"}],
        "counts": {"pending": 0, "in_progress": 1, "completed": 0},
    }]


def test_todo_snapshot_is_copied_from_agent_context_before_pending_registration():
    stream_state = {}
    context = SimpleNamespace(todo_snapshot={
        "type": "todo_update",
        "todos": [{"content": "挂起前的任务", "status": "in_progress"}],
        "counts": {"pending": 0, "in_progress": 1, "completed": 0},
    })

    _sync_todo_snapshot_from_context(stream_state, context)

    assert stream_state["todo_snapshot"] == context.todo_snapshot


def test_published_download_urls_are_copied_before_pending_registration():
    stream_state = {}
    context = SimpleNamespace(published_download_urls=["/api/v1/chat/generated-files/a?token=t"])

    _sync_published_download_urls_from_context(stream_state, context)

    assert stream_state["published_download_urls"] == context.published_download_urls


def test_published_download_urls_are_restored_when_a_pending_execution_resumes():
    pending = SimpleNamespace(
        state={"published_download_urls": ["/api/v1/chat/generated-files/b?token=t"]},
        snapshot=SimpleNamespace(stream_state={}),
    )

    assert _restore_published_download_urls_from_pending(pending) == [
        "/api/v1/chat/generated-files/b?token=t"
    ]


def test_runner_does_not_reference_unscoped_core_stream_state_for_todo_events():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[3] / "app/services/ai/runners/assistant_agent_runner.py").read_text(
        encoding="utf-8"
    )

    assert "capture_todo_update(state, val)" not in source


def test_accumulate_reasoning_content_is_stored_separately():
    assert _accumulate_reasoning_content("", {
        "type": "reasoning_content",
        "content": "模型推理",
    }) == "模型推理"
    assert _accumulate_reasoning_content("模型推理", {"content": "回答"}) == "模型推理"
