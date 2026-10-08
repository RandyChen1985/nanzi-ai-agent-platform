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
 * Office 里可交给 DocumentViewer 预览的格式（排除旧版二进制 .ppt）。
 *
 * 画布的类型分派用它：画布认为「文档预览」只应该是 Office。
 * 文本格式（.md/.ts 等）在画布里必须留在 'code'，否则会丢掉画布的
 * markdown 渲染、编辑/预览切换以及 mermaid/ECharts 集成。
 */
export const OFFICE_PREVIEW_EXTENSIONS = new Set([
  '.docx', '.doc', '.xlsx', '.xls', '.xlsm', '.pptx',
])

/**
 * 交给 DocumentViewer 渲染的格式 = Office 可预览格式 + file-viewer 原生支持的文本。
 *
 * 不含旧版二进制 `.ppt`：它需要 @file-viewer/renderer-ppt，而该链会引入
 * 带内置水印、许可为 SEE LICENSE IN LICENSE 的 @file-viewer/ppt。
 *
 * 文本类由 @file-viewer/renderer-text 原生支持（markdown 2 种 + code 48 种），
 * 共 50 种。用它而不是自己取文本渲染，除了格式覆盖更全、代码文件有语法高亮，
 * 也顺带避开了「后端无法为 .md/.yaml 等格式给出可内联 Content-Type、
 * 导致浏览器直接下载」的问题——组件靠扩展名选择渲染链路，不依赖 Content-Type。
 *
 * ⚠️ 本集合是 RAG 引用抽屉 / 知识库预览弹窗的语义（「能否交给 DocumentViewer」），
 * 不等于画布语义。画布分派必须用 OFFICE_PREVIEW_EXTENSIONS。
 */
export const DOCUMENT_VIEWER_EXTENSIONS = new Set([
  // Office
  ...OFFICE_PREVIEW_EXTENSIONS,
  // Markdown（renderer-text: markdown）
  '.md', '.markdown',
  // 纯文本与代码（renderer-text: code）
  '.txt', '.json', '.jsonc', '.json5', '.log',
  '.yaml', '.yml', '.ini', '.toml',
  '.js', '.mjs', '.cjs', '.jsx', '.ts', '.tsx', '.vue', '.react',
  '.css', '.html', '.htm', '.xml', '.http', '.gv',
  '.py', '.java', '.go', '.rs', '.rb', '.php', '.swift', '.kt', '.cs',
  '.c', '.cpp', '.cc', '.h', '.hpp',
  '.sh', '.bash', '.sql', '.tex', '.proto', '.hcl', '.ipynb',
  '.diff', '.patch', '.bundle', '.bdl',
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
 * 处于 OFFICE_EXTENSIONS 但不在 OFFICE_PREVIEW_EXTENSIONS 的格式：目前只有旧版二进制 `.ppt`。
 * 它能被识别为 Office 文件，但因需要带内置水印、许可为 SEE LICENSE IN LICENSE 的
 * @file-viewer/ppt，刻意不交给 DocumentViewer —— 这些格式保持「点击即下载」。
 */
export function shouldDownloadInsteadOfPreview(name: string): boolean {
  const ext = getWorkspaceFileExtension(name)
  return OFFICE_EXTENSIONS.has(ext) && !OFFICE_PREVIEW_EXTENSIONS.has(ext)
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
