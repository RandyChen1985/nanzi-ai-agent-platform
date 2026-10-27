from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
USER_QUESTION = ROOT / "frontend/src/utils/userQuestion.ts"
PREFLIGHT = ROOT / "app/services/ai/pipeline/steps/preflight_step.py"
HISTORY_STATUS = ROOT / "app/services/ai/user_question_history.py"

pytestmark = pytest.mark.no_infrastructure


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_timeline_replay_restores_persisted_question_status():
    """刷新后卡片要直接沿用快照里的终态，而不是永远当成 pending。"""
    source = _source(USER_QUESTION)
    fn = source.split("export function userQuestionStateFromTimelineLog", 1)[1].split("/**", 1)[0]

    assert 'const persisted = String((raw as Record<string, unknown>).status || "").trim();' in fn
    assert 'persisted === "cancelled"' in fn
    assert 'persisted === "submitted"' in fn
    assert 'persisted === "stale"' in fn
    # 不允许把 pending 当作「已持久化终态」直接写回（否则旧数据行为会变）
    assert 'persisted === "pending"' not in fn


def test_cancellation_is_written_back_to_the_question_turn():
    """取消轮不会走 finalize 的 History 落库，必须在 preflight 里补一条并写回快照。"""
    source = _source(PREFLIGHT)

    assert "persist_user_question_cancellation(" in source
    assert "CANCELLATION_MESSAGE" in source
    assert "if context.user_question_cancelled:" in source
    # 写失败不能阻断取消流程
    assert "Failed to persist user-question cancellation" in source


def test_history_status_writer_only_touches_matching_question():
    source = _source(HISTORY_STATUS)

    assert "def apply_question_status_to_timeline(" in source
    assert 'str(item.get("category") or "") != QUESTION_LOG_CATEGORY' in source
    assert 'item["status"] = _RESOLVED_LOG_STATUS' in source
    assert 'snapshot["status"] = normalized_status' in source
    assert "flag_modified(row, \"process_timeline\")" in source


def test_cancellation_turn_is_idempotent_and_well_formed():
    """补的历史轮次必须带 cancelled 状态，并以 trace_id 去重。"""
    source = _source(HISTORY_STATUS)

    assert "trace_id == trace_id" in source
    assert "AgentExecutionHistory.trace_id == trace_id" in source
    assert 'status="cancelled"' in source
    assert "summary=cancellation_message" in source
    assert "IntegrityError" in source
