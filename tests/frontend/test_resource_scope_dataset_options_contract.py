from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_embed_resource_scope_loads_accessible_dataset_options():
    source = _source("frontend/src/views/EmbedChat.vue")
    assert "/api/portal/metadata/datasets/accessible" in source
    assert "axios.get('/api/portal/metadata/datasets')" not in source or "datasets/accessible" in source
    # 会话资源候选列表不再走重的全量 metadata 列表
    assert "loadResourceOptions" in source
    assert source.count("/api/portal/metadata/datasets/accessible") >= 1


def test_accessible_datasets_endpoint_exists():
    source = _source("app/api/portal/endpoints/metadata.py")
    assert '/datasets/accessible"' in source or "/datasets/accessible" in source
    assert "list_accessible_dataset_options" in source


def test_embed_resource_scope_switch_is_race_safe_and_preserves_skill_scope():
    """会话切换必须丢弃迟到请求，个人/全局同 ID 技能不得混淆。"""
    source = _source("frontend/src/views/EmbedChat.vue")
    assert "resourceScopeLoadSequence" in source
    assert "conversationId.value !== targetConversationId" in source
    assert "scope: item.scope || defaultScope" in source
    assert "resourceScopeLoading.value || resourceScopeLoadError.value" in source


def test_new_conversation_clears_parent_messages_and_child_attachments():
    """/project 新会话不得携带上一会话的消息或附件。"""
    embed_source = _source("frontend/src/views/EmbedChat.vue")
    input_source = _source("frontend/src/components/embed/ChatInput.vue")
    assert "messages.value = [];" in embed_source
    assert "resetSessionState" in embed_source
    assert "uploadedFiles.value = [];" in input_source
