"""会话历史侧栏开关只存本机（localStorage）。

刻意**不再**同步到账号偏好：这个开关是「我这台设备上此刻想不想看侧栏」的临时布局偏好，
跨设备继承反而别扭——在窄屏或小窗口上被强制展开会直接压掉正文。窄屏下侧栏还是带遮罩的
浮层，刷新后自动弹开会挡住内容，所以只有桌面端恢复/保存。
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


def test_history_sidebar_state_is_local_only():
    source = _source()
    assert 'const HISTORY_SIDEBAR_OPEN_KEY = "embed_history_sidebar_open";' in source
    # 不再写账号偏好：既没有 PUT，也不读 prefs 里的同名字段
    assert '"/api/portal/portal-prefs/history-sidebar"' not in source
    assert "prefs.history_sidebar_open" not in source


def test_history_sidebar_restores_from_local_cache():
    source = _source()
    assert "restoreHistorySidebarPreference()" in source
    assert "localStorage.getItem(HISTORY_SIDEBAR_OPEN_KEY)" in source


def test_history_sidebar_toggle_writes_local_cache():
    watch = _section("watch(showHistorySidebar, (val) => {", "if (!val) return;")
    assert "localStorage.setItem(HISTORY_SIDEBAR_OPEN_KEY" in watch
    # 开关翻转只落本地，不该再发任何请求
    assert "axios" not in watch


def test_history_sidebar_persistence_skips_mobile_overlay():
    source = _source()
    assert '!window.matchMedia("(max-width: 639px)").matches' in source
