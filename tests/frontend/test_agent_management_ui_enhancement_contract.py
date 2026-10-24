from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
AGENT_MGMT = ROOT / "frontend/src/views/AgentManagement.vue"

pytestmark = pytest.mark.no_infrastructure


def agent_mgmt_source():
    return AGENT_MGMT.read_text(encoding="utf-8")


def test_agent_management_uses_filter_panel_instead_of_tab_row():
    """7 个分段 tab 已取消，筛选维度全部收进「筛选」面板。

    取消的原因：tab 里「通用助手/ChatBI/知识库」与「类型」下拉是同一维度，
    两处都能选，最容易出现「tab 选知识库、下拉又选 ChatBI → 结果全空」。
    """
    source = agent_mgmt_source()

    assert "filterPanelOpen" in source
    # 五个维度都在面板里
    for ref_name in ("statusFilter", "typeFilter", "engineFilter", "readinessFilter", "sourceFilter"):
        assert ref_name in source, f"缺少筛选维度 {ref_name}"
    # 旧的「按状态/类型分段筛选」tab 行彻底移除
    assert "segmentedFilter" not in source
    assert "segmentTabs" not in source
    # 但视图切换条（我的 / 分享给我 / 我分享的）本身是 tablist 语义，必须保留
    # 无障碍属性；它切的是数据源，不是筛选维度，与上面禁掉的分段筛选不是一回事
    assert 'role="tablist"' in source
    assert 'role="tab"' in source
    assert ':aria-selected="workspaceTab === tab.key"' in source
    # 条件收起后要能回显，否则用户会以为智能体丢了
    assert "activeFilterChips" in source
    assert "清除全部" in source


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
