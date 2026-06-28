<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

export type RowFilterSelectOption = {
  value: string
  label: string
  remark?: string
}

const props = withDefaults(defineProps<{
  modelValue: string
  options: RowFilterSelectOption[]
  placeholder?: string
  disabled?: boolean
  buttonClass?: string
  menuClass?: string
}>(), {
  placeholder: '请选择...',
  disabled: false,
  buttonClass: 'border-gray-300 focus:ring-slate-500',
  menuClass: 'hover:bg-slate-50',
})

const emit = defineEmits<{ 'update:modelValue': [string] }>()

const open = ref(false)
const rootRef = ref<HTMLElement | null>(null)

const selectedOption = computed(
  () => props.options.find((option) => option.value === props.modelValue) || null,
)

const toggle = () => {
  if (props.disabled) return
  open.value = !open.value
}

const selectOption = (value: string) => {
  emit('update:modelValue', value)
  open.value = false
}

const onClickOutside = (event: MouseEvent) => {
  if (!rootRef.value?.contains(event.target as Node)) {
    open.value = false
  }
}

onMounted(() => document.addEventListener('click', onClickOutside))
onBeforeUnmount(() => document.removeEventListener('click', onClickOutside))
</script>

<template>
  <div ref="rootRef" class="relative w-40 min-w-[9rem]">
    <button
      type="button"
      :disabled="disabled"
      class="w-full h-[2.625rem] bg-white border rounded-lg px-2 text-left shadow-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50 focus:ring-1 outline-none"
      :class="buttonClass"
      @click.stop="toggle"
    >
      <div class="flex items-center gap-1">
        <div class="min-w-0 flex-1">
          <template v-if="selectedOption">
            <div class="truncate text-xs font-medium text-gray-800">{{ selectedOption.label }}</div>
            <div
              v-if="selectedOption.remark"
              class="truncate text-[10px] leading-tight text-gray-400"
            >
              {{ selectedOption.remark }}
            </div>
          </template>
          <div v-else class="truncate text-xs text-gray-400">{{ placeholder }}</div>
        </div>
        <svg
          class="h-3.5 w-3.5 shrink-0 text-gray-400 transition-transform"
          :class="{ 'rotate-180': open }"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
        </svg>
      </div>
    </button>

    <div
      v-if="open"
      class="absolute left-0 top-full z-[10000] mt-1 max-h-56 w-full min-w-[11rem] overflow-y-auto rounded-lg border border-gray-200 bg-white py-1 shadow-xl custom-scrollbar"
    >
      <button
        v-for="option in options"
        :key="option.value"
        type="button"
        class="w-full px-2.5 py-1.5 text-left transition-colors"
        :class="[
          menuClass,
          modelValue === option.value ? 'bg-blue-50 text-blue-700' : 'text-gray-800',
        ]"
        @click.stop="selectOption(option.value)"
      >
        <div class="truncate text-xs font-medium">{{ option.label }}</div>
        <div v-if="option.remark" class="truncate text-[10px] leading-tight text-gray-400">
          {{ option.remark }}
        </div>
      </button>
      <div v-if="options.length === 0" class="px-2.5 py-2 text-[10px] text-gray-400">
        暂无可选项
      </div>
    </div>
  </div>
</template>
