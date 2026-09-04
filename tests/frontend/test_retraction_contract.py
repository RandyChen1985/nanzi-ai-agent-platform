from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_frontend_retraction_replaces_visible_answer_body():
    handlers = (ROOT / "frontend/src/utils/agentscopeSseHandlers.ts").read_text(encoding="utf-8")
    embed_chat = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    agent_debug = (ROOT / "frontend/src/views/AgentDebug.vue").read_text(encoding="utf-8")

    assert 'eventType === "retraction"' in handlers
    assert "msg.content =" in handlers
    assert 'data.type === "retraction"' in embed_chat
    assert 'data.type === "retraction"' in agent_debug
