import type { DatasetPortalPayload } from "@/composables/useDatasetPortal";

export interface DataPortalAttention {
  failed_runs_today: number;
  latest_failed_run: null | Record<string, any>;
  digests_today: number;
  latest_digest_at: string | null;
  active_subscriptions: number;
  completed_subscriptions_today: number;
}

export interface DataPortalActivity {
  type: "digest" | "report_run" | "conversation";
  id: number | string;
  report_id?: string;
  run_id?: number;
  conversation_id?: string;
  title: string;
  subtitle: string;
  status: string;
  occurred_at: string | null;
  action: "open_digest" | "open_report" | "continue_analysis" | "open_conversation";
}

export interface DataPortalReportItem {
  id: string;
  title: string;
  owner_name?: string | null;
  is_owner: boolean;
  is_favorite: boolean;
  pinned: boolean;
  pinned_at?: string | null;
  description?: string | null;
  tags?: string[];
  last_run_at?: string | null;
  last_error?: string | null;
  subscription_status?: string | null;
  subscription_cron_expr?: string | null;
  subscription_next_run_at?: string | null;
}

export interface DataPortalHomePayload {
  attention: DataPortalAttention;
  recent_analysis: DataPortalActivity[];
  report_summary: {
    subscribed: number;
    pinned: number;
    favorite: number;
    shared: number;
    recent: number;
    items: DataPortalReportItem[];
  };
  generated_at: string;
}

export type DataPortalScenePayload = DatasetPortalPayload;
export type DataPortalReportFilter = "all" | "subscribed" | "pinned" | "favorite" | "shared" | "recent";
