"""用户管理页顶部布局：与角色管理一致，标题与搜索/筛选/操作同一行（lg 以上）。

历史：三个并排 select 时控件总宽接近 900px，再加标题区约 300px，一行放不下，
于是把筛选条拆出去独占一行。身份/状态/部门合并成一个「筛选」按钮后控件降到约 600px，
单行重新成立，因此按用户要求改回角色管理的结构：

1. 标题与控件组在同一个 Header 里，lg 以上横排；
2. 搜索框、筛选、刷新、更多、创建用户同属一个控件组，不再有独立筛选条；
3. 控件组允许 flex-wrap：窄屏时在右侧区域内折行，而不是把整个顶栏撑成两段。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
USERS = (ROOT / "frontend/src/views/Users.vue").read_text(encoding="utf-8")


def _header() -> str:
    """Header 片段：从 Header 注释到视图切换。"""
    assert "<!-- Header" in USERS, "用户页缺少 Header 注释标记"
    return USERS.split("<!-- Header", 1)[1].split("<!-- 视图切换", 1)[0]


def test_title_and_controls_share_one_header():
    """标题与搜索/筛选/操作必须在同一个 Header 里（lg 以上同一行）。"""
    header = _header()

    assert "用户管理" in header
    for control in ('type="search"', "toggleFilterPanel", 'title="刷新列表"', "更多", "创建用户"):
        assert control in header, f"Header 里缺少控件 {control}"

    assert "lg:flex-row" in header and "lg:justify-between" in header, (
        "Header 要在 lg 以上横排，标题与控件组同一行"
    )


def test_no_separate_filter_bar_returns():
    """旧的独立筛选条容器不得回归（那正是用户反馈的两行布局）。"""
    assert "<!-- 筛选条" not in USERS


def test_controls_can_wrap_inside_header():
    """控件组自身允许折行：窄屏时在右侧区域内换行，而不是把页面撑宽。"""
    header = _header()
    assert "flex flex-wrap items-center gap-2.5 sm:gap-3" in header
