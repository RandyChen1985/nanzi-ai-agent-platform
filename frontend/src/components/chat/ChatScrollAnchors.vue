<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";

/**
 * 消息区右侧的「第一条 / 最后一条」定位浮标。
 *
 * 为什么单独成组件：拖动逻辑要改 DOM 位置、要量父容器尺寸、要把位置写进
 * localStorage，塞进已经一万多行的 EmbedChat 只会更难维护。显示条件仍由父级
 * 决定（条数阈值 / 是否可滚动 / 用户是否刚滚动过），这里只管画和拖。
 */
const props = defineProps<{
  /** 父级判定「现在该不该出现」，包含条数、可滚动、滚动中三个条件。 */
  visible: boolean;
  /** 已经在顶部时传 false：不渲染「第一条」，避免点下去毫无反应。 */
  showTop: boolean;
  /** 已经在底部时传 false。 */
  showBottom: boolean;
}>();

const emit = defineEmits<{
  (e: "jump-first"): void;
  (e: "jump-last"): void;
  /** 悬停时父级要暂停淡出倒计时，否则鼠标刚要点击按钮就消失了。 */
  (e: "hover", hovered: boolean): void;
}>();

type AnchorPosition = { right: number; bottom: number };

const STORAGE_KEY = "yovole_scroll_anchor_pos";
/** 贴边到点不到，也给别的浮层留点空间。 */
const EDGE_GAP_PX = 8;
/** 位移超过这个距离就判定为「拖动」，不再触发跳转。 */
const DRAG_THRESHOLD_PX = 4;
/**
 * 默认落点：距底部 192px 左右。
 *
 * 再往下就会压到输入框内部右上角那组状态胶囊（反幻觉 / 上下文用量），
 * 实测「最后一条」按钮会被整个盖住，只剩上面的「第一条」露头。
 */
const DEFAULT_POSITION: AnchorPosition = { right: 12, bottom: 192 };

const rootRef = ref<HTMLDivElement | null>(null);
const dragging = ref(false);
const position = ref<AnchorPosition | null>(null);

const positionStyle = computed(() => {
  const p = position.value ?? DEFAULT_POSITION;
  return { right: `${p.right}px`, bottom: `${p.bottom}px` };
});

let dragOrigin: {
  pointerX: number;
  pointerY: number;
  right: number;
  bottom: number;
  maxRight: number;
  maxBottom: number;
} | null = null;
/** 拖动结束后紧跟的 click 要吞掉，否则拖完顺手就跳了一次。 */
let suppressClick = false;

const clamp = (value: number, min: number, max: number) =>
  Math.min(Math.max(value, min), max);

const readStoredPosition = (): AnchorPosition | null => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<AnchorPosition>;
    if (typeof parsed.right !== "number" || typeof parsed.bottom !== "number") return null;
    if (!Number.isFinite(parsed.right) || !Number.isFinite(parsed.bottom)) return null;
    return { right: parsed.right, bottom: parsed.bottom };
  } catch {
    return null;
  }
};

const persistPosition = (value: AnchorPosition) => {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  } catch {
    // 隐私模式 / 配额满：记不住位置不影响当下使用
  }
};

/** 定位父容器（浮标 absolute 的参照物），拿它算可拖范围。 */
const resolveHost = (): HTMLElement | null =>
  (rootRef.value?.offsetParent as HTMLElement | null) ?? null;

/** 窗口尺寸变化后，之前拖到右下的位置可能已经出界，拉回可视范围。 */
const ensureInBounds = () => {
  const root = rootRef.value;
  const host = resolveHost();
  if (!root || !host || !position.value) return;
  const rootRect = root.getBoundingClientRect();
  const hostRect = host.getBoundingClientRect();
  position.value = {
    right: clamp(
      position.value.right,
      EDGE_GAP_PX,
      Math.max(EDGE_GAP_PX, hostRect.width - rootRect.width - EDGE_GAP_PX),
    ),
    bottom: clamp(
      position.value.bottom,
      EDGE_GAP_PX,
      Math.max(EDGE_GAP_PX, hostRect.height - rootRect.height - EDGE_GAP_PX),
    ),
  };
};

