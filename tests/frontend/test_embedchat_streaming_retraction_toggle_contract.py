from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_embedchat_exposes_session_level_streaming_retraction_toggle():
    embed_chat = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    chat_settings = (ROOT / "frontend/src/components/embed/ChatSettings.vue").read_text(
        encoding="utf-8"
    )

    assert "groundingBlockMode" in embed_chat
    assert 'groundingBlockMode: "strict_buffer"' in embed_chat
    assert 'yovole_grounding_block_mode' in embed_chat
    assert 'grounding_block_mode: config.groundingBlockMode' in embed_chat
    assert 'stream_with_retraction' in embed_chat
    assert "实时输出" in chat_settings
    assert "校验失败后撤回" in chat_settings
    assert "config.enableGrounding" in chat_settings
