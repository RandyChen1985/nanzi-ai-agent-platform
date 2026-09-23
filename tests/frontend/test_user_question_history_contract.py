"""主动提问（ask_user_question）历史回放契约。

背景：提问卡实时渲染依赖消息对象上的 `userQuestion`，该字段**不落库**；
打开历史会话时前端只拿到审计表的 `process_timeline`。因此必须保证：

1. 后端把完整卡片快照（含选项）写进 process_timeline，且在定稿压缩中保留；
2. awaiting_user 轮次必须落库，否则该轮在历史里整体缺失；
3. 前端历史加载时用快照重建卡片，并用后续的「用户回答」回执回填状态。

后端 1/2 由 tests/ai/runtime/test_process_timeline_snapshot.py 与
tests/ai/pipeline/test_pipeline_steps.py 做行为级覆盖；这里锁定前后端接线。
"""

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

EMBED_CHAT = "frontend/src/views/EmbedChat.vue"
AGENT_DEBUG = "frontend/src/views/AgentDebug.vue"
CHAT_LOGS = "frontend/src/views/ChatLogs.vue"
AGENT_API_TS = "frontend/src/api/agent.ts"
USER_QUESTION_TS = "frontend/src/utils/userQuestion.ts"
PROCESS_TIMELINE_TS = "frontend/src/utils/processTimeline.ts"
SNAPSHOT_PY = "app/services/ai/runtime/agentscope/process_timeline_snapshot.py"
FINALIZE_PY = "app/services/ai/pipeline/steps/finalize_step.py"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


# 两个对话视图各自的历史加载入口：EmbedChat（用户端）与 AgentDebug（调试台）。
# 二者都必须支持提问卡回放，缺一都会出现「历史里只剩一行需要用户回答」。
HISTORY_LOADERS = (
    (EMBED_CHAT, "const fetchConversationHistory = async", 9000),
    (AGENT_DEBUG, "const loadSessionHistory = async", 6000),
)


@pytest.mark.parametrize("relative_path, anchor, window", HISTORY_LOADERS)
def test_history_load_rebuilds_question_card_from_timeline_snapshot(
    relative_path: str, anchor: str, window: int
) -> None:
    """历史加载必须从 process_timeline 重建 userQuestion，否则卡片无法显示。"""
    source = _read(relative_path)
    start = source.index(anchor)
    body = source[start : start + window]
    assert "userQuestionStatesFromTimeline(hydratedTimeline)" in body, (
        relative_path,
        "未从时间线快照重建提问卡",
    )
    assert "userQuestion:" in body, (relative_path, "重建结果未挂到历史消息上")


@pytest.mark.parametrize("relative_path, anchor, window", HISTORY_LOADERS)
def test_history_load_backfills_answer_state_from_receipts(
    relative_path: str, anchor: str, window: int
) -> None:
    """历史里的卡片需按后续「用户回答」回执回填状态，避免过期卡片仍可点击。"""
    source = _read(relative_path)
    start = source.index(anchor)
    body = source[start : start + window]
    assert "parseUserQuestionReceipt(" in body, (relative_path, "未解析历史回答回执")
    assert "applyUserQuestionReceipts(" in body, (relative_path, "未回填卡片状态")
    assert "historyReceipts" in body, relative_path


def test_user_question_helpers_are_exported():
    source = _read(USER_QUESTION_TS)
    for helper in (
        "export function userQuestionStateFromTimelineLog",
        "export function userQuestionStatesFromTimeline",
        "export function parseUserQuestionReceipt",
        "export function applyUserQuestionReceipts",
        "export function markUserQuestionTimelineResolved",
        "export function userQuestionTextFromTimeline",
    ):
        assert helper in source, helper


def test_resolved_answer_also_clears_timeline_pending_step():
    """作答后必须收尾时间线步骤。

    提问日志项创建时固定 `status: "pending"`、后端也不再补写结果项，若不收尾，
    历史回放与实时界面都会给已作答的步骤亮「进行中」呼吸灯。
    """
    source = _read(USER_QUESTION_TS)
    start = source.index("export function applyUserQuestionReceipts")
    body = source[start:]
    assert "markUserQuestionTimelineResolved(message.processTimeline" in body, "回填未收尾时间线步骤"


@pytest.mark.parametrize("relative_path", (EMBED_CHAT, AGENT_DEBUG))
def test_submit_action_resolves_timeline_step_immediately(relative_path: str) -> None:
    """实时提交也要收尾，避免同一消息在实时与刷新后显示不一致。"""
    source = _read(relative_path)
    start = source.index("const submitUserQuestion = async")
    body = source[start : start + 1200]
    assert (
        "markUserQuestionTimelineResolved(msg.processTimeline, card.question_id)" in body
    ), (relative_path, "提交后未收尾时间线步骤")
    assert body.index("markUserQuestionTimelineResolved") < body.index(
        "await sendMessage()"
    ), (relative_path, "收尾应发生在发起请求前")


