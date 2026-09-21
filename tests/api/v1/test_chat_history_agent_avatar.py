"""历史消息必须带上智能体专属头像，供前端做「智能体头像 → 全局形象」继承。

只有后端补得了的场景：`/agents/allowed` 过滤了 `is_enabled=True`，已停用智能体的
历史消息在前端索引不到，只能靠历史接口下发 `agent_avatar_url`。
"""

import pytest

from app.api.v1.endpoints.chat import _agent_identity_fields, _apply_agent_identity
from app.schemas.agent import AgentExecutionHistoryResponse


pytestmark = pytest.mark.no_infrastructure


def _history_item(agent_id="a1"):
    return AgentExecutionHistoryResponse(
        id=1,
        trace_id="trace-1",
        agent_id=agent_id,
        status="success",
        created_at="2026-09-21T00:00:00",
    )


def test_execution_history_schema_exposes_agent_avatar_url():
    assert "agent_avatar_url" in AgentExecutionHistoryResponse.model_fields


def test_apply_agent_identity_backfills_avatar():
    item = _history_item()
    _apply_agent_identity(item, {"a1": ("kb", "知识库专家", "/branding/agent-avatars/a1_1.png")})

    assert item.agent_name == "kb"
    assert item.agent_display_name == "知识库专家"
    assert item.agent_avatar_url == "/branding/agent-avatars/a1_1.png"


def test_apply_agent_identity_keeps_avatar_empty_when_agent_has_none():
    item = _history_item()
    _apply_agent_identity(item, {"a1": ("chatbi", "数据智能助手", None)})

    # 为空表示「该智能体未单独设置头像」，前端据此继承全局官方形象
    assert item.agent_avatar_url is None


def test_apply_agent_identity_tolerates_legacy_two_element_map():
    """兼容只带 (slug, 显示名) 的旧映射，不能因为少一个元素就崩。"""
    item = _history_item()
    _apply_agent_identity(item, {"a1": ("kb", "知识库专家")})

    assert item.agent_name == "kb"
    assert item.agent_avatar_url is None


def test_apply_agent_identity_ignores_unknown_agent():
    item = _history_item(agent_id="missing")
    _apply_agent_identity(item, {"a1": ("kb", "知识库专家", "/x.png")})

    assert item.agent_name is None
    assert item.agent_avatar_url is None


def test_agent_identity_fields_shape_for_fallback_history():
    """conversation 兜底历史走 dict 形态字段，键名必须与前端映射一致。"""
    fields = _agent_identity_fields("a1", {"a1": ("kb", "知识库专家", "/branding/agent-avatars/a1_1.png")})

    assert fields == {
        "agent_name": "kb",
        "agent_display_name": "知识库专家",
        "agent_avatar_url": "/branding/agent-avatars/a1_1.png",
    }
    assert _agent_identity_fields(None, {"a1": ("kb", "知识库专家", "/x.png")}) == {
        "agent_name": None,
        "agent_display_name": None,
        "agent_avatar_url": None,
    }
