from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_canvas_types_are_defined_in_single_source():
    source = _source("frontend/src/types/canvas.ts")

    assert "export type WorkspaceCanvasType" in source
    assert "'document'" in source
    assert "export interface CanvasDocumentMeta" in source
    assert "export interface CanvasPanelData" in source
    assert "sourcePath?: string" in source
    assert "documentMeta?: CanvasDocumentMeta" in source
    assert "downloadPath?: string" in source


def test_canvas_consumers_import_types_instead_of_redeclaring():
    consumers = {
        "ChatCanvas.vue": "frontend/src/components/embed/ChatCanvas.vue",
        "useWorkspaceCanvas.ts": "frontend/src/composables/chat/useWorkspaceCanvas.ts",
        "workspaceFilePreview.ts": "frontend/src/utils/workspaceFilePreview.ts",
        "MessageRenderer.vue": "frontend/src/components/MessageRenderer.vue",
    }

    for label, path in consumers.items():
        source = _source(path)
        assert (
            "from '@/types/canvas'" in source or 'from "@/types/canvas"' in source
        ), label


def test_office_preview_set_is_the_single_source_for_document_viewer_formats():
    """`DOCUMENT_VIEWER_EXTENSIONS`（56 种）必须由 6 种 Office 派生，不得内联重复。"""
    formats = _source("frontend/src/utils/documentPreviewFormats.ts")

    assert "export const OFFICE_PREVIEW_EXTENSIONS = new Set([" in formats
    viewer_block = formats.split(
        "export const DOCUMENT_VIEWER_EXTENSIONS = new Set(["
    )[1].split("])")[0]
    assert "...OFFICE_PREVIEW_EXTENSIONS," in viewer_block


def test_canvas_document_dispatch_uses_office_only_preview_set():
    """画布语义 = 只有 Office 走 'document'，文本格式必须留在 'code'。

    回归背景：`DOCUMENT_VIEWER_EXTENSIONS` 扩到 56 种（RAG/知识库语义）后，
    画布分派复用了同一常量，把 .md/.ts/.vue 一并判成 'document'，于是画布
    丢失 markdown 渲染、编辑/预览切换、mermaid/ECharts 集成与代码编辑，
    徽章也从 Markdown 掉成 Office。画布必须用只含 Office 的常量。
    """
    preview = _source("frontend/src/utils/workspaceFilePreview.ts")
    dispatch = preview.split("export function resolveWorkspaceCanvasType(")[1].split("\n}")[0]

    assert "OFFICE_PREVIEW_EXTENSIONS.has(getWorkspaceFileExtension(name))" in dispatch
    assert "return 'document'" in dispatch
    # 不得再用 56 种（RAG/知识库语义）的集合做画布类型分派
    assert "DOCUMENT_VIEWER_EXTENSIONS" not in dispatch

    composable = _source("frontend/src/composables/chat/useWorkspaceCanvas.ts")
    assert "DOCUMENT_VIEWER_EXTENSIONS" not in composable
    assert "OFFICE_PREVIEW_EXTENSIONS.has(getWorkspaceFileExtension(filename))" in composable
    # .ppt 仍必须落到共享下载分支（该分支不得改动）
    assert "shouldDownloadInsteadOfPreview(filename)" in composable


def test_chat_canvas_hides_duplicate_download_button_for_document_viewer():
    """Office/PDF 由 file-viewer 渲染，其工具栏自带下载，画布下载按钮须隐藏以免重复。

    用 `!isDocumentPreview` 而不是内联的 `data?.type !== 'document' && data?.type !== 'pdf'`：
    后者会让 TS 对本按钮子节点收窄 `data.type`，使按钮内「下载文件/下载数据」
    那行的 pdf/document 比较变成不可达分支，新增 2 条 TS2367
    （vue-tsc 62 → 64 条且落在本文件）。语义等价：isDocumentPreview = document || pdf。
    """
    canvas = _source("frontend/src/components/embed/ChatCanvas.vue")

    # isDocumentPreview 的定义保持 document || pdf
    assert "() => props.data?.type === 'document' || props.data?.type === 'pdf'" in canvas
    assert "v-if=\"!isDocumentPreview\"" in canvas
    # 该 v-if 必须恰好挂在 header 中 @click="downloadFile" 的按钮上（.ppt 仍走此按钮）
    download_button = canvas.split("v-if=\"!isDocumentPreview\"")[1].split("</button>")[0]
    assert '@click="downloadFile"' in download_button
    # 「复制链接」保留：file-viewer 没有对应功能
    assert "isDocumentPreview ? '复制链接' : '复制代码'" in canvas


