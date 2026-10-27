"""个人资源条新增两项：与后端 PERSONAL_RESOURCE_DEFS 对齐，并处理 embed 场景。"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
CONST = ROOT / "frontend/src/constants/personalResources.ts"
GRID = ROOT / "frontend/src/components/workbench/WorkbenchPersonalResources.vue"
KB_MGMT = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"


def _read(path: Path) -> str:
    assert path.exists(), f"文件不存在：{path}"
    return path.read_text(encoding="utf-8")


def test_frontend_defs_match_backend_order():
    const = _read(CONST)
    knowledge = const.index('key: "knowledge"')
    datasets = const.index('key: "datasets"')
    memory = const.index('key: "memory"')
    # 顺序必须与后端一致，否则卡片会按前端数组重排
    assert knowledge < datasets < memory


def test_square_items_hidden_in_embed_welcome():
    const = _read(CONST)
    assert "EMBED_WELCOME_HIDDEN_SQUARE_KEYS" in const
    assert 'new Set(["knowledge", "datasets"])' in const
    assert "EMBED_WELCOME_HIDDEN_SQUARE_KEYS.has(item.key)" in const


def test_modal_tabs_exclude_square():
    const = _read(CONST)
    assert 'spec.tab !== "square"' in const


def test_grid_uses_four_columns_for_eight_visible_cards():
    """工作台显示 8 张（站内消息由顶栏铃铛承担，已隐藏）。

    4 列正好两行；5 列会排成 5+3、第二行右侧空两格。
    """
    grid = _read(GRID)
    assert "xl:grid-cols-4" in grid
    assert "xl:grid-cols-5" not in grid
    assert "xl:grid-cols-6" not in grid


def test_new_cards_have_own_icons_not_the_fallback():
    """漏配图标会落到 default 分支——灰色感叹号，看起来像「出错了」。"""
    grid = _read(GRID)
    assert "item.key === 'knowledge'" in grid
    assert "item.key === 'datasets'" in grid
    assert 'case "knowledge"' in grid
    assert 'case "datasets"' in grid


def test_value_and_unit_share_one_row():
    """单位单独占一行时卡片白高约 18px，两行卡就多出 36px。"""
    grid = _read(GRID)
    assert "items-baseline gap-1" in grid


def test_knowledge_management_points_to_square():
    page = _read(KB_MGMT)
    assert "我的工作台 › 资源广场" in page
