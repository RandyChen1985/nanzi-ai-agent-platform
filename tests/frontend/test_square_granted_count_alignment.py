"""「已授权」视图的数字必须与工作台卡片对得上。

用户问题的原话：「如果是从工作台我的知识库、数据集点过去，默认已经 tab 页面是
已授权的，不然显示数量会对不齐，现在是显示全部的」。

对不齐有两个来源，一个是前端（跳过去没带筛选），一个是后端（候选集口径不同）：

1. 工作台「我的知识库」的数字是 `len(accessible_ids)`，**含自己创建的私有库**；
   而广场默认只列 `public`/`team`。所以即便前端默认筛「已授权」，
   普通用户看到 3 与 2 两个数字仍然对不上。
2. 跳转本身之前没带 `status`，落到广场就是「全部」。

下面把两处都锁住：前端必须带 `status=has` 并在落地时读它，后端在
`status=has` 时必须把「我有权限的库」全取出来，不能只取授权可见/TEAM 级。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
WORKBENCH = ROOT / "frontend/src/views/PersonalWorkbench.vue"
PANEL = ROOT / "frontend/src/components/resource-square/ResourceSquarePanel.vue"
CENTER = ROOT / "frontend/src/views/PersonalCenter.vue"
BACKEND = ROOT / "app/api/portal/endpoints/resource_square.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_workbench_card_jump_carries_granted_filter():
    """工作台卡片写着「我有权限的 N 个」，跳过去就必须默认只看已授权。"""
    src = _read(WORKBENCH)
    assert 'tab: "square"' in src
    assert 'status: "has"' in src


def test_square_panel_reads_status_from_url():
    """落地时要真的读 URL 上的 status，否则前面带了也白带。"""
    src = _read(PANEL)
    assert "route.query.status" in src
    assert "STATUS_FILTERS" in src
    assert "stateFilter = ref<SquareStateFilter>(" in src


def test_backend_granted_view_includes_private_own_bases():
    """`status=has` 时若仍只查 public/team，工作台 3 vs 广场 2 的老问题会回来。"""
    src = _read(BACKEND)
    assert 'if status == "has":' in src
    # 按「我有权限的库」取，而不是按可见性取
    assert "ragflow_dataset_id.in_(list(accessible))" in src
    # 管理员口径是「库里还剩多少个」，因此不受可见性限制
    assert "if is_admin:" in src


def test_backend_private_bases_stay_hidden_outside_granted_view():
    """私有库的存在性不能泄露：非 has 视图必须仍然只查 public/team。"""
    src = _read(BACKEND)
    start = src.index('if status == "has":')
    else_at = src.index("    else:\n", start)
    else_branch = src[else_at:]
    assert 'visibility.in_(["public", "team"])' in else_branch


def test_personal_center_knowledge_grant_goes_to_square_not_blank_page():
    """已授权的知识库不再跳只读概览（当前点开是空白）。"""
    src = _read(CENTER)
    assert "status: 'has'" in src
    # 数据集仍可跳概览页
    assert "name: 'ResourceSquareDataset'" in src
