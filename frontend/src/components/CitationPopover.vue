<script setup lang="ts">
import { computed, onUnmounted, watch, ref } from "vue";

export interface CitationItem {
  id?: string | number;
  chunk_id?: string;
  doc_name?: string;
  content?: string;
  similarity?: number;
}

const props = defineProps<{
  visible: boolean;
  citation: CitationItem | null;
  anchorRect: DOMRect | null;
  anchorEl?: HTMLElement | null;
}>();

const emit = defineEmits<{
  (e: "close"): void;
  (e: "copy", content: string): void;
}>();

const copyState = ref<"idle" | "success" | "error">("idle");
let copyResetTimer: ReturnType<typeof setTimeout> | null = null;

const resetCopyState = () => {
  copyState.value = "idle";
  if (copyResetTimer) {
    clearTimeout(copyResetTimer);
    copyResetTimer = null;
  }
};

const handleCopy = async () => {
  const text = props.citation?.content || "";
  if (!text.trim()) {
    copyState.value = "error";
    copyResetTimer = setTimeout(resetCopyState, 2000);
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    copyState.value = "success";
    emit("copy", text);
    if (copyResetTimer) clearTimeout(copyResetTimer);
    copyResetTimer = setTimeout(resetCopyState, 2000);
  } catch (err) {
    console.error("Failed to copy citation:", err);
    copyState.value = "error";
    copyResetTimer = setTimeout(resetCopyState, 2000);
  }
};

const POPOVER_WIDTH = 380;
const POPOVER_MAX_HEIGHT = 320;
const GAP = 8;
const VIEWPORT_PADDING = 12;

const layout = ref({
  top: 0,
  left: 0,
  width: POPOVER_WIDTH,
  maxHeight: POPOVER_MAX_HEIGHT,
  placement: "bottom" as "bottom" | "top",
});

/** 滚动重定位时驱动箭头样式刷新 */
const positionTick = ref(0);

const getAnchorRect = (): DOMRect | null => {
  if (props.anchorEl?.isConnected) {
    return props.anchorEl.getBoundingClientRect();
  }
  return props.anchorRect;
};

const updatePosition = () => {
  const rect = getAnchorRect();
  if (!rect) return;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const width = Math.min(POPOVER_WIDTH, vw - VIEWPORT_PADDING * 2);

  const spaceBelow = vh - rect.bottom - VIEWPORT_PADDING;
  const spaceAbove = rect.top - VIEWPORT_PADDING;
  const showBelow = spaceBelow >= 140 || spaceBelow >= spaceAbove;

  let maxHeight = Math.min(
    POPOVER_MAX_HEIGHT,
    (showBelow ? spaceBelow : spaceAbove) - GAP
  );
  maxHeight = Math.max(120, maxHeight);

  let top = showBelow ? rect.bottom + GAP : rect.top - maxHeight - GAP;
  top = Math.max(VIEWPORT_PADDING, Math.min(top, vh - maxHeight - VIEWPORT_PADDING));

  let left = rect.left + rect.width / 2 - width / 2;
  left = Math.max(VIEWPORT_PADDING, Math.min(left, vw - width - VIEWPORT_PADDING));

  layout.value = {
    top,
    left,
    width,
    maxHeight,
    placement: showBelow ? "bottom" : "top",
  };
  positionTick.value++;
};

const popoverStyle = computed(() => ({
  top: `${layout.value.top}px`,
  left: `${layout.value.left}px`,
  width: `${layout.value.width}px`,
  maxHeight: `${layout.value.maxHeight}px`,
}));

const arrowStyle = computed(() => {
  positionTick.value;
  const rect = getAnchorRect();
  if (!rect) return {};
  const anchorCenter = rect.left + rect.width / 2;
  const arrowLeft = Math.max(16, Math.min(anchorCenter - layout.value.left, layout.value.width - 16));
  return layout.value.placement === "bottom"
    ? { left: `${arrowLeft}px`, top: "-6px" }
    : { left: `${arrowLeft}px`, bottom: "-6px", transform: "rotate(180deg)" };
});

const onDocClick = (e: MouseEvent) => {
  if (!props.visible) return;
  const el = e.target as HTMLElement;
  if (
    el.closest(".citation-popover") ||
    el.closest("[data-cite-id]") ||
    el.closest(".citation-chip")
  ) {
    return;
  }
  emit("close");
};

const onKeyDown = (e: KeyboardEvent) => {
  if (!props.visible) return;
  if (e.key === "Escape") emit("close");
};

