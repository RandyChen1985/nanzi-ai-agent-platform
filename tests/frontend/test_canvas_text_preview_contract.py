"""画布文本格式默认走 file-viewer 的契约。

用户诉求：画布里默认都应该是 file-viewer 预览，只有要编辑时才切 code。
落地为**静态**扩展名规则（见 documentPreviewFormats.shouldPreviewWithFileViewerInCanvas），
但三类格式必须在画布侧渲染：.md（mermaid + ECharts）、.html（sandbox 片段渲染）、
.py/.sh（脚本执行），否则是能力倒退。
"""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_canvas_text_preview_behaviour_via_node():
    script = Path("tests/frontend/canvas_text_preview.mjs")
    assert script.exists(), "行为测试脚本缺失"
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "画布文本预览判定测试全部通过" in proc.stdout


def test_helper_is_exported_and_dependency_free():
    source = _source("frontend/src/utils/documentPreviewFormats.ts")

    assert "export function shouldPreviewWithFileViewerInCanvas(" in source
    assert "export const CANVAS_NATIVE_TEXT_EXTENSIONS" in source
    # 零运行时依赖：该模块必须能继续被 node --experimental-strip-types 直接加载
    assert "from '@/utils/axios'" not in source
    assert "from 'axios'" not in source
    # 三类画布专属格式必须在排除集合里
    for ext in ("'.md'", "'.markdown'", "'.html'", "'.htm'", "'.py'", "'.sh'", "'.bash'"):
        assert ext in source, f"{ext} 应出现在 CANVAS_NATIVE_TEXT_EXTENSIONS 中"


def test_canvas_uses_shared_helper_and_never_document_viewer_set():
    canvas = _source("frontend/src/components/embed/ChatCanvas.vue")

    assert "shouldPreviewWithFileViewerInCanvas" in canvas, "画布未复用共享判定"
    # 历史事故：画布直接用 DOCUMENT_VIEWER_EXTENSIONS 会让 .md/.txt 显示 OFFICE 徽章
    assert "DOCUMENT_VIEWER_EXTENSIONS" not in canvas


def test_canvas_preview_branch_precedes_code_branch():
    """预览分支必须排在 type === 'code' 分支之前，否则永远命中不了。

    锚点必须用模板里的完整字面量 `<template v-else-if="data?.type === 'code'">`：
    裸的 `data?.type === 'code'` 在脚本区第 446 行（isRunnableCode 内）也会命中，
    用它会锚到错误位置，让正确实现也被判失败。
    """
    canvas = _source("frontend/src/components/embed/ChatCanvas.vue")

    preview_at = canvas.index("isFileViewerTextPreview && activeTab === 'preview'")
    code_at = canvas.index("""<template v-else-if="data?.type === 'code'">""")
    assert preview_at < code_at, "file-viewer 文本预览分支必须排在 code 分支之前"

    # 也必须排在 isHtmlContent 之前，避免内容嗅探把扩展名已知的文件改道
    html_at = canvas.index('<template v-else-if="isHtmlContent">')
    assert preview_at < html_at, "文本预览分支必须排在 isHtmlContent 分支之前"


def test_document_viewer_accepts_content_without_network():
    viewer = _source("frontend/src/components/embed/DocumentViewer.vue")

    assert "content?: string" in viewer, "DocumentViewer 应支持直接接收内容"
    assert "props.content !== undefined" in viewer, "空字符串也是合法内容，不得用真值判断"
    # 必须保留了 url 这条路径（RAG 抽屉 / 知识库弹窗仍在用）
    assert "props.url" in viewer


def test_download_icon_follows_file_viewer_rendering():
    canvas = _source("frontend/src/components/embed/ChatCanvas.vue")

    assert "isFileViewerRendered" in canvas
    assert 'v-if="!isFileViewerRendered"' in canvas
