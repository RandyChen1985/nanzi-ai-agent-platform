import axios from '@/utils/axios'

export type ReasoningEffort = 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh'

export interface AIModel {
  id: string
  name: string
  model_id: string
  provider: string
  type: string
  api_base_url?: string
  context_size?: number | null
  max_output_tokens?: number | null
  temperature?: number | null
  thinking_enable: boolean
  thinking_only: boolean
  allow_disable_thinking: boolean
  reasoning_effort: ReasoningEffort | null
  supported_reasoning_efforts: ReasoningEffort[]
  has_api_key?: boolean
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface AIModelCreate {
  name: string
  model_id: string
  provider: string
  type: string
  api_base_url?: string
  context_size?: number | null
  max_output_tokens?: number | null
  temperature?: number | null
  thinking_enable?: boolean
  thinking_only?: boolean
  allow_disable_thinking?: boolean
  reasoning_effort?: ReasoningEffort | null
  supported_reasoning_efforts?: ReasoningEffort[]
  api_key?: string
  is_active?: boolean
}

export interface AIModelUpdate {
  name?: string
  model_id?: string
  provider?: string
  type?: string
  api_base_url?: string
  context_size?: number | null
  max_output_tokens?: number | null
  temperature?: number | null
  thinking_enable?: boolean
  thinking_only?: boolean
  allow_disable_thinking?: boolean
  reasoning_effort?: ReasoningEffort | null
  supported_reasoning_efforts?: ReasoningEffort[]
  api_key?: string
  is_active?: boolean
}

export interface AIModelDiscoverRequest {
  provider: string
  api_base_url?: string
  api_key?: string
  model_config_id?: string
}

export interface AIModelOption {
  model_id: string
  name: string
}

/** 一次供应商调用的单步耗时，用于在配置弹窗里渲染「测试过程」。 */
export interface ModelDiagnosticStep {
  name: string
  status: string
  detail?: string | null
  elapsed_ms?: number | null
}

export interface AIModelDiscoverResponse {
  models: AIModelOption[]
  steps: ModelDiagnosticStep[]
}

export interface AIModelTestResponse {
  status: string
  message: string
  response?: string
  steps?: ModelDiagnosticStep[]
}

export interface AIModelReference {
  kind: string
  key: string
  label: string
  detail: string
  config_key?: string
  agent_id?: string
  agent_name?: string
  version_id?: string
  version_number?: number
  version_status?: string
  agent_enabled?: boolean
}

export interface AIModelTestRequest {
  provider: string
  type: string
  model_id: string
  api_base_url?: string | null
  api_key?: string | null
  context_size?: number | null
  max_output_tokens?: number | null
  temperature?: number | null
  model_config_id?: string
}

export const modelApi = {
  list: (type?: string, includeInactive = false) => {
    return axios.get<AIModel[]>('/api/portal/models', { params: { type, include_inactive: includeInactive } })
  },

  /** 聊天输入框专用：后端按当前用户的模型白名单收窄（未配置白名单 = 全量）。 */
  listAvailableForChat: () => {
    return axios.get<AIModel[]>('/api/portal/models', { params: { scope: 'chat' } })
  },
  
  create: (data: AIModelCreate) => {
    return axios.post<AIModel>('/api/portal/models', data)
  },
  
  update: (id: string, data: AIModelUpdate) => {
    return axios.put<AIModel>(`/api/portal/models/${id}`, data)
  },
  
  delete: (id: string) => {
    return axios.delete(`/api/portal/models/${id}`)
  },

  references: (id: string) => {
    return axios.get<AIModelReference[]>(`/api/portal/models/${id}/references`)
  },

  testConnection: (id: string) => {
    return axios.post<AIModelTestResponse>(`/api/portal/models/${id}/test`)
  },

  testConfig: (data: AIModelTestRequest) => {
    return axios.post<AIModelTestResponse>('/api/portal/models/test-config', data)
  },

  discover: (data: AIModelDiscoverRequest) => {
    return axios.post<AIModelDiscoverResponse>('/api/portal/models/discover', data)
  }
}
