import axios from "../utils/axios";

const API_BASE = "/api/portal/metadata";

export interface DbConnectionConfig {
  id: number;
  name: string;
  db_type: string;
  host: string;
  port: number;
  db_user: string;
  password: string;
  database_name: string;
  description?: string;
  created_by: number;
  created_at: string;
  updated_at?: string;
}

export interface Dataset {
  id: number;
  name: string;
  display_name: string;
  description?: string;
  tags: string[];
  data_source: string;
  status?: number;
  enable_data_perm?: boolean;
  row_filter_config?: any;
  created_at: string;
  updated_at?: string;
  rag_dataset_id?: string;
  rag_synced_at?: string;
  rag_sync_status?: number; // 0:None, 1:Syncing, 2:Synced, -1:Failed
  rag_sync_notes?: string;
  table_count?: number;
  metric_count?: number;
  relationship_count?: number;
  tables?: Table[];
}

export interface MetadataSyncStartResponse {
  code: number;
  message: string;
  data?: {
    rag_sync_status: number;
    task_id: string;
  };
}

export interface Column {
  id?: number;
  physical_name: string;
  term: string;
  type: string;
  description?: string;
  enums?: any[];
  synonyms?: string[];
  is_primary?: boolean;
}

export interface Table {
  id?: number;
  physical_name: string;
  term: string;
  description?: string;
  columns: Column[];
}

export interface Metric {
  id?: number;
  name: string;
  display_name: string;
  description?: string;
  calculation_logic: string;
  unit?: string;
  tags?: string[];
}

export interface Relationship {
  id?: number;
  source_table_id: number;
  target_table_id: number;
  join_condition: string;
  join_type: string;
  description?: string;
  // Optional resolved names for UI convenience if backed by views matches
  source_table_name?: string;
  target_table_name?: string;
}

// 跨数据集 all-tables 接口返回类型
export interface AllTablesColumn {
  physical_name: string;
  term: string;
}

export interface AllTablesTable {
  id: number;
  physical_name: string;
  term: string;
  columns?: AllTablesColumn[];
}

export interface AllTablesDataset {
  dataset_id: number;
  dataset_name: string;
  display_name: string;
  tables: AllTablesTable[];
}

// 智能发现关系 - 推荐结果类型（仅预览，不入库）
export type RelationshipRelationType =
  | "one_to_one"
  | "one_to_many"
  | "many_to_one";

export type RelationshipRecommendationStrategy = "strict" | "smart";

export interface RelationshipRecommendation {
  source_table: string; // 源表物理名
  target_table: string; // 目标表物理名
  condition: string; // JOIN 表达式，如 't1.a = t2.b'
  relation_type: RelationshipRelationType;
  description: string; // 业务含义描述
  confidence: number; // 置信度 0~1
  source?: string; // 推荐来源标识，如 'AI'
}

export interface RelationshipRecommendationResult {
  relationships: RelationshipRecommendation[];
  _trace_id?: string;
  _batch_count?: number;
  _stop_reason?: string;
  _debug?: {
    strategy?: RelationshipRecommendationStrategy;
    schema_len?: number;
    schema_table_count?: number;
    schema_table_names_preview?: string[];
    possible_pair_count?: number;
    candidate_pair_count?: number;
    smart_candidate_pair_count?: number;
    candidate_pair_limit?: number;
    truncated_pair_count?: number;
    filtered_pair_count?: number;
    candidate_group_count?: number;
    completed_group_count?: number;
    failed_group_count?: number;
    remaining_group_count?: number;
    completed_pair_count?: number;
    remaining_pair_count?: number;
    fk_relationship_count?: number;
    probed_pair_count?: number;
    confirmed_pair_count?: number;
    unverified_pair_count?: number;
    rejected_reasons?: Record<string, number>;
    confirmed_duplicate_count?: number;
    confirmed_description_updated?: number;
    probe_duration_ms?: number;
    probe_unavailable_reason?: string | null;
    total_prompt_chars?: number;
    total_scoped_schema_chars?: number;
    batch_size?: number;
    batch_count?: number;
    stop_reason?: string;
    batches?: Array<Record<string, unknown>>;
    groups?: Array<Record<string, unknown>>;
  };
}

