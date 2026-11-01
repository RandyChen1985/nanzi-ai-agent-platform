"""消息快速定位浮标（第一条 / 最后一条）的契约。

浮标的价值全在「什么时候出现」和「点了去哪」上，纯视觉改动没有测试价值，
所以这里锁的是判定条件与跳转行为，不锁像素。
"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EMBED = ROOT / "frontend/src/views/EmbedChat.vue"
ANCHORS = ROOT / "frontend/src/components/chat/ChatScrollAnchors.vue"


def _source() -> str:
    return EMBED.read_text(encoding="utf-8")


def _anchors_source() -> str:
    return ANCHORS.read_text(encoding="utf-8")


def test_threshold_is_eight_messages():
    """条数阈值固定为 8：更短的会话一屏看得完，浮标只是占地方。"""
    assert "const SCROLL_ANCHOR_MIN_MESSAGES = 8;" in _source()


def test_anchors_require_message_count_and_scrollability():
    """只有条数够、且内容真的能滚，才显示浮标——否则点了毫无反应。"""
    source = _source()
    assert "messages.value.length >= SCROLL_ANCHOR_MIN_MESSAGES" in source
    assert "&& canScrollMessages.value" in source


def test_each_anchor_hides_when_already_at_that_end():
    """到顶藏「第一条」、到底藏「最后一条」，不出现过即无效的按钮。"""
    source = _source()
    assert "const showScrollAnchorTop = computed(() => showScrollAnchors.value && !isAtTop.value);" in source
    assert "const showScrollAnchorBottom = computed(() => showScrollAnchors.value && !isAtBottom.value);" in source


def test_scroll_state_syncs_before_the_programmatic_guard():
    """浮标状态要在「程序滚底保护窗口」之前更新，否则点完按钮状态不刷新。"""
    source = _source()
    scroll_fn = source.split("const handleScroll = (e: Event) => {", 1)[1][:600]
    assert "syncScrollAnchorState(target);" in scroll_fn
    assert scroll_fn.index("syncScrollAnchorState(target);") < scroll_fn.index(
        "if (Date.now() < programmaticScrollUntil.value) return;"
    )


def test_message_count_change_resyncs_scroll_state():
    """新消息到达到可能刚变得可滚动，但用户没滚过，必须主动同步一次。"""
    source = _source()
    assert "() => messages.value.length," in source
    assert "void nextTick(() => syncScrollAnchorState());" in source


def test_jump_to_first_uses_smooth_scroll():
    """跳顶用平滑滚动，避免远距离瞬移丢失空间感。"""
    source = _source()
    assert 'el.scrollTo({ top: 0, behavior: "smooth" });' in source


def test_jump_to_last_scrolls_smoothly():
    """跳底也要平滑滚过去。

    既有的 scrollToBottom 是流式跟底用的逐帧赋值（el.scrollTop = scrollHeight），
    点了会直接瞬移到底，用户看不到滚动过程。
    """
    source = _source()
    jump = source.split("const jumpToLastMessage = () => {", 1)[1][:260]
    assert 'el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });' in jump
    assert "scrollToBottom(true);" not in jump


def test_template_renders_both_anchors_with_labels():
    """组件要真的渲染两颗按钮，并带可访问名称；父级负责跳转接线。"""
    anchors = _anchors_source()
    assert 'aria-label="回到第一条消息"' in anchors
    assert 'aria-label="回到最后一条消息"' in anchors
    assert '@click="jump(\'first\')"' in anchors
    assert '@click="jump(\'last\')"' in anchors
    source = _source()
    assert '@jump-first="jumpToFirstMessage"' in source
    assert '@jump-last="jumpToLastMessage"' in source


def test_anchors_are_visually_subdued_by_default():
    """平时半透明不抢视线，hover 才完全清晰。"""
    anchors = _anchors_source()
    assert "opacity-65" in anchors
    assert "hover:opacity-100" in anchors


def test_the_sfc_compiles():
    """整个 SFC 必须能被 Vue 官方编译器解析（vue-tsc 不查模板标签平衡）。"""
    if not (ROOT / "frontend/node_modules").exists():
        pytest.skip("前端依赖未安装")
    script = (
        "const {parse}=require('@vue/compiler-sfc');const fs=require('fs');"
        "const r=parse(fs.readFileSync('src/views/EmbedChat.vue','utf8'));"
        "if(r.errors.length){console.error(r.errors.map(String).join(String.fromCharCode(10)));process.exit(1)}"
    )
    proc = subprocess.run(
        ["node", "-e", script], cwd=str(ROOT / "frontend"), capture_output=True, text=True
    )
    assert proc.returncode == 0, f"EmbedChat.vue 模板无法编译：\n{proc.stderr}"


def test_anchors_only_show_while_user_is_scrolling():
    """不滚动的时候页面要干净：浮标只在「用户刚滚动过」时露面。"""
    source = _source()
    assert "const SCROLL_ANCHOR_IDLE_HIDE_MS = 1200;" in source
    assert "const isScrollingMessages = ref(false);" in source
    anchors = source.split("const showScrollAnchors = computed(", 1)[1][:400]
    assert "isScrollingMessages.value" in anchors


def test_streaming_autoscroll_does_not_summon_the_anchors():
    """流式自动跟底也会发 scroll 事件，不能让它把浮标一直挂着。"""
    source = _source()
    scroll_fn = source.split("const handleScroll = (e: Event) => {", 1)[1][:900]
    assert "if (Date.now() < userScrollIntentUntil) markScrollAnchorActivity();" in scroll_fn


def test_hover_pauses_the_fade_out():
    """鼠标停在浮标上时不能淡出，否则刚要点击按钮就消失了。"""
    source = _source()
    assert "const scrollAnchorHovered = ref(false);" in source
    hover = source.split("const handleScrollAnchorHover = (hovered: boolean) => {", 1)[1][:400]
    assert "if (scrollAnchorHovered.value) return;" in source
    assert "scheduleScrollAnchorHide();" in hover
    assert '@mouseenter="emit(\'hover\', true)"' in _anchors_source()
    assert '@mouseleave="emit(\'hover\', false)"' in _anchors_source()
    assert '@hover="handleScrollAnchorHover"' in source


def test_idle_timer_is_cleared_on_unmount():
    """组件卸载后残留的定时器会改已销毁的 ref，必须清掉。"""
    source = _source()
    assert "if (scrollAnchorIdleTimer !== null) {" in source
    assert "window.clearTimeout(scrollAnchorIdleTimer);" in source


def test_anchors_can_be_dragged_and_position_is_remembered():
    """浮标可以拖到任意位置，并把落点记在 localStorage 里。"""
    anchors = _anchors_source()
    assert "const STORAGE_KEY = \"yovole_scroll_anchor_pos\";" in anchors
    assert "const DRAG_THRESHOLD_PX = 4;" in anchors
    assert '@pointerdown="onPointerDown"' in anchors
    assert "window.addEventListener(\"pointermove\", onPointerMove);" in anchors
    assert "persistPosition(position.value);" in anchors
    assert "localStorage.setItem(STORAGE_KEY" in anchors


def test_drag_does_not_trigger_a_jump():
    """拖完手一松不能顺手跳一次——位移超过阈值就吞掉随后的 click。"""
    anchors = _anchors_source()
    assert "suppressClick = true;" in anchors
    jump = anchors.split("const jump = (kind: \"first\" | \"last\") => {", 1)[1][:160]
    assert "if (suppressClick) return;" in jump


def test_drag_is_clamped_inside_the_container():
    """拖不出容器：越界会把按钮扔到看不见的地方。"""
    anchors = _anchors_source()
    assert "const clamp = (value: number, min: number, max: number) =>" in anchors
    assert "maxRight" in anchors and "maxBottom" in anchors


def test_default_position_clears_the_input_pills():
    """默认落点要避开输入框右上角的状态胶囊，否则「最后一条」会被整个盖住。"""
    anchors = _anchors_source()
    assert "const DEFAULT_POSITION: AnchorPosition = { right: 12, bottom: 192 };" in anchors
