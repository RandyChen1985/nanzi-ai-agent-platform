"""SystemConfig 配置分组折叠与批次展开/收起契约。

新「参数配置」布局为左侧分组导航栏 + 右侧当前组单面板：
- 保留单个分组的折叠（收起/展开）能力；
- 不再提供「全部展开 / 全部折叠」按钮（原纵向堆叠 8 组卡片的设计已移除，
  改为按组聚焦导航，故批次展开/收起失去意义）。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]
SYSTEM_CONFIG = ROOT / "frontend/src/views/SystemConfig.vue"


def test_system_config_single_group_collapse_controls_retained():
    source = SYSTEM_CONFIG.read_text(encoding="utf-8")

    assert "collapsedConfigGroups" in source
    assert "toggleConfigGroup" in source
    assert "isConfigGroupCollapsed(String(category))" in source
    assert "aria-expanded" in source


def test_system_config_batch_expand_collapse_removed():
    source = SYSTEM_CONFIG.read_text(encoding="utf-8")

    # 向左分组导航 + 单面板改造后，全部展开/折叠不再需要
    assert "expandAllConfigGroups" not in source
    assert "collapseAllConfigGroups" not in source
    assert "全部展开" not in source
    assert "全部折叠" not in source


def test_system_config_group_focus_rail_present():
    source = SYSTEM_CONFIG.read_text(encoding="utf-8")

    # 左侧分组导航聚焦逻辑
    assert "selectConfigCategory" in source
    assert "realizedActiveCategory" in source
    assert "md:grid-cols-[220px_minmax(0,1fr)]" in source
    # 切换分组时自动展开该组，避免看到空收起卡片
    assert "next.delete(cat)" in source