"""凭据中心（个人端 / 管理端）的窄屏布局契约。

430px 以下的问题不是「不好看」，而是**读不出来**：工具栏整块是一个不换行的
flex，六个控件（搜索框 + 视图切换 + 三个中文按钮）挤在同一行，剩余宽度不够时
中文按钮从「变 / 更 / 记 / 录」逐字折行，竖排成一列——截图里「新建系统凭据」
被压成五个字一列，点都不敢点。

所以窄屏的底线：**搜索整行、按钮不折字、主操作独占一行**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
ADMIN = ROOT / "frontend/src/views/AdminSecretCenter.vue"
MINE = ROOT / "frontend/src/views/SecretCenter.vue"


@pytest.fixture(scope="module")
def admin() -> str:
    return ADMIN.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def mine() -> str:
    return MINE.read_text(encoding="utf-8")


def _toolbar(source: str, end_marker: str) -> str:
    """工具栏区域：从「工具栏」注释到下个区块的注释。"""
    start = source.index("<!-- 工具栏")
    end = source.index(end_marker, start)
    return source[start:end]


def _button_before(source: str, label: str) -> str:
    """取承载这段文字的那个 `<button ...>` 起始标签（文字在下一行，不能按 `>{label}` 找）。"""
    idx = source.index(label)
    return source[source.rindex("<button", 0, idx):idx]


def test_admin_toolbar_wraps_on_narrow_screens(admin: str) -> None:
    """工具区自己必须能换行，否则内部控件被压成竖排字。"""
    bar = _toolbar(admin, "<!-- 列表 -->")
    assert "flex w-full flex-wrap items-center gap-2 sm:w-auto" in bar


def test_admin_search_takes_a_whole_row_on_narrow_screens(admin: str) -> None:
    bar = _toolbar(admin, "<!-- 列表 -->")
    assert "w-full min-w-0 rounded-lg border border-gray-300 px-3 py-1.5 text-sm" in bar
    assert "sm:w-52" in bar


@pytest.mark.parametrize("label", ["变更记录", "使用记录", "新建系统凭据"])
def test_admin_buttons_never_break_into_vertical_text(admin: str, label: str) -> None:
    """`whitespace-nowrap` 是这条 bug 的直接解药：没有它，中文按钮会逐字换行。"""
    button = _button_before(_toolbar(admin, "<!-- 列表 -->"), label)
    assert "whitespace-nowrap" in button, f"「{label}」缺 whitespace-nowrap，窄屏会变竖排"


def test_admin_primary_action_owns_a_row_on_narrow_screens(admin: str) -> None:
    """「新建系统凭据」是这页唯一的写操作，挤在中间行最容易被压。"""
    button = _button_before(_toolbar(admin, "<!-- 列表 -->"), "新建系统凭据")
    assert "w-full" in button
    assert "sm:w-auto" in button


def test_personal_toolbar_search_owns_a_row_on_narrow_screens(mine: str) -> None:
    bar = _toolbar(mine, "<!-- 安全提示 -->")
    assert "flex w-full flex-wrap items-center gap-2 sm:w-auto" in bar
    assert "order-1 w-full min-w-0" in bar
    assert "sm:w-56" in bar


def test_personal_primary_action_stretches_next_to_help(mine: str) -> None:
    """窄屏第二行是「?（使用范围）」+「新建凭据」，新建吃满剩余宽度。"""
    bar = _toolbar(mine, "<!-- 安全提示 -->")
    assert "order-2 flex h-8 w-8 shrink-0" in bar
    assert "sm:order-1" in bar
    assert "sm:order-2" in bar
    assert "order-3 flex-1 whitespace-nowrap" in bar
    assert "sm:flex-none" in bar


def test_toolbars_are_not_one_unwrapping_row(admin: str, mine: str) -> None:
    """回归守卫：工具区容器不能退回「不换行的 flex」。

    以前是 `<div class="flex items-center gap-2">` 直接把搜索、视图切换和三个
    按钮串成一行——**这是 375~430px 上唯一会稳定复现的破版方式**。
    """
    for source, name in ((admin, "AdminSecretCenter"), (mine, "SecretCenter")):
        bar = _toolbar(source, "<!-- 列表 -->" if "AdminSecretCenter" in name else "<!-- 安全提示 -->")
        assert 'class="flex items-center gap-2">' not in bar, f"{name}: 工具区又变回不换行"
