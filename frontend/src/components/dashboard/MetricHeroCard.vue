<template>
  <div class="metric-hero relative flex flex-col justify-between gap-3 overflow-hidden rounded-[14px] border border-[#e4ecf9] bg-gradient-to-br from-[#f7faff] to-white px-[18px] pb-3.5 pt-[15px] shadow-sm">
    <div class="flex items-end justify-between gap-3.5">
      <div class="min-w-0">
        <div class="text-[12.5px] font-medium text-gray-500">{{ label }}</div>
        <div class="mt-1.5 text-[37px] font-bold leading-none tracking-tight text-gray-900 tabular-nums">
          {{ value }}<span v-if="unit" class="ml-0.5 text-[17px] font-semibold tracking-normal text-gray-500">{{ unit }}</span>
        </div>
      </div>
      <div class="shrink-0 pb-1 text-right text-[11.5px] leading-relaxed text-gray-400">
        <template v-if="total !== undefined">
          <strong class="font-semibold text-gray-600 tabular-nums">{{ formattedSuccess }}</strong>
          <span> / </span>
          <strong class="font-semibold text-gray-600 tabular-nums">{{ formattedTotal }}</strong>
          <span> 次成功</span><br>
        </template>
        <span v-if="failed !== undefined" class="font-semibold tabular-nums" :class="failedToneClass">
          {{ formattedFailed }}
        </span>
        <span v-if="failed !== undefined"> 次失败</span>
        <span v-if="note">{{ note }}</span>
      </div>
    </div>
    <div class="h-[5px] overflow-hidden rounded-full bg-[#eef2f7]">
      <span class="block h-full rounded-full" :class="barClass" :style="{ width: `${clampedPercent}%` }"></span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

/**
 * 概览页「主角指标」卡：结论性指标（成功率）用更大字号与宽度建立层级。
 *
 * 刻意不用大面积色块承载：页面其余部分都是白卡，一块深色底会孤立得像另一个体系。
 * 这里靠极淡的冷色渐变 + 左侧主色竖条表达「这是主指标」。
 */
const props = withDefaults(
  defineProps<{
    label: string
    value: string | number
    unit?: string
    /** 成功率等百分比指标：用于画进度条，取值 0–100 */
    percent?: number
    total?: number
    success?: number
    failed?: number
    /** 低于该百分比时进度条转琥珀；默认 99 */
    warnBelow?: number
    /** 无 total/failed 时的补充说明 */
    note?: string
  }>(),
  { warnBelow: 99 },
)

const clamp = (n: number, min: number, max: number) => Math.min(max, Math.max(min, n))

const clampedPercent = computed(() => clamp(props.percent ?? 0, 0, 100))
const isWarn = computed(() => (props.percent ?? 0) < props.warnBelow)

const barClass = computed(() =>
  isWarn.value
    ? 'bg-gradient-to-r from-amber-400 to-amber-500'
    : 'bg-gradient-to-r from-emerald-400 to-emerald-500',
)

// 失败数是这段里唯一的负面信号，超过 0 就标出来
const failedToneClass = computed(() =>
  (props.failed ?? 0) > 0 ? 'text-amber-700' : 'text-gray-600',
)

const formattedTotal = computed(() => (props.total ?? 0).toLocaleString())
const formattedSuccess = computed(() => (props.success ?? 0).toLocaleString())
const formattedFailed = computed(() => (props.failed ?? 0).toLocaleString())
</script>

<style scoped>
/* 左侧主色竖条：卡片的「主指标」身份标记，不用色块也能被识别 */
.metric-hero::before {
  content: "";
  position: absolute;
  left: 0;
  top: 15px;
  bottom: 15px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: #2563eb;
}
</style>
