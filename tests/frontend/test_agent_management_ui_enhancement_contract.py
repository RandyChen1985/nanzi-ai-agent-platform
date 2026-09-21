from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
AGENT_MGMT = ROOT / "frontend/src/views/AgentManagement.vue"

pytestmark = pytest.mark.no_infrastructure


def agent_mgmt_source():
    return AGENT_MGMT.read_text(encoding="utf-8")


def test_agent_management_has_segmented_filter_tabs():
    source = agent_mgmt_source()

    # 包含分段标签栏状态定义与分类
    assert "segmentedFilter" in source or "activeSegmentTab" in source
    assert "ready" in source
    assert "unready" in source
    assert "role=\"tablist\"" in source or "aria-label=\"智能体分类筛选\"" in source


def test_agent_management_has_externalized_preview_chat_button():
    source = agent_mgmt_source()

    # 卡片操作区外置「预览对话」快捷按钮
    assert "openPreview(agent)" in source
    assert "预览对话" in source
    # 必须在卡片动作区直接存在预览对话按钮（非仅在三点菜单中）
    assert "title=\"快速预览对话\"" in source or "title=\"预览对话\"" in source


def test_agent_management_has_readiness_checklist_popover():
    source = agent_mgmt_source()

    # 包含就绪度诊断浮窗逻辑与检查项
    assert "activeReadinessPopoverAgentId" in source or "readinessPopover" in source
    assert "就绪度诊断" in source or "配置就绪检查" in source
    assert "followReadinessGap" in source
