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
  }
}
