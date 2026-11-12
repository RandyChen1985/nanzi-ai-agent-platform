<script setup lang="ts">
import { computed, ref, watch } from "vue";
import Modal from "../Modal.vue";

interface ToolConfig {
  name: string;
  /** @deprecated 历史字段：工具级模型没有下发点，弹窗不再展示 */
  model_name?: string;
  temperature?: number | null;
  timeout_seconds?: number | null;
  description_override?: string | null;
  [key: string]: any;
}

const props = withDefaults(defineProps<{
  model: boolean;
  toolName: string;
  config: ToolConfig;
  readonly?: boolean;
  /** 该工具内部是否会自己调 LLM；只有这类工具的温度才有意义 */
  supportsInternalLlm?: boolean;
  /** 上层已选定的工具超时（版本级或全局级），工具级只能收紧不能放宽 */
  upperTimeoutSeconds?: number | null;
  /** 上层超时的来源描述，用于「跟随上层」的说明文案 */
  upperTimeoutSource?: string;
}>(), {
  readonly: false,
  supportsInternalLlm: false,
  upperTimeoutSeconds: null,
  upperTimeoutSource: "智能体版本配置",
});

const emit = defineEmits(["update:model", "save"]);

const MAX_TOOL_TIMEOUT = 3600;

const localConfig = ref<ToolConfig>({ ...props.config });
const timeoutDraft = ref<string>("");
// 温度单独用草稿值：0 与「未配置」在视觉上相同，用 null 表示未配置、0 表示显式配了 0，
// 但保存时两者都还原成「不写这个键」，避免把默认值写成显式配置。
const temperatureDraft = ref<number>(0);

/**
 * 输入上限 = min(表单硬上限 3600, 上层生效值)。
 * 不这样做会出现「提示按 180s 生效、实际存下 600s」——列表摘要读的是存下的值，
 * 于是界面数字与真实下发对不上，而这正是本次要修的毛病。
 */
const timeoutCeiling = computed<number>(() => {
  const upper = props.upperTimeoutSeconds;
  if (upper === null || upper === undefined || !Number.isFinite(Number(upper)) || Number(upper) <= 0) {
    return MAX_TOOL_TIMEOUT;
  }
  return Math.min(MAX_TOOL_TIMEOUT, Math.round(Number(upper)));
});

const clampTimeout = (value: unknown): number | null => {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return null;
  return Math.min(timeoutCeiling.value, Math.max(1, Math.round(number)));
};

// 工具级只能收紧：最终生效值不会超过上层选定值
const effectiveTimeout = computed<{ value: number | null; source: "tool" | "upper" }>(() => {
  const own = clampTimeout(timeoutDraft.value);
  const upper = props.upperTimeoutSeconds;
  if (own === null) return { value: upper ?? null, source: "upper" };
  return { value: upper === null ? own : Math.min(own, upper), source: "tool" };
});

const isTimeoutClamped = computed(() => {
  // 必须拿「用户原样输入」和上限比：clampTimeout() 返回的已经是压到上限以内的值，
  // 用它来判断会恒为 false，琥珀色告警永远不出现，用户填 600 就被静默改成上限。
  const raw = Number(timeoutDraft.value);
  if (timeoutDraft.value === "" || !Number.isFinite(raw) || raw <= 0) return false;
  return raw > timeoutCeiling.value;
});

/** 上限的来历：是被上层值限住，还是被工具级表单硬上限 3600 限住。 */
const ceilingHint = computed(() => {
  const upper = Number(props.upperTimeoutSeconds);
  if (Number.isFinite(upper) && upper > timeoutCeiling.value) {
    return `范围 1–${timeoutCeiling.value} 秒（工具级单次上限 ${MAX_TOOL_TIMEOUT}s，上层 ${upper}s 更大）`;
  }
  return `范围 1–${timeoutCeiling.value} 秒（等于上层生效值，工具级只能更小）`;
});

const temperatureValue = computed<number>(() => Number(temperatureDraft.value || 0));

watch(() => props.model, (val) => {
  if (!val) return;
  localConfig.value = {
    ...props.config,
    name: props.toolName,
    description_override: props.config.description_override ?? "",
  };
  // 历史脏数据里可能存着 0/负数，当作「没配」展示，而不是在输入框里摆一个非法值
  const existing = Number(localConfig.value.timeout_seconds);
  timeoutDraft.value =
    Number.isFinite(existing) && existing > 0 ? String(Math.round(existing)) : "";
  const rawTemperature = props.config.temperature;
  temperatureDraft.value =
    rawTemperature === null || rawTemperature === undefined ? 0 : Number(rawTemperature);
});

