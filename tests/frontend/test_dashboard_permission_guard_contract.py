from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure


def test_router_falls_back_to_first_allowed_menu_instead_of_no_permission():
    source = Path("frontend/src/router/index.ts").read_text()

    assert "resolveFirstAllowedRoute" in source
    assert "MENU_HOME_CANDIDATES" in source
    assert "{ perm: 'menu:ai_chat', name: 'PersonalWorkbench' }" in source
    assert "缺目标页权限时落到第一个有权限的页面" in source
    assert "next({ name: 'Overview' }) // Fallback to overview" not in source
    assert "如果访问首页没权限，尝试重定向到第一个有权限的菜单" not in source


def test_workbench_is_a_default_entry_without_menu_permission():
    router = Path("frontend/src/router/index.ts").read_text()
    dashboard = Path("frontend/src/views/Dashboard.vue").read_text()
    login = Path("frontend/src/views/Login.vue").read_text()

    assert "name: 'PersonalWorkbench'" in router
    assert "meta: { title: '我的工作台' }" in router
    assert "to.name === 'PersonalWorkbench'" in router
    assert "if (userData.role !== 'admin')" in login
    assert "router.push('/dashboard/workbench')" in login
    assert "router.push('/dashboard')" in login
    assert "{ name: '我的工作台', to: '/dashboard/workbench', icon: 'dashboard', activeNames: ['PersonalWorkbench'] }" in dashboard
    assert "perm?: string" in dashboard
    assert "if (!perm) return true" in dashboard
    # 系统配置在移动端侧栏可见（不再 desktopOnly）
    assert (
        "{ name: '系统配置', to: '/dashboard/system', icon: 'system', "
        "perm: 'menu:system:config', activeNames: ['System'] }"
    ) in dashboard
    assert "perm: 'menu:system:config', desktopOnly: true" not in dashboard


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
