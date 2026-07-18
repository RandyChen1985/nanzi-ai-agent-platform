<script setup lang="ts">
import { ref, watch } from "vue";
import Modal from "../Modal.vue";
import { metadataApi, type Dataset } from "../../api/metadata";

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
    size="max-w-lg"
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

      <div v-else class="max-h-80 overflow-y-auto space-y-2 pr-1">
        <label
          v-for="dataset in metadataDatasets"
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
      </div>

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
