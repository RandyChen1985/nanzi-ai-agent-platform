<template>
  <div class="mb-3">
    <div class="flex items-start justify-between gap-3">
      <div class="min-w-0">
        <p class="text-xs font-semibold uppercase tracking-wider" :class="toneClass">{{ eyebrow }}</p>
        <h2 class="mt-1 text-lg font-semibold text-gray-900 dark:text-white">{{ title }}</h2>
        <p v-if="description" class="mt-1 text-sm text-gray-500">{{ description }}</p>
      </div>
      <button
        v-if="viewAllLabel"
        type="button"
        class="hidden shrink-0 text-xs font-medium text-blue-600 hover:text-blue-700 md:inline"
        @click="$emit('view-all')"
      >
        {{ viewAllLabel }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue"

const props = withDefaults(
  defineProps<{
    eyebrow: string
    title: string
    description?: string
    tone?: "blue" | "violet" | "emerald" | "amber"
    viewAllLabel?: string
  }>(),
  { tone: "blue" }
)

defineEmits<{ (event: "view-all"): void }>()

const toneClass = computed(() => {
  if (props.tone === "violet") return "text-violet-600"
  if (props.tone === "emerald") return "text-emerald-600"
  if (props.tone === "amber") return "text-amber-600"
  return "text-blue-600"
})
</script>
