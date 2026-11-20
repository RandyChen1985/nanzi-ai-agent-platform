"""生图 / 改图「正在出图」占位骨架的契约。

背景：一次出图可能要十几秒，而且**要花一次钱**。改造前这段时间消息区完全空白，
唯一反馈是折叠卡片里一行「调用工具 · generate_image」，用户既不知道在做什么，
也不知道要等多久。这里补一块占位骨架，把等待变成可解释的。

契约钉住四件事：

1. **只在工具真的还在跑时出现**：完成、失败、历史回放都不许挂占位，否则会和真图叠在一起；
2. **比例信息不能丢，但不再决定框的高度**：框是一条固定高度的矮横幅（2026-11-20 改），
   宽高比取自请求的 size、画成框内的虚线幽灵框；给不出时写「?」而不是假装方形；
3. **不编百分比**：后端没有真实进度，只给「阶段 + 已用秒数 + 不确定进度条」；
4. **占位是纯视觉**：不发请求、不读配置、不影响超时预算，去掉它功能行为完全不变。
"""

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
UTIL = ROOT / "frontend/src/utils/imageGenerationPlaceholder.ts"
COMPONENT = ROOT / "frontend/src/components/chat/ImageGenerationPlaceholder.vue"
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
PROBE = ROOT / "tests/frontend/image_generation_placeholder_probe.cjs"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_placeholder_resolution_probe_passes():
    """识别逻辑必须真跑：该不该出现、取哪一条、比例对不对，读源码看不出来。"""
    if not (ROOT / "frontend/node_modules").exists():
        pytest.skip("前端依赖未安装")
    proc = subprocess.run(
        ["node", str(PROBE)], cwd=str(ROOT), capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_placeholder_is_visual_only():
    """占位不许有副作用：一旦它去发请求或改超时，就变成了行为变更而不是视觉占位。"""
    source = _read(COMPONENT)
    for forbidden in ("axios", "fetch(", "@/api/", "setTimeout(", "AbortController"):
        assert forbidden not in source, f"占位组件不该出现 {forbidden}"
    # 它只允许有一个定时器：500ms 心跳（秒表）。
    assert source.count("setInterval(") == 1


def test_placeholder_announces_stage_but_not_stopwatch():
    """读屏只听阶段，不听秒表：500ms 一次的播报会把真正要听的内容盖掉。"""
    source = _read(COMPONENT)
    assert 'role="status"' in source
    assert 'aria-live="polite"' in source
    assert 'aria-busy="true"' in source

    # 秒表所在标签必须是 aria-hidden（否则每 500ms 触发一次 live region 更新）。
    time_mark = source.index("{{ elapsedText }}")
    window = source[max(0, time_mark - 400) : time_mark]
    assert "aria-hidden" in window, "秒表要 aria-hidden，只给人看"


def test_placeholder_respects_reduced_motion():
    source = _read(COMPONENT)
    assert "prefers-reduced-motion" in source
    # 关掉动效后骨架仍在（用户还能看到「在出图」），只是不再扫光。
    assert "animation: none" in source


def test_placeholder_band_keeps_ratio_as_ghost():
    """矮横幅不再按出图比例定高；比例信息改由幽灵框承载，且不再靠 60vh 反算。

    背景（2026-11-20，用户提「高度减半、宽度增加」）：旧算法 `min(100%, 420px, 60vh×比例)`
    在方形兜底时桌面就是 420×420，占掉近半屏，而成品图被 `.markdown-body img` 压到最高
    320px——骨架比成品还大，图落地时是「缩一下」。而「高度减半」与「宽度增加」在
    「框必须同比例」的前提下数学上矛盾，所以框与比例解耦：框固定一档高度，比例改由框内
    按目标比例缩放的虚线空框承载。
    """
    component = _read(COMPONENT)

    # 框：宽度上限放宽到 560px，高度用 clamp 按屏宽分档，两者都不再由比例决定。
    assert 'width: min(100%, 560px)' in component, "宽度上限要从 420px 放宽到 560px"
    assert "height: clamp(" in component, "高度要按屏宽分档，手机上自动收窄"
    assert "60vh" not in component, "高度改成矮横幅后不该再有 60vh 反算"
    assert "aspectRatio: ratio," not in component, "框自身不许再按比例定高"

    # 比例信息仍在：幽灵框用请求尺寸画形状，未知时写「?」而不是假装方形。
    assert "imgph-ghost" in component
    assert "aspectRatio: ghostRatio" in component, "幽灵框要按请求尺寸定形状"
    assert 'ratioLabel || "?"' in component, "尺寸未知时写问号，不假装是方图"

    util = _read(UTIL)
    assert "sizeToRatioLabel" in util
    assert "1664 / 928" not in util  # 不把某个尺寸写进代码


def test_ratio_label_is_readable():
    """比例标签要写给人看：928x1664 应显示 9:16，而不是约分后的 29:52。"""
    util = _read(UTIL)
    assert "COMMON_RATIOS" in util, "常见比例对要按容差匹配，不能只做约分"


def test_placeholder_uses_dot_matrix_visual():
    """视觉是「点阵 + 汇聚亮环」，不许退回一大块实心灰底。

    第一版是 #f3f4f6 铺满整块 + 中心白卡片，用户的原话是「只是一个纯灰色？不好看」——
    大面积均匀灰读起来像加载失败的空白框。现在的层次全部由点阵承担：底是白（深色是暗底），
    点靠粗细与浓淡区分远近，亮环用径向遮罩挖出环带、靠 scale 从外向内收。
    """
    source = _read(COMPONENT)

    # 点阵：radial-gradient 画点，间距固定（不能随容器缩放，否则大图上点会糊成一片）。
    assert "radial-gradient" in source
    assert "background-size: 14px 14px" in source, "点阵间距要固定"

    # 汇聚环带：径向遮罩挖出环形，scale 由外向内收。
    assert ".imgph-gather" in source
    assert "mask-image" in source, "环带靠遮罩挖出来，不是画一个圆"
    assert "imgph-gather-in" in source

    # 实心灰底与扫光是上一版被否掉的做法，不能回潮。
    assert "bg-gray-100" not in source, "骨架底不许再是实心灰块"
    assert "imgph-skel" not in source, "旧的灰块 + 扫光实现已废弃"
    assert "imgph-shimmer" not in source

    # 不确定性用跳点表达；颜色/间距走 CSS 变量，深色模式只换变量不换结构。
    assert "imgph-udots" in source
    assert "--imgph-dot" in source, "深色模式要靠变量切换点色"
    assert "--imgph-dot-hi" in source


def test_placeholder_has_no_fake_percentage():
    """不编百分比：编出来的数字会在长尾请求上打脸。"""
    util = _read(UTIL)
    component = _read(COMPONENT)
    for source in (util, component):
        for forbidden in ("progressPercent", "Percent", "Math.random"):
            assert forbidden not in source
        assert "width: ${" not in source  # 进度条宽度不随"进度"变化


def test_placeholder_recognises_tool_from_title():
    """工具进行中只有 title、没有 tool_name——只认字段的话骨架永远不会出现。

    这是线上真实踩到的：后端 `start_log` 只下发 `title: "调用工具: generate_image"`，
    `tool_name` 要等完成事件才有，而占位恰恰只在完成前那段时间需要判断工具名。
    """
    util = _read(UTIL)
    assert "toolNameFromTitle" in util, "标题兜底不能被去掉"

    match = util[util.index("    // tool_name 优先"):][:260]
    assert "log.tool_name" in match
    assert "toolNameFromTitle(log.title)" in match, "字段为空时必须回落到标题"

    # 两种标题形态都要认（冒号来自 start_log，中点是前端展示格式）。
    assert "调用工具" in util


def test_placeholder_is_wired_into_the_message_stream():
    """接在时间线下方、正文上方：与最终图片位置一致，图落地不会来回跳。"""
    source = _read(EMBED_CHAT)
    assert 'import ImageGenerationPlaceholder from "@/components/chat/ImageGenerationPlaceholder.vue";' in source
    assert 'import { resolveImagePlaceholder } from "@/utils/imageGenerationPlaceholder";' in source

    timeline_at = source.index("<ChatExecutionTimeline")
    placeholder_at = source.index('<ImageGenerationPlaceholder :spec="imagePlaceholderFor(msg)" />')
    body_at = source.index('<ToolPermissionCard', timeline_at)
    assert timeline_at < placeholder_at < body_at

    # 派生函数必须是纯的：只读时间线，不含计时（计时归组件）。
    helper_at = source.index("const imagePlaceholderFor")
    helper = source[helper_at : helper_at + 500]
    assert "resolveImagePlaceholder(msg.processTimeline)" in helper
    assert "Date.now()" not in helper


def test_placeholder_hidden_while_waiting_for_permission():
    """等用户批准工具权限时，工具根本没开始跑：这时候不能写「正在绘制」。"""
    source = _read(EMBED_CHAT)
    helper_at = source.index("const imagePlaceholderFor")
    helper = source[helper_at : helper_at + 500]
    assert "pendingPermission" in helper, "权限卡在的时候必须先让位"


def test_stopwatch_prefers_backend_start_time():
    """后端给的 started_at 比挂载时刻准：组件要优先用它，缺失才回落。"""
    source = _read(COMPONENT)
    assert "startedAtMs ?? mountedAt" in source
