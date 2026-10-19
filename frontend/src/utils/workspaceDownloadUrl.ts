/**
 * 工作区文件「下载」URL 构造 —— 刻意保持零运行时依赖（无 axios、无别名导入）。
 *
 * 这样本模块能被 `node --experimental-strip-types` 直接加载，对编码行为做真实断言
 * （见 tests/frontend/workspace_download_url.mjs）。
 *
 * 为什么下载不能复用预览接口：`/api/v1/chat/fs/preview` 按扩展名白名单放行
 * （图片 / 文本 / PDF / Office），这是「能否在画布渲染」的语义。拿它当下载用，
 * zip、7z、无扩展名文件会一律 400 —— 用户在列表里看得见文件却下不下来。
 * 下载因此走 `/api/v1/chat/fs/download`：归属校验与预览完全一致，但不限制 MIME。
 */
export const WORKSPACE_DOWNLOAD_ENDPOINT = '/api/v1/chat/fs/download'

/**
 * 生成下载地址。空路径返回空串，调用方据此提示而不是发出一次注定 404 的请求。
 *
 * path 必须整体 encodeURIComponent：路径里的 `&`、`#`、`?` 不编码会被拆成伪 query
 * 参数或 fragment，服务端拿到的是截断后的路径。
 */
export function buildWorkspaceDownloadUrl(
  path: string | null | undefined,
  conversationId?: string | null,
): string {
  const rawPath = String(path ?? '')
  if (!rawPath.trim()) return ''

  const convId = String(conversationId ?? '').trim()
  const convParam = convId ? `&conversation_id=${encodeURIComponent(convId)}` : ''
  return `${WORKSPACE_DOWNLOAD_ENDPOINT}?path=${encodeURIComponent(rawPath)}${convParam}`
}
