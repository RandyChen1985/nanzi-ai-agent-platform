import axios from '@/utils/axios'
import { copyToClipboard } from './clipboard'
import type { CanvasPanelData, WorkspaceCanvasType } from '@/types/canvas'
import {
  IMAGE_EXTENSIONS,
  OFFICE_EXTENSIONS,
  OFFICE_PREVIEW_EXTENSIONS,
  TEXT_EXTENSIONS,
  getWorkspaceFileExtension,
  resolveDocumentViewerMime,
} from './documentPreviewFormats'

// 格式判定集中在零依赖模块，便于 Node 行为测试；此处 re-export 保持既有导入路径不变
export * from './documentPreviewFormats'

export type { CanvasPanelData, WorkspaceCanvasType } from '@/types/canvas'

// 以下为依赖 axios / clipboard 的工作区编排逻辑，保持原样（normalizeWorkspacePath 起）

export function normalizeWorkspacePath(path: string): string {
  return String(path || '').replace(/\\/g, '/').replace(/\/+$/, '')
}

export function isSameWorkspacePreviewPath(
  a: string | null | undefined,
  b: string | null | undefined,
): boolean {
  if (!a || !b) return false
  return normalizeWorkspacePath(a) === normalizeWorkspacePath(b)
}

export function resolveWorkspaceScriptLanguage(name: string): 'python' | 'shell' | null {
  const ext = getWorkspaceFileExtension(name)
  if (ext === '.py') return 'python'
  if (ext === '.sh' || ext === '.bash') return 'shell'
  return null
}

export function canPreviewWorkspaceFile(name: string): boolean {
  const ext = getWorkspaceFileExtension(name)
  if (!ext) return false
  return (
    ext === '.pdf' ||
    IMAGE_EXTENSIONS.has(ext) ||
    TEXT_EXTENSIONS.has(ext) ||
    OFFICE_EXTENSIONS.has(ext)
  )
}

export function resolveWorkspaceCanvasType(name: string): WorkspaceCanvasType {
  const lower = name.toLowerCase()
  if (lower.endsWith('.csv')) return 'csv'
  if (lower.endsWith('.pdf')) return 'pdf'
  if (/\.(jpe?g|png|gif|webp)$/.test(lower)) return 'image'
  if (lower.endsWith('.html') || lower.endsWith('.htm')) return 'html'
  // 只有 Office 才在画布里走 'document'（.ppt 由 useWorkspaceCanvas 单独处理成下载）；
  // 文本格式必须留在 'code'，否则会丢掉画布的 markdown 渲染与编辑能力。
  if (OFFICE_PREVIEW_EXTENSIONS.has(getWorkspaceFileExtension(name))) return 'document'
  return 'code'
}

export function canWriteWorkspaceFile(name: string): boolean {
  const ext = getWorkspaceFileExtension(name)
  if (!ext) return false
  return TEXT_EXTENSIONS.has(ext)
}

function sanitizeGeneratedFilenameBase(title: string): string {
  const base = String(title || '')
    .replace(/\\/g, '/')
    .split('/')
    .pop()
    ?.replace(/\.[^.]+$/, '')
    .replace(/[^\p{L}\p{N}_-]+/gu, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80)
  return base || 'generated-file'
}

/** 为没有 sourcePath 的画布内容生成安全、可识别的默认文件名。 */
export function buildGeneratedWorkspaceFilename(
  title: string,
  options?: { language?: string; type?: string },
): string {
  const existingExtension = getWorkspaceFileExtension(title)
  if (existingExtension && TEXT_EXTENSIONS.has(existingExtension)) {
    const filename = String(title || '').replace(/\\/g, '/').split('/').pop() || ''
    return filename || `generated-file${existingExtension}`
  }

  const language = String(options?.language || '').toLowerCase()
  const type = String(options?.type || '').toLowerCase()
  let extension = '.txt'
  if (language === 'python' || language === 'python3') extension = '.py'
  else if (language === 'shell' || language === 'sh' || language === 'bash') extension = '.sh'
  else if (type === 'html' || language === 'html' || /html|xml/i.test(title)) extension = '.html'
  else if (type === 'markdown' || type === 'md' || /markdown|\.md$/i.test(title)) extension = '.md'
  else if (language === 'javascript' || language === 'js') extension = '.js'
  else if (language === 'typescript' || language === 'ts') extension = '.ts'
  else if (language === 'sql') extension = '.sql'
  else if (language === 'css') extension = '.css'
  else if (language === 'json') extension = '.json'

  return `${sanitizeGeneratedFilenameBase(title)}${extension}`
}

export function shouldAttachWorkspaceSourcePath(path: string, name: string): boolean {
  if (!canWriteWorkspaceFile(name)) return false
  const normalized = path.replace(/\\/g, '/')
  return normalized.includes('/agent_workspaces/') || normalized.startsWith('/workspace') || normalized.startsWith('workspace/')
}