def test_history_rebuild_reuses_live_parsing_rules():
    """历史重建必须复用实时事件解析，避免两套校验口径漂移。"""
    source = _read(USER_QUESTION_TS)
    start = source.index("export function userQuestionStateFromTimelineLog")
    body = source[start : start + 900]
    assert 'type: "user_question"' in body, "未复用 parseUserQuestionEvent 的校验"
    assert "parseUserQuestionEvent({" in body


def test_timeline_log_type_carries_persisted_question_card():
    source = _read(PROCESS_TIMELINE_TS)
    assert "export type PersistedUserQuestion" in source
    assert "user_question?: PersistedUserQuestion | null;" in source


def test_backend_persists_and_preserves_question_card():
    """后端写入卡片快照，且在 finalize 的白名单压缩中不被丢弃。"""
    source = _read(SNAPSHOT_PY)
    assert "_user_question_log_payload(chunk)" in source, "未写入卡片快照"
    assert '"user_question": chunk.get("user_question"),' in source, "中间态白名单丢弃卡片快照"
    assert 'copied["user_question"] = item.get("user_question")' in source, "定稿压缩丢弃卡片快照"


def test_awaiting_user_turn_is_audited_for_history_replay():
    """awaiting_user 没有同轮恢复，必须在本轮落库，否则历史整体缺该轮。"""
    source = _read(FINALIZE_PY)
    assert "AUDIT_DEFERRED_STATUSES" in source
    start = source.index("AUDIT_DEFERRED_STATUSES = {")
    deferred = source[start : source.index("}", start)]
    assert "awaiting_user" not in deferred, "awaiting_user 仍在延迟审计集合中"
    assert "awaiting_permission" in deferred, "同轮可恢复暂停态应保持延迟审计"
    assert "context.execution_status not in AUDIT_DEFERRED_STATUSES" in source


def test_dashboard_success_rate_excludes_suspended_turns():
    """暂停态轮次落库后不得计入成功率分母，否则主动提问会拉低成功率。"""
    source = _read("app/api/portal/endpoints/dashboard.py")
    assert "SUSPENDED_HISTORY_STATUSES" in source
    start = source.index("SUSPENDED_HISTORY_STATUSES = (")
    statuses = source[start : source.index(")", start)]
    for status in ("awaiting_user", "awaiting_permission", "awaiting_external_execution", "interrupted"):
        assert status in statuses, status
    assert "AgentExecutionHistory.status.notin_(SUSPENDED_HISTORY_STATUSES)" in source


def test_chat_logs_replays_question_card_for_turns_without_summary():
    """聊天日志的主动提问轮没有正文，必须回放提问卡，否则只剩「(无响应内容)」。"""
    source = _read(CHAT_LOGS)
    start = source.index("const setConversationTurns =")
    body = source[start : start + 1200]
    assert "userQuestionStatesFromTimeline(turn.process_timeline)" in body, "未从快照重建提问卡"
    assert "parseUserQuestionReceipt(turn.query)" in body, "未解析历史回答回执"
    assert "applyUserQuestionReceipts(turns, receipts)" in body, "未回填回答状态"
    assert (
        "userQuestionTextFromTimeline(turn.process_timeline)" in body
    ), "修复前落库的轮次未退化回放问题文本"
    # 所有轮次赋值路径都必须经过 setConversationTurns，避免某条分支漏挂卡片
    assert "conversationTurns.value = items" not in source, "存在绕过提问卡重建的赋值路径"
    assert "conversationTurns.value = [log]" not in source, "存在绕过提问卡重建的赋值路径"


def test_chat_logs_renders_read_only_question_card():
    """日志页只读展示：卡片必须 disabled，且不能与「(无响应内容)」同时出现。"""
    source = _read(CHAT_LOGS)
    start = source.index("<UserQuestionCard")
    tag = source[start : source.index("/>", start)]
    assert ':payload="turn.userQuestion"' in tag
    assert "disabled" in tag, "日志页的提问卡必须只读"
    assert 'v-if="!turn.summary && !turn.userQuestion"' not in source, "(无响应内容) 会盖住提问卡"
    assert "!turn.userQuestionText" in source, "(无响应内容) 会盖住退化回放的问题文本"
    assert "{{ turn.userQuestionText }}" in source, "旧数据的问题文本未渲染"


def test_history_api_type_exposes_process_timeline():
    """类型缺字段会让日志页/历史回放取不到时间线（后端 schema 实际已下发）。"""
    source = _read(AGENT_API_TS)
    assert "process_timeline?: ProcessTimelineItem[] | null" in source, "历史轮次类型未暴露时间线"
