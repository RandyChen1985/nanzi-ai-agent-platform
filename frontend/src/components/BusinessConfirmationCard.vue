<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue';
import type {
  BusinessConfirmationField,
  BusinessConfirmationState,
} from '@/utils/businessConfirmation';

const props = defineProps<{
  payload: BusinessConfirmationState;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  (
    event: 'submit',
    payload: { confirmed: boolean; fields: BusinessConfirmationField[] },
  ): void;
}>();

const draftFields = reactive<BusinessConfirmationField[]>([]);
const expanded = ref(props.payload.status === 'pending');

function syncDraft(fields: BusinessConfirmationField[]) {
  draftFields.splice(
    0,
    draftFields.length,
    ...fields.map((field) => ({
      key: field.key,
      label: field.label,
      value: field.value ?? '',
      editable: field.editable !== false,
      value_type: field.value_type || 'string',
    })),
  );
}

watch(
  () => props.payload,
  (next) => {
    syncDraft(next?.fields || []);
    expanded.value = next?.status === 'pending';
  },
  { immediate: true, deep: true },
);

watch(
  () => props.payload.status,
  (nextStatus, prevStatus) => {
    if (nextStatus !== 'pending' && prevStatus === 'pending') {
      expanded.value = false;
    } else if (nextStatus === 'pending' && prevStatus !== 'pending') {
      expanded.value = true;
    }
  },
);

// 点击后到父组件把 status 翻成 submitted 之间有一次网络往返：这段窗口里卡片看起来
// 还是 pending，用户连点两下就会发两条【业务确认】回执，后端按两条消息各跑一轮。
// 用本地 submitting 立刻锁住，并在 status 变化或超时后解锁（失败时不至于永久卡死）。
const submitting = ref(false);
let submittingTimer: ReturnType<typeof setTimeout> | null = null;

watch(
  () => props.payload.status,
  (next) => {
    if (next !== 'pending' && submitting.value) {
      submitting.value = false;
      if (submittingTimer) {
        clearTimeout(submittingTimer);
        submittingTimer = null;
      }
    }
  },
);

onBeforeUnmount(() => {
  if (submittingTimer) clearTimeout(submittingTimer);
  submittingTimer = null;
});

const locked = computed(
  () =>
    submitting.value ||
    props.disabled ||
    props.payload.status === 'submitted' ||
    props.payload.status === 'stale',
);

const statusLabel = computed(() => {
  if (props.payload.status === 'submitted') {
    return props.payload.decision === 'cancelled' ? '已取消' : '已确定';
  }
  if (props.payload.status === 'stale') return '已失效';
  return '待确认';
});

function toggleExpand() {
  expanded.value = !expanded.value;
}

function onBooleanChange(field: BusinessConfirmationField, checked: boolean) {
  field.value = checked;
}

function submit(confirmed: boolean) {
  if (locked.value) return;
  submitting.value = true;
  if (submittingTimer) clearTimeout(submittingTimer);
  // 兜底解锁：请求失败/父组件没改 status 时不能让卡片永久不可点。
  submittingTimer = setTimeout(() => {
    submitting.value = false;
    submittingTimer = null;
  }, 8000);
  emit('submit', {
    confirmed,
    fields: draftFields.map((field) => ({ ...field })),
  });
}
</script>

