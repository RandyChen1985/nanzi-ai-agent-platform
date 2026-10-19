from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _embed() -> str:
    return (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")


def _popover() -> str:
    return (ROOT / "frontend/src/components/chat/ActiveRunsPopover.vue").read_text(encoding="utf-8")


def test_active_run_views_carry_agent_label():
    """运行锁里只有 trace_id，智能体只能靠会话级 routing 记录补出来。"""
    source = _embed()
    assert "const activeRunAgentLabels" in source
    assert "agent_label:" in source


def test_agent_label_reads_conversation_routing():
    source = _embed()
    assert "/routing`" in source
    assert 'let label = "自动委派";' in source


def test_agent_label_never_blocks_the_panel():
    """查不到就留空，面板其余信息（标题、剩余时间、终止）必须照常可用。"""
    source = _embed()
    assert "activeRunAgentLabelAttempted" in source


def test_popover_renders_optional_agent_label():
    source = _popover()
    assert "agent_label?: string" in source
    assert "item.agent_label" in source
