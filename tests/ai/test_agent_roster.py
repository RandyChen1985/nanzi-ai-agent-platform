from types import SimpleNamespace
import pytest

from app.services.ai.agent_roster import (
    AGENT_ROSTER_PLACEHOLDER,
    format_agent_roster_markdown,
    inject_agent_roster,
)

pytestmark = pytest.mark.no_infrastructure


def _agent(name, display_name, description):
    return SimpleNamespace(
        name=name,
        display_name=display_name,
        description=description,
        capabilities=["cap1"],
    )


def test_format_agent_roster_includes_delegable_and_current():
    roster = format_agent_roster_markdown(
        [_agent("chat-bi", "数据智能助手", "专注 SQL 与报表")],
        current_display_name="主助手(Main)",
        current_description="通用问答兜底",
    )
    assert "**数据智能助手**（`chat-bi`）" in roster
    assert "专注 SQL 与报表" in roster
    assert "**主助手(Main)（当前）**" in roster
    assert "通用问答兜底" in roster


def test_inject_agent_roster_replaces_placeholder():
    prompt = f"欢迎。\n{AGENT_ROSTER_PLACEHOLDER}\n结束。"
    result = inject_agent_roster(prompt, "   - **A**：desc")
    assert AGENT_ROSTER_PLACEHOLDER not in result
    assert "**A**：desc" in result


def test_inject_agent_roster_noop_without_placeholder():
    prompt = "无占位符"
    assert inject_agent_roster(prompt, "x") == prompt
