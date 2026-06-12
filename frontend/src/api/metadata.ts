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
    axios.post<any>(`${API_BASE}/datasets/${id}/rag/sync`),
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

  // Metrics
  getMetrics: (datasetId: number) =>
    axios.get<Metric[]>(`${API_BASE}/datasets/${datasetId}/metrics`),
  createMetric: (datasetId: number, data: Metric) =>
    axios.post<Metric>(`${API_BASE}/datasets/${datasetId}/metrics`, data),
  updateMetric: (id: number, data: Partial<Metric>) =>
    axios.put<Metric>(`${API_BASE}/metrics/${id}`, data),
  deleteMetric: (id: number) => axios.delete(`${API_BASE}/metrics/${id}`),

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

  // AI Assistant / Import (Mock for Phase 4)
  analyzeDDL: (ddl: string) =>
    axios.post(`${API_BASE}/tables/import`, { ddl }, { timeout: 300000 }),
  
  recommendMetrics: (datasetId: number) =>
    axios.post(`${API_BASE}/datasets/${datasetId}/metrics/recommend`, {}, { timeout: 300000 }),

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
  debugDbConnectionSql: (id: number, sql: string, limit: number = 100) =>
    axios.post<any>(`${API_BASE}/db/connection-configs/${id}/preview`, { sql, limit }),
};
