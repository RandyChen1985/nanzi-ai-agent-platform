<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import type { UserQuestionState } from "@/utils/userQuestion";

const props = defineProps<{
  payload: UserQuestionState;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  (event: "submit", payload: { selectedOptionIds: string[]; customInput: string; cancelled: boolean }): void;
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

const statusLabel = computed(() => {
  if (props.payload.status === "submitted") return "已提交";
  if (props.payload.status === "cancelled") return "已取消";
  if (props.payload.status === "stale") return "已失效";
  return "等待回答";
});

function toggleOption(id: string) {
  if (locked.value) return;
  if (props.payload.is_multi_select) {
    selectedOptionIds.value = selectedOptionIds.value.includes(id)
      ? selectedOptionIds.value.filter((item) => item !== id)
      : [...selectedOptionIds.value, id];
    return;
  }
  selectedOptionIds.value = [id];
}

function toggleExpand() {
  expanded.value = !expanded.value;
}

async function submit() {
  if (locked.value || isSubmitting.value) return;
  isSubmitting.value = true;
  emit("submit", {
    selectedOptionIds: [...selectedOptionIds.value],
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
    class="mt-2.5 w-full min-w-0 max-w-[42rem] lg:max-w-[48rem] 2xl:max-w-[52rem] rounded-xl border border-violet-200/90 bg-violet-50/60 p-2.5 sm:p-3 text-xs text-violet-950 shadow-sm dark:border-violet-900/40 dark:bg-violet-950/20 dark:text-violet-100 transition-all"
    role="group"
    :aria-label="payload.question || 'AI 提问'"
  >
    <div class="flex items-start gap-2 sm:gap-2.5">
      <div class="mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-md bg-violet-100 text-violet-700 dark:bg-violet-900/50 dark:text-violet-300">
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
            <span class="font-bold text-violet-900 dark:text-violet-100 text-xs shrink-0">需要你的补充</span>
            <!-- Collapsed summary preview -->
            <span
              v-if="!expanded && payload.question"
              class="min-w-0 flex-1 truncate text-[11px] text-violet-700/70 dark:text-violet-300/70 font-normal"
            >
              {{ payload.question }}
            </span>
          </div>
          <div class="flex items-center gap-1.5 shrink-0">
            <span
              class="rounded-full px-2 py-0.5 text-[10px] font-medium leading-none"
              :class="{
                'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300': payload.status === 'pending',
                'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300': payload.status === 'submitted',
                'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300': payload.status === 'cancelled',
                'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300': payload.status === 'stale',
              }"
            >{{ statusLabel }}</span>
            <!-- Fold / Unfold Arrow -->
            <button
              type="button"
              class="flex h-5 w-5 items-center justify-center rounded text-violet-500 hover:text-violet-800 dark:text-violet-400 dark:hover:text-violet-200 transition-colors"
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
          <!-- Question & Context -->
          <div>
            <p class="break-words text-xs sm:text-[13px] font-semibold text-violet-950 dark:text-violet-100 leading-snug">
              {{ payload.question }}
            </p>
            <p v-if="payload.context" class="mt-0.5 break-words text-[11px] text-violet-800/80 dark:text-violet-200/80 leading-normal">
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
              :class="selectedOptionIds.includes(option.id)
                ? 'border-violet-500 bg-white dark:bg-violet-950/40 shadow-xs ring-1 ring-violet-400/40'
                : 'border-violet-100/90 bg-white/75 hover:bg-white hover:border-violet-300 dark:border-violet-900/30 dark:bg-gray-950/30 dark:hover:bg-gray-950/50 dark:hover:border-violet-800'"
              :disabled="locked"
              @click="toggleOption(option.id)"
            >
              <!-- Indicator: rounded-full for single, rounded-[3px] for multi-select -->
              <span
                class="mt-0.5 flex h-3.5 w-3.5 flex-shrink-0 items-center justify-center border transition-colors"
                :class="[
                  payload.is_multi_select ? 'rounded-[3px]' : 'rounded-full',
                  selectedOptionIds.includes(option.id)
                    ? 'border-violet-600 bg-violet-600 dark:border-violet-500 dark:bg-violet-500'
                    : 'border-violet-300 bg-transparent group-hover/opt:border-violet-400 dark:border-violet-700'
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
                    ? 'text-violet-950 font-semibold dark:text-violet-100'
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
            class="w-full rounded-lg border border-violet-200/80 bg-white/90 px-2.5 py-1.5 text-xs text-gray-800 outline-none transition-all placeholder:text-gray-400 focus:border-violet-500 focus:bg-white focus:ring-1 focus:ring-violet-400 disabled:cursor-not-allowed disabled:bg-gray-50 dark:border-violet-900/50 dark:bg-gray-900/80 dark:text-gray-100 dark:focus:border-violet-500"
            :disabled="locked"
            placeholder="也可以补充说明（可选）"
          />

          <!-- Action buttons -->
          <div v-if="payload.status === 'pending'" class="flex items-center gap-2 pt-0.5">
            <button
              type="button"
              class="inline-flex items-center rounded-md bg-violet-600 px-3 py-1 text-xs font-semibold text-white shadow-xs hover:bg-violet-700 active:scale-95 disabled:cursor-not-allowed disabled:opacity-60 transition-all"
              :disabled="locked || isSubmitting || (!selectedOptionIds.length && !customInput.trim())"
              @click="submit"
            >
              提交回答并继续
            </button>
            <button
              type="button"
              class="inline-flex items-center rounded-md border border-violet-200 bg-white/80 px-2.5 py-1 text-xs font-medium text-violet-700 hover:bg-violet-100 active:scale-95 disabled:cursor-not-allowed disabled:opacity-60 dark:border-violet-800 dark:bg-gray-900/40 dark:text-violet-200 dark:hover:bg-violet-900/40 transition-all"
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
