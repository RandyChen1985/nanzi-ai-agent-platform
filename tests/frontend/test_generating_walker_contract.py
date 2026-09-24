"""生成中提示的「二进制拖尾小人」动画契约。

输入框在生成态会把 textarea 整块隐藏，只剩一行 11px 的提示文案。用户反馈
「不够丰富、没有 AI 科技感」，因此在其下方加一条跑道：一个小人从左侧走到
右侧，身后拖出渐隐的 0/1，跑道带流动刻度与极淡网格。

组件本身是纯 CSS 动画，无法做行为单测，这里沿用仓库既有的契约测试做法，
锁住「动画骨架齐全」「无障碍兜底存在」「已被 ChatInput 接入」这三类不可回退
的结构，避免后续重构时静默丢掉关键帧或降低无障碍支持。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
WALKER = "frontend/src/components/embed/GeneratingWalker.vue"
CHAT_INPUT = "frontend/src/components/embed/ChatInput.vue"
CHAT_SETTINGS = "frontend/src/components/embed/ChatSettings.vue"
EMBED_CHAT = "frontend/src/views/EmbedChat.vue"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_generating_walker_component_exists():
    assert (ROOT / WALKER).is_file()


def test_walker_ships_the_full_walk_cycle():
    """位移、摆腿、身体起伏三者缺一，走路的观感就不成立。"""
    source = _read(WALKER)
    assert "@keyframes" in source
    assert "gw-walk" in source
    assert "gw-swing" in source
    assert "gw-bob" in source
    # 两条腿必须反相，否则是蹦而不是走。
    assert "animation-delay" in source


def test_walker_trails_binary_digits():
    """身后渐隐的 0/1 是「AI 正在输出 token」的核心视觉隐喻。"""
    source = _read(WALKER)
    assert ">0</i>" in source
    assert ">1</i>" in source
    # 数字必须等宽，否则拖尾会边跑边抖。
    assert "ui-monospace" in source


def test_walker_respects_reduced_motion():
    """无障碍兜底：系统偏好减少动效时必须退回静态展示。

    仓库内已有 5 处 `prefers-reduced-motion` 约定，这里不能破例。
    """
    source = _read(WALKER)
    assert "prefers-reduced-motion" in source


def test_walker_supports_dark_mode():
    source = _read(WALKER)
    assert (
        "dark:" in source
        or ":global(.dark)" in source
        or "prefers-color-scheme" in source
    )


def test_grid_fade_lives_on_its_own_layer_not_the_lane_root():
    """网格的「淡」必须由独立图层承担，不能挂在跑道根节点上。

    CSS 的 opacity 是层叠相乘的：根节点一旦设成 0.06，整棵子树（含小人和 0/1
    拖尾）都会一起被压到 6%，子元素再声明 opacity: 1 也救不回来。这个坑不会
    报错、不会让测试变红，只会让动画「几乎看不见」。
    """
    source = _read(WALKER)
    assert 'class="gw-grid"' in source
    # 跑道根节点不得声明 opacity，淡只能来自 .gw-grid 自己。
    lane_block = source[source.index(".gw-lane {") : source.index(".gw-bus {")]
    assert "opacity" not in lane_block


def test_chat_input_mounts_walker_while_locked():
    source = _read(CHAT_INPUT)
    assert "import GeneratingWalker" in source
    assert "<GeneratingWalker" in source


def test_chat_input_keeps_hint_and_walker_in_separate_rows():
    """跑道必须在提示行的**下方**独立成行，不能和文案挤在同一行。

    同行的结果是：小人会从「AI 正在努力生成回复中…」这行字上横穿过去。
    """
    source = _read(CHAT_INPUT)
    locked_block_start = source.index("v-if=\"isInteractionLocked\"")
    locked_block = source[locked_block_start : locked_block_start + 900]
    # 提示行自成一个 flex 行，跑道是它的兄弟节点而不是同一行里的元素。
    assert "flex items-center" in locked_block
    assert locked_block.index("flex items-center") < locked_block.index(
        "<GeneratingWalker"
    )


def test_walker_does_not_sway_or_pause():
    """中途停顿摇摆做过、又被用户否掉了：小人应该一路走到底。

    这里用反向断言是刻意的——防止「摇头晃脑」这类效果在后续迭代里被重新加回来。
    """
    source = _read(WALKER)
    assert "gw-sway" not in source

    walk_block = source[source.index("@keyframes gw-walk") : source.index(".gw-creature")]
    # left 只应出现在起点与终点；多于两处即意味着中间又插了停顿段。
    assert walk_block.count("left:") == 2


def test_walker_duration_is_adaptive_not_hardcoded():
    """时长必须由 CSS 变量驱动（按跑道宽度算），不能写死秒数。

    写死秒数是「电脑端跑太快」的根因：宽度越大速度越快。
    """
    source = _read(WALKER)
    assert "--gw-duration" in source
    assert "var(--gw-duration)" in source
    # 必须真的去量跑道宽度，否则变量无从计算。
    assert "ResizeObserver" in source
    # 保留无 JS 时的兜底：默认值写在样式里，SSR/首帧不会出现零时长。
    assert "--gw-duration:" in source


def test_settings_panel_exposes_the_generating_animation_toggle():
    """设置面板要能关掉生成中动画。

    系统级 `prefers-reduced-motion` 覆盖不到「系统没设置、但个人就是不喜欢
    这个动效」的人，所以需要一个应用内开关兜住。
    """
    source = _read(CHAT_SETTINGS)
    assert "生成中动画" in source
    assert "handleSetGeneratingAnimation" in source
    assert "showGeneratingAnimation" in source


def test_generating_animation_defaults_on_and_persists():
    """默认开启（新功能要被看见），关闭后写进 localStorage 并在启动时读回。"""
    source = _read(EMBED_CHAT)
    assert "showGeneratingAnimation: true" in source
    assert 'localStorage.setItem("yovole_show_generating_animation"' in source
    assert 'localStorage.getItem("yovole_show_generating_animation")' in source
    # 必须真的把开关接到输入框上，否则只是存了一个没人读的布尔值。
    assert ':show-generating-animation="config.showGeneratingAnimation"' in source


def test_chat_input_renders_walker_only_when_enabled():
    """关掉动画时，三点 loading 提示必须保留，不能连加载状态一起消失。"""
    source = _read(CHAT_INPUT)
    assert "showGeneratingAnimation?: boolean;" in source
    assert 'v-if="showGeneratingAnimation"' in source


def test_input_box_has_no_pulsing_glow():
    """用户明确不要输入框「一闪一闪的光晕」。

    生成态的静态边框高亮（border-primary/60）保留——那不是闪烁，是状态标识。
    """
    source = _read(CHAT_INPUT)
    assert "input-glow-processing" not in source
    assert "glow-pulse" not in source


def test_generating_composer_stays_subdued():
    """用户反馈生成态输入框「过于亮」，定档 B：白底 + 跑道整体退后。

    真正抢眼的其实不是蓝底，而是横跨整行、重复三十多组的刻度虚线；两者叠
    在一起才显得满。所以这里同时锁住「去蓝底」与「刻度/网格压淡」两件事。
    """
    input_source = _read(CHAT_INPUT)
    # 生成态不再铺淡蓝底，状态识别交回给边框。
    assert "bg-blue-50/30" not in input_source
    assert "dark:bg-blue-950/20" not in input_source

    walker_source = _read(WALKER)
    # 刻度由 0.45 压到 0.26、网格由 0.07 压到 0.04（深色模式 0.12 → 0.07）。
    assert "opacity: 0.26" in walker_source
    assert "opacity: 0.04" in walker_source
    assert "opacity: 0.07" in walker_source


def test_focus_hint_is_neutral_not_brand_blue():
    """用户反馈「其他都是浅色，蓝色就感觉特别亮」：聚焦提示改用中性灰。

    注意生成态的蓝色边框**保留**——那是状态色（正在生成），与聚焦这种纯交互
    反馈分工不同，两者分开反而更好辨认。
    """
    source = _read(CHAT_INPUT)
    assert "focus-within:border-primary" not in source
    assert "focus-within:ring-primary/25" not in source
    assert "focus-within:border-gray-400" in source
    assert "focus-within:ring-gray-900/5" in source
    # 深色模式要配套，不能只改浅色。
    assert "dark:focus-within:border-gray-500" in source
    assert "dark:focus-within:ring-gray-100/10" in source


def test_focus_hint_is_disabled_while_generating():
    """生成态要禁用聚焦光环，避免与状态色边框叠成两层。

    做法是把全部 focus-within 规则挪进非生成分支，而**不是**加 `ring-0` 去覆盖
    `ring-2`——同组的 ring 工具类胜负由 Tailwind 的生成顺序决定，不取决于 class
    的书写顺序，用 ring-0 覆盖并不可靠。
    """
    source = _read(CHAT_INPUT)

    base_class_line = next(
        line
        for line in source.splitlines()
        if "transition-all duration-300" in line and "dark:bg-gray-800" in line
    )
    # 基础样式挂了聚焦规则的话，生成态也会一起命中。
    assert "focus-within" not in base_class_line

    idle_line = next(
        line for line in source.splitlines() if "border-gray-200 dark:border-gray-700" in line
    )
    assert "focus-within:ring-2" in idle_line

    processing_line = next(
        line
        for line in source.splitlines()
        if "'border-gray-300 dark:border-gray-600'" in line
    )
    assert "focus-within" not in processing_line


def test_generating_state_uses_neutral_palette():
    """用户要求生成态整体去蓝：边框、三点、文案、跑道全部改中性灰。

    生成态不再承载品牌色——「正在生成」由灰度与动画表达，颜色留给真正需要
    强调的地方（发送按钮等）。注意 `--primary-color` 在项目别处仍在使用，
    这里只收掉生成态自己这几处。
    """
    input_source = _read(CHAT_INPUT)

    # ① 生成态边框不再是品牌蓝
    assert "'border-primary/50'" not in input_source
    assert "border-gray-300 dark:border-gray-600" in input_source

    # ② 提示文案不再用主色
    assert "text-primary/70" not in input_source
    assert "text-gray-500 dark:text-gray-400" in input_source

    # ③ 三点跳动改中性灰
    dot_block = input_source[input_source.index(".ai-dot {") :][:240]
    assert "#9ca3af" in dot_block
    assert "var(--primary-color" not in dot_block

    # ④ 跑道不再引用主题色，且深色下的小人辉光（蓝）一并去掉
    walker_source = _read(WALKER)
    assert "var(--primary-color" not in walker_source
    assert "#9ca3af" in walker_source
    assert "#6cb2ff" not in walker_source
    assert "#7cb8ff" not in walker_source
    assert "drop-shadow" not in walker_source