export type MetadataAiRunStatus =
  | "idle"
  | "running"
  | "completed"
  | "interrupted"
  | "error";

export interface MetadataAiProgress {
  status: MetadataAiRunStatus;
  recommendation_type?: "metrics" | "relationships";
  phase: string;
  message: string;
  percent: number;
  trace_id?: string;
  completed_units?: number;
  total_units?: number;
  remaining_units?: number;
  unit_label?: string;
  current_item?: string;
  current_page?: number; // 兼容旧版逐表推导批次；候选组模式固定为 0。
  batch_count?: number;
  result_count?: number;
  candidate_pair_count?: number;
  strategy?: RelationshipRecommendationStrategy;
  smart_candidate_pair_count?: number;
  candidate_pair_limit?: number;
  truncated_pair_count?: number;
  completed_pair_count?: number;
  remaining_pair_count?: number;
  estimated_remaining_seconds?: number;
  stop_reason?: string | null;
}

export interface MetricRecommendationResult {
  metrics: Metric[];
  _trace_id?: string;
}

interface MetadataAiStreamEvent<T> {
  event: "started" | "progress" | "completed" | "interrupted" | "error" | string;
  data: MetadataAiProgress & { result?: T };
}

const metadataStreamAuthHeaders = (): Record<string, string> => {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  const apiKey = localStorage.getItem("api_key");
  const token = localStorage.getItem("yovole_token") || localStorage.getItem("admin_token");
  if (apiKey) headers["X-API-Key"] = apiKey;
  // 与 Axios 拦截器保持一致：API Key 优先，只有缺少 API Key 时才使用 JWT。
  if (!apiKey && token) headers.Authorization = `Bearer ${token}`;
  return headers;
};

const parseMetadataSseBlock = <T>(block: string): MetadataAiStreamEvent<T> | null => {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (!dataLines.length) return null;
  try {
    return {
      event,
      data: JSON.parse(dataLines.join("\n")),
    } as MetadataAiStreamEvent<T>;
  } catch (error) {
    console.error("元数据 AI SSE 事件解析失败", { event, block, error });
    throw new Error("元数据 AI 进度事件格式无效");
  }
};

/**
 * 读取 POST SSE 流。POST 用于携带表范围与提示词，completed 事件携带最终推荐结果。
 */
export async function streamMetadataRecommendation<T>(
  url: string,
  params: {
    table_names?: string[];
    user_prompt?: string;
    strategy?: RelationshipRecommendationStrategy;
  },
  signal: AbortSignal,
  onEvent: (event: MetadataAiStreamEvent<T>) => void,
): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    headers: metadataStreamAuthHeaders(),
    credentials: "include",
    body: JSON.stringify(params || {}),
    signal,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || body.message || `请求失败（HTTP ${response.status}）`);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("当前浏览器不支持 AI 实时进度流");

  const decoder = new TextDecoder();
  let buffer = "";
  let completedResult: T | undefined;
  let terminalEvent = false;

  const handleBlock = (block: string) => {
    const streamEvent = parseMetadataSseBlock<T>(block);
    if (!streamEvent) return;
    console.info("元数据 AI SSE 事件", streamEvent.event, streamEvent.data);
    onEvent(streamEvent);
    if (
      (streamEvent.event === "completed" || streamEvent.event === "interrupted")
      && streamEvent.data.result !== undefined
    ) {
      completedResult = streamEvent.data.result;
      terminalEvent = true;
    } else if (streamEvent.event === "error" || streamEvent.event === "interrupted") {
      terminalEvent = true;
      throw new Error(streamEvent.data.message || "AI 推荐任务已中断");
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() || "";
    for (const block of blocks) {
      handleBlock(block);
    }
    if (terminalEvent || done) break;
  }
  if (buffer.trim() && !terminalEvent) handleBlock(buffer);
  if (completedResult === undefined) {
    throw new Error("AI 推荐进度流意外中断，未收到完成结果");
  }
  return completedResult;
}

