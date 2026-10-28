import axios from '../utils/axios'
import type { StandardResponse, ListResponse } from './common'

export interface User {
  id: number
  user_name: string
  real_name?: string
  email?: string
  role: string
  dept_code?: string
  org_path?: string
  extra_data?: string | Record<string, unknown> | null
  is_active: boolean
}

// 个人中心「消息通知」中邮件渠道的配置。
// smtp_source：global=走平台统一邮件服务（默认），custom=使用个人自定义 SMTP。
// include_self：是否同时发送到用户自己的邮箱；存标志而非邮箱快照。
export interface NotificationEmailConfig {
  is_enabled: boolean
  smtp_source?: 'global' | 'custom'
  include_self?: boolean
  smtp_host?: string
  smtp_port?: number
  smtp_user?: string
  smtp_password?: string
  sender_name?: string
  recipients?: string
}

export interface Role {
  id: number
  code: string
  name: string
  description?: string
  user_count: number
}

export interface ScenarioTemplateResourceRequirement {
  type: string
  name: string
  required: boolean
  description?: string
}

export interface ScenarioTemplateSummary {
  id: string
  name: string
  category: string
  description: string
  tags: string[]
  recommended: boolean
  target_departments: string[]
  delivery_time?: string
  maturity?: string
  included_capabilities: string[]
  deliverables: string[]
  business_goals: string[]
  install_steps: string[]
  acceptance_criteria: string[]
  required_resources: ScenarioTemplateResourceRequirement[]
  sample_questions: string[]
}

export interface ScenarioTemplateDetail extends ScenarioTemplateSummary {
  manifest: Record<string, unknown>
}

export interface ScenarioTemplateResourceOption {
  id: string
  name: string
  label: string
  description?: string
  status?: string
  meta: Record<string, unknown>
}

export interface ScenarioTemplateResourceOptions {
  template_id: string
  options: Record<string, ScenarioTemplateResourceOption[]>
}

export interface ScenarioTemplatePrecheck {
  template_id: string
  target_agent_name: string
  can_install: boolean
  checks: Array<{
    key: string
    label: string
    status: 'success' | 'warning' | 'error'
    message: string
  }>
}

export interface ScenarioTemplateInstallResult {
  template_id: string
  created: boolean
  instance: {
    id: string
    status: string
    template_name: string
    owner?: string
  }
  run: {
    id: string
    status: string
  }
  agent: {
    id: string
    name: string
    display_name: string
    description?: string
  }
  version: {
    id: string
    version_number: number
    status: string
  }
  resource_bindings: Record<string, unknown>
  missing_resources: ScenarioTemplateResourceRequirement[]
  next_steps: string[]
  enabled_tools: string[]
  sample_questions: string[]
  resource_summary: Array<{
    type: string
    label: string
    count: number
    ids: string[]
    names: string[]
  }>
}

export interface ScenarioTemplateInstanceSummary {
  id: string
  template_id: string
  template_name: string
  status: string
  owner?: string
  agent: {
    id: string
    name: string
    display_name: string
  }
  latest_run?: {
    id: string
    status: string
    version_id?: string
  } | null
  resource_summary: Array<{
    type: string
    label: string
    count: number
    ids: string[]
    names: string[]
  }>
  acceptance_criteria: string[]
  sample_questions: string[]
  next_steps: string[]
}

export interface ScenarioTemplateInstallPayload {
  instance_name?: string
  display_name?: string
  description?: string
  resource_bindings?: Record<string, unknown>
  publish?: boolean
}

export const portalApi = {
  // 我的头像（当前登录用户）
  //
  // 上传走 multipart，字段名固定为 `file`；**不手写请求头**，交给浏览器自动生成
  // 带 boundary 的请求头，否则后端解析不出文件。成功返回可直接用作 img src 的
  // 头像短路径（形如 /static/uploads/avatars/<user_id>/avatar_<ts>.png）。
  uploadMyAvatar: async (file: Blob, fileName = 'avatar.png'): Promise<string | null> => {
    const formData = new FormData()
    formData.append('file', new File([file], fileName, { type: file.type || 'image/png' }))
    const response = await axios.post('/api/portal/auth/me/avatar', formData)
    // 兼容两种包裹：门户接口可能是 { status, data: { avatar_url } }，
    // 也可能按契约直接返回 { avatar_url }。取不到就返回 null，由调用方兜底提示。
    const payload = response.data?.data ?? response.data
    const avatarUrl = payload?.avatar_url
    return typeof avatarUrl === 'string' && avatarUrl.trim() ? avatarUrl : null
  },

  // 删除我的自定义头像（后端幂等，重复删除同样成功）
  deleteMyAvatar: async (): Promise<void> => {
    await axios.delete('/api/portal/auth/me/avatar')
  },

  // Roles
  getRoles: (params?: { search?: string, page?: number, size?: number }) => 
    axios.get<StandardResponse<ListResponse<Role>>>('/api/portal/roles', { params }),
  
  // Users
  getUsers: (params?: { search?: string, page?: number, size?: number }) => 
    axios.get<StandardResponse<ListResponse<User>>>('/api/portal/management/users', { params }),

  // SSO Users (if needed for selection)
  getSsoUsers: (params?: { search?: string, page?: number, size?: number }) => 
    axios.get<StandardResponse<ListResponse<User>>>('/api/portal/management/sso-users', { params }),

  // Scenario Templates
  getScenarioTemplates: () =>
    axios.get<StandardResponse<ScenarioTemplateSummary[]>>('/api/portal/scenario-templates'),

  getScenarioTemplateInstances: () =>
    axios.get<StandardResponse<ScenarioTemplateInstanceSummary[]>>('/api/portal/scenario-templates/instances'),

  getScenarioTemplateInstance: (instanceId: string) =>
    axios.get<StandardResponse<ScenarioTemplateInstallResult>>(`/api/portal/scenario-templates/instances/${instanceId}`),

  getScenarioTemplate: (templateId: string) =>
    axios.get<StandardResponse<ScenarioTemplateDetail>>(`/api/portal/scenario-templates/${templateId}`),

  getScenarioTemplateResourceOptions: (templateId: string) =>
    axios.get<StandardResponse<ScenarioTemplateResourceOptions>>(`/api/portal/scenario-templates/${templateId}/resource-options`),

  precheckScenarioTemplate: (templateId: string, payload: ScenarioTemplateInstallPayload) =>
    axios.post<StandardResponse<ScenarioTemplatePrecheck>>(`/api/portal/scenario-templates/${templateId}/precheck`, payload),

  installScenarioTemplate: (templateId: string, payload: ScenarioTemplateInstallPayload) =>
    axios.post<StandardResponse<ScenarioTemplateInstallResult>>(`/api/portal/scenario-templates/${templateId}/install`, payload)
}

// 头像两个方法额外做**具名导出**：`portalApi.xxx` 这种对象属性无法被
// `import { uploadMyAvatar } from '@/api/portal'` 取到（rollup 会直接报
// "is not exported by"），个人中心/聊天页用具名导入更直观，两条路都留着。
export const uploadMyAvatar = portalApi.uploadMyAvatar
export const deleteMyAvatar = portalApi.deleteMyAvatar
