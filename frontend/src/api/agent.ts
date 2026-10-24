import axios from '../utils/axios'
import type { StandardResponse } from './common'
import type { ProcessTimelineItem } from '../utils/processTimeline'

export type AgentType = 'GENERAL' | 'CHATBI' | 'KNOWLEDGE_BASE'

/**
 * `/chat/history` 与 `/chat/admin/history` 共用的查询参数。
 *
 * 两个端点签名一致：`username` 只在审计端点上有意义（自隔离端点会忽略它）。
 */
export interface ChatHistoryQueryParams {
  page?: number
  page_size?: number
  agent_id?: string
  conversation_id?: string
  username?: string
  keyword?: string
  status?: string
  start_date?: string
  end_date?: string
  group_by_conversation?: boolean
}

/** 模型配置异常原因：missing | unregistered | disabled | missing_api_key */
export interface AgentModelIssue {
  code: string
  message: string
  model_name?: string
}

/** 已发布版本里运行时已失效的工具引用摘要（列表页配置异常横幅用） */
export interface AgentToolIssue {
  code: string
  message: string
  count: number
  names: string[]
  version_id: string
}

export interface AIAgent {
  id: string
  name: string
  display_name: string
  description: string
  avatar_url?: string
  capabilities?: string[]
  agent_type: AgentType
  is_system: boolean
  /** 系统级流程状态：PENDING_REVIEW 表示正在申请审核中，内容对所有者为只读 */
  system_status?: 'NONE' | 'PENDING_REVIEW'
  system_pending_request_id?: string | null
  /** 待审申请单的提交时间，卡片冻结条显示「等了多久」 */
  system_requested_at?: string | null
  is_enabled: boolean
  created_by?: string
  /** 版本内容可编辑（系统级所有者为 true，能建版本、改草稿、存草稿） */
  can_edit_versions?: boolean
  /** 能否发布草稿上线（系统级对非管理员恒为 false） */
  can_publish_version?: boolean
  /** 创建者显示名（real_name 优先），管理员辨认归属用 */
  created_by_name?: string
  owner_group?: string
  is_editable?: boolean
  engine_type?: 'LOCAL' | 'RAGFLOW' | 'OPENCLAW'
  engine_config?: any
  sort_order?: number
  created_at: string
  updated_at: string
  execution_count?: number
  /** 已发布版本：静态/API 工具数；无发布版或非 LOCAL 为 null */
  tool_count?: number | null
  /** 已发布版本：MCP 工具数 */
  mcp_count?: number | null
  /** 已发布版本：Skills 数；skills_custom=false 时为启用中的公共技能总数 */
  skill_count?: number | null
  skills_custom?: boolean | null
  /** 显式绑定的元数据集数；未绑定（走全局）为 null */
  metadata_dataset_count?: number | null
  /** 显式绑定的知识库数；未绑定（走全局）为 null */
  knowledge_base_count?: number | null
  /** 线上版本号：卡片上要显示「现在生效的是哪一版」 */
  published_version_number?: number | null
  readiness_ready?: boolean
  readiness_missing?: string[]
  /**
   * 已发布版本绑定的模型在当前环境不可用的原因；null/undefined 表示正常。
   * code: missing | unregistered | disabled | missing_api_key
   */
  model_issue?: AgentModelIssue | null
  /**
   * 已发布版本里运行时已失效的工具/MCP 引用摘要；null/undefined 表示正常，
   * 或该智能体还没有已发布版本（由 readiness 覆盖）。
   */
  tool_issue?: AgentToolIssue | null
  onboarding_step?: 'VERSION' | 'RESOURCE' | 'COMPLETE'
  /** 已发布版本是否允许在输入框手动切换模型；null/undefined 表示无发布版或未配置，前端按允许处理 */
  allow_model_switch?: boolean | null
}

export interface AIAgentBase {
  name: string
  display_name: string
  description: string
  avatar_url?: string
  capabilities?: string[]
  agent_type: AgentType
  is_system?: boolean
  is_enabled?: boolean
  engine_type?: 'LOCAL' | 'RAGFLOW' | 'OPENCLAW'
  engine_config?: any
  sort_order?: number
}

export interface ToolConfigItem {
  name: string
  enabled?: boolean | null
  model_name?: string | null
  temperature?: number | null
  description_override?: string | null
  engine_config_override?: Record<string, unknown> | null
  metadata_dataset_ids?: string[] | null
}

