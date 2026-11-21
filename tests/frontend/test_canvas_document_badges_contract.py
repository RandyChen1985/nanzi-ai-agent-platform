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


def test_canvas_header_puts_badges_below_the_title():
    """标题必须独占一行，类型徽标与「已保存」落到第二行。

    用户反馈：打开的图叫 `generated-image-20261120-013808-1-d0c835.png`，头部
    三者挤同一行时标题被两个徽标顶掉，只剩 `generated-image-20261120-013808-1-d0c8…`——
    而标题恰恰是「我打开的是哪个文件」的唯一线索，徽标只是补充信息。

    改之前标题还被迫带 `max-w-[220px]`：那个硬截断原本就是为了给徽标腾地方，
    标题独占一行后它就不该存在了（留着仍然会截断长文件名）。
    """
    canvas = _source("frontend/src/components/embed/ChatCanvas.vue")

    header_at = canvas.index("<!-- Header -->")
    header = canvas[header_at : header_at + 3600]

    # 左列改成竖排两行
    assert 'class="flex min-w-0 flex-col gap-1"' in header
    # 标题不再被 max-w 硬截断
    assert "max-w-[220px]" not in header
    # 徽标行是标题之后的兄弟节点，且同时装着类型与已保存
    badges_at = header.index('class="flex min-w-0 items-center gap-1.5"')
    assert badges_at > header.index("truncate text-sm font-bold")
    badges = header[badges_at:]
    assert "canvasTypeMeta.label" in badges
    assert "已保存" in badges[:1400]
