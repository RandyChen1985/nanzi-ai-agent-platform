"""移动端「用户管理 / 角色管理」的搜索框与操作按钮必须同一行。

## 现象

窄屏下工具栏被拆成两行：搜索框独占一行，「筛选 / 刷新」被挤到下一行的最左边
（角色管理里只剩一个孤零零的刷新按钮），既浪费垂直空间，也让筛选/刷新看起来像
脱离了搜索的独立区块。

## 根因

两个页面的工具栏都给了搜索框 `w-full`（角色管理更是把容器写成 `flex-col sm:flex-row`），
于是窄屏下搜索必然占满整行、按钮只能折行。

## 修复约定

1. 搜索容器在窄屏用 `flex-1` 参与同一行分配，并保留 `min-w-[7rem]` 作为可读底线；
   `sm:` 起再退回固定宽度，桌面端布局不变。
2. 按钮侧容器必须是横向 `flex-wrap`（不再有 `flex-col`），按钮维持 `shrink-0`，
   窄屏优先压缩搜索框而不是把按钮换行。
3. 只有控件总宽连搜索底线都放不下时才折行——这是兜底，不再是默认形态。
"""

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _normalized(path: str) -> str:
    return re.sub(r"\s+", " ", _read(path))


def test_users_toolbar_keeps_search_and_buttons_on_one_row():
    source = _normalized("frontend/src/views/Users.vue")

    # 搜索：窄屏 flex-1 可收缩，sm 起固定宽度（桌面端视觉不变）
    assert "relative min-w-[7rem] flex-1 sm:w-56 sm:flex-none lg:w-64" in source
    # 旧的整行宽度会强制搜索独占一行
    assert "relative w-full sm:w-56 lg:w-64" not in source
    # 按钮侧容器保持横向 wrap，不能退化成纵向堆叠
    assert "flex flex-wrap items-center gap-2.5 sm:gap-3" in source


def test_roles_toolbar_keeps_search_and_buttons_on_one_row():
    source = _normalized("frontend/src/views/Roles.vue")

    assert "relative min-w-[7rem] flex-1 sm:w-64 sm:flex-none lg:w-72" in source
    assert "relative w-full sm:w-64 lg:w-72" not in source
    # 窄屏曾是 flex-col：搜索一行、按钮一行，正是本次要消除的形态
    assert "flex flex-col sm:flex-row sm:items-center gap-2.5 sm:gap-3" not in source
    assert "flex flex-wrap items-center gap-2.5 sm:gap-3" in source


def test_roles_toolbar_buttons_do_not_pin_themselves_to_the_top():
    """纵向堆叠时代留下的 `self-start` 会让按钮在横排工具栏里贴着顶部、与搜索框错位。

    只看工具栏那一段：「缺失 default 角色」提示条里的按钮仍处在纵向容器中，
    那里的 `self-start` 是正确用法，不能一并要求删掉。
    """
    source = _normalized("frontend/src/views/Roles.vue")
    toolbar = source[
        source.index("flex flex-wrap items-center gap-2.5 sm:gap-3") :
        source.index("<!-- Default Role Hint")
    ]

    assert "self-start sm:self-auto" not in toolbar