<template>
  <section
    class="mt-2.5 w-full min-w-0 max-w-[42rem] lg:max-w-[48rem] 2xl:max-w-[52rem] rounded-xl border border-sky-200/90 bg-sky-50/60 p-2.5 sm:p-3 text-xs text-primary-dark shadow-sm dark:border-primary-dark/40 dark:bg-primary-dark/20 dark:text-sky-100 transition-all"
    role="group"
    :aria-label="payload.title || '业务数据确认'"
  >
    <div class="flex items-start gap-2 sm:gap-2.5">
      <div
        class="mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-md bg-sky-100 text-primary-dark dark:bg-primary-dark/50 dark:text-sky-300"
      >
        <svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      </div>
      <div class="min-w-0 flex-1">
        <!-- Card Header with Toggle -->
        <div
          class="flex items-center justify-between gap-2 cursor-pointer select-none group"
          :title="expanded ? '点击收起' : '点击展开'"
          @click="toggleExpand"
        >
          <div class="flex min-w-0 flex-1 items-center gap-2">
            <div class="truncate font-bold text-primary-dark dark:text-sky-100 shrink-0">
              {{ payload.title || '请确认以下信息' }}
            </div>
            <!-- Collapsed summary preview -->
            <span
              v-if="!expanded && payload.summary"
              class="min-w-0 flex-1 truncate text-[11px] text-primary-dark/70 dark:text-sky-300/70 font-normal"
            >
              {{ payload.summary }}
            </span>
          </div>
          <div class="flex items-center gap-1.5 shrink-0">
            <span
              class="rounded-full px-2 py-0.5 text-[10px] font-bold uppercase"
              :class="{
                'bg-sky-100 text-primary-dark dark:bg-primary-dark/40 dark:text-sky-300': payload.status === 'pending',
                'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300': payload.status === 'submitted' && payload.decision !== 'cancelled',
                'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300': payload.status === 'submitted' && payload.decision === 'cancelled',
                'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300': payload.status === 'stale',
              }"
            >
              {{ statusLabel }}
            </span>
            <!-- Fold / Unfold Arrow -->
            <button
              type="button"
              class="flex h-5 w-5 items-center justify-center rounded text-primary hover:text-primary-dark dark:text-primary dark:hover:text-sky-200 transition-colors"
              :aria-label="expanded ? '收起确认' : '展开确认'"
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
        <div v-show="expanded" class="mt-2">
          <p
            v-if="payload.summary"
            class="break-words text-primary-dark/80 dark:text-sky-200/80"
          >
            {{ payload.summary }}
          </p>

          <div class="mt-3 overflow-hidden rounded-md border border-sky-100 bg-white/80 dark:border-primary-dark/40 dark:bg-gray-950/30">
            <table class="w-full text-left">
              <thead class="bg-sky-50/80 text-[10px] uppercase tracking-wide text-primary-dark dark:bg-primary-dark/40 dark:text-sky-300">
                <tr>
                  <th class="px-2 py-1.5 font-semibold">字段名</th>
                  <th class="px-2 py-1.5 font-semibold">字段值</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="field in draftFields"
                  :key="field.key || field.label"
                  class="border-t border-sky-100 dark:border-primary-dark/40"
                >
                  <td class="w-[36%] px-2 py-1.5 align-top font-medium text-primary-dark dark:text-sky-100">
                    {{ field.label }}
                  </td>
                  <td class="px-2 py-1.5 align-top">
                    <input
                      v-if="field.value_type === 'boolean'"
                      type="checkbox"
                      class="mt-0.5 h-3.5 w-3.5 rounded border-sky-300 text-primary focus:ring-primary"
                      :checked="Boolean(field.value)"
                      :disabled="locked || field.editable === false"
                      @change="onBooleanChange(field, ($event.target as HTMLInputElement).checked)"
                    />
                    <textarea
                      v-else-if="field.value_type === 'text'"
                      v-model="field.value as string"
                      rows="2"
                      class="w-full rounded border border-sky-100 bg-white px-2 py-1 text-xs text-gray-800 outline-none focus:border-primary disabled:cursor-not-allowed disabled:bg-gray-50 dark:border-primary-dark/50 dark:bg-gray-900 dark:text-gray-100 dark:disabled:bg-gray-900/60"
                      :disabled="locked || field.editable === false"
                    />
                    <input
                      v-else
                      v-model="field.value as string | number"
                      :type="field.value_type === 'number' ? 'number' : 'text'"
                      class="w-full rounded border border-sky-100 bg-white px-2 py-1 text-xs text-gray-800 outline-none focus:border-primary disabled:cursor-not-allowed disabled:bg-gray-50 dark:border-primary-dark/50 dark:bg-gray-900 dark:text-gray-100 dark:disabled:bg-gray-900/60"
                      :disabled="locked || field.editable === false"
                    />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <p
            v-if="payload.risk_note"
            class="mt-2 text-[11px] leading-5 text-primary-dark/80 dark:text-sky-300/80"
          >
            风险提示：{{ payload.risk_note }}
          </p>

          <div v-if="payload.status === 'pending'" class="mt-3 flex items-center gap-2">
            <button
              type="button"
              class="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-bold text-white shadow-sm hover:bg-primary-dark disabled:cursor-not-allowed disabled:opacity-60"
              :disabled="locked"
              @click="submit(true)"
            >
              {{ payload.confirm_label || '确定' }}
            </button>
            <button
              type="button"
              class="inline-flex items-center gap-1.5 rounded-md border border-gray-200 bg-white px-3 py-1.5 text-xs font-bold text-gray-700 shadow-sm hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
              :disabled="locked"
              @click="submit(false)"
            >
              {{ payload.cancel_label || '取消' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
