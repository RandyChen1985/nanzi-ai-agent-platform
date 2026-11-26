<template>
  <section class="mt-3 rounded-xl border border-primary/10 bg-primary/5 p-3 dark:border-primary-dark/60 dark:bg-primary-dark/20">
    <div class="flex flex-wrap items-center gap-2 text-xs">
      <span class="font-semibold text-primary-dark dark:text-primary-hover">可分析内容</span>
      <span v-for="metric in guide.metrics.slice(0, 4)" :key="`${metric.table}.${metric.physical_name}`" class="rounded-full bg-white px-2 py-1 text-gray-600 shadow-sm dark:bg-gray-900 dark:text-gray-300" :title="metric.physical_name">
        {{ metric.label }}
      </span>
    </div>
    <div v-if="guide.suggestions.length" class="mt-2 flex flex-wrap gap-2">
      <button v-for="item in guide.suggestions" :key="item.query" type="button" class="rounded-lg border border-primary/20 bg-white px-2.5 py-1.5 text-xs font-medium text-primary-dark hover:bg-primary/10 dark:border-primary-dark dark:bg-gray-900 dark:text-primary-hover" @click="emit('select', item.query)">
        {{ item.label }}
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import type { ChatBIMetadataGuide } from "@/types/chatbiMetadataGuide";

defineProps<{ guide: ChatBIMetadataGuide }>();
const emit = defineEmits<{ (event: 'select', query: string): void }>();
</script>
