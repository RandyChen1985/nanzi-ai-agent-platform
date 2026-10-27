/**
 * 资源广场与资源访问申请的类型。
 *
 * 两个容易混淆的名字，后端也是这么用的：
 * - `dataset`   = 知识库（RAGFlow Dataset）
 * - `metadata`  = 数据集（meta_datasets）
 * 所以「申请一个知识库」传的是 `resource_type: 'dataset'`，别被字面骗了。
 */

export type ResourceAccessStatus = 'pending' | 'approved' | 'rejected' | 'cancelled'
export type ResourceType = 'dataset' | 'metadata'
export type SquareTab = 'all' | 'kb' | 'ds'
export type SquareVisibility = 'all' | 'public' | 'team'
export type SquareStateFilter = 'all' | 'apply' | 'pending' | 'has'

export interface SquareKnowledgeItem {
  dataset_id: string
  name: string
  description: string | null
  visibility: 'public' | 'team'
  owner: string | null
  owner_dept_id: number | null
  tags: string[]
  /** 引擎侧统计；RAGFlow 读不到时为 null，前端显示「—」而不是 0 */
  doc_count: number | null
  chunk_count: number | null
  updated_at: string | null
  has_access: boolean
  request_status: ResourceAccessStatus | null
  request_id: number | null
}

export interface SquareDatasetItem {
  id: number
  name: string
  display_name: string
  description: string | null
  data_source: string | null
  tags: string[]
  quality_score: number | null
  table_count: number
  metric_count: number
  updated_at: string | null
  has_access: boolean
  request_status: ResourceAccessStatus | null
  request_id: number | null
}

export interface SquareSummary {
  apply: number
  pending: number
  has: number
}

export interface SquarePage<T> {
  items: T[]
  total: number
  page: number
  size: number
  summary: SquareSummary
}

export interface SquareKnowledgePage extends SquarePage<SquareKnowledgeItem> {
  /** 知识库规模来自 RAGFlow 缓存，读不到时后端置 true */
  stale: boolean
}

export interface SquareDatasetPage extends SquarePage<SquareDatasetItem> {
  /** 系统配置关掉数据集目录时为 false */
  enabled: boolean
}

export interface SquareApplyPayload {
  resource_type: 'dataset' | 'metadata'
  resource_id: string
  reason?: string
}

export interface AccessRequestItem {
  id: number
  resource_type: ResourceType
  resource_id: string
  resource_name: string | null
  applicant_user_id: number
  applicant_user_name: string | null
  reason: string | null
  status: ResourceAccessStatus
  reviewer_user_name: string | null
  review_comment: string | null
  created_at: string | null
  reviewed_at: string | null
  /** 资源当前是否还存在；审批时要拦住「批了也打不开」的申请 */
  resource_exists?: boolean
  resource_enabled?: boolean
  visibility?: string | null
  owner?: string | null
  data_source?: string | null
  table_count?: number
  metric_count?: number
  quality_score?: number | null
  doc_count?: number | null
  chunk_count?: number | null
}

export interface KnowledgeReadonlyDocument {
  id: string
  name: string
  file_type: string | null
  size: number | null
  chunk_count: number | null
  run_status: string | null
  updated_at: string | null
}

export interface KnowledgeReadonly {
  dataset_id: string
  name: string
  description: string | null
  visibility: string
  owner: string | null
  tags: string[]
  updated_at: string | null
  documents: KnowledgeReadonlyDocument[]
  /** 引擎读不到文档列表时为 true：此时 documents 为空不代表这个库是空的 */
  documents_error?: boolean
  page: number
  size: number
}

export interface DatasetReadonly {
  id: number
  name: string
  display_name: string
  description: string | null
  data_source: string | null
  tags: string[]
  quality_score: number | null
  updated_at: string | null
  relationship_count: number
  /** 只读边界：只有表级信息，不含字段明细与样例值 */
  tables: Array<{
    physical_name: string
    term: string
    field_count: number
    description: string | null
  }>
  metrics: Array<{
    name: string
    display_name: string | null
    description: string | null
    unit: string | null
  }>
}
