import type { ToastOptions } from '@/composables/useToast'

type RequestedTool = { id: string; name: string }

export function mcpMountFeedback(requested: RequestedTool[], saved: unknown): ToastOptions {
  const savedIds = new Set(
    Array.isArray(saved) ? saved.map(tool => String(tool?.id || '')) : [],
  )
  const accepted = requested.filter(tool => savedIds.has(tool.id))
  if (!accepted.length) {
    return { message: '未能挂载 MCP 工具，请确认服务已启用且工具已发布。', type: 'error' }
  }
  if (accepted.length < requested.length) {
    return { message: `已挂载 ${accepted.length} 个 MCP 工具。${requested.length - accepted.length} 个不可用。`, type: 'warning' }
  }
  return {
    message: accepted.length === 1 ? `已挂载 MCP 工具：${accepted[0]?.name}` : `已挂载 ${accepted.length} 个 MCP 工具`,
    type: 'success',
  }
}
