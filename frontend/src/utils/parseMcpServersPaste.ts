/**
 * 解析常见 MCP 客户端配置 JSON（Cursor / Claude Desktop / ModelScope 导出等），
 * 提取可填入登记向导的 url / headers / 建议后缀名。
 */

export type ParsedMcpPasteEntry = {
  /** 配置里的服务键名，用作名称后缀建议 */
  key: string
  url: string
  type?: string
  headers: Record<string, string>
}

export type ParseMcpServersPasteResult =
  | { ok: true; entries: ParsedMcpPasteEntry[]; warning?: string }
  | { ok: false; error: string }

const pickUrl = (cfg: Record<string, unknown>): string => {
  const candidates = [cfg.url, cfg.serverUrl, cfg.sse_url, cfg.sseUrl, cfg.endpoint]
  for (const c of candidates) {
    const s = String(c || '').trim()
    if (s) return s
  }
  return ''
}

const pickHeaders = (cfg: Record<string, unknown>): Record<string, string> => {
  const out: Record<string, string> = {}
  const raw = cfg.headers
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
      const key = String(k || '').trim()
      const val = String(v ?? '').trim()
      if (key && val) out[key] = val
    }
  }
  // 部分导出把 Authorization 放在 env
  const env = cfg.env
  if (env && typeof env === 'object' && !Array.isArray(env)) {
    const envObj = env as Record<string, unknown>
    for (const [k, v] of Object.entries(envObj)) {
      const key = String(k || '').trim()
      const val = String(v ?? '').trim()
      if (!key || !val) continue
      if (/^authorization$/i.test(key) && !out.Authorization) {
        out.Authorization = val.startsWith('Bearer ') ? val : `Bearer ${val}`
      }
    }
  }
  return out
}

const normalizeServerMap = (root: unknown): Record<string, unknown> | null => {
  if (!root || typeof root !== 'object' || Array.isArray(root)) return null
  const obj = root as Record<string, unknown>
  if (obj.mcpServers && typeof obj.mcpServers === 'object' && !Array.isArray(obj.mcpServers)) {
    return obj.mcpServers as Record<string, unknown>
  }
  if (obj.servers && typeof obj.servers === 'object' && !Array.isArray(obj.servers)) {
    return obj.servers as Record<string, unknown>
  }
  // 单服务直出：{ "url": "..." } 或带 type
  if (pickUrl(obj)) {
    return { server: obj }
  }
  return null
}

/**
 * 从粘贴文本解析 MCP 服务配置。
 * 多服务时全部返回，由 UI 选择或默认取第一项。
 */
export const parseMcpServersPaste = (raw: string): ParseMcpServersPasteResult => {
  const text = String(raw || '').trim()
  if (!text) {
    return { ok: false, error: '请粘贴 MCP 配置 JSON' }
  }

  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return { ok: false, error: 'JSON 格式无效，请检查是否完整复制' }
  }

  const map = normalizeServerMap(parsed)
  if (!map || !Object.keys(map).length) {
    return {
      ok: false,
      error: '未识别到 mcpServers 配置。请粘贴形如 { "mcpServers": { "name": { "url": "..." } } } 的内容',
    }
  }

  const entries: ParsedMcpPasteEntry[] = []
  for (const [key, value] of Object.entries(map)) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) continue
    const cfg = value as Record<string, unknown>
    const url = pickUrl(cfg)
    if (!url) continue
    const type = String(cfg.type || cfg.transport || '').trim() || undefined
    entries.push({
      key: String(key || 'server').trim() || 'server',
      url,
      type,
      headers: pickHeaders(cfg),
    })
  }

  if (!entries.length) {
    const hasCommand = Object.values(map).some((val) => {
      if (val && typeof val === 'object' && !Array.isArray(val)) {
        const v = val as Record<string, unknown>
        return Boolean(v.command || v.cmd)
      }
      return false
    })
    if (hasCommand) {
      return {
        ok: false,
        error:
          '检测到本地命令行（STDIO）模式的 MCP 配置。本平台为云端 Web 架构，不支持直接启动本地进程；请使用 mcp-proxy 或将该服务以 SSE / HTTP 远程服务形态部署后填入 URL。',
      }
    }
    return { ok: false, error: '配置中未找到有效的 url / serverUrl 字段' }
  }
  const firstEntry = entries[0]
  if (!firstEntry) {
    return { ok: false, error: '配置中未找到有效的服务条目' }
  }

  let warning: string | undefined
  if (entries.length > 1) {
    warning = `检测到 ${entries.length} 个服务，已默认填入第一个「${firstEntry.key}」，可修改地址后继续`
  }
  const unsupported = entries.filter(
    (e) => e.type && !/sse|http|streamable|streamable_http|streamable-http/i.test(e.type),
  )
  if (unsupported.length) {
    warning = [
      warning,
      `部分 type（如 ${unsupported.map((e) => e.type).join(', ')}）可能需本平台探测后才能确认是否兼容`,
    ]
      .filter(Boolean)
      .join('；')
  }

  return { ok: true, entries, warning }
}

/** 建议用作名称后缀的片段（去掉常见 mcp- 前缀噪音仍保留可读性） */
export const suggestMcpNameSuffixFromKey = (key: string): string => {
  const raw = String(key || '').trim()
  if (!raw) return ''
  return raw
    .replace(/^mcp[-_]+/i, '')
    .replace(/[^a-zA-Z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '')
}
