<script setup lang="ts">
import { computed } from 'vue';
import {
  SQL_PLAN_FIELD_LABELS,
  type SqlPlanData,
  type SqlPlanFieldKey,
  isSqlPlanPlaceholder,
  normalizeSqlPlanList,
} from '@/utils/sqlPlan';

const props = defineProps<{
  plan: SqlPlanData;
}>();

const DISPLAY_FIELDS: SqlPlanFieldKey[] = [
  'tables',
  'fields',
  'metrics',
  'filters',
  'time_range',
  'grain',
  'joins',
  'risk_notes',
];

const goal = computed(() => String(props.plan.goal || '').trim());

const rows = computed(() => {
  return DISPLAY_FIELDS.map((key) => {
    if (key === 'time_range' || key === 'grain') {
      const value = String(props.plan[key] ?? '').trim();
      if (!value) return null;
      return {
        key,
        label: SQL_PLAN_FIELD_LABELS[key],
        values: [value],
        muted: isSqlPlanPlaceholder(value),
        warning: false,
      };
    }

    const values = normalizeSqlPlanList(props.plan[key]);
    if (!values.length) return null;

    const muted = values.every(isSqlPlanPlaceholder);
    return {
      key,
      label: SQL_PLAN_FIELD_LABELS[key],
      values,
      muted,
      warning: key === 'risk_notes',
    };
  }).filter(Boolean) as Array<{
    key: SqlPlanFieldKey;
    label: string;
    values: string[];
    muted: boolean;
    warning: boolean;
  }>;
});

const hasRisk = computed(() => rows.value.some((row) => row.warning && !row.muted));
</script>

<template>
  <div class="sql-plan-card" :class="{ 'sql-plan-card--warning': hasRisk }">
    <div class="sql-plan-card__header">
      <div class="sql-plan-card__icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"
          />
        </svg>
      </div>
      <div class="sql-plan-card__title-wrap">
        <div class="sql-plan-card__eyebrow">SQL 查询计划</div>
        <div v-if="goal" class="sql-plan-card__goal">{{ goal }}</div>
        <div v-else class="sql-plan-card__goal sql-plan-card__goal--muted">未提供查询目标</div>
      </div>
    </div>

    <div class="sql-plan-card__body">
      <div
        v-for="row in rows"
        :key="row.key"
        class="sql-plan-row"
        :class="{
          'sql-plan-row--warning': row.warning && !row.muted,
          'sql-plan-row--muted': row.muted,
        }"
      >
        <div class="sql-plan-row__label">{{ row.label }}</div>
        <div class="sql-plan-row__values">
          <span
            v-for="(value, index) in row.values"
            :key="`${row.key}-${index}`"
            class="sql-plan-chip"
            :class="{
              'sql-plan-chip--warning': row.warning && !isSqlPlanPlaceholder(value),
              'sql-plan-chip--muted': isSqlPlanPlaceholder(value),
            }"
          >
            {{ value }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sql-plan-card {
  margin: 1rem 0;
  border: 1.5px solid #dbeafe;
  border-radius: 16px;
  background: linear-gradient(180deg, #f8fbff 0%, #ffffff 100%);
  box-shadow: 0 8px 24px -18px rgba(37, 99, 235, 0.45);
  overflow: hidden;
}

.dark .sql-plan-card {
  border-color: #1d4ed8;
  background: linear-gradient(180deg, #0f172a 0%, #111827 100%);
}

.sql-plan-card--warning {
  border-color: #fcd34d;
  box-shadow: 0 8px 24px -18px rgba(245, 158, 11, 0.35);
}

.sql-plan-card__header {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 14px 16px;
  border-bottom: 1px solid #e0e7ff;
  background: rgba(239, 246, 255, 0.75);
}

.dark .sql-plan-card__header {
  border-bottom-color: #1e3a8a;
  background: rgba(30, 58, 138, 0.22);
}

.sql-plan-card__icon {
  width: 36px;
  height: 36px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #2563eb;
  background: #dbeafe;
  flex-shrink: 0;
}

.sql-plan-card__icon svg {
  width: 18px;
  height: 18px;
}

.dark .sql-plan-card__icon {
  color: #93c5fd;
  background: rgba(37, 99, 235, 0.2);
}

.sql-plan-card__eyebrow {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: #2563eb;
}

.dark .sql-plan-card__eyebrow {
  color: #93c5fd;
}

.sql-plan-card__goal {
  margin-top: 4px;
  font-size: 14px;
  font-weight: 700;
  line-height: 1.5;
  color: #0f172a;
}

.dark .sql-plan-card__goal {
  color: #e2e8f0;
}

.sql-plan-card__goal--muted {
  color: #64748b;
  font-weight: 600;
}

.sql-plan-card__body {
  padding: 12px 16px 16px;
  display: grid;
  gap: 10px;
}

.sql-plan-row {
  display: grid;
  grid-template-columns: 88px minmax(0, 1fr);
  gap: 10px;
  align-items: start;
}

@media (max-width: 640px) {
  .sql-plan-row {
    grid-template-columns: 1fr;
    gap: 6px;
  }
}

.sql-plan-row__label {
  font-size: 12px;
  font-weight: 700;
  color: #475569;
  padding-top: 4px;
}

.dark .sql-plan-row__label {
  color: #94a3b8;
}

.sql-plan-row__values {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.sql-plan-chip {
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid #bfdbfe;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.4;
  word-break: break-word;
}

.dark .sql-plan-chip {
  border-color: #1e40af;
  background: rgba(30, 64, 175, 0.18);
  color: #bfdbfe;
}

.sql-plan-chip--muted {
  border-color: #e2e8f0;
  background: #f8fafc;
  color: #94a3b8;
}

.dark .sql-plan-chip--muted {
  border-color: #334155;
  background: rgba(51, 65, 85, 0.35);
  color: #94a3b8;
}

.sql-plan-chip--warning {
  border-color: #fcd34d;
  background: #fffbeb;
  color: #b45309;
}

.dark .sql-plan-chip--warning {
  border-color: #b45309;
  background: rgba(180, 83, 9, 0.18);
  color: #fcd34d;
}

.sql-plan-row--warning .sql-plan-row__label {
  color: #b45309;
}
</style>