export interface AIAgentVersion {
  id: string
  agent_id: string
  version_number: number
  model_name: string
  temperature: number
  synthesis_model_name?: string
  synthesis_temperature?: number
  /** 是否允许在输入框手动切换模型；历史版本未配置时按 true（允许）处理 */
  allow_model_switch?: boolean
  system_prompt: string
  tools: Array<string | ToolConfigItem>
  toolcall_timeout_seconds?: number | null
  skills_custom?: boolean
  skills?: string[]
  welcome_config?: WelcomeConfig
  status: 'DRAFT' | 'PUBLISHED' | 'ARCHIVED'
  comment?: string
  created_at: string
}

export interface WelcomeCard { icon: 'chart' | 'knowledge' | 'workspace' | 'report' | 'alert' | 'chat'; title: string; subtitle: string; prompt: string }
export interface WelcomeConfig { enabled: boolean; mode: 'manual' | 'ai'; generation_requirement: string; cards: WelcomeCard[] }

/**
 * 版本配置里「运行时已失效」的工具引用。
 * 服务被删/禁用、工具下线后，引用仍在 tools 数组里，但选择器不再渲染它，
 * 用户因此没有取消勾选的入口——需要这份诊断数据来提供解绑按钮。
 */
export interface StaleToolRef {
  name: string
  display_name: string
  server_name: string
  reason: 'server_deleted' | 'server_forbidden' | 'server_disabled' | 'tool_missing' | 'tool_removed_remote' | 'tool_unpublished' | 'unknown'
  reason_label: string
}

export interface VersionToolHealth {
  agent_id: string
  version_id: string
  stale_count: number
  stale_tools: StaleToolRef[]
}

export const agentApi = {
  // List all agents
  listAgents: () => axios.get<AIAgent[]>('/api/portal/agents/'),

  /**
   * 当前用户有权执行/对话的智能体（只要求登录态，不要求「智能体管理」菜单权限）。
   * 任务调度台等非管理页面必须用这个：普通用户若只有 menu:task_center，
   * /agents/ 会返回 403，导致智能体下拉永远为空。
   */
  listAllowedAgents: (params?: { includeDisabled?: boolean }) =>
    axios.get<AIAgent[]>('/api/portal/agents/allowed', {
      params: params?.includeDisabled ? { include_disabled: true } : undefined,
    }),

  // Read-only global tool-call timeout shown in the version editor
  getGlobalToolcallTimeout: () => axios.get<{ seconds: number }>('/api/portal/agents/toolcall-timeout'),

  /** 系统默认 LLM 模型的健康状态；default_model_issue 为 null 表示正常 */
  getModelHealth: () =>
    axios.get<{ default_model_issue: AgentModelIssue | null }>('/api/portal/agents/model-health'),
  
  // Create agent
  createAgent: (data: Partial<AIAgent>) => axios.post<AIAgent>('/api/portal/agents/', data),

  createAgentOnboarding: (data: Partial<AIAgent> & { onboarding_key: string }) =>
    axios.post<{
      agent: AIAgent
      version: AIAgentVersion
      onboarding_step: 'VERSION' | 'RESOURCE' | 'COMPLETE'
      template_fallback: boolean
    }>('/api/portal/agents/onboarding', data),
  
  // Update agent
  updateAgent: (id: string, data: Partial<AIAgent>) => axios.put<AIAgent>(`/api/portal/agents/${id}`, data),

  // Batch reorder agents (sort_order)
  reorderAgents: (items: { id: string; sort_order: number }[]) =>
    axios.post<{ status: string }>('/api/portal/agents/reorder', { items }),
  
  // Delete agent
  deleteAgent: (id: string) => axios.delete<void>(`/api/portal/agents/${id}`),

  // Duplicate agent (metadata + published version → an immediately usable copy)
  duplicateAgent: (id: string, data: { name: string; display_name: string }) =>
    axios.post<AIAgent>(`/api/portal/agents/${id}/duplicate`, data),
  
  // List versions
  listVersions: (agentId: string) => axios.get<AIAgentVersion[]>(`/api/portal/agents/${agentId}/versions`),
  
  // Create version
  createVersion: (agentId: string, data: Partial<AIAgentVersion>) => axios.post<AIAgentVersion>(`/api/portal/agents/${agentId}/versions`, data),
  
  // Update version (DRAFT only)
  updateVersion: (agentId: string, versionId: string, data: Partial<AIAgentVersion>) => axios.put<AIAgentVersion>(`/api/portal/agents/${agentId}/versions/${versionId}`, data),
  
  // Publish version
  publishVersion: (agentId: string, versionId: string) => axios.post(`/api/portal/agents/${agentId}/versions/${versionId}/publish`),
  
  // Delete version (DRAFT/ARCHIVED only)
  deleteVersion: (agentId: string, versionId: string) => axios.delete(`/api/portal/agents/${agentId}/versions/${versionId}`),

  /** 版本配置里已失效的工具引用（只读诊断，用于编辑器内解绑） */
  getVersionToolHealth: (agentId: string, versionId: string) =>
    axios.get<VersionToolHealth>(`/api/portal/agents/${agentId}/versions/${versionId}/tool-health`),

  /**
   * 诊断「还没有版本 id」的工具配置（新建、克隆）。
   * 克隆会把旧版本的失效引用整份复制过来，这时按版本查不了，只能把 tools 传上去。
   */
  checkToolsHealth: (agentId: string, tools: Array<string | ToolConfigItem>) =>
    axios.post<VersionToolHealth>(`/api/portal/agents/${agentId}/tool-health`, { tools }),

  // Get agent execution history (Old portal API)
  getAgentExecutions: (agentId: string, limit: number = 50) => axios.get<AgentExecutionHistory[]>(`/api/portal/agents/${agentId}/executions`, { params: { limit } }),

  // Unified Chat History (New V1 API)
  getChatHistory: (params: ChatHistoryQueryParams) =>
    axios.get<StandardResponse<AgentExecutionHistoryListResponse>>('/api/v1/chat/history', { params }),

  // 聊天日志审计（admin 专用）：跨用户查询会话历史。非 admin 调用一律 403。
  // 与上面的自隔离端点共用同一实现，只是不按当前用户过滤。
  getAdminChatHistory: (params: ChatHistoryQueryParams) =>
    axios.get<StandardResponse<AgentExecutionHistoryListResponse>>('/api/v1/chat/admin/history', { params }),

  // Get chat trace logs
  getChatTrace: (traceId: string) => axios.get<StandardResponse<any>>(`/api/v1/chat/logs/${traceId}`),

  // 聊天日志审计（admin 专用）：查看任意用户单次对话的执行链路。
  getAdminChatTrace: (traceId: string) =>
    axios.get<StandardResponse<any>>(`/api/v1/chat/admin/logs/${traceId}`),

  // Get context compaction timeline for one conversation
  getContextCompactions: (conversationId: string, config?: { headers?: Record<string, string> }) =>
    axios.get<StandardResponse<ContextCompactionsResponse>>(
      `/api/v1/chat/conversation/${encodeURIComponent(conversationId)}/context_compactions`,
      config,
    ),

  // 聊天日志审计（admin 专用）：按会话归属人读取上下文压缩时间线。
  getAdminContextCompactions: (conversationId: string, config?: { headers?: Record<string, string> }) =>
    axios.get<StandardResponse<ContextCompactionsResponse>>(
      `/api/v1/chat/admin/conversation/${encodeURIComponent(conversationId)}/context_compactions`,
      config,
    ),

  manualContextCompaction: (conversationId: string, retainRatio: 0.25 | 0.5 | 0.75, mode: 'fast' | 'smart' = 'fast', config?: { headers?: Record<string, string> }) =>
    axios.post<StandardResponse<Record<string, unknown>>>(
      `/api/v1/chat/conversation/${encodeURIComponent(conversationId)}/context_compactions/manual`,
      { retain_ratio: retainRatio, mode },
      config,
    ),
}

