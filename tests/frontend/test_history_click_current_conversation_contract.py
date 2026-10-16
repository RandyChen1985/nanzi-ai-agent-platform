"""契约：点历史列表里的「当前会话」不得触发重载。

现象：某会话正在生成时，点历史列表里它自己那一项，界面内容会**冻结在点击那一刻**，
直到本轮结束才一次性补齐（正文不丢，但过程可视化中断，很容易以为卡住了）。

根因是三个条件叠加：
① `handleHistoryClick` 里 `messages.value = []` 是无条件执行的——即使目标就是当前会话；
② 在途流没有被中止，而 SSE 的写入目标是 `sendMessageInternal` 内的**局部 ref**（见「顶层
   收尾函数取不到它」的注释），那份消息对象此时已经脱离消息列表；
③ `restoreEmbedStreamSnapshot()` 又用 sessionStorage 快照重建了一条新草稿并把
   `activeStreamDraft` 指向新对象，但恢复器的闸门是「SSE 在读就不介入」，于是新草稿收不到
   任何增量：SSE 写旧对象、事件续显不启动。

修法：目标就是当前会话且消息非空时，只把视图滚到底，不做任何重载。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
GUARD = "if (target === conversationId.value && messages.value.length > 0)"


def _source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def _handler() -> str:
    source = _source()
    start = source.index("const handleHistoryClick = async (item: any) => {")
    return source[start : source.index("\n};", start)]


def _guard_block() -> str:
    """守卫块：从条件判断到 return 之后的第一个闭合括号（不依赖缩进宽度）。"""
    body = _handler()
    start = body.index(GUARD)
    end = body.index("}", body.index("return;", start))
    return body[start:end]


def test_clicking_the_current_conversation_returns_early():
    body = _handler()
    assert GUARD in body, "缺少「目标即当前会话」的提前返回守卫"
    assert re.search(r"if \(target === conversationId\.value[\s\S]{0,400}?return;", body), (
        "守卫没有 return，仍会继续往下重载"
    )


def test_the_guard_runs_before_the_message_list_is_cleared():
    """顺序是这条修复的全部要点：一旦先清空列表，在途草稿就已经成了孤儿。"""
    body = _handler()
    assert body.index(GUARD) < body.index("messages.value = [];"), (
        "守卫排在「清空消息列表」之后，等于没修"
    )


def test_the_guard_does_not_reload_anything():
    block = _guard_block()
    assert "fetchConversationHistory" not in block, "提前返回分支里不该重载历史"
    assert "restoreEmbedStreamSnapshot" not in block, "提前返回分支里不该再还原快照"


def test_empty_list_still_takes_the_normal_reload_path():
    """初始化中／异常态下当前会话可能没有消息，那种情况仍需正常重载。

    这里必须断言实现里真的写了长度条件——只断言模块常量会变成恒真的空测试。
    """
    assert GUARD in _handler()


def test_the_guard_still_closes_the_sidebar_on_mobile():
    """移动端点条目后侧栏要收起——这条行为不能因为提前返回而丢掉。"""
    block = _guard_block()
    assert "showHistorySidebar.value = false" in block
    assert "scrollToBottom" in block
