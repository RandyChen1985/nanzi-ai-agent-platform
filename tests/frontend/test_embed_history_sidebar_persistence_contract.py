"""会话历史侧栏开关要按账号持久化并支持跨设备。

开关主存后端 Redis（``/api/portal/portal-prefs`` 的 ``history_sidebar_open``），
localStorage 只在偏好接口不可用时做本机兜底。窄屏下侧栏是带遮罩的浮层，
不能自动弹开挡住内容，所以桌面端才恢复/保存。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"


def _source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def _section(start: str, end: str) -> str:
    source = _source()
    return source[source.index(start):source.index(end)]


def test_history_sidebar_open_state_is_saved_to_the_account_preference():
    source = _source()
    assert 'const HISTORY_SIDEBAR_OPEN_KEY = "embed_history_sidebar_open";' in source
    assert "let historySidebarPreferenceApplied = false;" in source
    assert "prefs.history_sidebar_open === true" in source
    assert "applyHistorySidebarPreference(" in source

    fetch_prefs = _section(
        "const fetchUserPortalPreferences",
        "const hasCustomMessageBorderPreference",
    )
    assert "historySidebarPreferenceApplied" in fetch_prefs
    assert "restoreHistorySidebarPreference();" in fetch_prefs

    save = _section(
        "const scheduleHistorySidebarPreferenceSave",
        "const historyList",
    )
    assert '"/api/portal/portal-prefs/history-sidebar"' in save
    assert "open" in save


def test_history_sidebar_toggle_triggers_the_account_save():
    watch = _section("watch(showHistorySidebar, (val) => {", "watch(historyKeyword")
    assert "scheduleHistorySidebarPreferenceSave(val)" in watch
    # 结果返回前先更新本地缓存，至少本机交互不丢
    assert watch.index("localStorage.setItem") < watch.index(
        "scheduleHistorySidebarPreferenceSave"
    )


def test_history_sidebar_persistence_skips_mobile_overlay():
    helper = _section(
        "const isHistorySidebarDesktop = () =>",
        "const restoreHistorySidebarPreference",
    )
    assert '!window.matchMedia("(max-width: 639px)").matches' in helper

    save = _section(
        "const scheduleHistorySidebarPreferenceSave",
        "const historyList",
    )
    assert "if (!isHistorySidebarDesktop()) return;" in save
    assert '"/api/portal/portal-prefs/history-sidebar"' in save


def test_history_sidebar_local_cache_is_only_a_fallback():
    fetch_prefs = _section(
        "const fetchUserPortalPreferences",
        "const hasCustomMessageBorderPreference",
    )
    # 成功路径应用账号值；catch 才回退 localStorage
    assert fetch_prefs.index("prefs.history_sidebar_open === true") < fetch_prefs.index(
        "restoreHistorySidebarPreference();"
    )
