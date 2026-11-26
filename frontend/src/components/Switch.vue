<template>
  <button
    type="button"
    @click="$emit('update:modelValue', !modelValue)"
    :disabled="disabled || loading"
    :aria-busy="loading"
    :class="[
      modelValue ? 'bg-primary' : 'bg-gray-200',
      disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer',
      'relative inline-flex h-6 w-11 flex-shrink-0 rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2'
    ]"
    role="switch"
    :aria-checked="modelValue"
  >
    <span
      aria-hidden="true"
      :class="[
        modelValue ? 'translate-x-5' : 'translate-x-0',
        loading ? 'opacity-0' : '',
        'pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out'
      ]"
    />
    <svg
      v-if="loading"
      class="absolute inset-0 m-auto h-4 w-4 animate-spin"
      :class="modelValue ? 'text-white' : 'text-primary'"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle class="opacity-25" cx="12" cy="12" r="9" stroke="currentColor" stroke-width="3" />
      <path class="opacity-90" fill="currentColor" d="M21 12a9 9 0 0 0-9-9v3a6 6 0 0 1 6 6h3Z" />
    </svg>
  </button>
</template>

<script setup lang="ts">
defineProps<{
  modelValue: boolean
  disabled?: boolean
  loading?: boolean
}>()

defineEmits<{
  (e: 'update:modelValue', value: boolean): void
}>()
</script>
