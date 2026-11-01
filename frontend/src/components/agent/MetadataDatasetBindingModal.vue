<script setup lang="ts">
import { computed, ref, watch } from "vue";
import Modal from "../Modal.vue";
import { metadataApi, type Dataset } from "../../api/metadata";
import {
  countByDataSource,
  filterAndSortMetadataDatasets,
  hasAnyMetadataFilter,
  labelOf,
  listDataSourceOptions,
  type MetadataFilters,
  type MetadataSortBy,
} from "../../utils/metadataDatasetFilter";

interface ToolConfig {
  name: string;
  model_name?: string;
  temperature?: number;
  description_override?: string;
  metadata_dataset_ids?: string[];
}

const props = defineProps<{
  model: boolean;
  config: ToolConfig;
  readonly?: boolean;
}>();

const emit = defineEmits(["update:model", "save"]);

const localConfig = ref<ToolConfig>({ ...props.config });
const metadataDatasets = ref<Dataset[]>([]);
const loadingMetadataDatasets = ref(false);

const loadMetadataDatasets = async () => {
  if (metadataDatasets.value.length > 0) return;
  loadingMetadataDatasets.value = true;
  try {
    const res = await metadataApi.getDatasets();
    metadataDatasets.value = (res.data || []).filter((item) => item.status === undefined || item.status === 1);
  } finally {
    loadingMetadataDatasets.value = false;
  }
};

/* ---------------- 搜索与筛选 ---------------- */

const keyword = ref("");
const dataSourceFilter = ref("");
const sortBy = ref<MetadataSortBy>("name");

const currentFilters = computed<MetadataFilters>(() => ({
  keyword: keyword.value,
  dataSource: dataSourceFilter.value,
}));

const filteredDatasets = computed(() =>
  filterAndSortMetadataDatasets(metadataDatasets.value, currentFilters.value, sortBy.value),
);

const dataSourceOptions = computed(() => listDataSourceOptions(metadataDatasets.value));
const dataSourceCounts = computed(() => countByDataSource(metadataDatasets.value));
const hasActiveFilters = computed(() => hasAnyMetadataFilter(currentFilters.value));

const selectedDatasetIds = computed(() => localConfig.value.metadata_dataset_ids || []);

/** 已选区：按选中顺序取回实体，找不到的（已删除）跳过 */
const selectedDatasets = computed(() =>
  selectedDatasetIds.value
    .map((id) => metadataDatasets.value.find((ds) => String(ds.id) === String(id)))
    .filter((ds): ds is Dataset => Boolean(ds)),
);

/** 全选只作用于当前筛选结果，不做全库全选 */
const selectAllFiltered = () => {
  if (props.readonly) return;
  const ids = new Set(selectedDatasetIds.value.map(String));
  filteredDatasets.value.forEach((ds) => ids.add(String(ds.id)));
  localConfig.value.metadata_dataset_ids = Array.from(ids);
};

const clearSelected = () => {
  if (props.readonly) return;
  localConfig.value.metadata_dataset_ids = [];
};

const removeSelected = (datasetId: number) => {
  if (props.readonly) return;
  localConfig.value.metadata_dataset_ids = selectedDatasetIds.value.filter(
    (item) => String(item) !== String(datasetId),
  );
};

const resetFilters = () => {
  keyword.value = "";
  dataSourceFilter.value = "";
};

const toggleMetadataDataset = (datasetId: number) => {
  if (props.readonly) return;
  const id = String(datasetId);
  const current = localConfig.value.metadata_dataset_ids || [];
  localConfig.value.metadata_dataset_ids = current.includes(id)
    ? current.filter((item) => item !== id)
    : [...current, id];
};

watch(() => props.model, (val) => {
  if (val) {
    localConfig.value = {
      ...props.config,
      name: props.config.name || "get_dataset_schema",
      metadata_dataset_ids: Array.isArray(props.config.metadata_dataset_ids)
        ? props.config.metadata_dataset_ids.map(String)
        : []
    };
    loadMetadataDatasets();
  }
});