export function buildWorkspaceCanvasPayload(path: string, name: string) {
  const scriptLanguage = resolveWorkspaceScriptLanguage(name)
  return {
    type: resolveWorkspaceCanvasType(name),
    title: name || '文件预览',
    content: `canvas://file?path=${encodeURIComponent(path)}`,
    sourcePath: shouldAttachWorkspaceSourcePath(path, name) ? path : undefined,
    langName: scriptLanguage || undefined,
    runnable: !!scriptLanguage,
  }
}

export function resolvePublicUploadsPreviewUrl(path: string): string | null {
  const normalized = path.replace(/\\/g, '/')
  if (normalized.includes('/agent_workspaces/')) return null

  const prefixes = [
    'uploads/',
    '/uploads/',
    'data/uploads/',
    '/data/uploads/',
    'app/data/uploads/',
    '/app/data/uploads/',
  ]
  for (const prefix of prefixes) {
    if (normalized.startsWith(prefix)) {
      return `/static/uploads/${normalized.slice(prefix.length).replace(/^\/+/, '')}`
    }
  }

  const marker = '/data/uploads/'
  const markerIndex = normalized.indexOf(marker)
  if (markerIndex >= 0) {
    return `/static/uploads/${normalized.slice(markerIndex + marker.length).replace(/^\/+/, '')}`
  }
  return null
}

/** 已是可直接用于 img/iframe/fetch 的 URL，无需再走 fs preview 转换 */
export function isDirectRenderableUrl(url: string): boolean {
  return (
    url.startsWith('http://') ||
    url.startsWith('https://') ||
    url.startsWith('data:') ||
    url.startsWith('blob:') ||
    url.startsWith('quick:') ||
    url.startsWith('canvas:') ||
    url.startsWith('/static/') ||
    url.startsWith('/api/') ||
    url.startsWith('/assets/')
  )
}

export function resolveFsPreviewUrl(path: string, conversationId?: string | null): string {
  if (!path) return ''
  if (isDirectRenderableUrl(path)) {
    return path
  }
  const publicUploadUrl = resolvePublicUploadsPreviewUrl(path)
  if (publicUploadUrl) return publicUploadUrl
  const convParam = conversationId ? `&conversation_id=${encodeURIComponent(conversationId)}` : ''
  return `/api/v1/chat/fs/preview?path=${encodeURIComponent(path)}${convParam}`
}

type OpenWorkspacePreviewOptions = {
  path: string
  name: string
  conversationId?: string | null
  showToast: (message: string, type?: 'success' | 'error' | 'warning' | 'info') => void
  onOpen: (data: CanvasPanelData) => void
  activeBlobUrlRef?: { value: string }
}

export async function openWorkspaceFileInCanvas(options: OpenWorkspacePreviewOptions) {
  const { path, name, conversationId, showToast, onOpen, activeBlobUrlRef } = options

  if (!canPreviewWorkspaceFile(name)) {
    showToast('不支持预览该类型的文件', 'error')
    return
  }

  const payload = buildWorkspaceCanvasPayload(path, name)
  const filePath = path
  const resolvedUrl = resolveFsPreviewUrl(filePath, conversationId)
  const ext = getWorkspaceFileExtension(name)

  if (activeBlobUrlRef?.value) {
    try {
      URL.revokeObjectURL(activeBlobUrlRef.value)
    } catch {
      /* ignore */
    }
    activeBlobUrlRef.value = ''
  }

  try {
    // 只有 Office 走 DocumentViewer。文本格式（.md/.ts/.txt 等）必须落到下方
    // 取 resText 的分支，否则会被当成文档预览：徽章显示 OFFICE，且丢掉代码视图
    // 与可运行脚本能力。DOCUMENT_VIEWER_EXTENSIONS 含 50 种文本格式，那是 RAG
    // 抽屉与知识库弹窗的语义，不可用于画布 / 工作空间的分派。
    if (OFFICE_PREVIEW_EXTENSIONS.has(ext)) {
      // 只传鉴权 URL：Blob 由 DocumentViewer 用 axios 获取，
      // 避免 Blob 进入响应式状态，也避免在此处创建对象 URL。
      onOpen({
        type: 'document',
        title: name,
        content: resolvedUrl,
        documentMeta: { filename: name, mime: resolveDocumentViewerMime(name) },
        downloadPath: path,
      })
      return
    }

    // 旧版二进制 .ppt 仍需走下载：它处于 OFFICE_EXTENSIONS 但不在 OFFICE_PREVIEW_EXTENSIONS
    if (OFFICE_EXTENSIONS.has(ext)) {
      const response = await axios.get(resolvedUrl, { responseType: 'blob' })
      const filename = name || 'download'
      const blobUrl = URL.createObjectURL(response.data)
      const link = document.createElement('a')
      link.href = blobUrl
      link.download = filename
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      URL.revokeObjectURL(blobUrl)
      showToast(`已开始下载 ${filename}`, 'success')
      return
    }

    if (payload.type === 'pdf') {
      // PDF 同样交给 DocumentViewer，只传 URL，不预取 Blob
      onOpen({
        type: 'pdf',
        title: name,
        content: resolvedUrl,
        documentMeta: { filename: name, mime: 'application/pdf' },
        downloadPath: path,
      })
      return
    }

    if (payload.type === 'image' || payload.type === 'csv') {
      const response = await axios.get(resolvedUrl, { responseType: 'blob' })
      const blobUrl = URL.createObjectURL(response.data)
      if (activeBlobUrlRef) activeBlobUrlRef.value = blobUrl
      onOpen({
        type: payload.type,
        title: payload.title,
        content: blobUrl,
      })
      return
    }

    const resText = await axios.get(resolvedUrl).then((res) => res.data)
    const scriptLanguage = resolveWorkspaceScriptLanguage(name)
    onOpen({
      type: payload.type,
      title: payload.title,
      content: resText,
      sourcePath: shouldAttachWorkspaceSourcePath(path, name) ? path : undefined,
      langName: scriptLanguage || undefined,
      runnable: !!scriptLanguage,
    })
  } catch (err: any) {
    console.error('加载工作空间文件失败:', err)
    let errMsg = '加载文件失败'
    if (err.response?.data?.detail) {
      errMsg = err.response.data.detail
    } else if (err.response?.status === 404) {
      errMsg = '预览的文件不存在，请确认路径是否正确。'
    } else if (err.response?.status === 403) {
      errMsg = '安全拦截：无权访问该服务器文件。'
    } else if (err.response?.status === 400) {
      errMsg = err.response?.data?.detail || '不支持预览该类型的文件。'
    } else {
      errMsg = err.message || String(err)
    }
    showToast(errMsg, 'error')
  }
}

