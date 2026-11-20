<script setup lang="ts">
/**
 * 生图 / 改图的「正在出图」占位骨架。
 *
 * 只在工具调用仍在进行时出现，图落地即消失——它不参与布局计算之外的一切：
 * 不发请求、不看配置、不影响超时。视觉沿用点阵 + 汇聚亮环（原型
 * `prototypes/2026-11-20-image-skeleton-dotmatrix.html` 的 P4），尺度按
 * `prototypes/2026-11-20-image-skeleton-compact.html` 的 V1 收成一条矮横幅。
 *
 * 为什么不再按出图比例定高（2026-11-20 改，用户提「高度减半、宽度增加」）：
 * 1. 旧算法把宽度三段取小（容器宽 / 420px 上限 / 按视口高度百分比反算），方形兜底时
 *    桌面就是 420×420，占掉近半屏，而消息流里的成品图被
 *    `.markdown-body img { max-height: 320px }` 压到最高 320px——骨架比成品还大，
 *    图落地时是「缩一下」，反而更难看；
 * 2. 「高度减半」与「宽度增加」在「框必须与成品同比例」的前提下数学上矛盾（只能二选一），
 *    而比例本来也只是尽力而为：改图没传 size 时前端根本不知道原图比例，兜底画的是方形。
 * 于是比例改成**信息**而不是**尺寸**：框固定一档高度、宽度放到 560px，框内左侧画一个
 * 按目标比例缩放的空框（「幽灵框」），竖图窄、横图宽，一眼看得出这次要出什么形状。
 *
 * 三条自我约束（原型阶段定的，改造后仍成立）：
 * 1. **不编百分比**。后端没有真实进度，只给阶段文案 + 已用秒数 + 不确定的跳点；
 * 2. **比例信息不许丢**。幽灵框按请求的 size 画，给不出时写「?」，不假装方形；
 * 3. **秒表不进无障碍播报**。500ms 一次的读屏会盖掉真正要听的东西。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import {
  imagePlaceholderDetailText,
  imagePlaceholderStageText,
  sizeToAspectRatio,
  sizeToRatioLabel,
  type ImagePlaceholderSpec,
} from "@/utils/imageGenerationPlaceholder";

const props = defineProps<{
  /** 为 null 时整块不渲染（历史消息、工具已结束都走这条）。 */
  spec: ImagePlaceholderSpec | null;
}>();

/**
 * 挂载时刻：时间线没给 `started_at` 时用它兜底，误差只有一个渲染帧。
 * 用 ref 而不是常量，是因为同一条消息可能连着跑第二次出图（失败重试 / 多轮对话）。
 */
const mountedAt = ref(Date.now());
const now = ref(Date.now());
let ticker: number | undefined;

onMounted(() => {
  ticker = window.setInterval(() => {
    now.value = Date.now();
  }, 500);
});

onBeforeUnmount(() => {
  if (ticker !== undefined) window.clearInterval(ticker);
});

watch(
  () => props.spec,
  () => {
    mountedAt.value = Date.now();
    now.value = Date.now();
  },
);

const startedAtMs = computed(() => props.spec?.startedAtMs ?? mountedAt.value);
const elapsedMs = computed(() => Math.max(0, now.value - startedAtMs.value));
const elapsedText = computed(() => `${(elapsedMs.value / 1000).toFixed(1)}s`);

const stageText = computed(() =>
  props.spec ? imagePlaceholderStageText(props.spec, elapsedMs.value) : "",
);
const detailText = computed(() =>
  props.spec ? imagePlaceholderDetailText(props.spec) : "",
);

/** 幽灵框的形状：来自请求尺寸，给不出就是方形（1 / 1，label 会写成「?」）。 */
const ghostRatio = computed(() => sizeToAspectRatio(props.spec?.size || ""));
const ratioLabel = computed(() => (props.spec ? sizeToRatioLabel(props.spec.size) : ""));
const ghostTitle = computed(() =>
  ratioLabel.value ? `目标比例 ${ratioLabel.value}` : "比例未知（改图跟随原图）",
);
</script>

