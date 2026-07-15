<template>
  <section>
    <div class="mb-3 flex flex-wrap items-center justify-between gap-3">
      <div><h2 class="text-base font-bold text-gray-900 dark:text-gray-100">数据目录</h2><p v-if="!compact" class="mt-1 text-xs text-gray-400">查找当前账号有权限的数据集、数据表和字段</p></div>
      <div v-if="!compact" class="relative w-full sm:w-80"><span class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400">⌕</span><input v-model.trim="keyword" type="search" placeholder="搜索数据集、表或字段" class="w-full rounded-xl border border-gray-200 bg-white py-2.5 pl-9 pr-3 text-sm outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-100 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100" /></div>
    </div>

    <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <div class="rounded-2xl border border-gray-100 bg-white p-4 dark:border-gray-800 dark:bg-gray-900"><span class="text-xs text-gray-400">数据集</span><strong class="mt-2 block text-xl text-gray-900 dark:text-gray-100">{{ datasetCount }}</strong></div>
      <div class="rounded-2xl border border-gray-100 bg-white p-4 dark:border-gray-800 dark:bg-gray-900"><span class="text-xs text-gray-400">数据表</span><strong class="mt-2 block text-xl text-gray-900 dark:text-gray-100">{{ tableCount }}</strong></div>
      <div class="rounded-2xl border border-gray-100 bg-white p-4 dark:border-gray-800 dark:bg-gray-900"><span class="text-xs text-gray-400">字段</span><strong class="mt-2 block text-xl text-gray-900 dark:text-gray-100">{{ columnCount }}</strong></div>
    </div>

    <div class="mt-3 divide-y divide-gray-100 border-y border-gray-100 dark:divide-gray-800 dark:border-gray-800">
      <article v-for="dataset in visibleDatasets" :key="dataset.key" class="py-3">
        <button type="button" class="flex w-full items-center justify-between gap-3 text-left" @click="toggleDataset(dataset.key)">
          <span><strong class="text-sm text-gray-800 dark:text-gray-200">{{ dataset.name }}</strong><span class="ml-2 text-xs text-gray-400">{{ dataset.tables.length }} 张表 · {{ dataset.columnCount }} 个字段</span></span>
          <span class="text-xs text-blue-500">{{ compact ? '查看结构 →' : (expandedDatasets.has(dataset.key) ? '收起' : '展开') }}</span>
        </button>
        <div v-if="!compact && expandedDatasets.has(dataset.key)" class="mt-3 space-y-2 pl-0 sm:pl-3">
          <div v-for="table in dataset.tables" :key="table.name" class="rounded-xl border border-gray-100 bg-gray-50/70 dark:border-gray-800 dark:bg-gray-950/40">
            <button type="button" class="flex w-full items-start justify-between gap-3 px-3 py-3 text-left" @click="toggleTable(dataset.key, table.name)">
              <span class="min-w-0"><strong class="block truncate text-sm text-gray-800 dark:text-gray-200">{{ table.name }}</strong><span v-if="table.physicalName" class="mt-0.5 block truncate font-mono text-[11px] text-gray-400">{{ table.physicalName }}</span><span v-if="table.description" class="mt-1 block text-xs text-gray-500">{{ table.description }}</span></span>
              <span class="shrink-0 text-xs text-gray-400">{{ table.columns.length }} 字段 {{ expandedTables.has(tableKey(dataset.key, table.name)) ? '⌃' : '⌄' }}</span>
            </button>
            <div v-if="expandedTables.has(tableKey(dataset.key, table.name))" class="border-t border-gray-100 px-3 py-3 dark:border-gray-800">
              <div v-if="table.columns.length" class="grid grid-cols-1 gap-2 lg:grid-cols-2">
                <div v-for="column in table.columns" :key="column.name" class="rounded-lg bg-white px-3 py-2 dark:bg-gray-900"><div class="flex items-center justify-between gap-2"><strong class="truncate font-mono text-xs text-blue-600 dark:text-blue-400">{{ column.name }}</strong><span class="shrink-0 text-[10px] uppercase text-gray-400">{{ column.type || '-' }}</span></div><div class="mt-1 text-xs text-gray-500">{{ column.term || column.description || '暂无业务说明' }}</div></div>
              </div>
              <div v-else class="text-xs text-gray-400">暂无字段元数据</div>
              <div class="mt-3 flex flex-wrap gap-2">
                <button type="button" class="rounded-lg bg-blue-50 px-2.5 py-1.5 text-xs text-blue-600 hover:bg-blue-100" @click.stop="askTable(dataset.name, table, 'structure')">结构说明</button>
                <button type="button" class="rounded-lg bg-emerald-50 px-2.5 py-1.5 text-xs text-emerald-600 hover:bg-emerald-100" @click.stop="askTable(dataset.name, table, 'query')">查询明细</button>
                <button type="button" class="rounded-lg bg-amber-50 px-2.5 py-1.5 text-xs text-amber-600 hover:bg-amber-100" @click.stop="askTable(dataset.name, table, 'recommend')">推荐提问</button>
              </div>
            </div>
          </div>
        </div>
      </article>
    </div>
    <div v-if="!visibleDatasets.length" class="rounded-xl border border-dashed border-gray-200 px-4 py-8 text-center text-sm text-gray-400 dark:border-gray-800">没有找到匹配的数据</div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import type { DatasetPortalColumn, DatasetPortalPayload } from "@/composables/useDatasetPortal";

