"""账户菜单（个人中心弹层）的静态契约。

专业模式与用户模式共用同一个 `AccountMenu`：两套侧栏只是触发位置不同，
菜单内容必须完全一致，否则切模式后同一个头像点出来的东西变了，用户会断片。
这里钉住四类边界：

1. 入口复用：两种模式的头像都开同一个菜单，不再直接跳个人中心；
2. 菜单项落点：个人中心 / 权限 / Token / 通知设置都走个人中心现有 Tab，
   外观在菜单内直接切主题，退出复用现有确认流程；
3. 模式切换：胶囊写入账号偏好后落到对应模式的主入口（用户模式→任务页，
   专业模式→落地页），避免停在侧栏里没有入口的页面上；
4. 弹层行为：点外部 / Esc 关闭，触发按钮自己负责 toggle，不被 document 监听闪关。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def menu() -> str:
    return _read("frontend/src/components/layout/AccountMenu.vue")


@pytest.fixture(scope="module")
def dashboard() -> str:
    return _read("frontend/src/views/Dashboard.vue")


@pytest.fixture(scope="module")
def sidebar() -> str:
    return _read("frontend/src/components/layout/UserModeSidebar.vue")


def test_both_modes_share_one_account_menu(menu: str, dashboard: str, sidebar: str) -> None:
    """两种模式只有一个弹层组件，且挂在侧栏外层（不属于某一个模式）。"""
    assert dashboard.count("<AccountMenu") == 1
    assert 'import AccountMenu from "../components/layout/AccountMenu.vue";' in dashboard
    # 弹层在用户模式/专业模式两段模板之外，才能被两边共用
    assert dashboard.index("<AccountMenu") > dashboard.index("</template>")

    assert "open-account-menu" in sidebar
    assert "open-account-menu" in dashboard
    assert "AccountMenu" in dashboard


def test_user_mode_avatar_opens_menu_instead_of_jumping(menu: str, sidebar: str) -> None:
    """用户模式头像不再直接跳个人中心。"""
    assert '(e: "open-account-menu"): void' in sidebar
    assert 'emit(\'open-account-menu\')' in sidebar
    assert 'emit(\'open-personal\')' not in sidebar
    assert "data-account-trigger" in sidebar
    assert 'aria-haspopup="menu"' in sidebar


def test_professional_avatar_opens_same_menu(dashboard: str) -> None:
    assert "const toggleAccountMenu = () => {" in dashboard
    assert '@click="toggleAccountMenu"' in dashboard
    assert 'aria-haspopup="menu"' in dashboard
    assert ':aria-expanded="accountMenuOpen"' in dashboard
    assert "data-account-trigger" in dashboard
    # 旧的直达个人中心函数已经不存在（避免两个入口行为不一致）
    assert "const openUserInfo" not in dashboard


def test_menu_items_point_at_existing_personal_tabs(menu: str) -> None:
    assert "go('/dashboard/personal')" in menu
    assert "go('/dashboard/personal?tab=permissions')" in menu
    assert "go('/dashboard/personal?tab=tokens')" in menu
    assert "go('/dashboard/personal?tab=notifications')" in menu
    assert 'emit(\'logout\')' in menu


def test_mode_switch_is_a_first_class_action(menu: str) -> None:
    """模式切换放在第一屏，用两枚胶囊表达当前态。"""
    assert 'role="menuitemradio"' in menu
    assert ':aria-checked="mode === option.value"' in menu
    assert "emit('switch-mode', option.value)" in menu
    assert "一步切换" in menu


def test_mode_switch_lands_on_mode_home(dashboard: str) -> None:
    """切完直接落到该模式的主入口。"""
    assert 'router.push(mode === "user" ? "/dashboard/chat" : homeRoute.value)' in dashboard
    assert "const handleAccountSwitchMode" in dashboard
    assert "void handleUserModeSwitchMode(mode)" in dashboard


def test_theme_can_be_switched_inline(menu: str) -> None:
    assert "emit('set-theme', option.value)" in menu
    assert ':aria-checked="theme === option.value"' in menu
    assert "themeOptions" in menu


def test_about_shows_version_and_reserves_changelog(menu: str, dashboard: str) -> None:
    """关于先展示版本；更新日志先留占位，后续再补数据源。"""
    assert "{{ version }}" in menu
    assert "更新日志：后续版本提供。" in menu
    assert ':version="displayVersion"' in dashboard
    assert ':repo-url="repoUrl"' in dashboard


def test_menu_closes_on_outside_click_and_escape(menu: str) -> None:
    assert "const onDocumentClick" in menu
    assert 'if (target.closest("[data-account-trigger]")) return' in menu
    assert 'event.key === "Escape"' in menu
    assert '@click="close"' in menu
    assert "document.addEventListener(\"click\", onDocumentClick)" in menu


def test_menu_closes_after_navigation(dashboard: str) -> None:
    """菜单项都会跳页，换页必须收掉弹层。"""
    assert "watch(() => route.fullPath" in dashboard
    assert "accountMenuOpen.value = false" in dashboard
    assert "const handleAccountNavigate" in dashboard


def test_mobile_renders_as_bottom_sheet(menu: str, dashboard: str) -> None:
    assert ':mobile="isMobile"' in dashboard
    assert "max-h-[86vh]" in menu
    assert "rounded-t-2xl" in menu
    assert "fixed inset-0 -z-10 bg-black/40" in menu


def test_menu_rows_define_their_own_layout(menu: str) -> None:
    """菜单行靠 .account-menu-item 做 flex 布局；缺了它按钮会退化成 inline，整块错位。"""
    assert ".account-menu-item {" in menu
    assert "display: flex;" in menu
    assert "align-items: center;" in menu
    assert 'class="account-menu-item"' in menu


def test_desktop_menu_closes_on_any_outside_click(menu: str) -> None:
    """聊天区是 iframe，点它不冒泡到父文档；桌面端要铺一层透明捕获层才能点哪都关。"""
    assert 'class="fixed inset-0 -z-10 bg-transparent"' in menu
    # 遮罩必须在菜单卡片之下、页面其他内容之上：靠父层的 z-60/z-70 与 -z-10 实现
    assert "fixed inset-0 -z-10 bg-transparent" in menu
    assert "@click=\"close\"" in menu
