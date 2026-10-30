import axios from '@/utils/axios'

export interface SysApiTool {
  id: string
  name: string
  description?: string
  method: string
  url_template: string
  headers?: Record<string, string>
  parameter_schema?: Record<string, any>
  /** 请求签名：关闭时运行时完全忽略签名配置 */
  signing_enabled?: boolean
  signing_key_id?: string | null
  signing_algorithm?: string
  /** 只读状态：服务端是否已保存密钥；响应不会返回密钥本身 */
  signing_secret_configured?: boolean
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
  signing_enabled?: boolean
  signing_key_id?: string | null
  signing_algorithm?: string
  signing_secret?: string
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
  signing_enabled?: boolean
  signing_key_id?: string | null
  signing_algorithm?: string
  signing_secret?: string
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
  signing_enabled?: boolean
  signing_algorithm?: string
  signing_key_id?: string | null
  signing_secret?: string
  /** 已保存工具试跑时传 id，服务端据此读取已加密的签名密钥 */
  tool_id?: string | null
}

export type PreviewErrorKind =
  | 'http_error'
  | 'connection'
  | 'timeout'
  | 'invalid_url'
  | 'unsupported_method'
  | 'missing_param'
  | 'invalid_parameter_schema'
  | 'missing_user_variable'
  | 'invalid_signing_config'

export interface SysApiToolPreviewRequestInfo {
  method: string
  url: string
  headers: Record<string, string>
  query: Record<string, any>
  body: any
}

export interface SysApiToolPreviewResult {
  ok: boolean
  status_code?: number | null
  elapsed_ms: number
  body: string
  body_truncated: boolean
  response_headers?: Record<string, string>
  request?: SysApiToolPreviewRequestInfo | null
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

/* -------------------------------------------------------------------------- */
/* 脚本工具（Python 脚本，在会话沙箱内执行）                                    */
/* -------------------------------------------------------------------------- */

export type ScriptToolSource = 'builtin' | 'custom'

export interface ScriptTool {
  id: string
  name: string
  display_name?: string | null
  description?: string | null
  source: ScriptToolSource
  language: string
  /** builtin 未覆盖时由服务端用代码库源码填充；页面可编辑，保存后以覆盖版本为准 */
  code?: string | null
  parameter_schema?: Record<string, any>
  requirements: string[]
  timeout_seconds?: number | null
  is_active: boolean
  /** 内置脚本被本平台改过：代码库版本不再生效，页面标「已覆盖」 */
  is_overridden?: boolean
  /** 该行只来自代码库定义（迁移未执行）：没有库行可编辑，写操作应禁用 */
  is_fallback?: boolean
  group_name?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface ScriptToolPayload {
  name: string
  display_name?: string | null
  description?: string | null
  language?: string
  code: string
  parameter_schema?: Record<string, any>
  requirements?: string[]
  timeout_seconds?: number | null
  is_active?: boolean
  group_name?: string | null
}

/** 运行环境说明：local 策略下必须如实显示「本地工作区」而不是「沙箱」 */
export interface ScriptToolCapabilities {
  sandbox_policy: string
  sandbox_label: string
  sandbox_enabled: boolean
  notice: string
  /** e2b / ssh 暂无路径映射，脚本工具不可用 */
  script_tool_supported: boolean
}

export interface ScriptToolPreviewPayload {
  tool_id?: string | null
  code?: string
  language?: string
  parameter_schema?: Record<string, any>
  requirements?: string[]
  timeout_seconds?: number | null
  params: Record<string, any>
}

export interface ScriptToolPreviewResult {
  ok: boolean
  exit_code?: number | null
  stdout: string
  stderr: string
  duration_ms: number
  timed_out: boolean
  sandbox_policy?: string | null
  message: string
}

export const scriptToolApi = {
  list: () => axios.get<ScriptTool[]>('/api/portal/tools/script'),

  capabilities: () => axios.get<ScriptToolCapabilities>('/api/portal/tools/script/capabilities'),

  create: (data: ScriptToolPayload) => axios.post<ScriptTool>('/api/portal/tools/script', data),

  update: (id: string, data: Partial<ScriptToolPayload>) =>
    axios.put<ScriptTool>(`/api/portal/tools/script/${id}`, data),

  delete: (id: string) => axios.delete(`/api/portal/tools/script/${id}`),

  batchSetGroup: (ids: string[], groupName: string | null) =>
    axios.post<{ ok: boolean; updated: number }>('/api/portal/tools/script/batch-group', {
      ids,
      group_name: groupName,
    }),

  /** 恢复内置默认版本：清空本平台对该内置脚本的覆盖（代码/参数/依赖/超时） */
  resetToBuiltin: (id: string) => axios.post<ScriptTool>(`/api/portal/tools/script/${id}/reset-to-builtin`),

  /** 试运行：走与真实调用相同的执行器，但落在预览目录、不写库 */
  preview: (data: ScriptToolPreviewPayload) =>
    axios.post<ScriptToolPreviewResult>('/api/portal/tools/script/preview', data),
}
