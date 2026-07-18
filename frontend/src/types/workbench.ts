export type WorkbenchMode = "active" | "quiet" | "new_user"
export type WorkbenchSourceState = "ok" | "empty" | "error"

export interface WorkbenchTarget {
  task_id?: number | string
  run_id?: number | string
  report_id?: string
  conversation_id?: string
  agent_id?: string
  scenario_id?: string
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
  action: string
  target: WorkbenchTarget
}

export interface WorkbenchAgent {
  id: string
  name: string
  description?: string
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

export interface WorkbenchHomePayload {
  mode: WorkbenchMode
  attention: WorkbenchItem[]
  latest_results: WorkbenchItem[]
  resume_items: WorkbenchItem[]
  favorite_agents: WorkbenchAgent[]
  recommended_scenarios: WorkbenchScenario[]
  next_scheduled_item: WorkbenchItem | null
  source_status: Record<string, WorkbenchSourceState>
  generated_at: string
}
