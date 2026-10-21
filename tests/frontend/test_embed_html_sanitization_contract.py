"""模型输出/知识库原文进入 `v-html` 前必须过 DOMPurify，且不能把公式削坏。

## 背景

`markdown.ts` 的 markdown-it 开了 `html: true`，原始 HTML 会被原样保留；渲染结果再走
`v-html` 插进 DOM，于是 `<img src=x onerror=...>` 一类注入能在终端用户浏览器里执行。
消毒已覆盖 `renderMarkdown` / `renderMarkdownPreview` 两条链路，但**引用弹层与 RAG
预览是另一条路**：它们直接渲染 `citation.content`（知识库切片原文），不经过
markdown 渲染，此前完全没有消毒。

## 这里守什么

1. 两个组件都必须真的调用 `sanitizeRenderedHtml`，而不是仅导入（导入未用最容易被
   后续重构悄悄变成裸 `v-html`）。
2. `USE_PROFILES` 必须同时开 `mathMl`。DOMPurify 的 `USE_PROFILES` 是**重置**白名单
   （源码：`ALLOWED_TAGS = addToSet({}, text)` 再按开关追加），只开 `html` 时 KaTeX 的
   `<math>` / `<mrow>` 会被当未知标签剥掉——视觉上公式靠 `katex-html` 撑着不坏，
   但无障碍朗读和「复制公式」会退化。
3. 脚本类向量必须有明确的关闭面：`FORBID_TAGS` 挡住内嵌容器，`FORBID_ATTR` 挡住
   `srcdoc` / `formaction`。
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SANITIZER = (ROOT / "frontend/src/utils/sanitizeHtml.ts").read_text(encoding="utf-8")
CITATION = (ROOT / "frontend/src/components/CitationPopover.vue").read_text(encoding="utf-8")
RAG = (ROOT / "frontend/src/components/RagPreviewDrawer.vue").read_text(encoding="utf-8")
MARKDOWN = (ROOT / "frontend/src/utils/markdown.ts").read_text(encoding="utf-8")

pytestmark = pytest.mark.no_infrastructure


def test_markdown_pipeline_is_sanitized():
    """两条 markdown 出口都必须包一层消毒，否则模型输出可注入脚本。"""
    assert MARKDOWN.count("sanitizeRenderedHtml(") == 2
    assert "md.render(normalizePipeTables" in MARKDOWN
    assert "mdPreview.render(normalizePipeTables" in MARKDOWN


def test_sanitizer_enables_mathml_profile():
    """只开 html profile 会把 KaTeX 的 MathML 副本剥掉。"""
    assert "USE_PROFILES: { html: true, mathMl: true }" in SANITIZER


def test_sanitizer_forbids_script_vectors():
    assert "'iframe'" in SANITIZER and "'object'" in SANITIZER and "'embed'" in SANITIZER
    assert "'srcdoc'" in SANITIZER and "'formaction'" in SANITIZER


def test_citation_popover_sanitizes_before_v_html():
    """引用切片是知识库原文，不能裸进 v-html。"""
    assert "sanitizeRenderedHtml" in CITATION
    assert 'v-html="safeCitationContent"' in CITATION
    assert 'v-html="citation.content"' not in CITATION


def test_rag_preview_sanitizes_before_v_html():
    assert "sanitizeRenderedHtml" in RAG
    assert 'v-html="safeContent"' in RAG
    assert 'v-html="content"' not in RAG


def test_no_untrusted_raw_v_html_left_in_the_two_external_sources():
    """回归哨兵：这两处以外的不可信原文入口若新增，应同步接入消毒。"""
    for source, name in ((CITATION, "CitationPopover"), (RAG, "RagPreviewDrawer")):
        for line in source.splitlines():
            if "v-html=" in line:
                assert "safe" in line, f"{name} 存在未经消毒的 v-html：{line.strip()}"
