import axios from '@/utils/axios'

export interface SysApiTool {
  id: string
  name: string
  description?: string
  method: string
  url_template: string
  headers?: Record<string, string>
  parameter_schema?: Record<string, any>
  is_active: boolean
  /** 业务分组名：智能体配置的「工具能力」步骤按它归组；为空表示未分组 */
  group_name?: string | null
  created_at: string
  updated_at: string
}

export interface SysApiToolCreate {
  name: string
  description?: string
  method: string
  url_template: string
  headers?: Record<string, string>
  parameter_schema?: Record<string, any>
  is_active?: boolean
  group_name?: string | null
}

export interface SysApiToolUpdate {
  name?: string
  description?: string
  method?: string
  url_template?: string
  headers?: Record<string, string>
  parameter_schema?: Record<string, any>
  is_active?: boolean
  group_name?: string | null
}

/** 保存前试跑的请求体：草稿（不落库），测试值只用于这一次请求 */
export interface SysApiToolPreviewPayload {
  method: string
  url_template: string
  headers?: Record<string, string>
  parameter_schema?: Record<string, any>
  params: Record<string, any>
}

export type PreviewErrorKind =
  | 'http_error'
  | 'connection'
  | 'timeout'
  | 'invalid_url'
  | 'unsupported_method'
  | 'missing_param'
  | 'missing_user_variable'

export interface SysApiToolPreviewResult {
  ok: boolean
  status_code?: number | null
  elapsed_ms: number
  body: string
  body_truncated: boolean
  error_kind?: PreviewErrorKind | null
  message: string
}

export const toolApi = {
  list: () => {
    return axios.get<SysApiTool[]>('/api/portal/tools')
  },
  
  create: (data: SysApiToolCreate) => {
    return axios.post<SysApiTool>('/api/portal/tools', data)
  },
  
  update: (id: string, data: SysApiToolUpdate) => {
    return axios.put<SysApiTool>(`/api/portal/tools/${id}`, data)
  },
  
  delete: (id: string) => {
    return axios.delete(`/api/portal/tools/${id}`)
  },

  /**
   * 批量设置业务分组（注册表页多选后一次改完）。
   * groupName 传 null/空串表示清除分组，回到「未分组」。
   */
  batchSetGroup: (ids: string[], groupName: string | null) => {
    return axios.post<{ updated: number; group_name: string | null }>(
      '/api/portal/tools/batch-group',
      { ids, group_name: groupName },
    )
  },

  /**
   * 保存前试跑一次。
   *
   * 提交的是**草稿**（新建、编辑、改完未保存都走它），服务端不落库、不接收 id；
   * 服务端走的是与智能体运行时**完全相同**的请求内核，所以这里的结论对运行时有效。
   */
  preview: (data: SysApiToolPreviewPayload) => {
    return axios.post<SysApiToolPreviewResult>('/api/portal/tools/preview', data)
  }
}
