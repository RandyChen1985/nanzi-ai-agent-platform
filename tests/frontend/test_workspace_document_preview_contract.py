from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_document_viewer_extensions_exclude_legacy_ppt():
    # 集合字面量已抽到零依赖模块 documentPreviewFormats.ts（workspaceFilePreview.ts 仅 re-export）
    source = _source("frontend/src/utils/documentPreviewFormats.ts")

    assert "DOCUMENT_VIEWER_EXTENSIONS" in source
    # 6 种走预览的格式
    for ext in (".docx", ".doc", ".xlsx", ".xls", ".xlsm", ".pptx"):
        assert f"'{ext}'" in source, ext
    # 旧版二进制 .ppt 必须被排除在预览之外（需水印包，未装配）
    assert "DOCUMENT_VIEWER_EXTENSIONS.has" in source
    assert source.index("DOCUMENT_VIEWER_EXTENSIONS") < source.index("OFFICE_EXTENSIONS.has(ext)")
    # 精确限定在 DOCUMENT_VIEWER_EXTENSIONS 的 Set 字面量内检查。
    # 仅断言 "'.ppt'" 出现在整个文件里是不够的——它本来就在 OFFICE_EXTENSIONS 中，
    # 因此把 .ppt 加回预览集合时那种写法会静默通过（变异验证已实测逃逸）。
    # 用引号界定：'.ppt' 不会命中 '.pptx'。
    preview_block = source.split("export const DOCUMENT_VIEWER_EXTENSIONS = new Set([")[1].split("])")[0]
    assert "'.ppt'" not in preview_block


def test_office_and_pdf_no_longer_prefetch_blob_in_dispatch_layer():
    preview = _source("frontend/src/utils/workspaceFilePreview.ts")

    # document 类型只传鉴权 URL，不再预取 Blob
    assert "type: 'document'" in preview
    assert "documentMeta" in preview
    assert "downloadPath" in preview
    # PDF 分支不再走 blob 预取
    assert "payload.type === 'image' || payload.type === 'csv'" in preview


def test_workspace_canvas_payload_reuses_shared_types():
    composable = _source("frontend/src/composables/chat/useWorkspaceCanvas.ts")

    assert "from '@/types/canvas'" in composable or 'from "@/types/canvas"' in composable
    # canvas://file 分支里重复的 Office 下载逻辑已移除
    assert "link.download = filename" not in composable
