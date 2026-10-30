<script setup lang="ts">
/**
 * 业务分组选择器：可选已有分组，也能直接输入新分组。
 *
 * 为什么不用原生 `<input list>` + `<datalist>`：datalist 只给输入建议，
 * 下拉外的视觉提示几乎没有，用户看不出「这里还能选现成的」；也不能显示
 * 每个分组下已有多少工具。这里做一个轻量 combobox，行为仍然简单：
 * 输入框里是什么，分组就是什么（留空 = 未分组）。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = withDefaults(defineProps<{
  modelValue: string | null | undefined
  /** 已有分组（可带计数，仅用于展示） */
  options?: { name: string; count?: number }[]
  placeholder?: string
  inputId?: string
  ariaLabel?: string
}>(), {
  options: () => [],
  placeholder: '选择已有分组，或直接输入新分组',
  inputId: undefined,
  ariaLabel: '业务分组',
})

const emit = defineEmits<{ (e: 'update:modelValue', value: string | null): void }>()

const root = ref<HTMLDivElement | null>(null)
const input = ref<HTMLInputElement | null>(null)
const open = ref(false)
const keyword = ref(props.modelValue || '')

watch(() => props.modelValue, (next) => {
  keyword.value = next || ''
})

const candidates = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  return props.options.filter((option) => !text || option.name.toLowerCase().includes(text))
})

/** 输入的内容与已有分组不一致时，提示「这会新建一个分组」 */
const isNewGroup = computed(() => {
  const text = keyword.value.trim()
  if (!text) return false
  return !props.options.some((option) => option.name === text)
})

function commit(value: string | null) {
  keyword.value = value || ''
  emit('update:modelValue', value && value.trim() ? value.trim() : null)
}

function pick(name: string) {
  commit(name)
  open.value = false
  input.value?.blur()
}

function onInput(event: Event) {
  const value = (event.target as HTMLInputElement).value
  keyword.value = value
  emit('update:modelValue', value.trim() ? value.trim() : null)
  open.value = true
}

function onFocus() {
  open.value = true
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    open.value = false
    return
  }
  if (event.key === 'Enter') {
    event.preventDefault()
    const first = candidates.value[0]
    // 输入恰好等于某个候选、或想用第一条建议时，回车即采纳
    if (first) pick(first.name)
    else {
      commit(keyword.value)
      open.value = false
    }
  }
}

let closeOnOutside: ((event: MouseEvent) => void) | null = null

onMounted(() => {
  closeOnOutside = (event: MouseEvent) => {
    if (!root.value) return
    if (!root.value.contains(event.target as Node)) open.value = false
  }
  document.addEventListener('mousedown', closeOnOutside)
})

onBeforeUnmount(() => {
  if (closeOnOutside) document.removeEventListener('mousedown', closeOnOutside)
})

async function focusInput() {
  await nextTick()
  input.value?.focus()
}

defineExpose({ focusInput })
</script>

<template>
  <div ref="root" class="relative">
    <div class="relative">
      <input
        :id="inputId"
        ref="input"
        :value="keyword"
        type="text"
        autocomplete="off"
        class="block w-full rounded-md border-gray-300 pr-8 text-sm shadow-sm focus:border-primary focus:ring-2 focus:ring-primary/20"
        :placeholder="placeholder"
        :aria-label="ariaLabel"
        role="combobox"
        :aria-expanded="open"
        aria-autocomplete="list"
        @input="onInput"
        @focus="onFocus"
        @keydown="onKeydown"
      />
      <button
        v-if="keyword"
        type="button"
        class="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
        title="清除分组"
        aria-label="清除分组"
        @click="commit(null)"
      >
        <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>

    <div
      v-if="open && (candidates.length || isNewGroup)"
      class="absolute left-0 right-0 z-50 mt-1 max-h-56 overflow-auto rounded-lg border border-gray-200 bg-white py-1 shadow-lg"
      role="listbox"
    >
      <button
        v-for="option in candidates"
        :key="option.name"
        type="button"
        class="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-sm transition-colors hover:bg-gray-50"
        :class="option.name === keyword.trim() ? 'bg-primary/5 text-primary' : 'text-gray-700'"
        role="option"
        :aria-selected="option.name === keyword.trim()"
        @click="pick(option.name)"
      >
        <span class="truncate">{{ option.name }}</span>
        <span v-if="option.count !== undefined" class="shrink-0 text-[11px] text-gray-400">{{ option.count }}</span>
      </button>
      <p v-if="isNewGroup" class="border-t border-gray-100 px-3 pb-1 pt-1.5 text-[11px] text-gray-500">
        将新建分组「<span class="font-medium text-gray-700">{{ keyword.trim() }}</span>」
      </p>
    </div>
  </div>
</template>