export interface AgentExecutionHistoryListResponse {
  total: number
  page: number
  page_size: number
  items: AgentExecutionHistory[]
}

export interface AgentExecutionHistory {
  id: number
  trace_id: string
  agent_id: string
  conversation_id?: string | null
  username?: string
  query?: string
  summary?: string
  status: string
  agent_version?: string
  model_id?: string
  execution_time_ms?: number
  created_at: string
  turn_count?: number
  agent_display_name?: string
  /**
   * 思考卡定稿快照（后端由 AgentExecutionHistoryResponse 自动带出）。
   * 主动提问轮的 summary 为空，提问卡只能从这个快照回放。
   */
  process_timeline?: ProcessTimelineItem[] | null
}

export interface ContextCompactionRecord {
  event_id: string
  conversation_id: string
  event_type: 'context_summarized' | 'context_compression' | string
  source: 'platform' | 'agentscope' | string
  stage: 'pre_route' | 'resolved_model' | 'agent_runtime' | string
  occurred_at: string
  title: string
  status: string
  preview: string
  trace_id?: string | null
  agent_name?: string | null
  model_name?: string | null
  dropped?: number | null
  kept?: number | null
  origin?: string | null
  token_used?: number | null
  token_budget?: number | null
  history_budget?: number | null
  physical_window?: number | null
  completion_reserve_tokens?: number | null
  request_input_budget?: number | null
  overhead_reservation_tokens?: number | null
  prompt_overhead_reservation_tokens?: number | null
  summary_chars?: number | null
  saved_tokens?: number | null
  saved_percent?: number | null
}

