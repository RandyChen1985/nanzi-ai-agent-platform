import axios from '../utils/axios'
import type { StandardResponse } from './common'

export type AgentType = 'GENERAL' | 'CHATBI' | 'KNOWLEDGE_BASE'

export interface AIAgent {
  id: string
  name: string
  display_name: string
  description: string
  avatar_url?: string
  capabilities?: string[]
  agent_type: AgentType
  is_system: boolean
  is_enabled: boolean
  created_by?: string
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
  /** 已发布版本：自定义 Skills 数；skills_custom=false 时为 null（表示全部） */
  skill_count?: number | null
  skills_custom?: boolean | null
  /** 显式绑定的元数据集数；未绑定（走全局）为 null */
  metadata_dataset_count?: number | null
  /** 显式绑定的知识库数；未绑定（走全局）为 null */
  knowledge_base_count?: number | null
  readiness_ready?: boolean
  readiness_missing?: string[]
  onboarding_step?: 'VERSION' | 'RESOURCE' | 'COMPLETE'
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

export interface AIAgentVersion {
  id: string
  agent_id: string
  version_number: number
  model_name: string
  temperature: number
  synthesis_model_name?: string
  synthesis_temperature?: number
  system_prompt: string
  tools: string[]
  skills_custom?: boolean
  skills?: string[]
  status: 'DRAFT' | 'PUBLISHED' | 'ARCHIVED'
  comment?: string
  created_at: string
}

export const agentApi = {
  // List all agents
  listAgents: () => axios.get<AIAgent[]>('/api/portal/agents/'),
  
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

  // Get agent execution history (Old portal API)
  getAgentExecutions: (agentId: string, limit: number = 50) => axios.get<AgentExecutionHistory[]>(`/api/portal/agents/${agentId}/executions`, { params: { limit } }),

  // Unified Chat History (New V1 API)
  getChatHistory: (params: { 
    page?: number, 
    page_size?: number, 
    agent_id?: string,
    conversation_id?: string,
    username?: string,
    keyword?: string, 
    status?: string,
    start_date?: string, 
    end_date?: string,
    group_by_conversation?: boolean,
  }) => axios.get<StandardResponse<AgentExecutionHistoryListResponse>>('/api/v1/chat/history', { params }),

  // Get chat trace logs
  getChatTrace: (traceId: string) => axios.get<StandardResponse<any>>(`/api/v1/chat/logs/${traceId}`)
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
}