interface CatalogTable { name: string; physicalName: string; description: string; columns: DatasetPortalColumn[] }
interface CatalogDataset { key: string; name: string; tables: CatalogTable[]; columnCount: number }
const props = withDefaults(defineProps<{ payload: DatasetPortalPayload; compact?: boolean }>(), { compact: false });
const emit = defineEmits<{ (event: "quick-question", query: string, action: "send" | "fill"): void }>();
const keyword = ref("");
const expandedDatasets = ref(new Set<string>());
const expandedTables = ref(new Set<string>());

const datasets = computed<CatalogDataset[]>(() => {
  const map = new Map<string, { name: string; tables: Map<string, CatalogTable> }>();
  for (const group of props.payload.groups || []) for (const related of group.related_data || []) {
    const key = related.dataset || related.display_name || "unknown";
    const entry = map.get(key) || { name: related.display_name || related.dataset || "未命名数据集", tables: new Map() };
    for (const name of related.tables || []) {
      const description = related.table_descriptions?.find((item) => item.name === name)?.description || "";
      const existing = entry.tables.get(name);
      entry.tables.set(name, { name, physicalName: related.table_physical_names?.[name] || existing?.physicalName || "", description: description || existing?.description || "", columns: related.table_columns?.[name] || existing?.columns || [] });
    }
    map.set(key, entry);
  }
  return Array.from(map.entries()).map(([key, item]) => { const tables = Array.from(item.tables.values()); return { key, name: item.name, tables, columnCount: tables.reduce((sum, table) => sum + table.columns.length, 0) }; });
});
const visibleDatasets = computed(() => {
  if (props.compact) return datasets.value.slice(0, 5);
  const needle = keyword.value.toLowerCase();
  if (!needle) return datasets.value;
  return datasets.value.map((dataset) => ({ ...dataset, tables: dataset.tables.filter((table) => [dataset.name, table.name, table.physicalName, table.description, ...table.columns.flatMap((column) => [column.name, column.term, column.description || ""])].join(" ").toLowerCase().includes(needle)) })).filter((dataset) => dataset.tables.length || dataset.name.toLowerCase().includes(needle));
});
const datasetCount = computed(() => props.payload.dataset_count ?? datasets.value.length);
const tableCount = computed(() => datasets.value.reduce((total, item) => total + item.tables.length, 0));
const columnCount = computed(() => datasets.value.reduce((total, item) => total + item.columnCount, 0));
const tableKey = (dataset: string, table: string) => `${dataset}::${table}`;
const toggleSet = (source: Set<string>, key: string) => { const next = new Set(source); next.has(key) ? next.delete(key) : next.add(key); return next; };
const toggleDataset = (key: string) => { if (props.compact) return; expandedDatasets.value = toggleSet(expandedDatasets.value, key); };
const toggleTable = (dataset: string, table: string) => { expandedTables.value = toggleSet(expandedTables.value, tableKey(dataset, table)); };
const askTable = (dataset: string, table: CatalogTable, type: "structure" | "query" | "recommend") => {
  const target = table.physicalName ? `“${table.name}”（物理表：${table.physicalName}）` : `“${table.name}”`;
  const query = type === "structure" ? `说明${dataset}数据集下数据表${target}的字段结构和分析口径` : type === "query" ? `查询数据表${target}最近10条明细数据` : `基于${dataset}数据集的数据表${target}，推荐适合业务分析的问题`;
  emit("quick-question", query, type === "recommend" ? "fill" : "send");
};
</script>
