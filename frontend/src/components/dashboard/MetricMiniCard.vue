<template>
  <div class="metric-mini flex flex-col justify-between gap-2.5 rounded-[14px] border border-[#e8edf3] bg-white px-[15px] py-3.5 shadow-sm transition-shadow duration-200 hover:shadow-md">
    <div class="flex items-center gap-1.5 text-[12px] font-medium text-gray-500">
      <!-- 图标继承 currentColor：color 由 iconColor 控制，slot 里的 svg 不必各自写色 -->
      <span v-if="$slots.icon" class="flex h-[13px] w-[13px] shrink-0 items-center justify-center" :style="{ color: iconColor }" aria-hidden="true">
        <slot name="icon"></slot>
      </span>
      <span class="truncate">{{ label }}</span>
    </div>
    <div class="text-[23px] font-bold leading-tight tracking-tight text-gray-900 tabular-nums" :class="valueClass">
      {{ value }}<span v-if="unit" class="ml-0.5 text-[12.5px] font-semibold tracking-normal text-gray-400">{{ unit }}</span>
    </div>
    <div class="text-[11px] text-gray-400">{{ foot }}</div>
  </div>
</template>

<script setup lang="ts">
/**
 * 概览页次级指标卡：与主角卡同排，但字号与宽度都退一档。
 *
 * 图标只保留线条色、不带彩色底块：五个卡片并排时，彩色方块会比数字更抢眼。
 */
withDefaults(
  defineProps<{
    label: string
    value: string | number
    unit?: string
    foot?: string
    /** 图标颜色，直接给 stroke 色，如 '#2563eb'。设为必填：每个指标一个语义色，
     *  漏传会退回灰色，而这种「只是灰了一点」的遗漏在页面上几乎看不出来 */
    iconColor: string
    /** 少数卡片（如 API Key 正常）需要强调文字本身而不是数字 */
    valueClass?: string
  }>(),
  { valueClass: 'text-gray-900' },
)
</script>
