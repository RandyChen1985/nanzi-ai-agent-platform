<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import type { UserQuestionState } from "@/utils/userQuestion";

const props = defineProps<{
  payload: UserQuestionState;
  disabled?: boolean;
  /**
   * 消息正文已经用「任务进度 / 待你确认」小节展示过问题与进度。
   *
   * 此时卡片只留选项：否则同一段文字在正文一遍、卡片一遍，一段 200 多字的进度会
   * 在一屏里出现两次（见 userQuestion.ts 的 splitUserQuestionBody）。
   */
  summaryRendered?: boolean;
}>();

const emit = defineEmits<{
  (event: "submit", payload: { selectedOptionIds: string[]; customInput: string; cancelled: boolean }): void;
  /** 选项附带的页面动作；与选中/提交并行，不改变回执内容 */
  (event: "option-action", action: string): void;
}>();

const selectedOptionIds = ref<string[]>([]);
const customInput = ref("");
// 展开态默认跟随待回答状态；若组件被复用换到新的问题（question_id 变化）则整体重置，
// status 变化时再单独驱动展开/折叠，两个来源各管各的、互不覆盖。
const expanded = ref(props.payload.status === "pending");
// 提交/取消防重入：结合父组件同步写入的 status 一起兜底，避免快速双击重复触发 emit，
// 同时在下一次 nextTick 后复位，保证父组件异步失败时仍可重试而不卡死。
const isSubmitting = ref(false);

watch(
  () => props.payload.question_id,
  () => {
    // 同一实例换到新问题：重置选择、补充输入与展开态
    selectedOptionIds.value = [...(props.payload.selected_option_ids || [])];
    customInput.value = props.payload.custom_input || "";
    expanded.value = props.payload.status === "pending";
    isSubmitting.value = false;
  },
);

watch(
  () => props.payload.status,
  (nextStatus, prevStatus) => {
    if (nextStatus !== "pending" && prevStatus === "pending") {
      expanded.value = false;
    } else if (nextStatus === "pending" && prevStatus !== "pending") {
      expanded.value = true;
    }
    if (nextStatus !== "pending") {
      isSubmitting.value = false;
    }
  },
);

const locked = computed(
  () =>
    Boolean(props.disabled) ||
    props.payload.status === "submitted" ||
    props.payload.status === "cancelled" ||
    props.payload.status === "stale",
);

const sourceLabel = computed(() => {
  const display = String(props.payload.source_agent_display_name || "").trim();
  const raw = String(props.payload.source_agent_name || "").trim();
  return display || raw;
});

const statusLabel = computed(() => {
  if (props.payload.status === "submitted") return "已提交";
  if (props.payload.status === "cancelled") return "已取消";
  if (props.payload.status === "stale") return "已失效";
  return "等待回答";
});

function isOptionDisabled(id: string) {
  return props.payload.options.some((option) => option.id === id && option.disabled === true);
}

function toggleOption(id: string) {
  if (locked.value || isOptionDisabled(id)) return;
  const action = props.payload.options.find((option) => option.id === id)?.action;
  if (props.payload.is_multi_select) {
    const next = selectedOptionIds.value.includes(id)
      ? selectedOptionIds.value.filter((item) => item !== id)
      : [...selectedOptionIds.value, id];
    selectedOptionIds.value = next;
    // 取消选中时不该再触发动作，只有"选上"才算点它
    if (action && next.includes(id)) emit("option-action", action);
    return;
  }
  selectedOptionIds.value = [id];
  // 只是"顺手做一件事"，绝不替代选中与提交：回执照旧发出去，
  // 否则后端 pending_scope_gate 快照清不掉，后续轮次会当成还有待回答的卡。
  if (action) emit("option-action", action);
}

function toggleExpand() {
  expanded.value = !expanded.value;
}

async function submit() {
  if (locked.value || isSubmitting.value) return;
  isSubmitting.value = true;
  emit("submit", {
    // 剔除禁用项：多选场景下可能先选中、之后才因状态变化被置灰，
    // 带着它提交等于送出后端预期收不到的 id。
    selectedOptionIds: selectedOptionIds.value.filter((id) => !isOptionDisabled(id)),
    customInput: customInput.value.trim(),
    cancelled: false,
  });
  // 父组件同步提交时会把 status 写入 submitted，从而锁定卡片；
  // 若父组件为异步/失败回滚（status 仍为 pending），则在下一帧复位以允许重试。
  await nextTick();
  if (props.payload.status === "pending") {
    isSubmitting.value = false;
  }
}

