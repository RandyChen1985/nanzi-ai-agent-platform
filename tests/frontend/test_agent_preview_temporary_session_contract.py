from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
AGENT_MGMT = ROOT / "frontend/src/views/AgentManagement.vue"
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
CHAT = ROOT / "frontend/src/views/Chat.vue"

pytestmark = pytest.mark.no_infrastructure


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _between(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def test_preview_entries_open_in_clean_temporary_session_mode():
    """预览对话的两条入口都必须带 preview=1。

    不带标记时，embed 页会按「新标签续接上次会话」的默认逻辑，把上一次的会话
    （甚至另一个智能体的会话）加载出来。
    """
    source = _source(AGENT_MGMT)

    assert "&agent_id=${encodeURIComponent(agentKey)}&theme=light&preview=1" in source
    assert "`/dashboard/chat?agent_id=${encodeURIComponent(agentKey)}&preview=1`" in source


def test_embed_preview_flag_is_parsed_from_url():
    source = _source(EMBED_CHAT)

    assert "let isPreviewSession = false;" in source
    assert 'if (query.get("preview") === "1") {' in source
    # 预览标记只用于本页签的内存状态：写存储会让下一次打开继续命中它。
    assert "isPreviewSession = true;" in source


def test_preview_never_reads_resumable_conversation_pointers():
    """预览页读会话指针必须早退：localStorage 里那条是正式对话的续接记录。"""
    source = _source(EMBED_CHAT)
    read_fn = _between(source, "const readStoredConversationId", "const clearStoredConversationId")

    assert 'if (isPreviewSession) return "";' in read_fn


def test_preview_never_writes_or_clears_conversation_pointers():
    """预览只存在于内存：既不写本地指针，也不因归属校验失败清掉正式对话的指针。"""
    source = _source(EMBED_CHAT)
    clear_fn = _between(source, "const clearStoredConversationId", "const persistConversationId")
    persist_fn = _between(source, "const persistConversationId", "const purgeLegacyConversationStorage")

    assert "if (isPreviewSession) return;" in clear_fn
    assert "if (isPreviewSession) return;" in persist_fn


def test_preview_never_claims_server_active_conversation():
    """预览不得把临时会话登记成服务端活跃会话，否则正常打开对话页会续接到预览。"""
    source = _source(EMBED_CHAT)
    update_fn = _between(source, "const updateActiveConversationOnServer", "watch(conversationId")

    assert "if (isPreviewSession) return;" in update_fn


def test_preview_init_skips_all_resume_sources_and_starts_unpersisted_session():
    source = _source(EMBED_CHAT)

    # 预览分支必须同时：清掉宿主登记的 resume id、新建不落盘的会话、置 loadedCid
    # 以跳过后面的「本地指针 + /chat/active」续接块。
    assert """if (isPreviewSession) {
      requestedConversationId = "";
      if (!conversationId.value) {
        generateNewConversation({ persist: false });
      }
      loadedCid = true;
    }""" in source


def test_host_init_config_cannot_reintroduce_conversation_or_reset_preview():
    """宿主重推 INIT_CONFIG 时，预览既不能采纳其 conversation_id，也不能清空临时会话。"""
    source = _source(EMBED_CHAT)

    assert "if (data.conversation_id && !isPreviewSession) {" in source
    assert "if (!data.conversation_id && !isPreviewSession) {" in source


def test_dashboard_chat_host_forwards_preview_flag_to_embed_iframe():
    """降级入口 /dashboard/chat?preview=1 经 iframe 打开 embed 时必须透传标记。"""
    source = _source(CHAT)

    assert 'const previewQuery = String(route.query.preview || \'\') === \'1\' ? \'?preview=1\' : \'\';' in source
    assert "iframeUrl.value = `/embed/chat${previewQuery}`;" in source
