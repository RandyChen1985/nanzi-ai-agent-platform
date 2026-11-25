"""契约：工具审批确认后恢复流期间输入框必须保持锁定，不得被旧轮询、旧流或交接空窗提前释放。

覆盖以下 5 类已发生/已复核的缺陷：
1. 残留历史同步轮询（`syncLatestSessionHistory`）在重试耗尽时不得在 `streamInFlight` 或仍有待确认卡时把 `isProcessing` 置 false；
2. 恢复流启动时必须取消残留历史同步、停止旧主流看门狗、同步 `markRunStarted()`，并先自增 `liveWriterToken` 再 `abort()` 旧流；
3. 主发送流 `finally` 必须无条件 `stopStreamLivenessWatch()`（防止看门狗泄漏 15s 后误杀恢复流），并按 `liveWriterToken === streamToken` 门控锁态与计时器收尾；
4. 子智能体授权交接（`subagent_permission_handoff`）在确认流收尾到 `dispatchResumeDirective` 接棒发出续聊期间保持 `subagentHandoffPending` 锁定，且 `stopGeneration` 通过 `handoffGeneration` 立即作废在途轮询；
5. `AgentDebug.vue` 对齐以上令牌归属、子智能体交接与停止行为，且 `stopGeneration` 不误增 `liveWriterToken`（避免恢复流收尾跳过 `flush()` 与 logs 收尾）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
AGENT_DEBUG = ROOT / "frontend/src/views/AgentDebug.vue"
pytestmark = pytest.mark.no_infrastructure


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for idx in range(brace, len(source)):
        ch = source[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start : idx + 1]
    raise AssertionError(f"未找到与签名配对的结束花括号: {signature}")


def test_history_sync_retry_exhaustion_guards_in_flight_stream_and_pending_card():
    source = _read(EMBED_CHAT)
    sync_body = _body(source, "const syncLatestSessionHistory = async (attempt = 1, maxAttempts = 15) => {")
    exhausted = sync_body.split("} else if (maxAttempts > 1) {", 1)[1]
    assert "if (isProcessing.value && !streamInFlight && !hasPendingConfirmation()) {" in exhausted
    # 取消旧历史同步必须带代次作废，防止在途请求回来重新 scheduleHistoryRetry。
    assert "const syncGeneration = visibilitySyncGeneration;" in sync_body
    assert "if (syncGeneration !== visibilitySyncGeneration) return;" in sync_body

    finish_body = _body(source, "const finishRecoveredTurn = () => {")
    assert "if (!streamInFlight && isProcessing.value && !pendingPermissionLocking.value) isProcessing.value = false;" in finish_body


def test_embed_resume_streams_cancel_old_timers_stop_watchdog_and_bump_token_before_abort():
    source = _read(EMBED_CHAT)
    for sig in (
        "const submitPendingExternalExecution = async (msg: Message) => {",
        "const confirmPendingPermission = async (",
    ):
        body = _body(source, sig)
        assert "stopStreamLivenessWatch();" in body, sig
        assert "cancelPendingHistorySync();" in body, sig
        assert "clearRunStatusNudge();" in body, sig
        assert "markRunStarted();" in body, sig
        assert "nudgeRunStatusConvergence();" in body, sig
        # 必须先自增写入方令牌再 abort 旧控制器，确保主流的 catch/finally 识别已交权。
        assert body.index("const resumeToken = ++liveWriterToken;") < body.index("abortController.abort();"), sig


def test_embed_main_stream_always_stops_watchdog_and_guards_lock_reset_by_token():
    source = _read(EMBED_CHAT)
    send_body = _body(source, "const sendMessageInternal = async (snapshot: ChatSendSnapshot) => {")
    assert "liveWriterToken += 1;" in send_body
    assert "const streamToken = liveWriterToken;" in send_body
    assert "else if (liveWriterToken === streamToken && !embedUnmounted)" in send_body

    finally_block = send_body.rsplit("} finally {", 1)[1]
    assert "const ownsLiveWriter = liveWriterToken === streamToken;" in finally_block
    # 看门狗必须无条件在 finally 停掉，位于 ownsLiveWriter 门控之外。
    assert finally_block.index("stopStreamLivenessWatch();") < finally_block.index(
        "const ownsLiveWriter = liveWriterToken === streamToken;"
    )
    assert "if (ownsLiveWriter) {\n      streamInFlight = false;" in finally_block
    assert "if (ownsLiveWriter) {\n          isProcessing.value =" in finally_block


def test_subagent_handoff_locks_gap_and_cancels_cleanly_on_stop():
    for path, stop_sig in (
        (EMBED_CHAT, "const stopGeneration = (options?: StopGenerationOptions) => {"),
        (AGENT_DEBUG, "const stopGeneration = (options?: StopGenerationOptions) => {"),
    ):
        source = _read(path)
        assert "const subagentHandoffPending = ref(false);" in source
        assert "let handoffGeneration = 0;" in source
        assert "subagentHandoffPending.value || hasActivePendingPermission()" in source

        confirm_body = _body(source, "const confirmPendingPermission = async (")
        assert "if (hasSubagentHandoff) {\n        subagentHandoffPending.value = true;" in confirm_body
        # 立起交接锁必须早于写 isProcessing，保证零解锁空窗。
        assert confirm_body.index("subagentHandoffPending.value = true;") < confirm_body.index(
            "isProcessing.value = Boolean("
        )

        dispatch_body = _body(source, "const dispatchResumeDirective = async (msg: Message, directive: string) => {")
        assert "const handoffGen = ++handoffGeneration;" in dispatch_body
        assert "handoffGeneration !== handoffGen" in dispatch_body
        assert "hasActivePendingPermission()" in dispatch_body
        assert "subagentHandoffPending.value = false;\n        await sendMessage({ content: directive });" in dispatch_body
        assert "if (handoffGeneration === handoffGen) {\n      subagentHandoffPending.value = false;" in dispatch_body

        stop_body = _body(source, stop_sig)
        assert "subagentHandoffPending.value = false;" in stop_body
        assert "handoffGeneration += 1;" in stop_body


def test_agent_debug_stop_does_not_bump_live_writer_token_and_wires_subagent_handoff():
    source = _read(AGENT_DEBUG)
    stop_body = _body(source, "const stopGeneration = (options?: StopGenerationOptions) => {")
    # 原地停止当前流不得自增 liveWriterToken，否则恢复流 finally 会跳过 flush() 与 logs 收尾。
    assert "liveWriterToken +=" not in stop_body
    assert "++liveWriterToken" not in stop_body

    apply_body = _body(source, "const applyPermissionStreamEvent = (msg: Message, data: any) => {")
    assert 'data?.type === "subagent_permission_handoff"' in apply_body
    assert "msg.pendingPermission.resumeDirective = data.directive;" in apply_body

    revoke_body = _body(source, "const revokeSessionPermission = async (msg: Message) => {")
    assert "pending?.sessionGrantScope || conversationId.value" in revoke_body
