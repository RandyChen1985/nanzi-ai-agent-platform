from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure


def test_router_falls_back_to_the_users_landing_menu_instead_of_no_permission():
    """落地页不再写死优先级清单，改由菜单表的 `is_landing` 下发。

    改造前 `MENU_HOME_CANDIDATES` 把「智能助手 → 我的工作台」钉在代码里，菜单入库后
    这个顺序是数据而不是代码：后端随 `/menus/nav` 下发 `landing_path`，守卫只读本地快照。
    守卫必须同步返回，所以这里同时钉住"没有快照时的兜底"是工作台而不是白屏。
    """
    source = Path("frontend/src/router/index.ts").read_text()

    assert "resolveFallbackPath" in source
    assert "getSnapshotLandingPath()" in source
    assert "return '/dashboard/workbench'" in source
    # 旧的写死清单必须彻底退场，否则两套规则会互相打架
    assert "MENU_HOME_CANDIDATES" not in source
    assert "resolveFirstAllowedRoute" not in source
    assert "next({ name: 'Overview' }) // Fallback to overview" not in source


def test_workbench_is_a_default_entry_without_menu_permission():
    """「我的工作台」不带权限码，任何登录用户都能进——它仍是登录后的兜底落地页。"""
    router = Path("frontend/src/router/index.ts").read_text()
    dashboard = Path("frontend/src/views/Dashboard.vue").read_text()
    login = Path("frontend/src/views/Login.vue").read_text()

    assert "name: 'PersonalWorkbench'" in router
    assert "meta: { title: '我的工作台' }" in router
    assert "to.name === 'PersonalWorkbench'" in router
    assert "if (userData.role !== 'admin')" in login
    assert "router.push('/dashboard/workbench')" in login
    assert "router.push('/dashboard')" in login

    # 侧栏不再持有任何菜单文案：数据来自 /menus/nav
    assert "useNavigation" in dashboard
    assert "visibleNavNodes" in dashboard
    assert "我的工作台" not in dashboard
    assert "menuGroups" not in dashboard


def test_mobile_visibility_comes_from_data_not_hardcoded_flags():
    """移动端可见性由菜单表的 `desktop_only` 决定，前端只按字段过滤。

    改造前这是 `desktopOnly: true` 常量；现在系统配置对移动端可见、数据源/元数据仍仅桌面端，
    这些差异必须落在种子数据里，而不是散落在模板条件中。
    """
    from app.services.menu_seed import BUILTIN_BY_CODE

    dashboard = Path("frontend/src/views/Dashboard.vue").read_text()

    assert "desktopOnly" not in dashboard
    assert "child.desktop_only && isMobile.value" in dashboard
    assert BUILTIN_BY_CODE["menu:system:config"].desktop_only is False
    assert BUILTIN_BY_CODE["menu:data_sources"].desktop_only is True
    # 元数据管理在移动端仍可见（原侧栏就没有限制），只有数据源是纯桌面操作
    assert BUILTIN_BY_CODE["menu:metadata"].desktop_only is False


def test_no_permission_page_refetches_me_before_giving_up():
    source = Path("frontend/src/views/NoPermission.vue").read_text()

    assert "/api/portal/auth/me" in source
    # 统一走 userSession 入口写回权限快照，保证 user_info 不携带 API Key
    assert "persistUserInfo(userData)" in source
    assert "PersonalWorkbench" in source
    assert "window.location.reload()" not in source


def test_online_users_widget_hidden_for_non_admin():
    """普通用户顶栏不展示在线人数与分隔线；仅管理员可见并拉取列表。"""
    dashboard = Path("frontend/src/views/Dashboard.vue").read_text(encoding="utf-8")

    assert 'v-if="userInfo.role === \'admin\'"' in dashboard
    assert "online-users-widget" in dashboard
    assert "online-users-divider" in dashboard
    assert 'if (userInfo.value.role === "admin")' in dashboard
    assert "fetchOnlineUsers()" in dashboard


def test_online_users_refresh_and_render_with_stable_identity():
    """在线人数应定期刷新，列表不能用可变用户名作为重复键。"""
    dashboard = Path("frontend/src/views/Dashboard.vue").read_text(encoding="utf-8")

    assert "onlineUsersRefreshTimer" in dashboard
    assert "setInterval" in dashboard
    assert "60_000" in dashboard
    assert "clearInterval" in dashboard
    assert ':key="user.user_id || user.user_name"' in dashboard
