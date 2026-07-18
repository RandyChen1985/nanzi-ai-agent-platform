<template>
  <button
    v-if="label"
    type="button"
    class="mt-3 flex w-full items-center justify-center rounded-xl border border-gray-200 bg-white px-3 py-2 text-xs font-medium text-blue-600 shadow-sm hover:border-blue-200 hover:bg-blue-50 md:hidden dark:border-gray-700 dark:bg-gray-900"
    @click="$emit('view-all')"
  >
    {{ label }}
  </button>
</template>

<script setup lang="ts">
defineProps<{ label?: string }>()
defineEmits<{ (event: "view-all"): void }>()
</script>
