"""Embed 聊天区的自动跟底不能被「布局挤出来的 scroll」关掉。

现象：流式输出时用户明明没往上滚，页面却停在中途不再跟底，只弹一个
「查看最新消息」按钮。

成因：`handleScroll` 挂在 `@scroll.passive` 上，分不清 scroll 事件来自真实输入
还是内容长高。流式输出每帧都在改变高度，浏览器默认的滚动锚定
（`overflow-anchor: auto`）会自行调整 `scrollTop` 并派发 scroll 事件；这一刻
`scrollHeight` 刚变大、`scrollTop` 还没跟上，`atBottom` 算出来是 false，
`autoScrollEnabled` 于是被关掉——之后每个事件只弹提示、不滚动。

修复由三部分共同保证，本文件锁定这三条不被改回去：
1. 容器禁用滚动锚定（`overflow-anchor: none`），浏览器不再自行调整 scrollTop
2. 只有真实输入（滚轮 / 触摸 / 指针按下 / 翻页键）之后的短窗口内，才允许关跟底
3. 程序滚底的保护窗口必须盖住 `settleScrollToBottom` 的 6 帧，否则边界上会漏判
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EMBED = ROOT / "frontend/src/views/EmbedChat.vue"


def _embed_source() -> str:
    return EMBED.read_text(encoding="utf-8")


def test_scroll_container_disables_overflow_anchor():
    """滚动锚定必须关掉，否则浏览器自己调 scrollTop 会制造假 scroll 事件。"""

    source = _embed_source()

    assert 'style="overflow-anchor: none"' in source
    # 容器上必须挂着三处真实输入监听，缺一个就有场景识别不到用户意图
    assert '@wheel.passive="markUserScrollIntent"' in source
    assert '@touchmove.passive="markUserScrollIntent"' in source
    # 拖动滚动条不发 wheel/touchmove，只有 pointerdown 能覆盖
    assert '@pointerdown.passive="markUserScrollIntent"' in source


def test_auto_scroll_only_disabled_on_real_user_intent():
    """关跟底必须同时满足「正在流式」和「刚刚有真实输入」。"""

    source = _embed_source()

    assert "let userScrollIntentUntil = 0;" in source
    assert "const markUserScrollIntent = () => {" in source
    # 关键分支：没有真实输入意图时不许关
    assert "if (isProcessing.value && Date.now() < userScrollIntentUntil) {" in source
    # 反向断言：旧的「一离开底部就关」写法必须消失
    assert "if (isProcessing.value) {\n            autoScrollEnabled.value = false;" not in source


def test_programmatic_guard_covers_settle_frames():
    """程序滚底的忽略窗口要盖住 settle 的 6 帧（约 100ms）。"""

    source = _embed_source()

    assert "const PROGRAMMATIC_SCROLL_GUARD_MS = 400;" in source
    assert "programmaticScrollUntil.value = Date.now() + PROGRAMMATIC_SCROLL_GUARD_MS;" in source
    # 旧值 120ms 贴着 6 帧边界，容易漏判
    assert "Date.now() + 120" not in source


def test_scroll_intent_keyboard_listener_is_registered_and_cleaned():
    """翻页键也要算真实滚动意图，且必须成对注册/移除。"""

    source = _embed_source()

    assert '"PageUp"' in source and '"PageDown"' in source
    assert 'window.addEventListener("keydown", handleScrollIntentKey);' in source
    assert 'window.removeEventListener("keydown", handleScrollIntentKey);' in source
