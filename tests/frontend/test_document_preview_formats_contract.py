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
