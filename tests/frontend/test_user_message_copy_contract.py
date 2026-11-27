"""前端契约：用户消息气泡「复制」按钮仅复制用户正文，不带折叠的附加系统元数据。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
AGENT_DEBUG = ROOT / "frontend/src/views/AgentDebug.vue"


def test_embed_chat_user_copy_strips_system_metadata():
    source = EMBED_CHAT.read_text(encoding="utf-8")
    assert (
        '@click="copyMessage(splitUserMessageContent(msg.content).userPart || visibleStreamBody(msg))"'
        in source
    )


def test_agent_debug_user_copy_strips_system_metadata():
    source = AGENT_DEBUG.read_text(encoding="utf-8")
    assert (
        '@click="copyContent(splitUserMessageContent(msg.content).userPart || msg.content, $event)"'
        in source
    )
