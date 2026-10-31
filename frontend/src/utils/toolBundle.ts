/**
 * 工具包（JSON 导出/导入）的前端工具函数。
 *
 * 只在浏览器侧做「能不能读、要不要提醒」这类轻判断；真正的字段校验、查重与落库
 * 全部由服务端负责（``/api/portal/tools/import/precheck``）。前端重复实现一遍校验
 * 只会制造第二份会漂移的规则。
 */
import type { ToolBundle } from '@/api/tool'

/** 与后端 ``tool_credentials.CREDENTIAL_PLACEHOLDER_PREFIX`` 逐字一致 */
export const CREDENTIAL_PLACEHOLDER_PREFIX = '${CREDENTIAL:'

export const BUNDLE_SCHEMA = 'nanzi.tool.bundle'
export const BUNDLE_SCHEMA_VERSION = 1

export function isCredentialPlaceholder(value: unknown): boolean {
  return typeof value === 'string' && value.trim().startsWith(CREDENTIAL_PLACEHOLDER_PREFIX)
}

/** 行内「待补凭据」徽标用：哪些 header 还是占位符 */
export function pendingCredentialHeaders(headers?: Record<string, string> | null): string[] {
  return Object.entries(headers || {})
    .filter(([, value]) => isCredentialPlaceholder(value))
    .map(([name]) => name)
}

export function bundleKindLabel(kind: string): string {
  if (kind === 'api') return 'API 工具'
  if (kind === 'script') return '脚本工具'
  return kind || '未知'
}

/** 读文件并做「文件级」校验；条目的字段问题留给服务端预检逐条报告 */
export function parseBundleText(text: string): ToolBundle {
  let parsed: any
  try {
    parsed = JSON.parse(text)
  } catch {
    throw new Error('文件不是合法的 JSON，请确认导出时没有改坏文件')
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('文件内容不是 JSON 对象')
  }
  if (parsed.schema !== BUNDLE_SCHEMA) {
    throw new Error('这不是工具包文件：schema 应为 nanzi.tool.bundle')
  }
  const version = Number(parsed.schema_version)
  if (!Number.isInteger(version) || version < 1) {
    throw new Error('schema_version 缺失或不是正整数')
  }
  if (version > BUNDLE_SCHEMA_VERSION) {
    throw new Error(
      `文件由更新版本的平台导出（schema_version=${version}，当前支持 ${BUNDLE_SCHEMA_VERSION}）`,
    )
  }
  if (!Array.isArray(parsed.tools) || parsed.tools.length === 0) {
    throw new Error('文件里没有工具（tools 为空）')
  }
  return parsed as ToolBundle
}

/** 服务端下的文件名优先；没有就按时间戳生成一个 */
export function resolveDownloadFilename(disposition: string | undefined, fallback: string): string {
  if (!disposition) return fallback
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
  if (utf8?.[1]) {
    try {
      return decodeURIComponent(utf8[1].trim())
    } catch {
      /* 编码坏了就退回 ASCII 那个 */
    }
  }
  const ascii = /filename="?([^";]+)"?/i.exec(disposition)
  return ascii?.[1]?.trim() || fallback
}

export function defaultBundleFilename(kind: 'api' | 'script' | 'all', now = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, '0')
  const stamp =
    `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}` +
    `-${pad(now.getHours())}${pad(now.getMinutes())}`
  const suffix = kind === 'all' ? '' : `-${kind}`
  return `nanzi-tools${suffix}-${stamp}.json`
}

/** 触发浏览器下载（与 chatSessionExport 的同类实现保持一致的写法） */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

export function formatBundleSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/**
 * 导出接口用 ``responseType: 'blob'``，出错时错误体也是 Blob，必须读完才能拿到
 * 后端给的 ``detail``；否则界面上只剩一句「导出失败」，用户不知道是权限还是文件太大。
 */
export async function readBundleError(error: any): Promise<string> {
  const data = error?.response?.data
  if (data instanceof Blob) {
    try {
      const parsed = JSON.parse(await data.text())
      if (typeof parsed?.detail === 'string') return parsed.detail
    } catch {
      /* 非 JSON 就退回下面的通用文案 */
    }
  }
  const detail = data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map((item: any) => item?.msg || JSON.stringify(item)).join('；')
  }
  return error?.message || '请求失败'
}
