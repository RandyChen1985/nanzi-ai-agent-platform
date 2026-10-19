/**
 * 从 axios 错误响应体里提取后端 `detail`。
 *
 * `responseType: 'blob'` 时，即使接口返回 4xx，`response.data` 也是 Blob；
 * 直接读 `.detail` 永远是 undefined，后端给的具体原因（例如「目标不是文件，
 * 无法下载。」）就会被兜底文案吞掉。这里统一先把 Blob 还原成文本再解析。
 *
 * 保持零依赖，便于 Node 直接跑行为测试。
 */
export async function readErrorDetail(data: unknown): Promise<string | undefined> {
  let payload = data

  if (typeof Blob !== 'undefined' && payload instanceof Blob) {
    try {
      payload = await payload.text()
    } catch {
      return undefined
    }
  }

  if (typeof payload === 'string') {
    const text = payload.trim()
    if (!text) return undefined
    try {
      payload = JSON.parse(text)
    } catch {
      return text
    }
  }

  if (payload && typeof payload === 'object' && 'detail' in payload) {
    const detail = (payload as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail.trim() || undefined
    if (detail != null) return JSON.stringify(detail)
  }

  return undefined
}
