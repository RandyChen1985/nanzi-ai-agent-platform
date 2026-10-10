/**
 * AI 产物（`/api/v1/chat/generated-files/...`）→ 画布数据形态的**纯判定**。
 *
 * 刻意保持零运行时依赖（只 import 同样零依赖的 ./documentPreviewFormats.ts，
 * 且带显式 .ts 扩展名），这样本模块能被 `node --experimental-strip-types` 直接加载，
 * 对判定表做真实行为断言 —— 静态字符串断言抓不到「.pptx 被 .ppt 规则误伤」这类缺陷。
 * 取内容（axios）在 ./generatedFilePreview.ts，不要混进来。
 */
import {
  DOCUMENT_VIEWER_EXTENSIONS,
  IMAGE_EXTENSIONS,
  OFFICE_PREVIEW_EXTENSIONS,
  TEXT_EXTENSIONS,
  getWorkspaceFileExtension,
  shouldDownloadInsteadOfPreview,
} from './documentPreviewFormats.ts'

export type GeneratedPreviewKind = 'url-document' | 'blob' | 'text' | 'download-only'

export type GeneratedPreviewCanvasType = 'document' | 'pdf' | 'image' | 'csv' | 'html' | 'code'

export interface GeneratedPreviewPlan {
  kind: GeneratedPreviewKind
  canvasType: GeneratedPreviewCanvasType | null
}

const HTML_EXTENSIONS = new Set(['.html', '.htm'])

/**
 * 产物扩展名 → 画布数据形态；自上而下短路匹配。
 *
 * 几个刻意的边界：
 *  - `.ppt`（旧版二进制）与未知扩展名一律 `download-only`：前者需要带内置水印、
 *    许可受限的 `@file-viewer/ppt`，后者没有画布渲染分支；
 *  - `.md` 归 `text/code` 而不是 `document`：画布靠 `title` 后缀命中 isMarkdownFile，
 *    归 code 才能拿到带 mermaid / ECharts 的画布 Markdown 渲染器；
 *  - `.csv` 归 `blob/csv`：`.csv` 不在 DOCUMENT_VIEWER_EXTENSIONS 内，画布的
 *    PivotTable 分析能力也只挂在 `type === 'csv'` 上。
 */
export function resolveGeneratedPreviewPlan(name: string): GeneratedPreviewPlan {
  const ext = getWorkspaceFileExtension(name)

  if (!ext || shouldDownloadInsteadOfPreview(name)) {
    return { kind: 'download-only', canvasType: null }
  }

  if (OFFICE_PREVIEW_EXTENSIONS.has(ext)) {
    return { kind: 'url-document', canvasType: 'document' }
  }
  if (ext === '.pdf') {
    return { kind: 'url-document', canvasType: 'pdf' }
  }
  if (IMAGE_EXTENSIONS.has(ext)) {
    return { kind: 'blob', canvasType: 'image' }
  }
  if (ext === '.csv') {
    return { kind: 'blob', canvasType: 'csv' }
  }
  if (HTML_EXTENSIONS.has(ext)) {
    return { kind: 'text', canvasType: 'html' }
  }
  // 文本与代码取两者的**并集**：DOCUMENT_VIEWER_EXTENSIONS 里有 `.markdown`、`.jsonc`、
  // `.toml`、`.vue` 等 TEXT_EXTENSIONS 没有的格式（行为测试就是被 `.markdown` 抓出来的），
  // 而 TEXT_EXTENSIONS 里有 `.env`、`.conf` 这两个前者没有的格式。
  if (DOCUMENT_VIEWER_EXTENSIONS.has(ext) || TEXT_EXTENSIONS.has(ext)) {
    return { kind: 'text', canvasType: 'code' }
  }

  return { kind: 'download-only', canvasType: null }
}

/**
 * 产物下载地址：追加 `download=1` 强制后端 `Content-Disposition: attachment`。
 *
 * 后端对 html / pdf / 图片默认给 `inline`（`chat.py:232-250`），不追加这个参数时
 * 「下载」按钮会退化成新标签页预览。产物下载地址的构造以此处为**单一来源**：
 * 抽屉的下载按钮、消息正文的「下载」按钮、`ChatCanvas.downloadFile` 的直链兜底
 * 三处都调它。
 */
export function buildArtifactDownloadUrl(url: string): string {
  const raw = String(url || '')
  if (!raw) return ''
  if (/[?&]download=/.test(raw)) return raw
  return raw.includes('?') ? `${raw}&download=1` : `${raw}?download=1`
}
