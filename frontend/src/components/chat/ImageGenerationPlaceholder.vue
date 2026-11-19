<script setup lang="ts">
/**
 * 生图 / 改图的「正在出图」占位骨架。
 *
 * 只在工具调用仍在进行时出现，图落地即消失——它不参与布局计算之外的一切：
 * 不发请求、不看配置、不影响超时。尺寸比例与最终图片一致，所以图标出现时
 * 整段对话不会跳动。
 *
 * 视觉是「白底 + 蓝色点阵 + 一圈亮环向内汇聚」（原型
 * `prototypes/2026-11-20-image-skeleton-dotmatrix.html` 的 P4）。中间区域整块留给点阵，
 * 不加卡片也不加描边——底色铺满、文字退到角上，就是为了不再像一块灰色补丁。
 *
 * 三条自我约束（原型阶段定的）：
 * 1. **不编百分比**。后端没有真实进度，只给阶段文案 + 已用秒数 + 不确定的跳点；
 * 2. **比例必须对**。宽高比取自请求的 size，给不出时用方形；
 * 3. **秒表不进无障碍播报**。500ms 一次的读屏会盖掉真正要听的东西。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import {
  imagePlaceholderDetailText,
  imagePlaceholderStageText,
  sizeToAspectRatio,
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

/**
 * 宽用三段取小：容器宽、420px 上限、以及「60vh 高时该多宽」。
 * 第三段是给竖图准备的——928×1664 若只按宽度铺满，手机上骨架会高到把输入框顶出屏幕。
 */
const frameStyle = computed(() => {
  const ratio = sizeToAspectRatio(props.spec?.size || "");
  const [rawWidth, rawHeight] = ratio.split("/").map((part) => Number(part.trim()));
  const width = rawWidth > 0 ? rawWidth : 1;
  const height = rawHeight > 0 ? rawHeight : 1;
  return {
    aspectRatio: ratio,
    width: `min(100%, 420px, calc(60vh * ${width} / ${height}))`,
  };
});
</script>

<template>
  <div v-if="spec" class="imgph-root mt-3" role="status" aria-live="polite" aria-busy="true">
    <div
      class="imgph-frame relative overflow-hidden rounded-xl border border-gray-200/80 bg-white [--imgph-dot:rgba(22,119,255,0.34)] [--imgph-dot-hi:rgba(22,119,255,0.95)] dark:border-gray-700 dark:bg-gray-900/60 dark:[--imgph-dot:rgba(96,165,250,0.32)] dark:[--imgph-dot-hi:rgba(96,165,250,0.95)]"
      :style="frameStyle"
    >
      <!-- 点阵底色：间距固定 14px，不随容器缩放，大图与缩略图密度感一致。 -->
      <div class="imgph-dots absolute inset-0" aria-hidden="true"></div>

      <!-- 汇聚亮环：两圈错开 1.7s，从外向内收——散落的点被收拢成形。 -->
      <div class="imgph-dot-hi imgph-gather absolute inset-0" aria-hidden="true"></div>
      <div class="imgph-dot-hi imgph-gather imgph-gather-alt absolute inset-0" aria-hidden="true"></div>

      <!-- 阶段文案退到左上角，中间完全留给视觉。 -->
      <div class="absolute left-2.5 top-2 z-10 max-w-[calc(100%-4.5rem)]">
        <div class="text-[11.5px] font-medium leading-tight text-slate-500 dark:text-slate-400">
          {{ stageText }}
        </div>
        <div v-if="detailText" class="mt-0.5 text-[10.5px] leading-tight text-slate-400 dark:text-slate-500">
          {{ detailText }}
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
