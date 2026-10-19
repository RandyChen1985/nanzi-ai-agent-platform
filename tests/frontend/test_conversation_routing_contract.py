from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _embed() -> str:
    return (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")


def test_embed_defines_conversation_routing_helpers():
    source = _embed()
    assert "const loadConversationRouting = async (cid: string)" in source
    assert "const saveConversationRouting = async (" in source
    assert "/api/v1/chat/conversation/${encodeURIComponent(normalized)}/routing" in source
    assert "routing_mode" in source
    assert "expert_agent_id" in source


def test_switching_conversation_restores_its_routing():
    source = _embed()
    assert "void loadConversationRouting(target);" in source
    assert "await loadConversationRouting(conversationId.value);" in source


def test_switching_expert_persists_conversation_routing():
    source = _embed()
    assert 'void saveConversationRouting(conversationId.value, "expert", normalizedAgentId);' in source
    assert 'void saveConversationRouting(conversationId.value, "auto");' in source


def test_new_conversation_seeds_account_default_routing():
    source = _embed()
    assert "const accountMode: RoutingMode =" in source
    assert "void saveConversationRouting(\n      conversationId.value,\n      accountMode," in source


def test_url_locked_agent_never_reads_or_writes_routing():
    """锁定链接的专家由 URL 决定，读写会话记录会破坏锁定语义。"""
    source = _embed()
    assert source.count("if (!normalized || isRoutingSettingsLocked.value) return;") >= 2


def test_account_default_cache_is_updated_on_save():
    """新建会话沿用账号默认，缓存不同步会拿到旧值。"""
    source = _embed()
    assert "savedRoutingPreference.value = {\n        routing_mode: mode," in source


def test_account_default_does_not_override_restored_conversation_routing():
    """会话级恢复与账号默认是两条异步链路：账号默认后到时会覆盖会话选择。

    因此恢复成功后必须立起标志，让账号默认只做兜底，而不是反过来盖掉该会话的专家。
    """
    source = _embed()
    assert "const conversationRoutingRestored = ref(false);" in source
    assert "conversationRoutingRestored.value = true;" in source
    assert "if (conversationRoutingRestored.value) {" in source


def test_identity_change_clears_restored_flag():
    """换用户后不能带着上一位用户的「已恢复」标志，否则新身份的账号默认永不生效。"""
    source = _embed()
    assert "conversationRoutingRestored.value = false;" in source


def test_restored_expert_is_validated_against_allowed_agents():
    """会话记录的专家可能已被停用/收权：列表就绪时必须当场回落，不能显示裸 ID。"""
    source = _embed()
    assert "allowedAgents.value.length > 0" in source
    assert "!allowedAgents.value.some((a: any) => a.id === config.expertAgentId)" in source
