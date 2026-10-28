"""会话级工具授权（本会话总是允许）的前端契约。"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_card_offers_session_scope_button_only_when_backend_allows_it():
    card = _read("frontend/src/components/chat/ToolPermissionCard.vue")

    assert 'v-if="props.payload.supports_session_grant"' in card
    assert "本会话总是允许" in card
    assert "submit(true, 'session')" in card
    assert "仅本次执行" in card


def test_card_emits_scope_and_revoke_events():
    card = _read("frontend/src/components/chat/ToolPermissionCard.vue")

    assert '(event: "submit", confirmed: boolean, scope?: "once" | "session"): void' in card
    assert '(event: "revoke"): void' in card
    assert "function submit(confirmed: boolean, scope: \"once\" | \"session\" = \"once\")" in card
    assert "撤销本会话授权" in card


def test_card_renders_session_approved_state():
    card = _read("frontend/src/components/chat/ToolPermissionCard.vue")

    assert 'approved_session: "已允许（本会话）"' in card
    assert "本会话内该工具不再逐条确认" in card
    assert "const canRevoke = computed" in card


def test_permission_payload_carries_session_grant_support():
    handlers = _read("frontend/src/utils/agentscopeSseHandlers.ts")

    assert "supports_session_grant?: boolean" in handlers
    assert "supports_session_grant: Boolean(data.supports_session_grant)" in handlers
    assert '"approved_session"' in handlers
    assert "sessionGrant?.granted" in handlers


def test_chat_pages_send_scope_and_revoke_grants():
    for page in ("frontend/src/views/AgentDebug.vue", "frontend/src/views/EmbedChat.vue"):
        source = _read(page)

        assert "scope: \"once\" | \"session\" = \"once\"" in source
        assert "JSON.stringify({ confirmed, scope })" in source
        assert "@revoke=\"revokeSessionPermission(msg)\"" in source
        assert "/api/v1/chat/permissions/grants/" in source
        assert 'method: "DELETE"' in source


def test_timeline_marks_auto_approved_tool_calls():
    timeline = _read("frontend/src/components/chat/ChatExecutionTimeline.vue")
    timeline_types = _read("frontend/src/utils/processTimeline.ts")

    assert "auto_approved?: boolean" in timeline_types
    assert 'v-if="item.auto_approved"' in timeline
    assert "本会话已授权" in timeline


def test_run_status_keeps_the_session_scope_marker():
    """恢复流末尾的 run_status 不能把「本会话已允许」抹成单次允许，否则撤销入口消失。"""

    for page in ("frontend/src/views/AgentDebug.vue", "frontend/src/views/EmbedChat.vue"):
        source = _read(page)

        assert 'msg.pendingPermission.status === "approved_session"' in source


def test_embed_revives_interrupted_session_approved_cards():
    """恢复轮被刷新打断时，approved_session 也要能复活成待确认，否则卡片永久卡住。"""

    embed = _read("frontend/src/views/EmbedChat.vue")

    revive_at = embed.find("const reviveInterruptedResumeCards")
    assert revive_at >= 0
    revive_body = embed[revive_at : revive_at + 800]
    assert 'permission.status === "approved_session"' in revive_body
