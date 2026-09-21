from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.no_infrastructure
@pytest.mark.parametrize(
    "relative_path",
    [
        "frontend/src/views/EmbedChat.vue",
        "frontend/src/views/AgentDebug.vue",
    ],
)
def test_edit_resend_uses_only_real_chat_messages(relative_path: str):
    source = (ROOT / relative_path).read_text(encoding="utf-8")

    assert "const isChatContextMessage" in source
    assert "message.role === \"user\" || message.role === \"agent\"" in source
    assert "messages.value.filter(isChatContextMessage)" in source
    assert "remainingMessages.filter(isChatContextMessage)" in source


@pytest.mark.no_infrastructure
@pytest.mark.parametrize(
    "relative_path",
    [
        "frontend/src/views/EmbedChat.vue",
        "frontend/src/views/AgentDebug.vue",
    ],
)
def test_edit_resend_stops_when_server_history_cannot_be_truncated(relative_path: str):
    source = (ROOT / relative_path).read_text(encoding="utf-8")

    assert "const truncateServerHistory = async" in source
    # saveAndResend 现封装在返回 payload 的回调中，失败时以 return null 终止重发。
    assert "if (!(await truncateServerHistory(keepCount))) return null;" in source


@pytest.mark.no_infrastructure
def test_embed_chat_sends_only_latest_user_for_existing_conversation():
    source = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")

    assert "const buildOutboundMessages" in source
    assert "conversationId.value" in source
    assert "latestUser" in source


@pytest.mark.no_infrastructure
def test_agent_debug_uses_same_current_user_boundary():
    source = (ROOT / "frontend/src/views/AgentDebug.vue").read_text(encoding="utf-8")

    assert "const buildOutboundMessages" in source
    assert "conversationId.value" in source
    assert "latestUser" in source
