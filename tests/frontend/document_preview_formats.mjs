// 用 Node 真跑纯函数：静态字符串断言看不出「.pptx 会不会被 .ppt 规则误伤」，
// 这里直接调用真实实现做行为验证。
import assert from 'node:assert/strict'

import {
  canPreviewWithDocumentViewer,
  shouldDownloadInsteadOfPreview,
  resolveDocumentViewerMime,
  getWorkspaceFileExtension,
  DOCUMENT_VIEWER_EXTENSIONS,
  OFFICE_PREVIEW_EXTENSIONS,
  PDF_EXTENSIONS,
} from '../../frontend/src/utils/documentPreviewFormats.ts'

// --- 6 种组件官方支持的格式，必须判为可预览 ---
for (const name of ['a.docx', 'a.doc', 'a.xlsx', 'a.xls', 'a.xlsm', 'a.pptx']) {
  assert.equal(canPreviewWithDocumentViewer(name), true, `${name} 应可预览`)
}

// --- 旧版二进制 .ppt 必须不可预览（需带水印、许可受限的 @file-viewer/ppt）---
assert.equal(canPreviewWithDocumentViewer('a.ppt'), false, '.ppt 不应可预览')

// --- 子串陷阱：.pptx / .pptm 含 ".ppt" 前缀，绝不能被 .ppt 规则误伤 ---
assert.equal(canPreviewWithDocumentViewer('模型各平台对比.pptx'), true, '.pptx 不能被 .ppt 规则误伤')
assert.equal(canPreviewWithDocumentViewer('deck.PPTX'), true, '大写 .PPTX 应可预览')
assert.equal(canPreviewWithDocumentViewer('deck.PPT'), false, '大写 .PPT 不应可预览')

// --- 带路径与多点的文件名 ---
assert.equal(canPreviewWithDocumentViewer('/tmp/a.b.docx'), true, '带路径与多点应正确取扩展名')
assert.equal(canPreviewWithDocumentViewer('无扩展名'), false, '无扩展名不应可预览')
assert.equal(canPreviewWithDocumentViewer(''), false, '空串不应可预览')

// --- .ppt 走下载而不是预览 ---
assert.equal(shouldDownloadInsteadOfPreview('a.ppt'), true, '.ppt 应走下载')
assert.equal(shouldDownloadInsteadOfPreview('a.pptx'), false, '.pptx 不应走下载')
assert.equal(shouldDownloadInsteadOfPreview('a.txt'), false, '非 Office 不应命中下载规则')

// --- MIME 映射 ---
assert.equal(
  resolveDocumentViewerMime('a.docx'),
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  '.docx 的 MIME',
)
assert.equal(resolveDocumentViewerMime('a.ppt'), undefined, '.ppt 无 MIME 映射')
assert.equal(resolveDocumentViewerMime('a.txt'), undefined, '未知格式无 MIME 映射')

// --- 扩展名提取 ---
assert.equal(getWorkspaceFileExtension('a.tar.gz'), '.gz', '多点取最后一段')
assert.equal(getWorkspaceFileExtension('noext'), '', '无扩展名返回空串')

// --- 文本格式改由 @file-viewer/renderer-text 原生渲染（markdown + code 共 50 种）---
for (const name of ['a.md', 'a.markdown', 'a.txt', 'a.json', 'a.yaml', 'a.yml',
                    'a.ini', 'a.toml', 'a.log', 'a.vue', 'a.tsx', 'a.js', 'a.py',
                    'a.sh', 'a.sql', 'a.xml', 'a.html', 'a.go', 'a.rs', 'a.kt']) {
  assert.equal(canPreviewWithDocumentViewer(name), true, `${name} 应由 DocumentViewer 渲染`)
}
// .ppt 仍不可预览、仍走下载
assert.equal(canPreviewWithDocumentViewer('a.ppt'), false, '.ppt 仍不可预览')
assert.equal(shouldDownloadInsteadOfPreview('a.ppt'), true, '.ppt 仍走下载')
// 明确不支持的格式
assert.equal(canPreviewWithDocumentViewer('a.rst'), false, '.rst 官方不支持，仍走 iframe')
assert.equal(canPreviewWithDocumentViewer('a.env'), false, '.env 官方不支持，仍走 iframe')

// --- 画布语义：文本格式必须留在 'code'，只有 Office 走 'document' ---
assert.equal(OFFICE_PREVIEW_EXTENSIONS.size, 6, 'OFFICE_PREVIEW_EXTENSIONS 应为 6 种')
for (const name of ['a.docx', 'a.doc', 'a.xlsx', 'a.xls', 'a.xlsm', 'a.pptx']) {
  assert.equal(OFFICE_PREVIEW_EXTENSIONS.has(getWorkspaceFileExtension(name)), true, `${name} 应为 Office 预览`)
}
assert.equal(OFFICE_PREVIEW_EXTENSIONS.has('.ppt'), false, '.ppt 不应在 Office 预览集合')
for (const name of ['a.md', 'a.ts', 'a.vue', 'a.json', 'a.py']) {
  assert.equal(OFFICE_PREVIEW_EXTENSIONS.has(getWorkspaceFileExtension(name)), false, `${name} 不应判成画布文档预览`)
  // 但在 RAG/知识库语义里仍交给 DocumentViewer
  assert.equal(canPreviewWithDocumentViewer(name), true, `${name} 仍应交给 DocumentViewer`)
}
assert.equal(DOCUMENT_VIEWER_EXTENSIONS.size, 56, 'DOCUMENT_VIEWER_EXTENSIONS 应仍为 56 种')

// --- PDF 的路由三态：画布与 RAG 抽屉都不走 file-viewer，只有知识库走 ---
// 原因：RAG 引用抽屉靠 `${fileUrl}#page=${pageNo}` 让浏览器原生 PDF 阅读器跳到引用页，
// 而 file-viewer 的 pdf 渲染器不认这个 URL fragment（renderer-pdf 里只有缩略图的
// data-pdf-thumbnail-page）。所以 .pdf 必须留在独立集合里，绝不能混进共享集合。
assert.equal(
  canPreviewWithDocumentViewer('a.pdf'),
  false,
  '.pdf 在 RAG 语义下仍不走 DocumentViewer（需保 #page= 引用页跳转）',
)
assert.equal(
  DOCUMENT_VIEWER_EXTENSIONS.has('.pdf'),
  false,
  '.pdf 绝不能进 DOCUMENT_VIEWER_EXTENSIONS —— 会连带改掉 RAG 抽屉并丢掉引用页跳转',
)
assert.equal(PDF_EXTENSIONS.has('.pdf'), true, 'PDF_EXTENSIONS 应含 .pdf')
assert.equal(PDF_EXTENSIONS.size, 1, 'PDF_EXTENSIONS 目前只应有 .pdf')
assert.equal(
  resolveDocumentViewerMime('a.pdf'),
  'application/pdf',
  '.pdf 需显式 MIME，否则 DocumentViewer 只能靠响应头兜底',
)
assert.equal(resolveDocumentViewerMime('a.PDF'), 'application/pdf', '大写 .PDF 同样应解析出 MIME')

console.log('文档预览格式判定测试全部通过')