const handleSave = () => {
  const normalized = clampTimeout(timeoutDraft.value);
  const next: ToolConfig = { ...localConfig.value, name: props.toolName };

  // 只写「真正设过」的键：留空保存等于恢复未配置，避免把默认值(温度 0)写成显式配置，
  // 否则列表里所有工具都会因为「键存在」而被判定成配置过。
  if (normalized === null) {
    delete next.timeout_seconds;
  } else {
    next.timeout_seconds = normalized;
  }

  if (props.supportsInternalLlm && Number(temperatureDraft.value) > 0) {
    next.temperature = Number(temperatureDraft.value);
  } else {
    // 纯函数工具不保留温度配置，历史遗留值一并清掉
    delete next.temperature;
  }

  const description = String(localConfig.value.description_override ?? "").trim();
  if (description) {
    next.description_override = description;
  } else {
    delete next.description_override;
  }

  delete next.model_name;

  emit("save", next);
  emit("update:model", false);
};

const clearTimeout = () => {
  timeoutDraft.value = "";
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
          <!-- Timeout -->
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1 flex items-center justify-between">
              <span>执行超时 (Execute Timeout)</span>
              <span class="text-primary font-mono text-xs">
                {{ effectiveTimeout.value === null ? "跟随上层" : `${effectiveTimeout.value}s` }}
              </span>
            </label>
            <div class="flex items-stretch overflow-hidden rounded-lg border border-gray-300 focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/20">
              <input
                v-model="timeoutDraft"
                type="number"
                min="1"
                :max="timeoutCeiling"
                step="1"
                :disabled="readonly"
                placeholder="留空 = 跟随上层"
                class="min-w-0 flex-1 px-3 py-2 text-sm outline-none disabled:bg-gray-50 disabled:text-gray-400"
              />
              <span class="grid place-items-center border-l border-gray-200 bg-gray-50 px-3 text-xs font-semibold text-gray-500">秒</span>
              <button
                v-if="!readonly"
                type="button"
                class="border-l border-gray-200 bg-white px-3 text-[11px] font-bold text-gray-400 hover:bg-gray-50 hover:text-gray-600"
                @click="clearTimeout"
              >清空</button>
            </div>
            <p class="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-gray-500">
              <span
                class="rounded-full px-2 py-0.5 text-[10px] font-bold"
                :class="effectiveTimeout.source === 'tool' ? 'bg-primary/10 text-primary' : 'bg-gray-100 text-gray-500'"
              >{{ effectiveTimeout.source === "tool" ? "工具级" : "上层" }}</span>
              <span v-if="effectiveTimeout.value !== null">生效 {{ effectiveTimeout.value }}s</span>
              <span v-else>上层也未设置时使用系统默认 180s</span>
              <span v-if="effectiveTimeout.source === 'upper'">· 来源：{{ upperTimeoutSource }}</span>
            </p>
            <p v-if="isTimeoutClamped" class="mt-1.5 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[11px] leading-relaxed text-amber-700">
              工具级只能收紧、不能放宽：上限 {{ timeoutCeiling }}s，保存后按 {{ effectiveTimeout.value }}s 生效。
            </p>
            <p v-else class="mt-1.5 text-[10px] leading-relaxed text-gray-400">
              {{ ceilingHint }}。留空则继承{{ upperTimeoutSource }}。
            </p>
          </div>

          <!-- Temperature -->
          <div :class="supportsInternalLlm ? '' : 'opacity-70'">
            <label class="block text-sm font-medium text-gray-700 mb-1 flex items-center justify-between">
              <span>温度 (Temperature)</span>
              <span class="font-mono text-xs" :class="supportsInternalLlm ? 'text-primary' : 'text-gray-400'">
                {{ supportsInternalLlm ? temperatureValue.toFixed(1) : "不适用" }}
              </span>
            </label>
            <input
              v-model.number="temperatureDraft"
              type="range"
              min="0"
              max="2"
              step="0.1"
              :disabled="readonly || !supportsInternalLlm"
              class="w-full accent-primary disabled:opacity-50"
            />
            <div class="flex justify-between text-[10px] text-gray-400 mt-1">
              <span>精确</span><span>平衡</span><span>发散</span>
            </div>
            <p v-if="!supportsInternalLlm" class="mt-1.5 rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1.5 text-[11px] leading-relaxed text-gray-500">
              <b>该工具执行时不调用模型</b>，温度对它没有任何作用，因此不可编辑。只有内部自己发起 LLM 调用的工具（如 read_image）才能配置温度。
            </p>
            <p v-else class="mt-1.5 text-[10px] leading-relaxed text-gray-400">
              该工具会调用多模态模型解析图片，温度用于这次内部采样。默认 0.0（最精确），调回 0.0 保存即恢复默认。
            </p>
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
            class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm outline-none focus:ring-2 focus:ring-primary disabled:bg-gray-50 disabled:text-gray-400"
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