const onPointerMove = (event: PointerEvent) => {
  if (!dragOrigin) return;
  const dx = event.clientX - dragOrigin.pointerX;
  const dy = event.clientY - dragOrigin.pointerY;
  if (Math.abs(dx) > DRAG_THRESHOLD_PX || Math.abs(dy) > DRAG_THRESHOLD_PX) {
    suppressClick = true;
  }
  position.value = {
    right: clamp(dragOrigin.right - dx, EDGE_GAP_PX, dragOrigin.maxRight),
    bottom: clamp(dragOrigin.bottom - dy, EDGE_GAP_PX, dragOrigin.maxBottom),
  };
};

const onPointerUp = () => {
  window.removeEventListener("pointermove", onPointerMove);
  dragging.value = false;
  dragOrigin = null;
  if (position.value) persistPosition(position.value);
  // click 在 pointerup 之后同步派发，隔一帧再解除抑制
  window.setTimeout(() => {
    suppressClick = false;
  }, 0);
};

const onPointerDown = (event: PointerEvent) => {
  if (event.pointerType === "mouse" && event.button !== 0) return;
  const root = rootRef.value;
  const host = resolveHost();
  if (!root || !host) return;
  const rootRect = root.getBoundingClientRect();
  const hostRect = host.getBoundingClientRect();
  const current = position.value ?? DEFAULT_POSITION;
  dragOrigin = {
    pointerX: event.clientX,
    pointerY: event.clientY,
    right: current.right,
    bottom: current.bottom,
    maxRight: Math.max(EDGE_GAP_PX, hostRect.width - rootRect.width - EDGE_GAP_PX),
    maxBottom: Math.max(EDGE_GAP_PX, hostRect.height - rootRect.height - EDGE_GAP_PX),
  };
  dragging.value = true;
  window.addEventListener("pointermove", onPointerMove);
  window.addEventListener("pointerup", onPointerUp, { once: true });
};

const jump = (kind: "first" | "last") => {
  if (suppressClick) return;
  emit(kind === "first" ? "jump-first" : "jump-last");
};

onMounted(() => {
  position.value = readStoredPosition();
});

// 浮标重新出现时校准一次：期间可能缩过窗口，旧坐标就落在容器外了
watch(
  () => props.visible,
  (visible) => {
    if (visible) window.requestAnimationFrame(ensureInBounds);
  },
);
</script>

<template>
  <div
    v-if="visible"
    ref="rootRef"
    class="absolute z-20 flex cursor-grab touch-none select-none flex-col gap-2 active:cursor-grabbing"
    :style="positionStyle"
    aria-label="消息定位浮标，可拖动调整位置"
    @pointerdown="onPointerDown"
    @mouseenter="emit('hover', true)"
    @mouseleave="emit('hover', false)"
  >
    <button
      v-if="showTop"
      type="button"
      class="group relative flex h-8 w-8 items-center justify-center rounded-full border border-gray-200/60 bg-white/60 text-gray-400 opacity-65 transition-all duration-150 hover:border-primary/30 hover:text-primary hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 dark:border-gray-700/60 dark:bg-gray-800/60 dark:text-gray-500"
      aria-label="回到第一条消息"
      title="第一条"
      @click="jump('first')"
    >
      <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7" /></svg>
      <span class="pointer-events-none absolute right-full mr-2 whitespace-nowrap rounded bg-gray-800/85 px-1.5 py-0.5 text-[10px] font-semibold text-white opacity-0 transition-opacity group-hover:opacity-100 dark:bg-gray-700/90">第一条</span>
    </button>
    <button
      v-if="showBottom"
      type="button"
      class="group relative flex h-8 w-8 items-center justify-center rounded-full border border-gray-200/60 bg-white/60 text-gray-400 opacity-65 transition-all duration-150 hover:border-primary/30 hover:text-primary hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 dark:border-gray-700/60 dark:bg-gray-800/60 dark:text-gray-500"
      aria-label="回到最后一条消息"
      title="最后一条"
      @click="jump('last')"
    >
      <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12l7 7 7-7" /></svg>
      <span class="pointer-events-none absolute right-full mr-2 whitespace-nowrap rounded bg-gray-800/85 px-1.5 py-0.5 text-[10px] font-semibold text-white opacity-0 transition-opacity group-hover:opacity-100 dark:bg-gray-700/90">最后一条</span>
    </button>
  </div>
</template>
