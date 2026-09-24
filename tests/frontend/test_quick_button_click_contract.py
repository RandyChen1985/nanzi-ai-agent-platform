"""Contract tests for quick button (quick:) click delivery under run locks.

回归背景：用户反馈「AI 已经输出完成，第一次点快捷按钮不触发发送，要点第二次」。
根因是 `handleQuickQuestion` 在 `isProcessing || remoteRunActive || sendLocked` 时
直接 `return` 静默丢弃点击；而 `remoteRunActive` 由 1.5s 轮询驱动，SSE 的 `run_status`
终态事件在切后台 / 代理缓冲 / 流异常中断时会丢失，导致前端锁迟迟不释放。

这些测试锁住修复后的行为契约，避免静默吞点击回归。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
CONVERGENCE = ROOT / "frontend/src/utils/runStatusConvergence.ts"
pytestmark = pytest.mark.no_infrastructure


def _embed_source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def _handle_quick_question_body() -> str:
    """截取 handleQuickQuestion 函数体，用于精确断言其内部行为。"""
    source = _embed_source()
    start = source.index("const handleQuickQuestion = async (")
    # 函数以 "\n};" 结束，取第一个出现处。
    end = source.index("\n};", start)
    return source[start:end]


def test_quick_click_is_never_silently_dropped():
    """被发送守卫拦下时，必须排队补发并提示，禁止裸 return 静默丢弃。"""
    body = _handle_quick_question_body()

    # 旧行为（回归点）：`if (... ) return;` —— 点击被吞且无任何反馈。
    assert not re.search(
        r"if \(action === \"send\" && \(isProcessing\.value \|\| remoteRunActive\.value \|\| sendLocked\.value\)\) return;",
        body,
    ), "quick 点击不应再被静默 return 丢弃"

    # 新行为：记入待补发队列 + 给出用户可见反馈。
    assert "pendingQuickSend" in body
    assert "showToast" in body


def test_quick_click_rechecks_backend_truth_before_blocking():
    """点击时应先核验后端真相，避免前端锁滞后导致「明明已空闲却点不动」。"""
    source = _embed_source()
    body = _handle_quick_question_body()
    # 核验走 `verifyRunStatusOnce()`（并发点击合流，避免连点重复打后端），
    # 它内部必须真的去刷新 run-status，否则这里只是换了个名字。
    assert "await verifyRunStatusOnce()" in body
    helper = source[source.index("const verifyRunStatusOnce"):source.index("const flushPendingQuickSend")]
    assert "refreshCurrentRunStatus()" in helper, "verifyRunStatusOnce 必须真正刷新 run-status"
    # 合流：在途请求未落地前，后续点击复用同一次核验。
    assert "quickSendVerifyInFlight" in helper

    # 核验顺序：必须先刷新状态，再判定是否仍被拦。
    refresh_at = body.index("await verifyRunStatusOnce()")
    block_at = body.index("pendingQuickSend = {")
    assert refresh_at < block_at, "必须先核验后端状态，再决定是否排队"


def test_new_quick_click_supersedes_queued_one():
    """新的点击必须取代此前排队的那一次，避免一次点击发出两条不同提问。"""
    body = _handle_quick_question_body()
    # send 分支开头先丢弃旧排队项，再进入核验。
    assert re.search(
        r"if \(action === \"send\"\) \{\s*//[^\n]*\n(\s*//[^\n]*\n)*\s*dropPendingQuickSend\(\);\s*//[^\n]*\n\s*if \(quickSendBlocked\(\)\) await verifyRunStatusOnce\(\);",
        body,
    ), "新的 quick 点击应先丢弃旧排队项再核验"
    # 取代即作废：丢弃时连同其一次性「强制查数」意图一起撤销。
    source = _embed_source()
    drop = source[source.index("const dropPendingQuickSend"):source.index("let quickSendVerifyInFlight")]
    assert "forceDataQueryAgentOnce.value = false" in drop, "丢弃排队项时必须撤销其强制查数意图"


def test_pending_quick_send_flushes_when_lock_releases():
    """锁一释放即自动补发，不依赖用户再点一次。"""
    source = _embed_source()
    assert "flushPendingQuickSend" in source
    # 补发由锁状态变化驱动。
    assert re.search(
        r"watch\(\[isProcessing, remoteRunActive, sendLocked\], \(\) => \{\s*void flushPendingQuickSend\(\);",
        source,
    ), "待补发的快捷提问应在发送锁释放时自动 flush"


def test_stream_end_nudges_run_status_convergence():
    """流结束必须助推 run-status 收敛，压缩「已输出完但按钮点不动」的窗口。"""
    source = _embed_source()
    assert "nudgeRunStatusConvergence()" in source
    assert "shouldContinueRunStatusNudge" in source
    # 刻意不直接清零 remoteRunActive：后端可能仍在跑，须以后端为准。
    assert "clearRunActiveForFinishedStream" not in source


def test_convergence_strategy_is_bounded():
    """补验必须有限次，不能退化成无限轮询。"""
    text = CONVERGENCE.read_text(encoding="utf-8")
    assert "RUN_STATUS_NUDGE_MAX_ATTEMPTS" in text
    assert "export function shouldContinueRunStatusNudge" in text
    # 额度用尽即停。
    assert "attempt < RUN_STATUS_NUDGE_MAX_ATTEMPTS" in text


def test_history_sync_fallback_releases_run_lock():
    """历史同步重试耗尽后，若无待确认卡片且流已结束，应一并释放运行锁。"""
    source = _embed_source()
    assert re.search(
        r"if \(remoteRunActive\.value && !streamInFlight && !hasPendingConfirmation\(\)\) \{\s*markOutputCompleted\(\);",
        source,
    ), "重试耗尽的兜底分支不应只清 isProcessing 而漏掉 remoteRunActive"


def test_queued_quick_send_cleared_on_send_and_unmount():
    """真正发送落地或组件卸载后，排队的快捷提问必须清除，避免重复发送 / 僵尸发送。"""
    source = _embed_source()
    # 发送主流程内清除（统一经 dropPendingQuickSend 收口，以便一并撤销强制查数意图）。
    assert re.search(
        r"const sendMessageInternal = async[\s\S]{0,600}?dropPendingQuickSend\(\);",
        source,
    ), "sendMessageInternal 应清除排队的快捷提问"
    # 卸载清理。
    assert re.search(
        r"clearRunStatusNudge\(\);\s*// 卸载后不再补发排队的快捷提问[\s\S]{0,200}?dropPendingQuickSend\(\);",
        source,
    ), "卸载时应清空排队状态"


def test_queued_quick_send_never_overrides_user_draft():
    """自动补发不得覆盖/清空用户已输入的草稿，也不得代发。"""
    source = _embed_source()
    assert "hasUserComposerDraft" in source
    # 补发前必须检查草稿。
    assert re.search(
        r"if \(hasUserComposerDraft\(\)\) \{[\s\S]{0,300}?return;",
        source,
    ), "补发前必须检查用户草稿并在有草稿时放弃代发"


def test_queued_quick_send_expires_instead_of_firing_late():
    """等待过久应放弃自动发送并交回输入框，而不是在任务跑完后突然冒出消息。"""
    source = _embed_source()
    assert "PENDING_QUICK_SEND_TTL_MS" in source
    assert re.search(
        r"Date\.now\(\) - pending\.at > PENDING_QUICK_SEND_TTL_MS",
        source,
    ), "补发必须带超时判定"
    # 超时分支仅在输入框为空时写入，避免覆盖草稿。
    assert re.search(
        r"if \(!hasUserComposerDraft\(\)\) userInput\.value = pending\.content;",
        source,
    ), "超时回填输入框时不得覆盖已有草稿"


def test_queued_quick_send_uses_frozen_snapshot():
    """补发必须用固定快照，避免重新抓取 userInput / 附件造成误带内容。"""
    source = _embed_source()
    assert re.search(
        r"sendPreparedMessage\(async \(\) => captureSendSnapshot\(\{\s*content: pending\.content,",
        source,
    ), "补发应通过 sendPreparedMessage + 固定快照，而非重新抓取输入框"


def test_force_data_query_intent_travels_with_pending_send():
    """「强制走查数」是一次性意图，必须与排队补发配对，且放弃代发时一并撤销。"""
    source = _embed_source()

    # 排队时记录意图。
    assert re.search(
        r"forceDataQueryAgent: forceDataQueryAgentOnce\.value,",
        source,
    ), "排队时应记录当时的强制查数意图"

    # 补发时带上意图。
    assert re.search(
        r"if \(pending\.forceDataQueryAgent\) forceDataQueryAgentOnce\.value = true;",
        source,
    ), "补发时应恢复强制查数意图"

    # 两条放弃路径都必须撤销意图，避免劫持用户之后的手动提问。
    assert source.count("if (pending.forceDataQueryAgent) forceDataQueryAgentOnce.value = false;") >= 2, (
        "超时放弃与草稿放弃两条路径都必须撤销强制查数意图"
    )

    # 手动发送取代排队项时也要撤销：由 dropPendingQuickSend 统一收口，
    # 因此 sendMessageInternal 与 handleQuickQuestion 两条取代路径都走同一实现。
    drop = source[source.index("const dropPendingQuickSend"):source.index("let quickSendVerifyInFlight")]
    assert re.search(
        r"if \(pendingQuickSend\.forceDataQueryAgent && !keepForceIntent\) \{\s*forceDataQueryAgentOnce\.value = false;",
        drop,
    ), "手动发送取代排队项时应撤销其强制查数意图"
