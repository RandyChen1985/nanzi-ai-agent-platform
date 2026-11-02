<template>
  <div class="flex flex-col">
    <div
      v-for="(item, index) in items"
      :key="item.label"
      class="flex items-center justify-between gap-3 border-b border-dashed border-[#e8edf3] py-3 last:border-b-0 last:pb-0.5 first:pt-0.5"
    >
      <span class="text-[12.5px] text-gray-500">{{ item.label }}</span>
      <span class="flex items-center gap-2">
        <span
          v-if="item.tone"
          class="inline-flex items-center rounded-full px-1.5 py-0.5 text-[10.5px] font-semibold"
          :class="toneClass(item.tone)"
        >{{ toneLabel(item.tone) }}</span>
        <span
          class="text-[17px] font-bold tracking-tight tabular-nums"
          :class="item.tone === 'warn' ? 'text-amber-700' : 'text-gray-900'"
        >{{ item.value }}<span v-if="item.unit" class="text-[12px] font-semibold text-gray-400">{{ item.unit }}</span></span>
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
export type MetricTone = 'ok' | 'warn' | 'danger' | undefined

/**
 * 核心指标列表。
 *
 * 原来三行「标签 + 数字」并列，读者无法判断 96.67% 是好还是坏。加状态徽章后，
 * 数字本身不用再靠颜色区分，含义也更明确。
 */
withDefaults(
  defineProps<{
    items: Array<{
      label: string
      value: string | number
      unit?: string
      tone?: MetricTone
    }>
  }>(),
  { items: () => [] },
)

const TONE_LABEL: Record<string, string> = {
  ok: '正常',
  warn: '偏高',
  danger: '异常',
}

const TONE_CLASS: Record<string, string> = {
  ok: 'bg-emerald-50 text-emerald-700',
  warn: 'bg-amber-50 text-amber-700',
  danger: 'bg-red-50 text-red-700',
}

const toneLabel = (tone: string) => TONE_LABEL[tone] || ''
const toneClass = (tone: string) => TONE_CLASS[tone] || ''
</script>
