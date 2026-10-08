from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_canvas_badge_labels_document_type_instead_of_falling_back_to_code():
    canvas = _source("frontend/src/components/embed/ChatCanvas.vue")

    # 徽标文案：document 必须有自己的标签，不能落到 'Code' 兜底
    assert "data?.type === 'document' ? 'Office'" in canvas
    # 徽标配色：document 必须有自己的色，不能落到灰色兜底
    assert "data?.type === 'document' ? 'bg-teal-100" in canvas


def test_canvas_download_button_treats_document_as_file_download():
    canvas = _source("frontend/src/components/embed/ChatCanvas.vue")

    # Office 文档要显示「下载文件」，而不是「下载数据」
    assert (
        "data?.type === 'image' || data?.type === 'pdf' || data?.type === 'document'"
        in canvas
    )


def test_canvas_has_no_shadowed_pdf_iframe_branch():
    canvas = _source("frontend/src/components/embed/ChatCanvas.vue")

    # DocumentViewer 已接管 pdf，旧的 iframe 分支被前置条件遮蔽，属死代码
    assert "PDF Viewer Sandbox" not in canvas
