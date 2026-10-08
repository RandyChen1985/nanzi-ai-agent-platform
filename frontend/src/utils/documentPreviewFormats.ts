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
 * 扩展名 → highlight.js 语言 id（供画布代码视图做语法高亮）。
 *
 * 必须**按扩展名**判定，不能用文件名字串推断：`config.json` 含子串 `js`（`.js`on）、
 * `assets.txt` 含 `ts`（asse`ts`）、`crash.log` 含 `sh`，而 `main.py` 又不含
 * "python" 三字——按子串推断会让这一批真实文件名全部高亮错误，其中 `json` 分支还会
 * 因 `js` 先命中而成为**不可达代码**。
 *
 * 所有取值都经 `hljs.getLanguage()` 校验（见 tests/frontend/highlight_language.mjs）：
 * 未注册的 id 会让高亮静默退化成纯文本转义。
 */
const HIGHLIGHT_LANGUAGE_BY_EXTENSION: Record<string, string> = {
  // 数据与配置
  '.json': 'json',
  '.jsonc': 'json',
  '.json5': 'json',
  '.ipynb': 'json',
  '.yaml': 'yaml',
  '.yml': 'yaml',
  '.toml': 'ini',
  '.ini': 'ini',
  '.conf': 'ini',
  // 脚本与语言
  '.py': 'python',
  '.js': 'javascript',
  '.mjs': 'javascript',
  '.cjs': 'javascript',
  '.jsx': 'javascript',
  '.ts': 'typescript',
  '.tsx': 'typescript',
  '.vue': 'xml',
  '.sh': 'bash',
  '.bash': 'bash',
  '.sql': 'sql',
  '.java': 'java',
  '.go': 'go',
  '.rs': 'rust',
  '.rb': 'ruby',
  '.php': 'php',
  '.c': 'c',
  '.h': 'c',
  '.cpp': 'cpp',
  '.cc': 'cpp',
  '.hpp': 'cpp',
  '.cs': 'csharp',
  '.swift': 'swift',
  '.kt': 'kotlin',
  // 文本与标记
  '.md': 'markdown',
  '.markdown': 'markdown',
  '.txt': 'plaintext',
  '.log': 'plaintext',
  '.html': 'xml',
  '.htm': 'xml',
  '.xml': 'xml',
  '.css': 'css',
  '.diff': 'diff',
  '.patch': 'diff',
}

/**
 * 无扩展名（AI 生成内容的标题常是描述而非文件名）或扩展名未知时的兜底线索。
 *
 * 刻意只匹配**完整单词**，不使用 `js` / `ts` / `sh` 这类两字母裸子串——那正是
 * 本次缺陷的根因。同理不匹配 `go`：`Django` 之类的词也含 `go`。
 */
function inferHighlightLanguageFromTitle(title: string): string {
  const text = String(title || '').toLowerCase()
  if (text.includes('python')) return 'python'
  if (text.includes('typescript')) return 'typescript'
  if (text.includes('javascript')) return 'javascript'
  if (text.includes('markdown')) return 'markdown'
  if (text.includes('json')) return 'json'
  if (text.includes('yaml')) return 'yaml'
  if (text.includes('sql')) return 'sql'
  if (text.includes('bash') || text.includes('shell')) return 'bash'
  if (text.includes('html') || text.includes('xml')) return 'xml'
  if (text.includes('css')) return 'css'
  if (text.includes('java')) return 'java'
  if (text.includes('rust')) return 'rust'
  if (text.includes('ruby')) return 'ruby'
  if (text.includes('kotlin')) return 'kotlin'
  if (text.includes('swift')) return 'swift'
  if (text.includes('php')) return 'php'
  return 'plaintext'
}

/**
 * 画布代码视图该用哪种 highlight.js 语言：扩展名优先，文本线索兜底。
 *
 * 放在本模块（而非 ChatCanvas 内）是为了能被 `node --experimental-strip-types` 直接
 * 加载做行为断言——子串推断这类缺陷靠源码字符串断言是发现不了的。
 */
export function resolveHighlightLanguage(name: string): string {
  const ext = getWorkspaceFileExtension(name)
  const mapped = ext ? HIGHLIGHT_LANGUAGE_BY_EXTENSION[ext] : undefined
  if (mapped) return mapped
  return inferHighlightLanguageFromTitle(name)
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

/**
 * 画布必须自己渲染、不可交给 file-viewer 的文本格式。三类，各有硬原因：
 *  - `.md` / `.markdown`：画布的 markdown 渲染器带 mermaid 与 ECharts；
 *    file-viewer 的 markdown 依赖只有 marked + dompurify，没有 ECharts 能力。
 *  - `.html` / `.htm`：画布用 sandbox iframe 渲染 HTML 片段；
 *    file-viewer 把 `.html` 交给 code 渲染器，只显示源码。
 *  - `.py` / `.sh` / `.bash`：画布支持脚本执行与实时输出，file-viewer 只读。
 */
export const CANVAS_NATIVE_TEXT_EXTENSIONS = new Set([
  '.md', '.markdown',
  '.html', '.htm',
  '.py', '.sh', '.bash',
])

/**
 * 画布 / 工作空间里，该文本格式是否默认交给 DocumentViewer 预览。
 *
 * 这是**静态**规则：只看扩展名，不看文件来源或可写性，保证同一文件每次打开行为一致。
 * 返回 true 的格式在画布中仍可通过「源代码」标签页切回 code 视图编辑。
 *
 * 注意边界：
 *  - Office 与 PDF 不走本函数（它们已由 `isDocumentPreview` 分派给 DocumentViewer）；
 *  - `.csv` 走画布 PivotTable，不在 DOCUMENT_VIEWER_EXTENSIONS 里，天然为 false；
 *  - `.ppt` 由 `shouldDownloadInsteadOfPreview` 拦成下载。
 */
export function shouldPreviewWithFileViewerInCanvas(name: string): boolean {
  const ext = getWorkspaceFileExtension(name)
  if (!ext) return false
  if (CANVAS_NATIVE_TEXT_EXTENSIONS.has(ext)) return false
  if (OFFICE_EXTENSIONS.has(ext)) return false
  return DOCUMENT_VIEWER_EXTENSIONS.has(ext)
}
