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
  /** 上层的工具超时默认值（版本级或全局级）；工具级留空时继承它，配了则以工具级为准 */
  upperTimeoutSeconds?: number | null;
  /** 上层超时的来源描述，用于「跟随上层」的说明文案 */
  upperTimeoutSource?: string;
  /**
   * 整轮看门狗时长（秒）。>0 时它是**真正的天花板**：从用户发消息到收尾，超过它整轮
   * 会被强制取消并释放会话锁——工具级配得比它大也跑不完。0 表示看门狗已关闭。
   */
  producerHardTimeoutSeconds?: number | null;
}>(), {
  readonly: false,
  supportsInternalLlm: false,
  upperTimeoutSeconds: null,
  producerHardTimeoutSeconds: null,
  upperTimeoutSource: "智能体版本配置",
});

const emit = defineEmits(["update:model", "save"]);

// 工具级允许配置的上限，与版本级同口径（1 天）。上层值只是「默认值」，不是天花板：
// 某些工具天生就慢，硬压到全局 180s/3600s 会逼用户把整个版本级调大，连带影响所有工具。
const MAX_TOOL_TIMEOUT = 86400;

const localConfig = ref<ToolConfig>({ ...props.config });
const timeoutDraft = ref<string>("");
// 温度单独用草稿值：0 与「未配置」在视觉上相同，用 null 表示未配置、0 表示显式配了 0，
// 但保存时两者都还原成「不写这个键」，避免把默认值写成显式配置。
const temperatureDraft = ref<number>(0);

/** 上层生效值（版本级优先、否则全局级）；只用于「默认值」与越界提示，不再钳制输入。 */
const upperTimeout = computed<number | null>(() => {
  const upper = Number(props.upperTimeoutSeconds);
  return Number.isFinite(upper) && upper > 0 ? Math.round(upper) : null;
});

const clampTimeout = (value: unknown): number | null => {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return null;
  // 只受表单硬上限约束：填多大就生效多大，不再被上层截断
  return Math.min(MAX_TOOL_TIMEOUT, Math.max(1, Math.round(number)));
};

/** 生效值即用户填的值；留空才回落到上层。 */
const effectiveTimeout = computed<{ value: number | null; source: "tool" | "upper" }>(() => {
  const own = clampTimeout(timeoutDraft.value);
  if (own === null) return { value: upperTimeout.value, source: "upper" };
  return { value: own, source: "tool" };
});

/** 超过上层值：只提示，不阻止保存（用户可能就是需要给这个慢工具更长的预算）。 */
const isAboveUpper = computed(() => {
  const own = clampTimeout(timeoutDraft.value);
  if (own === null || upperTimeout.value === null) return false;
  return own > upperTimeout.value;
});

/** 超过表单硬上限：这时会被截断，必须说清楚。 */
const isOverMax = computed(() => {
  const raw = Number(timeoutDraft.value);
  if (timeoutDraft.value === "" || !Number.isFinite(raw) || raw <= 0) return false;
  return raw > MAX_TOOL_TIMEOUT;
});

/** 整轮看门狗（真正天花板）；0/null/非法值视为关闭。 */
const producerHardTimeout = computed<number | null>(() => {
  const value = Number(props.producerHardTimeoutSeconds);
  return Number.isFinite(value) && value > 0 ? Math.round(value) : null;
});

/** 工具级配置超过了整轮预算：跑不到，必须提示。 */
const isAboveProducer = computed(() => {
  const own = clampTimeout(timeoutDraft.value);
  if (own === null || producerHardTimeout.value === null) return false;
  return own > producerHardTimeout.value;
});

const rangeHint = computed(() =>
  `范围 1–${MAX_TOOL_TIMEOUT} 秒；留空则继承${props.upperTimeoutSource}`,
);

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
                :max="MAX_TOOL_TIMEOUT"
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
            <!-- 超过整轮看门狗：这是真正的天花板，配了也跑不完 -->
            <p v-if="isAboveProducer" class="mt-1.5 rounded-lg border border-amber-300 bg-amber-50 px-2.5 py-1.5 text-[11px] leading-relaxed text-amber-800">
              已超出整轮看门狗 {{ producerHardTimeout }}s：本轮会先被强制终止并释放会话，
              这个工具等不到自己超时。需要更久请先调大系统配置的看门狗时长。
            </p>
            <!-- 超过上层值：允许保存，但要说清"这个工具会比其它工具跑得久" -->
            <p v-else-if="isAboveUpper" class="mt-1.5 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[11px] leading-relaxed text-amber-700">
              已超出{{ upperTimeoutSource }}的 {{ upperTimeout }}s：该工具会独占更长运行时间，保存后按
              {{ effectiveTimeout.value }}s 生效（其它工具不受影响）。
            </p>
            <!-- 超过表单硬上限：这里是真的会被截断，必须说清最终值 -->
            <p v-if="isOverMax" class="mt-1.5 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[11px] leading-relaxed text-amber-700">
              单个工具最多 {{ MAX_TOOL_TIMEOUT }}s，超出部分会被截断，保存后按 {{ effectiveTimeout.value }}s 生效。
            </p>
            <p v-if="!isAboveUpper && !isOverMax && !isAboveProducer" class="mt-1.5 text-[10px] leading-relaxed text-gray-400">
              {{ rangeHint }}。
            </p>
            <!-- 整轮预算常驻展示：它是「工具级生效上限」之外的第二道约束，藏起来就会踩坑 -->
            <p v-if="producerHardTimeout !== null" class="mt-1 text-[10px] leading-relaxed text-gray-400">
              整轮看门狗 {{ producerHardTimeout }}s：从发消息到收尾的总时长上限（含模型调用与所有工具），
              超过它整轮会被强制终止。
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
