from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]
MESSAGE_RENDERER = ROOT / "frontend/src/components/MessageRenderer.vue"
EMBED = ROOT / "frontend/src/views/EmbedChat.vue"
CHAT_ENDPOINT = ROOT / "app/api/v1/endpoints/chat.py"


def test_quick_links_can_carry_hidden_chatbi_result_context_without_changing_label():
    source = MESSAGE_RENDERER.read_text(encoding="utf-8")

    assert "quickContext" in source
    assert "question: string" in source
    assert "quick_context" in source
    assert "normalizedQuestion = question.trim()" in source
    assert "props.quickContext" in source


def test_embed_chat_only_marks_verified_data_messages_and_forwards_context():
    source = EMBED.read_text(encoding="utf-8")

    assert "interface QuickQuestionContext" in source
    assert "quickContextForMessage" in source
    assert ":quick-context=\"quickContextForMessage(msg)\"" in source
    assert "requires_fresh_data: true" in source
    assert "hasDataOutput" in source
    assert "quick_context" in source


def test_chat_api_accepts_internal_quick_result_context_as_top_level_metadata():
    source = CHAT_ENDPOINT.read_text(encoding="utf-8")

    assert "class ChatBIQuickContext(BaseModel)" in source
    assert "quick_context: Optional[ChatBIQuickContext]" in source
    assert "quick_context=quick_context" in source
