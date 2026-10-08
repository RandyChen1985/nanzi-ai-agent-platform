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
    # iframe 仍是 else 分支，PDF 不受影响
    assert "v-else-if=\"showDocPreview && previewDocUrl\"" in source
    # 该页面无暗色适配，主题固定 light
    assert 'theme="light"' in source


def test_duplicated_office_judgement_removed_from_hosts():
    for path in ("frontend/src/views/EmbedChat.vue", "frontend/src/views/AgentDebug.vue"):
        source = _source(path)
        assert "isOfficeDocument" not in source, path
        assert ":is-office-document" not in source, path
