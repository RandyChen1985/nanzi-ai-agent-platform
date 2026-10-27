"""EmbedPersonal 返回对话入口契约测试。

背景：`/embed/personal` 是给第三方 iframe 嵌入的个人中心，刻意不带 Dashboard 导航
（设计注释写明「只出现个人中心本身」）。但对话页头部的「我的账号」会跳到它——
**小程序 web-view 里小程序的返回键是退出整个 web-view，而不是回退站内路由**，
没有返回入口用户就会卡在个人中心并丢掉对话上下文。

因此：从 `/embed/chat` 跳进来时必须出现「收起我的账号」把手；而第三方 iframe 直接打开本页时
不该凭空多出一个把手。

把手形态在 2026-10-27 按用户要求改为与 chat 页右侧「我的账号」把手一致的贴边竖条
（原来是一个写着「返回对话」的右下角胶囊），一开一合形成开关式对称。

把手的约束（均为实测反馈后逐轮修正）：贴右侧中部（左上角会压住 tab 栏、左侧中部会压正文）、
`lg:hidden`（宽屏 Web 有完整导航可回退，按钮多余）、h-11（44px）细把手且用
`before:` 伪元素向左扩 44px 补足可点区域、实底 + 可见边界（压在内容之上仍要读得清）。

2026-10-27 二次修正：先做成 16px 宽的细竖条（视觉低调、靠伪元素扩热区），用户实测
「这个返回怎么点啊，回不去，是不是太小了」——热区够大也没用，**看不见的图标 + 窄条
等于不知道该点哪里**。现改为 44×36 的实体按钮 + 明确的「‹」箭头。
其中「低调」只指**不用强调色**，不指降尺寸、也不指降对比度。

只做源码静态断言（沿用 tests/frontend 既有风格），不启动浏览器。
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
VIEW = ROOT / "frontend/src/views/EmbedPersonal.vue"
CHAT = ROOT / "frontend/src/views/EmbedChat.vue"


def _view() -> str:
    return VIEW.read_text(encoding="utf-8")


def test_view_exists():
    assert VIEW.exists(), f"缺少视图文件: {VIEW}"


def test_chat_links_here_via_embed_route():
    """对话页的「我的账号」必须指向本页，且走 embed 路由而非门户路由。"""
    chat = CHAT.read_text(encoding="utf-8")
    m = re.search(r"const runHeaderMoreAccount = \(\) => \{(.*?)\n\};", chat, re.S)
    assert m, "对话页缺少 runHeaderMoreAccount"
    assert 'router.push({ name: "EmbedPersonal" })' in m.group(1), (
        "对话页未跳转到 /embed/personal"
    )
    assert "/dashboard/personal" not in m.group(1), (
        "跳到了门户路由——嵌入场景会被守卫拦到登录页"
    )


def test_return_entry_is_conditional_on_coming_from_chat():
    """返回键只在「从 /embed/chat 跳进来」时出现，不能无条件显示。"""
    view = _view()

    assert 'v-if="backToChat"' in view, "返回按钮未做条件渲染"

    m = re.search(r"const detectBackToChat = \(\) => \{(.*?)\n\};", view, re.S)
    assert m, "缺少 detectBackToChat"
    body = m.group(1)
    assert "history.state" in body, "未读取 history.state.back，无法判断来源"
    assert 'startsWith("/embed/chat")' in body, "未按 /embed/chat 前缀判断来源"
    assert "history.length" not in body, "用 history.length 判断来源不可靠——iframe 里也会 > 1"

    assert "onMounted(detectBackToChat)" in view, "未在挂载后判定来源"


def _return_button_tag() -> str:
    """取出返回按钮的开标签，供各条定位 / 可见性断言复用。"""
    view = _view()
    idx = view.index('v-if="backToChat"')
    start = view.rindex("<button", 0, idx)
    return view[start:view.index(">", idx)]


def test_return_entry_is_fixed_out_of_flow():
    """把手必须 fixed：本页约定「只出现个人中心本身」，不该占布局、不该随滚动移出视口。"""
    tag = _return_button_tag()
    assert "fixed" in tag, "把手未使用 fixed 定位"
    # 唯一的 absolute 只能是 before: 伪元素（扩可点热区），标签自身必须是 fixed
    assert tag.count("absolute") == 1 and "before:absolute" in tag, "把手用了 absolute——容器滚动时会移出视口"
    assert 'aria-label="收起我的账号"' in tag, "把手缺少可访问名称"


def test_return_entry_is_anchored_right_middle():
    """必须贴右侧中部：与 chat 页右侧「我的账号」按钮同位，一开一合形成对称。

    左上角会压住个人中心的 tab 栏（我的权限 / 我的记忆 / 我的 Token…），
    左侧中部会压住正文首列；右下角则要越过整屏内容才够得到。
    垂直居中用 -mt-[22px]（44px 的一半）而不是 -translate-y-1/2：收起动画要走
    translate-x，两个 translate 会互相覆盖。
    """
    tag = _return_button_tag()
    assert "right-0" in tag, "按钮未贴右边"
    assert "top-1/2" in tag and "-mt-[22px]" in tag, "按钮未垂直居中于右侧中部"
    assert "bottom-4" not in tag, "按钮仍是旧的右下角位置"
    assert "top-3" not in tag, "把手在顶部，会遮挡 tab 栏"
    assert "left-3" not in tag, "把手在左侧，会遮挡 tab 栏"


def test_return_entry_is_mobile_only():
    """宽屏（Web）下不显示：此页有完整导航可回退，浮标纯属多余。

    断点必须与 chat 页把手、Dashboard 的 isMobile 一致：都是 1024px。
    曾经写成 sm:hidden（640px），640–1023px 之间 chat 页有把手、账号页却有进无出。
    """
    tag = _return_button_tag()
    assert "lg:hidden" in tag, "把手未在 ≥1024px 隐藏，Web 下会多出浮标"
    assert "sm:hidden" not in tag, "把手断点回退到 640px，会与 chat 页把手（1024px）不对齐"


def test_return_entry_is_readable_over_content():
    """浮层压在内容之上，必须有实底 + 与正文的可见边界，否则底下的字透出来糊成一片。

    初版用 bg-white/70 半透明 + text-gray-400，用户反馈「是不是有点看不清」。
    浮层控件的可读性优先于「低调」。
    按用户偏好不用边框（bg-white/95），因此阴影是**唯一**的分隔手段，不能一起删。
    """
    tag = _return_button_tag()
    assert "bg-white/95" in tag, "按钮背景过透，会与内容糊在一起"
    # 浅色模式不能用 text-gray-400（深色模式的 dark:text-gray-400 是另一回事）
    assert not re.search(r"(?<!dark:)text-gray-400", tag), "把手文字过浅，压在内容上读不清"
    assert "shadow-md" in tag, "按钮与正文缺少可见边界（已去边框，阴影是唯一分隔手段）"
    assert "border" not in tag, "返回键按用户偏好不用边框"


def test_return_entry_avoids_loud_accent():
    """但要克制：不用强调色实底、不用粗体，避免抢内容注意力。

    「低调」= 不用强调色，而不是降可读性。
    """
    tag = _return_button_tag()
    assert "bg-primary" not in tag, "返回键用了 primary 实底，过于抢眼"
    assert "font-bold" not in tag, "返回键用了粗体，拉高了视觉权重"
    assert "hover:" in tag, "返回键缺少 hover 提亮"


def test_return_entry_meets_touch_target():
    """浮标必须达到 44px 触控热区。

    它是移动端**唯一**的返回入口；初版为「低调」写成 text-xs + py-1.5，
    实际高度仅约 26px，与头部按钮 44px 的标准自相矛盾。
    「低调」不该降尺寸。
    """
    tag = _return_button_tag()
    # 细把手（视觉 12px）但热区要够：高度 44px，宽度靠伪元素向左扩 44px（约 56×44）。
    # 只做窄条会被反馈「太小、点不到」；只做实体块会被反馈「太大」——两者都不行。
    assert "h-11" in tag, "把手高度不足 44px 触控热区下限"
    assert "w-3" in tag, "把手视觉宽度不是 12px（细把手形态）"
    assert "before:-left-11" in tag and "before:w-11" in tag, "把手未向左扩出可点热区"
    assert "w-9" not in tag, "把手回退成了 36px 宽的实体按钮（用户反馈「太大了」）"
    assert "text-xs" not in tag, "把手文字过小"
    assert "py-1.5" not in tag, "把手用 py-1.5 撑高度，实际不足 44px"


def test_return_uses_history_back():
    """返回要走 history 回退，把用户送回原对话而不是新建一次导航。"""
    view = _view()
    m = re.search(r"const goBackToChat = \(\) => \{(.*?)\n\};", view, re.S)
    assert m, "缺少 goBackToChat"
    assert "router.back()" in m.group(1), "未使用 router.back()"


def test_scroll_listener_is_bound_to_container_not_window():
    """监听必须挂在滚动容器上。

    个人中心的滚动容器是 `<div class="h-full w-full overflow-y-auto">`，
    页面本身不滚动——挂到 window 上会一个事件都收不到。
    """
    view = _view()
    assert 'ref="scrollContainerRef"' in view, "滚动容器缺少 ref，无法挂监听"
    assert "scrollContainerRef.value" in view, "未通过容器 ref 取元素"
    assert "addEventListener(\"scroll\"" in view or "addEventListener('scroll'" in view, (
        "未注册 scroll 监听"
    )
    assert 'window.addEventListener("scroll"' not in view, (
        "监听挂到了 window 上——本页滚动的是容器，收不到事件"
    )


def test_back_fab_hides_on_scroll_down():
    """向下滚（看内容）时收起。"""
    view = _view()
    m = re.search(r"const handleContainerScroll = \(\) => \{(.*?)\n\};", view, re.S)
    assert m, "缺少 handleContainerScroll"
    body = m.group(1)
    assert "backFabHidden.value = true" in body, "向下滚未收起浮标"
    assert "delta > 0" in body, "未按位移方向判断"


def test_back_fab_restores_immediately_on_scroll_up():
    """向上滚（想回上面）时立即恢复。"""
    view = _view()
    m = re.search(r"const handleContainerScroll = \(\) => \{(.*?)\n\};", view, re.S)
    assert m, "缺少 handleContainerScroll"
    body = m.group(1)
    assert "delta < 0" in body, "未处理向上滚"
    assert "backFabHidden.value = false" in body, "向上滚未恢复浮标"


def test_scroll_up_cancels_pending_restore_timer():
    """向上滚必须取消待执行的兜底定时器。

    否则「立即恢复」之后，先前排队的定时器还会再触发一次——无副作用，
    但属于多余的状态跳变。
    """
    view = _view()
    m = re.search(r"const handleContainerScroll = \(\) => \{(.*?)\n\};", view, re.S)
    assert m, "缺少 handleContainerScroll"
    up_branch = m.group(1).split("delta < 0")[1]
    assert "clearRestoreTimer()" in up_branch, "向上滚分支未取消兜底定时器"


def test_back_fab_has_restore_fallback():
    """停止滚动后必须有兜底恢复。

    纯方向感知的洞：滚到底部后才想返回时，最后一段动作是向下滚，浮标恰好收起。
    用户明确要求保留这条兜底。
    """
    view = _view()
    m = re.search(r"const handleContainerScroll = \(\) => \{(.*?)\n\};", view, re.S)
    assert m, "缺少 handleContainerScroll"
    body = m.group(1)
    assert "setTimeout" in body, "缺少兜底恢复定时器"
    assert "BACK_FAB_RESTORE_MS" in body, "定时器未使用具名常量"
    assert re.search(r"const BACK_FAB_RESTORE_MS = \d+", view), "未定义恢复时长常量"


def test_scroll_jitter_threshold_and_top_guard_exist():
    """位移阈值与顶部保护都必须存在。

    阈值：移动端惯性滚动会产生大量微小反向抖动，不设阈值浮标会疯狂闪烁。
    顶部保护：刚进页面（scrollTop = 0）只向下滚一点点时，「在看内容」的语义还不成立。
    """
    view = _view()
    assert re.search(r"const SCROLL_JITTER_PX = \d+", view), "未定义抖动阈值常量"
    assert re.search(r"const SCROLL_TOP_GUARD_PX = \d+", view), "未定义顶部保护常量"
    m = re.search(r"const handleContainerScroll = \(\) => \{(.*?)\n\};", view, re.S)
    assert m, "缺少 handleContainerScroll"
    body = m.group(1)
    assert "SCROLL_JITTER_PX" in body, "未使用抖动阈值"
    assert "SCROLL_TOP_GUARD_PX" in body, "未使用顶部保护"


def test_hidden_state_is_inert_and_accessible():
    """收起态必须同时不可点且对辅助技术隐藏。

    pointer-events-none 只挡指针，挡不住屏幕阅读器——不加 aria-hidden
    会读出一个「看不见但存在」的按钮。
    """
    view = _view()
    assert "pointer-events-none" in view, "收起态缺少 pointer-events-none"
    assert "aria-hidden" in _return_button_tag(), "收起态缺少 aria-hidden"
    assert "opacity-0" in view, "收起态缺少透明度变化"


def test_scroll_listener_and_timer_are_cleaned_up():
    """卸载时必须移除监听并清掉定时器，否则泄漏。"""
    view = _view()
    assert "removeEventListener" in view, "未移除 scroll 监听"
    assert "clearRestoreTimer" in view, "未清理兜底定时器"


def test_back_fab_is_draggable_by_touch():
    """浮标必须支持触摸拖动。

    用户反馈（原文）：「还是需要能支持拖动，因为有时候会挡住我后面的按钮，
    我要手动拖开」。滚动收起解决的是「滚动时遮挡」，拖动解决的是
    「静止时压住了某个按钮」——两者正交，都要有。
    """
    view = _view()
    for event in ("touchstart", "touchmove", "touchend"):
        assert event in view, f"浮标缺少 {event} 处理"


def test_drag_position_is_not_persisted():
    """拖动位置不得持久化——用户明确要求「不需要持久化记忆」。

    刷新或重新进入即回到默认右下角。
    注：不能笼统断言「文件里没有 sessionStorage」——本页用 sessionStorage
    按 instance_id 存 ticket 凭据，那是另一件事。只断言 fab 位置没进存储。
    """
    view = _view()
    assert re.search(r"(localStorage|sessionStorage|indexedDB)[^\n]*[Ff]ab", view) is None, (
        "拖动位置被写进了存储，但用户明确要求不持久化"
    )
    assert "saveFabPosition" not in view, "出现了疑似持久化拖动位置的实现"


def test_drag_requires_threshold_before_taking_over():
    """位移越过阈值才算拖动，且拖动时接管手势。

    阈值以下不接管：否则手指在浮标上轻微滑动就变成拖动，页面也滚不动。
    """
    view = _view()
    assert re.search(r"const FAB_DRAG_THRESHOLD_PX = \d+", view), "未定义拖动阈值常量"
    m = re.search(r"const handleFabTouchMove = \(.*?\) => \{(.*?)\n\};", view, re.S)
    assert m, "缺少 handleFabTouchMove"
    body = m.group(1)
    assert "FAB_DRAG_THRESHOLD_PX" in body, "未使用拖动阈值"
    assert "preventDefault" in body, "拖动时未阻止页面滚动"


def test_drag_suppresses_the_return_click():
    """拖动后松手不得触发返回。

    否则「把挡住的按钮拖开」这一步会顺手把用户送回对话页、丢掉上下文。
    """
    view = _view()
    assert re.search(r"const FAB_CLICK_SUPPRESS_MS = \d+", view), "未定义点击抑制时长常量"
    m = re.search(r"const goBackToChat = \(\) => \{(.*?)\n\};", view, re.S)
    assert m, "缺少 goBackToChat"
    body = m.group(1)
    assert "router.back()" in body, "未使用 router.back()"
    assert "dragEndedAt" in body, "拖动后未抑制返回点击"
    assert "FAB_CLICK_SUPPRESS_MS" in body, "抑制时长未使用具名常量"


def test_drag_is_clamped_to_viewport():
    """拖动必须夹在视口内，否则浮标会被拖出屏幕、再也点不到。"""
    view = _view()
    assert re.search(r"const FAB_VIEWPORT_MARGIN_PX = \d+", view), "未定义边缘留白常量"
    m = re.search(r"const clampToViewport = \(.*?\) => \{(.*?)\n\};", view, re.S)
    assert m, "缺少 clampToViewport"
    body = m.group(1)
    assert "innerWidth" in body and "innerHeight" in body, "未按视口尺寸限制"


def test_dragged_position_switches_to_absolute_coordinates():
    """拖动后改用绝对坐标定位，未拖动时仍是默认右下角。

    绝对坐标让手指跟手，也不必从「右下角」反推默认位置。
    断言必须落在 :style 上——只查「标签里有没有 fabPos」是不够的，
    :class 里同样会出现 fabPos，那样即使删掉定位绑定也照样通过。
    """
    view = _view()
    assert "fabPos" in view, "缺少拖动位置状态"
    tag = _return_button_tag()
    assert ":style" in tag and "fabPos" in tag, "模板未把拖动位置绑到 style"
    assert "left" in tag and "top" in tag, "未使用绝对坐标"
    assert "-mt-[22px] right-0 top-1/2" in tag, "未保留默认的右侧中部位置"
    assert "touchstart" in tag, "模板未绑定触摸事件"