/** 外层页面滚动时跟随锚点重定位；弹层内部滚动不处理 */
const onScroll = (e: Event) => {
  if (!props.visible) return;
  const target = e.target as HTMLElement | null;
  if (target?.closest?.(".citation-popover")) return;
  updatePosition();
};

const bindListeners = () => {
  document.addEventListener("click", onDocClick, true);
  document.addEventListener("keydown", onKeyDown);
  window.addEventListener("resize", updatePosition);
  window.addEventListener("scroll", onScroll, true);
};

const unbindListeners = () => {
  document.removeEventListener("click", onDocClick, true);
  document.removeEventListener("keydown", onKeyDown);
  window.removeEventListener("resize", updatePosition);
  window.removeEventListener("scroll", onScroll, true);
};

watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      updatePosition();
      bindListeners();
    } else {
      unbindListeners();
      resetCopyState();
    }
  }
);

watch(
  () => [props.anchorRect, props.anchorEl] as const,
  () => {
    if (props.visible) updatePosition();
  }
);

onUnmounted(() => {
  unbindListeners();
  resetCopyState();
});
</script>

<template>
  <Teleport to="body">
    <div
      v-if="visible && citation"
      class="citation-popover fixed z-[200] flex flex-col bg-white dark:bg-gray-800 rounded-xl shadow-[0_8px_30px_rgba(0,0,0,0.12)] border border-gray-200/90 dark:border-gray-700 overflow-hidden animate-fade-in-up"
      :style="popoverStyle"
      @click.stop
    >
      <div
        class="absolute w-3 h-3 bg-white dark:bg-gray-800 border-l border-t border-gray-200/90 dark:border-gray-700 rotate-45 pointer-events-none"
        :style="arrowStyle"
      />

      <div class="flex items-start justify-between gap-2 px-3 py-2.5 border-b border-gray-100 dark:border-gray-700/60 bg-blue-50/40 dark:bg-blue-900/10 flex-shrink-0">
        <div class="flex items-start gap-2 min-w-0">
          <div class="p-1.5 bg-blue-100/80 dark:bg-blue-900/30 rounded-lg text-blue-600 flex-shrink-0">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
          </div>
          <div class="min-w-0">
            <h4 class="text-xs font-bold text-gray-800 dark:text-gray-100 truncate">{{ citation.doc_name || "引用来源" }}</h4>
            <div class="flex items-center gap-1.5 mt-0.5 text-[10px] text-gray-400 font-mono">
              <span v-if="citation.similarity != null">匹配 {{ (citation.similarity * 100).toFixed(0) }}%</span>
              <span v-if="citation.chunk_id || citation.id" class="truncate">#{{ String(citation.chunk_id || citation.id).slice(-6) }}</span>
            </div>
          </div>
        </div>
        <button
          @click="emit('close')"
          class="p-1 rounded-md text-gray-400 hover:text-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 flex-shrink-0"
          title="关闭"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div
        class="citation-popover-body flex-1 min-h-0 overflow-y-auto overscroll-contain px-3 py-2.5 text-sm text-gray-600 dark:text-gray-300 leading-relaxed whitespace-pre-wrap custom-scrollbar"
        @wheel.stop
        @touchmove.stop
      >
        {{ citation.content }}
      </div>

      <div class="flex items-center justify-between gap-2 px-3 py-2 border-t border-gray-100 dark:border-gray-700/60 flex-shrink-0">
        <span
          v-if="copyState === 'success'"
          class="text-[10px] text-emerald-600 dark:text-emerald-400 font-medium"
        >已复制到剪贴板</span>
        <span
          v-else-if="copyState === 'error'"
          class="text-[10px] text-red-500 font-medium"
        >复制失败，请重试</span>
        <span v-else class="text-[10px] text-transparent select-none">.</span>
        <button
          @click="handleCopy"
          class="px-2.5 py-1 text-[11px] font-bold rounded-md transition-colors flex items-center gap-1"
          :class="copyState === 'success'
            ? 'text-emerald-600 bg-emerald-50 dark:bg-emerald-900/20'
            : copyState === 'error'
              ? 'text-red-500 bg-red-50 dark:bg-red-900/20'
              : 'text-gray-500 hover:text-primary hover:bg-gray-50 dark:hover:bg-gray-700/50'"
        >
          <svg
            v-if="copyState === 'success'"
            class="w-3 h-3"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7" />
          </svg>
          {{ copyState === 'success' ? '已复制' : copyState === 'error' ? '重试' : '复制' }}
        </button>
      </div>
    </div>
  </Teleport>
</template>