const handleSave = () => {
  emit("save", { ...localConfig.value });
  emit("update:model", false);
};
</script>

<template>
  <Modal
    v-if="model"
    title="绑定数据集 - get_dataset_schema"
    :z-index="10050"
    @close="emit('update:model', false)"
    size="max-w-2xl"
  >
    <div class="space-y-5 py-1">
      <div class="rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-xs leading-5 text-blue-700">
        限制 get_dataset_schema 只能检索选中的元数据集。未选择时沿用当前用户有权访问的数据集范围。
      </div>

      <div v-if="loadingMetadataDatasets" class="text-sm text-gray-400 py-6 text-center">
        正在加载元数据集...
      </div>

      <div v-else-if="metadataDatasets.length === 0" class="rounded-xl border border-dashed border-gray-300 p-5 text-sm text-gray-500">
        暂无可绑定元数据集，请先到元数据管理中创建并启用数据集。
      </div>

      <template v-else>
        <!-- 搜索与筛选：数据集上百个时，靠滚动找很费劲 -->
        <div class="space-y-2">
          <div class="relative">
            <svg class="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.4">
              <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-5-5m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            <input
              v-model="keyword"
              type="search"
              placeholder="搜索数据集名称、描述、数据源或标签…"
              autocomplete="off"
              class="h-9 w-full rounded-lg border border-gray-200 bg-gray-50 py-0 pl-8 pr-8 text-xs text-gray-800 outline-none transition-colors placeholder:text-gray-400 focus:border-primary focus:bg-white focus:ring-2 focus:ring-primary/10"
            />
          </div>

          <div class="flex flex-wrap items-center gap-1.5">
            <span class="text-[10px] font-bold tracking-wide text-gray-400">数据源</span>
            <select
              v-model="dataSourceFilter"
              aria-label="按数据源筛选"
              class="h-[25px] cursor-pointer appearance-none rounded-md border border-gray-200 bg-white py-0 pl-2 pr-5 text-[11px] font-semibold text-gray-600 outline-none"
              style="background-image: url(&quot;data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%239ca3af' stroke-width='2.5'><path stroke-linecap='round' stroke-linejoin='round' d='M6 9l6 6 6-6'/></svg>&quot;); background-repeat: no-repeat; background-position: right 4px center; background-size: 11px"
            >
              <option value="">全部（{{ metadataDatasets.length }}）</option>
              <option v-for="source in dataSourceOptions" :key="source" :value="source">
                {{ source }}（{{ dataSourceCounts[source] || 0 }}）
              </option>
            </select>

            <span class="ml-auto"></span>

            <select
              v-model="sortBy"
              aria-label="排序方式"
              class="h-[25px] cursor-pointer appearance-none rounded-md border border-gray-200 bg-white py-0 pl-2 pr-5 text-[11px] font-semibold text-gray-600 outline-none"
              style="background-image: url(&quot;data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%239ca3af' stroke-width='2.5'><path stroke-linecap='round' stroke-linejoin='round' d='M6 9l6 6 6-6'/></svg>&quot;); background-repeat: no-repeat; background-position: right 4px center; background-size: 11px"
            >
              <option value="name">名称 A→Z</option>
              <option value="tables">表最多</option>
              <option value="updated">最近更新</option>
            </select>
          </div>

          <div class="flex items-center gap-2 text-[11px] text-gray-500">
            <span>
              筛选出 <b class="tabular-nums text-gray-800">{{ filteredDatasets.length }}</b>
              / 共 <b class="tabular-nums text-gray-800">{{ metadataDatasets.length }}</b> 个
            </span>
            <span class="ml-auto"></span>
            <template v-if="!readonly">
              <button type="button" @click="selectAllFiltered" class="rounded px-1 py-0.5 text-[11px] font-bold text-primary hover:bg-primary/5">全选当前结果</button>
              <button type="button" @click="clearSelected" class="rounded px-1 py-0.5 text-[11px] font-bold text-gray-400 hover:bg-gray-100 hover:text-gray-600">清空已选</button>
            </template>
          </div>

          <!-- 已选区：选错了不用回长列表里翻找 -->
          <div v-if="selectedDatasets.length" class="rounded-lg border border-blue-100 bg-blue-50/60 px-2 py-1.5">
            <div class="flex flex-wrap items-center gap-1.5">
              <span class="text-[10px] font-bold text-blue-700">已选 {{ selectedDatasets.length }}</span>
              <span
                v-for="ds in selectedDatasets.slice(0, 6)"
                :key="'sel-' + ds.id"
                class="flex max-w-[160px] items-center gap-1 rounded-md border border-blue-100 bg-white pl-1.5 pr-0.5 text-[10.5px] font-semibold text-blue-700"
                :title="labelOf(ds)"
              >
                <span class="truncate">{{ labelOf(ds) }}</span>
                <button
                  v-if="!readonly"
                  type="button"
                  :aria-label="`移除 ${labelOf(ds)}`"
                  @click="removeSelected(ds.id)"
                  class="flex h-3.5 w-3.5 items-center justify-center rounded text-blue-600 opacity-60 hover:bg-blue-50 hover:opacity-100"
                >
                  <svg class="h-2 w-2" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3.4">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </span>
              <span v-if="selectedDatasets.length > 6" class="text-[10px] font-bold text-gray-400">
                +{{ selectedDatasets.length - 6 }} 个
              </span>
            </div>
          </div>
        </div>

        <div class="max-h-80 overflow-y-auto space-y-2 pr-1">
          <label
            v-for="dataset in filteredDatasets"
            :key="dataset.id"
            class="flex cursor-pointer items-start gap-3 rounded-xl border border-gray-200 p-3 hover:bg-gray-50"
            :class="(localConfig.metadata_dataset_ids || []).includes(String(dataset.id)) ? 'border-blue-300 bg-blue-50/60' : 'bg-white'"
          >
            <input
              type="checkbox"
              class="mt-1 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
              :disabled="readonly"
              :checked="(localConfig.metadata_dataset_ids || []).includes(String(dataset.id))"
              @change="toggleMetadataDataset(dataset.id)"
            >
            <span class="min-w-0 flex-1">
              <span class="block text-sm font-medium text-gray-900 truncate">
                {{ dataset.display_name || dataset.name }}
              </span>
              <span class="block text-xs text-gray-500">
                ID: {{ dataset.id }} · {{ dataset.data_source || '默认数据源' }}
              </span>
              <span v-if="dataset.description" class="mt-1 block text-xs leading-5 text-gray-500">
                {{ dataset.description }}
              </span>
            </span>
          </label>

          <div v-if="filteredDatasets.length === 0" class="flex flex-col items-center justify-center rounded-xl border border-dashed border-gray-300 px-4 py-8 text-center">
            <svg class="mb-2 h-7 w-7 text-gray-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.6">
              <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-5-5m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            <p class="text-xs font-semibold text-gray-500">没有匹配的数据集</p>
            <p class="mt-0.5 text-[11px] text-gray-400">换个关键词，或放宽筛选条件</p>
            <button
              v-if="hasActiveFilters"
              type="button"
              @click="resetFilters"
              class="mt-2 rounded-md border border-gray-200 bg-white px-2.5 py-1 text-[11px] font-bold text-primary hover:border-primary/30 hover:bg-primary/5"
            >
              清除全部筛选
            </button>
          </div>
        </div>
      </template>

      <div class="flex justify-end space-x-3 pt-4 border-t border-gray-100">
        <button
          @click="emit('update:model', false)"
          class="px-4 py-2 text-sm text-gray-500 hover:text-gray-700"
        >
          {{ readonly ? '关闭' : '取消' }}
        </button>
        <button
          v-if="!readonly"
          @click="handleSave"
          class="px-6 py-2 bg-primary text-white text-sm rounded-lg hover:bg-primary-dark transition-colors font-medium"
        >
          保存绑定
        </button>
      </div>
    </div>
  </Modal>
</template>
