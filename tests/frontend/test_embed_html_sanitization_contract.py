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


def test_sanitizer_allows_business_custom_protocols():
    """消毒层必须放行 `quick:` 与 `canvas:`，否则快捷按钮直接失效。

    DOMPurify 除了校验标签/属性，还会按 `ALLOWED_URI_REGEXP` 过滤**属性值里的协议**，
    不匹配就把整个 `href` 删掉。markdown-it 那层早有 `validateLink` 放行 `quick:`，
    但消毒层没跟上，于是 `<a href="quick:...">` 渲染出来只剩 `<a class="quick-action-btn">`，
    而点击处理读的是 `href`（`href.startsWith('quick:')`）——读不到就整段跳过，表现就是
    「快捷按钮点了没反应」。`canvas://` 的预览链接同理。
    """
    assert "ALLOWED_URI_REGEXP" in SANITIZER
    lines = SANITIZER.splitlines()
    # 取赋值行（带冒号）之后的那个正则字面量，别被上面的注释行骗到
    idx = next(i for i, l in enumerate(lines) if "ALLOWED_URI_REGEXP:" in l)
    line = lines[idx + 1]
    assert "quick" in line, f"消毒层未放行 quick: 协议：{line.strip()}"
    assert "canvas" in line, f"消毒层未放行 canvas: 协议：{line.strip()}"
    # 放行自定义协议不等于放开 javascript:——默认白名单里本来就没有它，别顺手加
    assert "javascript" not in line


def test_markdown_and_sanitizer_agree_on_custom_protocols():
    """两层必须对同一批自定义协议达成一致，否则就会出现上面那种「渲染好了但点不动」。"""
    assert "quick:" in MARKDOWN  # markdown-it 的 validateLink 放行
    assert "quick" in SANITIZER  # 消毒层也必须放行


def test_style_tag_is_forbidden_but_attribute_is_kept():
    """`<style>` 标签必须禁，`style` 属性必须留——这两件事不能搞混。

    DOMPurify 的 html profile **包含** `<style>` 标签，模型输出
    `<style>body{display:none}</style>` 会原样保留并生效，足够把整页搞乱。
    但 `style` **属性**不能禁：KaTeX 的 `katex-html` 那份完全靠内联 style 定位，
    禁掉公式就散架了。所以只禁标签、留属性。
    """
    forbid_line = next(
        line for line in SANITIZER.splitlines() if "FORBID_TAGS:" in line
    )
    assert "'style'" in forbid_line, f"<style> 标签未被禁：{forbid_line.strip()}"

    forbid_attr_line = next(
        (line for line in SANITIZER.splitlines() if "FORBID_ATTR:" in line), ""
    )
    assert "'style'" not in forbid_attr_line, "style 属性被禁会让 KaTeX 公式散架"
    assert "'style'" in SANITIZER.split("export function", 1)[0], "style 必须留在允许列表"


def test_katex_mathml_wrapper_tags_are_added():
    """mathMl profile 不含 `semantics` / `annotation`，KaTeX 的 MathML 副本会残缺。

    KaTeX 输出 `<math><semantics><mrow>…</mrow><annotation encoding="application/x-tex">`
    ——缺这两个标签时 `mrow` 被提升、`<annotation>` 里的 LaTeX 源码变成裸文本，
    读屏软件可能把它念出来。
    """
    line = next((l for l in SANITIZER.splitlines() if "ADD_TAGS:" in l), "")
    assert "semantics" in line and "annotation" in line, f"未补 KaTeX 的 MathML 包装标签：{line!r}"
