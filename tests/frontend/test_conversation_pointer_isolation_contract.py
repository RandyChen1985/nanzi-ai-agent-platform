from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _embed() -> str:
    return (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")


def test_conversation_pointer_is_scoped_to_tab():
    """会话 ID 只写 localStorage 时两个标签共用一条指针：

    A 切到会话 X 写入指针，B 刷新读到同一条指针就进了 A 的会话。
    会话都变成同一个之后，会话级专家当然也就一致了。
    """
    source = _embed()
    assert "const tabConversationStorageKey = (): string" in source
    assert "if (tabKey) sessionStorage.setItem(tabKey, cid);" in source


def test_tab_pointer_wins_over_shared_pointer():
    """本标签自己的会话必须优先于跨标签共享的那条指针。"""
    source = _embed()
    assert 'const tabValue = tabKey ? (sessionStorage.getItem(tabKey) || "").trim() : "";' in source
    assert "if (tabValue) return tabValue;" in source


def test_shared_pointer_remains_fallback_for_new_tabs():
    """新标签没有本标签指针时仍要能续接「上次会话」，否则跨标签体验倒退。"""
    source = _embed()
    assert 'return key ? (localStorage.getItem(key) || "").trim() : "";' in source


def test_clearing_pointer_clears_both_scopes():
    """归属校验失败时要连本标签指针一起清掉，否则会反复读到别人的会话。"""
    source = _embed()
    assert "if (tabKey) sessionStorage.removeItem(tabKey);" in source


def test_conversation_without_record_does_not_inherit_account_default():
    """账号默认会被「另一个会话里的切换」改写。

    无记录的会话若回落账号默认，表现为「我改 A，B 刷新也跟着变」，
    与会话级的目标冲突，因此应显式按「智能委派」处理。
    """
    source = _embed()
    assert "if (!routing) {" in source


def test_adopting_shared_pointer_also_claims_it_for_the_tab():
    """从共享指针续接会话时必须顺手认领到本标签。

    否则这个标签自己的指针永远为空，下次刷新又会读到被别人覆盖的共享指针。
    """
    source = _embed()
    assert "conversationId.value = savedId;" in source
    assert "persistConversationId(savedId);" in source


def test_trace_detail_surfaces_conversation_id():
    """排查「两个窗口是不是同一个会话」时，界面上必须有地方能看到会话 ID。

    回溯详情的响应里本来就带 history.conversation_id，只是模板没露出来。
    """
    source = _embed()
    assert "copyToClipboard(traceLogData.history.conversation_id" in source
    assert "String(traceLogData.history.conversation_id).slice(0, 8)" in source
