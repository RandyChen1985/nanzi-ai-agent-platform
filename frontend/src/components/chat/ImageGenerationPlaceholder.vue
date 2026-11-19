<script setup lang="ts">
/**
 * 生图 / 改图的「正在出图」占位骨架。
 *
 * 只在工具调用仍在进行时出现，图落地即消失——它不参与布局计算之外的一切：
 * 不发请求、不看配置、不影响超时。尺寸比例与最终图片一致，所以图标出现时
 * 整段对话不会跳动。
 *
 * 三条自我约束（原型阶段定的）：
 * 1. **不编百分比**。后端没有真实进度，只给阶段文案 + 已用秒数 + 不确定进度条；
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
    <div class="imgph-frame relative overflow-hidden rounded-xl border border-gray-200 dark:border-gray-700" :style="frameStyle">
      <!-- 骨架本体：静止底色 + 一条横扫的高光。 -->
      <div
        class="imgph-skel absolute inset-0 bg-gray-100 [--imgph-hi:rgba(255,255,255,0.78)] dark:bg-gray-700/60 dark:[--imgph-hi:rgba(255,255,255,0.10)]"
        aria-hidden="true"
      ></div>

      <div class="relative z-10 flex h-full w-full items-center justify-center p-3">
        <div class="rounded-lg border border-gray-200 bg-white/85 px-3 py-1.5 text-center shadow-sm backdrop-blur-sm dark:border-gray-600 dark:bg-gray-800/85">
          <div class="flex items-center justify-center gap-1.5 text-xs font-semibold text-gray-700 dark:text-gray-200">
            <span>{{ stageText }}</span>
            <!-- 秒表每 500ms 变一次，读屏会被刷屏：只给人看。 -->
            <span class="font-medium tabular-nums text-gray-400 dark:text-gray-500" aria-hidden="true">
              {{ elapsedText }}
            </span>
          </div>
          <div class="mt-0.5 text-[11px] text-gray-500 dark:text-gray-400">{{ detailText }}</div>
        </div>
      </div>

      <!-- 不确定进度条：只表达「在动」，不表达「到哪了」。 -->
      <div class="absolute inset-x-0 bottom-0 z-10 h-0.5 overflow-hidden bg-gray-900/5 dark:bg-white/10" aria-hidden="true">
        <i class="imgph-bar block h-full w-[38%] rounded-sm bg-primary"></i>
      </div>
    </div>
  </div>
</template>

<style scoped>
.imgph-skel::after {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(
    100deg,
    transparent 18%,
    var(--imgph-hi, rgba(255, 255, 255, 0.78)) 50%,
    transparent 82%
  );
  transform: translateX(-100%);
  animation: imgph-shimmer 1.7s ease-in-out infinite;
}

@keyframes imgph-shimmer {
  100% {
    transform: translateX(100%);
  }
}

.imgph-bar {
  animation: imgph-indeterminate 1.5s ease-in-out infinite;
}

@keyframes imgph-indeterminate {
  0% {
    margin-left: -38%;
  }
  55% {
    margin-left: 100%;
  }
  100% {
    margin-left: 100%;
  }
}

/* 关掉动效偏好下只留静态骨架：转圈和扫光对这类用户是干扰，不是信息。 */
@media (prefers-reduced-motion: reduce) {
  .imgph-skel::after {
    animation: none;
    opacity: 0;
  }
  .imgph-bar {
    animation: none;
    margin-left: 0;
    width: 100%;
    opacity: 0.35;
  }
}
</style>
