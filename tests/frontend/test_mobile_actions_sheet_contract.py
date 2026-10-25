"""移动端 AI 消息操作栏与操作抽屉的展示契约。

移动端此前把「数据 / 文件」藏在「⋯ 更多」浮层里、看不到计数徽标，一行还要横向
滚动；本组契约把「移动端两个入口 + 一个四组抽屉 + 桌面侧不被误伤」钉在源码结构上。
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_continue_actions_live_in_one_shared_module():
    source = _read("frontend/src/components/chat/continueAnalysisActions.ts")
    assert "export const CONTINUE_ANALYSIS_ACTIONS" in source
    assert "export const continueActionIcon" in source
    assert "export const chatbiActionIcon" in source
    for action_id in ("visual_report", "save_markdown", "save_word", "create_skill"):
        assert f'id: "{action_id}"' in source, f"{action_id} 必须留在共享模块里"


def test_both_continue_consumers_import_the_shared_module():
    continue_source = _read("frontend/src/components/chat/MessageContinueAnalysis.vue")
    chatbi_source = _read("frontend/src/components/chatbi/ChatBIContinueAnalysis.vue")
    assert "continueAnalysisActions" in continue_source
    assert "continueAnalysisActions" in chatbi_source
    # 文案与图标映射不得再各留一份
    assert "请基于当前这条 AI 回复中已经存在的文本" not in continue_source
    assert 'visual_report: "▥"' not in continue_source
    assert 'trend: "↗"' not in chatbi_source


def _can_render_sfc() -> bool:
    if shutil.which("node") is None:
        return False
    node_modules = ROOT / "frontend/node_modules"
    return all((node_modules / name).exists() for name in ("vue", "esbuild", "@vue/server-renderer"))


def test_action_sheet_shell_owns_appearance_close_and_scroll_lock():
    source = _read("frontend/src/components/chat/MobileActionSheet.vue")
    assert '<Teleport to="body">' in source
    assert 'role="dialog"' in source
    assert 'aria-modal="true"' in source
    assert '@click.self="close"' in source
    assert "z-[260]" in source
    assert "env(safe-area-inset-bottom)" in source
    assert 'event.key === "Escape"' in source
    assert 'document.body.style.overflow = "hidden"' in source
    # 常驻挂载约束必须写在注释里，避免后来者用 v-if 包住它导致不锁滚动
    assert "常驻挂载" in source


@pytest.mark.skipif(not _can_render_sfc(), reason="前端依赖未安装，跳过真渲染探针")
def test_action_sheet_only_renders_when_open():
    script = ROOT / "tests/frontend/mobile_action_sheet_ssr.cjs"
    proc = subprocess.run(["node", str(script)], cwd=ROOT / "frontend", capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)

    closed = data["closed"]
    assert closed["hasDialog"] is False, "open=false 时不得渲染抽屉"
    assert closed["hasBackdrop"] is False, "open=false 时不得渲染遮罩"
    assert closed["hasSlot"] is False, "open=false 时插槽内容不得渲染"
    assert closed["hasCancel"] is False

    opened = data["opened"]
    assert opened["hasDialog"] is True
    assert opened["hasAriaModal"] is True
    assert opened["hasBackdrop"] is True, "遮罩必须带 z-[260] 全屏定位"
    assert opened["hasSafeArea"] is True, "贴底容器必须处理 iPhone 安全区"
    assert opened["hasTitle"] is True
    assert opened["hasSubtitle"] is True
    assert opened["hasSlot"] is True, "默认插槽内容必须真的渲染"
    assert opened["hasCancel"] is True


def test_actions_sheet_content_has_four_sections_and_focus_highlight():
    source = _read("frontend/src/components/chat/MobileMessageActionsSheet.vue")
    for anchor in ("data", "continue", "call", "report"):
        assert f'data-section="{anchor}"' in source, f"缺少分组锚点 {anchor}"
    assert "props.focus === 'data'" in source, "focus=data 时必须走高亮分支"
    assert "border-l-2 border-primary" in source
    # 空组不渲染
    assert 'v-if="canExport"' in source
    assert 'v-if="hasTrace"' in source
    # ChatBI 与通用两套动作共用同一渲染，但事件要分开
    assert "chatbiAction" in source
    assert "continueAnalysis" in source
    assert "continueActionIcon" in source
    assert "chatbiActionIcon" in source


def test_embedchat_mobile_bar_exposes_data_entry_and_sheet_trigger():
    source = _read("frontend/src/views/EmbedChat.vue")
    # 移动端「▤ 数据」入口：会话有数据 / 文件时才出现
    assert 'v-if="hasConversationDataFile"' in source
    # 「▤ 数据」三分支分派：有徽标进抽屉；无徽标但有调用统计→查看调用详情；连统计都没有→仍进抽屉
    assert "const hasMessageDataBadge = (msg: Message): boolean" in source
    assert "const hasMessageTokenStats = (msg: Message): boolean" in source
    assert "const handleMobileDataClick = (msg: Message)" in source
    assert "hasMessageDataBadge(msg) || !hasMessageTokenStats(msg)" in source
    assert '@click="handleMobileDataClick(msg)"' in source
    # aria-label 必须与分派同源，不得各自判断（否则会出现「说的」和「做的」不一致）
    assert "const mobileDataAriaLabel = (msg: Message): string" in source
    assert ':aria-label="mobileDataAriaLabel(msg)"' in source
    # 防回退：不得再出现只按徽标判断的旧三元
    assert "? openMobileActions(msg, 'data') : openModelCallStats(msg)" not in source
    # 移动端「⋯ 更多」改为打开同一个抽屉
    assert '@click="openMobileActions(msg, null)"' in source
    assert 'v-if="hasMobileSheetContent(msg)"' in source
    assert ':open="mobileActionsMessageId === msg.id"' in source
    # 与操作栏其它按钮一致的 ghost 风格（用户反馈：其它按钮都没边框）
    assert 'class="sm:hidden flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-gray-500' in source
    assert (
        "rounded-md border border-primary/30 bg-primary/5 px-2 py-1.5 text-[11px] font-medium text-primary" not in source
    ), "数据入口不得再带边框"


def test_embedchat_mobile_bar_shows_inline_usage_as_icon_plus_value():
    """移动端操作栏右侧还有空位：用量要露在行内，格式为「圆柱图标 + 数据」，不重复「用量」二字。"""
    source = _read("frontend/src/views/EmbedChat.vue")
    marker = 'sm:hidden flex shrink-0 items-center gap-1 whitespace-nowrap'
    assert source.count(marker) == 1, "移动端用量按钮必须唯一（多一个就会串到别的行）"
    start = source.index(marker)
    start = source.rindex("<button", 0, start)  # 从按钮起始标签开始切，v-if 在 class 之上
    block = source[start : start + 1600]

    # 与桌面「用量」按钮同一判据，且点击复用同一个调用明细弹层
    assert 'v-if="hasMessageTokenStats(msg)"' in block
    assert '@click="openModelCallStats(msg)"' in block
    # 图标 + 数据：数据文案不带「用量」前缀（移动端空间紧，图标已表意）
    assert "<span>{{ getMessageTokenAmount(msg) }}</span>" in block
    assert "用量 {{ getMessageTokenAmount(msg) }}" not in block
    assert 'class="w-3.5 h-3.5 shrink-0 text-gray-400 dark:text-gray-500"' in block
    # 移动端触摸没有 hover，语义交给 aria-label（不得只靠原生 title）
    assert ':aria-label="`查看本次调用 Token 用量：${getMessageTokenAmount(msg)}`"' in block

    # 位置必须在「▤ 数据」之后、「⋯ 更多」之前
    assert "▤" in source[:start], "用量应排在移动端「▤ 数据」之后"
    assert source.index('@click="openMobileActions(msg, null)"', start) != -1, "用量应排在「⋯ 更多」之前"

    # 桌面侧保持原样：仍有「用量」文字，且仍被限制在 sm 以上
    assert "<span>用量 {{ getMessageTokenAmount(msg) }}</span>" in source
    assert 'class="hidden sm:flex shrink-0 items-center space-x-1"' in source


def test_embedchat_mobile_bar_does_not_become_a_vertical_scroll_area():
    """操作栏只该横向滚动，绝不能长出一条纵向滚动条。

    CSS 规范：`overflow-x` 不是 visible 时，`overflow-y: visible` 会被**计算**成 `auto`。
    因此 `overflow-x-auto` 的容器其实是双向滚动容器——只要容器内有任何纵向溢出的子节点，
    就会冒出纵向滚动条、并让这块区域能被上下拖动（用户实测反馈：「横条滚动我明白，
    为什么还有上下固定条」，截图里操作栏右侧/底部多出灰条）。
    纵向溢出的来源就是 4 个 hover 气泡（`absolute top-full`，向下伸出 20+px）；
    它们挂在 `group-hover` 上，触摸端（<640px）根本不会触发，却实打实地撑高了滚动区域。
    所以锁两件事：气泡在移动端不渲染，容器在移动端显式纵向裁剪。
    """
    source = _read("frontend/src/views/EmbedChat.vue")

    # ① 4 个操作栏 hover 气泡（复制 / 重新生成 / 赞 / 踩）移动端必须不渲染
    for label in ("复制", "重新生成", "很有帮助", "回答不准确"):
        pattern = (
            r'transition-all duration-150 hidden sm:flex flex-col items-center z-50[^\n]*\n'
            r'\s*<div class="w-1\.5 h-1\.5[^\n]*\n'
            r'\s*<div class="rounded-md[^\n]*\n'
            rf'\s*{label}'
        )
        assert re.search(pattern, source), f"「{label}」气泡未在移动端隐藏，会继续撑高操作栏"

    # ② 容器移动端纵向裁剪、桌面恢复 visible（桌面靠 overflow-x-visible 放气泡，不能被裁）
    bar = re.search(r'class="flex min-w-0 max-w-full flex-nowrap items-center space-x-2[^"]*"', source)
    assert bar, "未找到消息操作栏容器"
    classes = bar.group(0)
    assert "overflow-x-auto" in classes, "横向滚动必须保留"
    assert "overflow-y-hidden" in classes, "移动端必须纵向裁剪，否则 overflow-y 被提升为 auto 出纵向滚动条"
    assert "sm:overflow-x-visible" in classes, "桌面横向不得滚动（否则浮层被裁）"
    assert "sm:overflow-y-visible" in classes, "桌面纵向必须可见，否则 hover 气泡被裁掉"

    # 防回退：操作栏切片内不得再有「移动端也渲染的」旧气泡写法
    # （页面别处还有 header 的桌面专属气泡，不在 scroll 容器内，不在此列）
    bar_start = bar.start()
    bar_end = source.index('@click="openMobileActions(msg, null)"', bar_start)
    assert "transition-all duration-150 flex flex-col items-center z-50" not in source[bar_start:bar_end]


def test_embedchat_mobile_sheet_content_and_breakpoint_close():
    source = _read("frontend/src/views/EmbedChat.vue")
    assert "MobileActionSheet" in source
    assert "MobileMessageActionsSheet" in source
    assert "const hasMobileSheetContent = (msg: Message): boolean" in source
    assert "const handleSheetChatbiAction = (action: ChatBIInsightAction, msg: Message)" in source
    # 跨断点必须关掉抽屉。
    # 不绑定写法：单行 `if (...) closeMobileActions();` 与代码块两种写法都算通过——
    # 头部「更多」抽屉收进同一个 if 后，这里从单行变成了块。
    match = re.search(r"if \(wasMobile !== isMobile\.value\)\s*(\{[^}]*\}|[^\n;]*;)", source)
    assert match, "未找到跨断点收起抽屉的逻辑"
    assert "closeMobileActions()" in match.group(1), "跨断点未收起消息操作抽屉"


def test_existing_drawers_reuse_the_shared_shell():
    continue_source = _read("frontend/src/components/chat/MessageContinueAnalysis.vue")
    chatbi_source = _read("frontend/src/components/chatbi/ChatBIContinueAnalysis.vue")
    for name, source in (
        ("MessageContinueAnalysis", continue_source),
        ("ChatBIContinueAnalysis", chatbi_source),
    ):
        assert "MobileActionSheet" in source, f"{name} 应复用公共抽屉壳"
        # 自绘遮罩必须消失，只剩桌面浮层
        assert "bg-black/35" not in source, f"{name} 的遮罩应完全交给壳"
        assert 'class="fixed inset-0 z-[260]' not in source, f"{name} 仍在自绘移动端遮罩"
        # 桌面浮层保留
        assert "absolute bottom-full right-0" in source, f"{name} 的桌面浮层不得被删掉"


def test_actions_sheet_content_closes_before_running_its_action():
    source = _read("frontend/src/components/chat/MobileMessageActionsSheet.vue")
    assert "const run = (action: () => void)" in source, "缺少与桌面 MessageActionMenus 一致的 run 辅助"
    assert 'emit("close");' in source, "run 必须先请求关闭抽屉"
    assert source.count('@click="run(() => emit(') == 6, "六个直接动作都必须经过 run"
    assert '@click="run(() => selectContinue(action))"' in source
    assert '(event: "close"): void' in source


def test_embedchat_wires_sheet_content_close_to_the_sheet():
    source = _read("frontend/src/views/EmbedChat.vue")
    start = source.index("<MobileMessageActionsSheet")
    block = source[start : start + 3000]
    assert '@close="closeMobileActions"' in block, "内容组件的 close 必须接到 closeMobileActions"


def test_embedchat_renders_legacy_continue_components_in_desktop_form_only():
    """EmbedChat 的移动端入口统一走操作抽屉；调试页没有抽屉，必须保留移动端形态。"""
    source = _read("frontend/src/views/EmbedChat.vue")
    # 触发按钮被 hidden sm:flex 隐藏 ⇒ 组件移动端形态不可达，恒传 false，别再多挂空抽屉壳
    assert source.count(':is-mobile="false"') == 2, "两个继续分析组件都应恒按桌面形态渲染"
    assert ':is-mobile="isMobile"' not in source
    # 反向保护：调试页仍按真实宽度传值，避免联带删掉它的移动端抽屉
    debug = _read("frontend/src/views/AgentDebug.vue")
    assert ':is-mobile="isMobile"' in debug, "调试页仍需移动端形态，不得联带清理"
