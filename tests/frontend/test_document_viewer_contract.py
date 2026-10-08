from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_document_viewer_lazy_loads_and_feeds_blob_instead_of_url():
    source = _source("frontend/src/components/embed/DocumentViewer.vue")

    assert "defineAsyncComponent" in source
    assert "import('@file-viewer/vue3')" in source
    assert ":file=" in source
    assert "responseType: 'blob'" in source


def test_document_viewer_avoids_object_urls_and_aborts_on_unmount():
    source = _source("frontend/src/components/embed/DocumentViewer.vue")

    assert "URL.createObjectURL" not in source
    assert "AbortController" in source
    assert "onUnmounted" in source
    assert "abort()" in source


def test_document_viewer_exposes_download_fallback_and_theme_bridge():
    source = _source("frontend/src/components/embed/DocumentViewer.vue")

    assert "fallback-download" in source
    assert "styleIsolation" in source
    assert "locale: 'auto'" in source


def test_document_viewer_wraps_blob_in_named_file_for_renderer_dispatch():
    """渲染器靠文件扩展名选择预览链路：裸 Blob 没有 name，必须先包装成 File。

    依据 @file-viewer/vue3 类型定义中对 FileRef 的说明：
    「如果业务侧拿到的是 Blob 或 ArrayBuffer，请先包装成
    new File([...], 'demo.pdf')，这样渲染器才能通过扩展名选择正确的预览链路。」
    """
    source = _source("frontend/src/components/embed/DocumentViewer.vue")

    assert "new File(" in source
    assert "props.meta?.filename || props.filename" in source


def test_document_viewer_imports_host_container_stylesheet():
    """宿主容器的基础样式（width/height）由 file-viewer3.css 提供。

    组件内部样式走 Shadow DOM 自动注入（adoptedStyleSheets），
    但宿主元素自身的尺寸规则不在 Shadow DOM 内。
    """
    source = _source("frontend/src/components/embed/DocumentViewer.vue")

    assert "@file-viewer/vue3/dist/file-viewer3.css" in source
