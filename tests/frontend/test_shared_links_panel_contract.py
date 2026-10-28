"""分享面板与侧栏 tab 的接线契约。"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_panel_exposes_copy_revoke_and_stats():
    source = _source("frontend/src/components/SharedLinksPanel.vue")
    assert "复制分享信息" in source and "复制密码" in source
    assert "作废" in source and "确认作废" in source
    assert "view_count" in source and "last_viewed_at" in source
    assert "state" in source  # active/expired/revoked 文案


def test_sidebar_has_two_tabs_and_share_button():
    source = _source("frontend/src/components/ChatHistorySidebar.vue")
    assert "会话历史" in source and "我分享的" in source
    assert 'emit("share-chat", item)' in source
    assert "enableShare" in source
    # 搜索与筛选只在历史 tab 内渲染
    assert 'v-if="activeTab === \'history\'"' in source


def test_sidebar_does_not_fetch_by_itself():
    """侧栏只 emit，请求由宿主注入的 headers 在面板内发起，避免两处各写一套鉴权。"""
    source = _source("frontend/src/components/ChatHistorySidebar.vue")
    assert "axios" not in source
