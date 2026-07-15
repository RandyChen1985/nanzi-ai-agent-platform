<template>
  <div
    ref="chooserRoot"
    class="relative shrink-0"
    @mouseenter="cancelScheduledClose"
    @mouseleave="scheduleClose"
    @focusout="handleFocusOut"
    @keydown.esc="closeChooser"
  >
    <button
      type="button"
      class="flex items-center gap-1 rounded-md px-2 py-1 text-[10px] font-medium text-gray-500 transition-colors hover:bg-indigo-50 hover:text-indigo-600 dark:text-gray-400 dark:hover:bg-indigo-950/40 dark:hover:text-indigo-300"
      :aria-expanded="open"
      aria-haspopup="menu"
      @click="open = true"
    >
      <span aria-hidden="true">✨</span>
      <span>继续分析</span>
      <svg class="h-3 w-3 transition-transform" :class="{ 'rotate-180': open }" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m6 9 6 6 6-6" />
      </svg>
    </button>

    <template v-if="open">
      <div v-if="isMobile" class="fixed inset-0 z-[260] flex items-end bg-black/35 backdrop-blur-[1px]" @click.self="closeChooser">
        <div class="w-full rounded-t-2xl border-t border-gray-100 bg-white p-4 pb-[max(1rem,env(safe-area-inset-bottom))] shadow-2xl dark:border-gray-800 dark:bg-gray-900">
          <div class="mx-auto mb-3 h-1 w-10 rounded-full bg-gray-300 dark:bg-gray-600" />
          <div class="mb-2 flex items-center justify-between gap-3">
            <div>
              <h3 class="text-sm font-bold text-gray-800 dark:text-gray-100">继续分析</h3>
              <p class="mt-0.5 text-[11px] text-gray-400">基于刚才的数据选择下一步</p>
            </div>
            <button type="button" class="rounded-full p-2 text-gray-400 hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-800" aria-label="关闭继续分析" @click="closeChooser">
              <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18 18 6M6 6l12 12" /></svg>
            </button>
          </div>
          <div class="space-y-1">
            <button v-for="action in props.actions.slice(0, 4)" :key="action.id" type="button" class="group w-full rounded-xl px-3 py-2.5 text-left transition-colors hover:bg-indigo-50 dark:hover:bg-indigo-950/30" @click="selectAction(action)">
              <div class="text-sm font-semibold text-gray-700 group-hover:text-indigo-700 dark:text-gray-200 dark:group-hover:text-indigo-300">{{ action.label }}</div>
              <div v-if="action.description" class="mt-0.5 text-xs leading-relaxed text-gray-400">{{ action.description }}</div>
            </button>
          </div>
        </div>
      </div>

      <div v-else class="absolute bottom-full right-0 z-[80] mb-2 w-72 overflow-hidden rounded-xl border border-gray-200/80 bg-white/95 shadow-[0_14px_40px_rgba(15,23,42,0.16)] backdrop-blur-xl dark:border-gray-700 dark:bg-gray-900/95" role="menu">
        <div class="flex items-center justify-between gap-3 border-b border-gray-100 px-3 py-2.5 dark:border-gray-800">
          <div class="min-w-0">
            <div class="text-xs font-bold text-gray-800 dark:text-gray-100">继续分析</div>
            <div class="mt-0.5 truncate text-[10px] text-gray-400">选择一个推荐的分析方向</div>
          </div>
          <button type="button" class="shrink-0 rounded-md p-1 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-800 dark:hover:text-gray-200" aria-label="关闭继续分析" @click="closeChooser">
            <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18 18 6M6 6l12 12" /></svg>
          </button>
        </div>
        <div class="max-h-72 space-y-0.5 overflow-y-auto p-1.5">
          <button v-for="action in props.actions.slice(0, 4)" :key="action.id" type="button" class="group flex w-full items-start gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-indigo-50 focus:bg-indigo-50 focus:outline-none dark:hover:bg-indigo-950/30 dark:focus:bg-indigo-950/30" role="menuitem" @click="selectAction(action)">
            <span class="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-gray-100 text-[11px] text-gray-500 group-hover:bg-indigo-100 group-hover:text-indigo-600 dark:bg-gray-800 dark:text-gray-400 dark:group-hover:bg-indigo-950 dark:group-hover:text-indigo-300">{{ actionIcon(action.id) }}</span>
            <span class="min-w-0">
              <span class="block text-xs font-semibold text-gray-700 group-hover:text-indigo-700 dark:text-gray-200 dark:group-hover:text-indigo-300">{{ action.label }}</span>
              <span v-if="action.description" class="mt-0.5 block truncate text-[10px] text-gray-400">{{ action.description }}</span>
            </span>
          </button>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import type { ChatBIInsightAction } from "@/types/chatbiInsight";

const props = defineProps<{ actions: ChatBIInsightAction[]; isMobile: boolean }>();
const emit = defineEmits<{ (event: "select", query: string): void }>();
const chooserRoot = ref<HTMLElement | null>(null);
const open = ref(false);
let closeTimer: ReturnType<typeof setTimeout> | null = null;

const cancelScheduledClose = () => {
  if (closeTimer) clearTimeout(closeTimer);
  closeTimer = null;
};
const closeChooser = () => {
  cancelScheduledClose();
  open.value = false;
};
const scheduleClose = () => {
  if (props.isMobile) return;
  cancelScheduledClose();
  closeTimer = setTimeout(closeChooser, 180);
};
const handleFocusOut = (event: FocusEvent) => {
  const next = event.relatedTarget as Node | null;
  if (!next || !chooserRoot.value?.contains(next)) scheduleClose();
};
const handleDocumentPointerDown = (event: PointerEvent) => {
  if (open.value && !chooserRoot.value?.contains(event.target as Node)) closeChooser();
};
const actionIcon = (id: string) => ({
  trend: "↗", ranking: "#", contribution: "%", anomaly: "!", visualize: "▥", summary: "≡",
}[id] || "→");
const selectAction = (action: ChatBIInsightAction) => {
  closeChooser();
  emit('select', action.query);
};

onMounted(() => document.addEventListener("pointerdown", handleDocumentPointerDown));
onUnmounted(() => {
  cancelScheduledClose();
  document.removeEventListener("pointerdown", handleDocumentPointerDown);
});
</script>
