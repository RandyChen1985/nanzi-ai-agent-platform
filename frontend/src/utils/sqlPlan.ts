export interface SqlPlanData {
  goal?: string;
  tables?: string[];
  fields?: string[];
  metrics?: string[];
  filters?: string[];
  time_range?: string;
  grain?: string;
  joins?: string[];
  risk_notes?: string[];
  dataset_name?: string;
  data_source?: string;
  [key: string]: unknown;
}

export type SqlPlanFieldKey =
  | 'tables'
  | 'fields'
  | 'metrics'
  | 'filters'
  | 'time_range'
  | 'grain'
  | 'joins'
  | 'risk_notes';

export const SQL_PLAN_FIELD_LABELS: Record<SqlPlanFieldKey, string> = {
  tables: '数据表',
  fields: '字段',
  metrics: '指标 / 口径',
  filters: '筛选条件',
  time_range: '时间范围',
  grain: '聚合粒度',
  joins: '表关联',
  risk_notes: '风险提示',
};

const EMPTY_VALUES = new Set(['无', '未找到', 'none', 'n/a', '-']);

export function normalizeSqlPlanList(value: unknown): string[] {
  if (value == null) return [];
  if (Array.isArray(value)) {
    return value
      .map((item) => String(item ?? '').trim())
      .filter(Boolean);
  }
  const text = String(value).trim();
  return text ? [text] : [];
}

export function isSqlPlanPlaceholder(value: string): boolean {
  const normalized = value.trim().toLowerCase();
  return !normalized || EMPTY_VALUES.has(normalized) || normalized.startsWith('未找到');
}

export function parseSqlPlan(raw: string): { ok: true; data: SqlPlanData } | { ok: false; error: string } {
  const text = (raw || '').trim();
  if (!text) {
    return { ok: false, error: 'empty sql_plan payload' };
  }

  try {
    const data = JSON.parse(text) as SqlPlanData;
    if (!data || typeof data !== 'object' || Array.isArray(data)) {
      return { ok: false, error: 'sql_plan must be a JSON object' };
    }
    return { ok: true, data };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'invalid sql_plan json',
    };
  }
}

export function dedupeSqlPlanPayload(raw: string): string {
  return raw.trim().replace(/\s+/g, ' ');
}
