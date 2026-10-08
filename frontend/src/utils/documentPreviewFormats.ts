/**
 * 文档预览格式判定 —— 刻意保持零运行时依赖（无 axios、无别名导入）。
 *
 * 这样本模块能被 `node --experimental-strip-types` 直接加载，
 * 从而对格式判定做真实行为断言（见 tests/frontend/document_preview_formats.mjs）。
 * 字符串子串断言抓不到「.pptx 被 .ppt 规则误伤」这类问题，行为测试可以。
 */

export const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.webp', '.gif'])

export const TEXT_EXTENSIONS = new Set([
  '.txt', '.md', '.csv', '.json', '.sql', '.py', '.js', '.ts',
  '.sh', '.xml', '.html', '.css', '.yaml', '.yml', '.ini', '.conf',
  '.log', '.env', '.htm',
])

export const OFFICE_EXTENSIONS = new Set([
  '.docx', '.doc', '.xlsx', '.xls', '.xlsm', '.pptx', '.ppt',
])

/**
 * 交给 DocumentViewer 渲染的格式。
 * 刻意不含旧版二进制 `.ppt`：它需要 @file-viewer/renderer-ppt，
 * 而该链会引入带内置水印、许可为 SEE LICENSE IN LICENSE 的 @file-viewer/ppt。
 */
export const DOCUMENT_VIEWER_EXTENSIONS = new Set([
  '.docx', '.doc', '.xlsx', '.xls', '.xlsm', '.pptx',
])

/** 交给 DocumentViewer 渲染时使用的 MIME，用于组件内部选择 renderer */
const DOCUMENT_VIEWER_MIME: Record<string, string> = {
  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  '.doc': 'application/msword',
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.xls': 'application/vnd.ms-excel',
  '.xlsm': 'application/vnd.ms-excel.sheet.macroEnabled.12',
  '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
}

export function getWorkspaceFileExtension(name: string): string {
  const parts = String(name || '').split('.')
  if (parts.length < 2) return ''
  return `.${parts.pop()!.toLowerCase()}`
}

export function resolveDocumentViewerMime(name: string): string | undefined {
  const ext = getWorkspaceFileExtension(name)
  return DOCUMENT_VIEWER_MIME[ext]
}

/**
 * 处于 OFFICE_EXTENSIONS 但不在 DOCUMENT_VIEWER_EXTENSIONS 的格式：目前只有旧版二进制 `.ppt`。
 * 它能被识别为 Office 文件，但因需要带内置水印、许可为 SEE LICENSE IN LICENSE 的
 * @file-viewer/ppt，刻意不交给 DocumentViewer —— 这些格式保持「点击即下载」。
 */
export function shouldDownloadInsteadOfPreview(name: string): boolean {
  const ext = getWorkspaceFileExtension(name)
  return OFFICE_EXTENSIONS.has(ext) && !DOCUMENT_VIEWER_EXTENSIONS.has(ext)
}

/**
 * 该文件能否交给 DocumentViewer 预览。
 *
 * 画布、RAG 引用抽屉、知识库预览弹窗统一调用本函数，
 * 避免同一判定出现多份实现后彼此漂移（画布那版曾因两份分派实现漂移，连出三个缺陷）。
 */
export function canPreviewWithDocumentViewer(name: string): boolean {
  return DOCUMENT_VIEWER_EXTENSIONS.has(getWorkspaceFileExtension(name))
}
