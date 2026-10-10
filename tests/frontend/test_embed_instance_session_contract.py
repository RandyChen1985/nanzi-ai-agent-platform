"""Contract checks for EmbedChat's legacy and instance-scoped sessions."""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
CHAT = ROOT / "frontend/src/views/Chat.vue"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_embedchat_defines_dual_track_conversation_storage_policy():
    source = _source(EMBED_CHAT)

    assert 'const LEGACY_CONVERSATION_STORAGE_KEY = "yovole_embed_conv_id";' in source
    assert 'const INSTANCE_CONVERSATION_STORAGE_PREFIX = "yovole_embed_conv_id:";' in source
    assert "const normalizeEmbedInstanceId =" in source
    assert "const conversationStorageKey = (): string =>" in source
    assert "const readStoredConversationId = (): string =>" in source
    assert "const persistConversationId = (cid: string) =>" in source
    assert "const shouldUseServerActiveConversation = () => Boolean(config.token);" in source
    assert 'localStorage.setItem("yovole_embed_conv_id",' not in source


def test_embedchat_resolves_instance_before_restoring_a_conversation():
    source = _source(EMBED_CHAT)

    assert 'const queryInstanceId = normalizeEmbedInstanceId(query.get("instance_id"));' in source
    assert "if (queryInstanceId) config.instanceId = queryInstanceId;" in source
    assert 'const savedId = localStorage.getItem("yovole_embed_conv_id");' not in source
    assert "const savedId = readStoredConversationId();" in source
    assert "scheduleUrlTokenInitialization();" in source
    assert "pendingUrlTokenInitTimer = window.setTimeout" in source
    assert "let conversationInitializationGeneration = 0;" in source
    assert "const initGeneration = conversationInitializationGeneration;" in source
    assert "if (initGeneration !== conversationInitializationGeneration) return;" in source
    assert "conversationInitializationGeneration += 1;" in source
    assert "fetchConversationHistory(false, initGeneration)" in source
    assert "expectedInitializationGeneration !== conversationInitializationGeneration" in source


def test_embedchat_uses_server_active_conversation_when_authenticated():
    source = _source(EMBED_CHAT)
    active_update = source[source.index("const updateActiveConversationOnServer"):source.index("const generateNewConversation")]
    init_chat = source[source.index("const initChat = async"):source.index("// History State")]

    assert "if (!shouldUseServerActiveConversation()) return;" in active_update
    assert "if (shouldUseServerActiveConversation())" in init_chat
    assert 'axios.get("/api/v1/chat/active"' in init_chat


def test_embedchat_scopes_server_active_conversation_by_instance_id():
    source = _source(EMBED_CHAT)
    active_update = source[source.index("const updateActiveConversationOnServer"):source.index("const generateNewConversation")]
    init_chat = source[source.index("const initChat = async"):source.index("// History State")]

    assert "const activeConversationRequestParams = () =>" in source
    assert "instance_id: config.instanceId" in source
    assert "params: activeConversationRequestParams()" in active_update
    assert "params: activeConversationRequestParams()" in init_chat


def test_embedchat_passes_current_conversation_to_message_renderer():
    embed_source = _source(EMBED_CHAT)
    renderer_source = _source(ROOT / "frontend/src/components/MessageRenderer.vue")

    assert embed_source.count(':conversation-id="conversationId"') >= 2
    assert "conversationId?: string;" in renderer_source
    # 预览 URL 只用调用方传入的会话 ID；不得回退读没有用户维度的旧键
    # （`yovole_embed_conv_id` 已按「只清理、不读取」处理）。
    assert "const convId = props.conversationId || \"\";" in renderer_source
    assert 'getItem("yovole_embed_conv_id")' not in renderer_source
    assert "getItem('yovole_embed_conv_id')" not in renderer_source


def test_embedchat_normalizes_instance_ids_before_postmessage_filtering():
    source = _source(EMBED_CHAT)

    assert "const messageInstanceId = normalizeEmbedInstanceId(data.instance_id);" in source
    assert "messageInstanceId !== config.instanceId" in source
    assert "normalized.length <= 128" not in source


def test_mainline_chat_keeps_legacy_init_config_without_instance_id():
    source = _source(CHAT)

    assert "type: 'INIT_CONFIG'" in source
    assert "conversation_id:" in source
    assert "instance_id:" not in source