<template>
  <div v-if="spec" class="imgph-root mt-3" role="status" aria-live="polite" aria-busy="true">
    <div
      class="imgph-band relative overflow-hidden rounded-xl border border-gray-200/80 bg-white [--imgph-dot:rgba(22,119,255,0.34)] [--imgph-dot-hi:rgba(22,119,255,0.95)] [--imgph-ghost:rgba(22,119,255,0.45)] [--imgph-ghost-fg:rgba(22,119,255,0.75)] dark:border-gray-700 dark:bg-gray-900/60 dark:[--imgph-dot:rgba(96,165,250,0.32)] dark:[--imgph-dot-hi:rgba(96,165,250,0.95)] dark:[--imgph-ghost:rgba(96,165,250,0.45)] dark:[--imgph-ghost-fg:rgba(96,165,250,0.8)]"
    >
      <!-- 点阵底色：间距固定 14px，不随容器缩放，大图与缩略图密度感一致。 -->
      <div class="imgph-dots absolute inset-0" aria-hidden="true"></div>

      <!-- 汇聚亮环：两圈错开 1.7s，从外向内收——散落的点被收拢成形。 -->
      <div class="imgph-dot-hi imgph-gather absolute inset-0" aria-hidden="true"></div>
      <div class="imgph-dot-hi imgph-gather imgph-gather-alt absolute inset-0" aria-hidden="true"></div>

      <!-- 幽灵框 + 一句话说明：框承载比例，文字承载阶段与尺寸。
           文字块只占剩余宽度（flex-1 + truncate），窄屏上宁可省略号也不换行——
           两行文案在 105px 高的横幅里会把点阵顶满，反而不像「在出图」。
           `pb-6` 是给右下角徽标留的净空：徽标绝对定位，早先靠给文字加 `pr-14` 避让，
           结果 390px 屏上文案被挤成两行——改成把文字整体抬上去，几何上就不可能重叠。 -->
      <div class="imgph-inner relative z-10 flex h-full items-center gap-3 px-3.5 pb-6">
        <div
          class="imgph-ghost flex-none"
          :style="{ aspectRatio: ghostRatio }"
          :title="ghostTitle"
          aria-hidden="true"
        >
          <span>{{ ratioLabel || "?" }}</span>
        </div>
        <div class="min-w-0 flex-1">
          <div
            class="truncate text-[12.5px] font-semibold leading-tight text-slate-600 dark:text-slate-300"
          >
            {{ stageText }}
          </div>
          <div
            v-if="detailText"
            class="mt-0.5 truncate text-[11px] leading-tight text-slate-400 dark:text-slate-500"
          >
            {{ detailText }}
          </div>
        </div>
      </div>

      <!-- 右下角徽标：跳点表达「在动」，秒表表达「多久」，两个都真实。 -->
      <div
        class="absolute bottom-2 right-2 z-10 inline-flex items-center gap-1.5 rounded-full border border-gray-200/80 bg-white px-2.5 py-[3px] text-[11.5px] font-semibold tabular-nums text-primary shadow-sm dark:border-gray-700 dark:bg-gray-800"
      >
        <span class="imgph-udots inline-flex items-center gap-[2px]" aria-hidden="true">
          <i></i><i></i><i></i>
        </span>
        <!-- 秒表每 500ms 变一次，读屏会被刷屏：只给人看。 -->
        <span aria-hidden="true">{{ elapsedText }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 矮横幅：宽度最多 560px（桌面气泡基本铺满），高度按屏宽分两档。
 *
 * 高度刻意与出图比例**无关**：按比例定高就是「方形 420px 占掉半屏」的老问题。
 * `28vw` 让手机自动落到 ~105px，`clamp` 上下限保证 320px 小屏和桌面都不过分。
 * 也刻意不再按视口高度反算宽度——那个兜底是为了「竖图别把输入框顶出屏幕」，
 * 现在高度本来就矮，这个约束不存在了。 */
.imgph-band {
  width: min(100%, 560px);
  height: clamp(104px, 28vw, 132px);
}

/* 幽灵框：按目标比例画一个空框，高度吃满 72%、宽度由比例推出来。
 *
 * 为什么需要它：框改成矮横幅后就不再「和成品同形状」了，如果连比例提示也一起丢掉，
 * 用户就分不清这次要出的是竖图还是横图（只能去读下面那行 928x1664）。
 *
 * 窄屏要收得更狠（见下面的 media query）：390px 屏上横幅只有 290px 宽，
 * 16:9 的空框按 72% 高画出来会占掉一半，右侧文案被挤成「正在绘/制」两行。 */
.imgph-ghost {
  height: 72%;
  max-width: 58%;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  padding-bottom: 3px;
  border: 1.5px dashed var(--imgph-ghost, rgba(22, 119, 255, 0.45));
  border-radius: 8px;
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.03em;
  color: var(--imgph-ghost-fg, rgba(22, 119, 255, 0.75));
}

@media (max-width: 639px) {
  .imgph-ghost {
    height: 64%;
    max-width: 44%;
    font-size: 9px;
  }
}

/* 点阵底：中心不特别提亮，只是整体极淡地呼吸，避免"在闪"的错觉。 */
.imgph-dots {
  background-image: radial-gradient(
    circle,
    var(--imgph-dot, rgba(22, 119, 255, 0.34)) 1px,
    transparent 1.5px
  );
  background-size: 14px 14px;
  background-position: -7px -7px;
  animation: imgph-dot-pulse 3.6s ease-in-out infinite;
}

@keyframes imgph-dot-pulse {
  0%,
  100% {
    opacity: 0.75;
  }
  50% {
    opacity: 1;
  }
}

/* 亮点阵层：只有被遮罩圈到的区域才显出来，用来做汇聚的环带。 */
.imgph-dot-hi {
  background-image: radial-gradient(
    circle,
    var(--imgph-dot-hi, rgba(22, 119, 255, 0.95)) 1.15px,
    transparent 1.7px
  );
  background-size: 14px 14px;
  background-position: -7px -7px;
}

.imgph-gather {
  transform-origin: 50% 50%;
  -webkit-mask-image: radial-gradient(
    circle at 50% 50%,
    transparent 0%,
    transparent 26%,
    #000 42%,
    transparent 62%
  );
  mask-image: radial-gradient(
    circle at 50% 50%,
    transparent 0%,
    transparent 26%,
    #000 42%,
    transparent 62%
  );
  animation: imgph-gather-in 3.4s cubic-bezier(0.4, 0, 0.2, 1) infinite;
}

.imgph-gather-alt {
  animation-delay: 1.7s;
}

@keyframes imgph-gather-in {
  0% {
    transform: scale(1.9);
    opacity: 0;
  }
  18% {
    opacity: 0.9;
  }
  100% {
    transform: scale(0.35);
    opacity: 0;
  }
}

/* 徽标里的三个跳点：不确定性的表达，不承诺任何进度。 */
.imgph-udots i {
  display: block;
  width: 3px;
  height: 3px;
  border-radius: 9999px;
  background: currentColor;
  opacity: 0.28;
  animation: imgph-udot 1.15s ease-in-out infinite;
}

.imgph-udots i:nth-child(2) {
  animation-delay: 0.16s;
}

.imgph-udots i:nth-child(3) {
  animation-delay: 0.32s;
}

@keyframes imgph-udot {
  0%,
  100% {
    opacity: 0.28;
    transform: translateY(0);
  }
  50% {
    opacity: 1;
    transform: translateY(-1px);
  }
}

/* 关掉动效偏好下只留静态点阵：点阵本身仍然说明「这里在出图」，只是不再汇聚。 */
@media (prefers-reduced-motion: reduce) {
  .imgph-dots {
    animation: none;
    opacity: 0.85;
  }
  .imgph-gather {
    animation: none;
    opacity: 0.75;
    transform: scale(0.85);
  }
  .imgph-gather-alt {
    opacity: 0;
  }
  .imgph-udots i {
    animation: none;
    opacity: 0.75;
  }
}
</style>
