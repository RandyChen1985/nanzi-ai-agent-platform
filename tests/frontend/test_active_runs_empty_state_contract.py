"""契约：进行中的会话弹层，空状态要给出路。

用户诉求（原文）：「没有进行中的会话，那个弹层里面应该要引导 可以打开历史列表查看历史会话吧」

空状态此前只有一句「当前没有进行中的会话」。但用户点这个入口时，多半就是想找某个会话 ——
进行中的没有，去处就只剩历史列表，而界面上没有任何指向它的引导。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

POPOVER = ROOT / "frontend/src/components/chat/ActiveRunsPopover.vue"
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
HANDLER_CALL = '@open-history="handleActiveRunsOpenHistory"'


def _popover() -> str:
    return POPOVER.read_text(encoding="utf-8")


def _embed() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def _empty_state() -> str:
    """空状态块：`v-if="!items.length"` 那个 div，到 `<template v-else>` 为止。"""
    source = _popover()
    start = source.index('v-if="!items.length"')
    return source[start : source.index("<template v-else>", start)]


def test_popover_declares_the_event():
    assert "(e: 'open-history'): void" in _popover(), "弹层没有声明 open-history 事件"


def test_empty_state_offers_a_way_to_the_history():
    block = _empty_state()
    assert "当前没有进行中的会话" in block, "原有的空状态文案不见了"
    assert "已结束的会话都在会话历史里" in block, "空状态缺少指向历史列表的引导文案"
    assert "emit('open-history')" in block, "空状态缺少可点击的入口"


def test_both_desktop_and_mobile_instances_listen():
    """桌面浮层与移动端 modal 是两个实例，漏绑一个就会出现「点了没反应」。"""
    assert _embed().count(HANDLER_CALL) == 2, "两个浮层实例没有都绑定 open-history"


def test_handler_closes_the_popover_before_opening_the_sidebar():
    source = _embed()
    start = source.index("const handleActiveRunsOpenHistory = ")
    body = source[start : source.index("\n};", start)]
    assert "showActiveRuns.value = false;" in body, "没有先收起浮层"
    assert "showHistorySidebar.value = true;" in body, "没有打开会话历史"
    # 移动端浮层是带遮罩的 modal：不先关掉，侧栏会被压在遮罩下面。
    assert body.index("showActiveRuns.value = false;") < body.index("showHistorySidebar.value = true;")
