"""左侧主导航折叠状态只存本机（localStorage）。

刻意**不再**同步到账号偏好：折叠与否取决于「这台设备的屏幕够不够用」，
桌面收起、手机展开是常态，跨设备继承反而是负优化（手机上继承到"收起"会看不到导航）。
移动端抽屉本身是临时态，不写桌面缓存。
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


def test_left_sidebar_state_is_local_only():
    source = _source()
    assert 'const LEFT_SIDEBAR_COLLAPSED_KEY = "embed_left_sidebar_collapsed";' in source
    # 不再与账号偏好有任何往来
    assert '"/api/portal/portal-prefs/left-sidebar"' not in source
    assert "res.data?.data?.left_sidebar_collapsed" not in source


def test_left_sidebar_reads_local_cache_at_startup():
    source = _source()
    # 初始值直接取本机缓存，避免刷新时先展开再收起
    assert "ref(isDesktopSidebar() && readLeftSidebarCollapsedCache())" in source
    assert 'axios.get("/api/portal/portal-prefs")' not in source


def test_left_sidebar_toggle_writes_local_cache():
    watch = _section("watch(isCollapsed", "const toggleSidebar")
    assert "localStorage.setItem(LEFT_SIDEBAR_COLLAPSED_KEY" in watch
    # 移动端抽屉是临时态，不覆盖桌面缓存
    assert "if (!isDesktopSidebar()) return;" in watch
    # 开关翻转只落本地，不该再发任何请求
    assert "axios" not in watch
