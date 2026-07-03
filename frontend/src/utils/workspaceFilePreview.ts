import axios from '@/utils/axios'

export type WorkspaceCanvasType = 'html' | 'code' | 'pdf' | 'csv' | 'image'

const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.webp', '.gif'])
const TEXT_EXTENSIONS = new Set([
  '.txt', '.md', '.csv', '.json', '.sql', '.py', '.js', '.ts',
  '.sh', '.xml', '.html', '.css', '.yaml', '.yml', '.ini', '.conf',
  '.log', '.env', '.htm',
])
const OFFICE_EXTENSIONS = new Set([
  '.docx', '.doc', '.xlsx', '.xls', '.xlsm', '.pptx', '.ppt',
])

export type CanvasPanelData = {
  type: WorkspaceCanvasType | 'compare' | 'mermaid'
  title: string
  content: string
  sourcePath?: string
  compareContent?: string
  compareTitle?: string
}

export function getWorkspaceFileExtension(name: string): string {
  const parts = name.split('.')
  if (parts.length < 2) return ''
  return `.${parts.pop()!.toLowerCase()}`
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
  return 'code'
}

export function canWriteWorkspaceFile(name: string): boolean {
  const ext = getWorkspaceFileExtension(name)
  if (!ext) return false
  return TEXT_EXTENSIONS.has(ext)
}

export function shouldAttachWorkspaceSourcePath(path: string, name: string): boolean {
  if (!canWriteWorkspaceFile(name)) return false
  const normalized = path.replace(/\\/g, '/')
  return normalized.includes('/agent_workspaces/')
}

export function buildWorkspaceCanvasPayload(path: string, name: string) {
  return {
    type: resolveWorkspaceCanvasType(name),
    title: name || '文件预览',
    content: `canvas://file?path=${encodeURIComponent(path)}`,
    sourcePath: shouldAttachWorkspaceSourcePath(path, name) ? path : undefined,
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

export function resolveFsPreviewUrl(path: string, conversationId?: string | null): string {
  if (!path) return ''
  if (
    path.startsWith('http://') ||
    path.startsWith('https://') ||
    path.startsWith('data:') ||
    path.startsWith('/static/') ||
    path.startsWith('/api/') ||
    path.startsWith('/assets/')
  ) {
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

    if (payload.type === 'pdf' || payload.type === 'image' || payload.type === 'csv') {
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
    onOpen({
      type: payload.type,
      title: payload.title,
      content: resText,
      sourcePath: shouldAttachWorkspaceSourcePath(path, name) ? path : undefined,
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
