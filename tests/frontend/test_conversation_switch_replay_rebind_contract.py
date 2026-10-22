"""契约：切换会话时的续显重绑与会话归属守卫。

背景：续显三元组（`activeStreamDraft` / `streamReplayCursor` / `streamReplayPrimed`）都是
模块级单例，而「多会话并行」已经是正式能力（生成中新建会话刻意不终止服务端 run）。切会话
若不换绑，目标会话的事件就会被写进上一个会话的孤儿草稿、游标沿用上一个会话的序号——而后端
每轮 producer 启动都会清空日志、让 `_seq` 从 1 重新计数，于是事件几乎全被 `after_seq` 挡掉。
表现为两种相反的症状：切过去看不到任何进度，或从 0 全量重放且写错对象。

同一条路径上还有两个连带缺陷：切走时 `resetRunStatus()` 触发的「运行结束」收敛会用刚清空的
消息列表判成 `reload`，把目标会话**仍在跑**的在途快照删掉；上一个会话的流收尾又会把当前会话
正在 pending 的时间线落定。这些都是模块级单例与多会话现实冲突的产物。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"


def _source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def _body(signature: str) -> str:
    """截取顶层箭头函数体：从签名到文件里下一个不带缩进的 `};`。"""
    source = _source()
    start = source.index(signature)
    end = source.index("\n};", start)
    return source[start:end]


# ---- F1 / F8：切会话复位续显三元组 ----


def test_switching_conversation_restores_the_snapshot_after_history_loads():
    body = _body("const handleHistoryClick = async (item: any) => {")
    # 顺序与初始化路径一致：历史会整体替换消息列表，先恢复快照会被冲掉。
    assert "void fetchConversationHistory(false).then(() => restoreEmbedStreamSnapshot());" in body


# ---- F2：切走不得触发目标会话的终态收敛 ----


def test_run_end_watcher_skips_convergence_for_a_conversation_switch():
    source = _source()
    assert re.search(
        r"if \(suppressRunTerminalSyncForConversationSwitch\) \{\s*"
        r"suppressRunTerminalSyncForConversationSwitch = false;\s*"
        r"return;\s*\}",
        source,
    ), "换会话触发的 remoteRunActive=false 必须吞掉终态收敛"


def test_history_sync_gives_up_once_the_conversation_changed():
    body = _body("const syncLatestSessionHistory = async (attempt = 1, maxAttempts = 15) => {")
    assert "const startedForConversation = conversationId.value;" in body
    guard = "if (conversationId.value !== startedForConversation) return;"
    assert body.count(guard) == 2
    reload_at = body.index('if (action.kind === "reload") {')
    first_guard = body.index(guard)
    second_guard = body.index(guard, first_guard + 1)
    # 拉取返回后先确认会话没被切走，再进入 reload；reload 内部重拉前还要再确认一次。
    assert first_guard < reload_at < second_guard
    # 顺序必须是「先重拉、后清理」：旧实现先 clearStreamSnapshot 再请求历史，慢网络下
    # 会在首屏历史尚未还原时就把快照删掉。
    fetch_at = body.index("await fetchConversationHistory(false);", reload_at)
    finish_at = body.index("finishRecoveredTurn();", fetch_at)
    assert fetch_at < finish_at


# ---- F3：日志被截断时不再丢弃已放行的增量 ----


def test_poll_keeps_applying_events_even_when_the_log_was_truncated():
    body = _body("const pollConversationStreamEvents = async () => {")
    assert "const shouldReplayEvents = !isFirstPoll || replayEvents.length > 0;" in body
    # 断层时仍不清空重建：快照里的内容可能比残缺日志更完整。
    assert "if (isFirstPoll && !plan.gap && replayEvents.length > 0) {" in body


# ---- F4：旧流收尾副作用限定在本轮自己的会话 ----


def test_stream_finish_side_effects_are_guarded_by_conversation():
    source = _source()
    assert "const streamConversationId = conversationId.value;" in source
    assert source.count("if (conversationId.value === streamConversationId) {") == 2
    # 落定时间线必须被守卫包住：currentStreamAgentMessage 的兜底是「列表最后一条」，
    # 切走后会落到当前会话正在 pending 的气泡上。
    assert re.search(
        r"if \(conversationId\.value === streamConversationId\) \{\s*"
        r"activeStreamDraft = null;[\s\S]{0,400}?settleRecoveredTimeline\(\);",
        source,
    ), "settleRecoveredTimeline 必须在会话守卫内"


# ---- F5：轮询不得把结果写进别的会话 ----


def test_poll_refuses_a_draft_from_another_conversation():
    body = _body("const pollConversationStreamEvents = async () => {")
    assert "isDraftBoundToConversation(activeStreamDraft, cid)" in body
    # await 之后要重新确认会话未变：请求在途时切走，响应会写进新会话。
    assert "if (conversationId.value !== cid) return;" in body


# ---- F9：fill 分支不得锁死未经校验的 trace ----


def test_fill_does_not_lock_an_unverified_trace_id():
    body = _body("const syncLatestSessionHistory = async (attempt = 1, maxAttempts = 15) => {")
    assert 'if (action.kind === "update") {' in body
    trace_write = "currentMsg.trace_id = currentMsg.trace_id || latestServerItem.trace_id;"
    assert trace_write in body
    # fill 没有 trace 匹配可依据，写入上一轮的 trace 会让本轮真答案永远匹配不上。
    assert body.index('if (action.kind === "update") {') < body.index(trace_write)

def test_switching_conversation_goes_through_the_single_reset_entry():
    """切会话必须复用统一复位入口，而不是再手写一份——手写那份就曾漏掉过复位项。

    复位内容的完整性由 `test_conversation_scoped_state_registry_contract.py` 守着，
    这里只管「切会话这条路确实走了入口」，以及判定与抑制标志的相对位置。
    """
    body = _body("const handleHistoryClick = async (item: any) => {")
    assert "shouldRebindReplayOnConversationSwitch(activeStreamDraft?.conversationId, target)" in body
    assert body.count("resetConversationScopedState();") == 1
    # 判定必须先于复位：点历史里的当前会话不该清掉在途进度。
    assert body.index("shouldRebindReplayOnConversationSwitch") < body.index(
        "resetConversationScopedState();"
    )
    # 抑制标志与复位配套：这次 remoteRunActive 翻 false 是「换会话」而非「本轮结束」，
    # 必须在 resetRunStatus（在入口内）之前立起，watcher 异步 flush 时才读得到。
    assert body.index("suppressRunTerminalSyncForConversationSwitch = true;") < body.index(
        "resetConversationScopedState();"
    )
