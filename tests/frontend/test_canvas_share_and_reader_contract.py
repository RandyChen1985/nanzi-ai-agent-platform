"""画布顶栏整理 + 阅读体验的接线契约。

这些是纯前端改动，跑不了真浏览器，所以把「结构不能退回去」的点固化成源码断言：
顶栏收敛规则、分享入口的前置条件、保存能力范围、阅读外壳的存在性。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


CANVAS = "frontend/src/components/embed/ChatCanvas.vue"
READER = "frontend/src/components/embed/CanvasReaderShell.vue"


def _header_actions() -> str:
    """取出画布顶栏右侧那一段（⋯ / 钉住 / 全屏 / 关闭 所在的容器）。"""
    source = _read(CANVAS)
    start = source.index('<div class="flex items-center space-x-1.5 shrink-0">')
    end = source.index("</div>\n      </div>", start)
    return source[start:end]


# ---------------------------------------------------------------------------
# 顶栏收敛：对文件做的事进 ⋯，只留窗口级控制
# ---------------------------------------------------------------------------


def test_header_keeps_only_window_controls():
    """顶栏显性图标只剩「⋯ + 钉住 + 全屏 + 关闭」。

    原来 6 个同级图标（保存 / 复制 / 下载 / 钉住 / 全屏 / 关闭）再加分享就是 7 个，
    窄屏必然挤成一片小点。这条断言防的是「顺手再加一个图标」把它退回去。
    """
    header = _header_actions()
    # 这些 title 是三元表达式（钉住/未钉住各一句），不能拿 title="..." 整段匹配
    assert "取消钉住" in header, "顶栏缺少钉住按钮"
    assert "全屏展示" in header, "顶栏缺少全屏按钮"
    assert 'title="关闭画布"' in header, "顶栏缺少关闭按钮"
    assert "更多操作" in header, "顶栏必须有 ⋯ 菜单入口"


def test_header_has_no_standalone_file_actions():
    """保存 / 复制 / 下载 的独立图标不能回到顶栏（只能出现在 ⋯ 菜单里）。"""
    header = _header_actions()
    for label in ("复制代码", "复制链接", "下载数据", "下载文件", "保存到目录"):
        assert f'title="{label}"' not in header, f"{label} 应该只在 ⋯ 菜单里"


def test_more_menu_offers_share_as_first_item():
    source = _read(CANVAS)
    # 用带 class 的完整标签定位：裸的 data-canvas-more 会先命中 script 里那个 closest() 选择器
    menu_start = source.index('<div class="relative" data-canvas-more>')
    menu = source[menu_start : menu_start + 5000]
    assert "分享文件" in menu
    # 分享必须排在下载之前：它是这个菜单存在的主要理由
    assert menu.index("分享文件") < menu.index("下载数据")


def test_share_requires_a_workspace_path():
    """没有 sourcePath 就不能分享——后端按路径 + 指纹分享，凭空的内容没有可分享的对象。"""
    source = _read(CANVAS)
    assert "const canShareFile = computed(() => !!effectiveSourcePath.value)" in source
    assert "请先保存到工作区，再分享" in source
    # 菜单项要置灰而不是点了才报错
    assert ':disabled="!canShareFile"' in source


def test_share_dialog_is_mounted_with_target():
    source = _read(CANVAS)
    assert "import ShareFileDialog from '@/components/ShareFileDialog.vue'" in source
    assert ':file-path="shareTarget?.path' in source
    assert '@created="handleShareCreated"' in source


def test_share_dialog_sits_outside_the_panel_teleport():
    """分享弹窗必须在 teleport 之外挂载。

    画布面板本身是 teleport 到 body 的 fixed 容器且有层叠上下文，
    弹窗放进去会被面板限制，钉住时容易被侧栏压住。
    """
    source = _read(CANVAS)
    teleport_end = source.index("</teleport>")
    assert source.index("<ShareFileDialog") > teleport_end


# ---------------------------------------------------------------------------
# 保存能力：生成型内容不再只有 code / html 能存
# ---------------------------------------------------------------------------


def test_generated_content_types_expanded():
    source = _read(CANVAS)
    assert "SAVEABLE_GENERATED_TYPES" in source
    for kind in ("'code'", "'html'", "'csv'", "'mermaid'", "'image'"):
        assert kind in source.split("SAVEABLE_GENERATED_TYPES")[1][:200], f"{kind} 未纳入保存范围"


def test_image_save_goes_through_upload_not_text_write():
    """图片的 content 是地址不是内容：走 create-entry 会写坏文件，必须走 upload。"""
    source = _read(CANVAS)
    assert "resolveGeneratedImageBlob" in source
    assert "uploadToWorkspaceDir(parentPath, file)" in source


def test_generated_filename_extensions_cover_new_types():
    source = _read("frontend/src/utils/workspaceFilePreview.ts")
    assert "type === 'csv') extension = '.csv'" in source
    assert "type === 'mermaid') extension = '.mmd'" in source
    assert "type === 'image') extension = '.png'" in source


# ---------------------------------------------------------------------------
# 阅读体验：大纲 / 字号 / 限宽 / 图片错误卡
# ---------------------------------------------------------------------------


def test_reader_shell_wraps_markdown_preview():
    source = _read(CANVAS)
    assert "import CanvasReaderShell from '@/components/embed/CanvasReaderShell.vue'" in source
    assert '<CanvasReaderShell' in source
    assert ':scale="readerScale"' in source
    assert ':narrow="readerNarrow"' in source


def test_reader_prefs_are_persisted():
    source = _read(CANVAS)
    assert "canvas:reader-scale" in source
    assert "canvas:reader-narrow" in source
    assert "localStorage.setItem" in source


def test_reader_scale_is_bounded():
    """字号要有上下界，否则连点几次就把正文放到一屏一个字。"""
    source = _read(CANVAS)
    assert "Math.min(1.4, Math.max(0.9" in source


def test_reader_controls_only_show_for_markdown_preview():
    """代码、图片、Office 有自己的显示逻辑，不该出现字号按钮。"""
    source = _read(CANVAS)
    assert "const showReaderControls = computed(() => isMarkdownContent.value && activeTab.value === 'preview')" in source
    assert 'v-if="showReaderControls"' in source


def test_outline_built_from_rendered_dom():
    """大纲必须扫渲染后的 DOM：标题可能散在多个 markdown 段、还被 mermaid 图表隔开。"""
    source = _read(READER)
    assert "querySelectorAll<HTMLElement>('h1, h2, h3')" in source
    assert "IntersectionObserver" in source


def test_image_error_uses_capture_listener():
    """error 事件不冒泡：挂在容器上做委托只有捕获阶段才收得到。"""
    source = _read(READER)
    assert "@error.capture=" in source
    assert "canvas-img-error" in source


def test_code_block_toolbar_has_copy_and_wrap():
    source = _read(READER)
    assert "canvas-code-bar" in source
    assert "复制" in source and "换行" in source
    # 行号是刻意不做的：语法高亮跨行，加行号得重写渲染
    assert "行号没做" in source


def test_reader_narrow_limits_line_length():
    source = _read(READER)
    assert "max-width: 660px" in source


def test_binary_workspace_file_still_carries_source_path():
    """xlsx / pdf / 图片从工作区打开时也必须带 sourcePath。

    这条是为一个真实 bug 加的：`shouldAttachWorkspaceSourcePath` 曾经先要求
    `canWriteWorkspaceFile`，把「能不能文本编辑并写回」当成了「是不是工作区文件」。
    于是 .xlsx 在画布里 sourcePath 为空 → 分享入口置灰成「需先保存」，可它就在
    用户自己的 docs 目录里。能否写回由 canSaveWorkspaceFile 单独判定。
    """
    source = _read("frontend/src/utils/workspaceFilePreview.ts")
    fn = source.split("export function shouldAttachWorkspaceSourcePath")[1].split("\n}")[0]
    assert "canWriteWorkspaceFile" not in fn, "判据不能再耦合文本可写性"
    assert "/agent_workspaces/" in fn


def test_text_write_back_is_judged_separately():
    """写回能力仍由 canSaveWorkspaceFile 把关，且它自己会再查一次文本白名单。"""
    source = _read(CANVAS)
    block = source.split("const canSaveWorkspaceFile = computed")[1].split("});")[0]
    assert "canWriteWorkspaceFile(effectiveSourcePath.value)" in block


def test_every_canvas_preview_branch_carries_source_path():
    """工作区文件的每条预览分支都必须带 sourcePath。

    这是同一个问题的第二次修正：第一次只改了 shouldAttachWorkspaceSourcePath 的判据，
    但 useWorkspaceCanvas 里 Office/PDF、图片、CSV、文本是四条各自构造 canvasData 的分支，
    其中只有文本那条注入了 sourcePath。于是 xlsx 打开后仍然显示「需先保存，无法分享」。
    注入必须发生在分支汇总处（showCanvas 之前）并做成兜底补，不能每条分支各写一遍。
    """
    source = _read("frontend/src/composables/chat/useWorkspaceCanvas.ts")
    block = source[source.index('canvas://file') :]
    inject_at = block.index("shouldAttachWorkspaceSourcePath")
    show_at = block.index("showCanvas()")
    assert inject_at < show_at, "注入必须发生在统一的 showCanvas 之前"
    # 兜底补：已经带了就不再覆盖，缺失的才补
    assert "!canvasData.value.sourcePath" in block


def test_share_size_is_not_computed_from_url_content():
    """Office/PDF/图片的 content 是地址，按它算体积会得到假数字。"""
    source = _read(CANVAS)
    fn = source.split("const openShare = () => {")[1].split("};")[0]
    assert "contentIsUrl" in fn
    assert "contentIsUrl ? 0 : new Blob([raw]).size" in fn


def test_drawer_preview_carries_source_path_for_every_type():
    """工作区抽屉打开文件的路径（openWorkspaceFileInCanvas）必须对所有类型都带 sourcePath。

    这是同一个问题的第三次修正，也是最关键的一次：用户从右侧「浏览工作空间」抽屉
    双击 xlsx 走的是这条路径，而不是上面 useWorkspaceCanvas 的 `canvas://file` 分支。
    该函数内部同样有 Office / PDF / 图片·CSV / 文本四条分支，此前只有文本那条传了
    sourcePath。只改其中一条路径，用户看到的仍然是「需先保存，无法分享」。
    """
    source = _read("frontend/src/utils/workspaceFilePreview.ts")
    fn = source[
        source.index("export async function openWorkspaceFileInCanvas") :
        source.index("export async function downloadWorkspaceFile")
    ]
    # 整个函数只允许有一个 onOpen 调用点，即统一出口 emitOpen 内部
    assert fn.count("onOpen({") == 1, "分支不得各自调 onOpen，必须走 emitOpen"
    assert fn.count("emitOpen({") == 4, "四条预览分支都要经过统一出口"
    assert "data.sourcePath ?? " in fn, "已带路径时不覆盖，缺失时兜底"


def test_reader_controls_live_in_the_tab_bar_not_the_header():
    """字号 / 行宽控件必须在页签栏，不能回到顶栏标题行。

    顶栏要挤下文件名、类型徽标、已保存徽标和 ⋯/钉住/全屏/关闭四个按钮，再放四个
    阅读控件会把标题压成省略号。页签栏左右本来是空的，控件在语义上也属于「怎么读」，
    而不是「这个文件是什么」。
    """
    source = _read(CANVAS)

    header_start = source.index('<div class="flex items-center space-x-1.5 shrink-0">')
    header_end = source.index("</div>\n      </div>", header_start)
    header = source[header_start:header_end]
    for marker in ("zoomReader", "readerScalePercent", "toggleReaderNarrow"):
        assert marker not in header, f"{marker} 不该出现在顶栏"

    tab_bar_at = source.index('class="relative px-4 py-2 border-b')
    assert source.index("zoomReader(-0.1)") > tab_bar_at, "控件应位于页签栏"
    # 绝对定位：否则会把中间那组页签推离居中位置
    assert "absolute right-3 flex items-center" in source[tab_bar_at : tab_bar_at + 2400]