export const metadataApi = {
  // Datasets
  getDatasets: () => axios.get<Dataset[]>(`${API_BASE}/datasets`),
  getDataset: (id: number) => axios.get<Dataset>(`${API_BASE}/datasets/${id}`),
  createDataset: (data: Partial<Dataset>) =>
    axios.post<Dataset>(`${API_BASE}/datasets`, data),
  updateDataset: (id: number, data: Partial<Dataset>) =>
    axios.put<Dataset>(`${API_BASE}/datasets/${id}`, data),
  deleteDataset: (id: number) => axios.delete(`${API_BASE}/datasets/${id}`),
  syncToRag: (id: number) =>
    axios.post<MetadataSyncStartResponse>(`${API_BASE}/datasets/${id}/rag/sync`),
  getDatasetYaml: (id: number) =>
    axios.get<any>(`${API_BASE}/datasets/${id}/yaml`),
  testRetrieval: (
    query: string,
    params?: {
      metadata_provider?: string;
      ragflow_metadata_top_k?: number;
      ragflow_similarity_threshold?: number;
      ragflow_vector_weight?: number;
    }
  ) => axios.post<any>("/api/v1/schema", { query, ...params }),

  // Tables
  saveTable: (datasetId: number, tableData: Table) =>
    axios.post<Table>(`${API_BASE}/datasets/${datasetId}/tables`, tableData),

  deleteTable: (datasetId: number, tableName: string) =>
    axios.delete(`${API_BASE}/datasets/${datasetId}/tables/${tableName}`),

  batchDeleteTables: (datasetId: number, tableNames: string[]) =>
    axios.post<{ message: string; deleted_count: number }>(
      `${API_BASE}/datasets/${datasetId}/tables/batch-delete`,
      { table_names: tableNames }
    ),

  // Metrics
  getMetrics: (datasetId: number) =>
    axios.get<Metric[]>(`${API_BASE}/datasets/${datasetId}/metrics`),
  createMetric: (datasetId: number, data: Metric) =>
    axios.post<Metric>(`${API_BASE}/datasets/${datasetId}/metrics`, data),
  updateMetric: (id: number, data: Partial<Metric>) =>
    axios.put<Metric>(`${API_BASE}/metrics/${id}`, data),
  deleteMetric: (id: number) => axios.delete(`${API_BASE}/metrics/${id}`),
  batchDeleteMetrics: (metricIds: number[]) =>
    axios.post<{ message: string; deleted_count: number }>(
      `${API_BASE}/metrics/batch-delete`,
      { metric_ids: metricIds }
    ),

  // Relationships
  getRelationships: (datasetId: number) =>
    axios.get<Relationship[]>(
      `${API_BASE}/datasets/${datasetId}/relationships`
    ),
  createRelationship: (datasetId: number, data: Relationship) =>
    axios.post<Relationship>(
      `${API_BASE}/datasets/${datasetId}/relationships`,
      data
    ),
  updateRelationship: (id: number, data: Partial<Relationship>) =>
    axios.put<Relationship>(`${API_BASE}/relationships/${id}`, data),
  deleteRelationship: (id: number) =>
    axios.delete(`${API_BASE}/relationships/${id}`),
  batchDeleteRelationships: (relationshipIds: number[]) =>
    axios.post<{ message: string; deleted_count: number }>(
      `${API_BASE}/relationships/batch-delete`,
      { relationship_ids: relationshipIds }
    ),
  getAllTables: () =>
    axios.get<AllTablesDataset[]>(`${API_BASE}/all-tables`),

  // AI Assistant / Import (Mock for Phase 4)
  analyzeDDL: (ddl: string, dataSource?: string, signal?: AbortSignal) =>
    axios.post(
      `${API_BASE}/tables/import`,
      { ddl, ...(dataSource ? { data_source: dataSource } : {}) },
      { timeout: 300000, signal }
    ),
  
  recommendMetrics: (datasetId: number, params?: { table_names?: string[]; user_prompt?: string }, signal?: AbortSignal) =>
    axios.post(`${API_BASE}/datasets/${datasetId}/metrics/recommend`, params || {}, { timeout: 300000, signal }),

  recommendMetricsStream: (
    datasetId: number,
    params: { table_names?: string[]; user_prompt?: string },
    signal: AbortSignal,
    onEvent: (event: MetadataAiStreamEvent<MetricRecommendationResult>) => void,
  ) => streamMetadataRecommendation<MetricRecommendationResult>(
    `${API_BASE}/datasets/${datasetId}/metrics/recommend/stream`,
    params,
    signal,
    onEvent,
  ),

  // 逐表关系扫描会串行执行多次模型请求，耗时取决于表数量；仅由调用方的 AbortSignal 主动取消。
  recommendRelationships: (
    datasetId: number,
    params?: { table_names?: string[]; user_prompt?: string; strategy?: RelationshipRecommendationStrategy },
    signal?: AbortSignal
  ) =>
    axios.post<{
      code: number;
      message?: string;
      data: RelationshipRecommendationResult;
    }>(`${API_BASE}/datasets/${datasetId}/relationships/recommend`, params || {}, { timeout: 0, signal }),

  recommendRelationshipsStream: (
    datasetId: number,
    params: { table_names?: string[]; user_prompt?: string; strategy?: RelationshipRecommendationStrategy },
    signal: AbortSignal,
    onEvent: (event: MetadataAiStreamEvent<RelationshipRecommendationResult>) => void,
  ) => streamMetadataRecommendation<RelationshipRecommendationResult>(
    `${API_BASE}/datasets/${datasetId}/relationships/recommend/stream`,
    params,
    signal,
    onEvent,
  ),

  enhanceDatasetMetadata: (datasetId: number) =>
    axios.post<{ code: number; message?: string; data: { description: string; tags: string[] } }>(
      `${API_BASE}/datasets/${datasetId}/enhance-metadata`,
      {},
      { timeout: 300000 }
    ),

  // DB Import
  testDbConnection: (config: any) =>
    axios.post(`${API_BASE}/db/test-connection`, config),
  listDbTables: (config: any) =>
    axios.post(`${API_BASE}/db/tables`, config),
  getDbDdl: (config: any, tables: string[]) =>
    axios.post(`${API_BASE}/db/ddl`, { config, tables }),

  // DB Connection Configs
  listDbConnectionConfigs: () =>
    axios.get<{ code: number; data: DbConnectionConfig[] }>(`${API_BASE}/db/connection-configs`),
  saveDbConnectionConfig: (data: {
    name: string;
    db_type: string;
    host: string;
    port: number;
    db_user: string;
    password: string;
    database_name: string;
    description?: string;
  }) => axios.post(`${API_BASE}/db/connection-configs`, data),
  updateDbConnectionConfig: (id: number, data: {
    name: string;
    db_type: string;
    host: string;
    port: number;
    db_user: string;
    password: string;
    database_name: string;
    description?: string;
  }) => axios.put(`${API_BASE}/db/connection-configs/${id}`, data),
  deleteDbConnectionConfig: (id: number) =>
    axios.delete(`${API_BASE}/db/connection-configs/${id}`),
  debugDbConnectionSql: (id: number, sql: string, limit: number = 100, includeTotal = false) =>
    axios.post<any>(`${API_BASE}/db/connection-configs/${id}/preview`, {
      sql,
      limit,
      include_total: includeTotal,
    }),

  // DB Table Profiling APIs
  triggerDbProfiling: (configId: number, full = false) =>
    axios.post<any>(`${API_BASE}/db/connection-configs/${configId}/profile`, null, {
      params: full ? { full: true } : undefined,
    }),
  cancelDbProfiling: (configId: number) =>
    axios.post<any>(`${API_BASE}/db/connection-configs/${configId}/profile/cancel`),
  getDbProfilingTask: (configId: number) =>
    axios.get<any>(`${API_BASE}/db/connection-configs/${configId}/profile-task`),
  getDbTableProfileStats: (configId: number) =>
    axios.get<any>(`${API_BASE}/db/connection-configs/${configId}/table-profiles/stats`),
  listDbTableProfiles: (
    configId: number,
    params?: {
      page?: number
      page_size?: number
      q?: string
      tag?: string
      is_ignored?: number
      status?: number
      sort_by?: 'default' | 'relevance' | 'table_name' | 'confidence_score' | 'ai_term'
      sort_order?: 'asc' | 'desc'
    }
  ) =>
    axios.get<any>(`${API_BASE}/db/connection-configs/${configId}/table-profiles`, { params }),
  getDbTableProfileDetail: (configId: number, tableName: string) =>
    axios.get<any>(
      `${API_BASE}/db/connection-configs/${configId}/table-profiles/${encodeURIComponent(tableName)}`
    ),
  getDbTableProfileRelated: (configId: number, table: string, limit = 15) =>
    axios.get<any>(`${API_BASE}/db/connection-configs/${configId}/table-profiles/related`, {
      params: { table, limit },
    }),
  importPreviewFromProfiles: (configId: number, tableNames: string[]) =>
    axios.post<any>(
      `${API_BASE}/db/connection-configs/${configId}/import-preview-from-profiles`,
      { table_names: tableNames }
    ),
  toggleDbTableProfileIgnore: (configId: number, tableName: string, isIgnored: number) =>
    axios.put<any>(`${API_BASE}/db/connection-configs/${configId}/table-profiles/ignore`, { table_name: tableName, is_ignored: isIgnored }),

  // Schema Drift Alerts & Inspection
  getDriftSummary: () =>
    axios.get<DriftSummaryResponse>(`${API_BASE}/datasets/drift-summary`),
  getDatasetDriftAlerts: (datasetId: number, status?: number) =>
    axios.get<MetaDriftAlert[]>(`${API_BASE}/datasets/${datasetId}/drift-alerts`, {
      params: status !== undefined ? { status } : undefined,
    }),
  getAllDriftAlerts: (params?: { dataset_id?: number; status?: number }) =>
    axios.get<MetaDriftAlert[]>(`${API_BASE}/drift-alerts`, { params }),
  resolveDriftAlert: (alertId: number, action: 'drop_column' | 'add_column' | 'sync_type' | 'ignore' | 'drop_table') =>
    axios.post<any>(`${API_BASE}/drift-alerts/${alertId}/resolve`, { action }),
  batchResolveDriftAlerts: (
    datasetId: number,
    data: {
      action: 'drop_column' | 'add_column' | 'sync_type' | 'ignore' | 'drop_table';
      drift_type?: string;
      alert_ids?: number[];
    }
  ) => axios.post<any>(`${API_BASE}/datasets/${datasetId}/drift-alerts/batch-resolve`, data),
  batchResolveAllDriftAlerts: (
    data: {
      action: 'drop_column' | 'add_column' | 'sync_type' | 'ignore' | 'drop_table';
      drift_type?: string;
      alert_ids?: number[];
    }
  ) => axios.post<any>(`${API_BASE}/drift-alerts/batch-resolve`, data),
  triggerInspection: (datasetId: number) =>
    axios.post<{ task_id: string; dataset_id: number; message: string }>(
      `${API_BASE}/datasets/${datasetId}/inspect-schema`
    ),
  triggerAllDatasetsInspection: () =>
    axios.post<{ task_id: string; dataset_id: number; message: string }>(
      `${API_BASE}/inspect-all`
    ),
  // Cron Inspection
  getCronInspectionConfig: () =>
    axios.get<CronInspectionConfig>(`${API_BASE}/cron-inspection`),
  updateCronInspectionConfig: (data: { enabled: boolean; cron_expr: string; notification_channels?: string[] }) =>
    axios.post<CronInspectionConfig>(`${API_BASE}/cron-inspection`, data),
  triggerCronInspectionImmediately: () =>
    axios.post<{ code: number; message: string; data: { task_id: number } }>(
      `${API_BASE}/cron-inspection/run`
    ),
};

export interface CronInspectionConfig {
  enabled: boolean;
  cron_expr: string;
  task_id?: number | null;
  next_run_at?: string | null;
  last_run_at?: string | null;
  run_count: number;
  health_status: 'healthy' | 'warning' | 'error' | 'skipped' | 'unknown' | string;
  last_status?: string | null;
  last_message?: string | null;
  last_error?: string | null;
  notification_channels?: string[];
}

export interface MetaDriftAlert {
  id: number;
  dataset_id: number;
  dataset_name?: string;
  table_id?: number | null;
  table_name: string;
  column_name: string;
  drift_type: 'missing_in_db' | 'new_in_db' | 'type_mismatch' | string;
  source: 'runtime' | 'manual_inspection' | 'cron_inspection' | string;
  error_sample?: string | null;
  hit_count: number;
  status: number; // 0: pending, 1: resolved, 2: ignored
  created_at?: string;
  updated_at?: string;
}

export interface DriftSummaryResponse {
  total_pending: number;
  datasets: Record<number, number>;
}



