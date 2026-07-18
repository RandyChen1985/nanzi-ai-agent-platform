import axios from '../utils/axios'
import type { StandardResponse, ListResponse } from './common'

export interface User {
  id: number
  user_name: string
  real_name?: string
  role: string
  dept_code?: string
  org_path?: string
  extra_data?: string | Record<string, unknown> | null
  is_active: boolean
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
