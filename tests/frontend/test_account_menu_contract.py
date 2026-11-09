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
    """模式切换放在第一屏，用两枚胶囊表达当前态；没权限的那枚置灰而不是消失。"""
    assert 'role="menuitemradio"' in menu
    assert ':aria-checked="mode === option.value"' in menu
    assert "emit('switch-mode', option.value)" in menu
    # 两枚胶囊都渲染出来，被角色禁用的那枚是真 disabled
    assert 'v-for="option in modeOptions"' in menu
    assert ':disabled="!isModeAllowed(option.value)"' in menu


def test_mode_switch_lands_on_mode_home(dashboard: str) -> None:
    """切完直接落到该模式的主入口。"""
    assert 'router.push(mode === "user" ? "/dashboard/chat" : homeRoute.value)' in dashboard
    assert "const handleAccountSwitchMode" in dashboard
    assert "void handleUserModeSwitchMode(mode)" in dashboard


def test_theme_can_be_switched_inline(menu: str) -> None:
    assert "emit('set-theme', option.value)" in menu
    assert ':aria-checked="theme === option.value"' in menu
    assert "themeOptions" in menu


def test_about_shows_version_and_links_changelog(menu: str, dashboard: str) -> None:
    """关于先展示版本；更新日志直链 GitHub Releases，dev 环境下入口也不消失。"""
    assert "{{ version }}" in menu
    assert ':version="displayVersion"' in dashboard
    assert ':repo-url="repoUrl"' in dashboard
    # 有仓库地址就给链接：能定位到 tag 就直达，定位不到就落到列表页
    assert ':href="changelogUrl"' in menu
    assert "releases/tag/${changelogTag.value}" in menu
    assert 'rel="noopener noreferrer"' in menu


def test_changelog_tag_uses_three_segments(menu: str) -> None:
    """1.0.22.0 的第 4 段是构建号，Release tag 只到三段。

    取前两段会拼出 /releases/tag/1.0，是必然 404 的地址。dev 与残缺版本号没有
    对应 tag，回退到 Releases 列表页——入口不该因为「不知道是哪个版本」就消失。
    """
    assert "parts.length > 3 ? parts.slice(0, 3) : parts" in menu
    assert "/^dev/i.test(raw)" in menu
    assert '"查看全部版本"' in menu
    assert "`${base}/releases`" in menu
    # 用 repoUrl 拼而不是硬编码 GitHub 前缀，白标部署换仓库地址时才能跟着走
    assert 'props.repoUrl.replace(/\\/+$/, "")' in menu


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
