"""数据门户「点击即去对话」的宿主→挂件交接契约。

锁两件事：

1. `portal_question` 投递给挂件后必须立刻从地址栏摘掉——否则刷新会重放同一问题；
2. 挂件必须把宿主下发的门户提问**排到主初始化（会话确定 + 首屏历史落地）之后**再发，
   不能再用 `setTimeout(0)` 插进 `initChat()` 的认证往返里（会被首屏历史清空）。
"""

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

CHAT = "frontend/src/views/Chat.vue"
EMBED = "frontend/src/views/EmbedChat.vue"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


# ---------------------------------------------------------------- 宿主侧（Chat.vue）


def test_host_removes_portal_question_from_url_after_handoff():
    chat = _read(CHAT)

    assert "const consumePortalQuestionFromUrl = () => {" in chat
    # 只清理门户提问相关参数，不碰 conversation_id / report_id 等其它钉选
    for key in ("portal_question", "portal_action", "source"):
        assert f"delete nextQuery.{key};" in chat, key
    assert "if (!route.query.portal_question) return;" in chat
    assert "router.replace({ path: route.path, query: nextQuery });" in chat


def test_host_cleans_up_only_after_the_question_was_posted():
    chat = _read(CHAT)

    handoff = chat.index("request_id: portalQuestionRequestId")
    cleanup = chat.index("consumePortalQuestionFromUrl();")
    assert handoff < cleanup, "必须先把 portal_question 交给挂件，再从地址栏摘掉"


def test_host_attaches_stable_request_id_for_dedup():
    chat = _read(CHAT)

    # 幂等键是模块级常量：重推 INIT_CONFIG（重试握手 / 切智能体）时不能每推一次换一个
    assert chat.count("const portalQuestionRequestId = ") == 1
    assert "request_id: portalQuestionRequestId" in chat
    assert "portalQuestionRequestId = `portal-q-" in chat


# ---------------------------------------------------------------- 挂件侧（EmbedChat.vue）


def test_embedded_chat_no_longer_fires_portal_question_on_a_bare_timeout():
    embed = _read(EMBED)

    # 旧实现（缺陷本身）：setTimeout(() => handlePortalQuickQuestion(...), 0)
    assert re.search(r"setTimeout\(\s*\(\)\s*=>\s*handlePortalQuickQuestion", embed) is None
    # 新实现：只入队，由主初始化结束后统一消费
    assert "queueHostPortalQuestion(data.portal_question);" in embed


def test_embedded_chat_queues_and_dedups_host_portal_question():
    embed = _read(EMBED)

    assert "let pendingHostPortalQuestion:" in embed
    assert "const handledHostPortalRequestIds = new Set<string>();" in embed
    assert "if (handledHostPortalRequestIds.has(requestId)) return;" in embed
    # 宿主没给请求号时退回问题内容做幂等键，同一次握手重推不会发两次
    assert "`host-portal-${query}`" in embed


def test_embedded_chat_flushes_after_host_init_finishes():
    embed = _read(EMBED)

    start = embed.index("const handleInitConfig = async")
    end = embed.index("const applyHostInitConfig = async")
    body = embed[start:end]

    assert "await applyHostInitConfig(data);" in body
    assert "finally {" in body
    # 放在 finally：认证失败的握手也要清空队列，别让旧问题滞留到下一次初始化
    assert body.index("finally {") < body.index("void flushPendingHostPortalQuestion();")


def test_embedded_chat_waits_for_first_screen_history_before_sending():
    embed = _read(EMBED)

    # initChat 的首屏历史链要登记成信号，排队项才能等它落地
    assert "initialHistorySettled = fetchConversationHistory(false, initGeneration)" in embed
    assert "initialHistorySettled = Promise.resolve();" in embed
    # 等待带超时上限：历史接口挂住时也不能把用户的点击吞掉
    assert "HOST_PORTAL_QUESTION_HISTORY_WAIT_MS" in embed
    assert "await waitInitialHistorySettled();" in embed

    start = embed.index("const flushPendingHostPortalQuestion = async")
    end = embed.index("const applyInitConfigPayload = (data: Record<string, any>) => {")
    flush_body = embed[start:end]
    # 顺序：先等历史落地，再交给统一的快捷提问入口
    assert flush_body.index("await waitInitialHistorySettled();") < flush_body.index(
        "await handlePortalQuickQuestion(pending.query, pending.action);"
    )


def test_embedded_chat_drops_portal_question_when_auth_failed():
    embed = _read(EMBED)

    start = embed.index("const flushPendingHostPortalQuestion = async")
    end = embed.index("const applyInitConfigPayload = (data: Record<string, any>) => {")
    flush_body = embed[start:end]

    assert "if (!hasPermission.value) return;" in flush_body
    # 队列先取出再判权限：认证失败时问题必须被丢弃，不能挂到下一次握手
    assert flush_body.index("pendingHostPortalQuestion = null;") < flush_body.index(
        "if (!hasPermission.value) return;"
    )


def test_embedded_chat_does_not_overwrite_user_draft_on_fill():
    embed = _read(EMBED)

    start = embed.index("const flushPendingHostPortalQuestion = async")
    end = embed.index("const applyInitConfigPayload = (data: Record<string, any>) => {")
    flush_body = embed[start:end]

    # 「填入」只是写输入框：用户已开始打字时不能把草稿顶掉
    assert 'pending.action === "fill" && hasUserComposerDraft()' in flush_body
    assert '"输入框已有内容，门户问题未填入"' in flush_body
