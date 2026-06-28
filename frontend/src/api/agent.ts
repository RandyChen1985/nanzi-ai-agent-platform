import axios from '../utils/axios'
import type { StandardResponse } from './common'

export interface AIAgent {
  id: string
  name: string
  display_name: string
  description: string
  avatar_url?: string
  capabilities?: string[]
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
}

export interface AIAgentBase {
  name: string
  display_name: string
  description: string
  avatar_url?: string
  capabilities?: string[]
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
  status: 'DRAFT' | 'PUBLISHED' | 'ARCHIVED'
  comment?: string
  created_at: string
}

export const agentApi = {
  // List all agents
  listAgents: () => axios.get<AIAgent[]>('/api/portal/agents/'),
  
  // Create agent
  createAgent: (data: Partial<AIAgent>) => axios.post<AIAgent>('/api/portal/agents/', data),
  
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
    username?: string,
    keyword?: string, 
    status?: string,
    start_date?: string, 
    end_date?: string 
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
  username?: string
  query?: string
  summary?: string
  status: string
  agent_version?: string
  model_id?: string
  execution_time_ms?: number
  created_at: string
}
