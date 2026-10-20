"""用户管理页顶部布局：筛选条必须独占一行。

现象：搜索框 + 身份 + 状态 + 部门 + 含子部门 + 清除 + 刷新 + 更多 + 创建用户
挤在标题右侧同一行，宽度不够后折成两行——「清除/刷新/更多/创建用户」掉到第二行，
「含子部门」还溢出了右边界。

根因是结构，不是间距：标题区占了约 300px，剩下的宽度放不下约 900px 的控件。
所以正解是**把筛选条从 header 里摘出来独占一行**，而不是把控件越缩越小
（缩小只是把折行点往后推，换个窗口宽度又会折）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
USERS = (ROOT / "frontend/src/views/Users.vue").read_text(encoding="utf-8")

def _split():
    """惰性切分：标记还没出现时要给出可读的失败，而不是收集期 IndexError。"""
    assert "<!-- 筛选条" in USERS, "筛选条还没从 header 里拆出来"
    header = USERS.split("<!-- Header", 1)[1].split("<!-- 筛选条", 1)[0]
    filter_bar = USERS.split("<!-- 筛选条", 1)[1].split("<!-- 视图切换", 1)[0]
    return header, filter_bar


def test_header_holds_only_title_and_primary_action():
    """header 里只留标题和「创建用户」——创建是主操作，固定右上不会随筛选折行。"""
    header, _ = _split()
    assert "创建用户" in header
    assert 'type="search"' not in header, "搜索框必须移出 header"


def test_filter_bar_owns_all_filter_controls():
    _, filter_bar = _split()
    for control in ('type="search"', "含子部门", "清除", "更多"):
        assert control in filter_bar, f"筛选控件 {control} 应该在筛选条里"


def test_filter_bar_can_wrap_and_push_actions_right():
    _, filter_bar = _split()
    assert "flex-wrap" in filter_bar
    assert "ml-auto" in filter_bar, "清除/刷新/更多 靠右，折行后也保持右侧对齐"