def test_chat_canvas_action_footer_is_removed_but_save_dialog_stays():
    """底部操作栏整块删除，纵向空间还给内容区；保存目录对话框必须保留。

    底栏（`<!-- Action Footer -->`）原占一整行，三个横向按钮已改为 header 图标按钮，
    因此底栏标记、其容器 class 与横向按钮样式都不得残留。
    """
    canvas = _source("frontend/src/components/embed/ChatCanvas.vue")

    assert "<!-- Action Footer -->" not in canvas
    assert (
        "p-3 border-t border-gray-100 dark:border-gray-700 bg-white dark:bg-gray-800/80"
        not in canvas
    )
    # 横向整行按钮样式不得残留（三个操作已改为 header 图标按钮）
    assert "flex-1 py-2 text-xs font-bold" not in canvas
    # 防误删：保存目录对话框是独立组件，不随底栏一起删
    assert "<WorkspaceDirectorySaveDialog" in canvas


def test_chat_canvas_actions_render_as_header_icon_buttons():
    """保存/复制/下载改为 header 图标按钮：p-1.5 图标按钮规范 + svg + title/aria-label。"""
    canvas = _source("frontend/src/components/embed/ChatCanvas.vue")

    # 原横向按钮文字现在落在 tooltip（:title）上
    for tooltip in ("保存到目录", "复制链接", "复制代码", "下载文件", "下载数据"):
        assert tooltip in canvas, tooltip
    assert "saving ? '保存中...' : '保存到目录'" in canvas
    assert (
        "copied ? '已复制！' : (data?.type === 'image' || isDocumentPreview ? '复制链接' : '复制代码')"
        in canvas
    )

    anchors = {
        "保存到目录": '@click="requestSaveToDirectory"',
        "复制": '@click="copyContent"',
        "下载": '@click="downloadFile"',
    }
    for label, anchor in anchors.items():
        assert canvas.count(anchor) == 1, label
        block = canvas.split(anchor)[1].split("</button>")[0]
        assert "p-1.5" in block, label
        assert "rounded-lg transition-colors" in block, label
        assert "<svg" in block, label
        assert ":title=" in block, label
        assert ":aria-label=" in block, label
        assert "flex-1" not in block, label

    # Office/PDF 仍隐藏下载图标（file-viewer 工具栏自带下载）：判据 = isDocumentPreview
    download_button = canvas.split('v-if="!isDocumentPreview"')[1].split("</button>")[0]
    assert '@click="downloadFile"' in download_button


def test_chat_canvas_header_actions_precede_pin_button():
    """三个操作按钮必须插在钉住按钮之前（header 右侧、Pin 前）。

    锚点唯一性：`@click="requestSaveToDirectory"` / `@click="copyContent"` /
    `@click="downloadFile"` / `@click="pinned = !pinned"` 在源码中各出现 1 次；
    而「取消钉住」出现 2 次（:title 与 :aria-label），故文案锚点改用唯一的
    「钉住画布（固定在侧边不遮挡对话）」。
    """
    canvas = _source("frontend/src/components/embed/ChatCanvas.vue")

    save_at = canvas.index('@click="requestSaveToDirectory"')
    copy_at = canvas.index('@click="copyContent"')
    download_at = canvas.index('@click="downloadFile"')
    pin_at = canvas.index('@click="pinned = !pinned"')

    assert save_at < copy_at < download_at < pin_at
    assert canvas.index("保存到目录") < canvas.index("钉住画布（固定在侧边不遮挡对话）")
    # 钉住按钮本身保持原样
    assert ":title=\"pinned ? '取消钉住' : '钉住画布（固定在侧边不遮挡对话）'\"" in canvas
