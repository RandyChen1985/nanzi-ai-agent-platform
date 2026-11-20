from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_canvas_badge_labels_document_type_instead_of_falling_back_to_code():
    """document 必须有自己的标签，不能落到 'Code' 兜底。

    徽标已从「8 套配色内联三元链」收敛为 canvasTypeMeta（中性色 + 图标），
    但「别把 Office 显示成 Code」这条意图不变，断言跟着挪到新位置。
    """
    canvas = _source("frontend/src/components/embed/ChatCanvas.vue")

    meta = canvas.split("const canvasTypeMeta")[1].split("});")[0]
    assert "type === 'document'" in meta
    assert "label: 'Office'" in meta
    # 兜底分支必须留在最后，且只有它返回 Code
    assert meta.count("label: 'Code'") == 1
    assert meta.rindex("label: 'Code'") > meta.index("type === 'document'")


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
