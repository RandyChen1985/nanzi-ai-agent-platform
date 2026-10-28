"""确认卡「超时过期」与「放弃本轮」的前端契约。

背景：挂起确认只有 10 分钟有效期（后端 `PENDING_CONFIRMATION_TTL_SECONDS`），
过期后快照被删，任何按钮都点不动；而 interrupt 之后 SSE 流已结束，输入框会被
误判为空闲。两件事都必须由前端显式表达，否则用户看到的是「按钮坏了 / 这一轮悄悄结束」。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_permission_event_carries_absolute_expiry():
    stream = _read("app/services/ai/runtime/agentscope/event_stream.py")

    assert '"expires_at"' in stream
    assert "pending.snapshot.created_at + PENDING_CONFIRMATION_TTL_SECONDS" in stream
    # 相对秒数也要跟随同一常量，不能写死 600 后与后端 TTL 漂移
    assert '"expires_in_seconds": PENDING_CONFIRMATION_TTL_SECONDS' in stream


def test_pending_permission_type_exposes_expiry_and_discard():
    types = _read("frontend/src/utils/agentscopeSseHandlers.ts")

    assert "expires_at?: number;" in types
    assert "expires_in_seconds?: number;" in types
    assert "discarded?: boolean;" in types


def test_card_marks_expired_and_disables_actions():
    card = _read("frontend/src/components/chat/ToolPermissionCard.vue")

    assert "const isExpired = computed(" in card
    assert "|| isExpired.value," in card  # locked 必须把过期算进去
    assert 'if (isExpired.value) return "已过期";' in card
    assert "这次确认已超时失效" in card


def test_card_offers_discard_and_receipts_it():
    card = _read("frontend/src/components/chat/ToolPermissionCard.vue")

    assert '(event: "discard"): void' in card
    assert "放弃本轮" in card
    assert 'emit("discard")' in card
    assert 'if (props.payload.discarded) return "已放弃";' in card
    assert "已放弃本轮，这条命令没有执行" in card


def test_both_pages_wire_discard_and_keep_input_locked_while_pending():
    embed = _read("frontend/src/views/EmbedChat.vue")
    debug = _read("frontend/src/views/AgentDebug.vue")

    for page in (embed, debug):
        assert '@discard="discardPendingPermission(msg)"' in page
        assert "const discardPendingPermission = (msg: Message) =>" in page
        assert "pending.discarded = true;" in page
        # 待确认期间不能把输入框当空闲：锁态里必须有这一项
        assert "const pendingPermissionLocking = computed(" in page
        assert "pendingPermissionLocking" in page

    assert "isProcessing || remoteRunBusy || recoveryLocking || pendingPermissionLocking" in embed
    assert ':disabled="isProcessing || pendingPermissionLocking"' in debug
    assert ':is-processing="isProcessing || remoteRunBusy || pendingPermissionLocking"' in debug


def test_expiry_is_persisted_so_the_pending_lock_can_release():
    """过期必须落到 status 上：只靠卡片内部的 computed，父组件的待确认锁会永久为真。"""

    card = _read("frontend/src/components/chat/ToolPermissionCard.vue")
    embed = _read("frontend/src/views/EmbedChat.vue")
    debug = _read("frontend/src/views/AgentDebug.vue")

    assert '(event: "expire"): void' in card
    assert 'emit("expire")' in card
    for page in (embed, debug):
        assert '@expire="expirePendingPermission(msg)"' in page
        assert "const expirePendingPermission = (msg: Message) =>" in page
        assert 'pending.status = "expired";' in page
