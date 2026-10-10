<script setup lang="ts">
import { computed } from "vue";
import { resolvePendingHint, type ProcessTimelineLogItem } from "@/utils/processTimeline";

const props = defineProps<{
  item: ProcessTimelineLogItem;
  /** 由父级统一的 500ms 心跳注入，避免每个挂起行各起一个定时器。 */
  now: number;
  /** 该行是否正在走右侧实时秒表（用于省略重复的「已等待 Ns ·」前缀）。 */
  isLiveTimer: boolean;
}>();

const hint = computed(() => resolvePendingHint(props.item, props.now, props.isLiveTimer));
const prefix = computed(() =>
  hint.value?.showElapsed ? `已等待 ${hint.value.elapsedSeconds}s · ` : "",
);
</script>

<template>
  <div
    v-if="hint"
    class="shrink-0 flex items-center gap-1.5 text-[10px] text-sky-600 dark:text-sky-400"
    aria-live="polite"
    aria-busy="true"
  >
    <span
      v-if="hint.indicator === 'bar'"
      class="workspace-prewarm-bar shrink-0"
      aria-hidden="true"
    ></span>
    <!-- 波形取色用 currentColor，颜色交给这里的 text-* 决定；
         深色用 Tailwind 的 dark: 变体而不是手写 .dark 选择器——后者在 scoped
         样式里会命中所有带 .dark 的祖先（同类坑见 GeneratingWalker.vue 注释）。 -->
    <span
      v-else
      class="pending-hint-wave shrink-0 text-slate-400 dark:text-slate-500"
      aria-hidden="true"
    >
      <i></i><i></i><i></i>
    </span>
    <span
      class="truncate max-w-[140px] sm:max-w-[320px] md:max-w-none"
      :title="prefix + hint.label"
    >{{ prefix }}{{ hint.label }}</span>
  </div>
</template>

<style scoped>
/* 挂起步骤的不确定进度条：滑块往复扫动，示意仍在推进。
   样式必须与唯一使用者同址：父组件是 scoped，作用不到本组件内部的元素。 */
.workspace-prewarm-bar {
  position: relative;
  overflow: hidden;
  width: 3.5rem;
  height: 0.25rem;
  border-radius: 9999px;
  background: rgba(14, 165, 233, 0.15);
}
.workspace-prewarm-bar::after {
  content: "";
  position: absolute;
  top: 0;
  bottom: 0;
  left: -40%;
  width: 40%;
  border-radius: 9999px;
  background: rgba(14, 165, 233, 0.75);
  animation: workspace-prewarm-slide 1.2s ease-in-out infinite;
}
@keyframes workspace-prewarm-slide {
  0% { left: -40%; }
  100% { left: 100%; }
}

/* 挂起步骤的波形：三根竖条此起彼伏，示意后台仍在推进。
   与沙箱那条横向亮蓝进度条在形状与颜色上都区分开。
   起伏用 scaleY 而不是 height：只走合成层，不每帧触发布局。 */
.pending-hint-wave {
  display: inline-flex;
  align-items: flex-end;
  gap: 2px;
  height: 0.6875rem;
}
.pending-hint-wave i {
  width: 2px;
  height: 100%;
  border-radius: 1px;
  background: currentColor;
  transform-origin: bottom;
  transform: scaleY(0.27);
  animation: pending-hint-wave 1.1s ease-in-out infinite;
}
.pending-hint-wave i:nth-child(2) { animation-delay: 0.18s; }
.pending-hint-wave i:nth-child(3) { animation-delay: 0.36s; }
@keyframes pending-hint-wave {
  0%, 100% { transform: scaleY(0.27); opacity: 0.45; }
  50% { transform: scaleY(1); opacity: 1; }
}

@media (prefers-reduced-motion: reduce) {
  .workspace-prewarm-bar::after {
    animation: none;
    left: 0;
    width: 100%;
    opacity: 0.5;
  }
  /* 静止时给一个中等高度，否则会停在最矮的基线帧上 */
  .pending-hint-wave i {
    animation: none;
    transform: scaleY(0.72);
    opacity: 0.6;
  }
}
</style>