export async function downloadWorkspaceFile(options: {
  path: string
  name: string
  conversationId?: string | null
  showToast: (message: string, type?: 'success' | 'error' | 'warning' | 'info') => void
}) {
  const { path, name, conversationId, showToast } = options
  const resolvedUrl = resolveFsPreviewUrl(path, conversationId)

  try {
    const response = await axios.get(resolvedUrl, { responseType: 'blob' })
    const filename = name || 'download'
    const blobUrl = URL.createObjectURL(response.data)
    const link = document.createElement('a')
    link.href = blobUrl
    link.download = filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(blobUrl)
    showToast(`已开始下载 ${filename}`, 'success')
  } catch (err: any) {
    console.error('下载工作空间文件失败:', err)
    let errMsg = '下载文件失败'
    if (err.response?.data?.detail) {
      errMsg = err.response.data.detail
    } else if (err.response?.status === 404) {
      errMsg = '文件不存在，请确认路径是否正确。'
    } else if (err.response?.status === 403) {
      errMsg = '安全拦截：无权访问该服务器文件。'
    } else if (err.response?.status === 400) {
      errMsg = err.response?.data?.detail || '不支持下载该类型的文件。'
    }
    showToast(errMsg, 'error')
  }
}

export async function saveWorkspaceFileContent(options: {
  path: string
  content: string
  conversationId?: string | null
}) {
  const payload: Record<string, string> = {
    path: options.path,
    content: options.content,
  }
  if (options.conversationId) {
    payload.conversation_id = options.conversationId
  }
  return axios.put('/api/v1/chat/fs/write', payload)
}

export async function createWorkspaceEntry(options: {
  parentPath: string
  name: string
  kind: 'file' | 'dir'
  content?: string
}) {
  return axios.post('/api/v1/chat/fs/create-entry', {
    parent_path: options.parentPath,
    name: options.name,
    kind: options.kind,
    content: options.content ?? '',
  })
}

export async function renameWorkspaceEntry(path: string, newName: string) {
  return axios.post('/api/v1/chat/fs/rename-entry', { path, new_name: newName })
}

export async function deleteWorkspaceEntry(path: string) {
  return axios.post('/api/v1/chat/fs/delete-entry', { path })
}

export async function restoreWorkspaceEntry(path: string) {
  return axios.post('/api/v1/chat/fs/restore-entry', { path })
}

export async function purgeWorkspaceEntry(path: string) {
  return axios.post('/api/v1/chat/fs/purge-entry', { path })
}

export async function emptyWorkspaceTrash() {
  return axios.post('/api/v1/chat/fs/empty-trash')
}

export async function uploadToWorkspaceDir(parentPath: string, file: File) {
  const form = new FormData()
  form.append('file', file)
  return axios.post('/api/v1/chat/fs/upload', form, {
    params: { parent_path: parentPath },
  })
}

export async function copyTextToClipboard(text: string) {
  const ok = await copyToClipboard(text)
  if (!ok) throw new Error('copy failed')
}
