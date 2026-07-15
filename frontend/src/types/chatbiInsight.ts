export interface ChatBIInsightAction {
  id: string;
  label: string;
  description?: string;
  action_type: "send_query" | "fill_query" | "local_action";
  query: string;
  priority?: number;
  requires_data_result?: boolean;
}

export interface ChatBIInsightSource {
  dataset_name?: string;
  data_source?: string;
  tables: Array<{ physical_name: string }>;
}

export interface ChatBIInsightMeta {
  version: number;
  status: "success";
  sources: ChatBIInsightSource[];
  permission?: {
    row_filter_applied?: boolean;
    dataset_name?: string;
    rule_count?: number;
    message?: string;
  };
  execution: {
    mode: "direct" | "repaired" | "federated";
    row_count: number;
    repair_count?: number;
    federated?: boolean;
  };
  final_sql?: string;
  actions: ChatBIInsightAction[];
}

