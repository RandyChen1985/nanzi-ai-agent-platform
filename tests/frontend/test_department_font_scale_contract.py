"""部门页字号必须与用户管理 / 角色管理同档。

用户反馈"这个字体有点小"。量化对比后确认不是错觉：

| 元素 | 用户 / 角色页 | 部门页（改前） |
| --- | --- | --- |
| 表格容器 | 不设字号（继承全局） | `text-xs`，整张表被压到 12px |
| 单元格 | `text-sm`(14px) + `py-4` | 继承 12px + `py-2` |
| 表头 | `text-xs uppercase tracking-wider` + `py-3` | 12px 粗体，无大写与字距 |
| 搜索框 | `text-sm` | `text-xs` |

三个因素叠加：正文小 2px、行高矮一半、表头没有"表头感"。改法是对齐用户页的档位，
而不是到处无脑放大——辅助信息（徽标、时间、邮箱）仍保持 12px，否则密集表格会糊成一片。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend/src"
TREE = FRONTEND / "components/department/DepartmentTree.vue"
MEMBERS = FRONTEND / "components/department/DepartmentMembersPanel.vue"
INFO = FRONTEND / "components/department/DepartmentInfoPanel.vue"
PAGE = FRONTEND / "views/DepartmentManagement.vue"


def _first_line_containing(src: str, needle: str) -> str:
    for line in src.splitlines():
        if needle in line:
            return line
    raise AssertionError(f"未找到包含 {needle!r} 的行")


def test_member_table_is_not_shrunk_to_xs():
    """表格容器上挂 `text-xs` 会把整张表（含单元格）压到 12px。"""
    src = MEMBERS.read_text(encoding="utf-8")
    assert '<table class="min-w-full text-xs">' not in src
    assert '<table class="min-w-full">' in src


def test_member_table_cells_use_body_font():
    src = MEMBERS.read_text(encoding="utf-8")
    cell = _first_line_containing(src, 'class="px-4 py-3 text-sm"')
    assert "text-sm" in cell


def test_table_header_has_users_page_hierarchy():
    """用户页表头是 `text-xs uppercase tracking-wider`：小字号 + 大写 + 字距，
    这样"表头"和"内容"才有层级；全用粗体 12px 会显得又小又闷。"""
    src = MEMBERS.read_text(encoding="utf-8")
    header = _first_line_containing(src, "成员</th>")
    assert "uppercase" in header
    assert "tracking-wider" in header
    assert "text-xs" in header


def test_tree_nodes_use_body_font():
    """左树是主导航，字号应跟列表正文一致（12px 会明显比右侧表格小）。"""
    src = TREE.read_text(encoding="utf-8")
    node = _first_line_containing(src, "group flex cursor-pointer items-center")
    assert "text-sm" in node


def test_search_inputs_use_body_font():
    """placeholder 与 class 分属两行，取 placeholder 之后的标签片段来判断。"""
    src = MEMBERS.read_text(encoding="utf-8")
    for marker in ('placeholder="搜索姓名 / 账号 / 邮箱"',):
        start = 0
        while True:
            idx = src.find(marker, start)
            if idx < 0:
                break
            tag = src[idx : idx + 500]
            assert "text-sm" in tag, "搜索框应与列表正文同字号"
            start = idx + 1


def test_secondary_info_stays_small():
    """辅助信息不要跟着放大：邮箱、上次登录、人数徽标放大会把密集表格撑糊。"""
    src = MEMBERS.read_text(encoding="utf-8")
    assert "text-[11px]" in src or "text-xs" in src


def test_info_panel_labels_are_not_tiny():
    """表单标签从 10/11px 提到 12px —— 10px 的中文标签在 125% 缩放下几乎糊掉。"""
    src = INFO.read_text(encoding="utf-8")
    assert "text-[10px] font-bold" not in src
