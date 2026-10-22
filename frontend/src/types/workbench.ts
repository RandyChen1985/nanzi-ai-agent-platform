export type WorkbenchMode = "active" | "quiet" | "new_user"
export type WorkbenchSourceState = "ok" | "empty" | "error"

export interface WorkbenchTarget {
  task_id?: number | string
  run_id?: number | string
  report_id?: string
  conversation_id?: string
  agent_id?: string
  scenario_id?: string
  trace_id?: string
}

export interface WorkbenchItem {
  id: string
  business_key?: string
  type: string
  title: string
  subtitle?: string
  occurred_at?: string
  next_run_at?: string
  status?: string
  severity?: string
  execution_time_ms?: number | null
  action: string
  source?: string
  target: WorkbenchTarget
}

export interface WorkbenchAgent {
  id: string
  name: string
  description?: string
  /** 智能体自己的头像；空字符串表示未配置，卡片回退到名称首字 */
  avatar_url?: string
  execution_count?: number
  action: "open_agent"
  target: WorkbenchTarget
}

export interface WorkbenchScenario {
  id: string
  name: string
  description?: string
  category?: string
  recommended?: boolean
  available: boolean
  action: "open_scenario"
  target: WorkbenchTarget
}

export interface WorkbenchPersonalResource {
  key: string
  label: string
  value: number
  unit: string
  tab: string
  status: WorkbenchSourceState
}

export interface WorkbenchHomePayload {
  mode: WorkbenchMode
  attention: WorkbenchItem[]
  latest_results: WorkbenchItem[]
  resume_items: WorkbenchItem[]
  recent_tasks: WorkbenchItem[]
  favorite_agents: WorkbenchAgent[]
  recommended_scenarios: WorkbenchScenario[]
  running_items: WorkbenchItem[]
  next_scheduled_item: WorkbenchItem | null
  personal_resources: WorkbenchPersonalResource[]
  source_status: Record<string, WorkbenchSourceState>
  generated_at: string
}