async function cancel() {
  if (locked.value || isSubmitting.value) return;
  isSubmitting.value = true;
  emit("submit", {
    selectedOptionIds: [],
    customInput: "",
    cancelled: true,
  });
  await nextTick();
  if (props.payload.status === "pending") {
    isSubmitting.value = false;
  }
}
</script>

<template>
  <!-- UserQuestionCard is an AI-initiated question, not a business confirmation. -->
  <section
    class="mt-2.5 w-full min-w-0 max-w-[42rem] lg:max-w-[48rem] 2xl:max-w-[52rem] rounded-xl border border-gray-200 border-l-[3px] border-l-primary bg-white p-2.5 sm:p-3 text-xs text-gray-900 shadow-sm dark:border-gray-700 dark:border-l-primary dark:bg-gray-900/90 dark:text-gray-100 transition-all"
    role="group"
    :aria-label="props.summaryRendered ? '需要你的补充' : payload.question || 'AI 提问'"
  >
    <div class="flex items-start gap-2 sm:gap-2.5">
      <div class="mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary dark:bg-primary/20 dark:text-primary">
        <svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.25 9.75h7.5m-7.5 3h4.5m-8.25 7.5 2.16-4.32A8.25 8.25 0 1 1 12 20.25c-1.8 0-3.46-.58-4.8-1.57Z" />
        </svg>
      </div>
      <div class="min-w-0 flex-1">
        <!-- Card Header with Toggle -->
        <div
          class="flex items-center justify-between gap-2 cursor-pointer select-none group"
          :title="expanded ? '点击收起' : '点击展开'"
          @click="toggleExpand"
        >
          <div class="flex min-w-0 flex-1 items-center gap-1.5 sm:gap-2">
            <span class="font-semibold text-gray-900 dark:text-gray-100 text-xs shrink-0">需要你的补充</span>
            <!-- 子智能体发起的提问：标清来源，否则用户以为父智能体在问 -->
            <span
              v-if="sourceLabel"
              class="shrink-0 rounded-full border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-[10px] font-medium leading-none text-gray-600 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300"
              title="由子智能体发起，回答后会接着它的进度继续"
            >来自：{{ sourceLabel }}</span>
            <!-- Collapsed summary preview -->
            <span
              v-if="!expanded && payload.question && !props.summaryRendered"
              class="min-w-0 flex-1 truncate text-[11px] text-gray-500 dark:text-gray-400 font-normal"
            >
              {{ payload.question }}
            </span>
          </div>
          <div class="flex items-center gap-1.5 shrink-0">
            <span
              class="rounded-full px-2 py-0.5 text-[10px] font-medium leading-none"
              :class="{
                'bg-primary/10 text-primary dark:bg-primary/20 dark:text-blue-300': payload.status === 'pending',
                'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300': payload.status === 'submitted',
                'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300': payload.status === 'cancelled',
                'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300': payload.status === 'stale',
              }"
            >{{ statusLabel }}</span>
            <!-- Fold / Unfold Arrow -->
            <button
              type="button"
              class="flex h-5 w-5 items-center justify-center rounded text-gray-400 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors"
              :aria-label="expanded ? '收起提问' : '展开提问'"
              @click.stop="toggleExpand"
            >
              <svg
                class="h-3.5 w-3.5 transition-transform duration-200"
                :class="{ 'rotate-180': !expanded }"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m19 15-7-7-7 7" />
              </svg>
            </button>
          </div>
        </div>

        <!-- Expanded Content -->
        <div v-show="expanded" class="mt-2 space-y-2">
          <!-- Question & Context：正文已用「任务进度 / 待你确认」小节展示过时不再重复一遍 -->
          <div v-if="!props.summaryRendered">
            <p class="break-words text-xs sm:text-[13px] font-semibold text-gray-900 dark:text-gray-100 leading-snug">
              {{ payload.question }}
            </p>
            <p v-if="payload.context" class="mt-0.5 break-words text-[11px] text-gray-500 dark:text-gray-400 leading-normal">
              {{ payload.context }}
            </p>
          </div>

          <!-- Options list (compact) -->
          <div class="space-y-1.5">
            <button
              v-for="option in payload.options"
              :key="option.id"
              type="button"
              class="group/opt flex w-full items-start gap-2 rounded-lg border px-2.5 py-1.5 text-left transition-all"
              :class="option.disabled
                ? 'cursor-not-allowed border-dashed border-gray-200 bg-gray-50/70 opacity-60 dark:border-gray-800 dark:bg-gray-950/20'
                : selectedOptionIds.includes(option.id)
                  ? 'border-primary bg-primary/[0.04] dark:bg-primary/15 shadow-xs ring-1 ring-primary/25'
                  : 'border-gray-200 bg-white hover:bg-gray-50 hover:border-gray-300 dark:border-gray-700 dark:bg-gray-900/50 dark:hover:bg-gray-800/80 dark:hover:border-gray-600'"
              :disabled="locked || option.disabled"
              :title="option.disabled ? (option.description || '当前不可选') : undefined"
              @click="toggleOption(option.id)"
            >
              <!-- Indicator: rounded-full for single, rounded-[3px] for multi-select -->
              <span
                class="mt-0.5 flex h-3.5 w-3.5 flex-shrink-0 items-center justify-center border transition-colors"
                :class="[
                  payload.is_multi_select ? 'rounded-[3px]' : 'rounded-full',
                  !option.disabled && selectedOptionIds.includes(option.id)
                    ? 'border-primary bg-primary'
                    : 'border-gray-300 bg-transparent group-hover/opt:border-primary/60 dark:border-gray-600'
                ]"
              >
                <!-- Tick for multi, dot for single -->
                <svg
                  v-if="payload.is_multi_select && selectedOptionIds.includes(option.id)"
                  class="h-2.5 w-2.5 text-white"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
                </svg>
                <span
                  v-else-if="!payload.is_multi_select && selectedOptionIds.includes(option.id)"
                  class="h-1.5 w-1.5 rounded-full bg-white"
                />
              </span>

              <span class="min-w-0 flex-1 leading-snug">
                <span
                  class="block text-xs font-medium transition-colors"
                  :class="selectedOptionIds.includes(option.id)
                    ? 'text-gray-900 font-semibold dark:text-white'
                    : 'text-gray-800 dark:text-gray-200'"
                >
                  {{ option.label }}
                </span>
                <span
                  v-if="option.description"
                  class="mt-0.5 block text-[11px] text-gray-500 dark:text-gray-400 leading-tight"
                >
                  {{ option.description }}
                </span>
              </span>
            </button>
          </div>

          <!-- Custom input textarea (compact rows=1) -->
          <textarea
            v-if="payload.allow_custom_input"
            v-model="customInput"
            rows="1"
            class="w-full rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-xs text-gray-800 outline-none transition-all placeholder:text-gray-400 focus:border-primary focus:bg-white focus:ring-1 focus:ring-primary/30 disabled:cursor-not-allowed disabled:bg-gray-50 dark:border-gray-700 dark:bg-gray-900/80 dark:text-gray-100 dark:focus:border-primary"
            :disabled="locked"
            placeholder="也可以补充说明（可选）"
          />

          <!-- Action buttons -->
          <div v-if="payload.status === 'pending'" class="flex items-center gap-2 pt-0.5">
            <button
              type="button"
              class="inline-flex items-center rounded-md bg-primary px-3 py-1 text-xs font-semibold text-white shadow-xs hover:bg-primary-dark active:scale-95 disabled:cursor-not-allowed disabled:opacity-60 transition-all"
              :disabled="locked || isSubmitting || (!selectedOptionIds.length && !customInput.trim())"
              @click="submit"
            >
              提交回答并继续
            </button>
            <button
              type="button"
              class="inline-flex items-center rounded-md border border-gray-200 bg-white px-2.5 py-1 text-xs font-medium text-gray-600 hover:bg-gray-50 hover:text-gray-900 active:scale-95 disabled:cursor-not-allowed disabled:opacity-60 dark:border-gray-700 dark:bg-gray-900/40 dark:text-gray-300 dark:hover:bg-gray-800 transition-all"
              :disabled="locked || isSubmitting"
              @click="cancel"
            >
              取消提问
            </button>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
