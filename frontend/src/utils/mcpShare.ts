/** 个人 MCP 分享的共享类型与文案映射。 */
export type McpPersonalMenuKey = 'mine' | 'received' | 'sent'

/** 「我分享出去的」与「我收到的」共用同一份字段（后端 _share_payload）。 */
export interface McpShareItem {
  id: string
  source_user_id: number
  source_user_name: string | null
  source_real_name: string | null
  source_server_id: string
  server_name: string
  remark: string | null
  sse_url: string
  include_credentials: boolean
  message: string | null
  target_user_id: number
  target_user_name: string | null
  target_real_name: string | null
  status: string
  accepted_server_id: string | null
  /** 后端按接收人账号算好的默认落地名，安装向导用它预填后缀 */
  suggested_server_name: string | null
  created_at: string | null
  responded_at: string | null
}

/** 展示分享人 / 接收人时统一取「真名 > 用户名 > 兜底」。 */
export function shareDisplayName(item: McpShareItem, side: 'source' | 'target'): string {
  if (side === 'source') {
    return item.source_real_name || item.source_user_name || `用户 ${item.source_user_id}`
  }
  return item.target_real_name || item.target_user_name || `用户 ${item.target_user_id}`
}

/** 状态文案与技能分享保持一致（同一套状态机）。 */
export { SHARE_STATUS_META, shareStatusMeta } from './skillShare'
