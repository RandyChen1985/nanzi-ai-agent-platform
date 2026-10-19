"""左侧主导航折叠状态要按账号持久化并支持跨设备。

状态主存后端 Redis（``/api/portal/portal-prefs`` 的 ``left_sidebar_collapsed``），
localStorage 只在偏好接口不可用时做本机兜底。移动端是抽屉临时态，不覆盖桌面偏好。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "frontend/src/views/Dashboard.vue"


def _source() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


def _section(start: str, end: str) -> str:
    source = _source()
    return source[source.index(start):source.index(end)]


def test_left_sidebar_collapsed_state_is_saved_to_the_account_preference():
    source = _source()
    assert 'const LEFT_SIDEBAR_COLLAPSED_KEY = "embed_left_sidebar_collapsed";' in source
    assert "leftSidebarPreferenceApplied" in source
    assert "left_sidebar_collapsed === true" in source

    load = _section("const loadLeftSidebarPreference", "watch(isCollapsed")
    assert 'axios.get("/api/portal/portal-prefs")' in load
    assert "applyLeftSidebarPreference" in load

    save = _section("const scheduleLeftSidebarPreferenceSave", "const loadLeftSidebarPreference")
    assert '"/api/portal/portal-prefs/left-sidebar"' in save
    assert "collapsed" in save


def test_left_sidebar_toggle_triggers_the_account_save():
    watch = _section("watch(isCollapsed", "const toggleSidebar")
    assert "scheduleLeftSidebarPreferenceSave(collapsed)" in watch
    assert watch.index("localStorage.setItem") < watch.index(
        "scheduleLeftSidebarPreferenceSave"
    )


def test_left_sidebar_preference_is_loaded_on_mount_and_skips_mobile():
    source = _source()
    mounted = source[source.index("onMounted(async () => {"):]
    assert "await loadLeftSidebarPreference();" in mounted

    apply = _section("const applyLeftSidebarPreference", "const scheduleLeftSidebarPreferenceSave")
    assert "if (!isDesktopSidebar()) return;" in apply
    assert 'localStorage.setItem(LEFT_SIDEBAR_COLLAPSED_KEY' in apply

    save = _section("const scheduleLeftSidebarPreferenceSave", "const loadLeftSidebarPreference")
    assert "if (!isDesktopSidebar()) return;" in save
