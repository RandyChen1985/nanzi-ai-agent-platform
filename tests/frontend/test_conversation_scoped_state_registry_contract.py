"""契约：会话级运行时状态必须登记到统一复位入口。

背景：切会话 / 新建会话 / 丢弃会话视图 / 换身份共用 `resetConversationScopedState()`。会话级
状态都是模块级单例，语义上却属于「当前会话」——每漏一项就是一个跨会话缺陷（事件写进孤儿
草稿、输入框永久锁死、恢复卡片显示别的会话的进度），这些都已实际发生过。

约定：声明会话级状态时在其上方加 `// @conversation-scoped: <name>`，本测试扫描标记并断言它
出现在复位入口里（或由入口调用的专用复位函数覆盖）。**忘记登记 → 测试失败**，这就是这套
标记存在的意义：把「记得清理」从人的记忆力转移到测试上。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"

# 名字本身不出现在入口函数体里、但由入口调用的专用复位函数覆盖的状态。
RESET_VIA_FUNCTION = {
    "pendingQuickSend": ("dropPendingQuickSend()",),
    "stalePendingTimer": ("clearStalePendingTimer()",),
    "stallTimer": ("resetStallTimer()",),
    "showStalledPrompt": ("resetStallTimer()",),
}

ENTRY_SIGNATURE = "const resetConversationScopedState = () => {"


def _source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def _body(signature: str) -> str:
    source = _source()
    start = source.index(signature)
    return source[start : source.index("\n};", start)]


def _registered_names() -> list[str]:
    return re.findall(r"@conversation-scoped:\s*(\w+)", _source())


def test_every_registered_state_is_reset_by_the_single_entry_point():
    """登记过的会话级状态必须在复位入口里被清掉（或由入口调用的复位函数覆盖）。"""
    body = _body(ENTRY_SIGNATURE)
    missing = [
        name
        for name in _registered_names()
        if name not in body and not any(fn in body for fn in RESET_VIA_FUNCTION.get(name, ()))
    ]
    assert not missing, f"这些会话级状态没有在 resetConversationScopedState 里复位：{missing}"


def test_registry_covers_the_states_that_have_actually_broken():
    """下面这些是历史上真实出过跨会话缺陷的状态，必须一直登记着。"""
    names = set(_registered_names())
    for required in (
        "activeStreamDraft",
        "streamReplayCursor",
        "streamReplayPrimed",
        "recoverySyncedEventCount",
        "isProcessing",
        "abortController",
        "streamInFlight",
    ):
        assert required in names, f"{required} 丢失了 @conversation-scoped 标记"


def test_entry_point_resets_the_replay_triplet_and_the_stream_locks():
    body = _body(ENTRY_SIGNATURE)
    for statement in (
        "activeStreamDraft = null;",
        'streamReplayCursor.value = { lastSeq: 0, traceId: "" };',
        "streamReplayPrimed = false;",
        "recoverySyncedEventCount = 0;",
        "clearStreamReplayTimer();",
        "streamInFlight = false;",
        "isProcessing.value = false;",
        "resetStallTimer();",
        "clearStalePendingTimer();",
        "stopRemoteRunPolling();",
        "resetRunStatus();",
        "resetRecoveryCard();",
        "dropPendingQuickSend();",
    ):
        assert statement in body, statement
    # 顺序陷阱：resetStallTimer 在 isProcessing 仍为 true 时会重新起一个「生成耗时较长」
    # 的定时器，顺序反过来就白复位了。
    assert body.index("isProcessing.value = false;") < body.index("resetStallTimer();")
    # 先停流再复位：否则在途回调可能写进刚刚清空的状态里。
    assert body.index("abortController = null;") < body.index("resetRunStatus();")


def test_all_reset_paths_share_the_single_entry_point():
    """切会话 / 新建 / 丢弃 / 换身份都必须走统一入口，不允许再各自手写一遍。"""
    source = _source()
    # 旧的分散清理函数不应再存在（它曾经漏掉游标、stall 定时器与 fetch 在途标志）。
    assert "stopInflightGenerationForNewConversation" not in source
    # 至少三条路径调用入口：丢弃会话视图、新建会话、切会话（换身份是第四条）。
    assert source.count("resetConversationScopedState();") >= 3
