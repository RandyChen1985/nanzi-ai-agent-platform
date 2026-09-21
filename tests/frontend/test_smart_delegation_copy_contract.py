"""智能委派模式的前端展示文案契约。"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_expert_selector_uses_smart_delegation_copy():
    mention_list = _read("frontend/src/components/agent/MentionList.vue")
    expert_menu = _read("frontend/src/components/embed/ExpertCascadeMenu.vue")
    chat_input = _read("frontend/src/components/embed/ChatInput.vue")

    for source in (mention_list, expert_menu):
        assert "智能委派" in source
        assert "由主助手直接处理，或按任务需要自动委派其他专家" in source
        assert "全能助手 (自动)" not in source
        assert "智能调度最合适的专家处理" not in source

    assert 'return "智能委派";' in chat_input
    assert "智能委派" in chat_input
    assert "全能助手（自动路由）" not in chat_input


def test_embed_auto_mode_hints_use_smart_delegation_copy():
    capsule = _read("frontend/src/components/embed/ExpertCapsule.vue")
    embed_chat = _read("frontend/src/views/EmbedChat.vue")

    assert "已切换为智能委派模式" in capsule
    assert "已切换为自动路由模式" not in capsule
    assert "已切换为智能委派模式" in embed_chat
    assert "无法切换到智能委派" in embed_chat
    assert "已切换为自动路由模式" not in embed_chat
    assert "无法切换到自动路由" not in embed_chat


def test_agent_debug_auto_mode_uses_smart_delegation_copy():
    agent_debug = _read("frontend/src/views/AgentDebug.vue")
    # Auto 模式的标签文案已收敛到共享 ChatInput（routingMode=auto 时显示“智能委派”）
    chat_input = _read("frontend/src/components/embed/ChatInput.vue")

    assert ":routing-mode=\"debugMode === 'specific' ? 'expert' : 'auto'\"" in agent_debug
    assert "已切换为智能委派模式" in agent_debug
    assert 'return "智能委派";' in chat_input
    assert "🤖 自动路由 (Auto)" not in agent_debug
    assert "已切换为自动路由模式" not in agent_debug
