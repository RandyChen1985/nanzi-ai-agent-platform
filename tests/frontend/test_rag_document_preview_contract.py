import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 行为验证：静态断言看不出「.pptx 会不会被 .ppt 规则误伤」，这里用 Node 真跑纯函数
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能执行纯函数行为测试")
def test_document_preview_formats_behaviour_via_node():
    """覆盖静态断言覆盖不到的核心风险：扩展名判定（含 .ppt/.pptx 子串陷阱）。"""
    script = Path("tests/frontend/document_preview_formats.mjs")
    assert script.exists(), "行为测试脚本缺失"
    proc = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(script)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "文档预览格式判定测试全部通过" in proc.stdout


def test_shared_helper_is_exported_and_dependency_free():
    source = _source("frontend/src/utils/documentPreviewFormats.ts")

    assert "export function canPreviewWithDocumentViewer(" in source
    assert "DOCUMENT_VIEWER_EXTENSIONS.has(" in source
    # 零运行时依赖：不得引入 axios 或 @/ 别名导入（否则 Node 无法直接加载）
    assert "from '@/utils/axios'" not in source
    assert "from 'axios'" not in source


def test_workspace_utils_reexports_formats_module():
    source = _source("frontend/src/utils/workspaceFilePreview.ts")

    assert "export * from './documentPreviewFormats'" in source


def test_rag_drawer_uses_document_viewer_and_self_judges():
    source = _source("frontend/src/components/RagPreviewDrawer.vue")

    assert "DocumentViewer" in source
    assert "canPreviewWithDocumentViewer" in source
    # 下载提示分支仍在（承接 .ppt），否则 .ppt 会退化为空白
    assert "shouldDownloadInsteadOfPreview" in source
    # 关闭抽屉必须真正卸载组件，否则会在后台继续加载文件
    assert "canPreview && modelValue && fileUrl" in source
    # 副标题按格式区分
    assert "subtitle" in source
    # 不再依赖父组件传入判定
    assert "isOfficeDocument" not in source


def test_rag_drawer_keeps_iframe_for_pdf_and_others():
    source = _source("frontend/src/components/RagPreviewDrawer.vue")

    assert "<iframe" in source
    assert "#page=" in source


def test_knowledge_modal_uses_document_viewer_and_keeps_iframe():
    source = _source("frontend/src/views/KnowledgeBaseManagement.vue")

    assert "DocumentViewer" in source
    assert "canPreviewWithDocumentViewer" in source
    assert "canPreviewSelectedDocument && showDocPreview && previewDocUrl" in source
    # iframe 仍是 else 分支（承接 file-viewer 不支持的格式，如 .rst/.env/.ppt）
    assert "v-else-if=\"showDocPreview && previewDocUrl\"" in source
    # 知识库把 PDF 也纳入 DocumentViewer —— 与 RAG 抽屉有意不同，见下一个测试
    # 必须是 `.has(` 而不是裸标识符：只断言 "PDF_EXTENSIONS" in kb 时，把判定里的
    # PDF 并入删掉（标识符仍留在 import 里）也照样通过 —— 变异验证抓到过这个假绿。
    assert "PDF_EXTENSIONS.has(" in source
    # 该页面无暗色适配，主题固定 light
    assert 'theme="light"' in source


def test_duplicated_office_judgement_removed_from_hosts():
    for path in ("frontend/src/views/EmbedChat.vue", "frontend/src/views/AgentDebug.vue"):
        source = _source(path)
        assert "isOfficeDocument" not in source, path
        assert ":is-office-document" not in source


def test_pdf_enters_file_viewer_only_from_knowledge_modal():
    """PDF 只在知识库「预览原文件」交给 file-viewer；画布与 RAG 抽屉必须排除。

    原因：RAG 引用抽屉靠 `${fileUrl}#page=${pageNo}` 让浏览器原生 PDF 阅读器跳到引用页，
    而 file-viewer 的 pdf 渲染器不认这个 URL fragment（renderer-pdf 里只有缩略图的
    data-pdf-thumbnail-page）。所以 .pdf 必须留在独立集合 PDF_EXTENSIONS 里 ——
    一旦有人图省事把它直接加进 DOCUMENT_VIEWER_EXTENSIONS，RAG 抽屉会静默丢掉引用页
    跳转，且不会有任何报错。本测试就是防住这个动作。

    形状上比「扩展名行为」更强的部分：这里是**源码级**护栏，能在 .pdf 被加进共享集合的
    那一刻立刻变红；扩展名行为测试（document_preview_formats.mjs）则验证运行时真值。
    """
    formats = _source("frontend/src/utils/documentPreviewFormats.ts")
    kb = _source("frontend/src/views/KnowledgeBaseManagement.vue")
    rag = _source("frontend/src/components/RagPreviewDrawer.vue")

    # 独立的 PDF 集合必须存在，并写明「为什么不能并入共享集合」
    assert "export const PDF_EXTENSIONS" in formats
    assert "#page=" in formats, "PDF 集合的注释里应说明 #page= 这个排除原因"
    # 共享集合仍是 56 种、仍不含 .pdf（运行时由 .mjs 断言，这里钉住源码形状）
    assert ".pdf" not in formats.split("export const DOCUMENT_VIEWER_EXTENSIONS")[1].split("])")[0]
    # 知识库纳入 PDF —— 必须断言「真正参与判定」（`.has(`），不能只断言标识符出现：
    # 标识符会留在 import 语句里，导致「把 PDF 并入删掉」这种功能回退测不出来。
    # 这条是变异验证（M2）实打实抓到的假绿，勿再退回弱断言。
    assert "PDF_EXTENSIONS.has(" in kb
    # RAG 抽屉绝不引用 PDF 集合，且 iframe 的页码定位必须保留
    assert "PDF_EXTENSIONS" not in rag
    assert "#page=" in rag, path
