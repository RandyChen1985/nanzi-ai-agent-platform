"""智能体中心的可见性与空态口径。

普通用户只看得到自己创建的智能体：别人创建的（含系统智能体）不出现，避免被整份
复刻。列表空时要说清原因，否则用户会以为系统坏了或权限丢了。
"""
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/AgentManagement.vue"
MANAGER = ROOT / "app/services/ai/agent_manager.py"


def test_list_filter_ignores_is_system():
    """可见性按 created_by 判定：自己创建后被动提升为系统级的仍要能看见。"""
    source = MANAGER.read_text(encoding="utf-8")
    list_block = source.split("async def list_agents", 1)[1].split("async def ", 1)[0]

    assert "if not is_admin and agent.created_by != username:" in list_block
    # 不能再用 is_system 参与可见性
    assert "not agent.is_system and agent.created_by" not in list_block


def test_duplicate_rejects_foreign_system_agent():
    """界面看不到不等于接口安全：id 会出现在对话/Embed 链接里，必须自己挡。"""
    source = MANAGER.read_text(encoding="utf-8")
    dup_block = source.split("async def duplicate_agent", 1)[1].split("async def ", 1)[0]

    assert "if source.is_system and not is_admin and source.created_by != username:" in dup_block
    assert "status_code=403" in dup_block


def test_empty_state_explains_why_list_is_empty():
    source = PAGE.read_text(encoding="utf-8")

    assert "const emptyStateMessage = computed" in source
    # 无筛选 + 非管理员：说明系统内置由管理员维护，去对话里用
    assert "你还没有创建智能体。系统内置智能体由管理员维护，已授权的可以直接在对话里使用。" in source
    assert 'return "没有找到匹配的智能体"' in source
    # 模板必须真的用它，而不是留着旧的硬编码
    assert "{{ emptyStateMessage }}" in source
