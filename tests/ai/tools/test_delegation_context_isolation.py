"""委派链的上下文隔离契约。

子代理运行在同一 task 里，`setup_context` 会把 `execution_options` 的 ContextVar
覆盖成自己那一份。只恢复 `agent_context` 是不够的：主智能体随后读到的
`execution_options` 仍是子代理的，于是「委派过一次」的请求里，主智能体自己的
`ask_user_question` 会被误判成无人值守上下文而被拒。

这里不跑完整委派链路（成本过高），只锁住三处关键代码不被误删。
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

SOURCE = (Path(__file__).resolve().parents[3] / "app/services/ai/tools/agent_delegate_tool.py").read_text(
    encoding="utf-8"
)


def test_delegation_marks_sub_agent_as_non_interactive():
    """子代理必须带上委派标记，否则判定不出「没有可回传的交互通道」。"""
    assert 'execution_options={"delegated_sub_agent": True}' in SOURCE


def test_delegation_saves_execution_options_before_switching_context():
    assert "original_execution_options = dict(execution_options_context.get() or {})" in SOURCE


def test_delegation_restores_execution_options_with_agent_context():
    """恢复必须成对：只恢复 agent_context 会留下跨轮次的误判。"""
    assert (
        "set_agent_context(original_ctx)\n"
        "        set_execution_options_context(original_execution_options)"
    ) in SOURCE
