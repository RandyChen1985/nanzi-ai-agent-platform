"""契约：会话历史列表的新鲜度。

用户报的现象（原文）：「我们打开历史，不会加载最新吗，需要我手动刷新才行？」

根因：打开面板的 watch 带了 `historyList.value.length === 0` 条件——首次拉取之后列表就非空
了，于是后续每次打开都不再请求，一直显示那份旧快照（新会话不出现、当前会话的轮次数 /
摘要 / 时间过期），只能手动点面板里的刷新按钮。

修法有两点不能少：① 打开即刷新；② 刷新路径去重——`fetchHistory` 原先只对「加载更多」
做了 in-flight 守卫，刷新若不加，随手开关面板会让并发响应乱序覆盖列表，比不刷新更糟。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"


def _source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def _watch_block(name: str) -> str:
    source = _source()
    start = source.index(f"watch({name}, ")
    return source[start : source.index("\n});", start)]


def _body(signature: str) -> str:
    source = _source()
    start = source.index(signature)
    return source[start : source.index("\n};", start)]


def test_opening_the_history_panel_always_refreshes():
    block = _watch_block("showHistorySidebar")
    assert "fetchHistory();" in block, "打开面板没有取最新"
    # 曾经的「列表为空才拉」条件正是这个 bug 的成因。
    assert "historyList.value.length" not in block, "打开面板仍带「列表为空才拉」的条件"


def test_closing_the_panel_does_not_fetch():
    block = _watch_block("showHistorySidebar")
    assert re.search(r"if \(!val\) return;", block), "关闭面板不该触发请求"


def test_history_refresh_is_deduplicated():
    body = _body("const fetchHistory = async")
    refresh_branch = body[body.index("} else {") :]
    assert "if (loadingHistory.value) return;" in refresh_branch, (
        "刷新路径没有去重：并发响应乱序会覆盖列表"
    )


def test_finished_turn_refreshes_an_open_panel():
    """面板开着时列表也会过期（轮次数 / 摘要 / 时间），本轮真正结束后取一次最新。"""
    source = _source()
    tail = source[source.index("void syncLatestSessionHistory(1, 5);") :]
    assert re.search(
        r"if \(showHistorySidebar\.value\) \{\s*void fetchHistory\(\);",
        tail,
    ), "轮结束时没有刷新已打开的历史面板"


def test_finished_turn_refresh_waits_for_the_run_to_settle():
    """必须挂在 run-status 翻 false（终态已落库）之后，而不是 SSE 读完那一步。

    断言覆盖**整个** watcher 回调并锁定「只出现一次」：否则把刷新提前到分支开头（那时
    FinalizeStep 可能还在落库，取回的仍是旧数据）也能骗过只看向后的断言。
    """
    source = _source()
    start = source.index("watch(remoteRunActive")
    block = source[start : source.index("\n});", start)]
    assert block.count("void fetchHistory();") == 1, "轮结束刷新应当只有一处"
    assert block.index("void syncLatestSessionHistory(1, 5);") < block.index("void fetchHistory();")
