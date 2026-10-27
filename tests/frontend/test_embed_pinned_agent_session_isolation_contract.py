from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"

pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def _between(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def test_pinned_agent_scope_is_derived_from_integration_lock():
    """指定智能体的判定必须复用集成锁，而不是「用户切专家」也会改的状态。"""
    source = _source()

    assert 'const AGENT_SCOPED_STORAGE_SEGMENT = "a";' in source
    assert "const pinnedAgentScope = (): string => {" in source
    scope_fn = _between(source, "const pinnedAgentScope", "const normalizeEmbedInstanceId")
    assert "integrationAgentLockId.value" in scope_fn


def test_tab_pointer_storage_key_carries_agent_dimension():
    """同一标签从 agent A 换到 agent B 时，必须读不到 A 的指针。"""
    source = _source()
    tab_key_fn = _between(source, "const tabConversationStorageKey", "const readStoredConversationId")

    assert "const agentScope = pinnedAgentScope();" in tab_key_fn
    assert "${agentScope ? `:${agentScope}` : \"\"}" in tab_key_fn


def test_pinned_agent_never_resumes_shared_conversation_pointer():
    """带 agent_id 打开时不得沿用跨标签共享指针（那是平台对话/别的智能体的会话）。"""
    source = _source()
    read_fn = _between(source, "const readStoredConversationId", "const clearStoredConversationId")

    # 本标签指针优先，仍然保留「同标签刷新续接」。
    assert "if (tabValue) return tabValue;" in read_fn
    assert 'if (pinnedAgentScope()) return "";' in read_fn
    # 必须先拒绝共享指针，才能走到 localStorage。
    assert read_fn.index('if (pinnedAgentScope()) return "";') < read_fn.index("localStorage.getItem(key)")


def test_pinned_agent_never_writes_shared_conversation_pointer():
    """写入同理：只写本标签，否则平台对话下次会续接到这条嵌入会话。"""
    source = _source()
    persist_fn = _between(source, "const persistConversationId", "const purgeLegacyConversationStorage")

    assert "if (tabKey) sessionStorage.setItem(tabKey, cid);" in persist_fn
    assert "if (pinnedAgentScope()) return;" in persist_fn
    assert persist_fn.index("if (pinnedAgentScope()) return;") < persist_fn.index("localStorage.setItem(key, cid)")


def test_pinned_agent_never_clears_shared_conversation_pointer():
    """嵌入侧归属校验失败只该清本标签，不能顺手抹掉平台对话的续接指针。"""
    source = _source()
    clear_fn = _between(source, "const clearStoredConversationId", "const persistConversationId")

    assert "if (tabKey) sessionStorage.removeItem(tabKey);" in clear_fn
    assert "if (pinnedAgentScope()) return;" in clear_fn
    assert clear_fn.index("if (pinnedAgentScope()) return;") < clear_fn.index("localStorage.removeItem(key)")


def test_server_active_conversation_is_disabled_for_pinned_agent():
    """服务端活跃会话是账号级的：指定智能体时读会串、写会污染平台对话。"""
    source = _source()

    assert "Boolean(config.token) && !pinnedAgentScope()" in source
