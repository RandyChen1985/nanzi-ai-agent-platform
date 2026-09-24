<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import { walkerDurationSeconds } from "@/utils/generatingWalkerSpeed";

/**
 * 生成态的「二进制拖尾小人」跑道。
 *
 * 纯装饰元素（`aria-hidden`），不承载任何语义：生成中提示的语义由旁边那行
 * 文案负责。这里只解决「等待太干巴、没有 AI 科技感」的观感问题。
 *
 * 全部是 CSS 动画，不使用 JS 定时器；JS 只在挂载与尺寸变化时量一次跑道宽度，
 * 用来把「每秒走多少像素」换算成单程时长，避免速度随宽度线性放大。
 */

const laneRef = ref<HTMLElement | null>(null);

// 首帧兜底：JS 量宽之前先用桌面端的典型时长，避免闪一下极速动画。
const durationSeconds = ref(walkerDurationSeconds(1100));

const laneStyle = computed<Record<string, string>>(() => ({
  "--gw-duration": `${(Math.round(durationSeconds.value * 10) / 10).toFixed(1)}s`,
}));

let resizeObserver: ResizeObserver | null = null;

const measureLane = () => {
  const element = laneRef.value;
  if (!element) return;
  durationSeconds.value = walkerDurationSeconds(element.clientWidth);
};

onMounted(() => {
  measureLane();
  if (typeof ResizeObserver === "undefined") return;
  resizeObserver = new ResizeObserver(measureLane);
  const element = laneRef.value;
  if (element) resizeObserver.observe(element);
});

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  resizeObserver = null;
});
</script>

<template>
  <div ref="laneRef" class="gw-lane" :style="laneStyle" aria-hidden="true">
    <!-- 网格独立成层，见下方 .gw-grid 的说明 -->
    <div class="gw-grid"></div>
    <!-- 向左流动的刻度：数据总线的暗示 -->
    <div class="gw-bus"></div>
    <div class="gw-walker">
      <!-- 身后的 0/1 拖尾：AI 正在吐 token 的视觉隐喻；停下时淡出 -->
      <span class="gw-bits"><i>0</i><i>1</i><i>1</i></span>
      <svg
        class="gw-creature"
        width="15"
        height="17"
        viewBox="0 0 15 17"
        fill="none"
      >
        <g class="gw-bob">
          <!-- 腿先画、身体后画，让腿的根部被身体盖住 -->
          <line
            class="gw-leg gw-leg-a"
            x1="6.2"
            y1="10.5"
            x2="5.6"
            y2="14.6"
            stroke="currentColor"
            stroke-width="1.6"
            stroke-linecap="round"
          />
          <line
            class="gw-leg gw-leg-b"
            x1="8.8"
            y1="10.5"
            x2="9.4"
            y2="14.6"
            stroke="currentColor"
            stroke-width="1.6"
            stroke-linecap="round"
          />
          <circle cx="7.5" cy="6.3" r="4.6" fill="currentColor" />
        </g>
      </svg>
    </div>
  </div>
</template>

<style scoped>
/*
  极淡网格：给跑道一个「数据地面」的底子。

  必须自成一层。CSS 的 opacity 是层叠相乘的——如果把淡度挂在跑道根节点上，
  整棵子树（含小人与 0/1）都会被一起压到 4%，子元素再声明 opacity: 1 也救不
  回来，动画会「几乎看不见」。
*/
.gw-grid {
  position: absolute;
  inset: 0;
  color: #9ca3af;
  background-image:
    linear-gradient(currentColor 1px, transparent 1px),
    linear-gradient(90deg, currentColor 1px, transparent 1px);
  background-size:
    100% 8px,
    8px 100%;
  opacity: 0.04;
}

.gw-lane {
  /* 兜底时长，JS 接管后由 inline style 覆盖 */
  --gw-duration: 24s;
  position: relative;
  height: 20px;
  overflow: hidden;
  border-radius: 4px;
}

.gw-bus {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 2px;
  background-image: repeating-linear-gradient(
    90deg,
    #9ca3af 0 3px,
    transparent 3px 9px
  );
  background-size: 36px 2px;
  opacity: 0.26;
  animation: gw-flow 1.1s linear infinite;
}

@keyframes gw-flow {
  from {
    background-position-x: 0;
  }
  to {
    background-position-x: -36px;
  }
}

.gw-walker {
  position: absolute;
  top: 1px;
  left: 0;
  display: flex;
  align-items: center;
  animation: gw-walk var(--gw-duration) linear infinite;
}

/* 一路走到底：中途不停顿、不摇摆（用户明确否掉了那个版本） */
@keyframes gw-walk {
  0% {
    left: 0;
    opacity: 0;
  }
  5% {
    opacity: 1;
  }
  92% {
    opacity: 1;
  }
  100% {
    /* max() 兜住窄屏：跑道比小人还窄时不能让 left 变成负数 */
    left: max(0px, calc(100% - 32px));
    opacity: 0;
  }
}

.gw-creature {
  flex: none;
  color: #9ca3af;
  opacity: 0.85;
}

.gw-bob {
  transform-box: view-box;
  animation: gw-bob 0.46s ease-in-out infinite;
}

@keyframes gw-bob {
  0%,
  100% {
    transform: translateY(0);
  }
  50% {
    transform: translateY(-0.8px);
  }
}

.gw-leg {
  transform-box: view-box;
  animation: gw-swing 0.46s ease-in-out infinite;
}

/* 两条腿反相摆动，才像走而不像蹦 */
.gw-leg-a {
  transform-origin: 6.2px 10.5px;
}

.gw-leg-b {
  transform-origin: 8.8px 10.5px;
  animation-delay: -0.23s;
}

@keyframes gw-swing {
  0%,
  100% {
    transform: rotate(-19deg);
  }
  50% {
    transform: rotate(19deg);
  }
}

.gw-bits {
  display: flex;
  gap: 1px;
  margin-right: 1px;
  flex: none;
}

.gw-bits i {
  /* 必须等宽，否则拖尾会边跑边抖 */
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-style: normal;
  font-size: 9px;
  line-height: 1;
  color: #9ca3af;
}

/* 越靠后越淡：形成「刚输出完」的渐隐感 */
.gw-bits i:nth-child(1) {
  opacity: 0.18;
}
.gw-bits i:nth-child(2) {
  opacity: 0.32;
}
.gw-bits i:nth-child(3) {
  opacity: 0.5;
}

/* 深色模式：颜色统一用 #9ca3af（深底上对比度足够），只需把网格提亮一档。
   这里必须写 scoped 形态的 `.dark .gw-grid`，**不能**写 `:global(.dark) .gw-grid`：
   `:global()` 会让整个选择器脱出作用域，Vue 编译后只剩一条**全局** `.dark{opacity:.07}`，
   它会命中页面上任何带 .dark 的元素——包括 EmbedChat 根容器与 `<html>`——把整个挂件
   压到 7% 不透明度（「深色下一片白雾、内容隐约可见」的真凶）。scoped 形态编译为
   `.dark .gw-grid[data-v-x]`，`.dark` 仍可匹配外部祖先。 */
.dark .gw-grid {
  opacity: 0.07;
}

/*
  无障碍兜底：系统偏好减少动效时整条跑道隐藏，退回原来的「三点 + 文案」。
  仓库内已有 5 处同样的约定，这里不能破例。
*/
@media (prefers-reduced-motion: reduce) {
  .gw-lane {
    display: none;
  }
}
</style>
