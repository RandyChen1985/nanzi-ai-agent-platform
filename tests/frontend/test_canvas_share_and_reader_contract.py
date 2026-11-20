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
