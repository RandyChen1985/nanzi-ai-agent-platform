"""聊天页接线契约：漏接一处就会「点了分享没反应」。

源码契约测试：只读文件内容比对关键字，不启动浏览器、不连数据库，
因此可以用 no_infrastructure 标记跳过共享环境初始化。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _embed() -> str:
    return (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")


def _sidebar() -> str:
    return (ROOT / "frontend/src/components/ChatHistorySidebar.vue").read_text(encoding="utf-8")


def test_sidebar_wires_share_button_and_headers():
    source = _embed()
    assert '@share-chat="openShareDialog"' in source
    assert ':enable-share="true"' in source
    assert ':share-auth-headers="embedAuthHeaders"' in source


def test_sidebar_gets_ref_for_post_create_refresh():
    source = _embed()
    assert 'ref="historySidebarRef"' in source
    assert "historySidebarRef.value?.reloadShares?.()" in source


def test_dialog_is_mounted_with_target_conversation():
    source = _embed()
    assert "ShareConversationDialog" in source
    assert "v-model:visible" in source or ":visible=" in source
    assert "conversation-id" in source
    # 弹窗必须挂在侧栏之后、同一个模板层级上，否则会被侧栏的
    # `flex-shrink-0` / 移动端 fixed 定位挤成 0 宽。
    sidebar_end = source.index('class="border-r border-gray-200 dark:border-gray-800"')
    dialog_start = source.index("<ShareConversationDialog")
    assert dialog_start > sidebar_end


def test_share_target_prefers_conversation_id():
    source = _embed()
    assert "openShareDialog" in source
    assert "conversation_id" in source
    # 没有 conversation_id 的 trace 聚合条目没有稳定会话可冻结：
    # 必须提示后直接返回，不能带着空 id 打开弹窗。
    assert "该记录没有会话 ID，暂不支持分享" in source
    assert 'showToast("该记录没有会话 ID，暂不支持分享", "warning")' in source


def test_sidebar_exposes_reload_for_shares():
    sidebar = _sidebar()
    assert "defineExpose" in sidebar
    assert "reloadShares" in sidebar
    # defineExpose 只应有一处：重复添加会让后一条覆盖前一条。
    assert sidebar.count("defineExpose(") == 1


def test_sidebar_refresh_button_dispatches_by_active_tab():
    """「我分享的」tab 下按刷新应刷新分享列表，而不是去拉会话历史。"""
    sidebar = _sidebar()
    assert "refreshActiveTab" in sidebar
    assert 'emit("fetch-history")' in sidebar
    assert "reloadShares" in sidebar
