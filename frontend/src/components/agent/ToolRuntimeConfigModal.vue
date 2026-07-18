<script setup lang="ts">
import { ref, watch } from "vue";
import Modal from "../Modal.vue";
import type { AIModel } from "../../api/model";

interface ToolConfig {
  name: string;
  model_name?: string;
  temperature?: number;
  description_override?: string;
  [key: string]: any;
}

const props = defineProps<{
  model: boolean;
  toolName: string;
  config: ToolConfig;
  availableModels: AIModel[];
  readonly?: boolean;
}>();

const emit = defineEmits(["update:model", "save"]);

const localConfig = ref<ToolConfig>({ ...props.config });

// Sync when modal opens
watch(() => props.model, (val) => {
  if (val) {
    localConfig.value = { 
      ...props.config,
      name: props.toolName,
      model_name: props.config.model_name || "",
      temperature: props.config.temperature ?? 0,
      description_override: props.config.description_override || ""
    };
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
    :title="`工具高级配置 - ${toolName}`"
    :z-index="10050"
    @close="emit('update:model', false)"
    size="max-w-md"
  >
    <div class="space-y-6">
      <!-- Runtime Settings Section -->
      <div>
        <h4 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4 flex items-center">
          <span class="mr-2">🚀</span> 运行环境 (Runtime)
        </h4>
        
        <div class="space-y-4">
          <!-- Model Selection -->
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">执行模型 (Override Model)</label>
            <select
              v-model="localConfig.model_name"
              :disabled="readonly"
              class="w-full px-3 py-2 border border-gray-300 rounded-lg outline-none focus:ring-2 focus:ring-primary bg-white text-sm disabled:bg-gray-50 disabled:text-gray-500"
            >
              <option value="">跟随智能体默认配置</option>
              <option
                v-for="m in availableModels"
                :key="m.id"
                :value="m.model_id"
              >
                {{ m.type === 'multimodal' ? '🖼️ ' : '' }}{{ m.name }} ({{ m.model_id }})
              </option>
            </select>
            <p class="text-[10px] text-gray-400 mt-1">
              指定该工具执行时使用的特定模型。例如 SQL 生成建议使用 Coder 模型。
            </p>
          </div>

          <!-- Temperature -->
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1 flex justify-between">
              <span>温度 (Temperature)</span>
              <span class="text-primary font-mono text-xs">{{ localConfig.temperature }}</span>
            </label>
            <input
              type="range"
              min="0"
              max="2"
              step="0.1"
              v-model.number="localConfig.temperature"
              :disabled="readonly"
              class="w-full accent-primary disabled:opacity-50"
            />
            <div class="flex justify-between text-[10px] text-gray-400 mt-1">
              <span>精确</span><span>平衡</span><span>发散</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Prompt Tuning Section (Optional/Advanced) -->
      <div class="pt-4 border-t border-gray-100">
        <h4 class="text-xs font-bold text-gray-400 uppercase tracking-widest mb-4 flex items-center">
          <span class="mr-2">📝</span> 提示词微调 (Prompt Override)
        </h4>
        <div>
          <label class="block text-sm font-medium text-gray-700 mb-1">工具描述覆盖</label>
          <textarea
            v-model="localConfig.description_override"
            :disabled="readonly"
            rows="3"
            placeholder="留空则使用工具默认描述。覆盖描述可以帮助智能体更精确地理解何时调用此工具。"
            class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm outline-none focus:ring-2 focus:ring-primary disabled:bg-gray-50 disabled:text-gray-500"
          ></textarea>
        </div>
      </div>

      <div class="flex justify-end space-x-3 pt-4">
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
          保存配置
        </button>
      </div>
    </div>
  </Modal>
</template>
