"""「会话状态已更新」（state reset）的触发边界。

前端展示的「会话状态已更新」就是后端这条 warning 级 log：

    title  = 智能体配置变更：历史会话状态已重置
    status = warning

它只在一种情况下发出：**上一轮成功保存了运行时状态，但这一轮的兼容性判定失败**。
判定入口是 ``RuntimeStateEnvelope.matches()``，只有三个条件：

1. ``schema_version`` 与当前代码的常量一致；
2. ``agent_name`` 相同；
3. ``tools_fingerprint`` 相同（指纹内含 agent_name / agent_version / model_name /
   稳定工具名集合，见 ``build_tools_fingerprint``）。

本文件把这三条判定边界、以及「助手 / ChatBI 两条执行路径文案必须一致」锁成契约：
文案或判定任一处漂移，前端的时间线映射就会只命中一半。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.ai.runtime.agentscope.state_store import (
    SCHEMA_VERSION,
    RuntimeStateEnvelope,
)

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
RESET_TITLE = "智能体配置变更：历史会话状态已重置"
RESET_DETAILS = "检测到绑定的工具集或模型配置发生改变，为防工具调用崩溃，已重置运行时状态。"


def _envelope(**overrides) -> RuntimeStateEnvelope:
    fields = {
        "schema_version": SCHEMA_VERSION,
        "agent_name": "main",
        "agent_version": "v1",
        "tools_fingerprint": "0123456789abcdef",
        "model_name": "deepseek-v4",
        "updated_at": "2026-11-03T00:00:00+00:00",
        "state": {"context": []},
    }
    fields.update(overrides)
    return RuntimeStateEnvelope(**fields)


def test_matches_when_nothing_changed():
    env = _envelope()
    assert (
        env.matches(tools_fingerprint=env.tools_fingerprint, agent_name=env.agent_name)
        is True
    )


@pytest.mark.parametrize("schema_version", [SCHEMA_VERSION - 1, SCHEMA_VERSION + 1, 0])
def test_schema_version_drift_invalidates_state(schema_version):
    """平台升级改了 SCHEMA_VERSION 后，旧结构的状态一律不可复用。"""
    env = _envelope(schema_version=schema_version)
    assert (
        env.matches(tools_fingerprint=env.tools_fingerprint, agent_name=env.agent_name)
        is False
    )


def test_agent_name_drift_invalidates_state():
    env = _envelope(agent_name="expert-a")
    assert env.matches(tools_fingerprint=env.tools_fingerprint, agent_name="expert-b") is False


def test_tools_fingerprint_drift_invalidates_state():
    """会话级挂载/卸载工具、换智能体版本都会改变指纹，最终落到这条判定上。"""
    env = _envelope(tools_fingerprint="old-fingerprint")
    assert env.matches(tools_fingerprint="new-fingerprint", agent_name=env.agent_name) is False


def test_matches_does_not_compare_version_or_model_directly():
    """版本/模型的变化必须**先体现到指纹里**才有判定力。

    这条锁住一个容易犯的实现错误：如果改成「matches 直接比 agent_version」，
    那么 ``agent_version`` 为空（不少智能体没版本号）时状态会被永久判为不匹配。
    """
    env = _envelope(agent_version=None, model_name=None)
    assert (
        env.matches(tools_fingerprint=env.tools_fingerprint, agent_name=env.agent_name)
        is True
    )


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_both_execution_paths_emit_identical_reset_event():
    """助手与 ChatBI 各有一份事件构造，文案必须完全一致。

    前端 ``processTimeline`` 是按 title 映射成「会话状态已更新」的，任一路径改了
    文案，那条路径的用户就再也看不到这张卡（而且不会有任何报错）。
    """
    for relative in (
        "app/services/ai/runners/assistant_agent_runner.py",
        "app/services/ai/runners/chatbi/native_turn.py",
    ):
        source = _source(relative)
        assert f'"{RESET_TITLE}"' in source, f"{relative} 的标题与前端映射不一致"
        assert f'"{RESET_DETAILS}"' in source, f"{relative} 的 details 与另一条路径不一致"
        assert '"status": "warning"' in source
        assert 'f"state_reset_{uuid.uuid4().hex[:8]}"' in source


def test_reset_event_is_emitted_only_on_mismatch():
    """匹配分支只做状态恢复，事件只能出现在 else（判定失败）里。"""
    source = _source("app/services/ai/runners/assistant_agent_runner.py")
    body = source[source.index("if persisted.matches("):]
    restore_at = body.index("AgentState.model_validate")
    else_at = body.index("else:")
    event_at = body.index(RESET_TITLE)
    assert restore_at < else_at < event_at


def test_reset_event_does_not_carry_fingerprint_details():
    """事件正文不能回显指纹：那里是内部实现细节，不是给用户看的排查信息。"""
    source = _source("app/services/ai/runners/assistant_agent_runner.py")
    body = source[source.index("if persisted.matches("):]
    event_block = body[body.index(RESET_TITLE): body.index(RESET_TITLE) + 600]
    assert "tools_fingerprint" not in event_block
