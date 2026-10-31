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
  <!-- 容器只要 hint 非空就渲染：非沙箱项前 30 秒 label 为空串，
       此时这一行只有转圈、没有任何文字（标题本身已说明在做什么）。 -->
  <div
    v-if="hint"
    class="shrink-0 flex items-center gap-1.5 text-[10px] text-slate-500 dark:text-slate-400"
    aria-live="polite"
    aria-busy="true"
  >
    <span
      v-if="hint.indicator === 'bar'"
      class="workspace-prewarm-bar shrink-0"
      aria-hidden="true"
    ></span>
    <!-- 转圈取色用 currentColor，自带比文案再淡一档的 slate；
         文案本身也是浅灰（不用 sky 强调色），这行只是安抚提示，不该抢状态语义。
         深色用 Tailwind 的 dark: 变体而不是手写 .dark 选择器——后者在 scoped
         样式里会命中所有带 .dark 的祖先（同类坑见 GeneratingWalker.vue 注释）。 -->
    <span
      v-else
      class="pending-hint-spinner shrink-0 text-slate-400 dark:text-slate-500"
      aria-hidden="true"
    ></span>
    <span
      v-if="hint.label"
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
  left: 0;
  width: 40%;
  border-radius: 9999px;
  background: rgba(14, 165, 233, 0.75);
  transform: translateX(-100%);
  animation: workspace-prewarm-slide 1.2s ease-in-out infinite;
  will-change: transform;
  backface-visibility: hidden;
}
/* 关键：这里原先动画的是 `left`。`left` 属于布局属性，浏览器**每帧都要重新排版**
   整条时间线（reflow），且无法交给合成器。委派场景里主助手与子代理会同时挂起多个
   pending 步骤，每个都在跑这条动画，于是每帧多次 reflow——表现为「主线程 JS 只有
   24ms，但 Rendering 稳定几百毫秒、画面持续卡」。换成 transform 后位移在合成器完成，
   布局只在首次发生。位移量与原来的 left:-40% → left:100% 等价（元素宽 40%：
   -40%/40% = -100%，100%/40% = 250%）。 */
@keyframes workspace-prewarm-slide {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(250%); }
}

/* 挂起步骤的转圈：一个缺口圆环匀速旋转，示意后台仍在推进。
   与沙箱那条横向亮蓝进度条在形状与颜色上都区分开。
   尺寸沿用原波形的 0.6875rem，行高不因换动画而跳动。 */
.pending-hint-spinner {
  width: 0.6875rem;
  height: 0.6875rem;
  border-radius: 9999px;
  border: 1.5px solid currentColor;
  border-top-color: transparent;
  animation: pending-hint-spin 0.9s linear infinite;
}
@keyframes pending-hint-spin {
  to { transform: rotate(360deg); }
}

@media (prefers-reduced-motion: reduce) {
  .workspace-prewarm-bar::after {
    animation: none;
    /* 基础规则用 translateX(-100%) 把滑块放到轨道左侧外；降级态要铺满整条轨道，
       必须同时清掉这个位移，否则宽 100% 的元素会被整体移出可视区（进度条看起来消失了）。 */
    transform: none;
    left: 0;
    width: 100%;
    opacity: 0.5;
  }
  /* 静止时补成完整圆环：留缺口会像个坏掉的圆，反而更像出错了 */
  .pending-hint-spinner {
    animation: none;
    border-top-color: currentColor;
    opacity: 0.6;
  }
}
</style>
