/**
 * 生成产物的 URL 解析。
 *
 * 生成类产物（图片、导出件）由后端落盘到 `<user_key>/generated/`，但工具交给模型/前端的
 * 只有 `download_url`——形如 `/api/v1/chat/generated-files/<32位hex>?token=...`。
 * 路径本身从没出过后端，前端据此只能判断「没有工作区路径」，于是把已经在工作区里的文件
 * 误判成未落盘（分享入口置灰、保存时又白复制一份）。
 *
 * 这里只做一件事：从 URL 里把 artifact_id 抠出来，交给后端反查真实路径。
 * 保持成纯函数，方便直接跑边界测试。
 */

/** 32 位十六进制；download_url 与 ai_artifacts.id 用的是同一套格式。 */
const ARTIFACT_ID_RE = /^[0-9a-f]{32}$/i;

/**
 * 从产物链接里取出 artifact_id；不是产物链接就返回空串。
 *
 * 认的形态（后端两代路径都有）：
 * - `/api/v1/chat/generated-files/<id>?token=xxx`
 * - `/api/v1/generated-files/<id>?token=xxx`
 * - 带域名或带 hash 后缀的绝对地址
 */
export function extractArtifactId(url: string): string {
  if (!url) return '';
  const marker = '/generated-files/';
  const at = url.toLowerCase().indexOf(marker);
  if (at < 0) return '';

  const rest = url.slice(at + marker.length);
  // 只认第一个路径段，避免把查询串里的十六进制串误当成 ID
  const segment = rest.split(/[/?#]/)[0] || '';
  if (!ARTIFACT_ID_RE.test(segment)) return '';
  return segment.toLowerCase();
}

/** 该链接是否是可分享的生成产物。 */
export function isGeneratedArtifactUrl(url: string): boolean {
  return extractArtifactId(url) !== '';
}
