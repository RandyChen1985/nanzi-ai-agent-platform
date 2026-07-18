<template>
  <div class="bg-white rounded-2xl shadow-sm border border-gray-100 p-4 sm:p-5">
    <div class="flex items-center gap-3.5">
      <div
        class="flex-shrink-0 rounded-xl p-2.5"
        :class="bgClass"
      >
        <slot name="icon"></slot>
      </div>
      <div class="min-w-0 flex-1">
        <p class="text-xs font-medium text-gray-500 truncate">
          {{ title }}
        </p>
        <p
          class="mt-0.5 text-xl sm:text-2xl font-bold tracking-tight tabular-nums"
          :class="valueClass"
        >
          {{ value }}
          <small v-if="unit" class="text-xs font-semibold text-gray-400 ml-0.5">{{ unit }}</small>
        </p>
        <div v-if="$slots.subtext || subtext" class="mt-0.5 text-xs text-gray-400">
          <slot name="subtext">{{ subtext }}</slot>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';

const props = defineProps<{
  title: string;
  value: string | number;
  type?: 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'purple';
  unit?: string;
  subtext?: string;
  valueColor?: string;
  bgColor?: string;
}>();

const bgClass = computed(() => {
  if (props.bgColor) return props.bgColor;

  switch (props.type) {
    case 'success': return 'bg-emerald-50 text-emerald-600';
    case 'warning': return 'bg-amber-50 text-amber-600';
    case 'danger': return 'bg-red-50 text-red-600';
    case 'info': return 'bg-blue-50 text-blue-600';
    case 'purple': return 'bg-violet-50 text-violet-600';
    default: return 'bg-blue-50 text-blue-600';
  }
});

const valueClass = computed(() => {
  if (props.valueColor) return props.valueColor;
  return 'text-gray-900';
});
</script>
