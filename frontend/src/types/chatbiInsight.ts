export interface ChatBIInsightAction {
  id: string;
  label: string;
  description?: string;
  action_type: "send_query" | "fill_query" | "local_action";
  query: string;
  priority?: number;
  requires_data_result?: boolean;
  result_id?: string;
}

export interface ChatBIInsightSource {
  dataset_name?: string;
  data_source?: string;
  tables: Array<{ physical_name: string }>;
}

export interface ChatBIEvidenceMeta {
  result_status: string;
  source_ref?: string | null;
  observed_at?: string | null;
  source_as_of?: string | null;
  freshness?: string | null;
}

export interface ChatBIInsightMeta {
  version: number;
  status: "success";
  result_id?: string;
  sources: ChatBIInsightSource[];
  permission?: {
    row_filter_applied?: boolean;
    dataset_name?: string;
    rule_count?: number;
    message?: string;
  };
  evidence?: ChatBIEvidenceMeta;
  execution: {
    mode: "direct" | "repaired" | "federated";
    row_count: number;
    repair_count?: number;
    federated?: boolean;
  };
  final_sql?: string;
  actions: ChatBIInsightAction[];
}
