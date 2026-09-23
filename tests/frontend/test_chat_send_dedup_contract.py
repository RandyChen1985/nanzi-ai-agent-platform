from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _assert_send_lock_contract(source: str) -> None:
    assert 'createChatSendGate' in source
    assert 'const { locked: sendLocked, runExclusive: runSendExclusive }' in source
    assert 'const sendPreparedMessage = async (' in source
    assert 'return sendMessageInternal(snapshot);' in source
    assert 'clientRequestId' in source
    assert 'groundingAction' in source
    assert 'const sendMessageInternal = async (snapshot: ChatSendSnapshot)' in source
    assert 'client_request_id = snapshot.clientRequestId;' in source


def test_chat_surfaces_lock_duplicate_sends_before_async_preflight_finishes():
    for relative_path in (
        "frontend/src/views/EmbedChat.vue",
        "frontend/src/views/AgentDebug.vue",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        _assert_send_lock_contract(source)
        assert ':is-processing="isProcessing || remoteRunActive"' in source
        assert ':is-submitting="sendLocked"' in source


def test_preflight_send_paths_claim_the_gate_before_history_or_render_awaits():
    for relative_path in (
        "frontend/src/views/EmbedChat.vue",
        "frontend/src/views/AgentDebug.vue",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert 'await sendPreparedMessage(async () =>' in source
        assert 'await truncateServerHistory(keepCount)' in source
        assert 'await nextTick();\n  sendMessage();' not in source


def test_chat_input_does_not_treat_submission_lock_as_a_cancelable_generation():
    source = (ROOT / "frontend/src/components/embed/ChatInput.vue").read_text(encoding="utf-8")
    assert "isSubmitting?: boolean;" in source
    assert "const isInteractionLocked = computed" in source
    assert 'type="button"' in source
    assert "isProcessing ? emit('stop') : isSubmitting ? null : emit('send')" in source
    assert ':disabled="!isProcessing && (isSubmitting || !canSend)"' in source
    # 生成中与提交中的提示必须分属不同状态分支（生成分支新增了 grounding 严格核验文案）。
    assert "'AI 正在努力生成回复中，请稍候…'" in source
    assert "isProcessing ? (enableGrounding ?" in source
    assert ") : isSubmitting ? '准备发送…'" in source
