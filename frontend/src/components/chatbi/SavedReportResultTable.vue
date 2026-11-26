<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  snapshot: Record<string, any>;
}>();

const columns = computed(() => {
  const rawColumns = Array.isArray(props.snapshot?.columns) ? props.snapshot.columns : [];
  if (rawColumns.length) {
    return rawColumns.map((column: any, index: number) => ({
      name: typeof column === "string" ? column : String(column?.name || `column_${index + 1}`),
      label: typeof column === "object" && (column?.term || column?.label || column?.display_name)
        ? String(column.term || column.label || column.display_name)
        : String(props.snapshot?.column_labels?.[String(column?.name || column)] || column?.name || column || `列 ${index + 1}`),
    }));
  }
  const firstRow = Array.isArray(props.snapshot?.rows) ? props.snapshot.rows[0] : null;
  if (firstRow && typeof firstRow === "object" && !Array.isArray(firstRow)) {
    return Object.keys(firstRow).map((name) => ({
      name,
      label: String(props.snapshot?.column_labels?.[name] || name),
    }));
  }
  return [];
});

const rows = computed(() => Array.isArray(props.snapshot?.rows) ? props.snapshot.rows : []);

const valueOf = (row: any, name: string, index: number) => {
  const value = Array.isArray(row) ? row[index] : row?.[name];
  if (value === null || value === undefined) return "NULL";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
};

const escapeCsvCell = (value: string) => `"${value.replace(/"/g, '""')}"`;

const exportCsv = () => {
  if (!columns.value.length) return;
  const lines = [
    columns.value.map((column) => escapeCsvCell(column.label)).join(","),
    ...rows.value.map((row) => columns.value.map((column, index) => escapeCsvCell(valueOf(row, column.name, index))).join(",")),
  ];
  const blob = new Blob([`\uFEFF${lines.join("\n")}`], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `saved-report-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
};
</script>

<template>
  <div class="space-y-2">
    <div class="flex items-center justify-between gap-2">
      <p class="text-[11px] font-bold text-gray-500">结果表格（前 {{ rows.length }} 行）</p>
      <button
        v-if="columns.length && rows.length"
        type="button"
        class="rounded-lg border border-gray-200 bg-white px-2 py-1 text-[10px] font-bold text-primary hover:border-primary/25 hover:bg-primary/5 dark:border-gray-700 dark:bg-gray-950 dark:text-primary-hover"
        @click="exportCsv"
      >
        导出 CSV
      </button>
    </div>
    <div v-if="columns.length && rows.length" class="max-h-72 overflow-auto rounded-lg border border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-950">
      <table class="min-w-full border-collapse text-left text-[10px]">
        <thead class="sticky top-0 bg-gray-100 dark:bg-gray-800">
          <tr>
            <th v-for="column in columns" :key="column.name" class="whitespace-nowrap border-b border-gray-200 px-2.5 py-2 font-bold text-gray-600 dark:border-gray-700 dark:text-gray-300">
              {{ column.label }}
            </th>
          </tr>
        </thead>
        <tbody class="divide-y divide-gray-100 dark:divide-gray-800">
          <tr v-for="(row, rowIndex) in rows" :key="rowIndex" class="hover:bg-primary/5 dark:hover:bg-gray-900">
            <td v-for="(column, columnIndex) in columns" :key="column.name" class="max-w-[18rem] whitespace-nowrap px-2.5 py-1.5 text-gray-600 dark:text-gray-300">
              {{ valueOf(row, column.name, columnIndex) }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="rounded-lg border border-dashed border-gray-200 px-3 py-4 text-center text-[11px] text-gray-400 dark:border-gray-800">本次没有可展示的结果行</p>
  </div>
</template>
