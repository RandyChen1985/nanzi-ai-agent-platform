/**
 * MCP 工具展示短名：分组内已显示服务名时，去掉 name 中的服务前缀。
 * 配置值 / 运行时仍使用完整 tool.name。
 */
export const mcpToolDisplayName = (
  toolName: unknown,
  serverName?: unknown,
  fallback = '未命名工具',
): string => {
  const raw = String(toolName || '').trim()
  if (!raw) return fallback
  const server = String(serverName || '').trim()
  if (!server) {
    // 无服务名时仍尽量去掉常见 server:tool 前缀的左侧段
    const colonIdx = raw.indexOf(':')
    if (colonIdx > 0 && colonIdx < raw.length - 1) {
      return raw.slice(colonIdx + 1).trim() || raw
    }
    return raw
  }
  const prefixes = [`${server}:`, `${server}/`, `${server}.`]
  for (const prefix of prefixes) {
    if (raw.toLowerCase().startsWith(prefix.toLowerCase())) {
      const shortName = raw.slice(prefix.length).trim()
      if (shortName) return shortName
    }
  }
  if (raw.toLowerCase().startsWith(server.toLowerCase()) && raw.length > server.length + 1) {
    // 必须紧跟明确分隔符才允许剥离服务名前缀，避免 server="git" 把 "github_search" 截残成 "hub_search"
    const tail = raw.slice(server.length)
    if (/^[:/._-]+/.test(tail)) {
      const rest = tail.replace(/^[:/._-]+/, '').trim()
      if (rest) return rest
    }
  }
  const colonIdx = raw.indexOf(':')
  if (colonIdx > 0 && colonIdx < raw.length - 1) {
    return raw.slice(colonIdx + 1).trim() || raw
  }
  return raw
}
