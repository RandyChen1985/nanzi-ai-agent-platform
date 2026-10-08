// 用 Node 真跑纯函数：静态字符串断言看不出「.pptx 会不会被 .ppt 规则误伤」，
// 这里直接调用真实实现做行为验证。
import assert from 'node:assert/strict'

import {
  canPreviewWithDocumentViewer,
  shouldDownloadInsteadOfPreview,
  resolveDocumentViewerMime,
  getWorkspaceFileExtension,
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

console.log('文档预览格式判定测试全部通过')
