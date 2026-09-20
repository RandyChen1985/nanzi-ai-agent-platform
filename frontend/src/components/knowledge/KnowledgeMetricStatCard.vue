<template>
  <div
    class="bg-white rounded-2xl p-5 border border-gray-100 shadow-sm flex items-center justify-between gap-3"
  >
    <div class="min-w-0 flex-1 space-y-1">
      <div class="flex items-center gap-1.5">
        <span class="text-sm font-medium text-gray-500">{{ label }}</span>
        <span
          v-if="hint"
          class="text-gray-300 hover:text-gray-400 cursor-help transition-colors"
          :title="hint"
          :aria-label="hint"
          role="img"
        >
          <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        </span>
      </div>

      <div v-if="loading" class="py-1" aria-hidden="true">
        <div class="h-7 w-24 rounded-md bg-gray-100 animate-pulse"></div>
      </div>
      <h3
        v-else
        class="text-2xl sm:text-3xl font-black tabular-nums leading-none"
        :class="warning ? 'text-amber-600' : 'text-gray-900'"
      >
        {{ value }}
      </h3>

      <div class="flex flex-wrap items-center gap-x-2 gap-y-0.5 min-h-[16px]">
        <p class="text-xs text-gray-400">{{ description }}</p>
        <span
          v-if="!loading && delta && delta.text"
          class="inline-flex items-center gap-0.5 text-xs font-medium tabular-nums"
          :class="deltaClass"
          :title="deltaTitle"
        >
          <svg
            v-if="delta.direction !== 'flat'"
            class="h-3 w-3"
            :class="{ 'rotate-180': delta.direction === 'down' }"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 10l7-7m0 0l7 7m-7-7v18" />
          </svg>
          {{ delta.text }}
          <span class="text-gray-400 font-normal">较上期</span>
        </span>
      </div>

      <p v-if="!loading && warning" class="text-xs text-amber-600">{{ warning }}</p>
    </div>

    <div class="flex-shrink-0 p-3.5 rounded-2xl" :class="toneClass" aria-hidden="true">
      <slot name="icon" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { DeltaResult } from '@/utils/knowledgeMetricsChart'

type Tone = 'blue' | 'indigo' | 'emerald' | 'amber'

const props = withDefaults(
  defineProps<{
    label: string
    value: string
    description?: string
    hint?: string
    tone?: Tone
    delta?: DeltaResult | null
    deltaTitle?: string
    loading?: boolean
    warning?: string | null
  }>(),
  {
    description: '',
    hint: '',
    tone: 'blue',
    delta: null,
    deltaTitle: '与上一个等长周期对比',
    loading: false,
    warning: null,
  },
)

const TONE_CLASS: Record<Tone, string> = {
  blue: 'bg-blue-50 text-blue-600',
  indigo: 'bg-indigo-50 text-indigo-600',
  emerald: 'bg-emerald-50 text-emerald-600',
  amber: 'bg-amber-50 text-amber-600',
}

const DELTA_CLASS: Record<DeltaResult['direction'], string> = {
  up: 'text-emerald-600',
  down: 'text-rose-600',
  flat: 'text-gray-400',
}

const toneClass = computed(() => TONE_CLASS[props.tone])
const deltaClass = computed(() => DELTA_CLASS[props.delta?.direction ?? 'flat'])
</script>
