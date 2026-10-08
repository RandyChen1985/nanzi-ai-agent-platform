/**
 * 画布类型定义的唯一来源。
 *
 * 此前该类型分散在 ChatCanvas.vue、useWorkspaceCanvas.ts、
 * workspaceFilePreview.ts、MessageRenderer.vue 四处，且已互相漂移
 * （前三处把 mermaid/compare 纳入，workspaceFilePreview 则另用联合扩展），
 * 现统一到本文件，避免继续分叉。
 */

/** 画布面板可渲染的内容类型 */
export type WorkspaceCanvasType =
  | 'html'
  | 'code'
  | 'mermaid'
  | 'pdf'
  | 'csv'
  | 'image'
  | 'compare'
  | 'document'

/** 交给 DocumentViewer 渲染时所需的文件元信息 */
export interface CanvasDocumentMeta {
  filename: string
  mime?: string
  size?: number
}

/** 画布面板数据 */
export interface CanvasPanelData {
  type: WorkspaceCanvasType
  title: string
  content: string
  sourcePath?: string
  langName?: string
  runnable?: boolean
  compareContent?: string
  compareTitle?: string
  /** document / pdf 类型的渲染元信息 */
  documentMeta?: CanvasDocumentMeta
  /** 仅用于「下载原文件」，不参与 canSaveWorkspaceFile 判定 */
  downloadPath?: string
}
