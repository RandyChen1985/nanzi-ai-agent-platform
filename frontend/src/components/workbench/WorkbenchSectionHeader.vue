<template>
  <div class="mb-3.5">
    <div class="flex items-start justify-between gap-3">
      <div class="min-w-0">
        <div class="flex items-center gap-2">
          <span class="h-1.5 w-1.5 shrink-0 rounded-full" :class="dotClass" />
          <p class="text-xs font-medium" :class="toneClass">{{ eyebrow }}</p>
        </div>
        <h2 class="mt-1 text-sm font-bold text-gray-900">{{ title }}</h2>
        <p v-if="description" class="mt-0.5 text-xs text-gray-500">{{ description }}</p>
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
  if (props.tone === "amber") return "text-amber-700"
  return "text-blue-600"
})

const dotClass = computed(() => {
  if (props.tone === "violet") return "bg-violet-500"
  if (props.tone === "emerald") return "bg-emerald-500"
  if (props.tone === "amber") return "bg-amber-500"
  return "bg-blue-500"
})
</script>
