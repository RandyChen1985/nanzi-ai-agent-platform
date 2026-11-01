"""打开 embedchat 首屏定位到最后一条的契约。

消息行带 `content-visibility: auto; contain-intrinsic-size: 180px`，离屏行先按 180px
估算高度。于是首次 `scrollTop = scrollHeight` 只滚到「估算出来的底部」，元素真正进入
视口后高度变大，滚动位置相对上移——历史越长，离底部越远。表现出来就是「打开会话没
定位到最后一条」。

`settleScrollToBottom` 会在随后几帧里继续贴底，但它只在 `force` 时被调用。首屏历史
落地必须走强制路径，并且骨架撤掉后要再补一次（骨架占位期间消息区还没真正排版）。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EMBED = ROOT / "frontend/src/views/EmbedChat.vue"


def _embed() -> str:
    return EMBED.read_text(encoding="utf-8")


def test_offscreen_rows_are_height_estimated():
    """这条契约的前提：消息行确实用了离屏估算，所以「一次贴底」不可靠。"""
    source = _embed()
    assert "content-visibility: auto; contain-intrinsic-size: 180px" in source


def test_settle_chain_only_runs_on_force():
    """补帧贴底只在强制路径上，别再被流式增量触发。"""
    source = _embed()
    body = source[source.index("const runScrollToBottom"):]
    body = body[: body.index("};")]
    assert "if (force) settleScrollToBottom(el);" in body


def test_first_history_load_forces_scroll_to_bottom():
    source = _embed()
    start = source.index("messages.value = newHistoryBatch;")
    tail = source[start: start + 400]
    assert "settleInitialScroll();" in tail
    # 不能再是裸的非强制贴底
    assert "nextTick(scrollToBottom);" not in source


def test_initial_scroll_helper_goes_through_force():
    source = _embed()
    start = source.index("const settleInitialScroll = () => {")
    body = source[start: start + 200]
    assert "nextTick(() => scrollToBottom(true));" in body


def test_skeleton_release_rescrolls_both_branches():
    """骨架撤掉后消息区才真正排版；同步撤与延时撤两个分支都要补一次贴底。"""
    source = _embed()
    body = source[source.index("const finishThreadSkeleton"):]
    body = body[: body.index("const abortThreadSkeleton")]
    assert body.count("isInitialLoading.value = false;") == 2
    assert body.count("settleInitialScroll();") == 2


def test_helper_is_defined_after_scroll_to_bottom():
    """避免 const 的暂时性死区：助手要在 scrollToBottom 之后定义。"""
    source = _embed()
    assert source.index("const scrollToBottom = (force = false)") < source.index(
        "const settleInitialScroll = () => {"
    )
