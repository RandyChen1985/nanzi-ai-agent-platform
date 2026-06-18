<template>
  <div
    v-show="modelValue"
    :class="[
      'z-50',
      pinned && isMobile
        ? 'fixed inset-x-0 bottom-0 max-w-full flex pointer-events-none'
        : pinned
          ? 'fixed inset-y-0 right-0 max-w-full flex pointer-events-none'
          : 'fixed inset-0 overflow-hidden',
    ]"
  >
    <transition
      v-if="!pinned"
      enter-active-class="ease-out duration-300"
      enter-from-class="opacity-0"
      enter-to-class="opacity-100"
      leave-active-class="ease-in duration-200"
      leave-from-class="opacity-100"
      leave-to-class="opacity-0"
    >
      <div
        v-show="modelValue"
        class="absolute inset-0 bg-gray-500/30 backdrop-blur-xs transition-opacity"
        @click="closeDrawer"
      />
    </transition>

    <div
      :class="[
        pinned
          ? isMobile
            ? 'w-full flex pointer-events-auto'
            : 'h-full flex pointer-events-auto'
          : isMobile
            ? 'absolute inset-x-0 bottom-0 flex justify-center'
            : 'absolute inset-y-0 right-0 pl-0 sm:pl-10 max-w-full flex',
      ]"
    >
      <transition
        enter-active-class="transform transition ease-in-out duration-300"
        :enter-from-class="sheetEnterFrom"
        enter-to-class="translate-x-0 translate-y-0"
        leave-active-class="transform transition ease-in-out duration-300"
        leave-from-class="translate-x-0 translate-y-0"
        :leave-to-class="sheetLeaveTo"
      >
        <div
          v-show="modelValue"
          :class="[
            'bg-white dark:bg-gray-900 shadow-2xl flex flex-col relative z-10 pb-[env(safe-area-inset-bottom,0px)]',
            isMobile
              ? 'w-full max-w-none rounded-t-2xl border-t border-gray-200 dark:border-gray-800'
              : 'w-screen max-w-[min(100vw,28rem)] h-full border-l border-gray-200 dark:border-gray-800',
            isMobile && pinned ? 'max-h-[min(58vh,100%)]' : '',
            isMobile && !pinned ? 'max-h-[min(85vh,100%)]' : '',
          ]"
        >
          <div class="px-4 py-3 sm:py-4 border-b border-gray-150 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-800/20 flex items-center justify-between gap-2 pt-[max(0.75rem,env(safe-area-inset-top,0px))]">
            <span class="text-sm font-bold text-gray-900 dark:text-gray-100 flex items-center gap-1.5 select-none min-w-0">
              <svg class="w-4 h-4 text-blue-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2H6a2 2 0 01-2-2v-4zM14 16a2 2 0 012-2h2a2 2 0 012 2v4a2 2 0 01-2 2h-2a2 2 0 01-2-2v-4z" />
              </svg>
              <span class="truncate">数据门户导航</span>
              <span
                v-if="pinned"
                class="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wide text-blue-600 bg-blue-50 border border-blue-100 dark:text-blue-300 dark:bg-blue-500/10 dark:border-blue-500/20"
              >
                已钉住
              </span>
            </span>
            <div class="flex items-center gap-2 flex-shrink-0">
              <label
                class="hidden sm:flex items-center gap-1.5 text-[10px] text-gray-500 dark:text-gray-400 cursor-pointer select-none whitespace-nowrap"
                title="开启后点击问题不会关闭抽屉，可连续提问（仅桌面端生效）"
              >
                <input
                  v-model="keepOpenOnQuestion"
                  type="checkbox"
                  class="rounded border-gray-300 text-primary focus:ring-primary/30"
                />
                提问后保持
              </label>
              <button
                type="button"
                class="inline-flex text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 p-1 rounded-md hover:bg-gray-150 dark:hover:bg-gray-800 transition-colors"
                :class="{ 'text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-500/10': pinned }"
                :title="pinButtonTitle"
                @click="pinned = !pinned"
              >
                <svg v-if="pinned" class="h-5 w-5" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M16 12V4h1a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-1v-2h1V6h-1v6h-2zM9.707 14.707a1 1 0 0 1-1.414 0l-2.829-2.829a1 1 0 0 1 1.414-1.414L9 12.586V4h2v8.586l1.707-1.707a1 1 0 0 1 1.414 1.414l-2.829 2.829z" />
                </svg>
                <svg v-else class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 12V4h1a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-1v-2h1V6h-1v6h-2M9.707 14.707a1 1 0 0 1-1.414 0l-2.829-2.829a1 1 0 0 1 1.414-1.414L9 12.586V4h2v8.586l1.707-1.707a1 1 0 0 1 1.414 1.414l-2.829 2.829z" />
                </svg>
              </button>
              <button
                type="button"
                class="text-gray-400 hover:text-gray-500 dark:hover:text-gray-300 p-1 rounded-md hover:bg-gray-150 dark:hover:bg-gray-800 transition-colors"
                title="关闭 (Esc)"
                @click="closeDrawer"
              >
                <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          </div>
          <div class="flex-1 overflow-y-auto p-3 sm:p-4 bg-white dark:bg-gray-900/60 min-h-0">
            <DatasetCapabilityMenu
              :payload="payload || { groups: [] }"
              :initial-loading="initialLoading"
              :background-refreshing="backgroundRefreshing"
              @quick-question="(query) => emit('quick-question', query)"
              @record-question-click="(p) => emit('record-question-click', p)"
              @clear-question-click="(p) => emit('clear-question-click', p)"
              @refresh="emit('refresh')"
            />
          </div>
        </div>
      </transition>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from "vue";
import DatasetCapabilityMenu from "@/components/chatbi/DatasetCapabilityMenu.vue";

const modelValue = defineModel<boolean>({ default: false });
const keepOpenOnQuestion = defineModel<boolean>("keepOpenOnQuestion", { default: false });
const pinned = defineModel<boolean>("pinned", { default: false });

defineProps<{
  payload: Record<string, unknown> | null;
  initialLoading?: boolean;
  backgroundRefreshing?: boolean;
}>();

const emit = defineEmits<{
  (event: "quick-question", query: string): void;
  (event: "record-question-click", payload: { query: string; label?: string; group_id?: string }): void;
  (event: "clear-question-click", payload: { query: string }): void;
  (event: "refresh"): void;
}>();

const isMobile = ref(false);
let mobileMq: MediaQueryList | null = null;

const syncMobile = () => {
  isMobile.value = !!mobileMq?.matches;
};

onMounted(() => {
  mobileMq = window.matchMedia("(max-width: 639px)");
  syncMobile();
  mobileMq.addEventListener("change", syncMobile);
});

onUnmounted(() => {
  mobileMq?.removeEventListener("change", syncMobile);
});

const sheetEnterFrom = computed(() => (isMobile.value ? "translate-y-full" : "translate-x-full"));
const sheetLeaveTo = computed(() => (isMobile.value ? "translate-y-full" : "translate-x-full"));

const pinButtonTitle = computed(() => {
  if (pinned.value) {
    return isMobile.value ? "取消钉住（恢复全屏抽屉）" : "取消钉住（恢复遮罩模式）";
  }
  return isMobile.value
    ? "钉住底部抽屉（去掉遮罩，可继续浏览聊天）"
    : "钉住侧栏（去掉遮罩，可继续浏览聊天）";
});

const closeDrawer = () => {
  modelValue.value = false;
};
</script>
