"""SystemConfig 配置分组折叠与批次展开/收起契约。

新「参数配置」布局为左侧分组导航栏 + 右侧当前组单面板：
- 右侧单面板头部已去掉「展开/收起」折叠按钮，正文始终展开（避免切组后又要点展开的冗余交互）；
- 不再提供「全部展开 / 全部折叠」按钮（原纵向堆叠 8 组卡片的设计已移除，
  改为按组聚焦导航，故批次展开/收起失去意义）；
- 左侧导航切换分组仍自动聚焦选中分组。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]
SYSTEM_CONFIG = ROOT / "frontend/src/views/SystemConfig.vue"


def test_system_config_single_group_collapse_controls_removed():
    """右侧单面板头部不再有「展开/收起」折叠按钮，正文始终展开。"""
    source = SYSTEM_CONFIG.read_text(encoding="utf-8")

    # 折叠按钮相关的交互控件应已移除
    assert "toggleConfigGroup" not in source
    assert "isConfigGroupCollapsed" not in source


def test_system_config_single_panel_always_expanded():
    """正文不再由折叠状态控制，始终渲染。"""
    source = SYSTEM_CONFIG.read_text(encoding="utf-8")

    assert "config-group-body" in source
    # 正文不应再有 v-if="!isConfigGroupCollapsed(...)" 之类的折叠控制
    assert "!isConfigGroupCollapsed" not in source


def _configs_block() -> str:
    """「参数配置」tab 的模板段——本文件的折叠契约只针对它。"""
    source = SYSTEM_CONFIG.read_text(encoding="utf-8")
    start = source.index("<div v-else-if=\"activeTab === 'configs'\"")
    return source[start:]


def test_system_config_batch_expand_collapse_removed():
    block = _configs_block()

    # 向左分组导航 + 单面板改造后，「参数配置」页不再需要全部展开/折叠。
    # 作用域限定在参数配置 tab：「系统诊断」页的 Redis Key 业务分组另有一套
    # 自己的「全部展开/收起」，属于不同功能，不应被这条契约误伤。
    assert "expandAllConfigGroups" not in block
    assert "collapseAllConfigGroups" not in block
    assert "全部展开" not in block
    assert "全部折叠" not in block


def test_system_config_group_focus_rail_present():
    source = SYSTEM_CONFIG.read_text(encoding="utf-8")

    # 左侧分组导航聚焦逻辑
    assert "selectConfigCategory" in source
    assert "realizedActiveCategory" in source
    assert "md:grid-cols-[220px_minmax(0,1fr)]" in source
    # 切换分组时自动展开该组，避免看到空收起卡片
    assert "next.delete(cat)" in source