export interface ContextCompactionsResponse {
  records: ContextCompactionRecord[]
  count: number
  retention_seconds: number
}

// ---------------------------------------------------------------------------
// 系统级发布（A 期）：申请 → 审核 → 上架 → 下架，以及发布历史
//
// 「发布为系统级」只改 is_system 状态，不选版本、不改任何版本状态——
// 运行时用哪个版本完全由既有的「发布版本」流程决定。
// ---------------------------------------------------------------------------

export interface AgentSystemRequestItem {
  id: string
  agent_id: string
  agent_name: string
  agent_identifier: string
  engine_type?: string | null
  status: string
  /** SYSTEM_PUBLISH=申请发布为系统级；VERSION_RELEASE=申请把某版本发布上线 */
  request_type?: 'SYSTEM_PUBLISH' | 'VERSION_RELEASE'
  /** VERSION_RELEASE 时指出要上线的是哪一版 */
  version_number?: number | null
  request_comment?: string | null
  requested_by?: number | null
  /** 审核队列要显示「谁提交的」 */
  requested_by_name?: string | null
  requested_at: string
  waiting_hours: number
}

export interface AgentSystemRequestDetail extends AgentSystemRequestItem {
  snapshot?: Record<string, any> | null
  requested_by_name?: string | null
  reviewed_by?: number | null
  reviewed_by_name?: string | null
  reviewed_at?: string | null
  review_comment?: string | null
}

export interface AgentSystemHistoryEntry {
  kind: 'request' | 'event'
  id: string
  at?: string | null
  /** at 对已审完的申请取审核时间，提交时间另存以便同时展示 */
  requested_at?: string | null
  reviewed_at?: string | null
  status?: string | null
  action?: string | null
  actor_name?: string | null
  comment?: string | null
  review_comment?: string | null
  reason?: string | null
  version_number?: number | null
  affected_user_count?: number | null
  notified?: boolean | null
  snapshot?: Record<string, any> | null
}

export interface AgentSystemHistoryResponse {
  total: number
  items: AgentSystemHistoryEntry[]
}

export interface AgentSystemUnpublishPreview {
  agent_id: string
  affected_user_count: number
  sample_user_names: string[]
}

export const agentSystemApi = {
  submitRequest: (agentId: string, data: { comment?: string } = {}) =>
    axios.post<{ request_id: string; status: string }>(
      `/api/portal/agents/${agentId}/system/requests`,
      data,
    ),

  cancelRequest: (agentId: string) =>
    axios.delete<{ request_id: string; status: string }>(
      `/api/portal/agents/${agentId}/system/requests`,
    ),

  /** 申请把某个草稿版本发布上线（系统级智能体的线上基线只能由管理员变更）。 */
  submitReleaseRequest: (agentId: string, versionId: string, data: { comment?: string } = {}) =>
    axios.post<{ request_id: string; status: string }>(
      `/api/portal/agents/${agentId}/system/release-requests`,
      { version_id: versionId, ...data },
    ),

  listRequests: (params: { status?: string; page?: number; page_size?: number } = {}) =>
    axios.get<AgentSystemRequestItem[]>('/api/portal/agents/system/requests', { params }),

  countRequests: () =>
    axios.get<{ count: number }>('/api/portal/agents/system/requests/count'),

  getRequest: (requestId: string) =>
    axios.get<AgentSystemRequestDetail>(
      `/api/portal/agents/system/requests/${requestId}`,
    ),

  approveRequest: (requestId: string, data: { comment?: string } = {}) =>
    axios.post<{ agent_id: string; is_system: boolean; message: string }>(
      `/api/portal/agents/system/requests/${requestId}/approve`,
      data,
    ),

  rejectRequest: (requestId: string, data: { comment: string }) =>
    axios.post<{ request_id: string; status: string }>(
      `/api/portal/agents/system/requests/${requestId}/reject`,
      data,
    ),

  publish: (agentId: string, data: { reason?: string; notify_restore?: boolean } = {}) =>
    axios.post<AIAgent>(`/api/portal/agents/${agentId}/system/publish`, data),

  previewUnpublish: (agentId: string) =>
    axios.get<AgentSystemUnpublishPreview>(
      `/api/portal/agents/${agentId}/system/unpublish-preview`,
    ),

  unpublish: (agentId: string, data: { reason: string; notify: boolean }) =>
    axios.post<{
      agent_id: string
      affected_user_count: number
      notified_user_count: number
    }>(`/api/portal/agents/${agentId}/system/unpublish`, data),

  history: (agentId: string, params: { page?: number; page_size?: number } = {}) =>
    axios.get<AgentSystemHistoryResponse>(
      `/api/portal/agents/${agentId}/system/history`,
      { params },
    ),
}
