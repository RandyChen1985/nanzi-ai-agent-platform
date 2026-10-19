<script setup lang="ts">
/**
 * 请求 Headers 编辑器：默认键值对，复杂场景保留原始 JSON。
 *
 * 只接受 JSON 对象，不猜数组/字符串等形状；值统一按字符串保存。键的重复由
 * 浏览器对象语义自然收敛，界面在保存前会提示空键与重复键。
 */
import { computed, nextTick, ref, watch } from 'vue'
import { TrashIcon } from '@heroicons/vue/24/outline'

const props = withDefaults(defineProps<{
  modelValue: string
  error?: string
}>(), {
  error: '',
})

const emit = defineEmits<{ (e: 'update:modelValue', value: string): void }>()

type HeaderRow = { id: number; key: string; value: string }

let rowSeed = 0
const rows = ref<HeaderRow[]>([])
const mode = ref<'rows' | 'json'>('rows')
const parseError = ref('')
const inputRefs = new Map<number, HTMLInputElement>()
let syncing = false

const displayError = computed(() => props.error || parseError.value)

const parseHeaders = (text: string): Record<string, string> | null => {
  try {
    const parsed = JSON.parse(text || '{}')
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      parseError.value = 'Headers 必须是 JSON 对象，例如 {"Authorization": "Bearer ..."}'
      return null
    }
    const headers: Record<string, string> = {}
    for (const [rawKey, rawValue] of Object.entries(parsed)) {
      const key = String(rawKey).trim()
      if (!key) continue
      headers[key] = typeof rawValue === 'string' ? rawValue : String(rawValue)
    }
    parseError.value = ''
    return headers
  } catch {
    parseError.value = 'Headers 不是合法 JSON，请先修正'
    return null
  }
}

const rowsToJson = (): string => {
  const headers: Record<string, string> = {}
  for (const row of rows.value) {
    const key = row.key.trim()
    if (!key) continue
    // 同名 Header 以最后一行为准，序列化后是唯一对象键。
    headers[key] = row.value
  }
  return JSON.stringify(headers, null, 2)
}

const syncRows = (text: string) => {
  const parsed = parseHeaders(text)
  if (!parsed) {
    return
  }
  rows.value = Object.entries(parsed).map(([key, value]) => ({
    id: ++rowSeed,
    key,
    value,
  }))
}

const emitFromRows = () => {
  const json = rowsToJson()
  syncing = true
  emit('update:modelValue', json)
  // 让父组件回写的 props 再触发一次 watch 时不会把正在编辑的行重建掉。
  void nextTick(() => { syncing = false })
}

const onKeyInput = (row: HeaderRow, event: Event) => {
  row.key = (event.target as HTMLInputElement).value
  emitFromRows()
}

const onValueInput = (row: HeaderRow, event: Event) => {
  row.value = (event.target as HTMLInputElement).value
  emitFromRows()
}

const addRow = () => {
  const row = { id: ++rowSeed, key: '', value: '' }
  rows.value.push(row)
  void nextTick(() => inputRefs.get(row.id)?.focus())
}

const removeRow = (id: number) => {
  rows.value = rows.value.filter((row) => row.id !== id)
  emitFromRows()
}

const switchToRows = () => {
  const parsed = parseHeaders(props.modelValue)
  if (!parsed) return
  syncRows(props.modelValue)
  mode.value = 'rows'
}

const switchToJson = () => {
  mode.value = 'json'
}

watch(
  () => props.modelValue,
  (value) => {
    if (syncing) return
    if (mode.value === 'json') {
      parseHeaders(value)
      return
    }
    const parsed = parseHeaders(value)
    if (!parsed) return
    const current = rowsToJson()
    let incoming = ''
    try {
      incoming = JSON.stringify(parsed, null, 2)
    } catch {
      incoming = ''
    }
    if (current !== incoming) syncRows(value)
  },
  { immediate: true },
)
</script>

<template>
  <div class="space-y-1.5">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <label class="block text-sm font-medium text-gray-700">Headers</label>
      <div class="flex rounded-lg bg-gray-100 p-0.5 text-[11px]" role="group" aria-label="Headers 编辑方式">
        <button
          type="button"
          class="rounded-md px-2 py-1 font-medium transition-colors"
          :class="mode === 'rows' ? 'bg-white text-primary shadow-sm' : 'text-gray-500 hover:text-gray-700'"
          @click="switchToRows"
        >键值对</button>
        <button
          type="button"
          class="rounded-md px-2 py-1 font-medium transition-colors"
          :class="mode === 'json' ? 'bg-white text-primary shadow-sm' : 'text-gray-500 hover:text-gray-700'"
          @click="switchToJson"
        >JSON 高级</button>
      </div>
    </div>

    <template v-if="mode === 'rows'">
      <div v-if="rows.length" class="hidden grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_2rem] gap-1.5 px-1 text-[11px] text-gray-400 sm:grid">
        <span>名称</span>
        <span>值</span>
        <span></span>
      </div>
      <div
        v-for="row in rows"
        :key="row.id"
        class="grid grid-cols-[minmax(0,1fr)_2rem] gap-1.5 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_2rem] sm:items-center"
      >
        <input
          :ref="(el) => { if (el) inputRefs.set(row.id, el as HTMLInputElement) }"
          :value="row.key"
          class="min-w-0 rounded-md border border-gray-300 px-2.5 py-1.5 font-mono text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
          placeholder="Header 名称"
          aria-label="Header 名称"
          @input="onKeyInput(row, $event)"
        />
        <input
          :value="row.value"
          class="col-span-2 min-w-0 rounded-md border border-gray-300 px-2.5 py-1.5 font-mono text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 sm:col-span-1"
          placeholder="值，支持 {user.user_name} 等用户变量"
          aria-label="Header 值"
          @input="onValueInput(row, $event)"
        />
        <button
          type="button"
          class="col-span-2 justify-self-end rounded-md p-1.5 text-gray-400 hover:text-red-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 sm:col-span-1 sm:justify-self-center"
          :aria-label="`删除 Header ${row.key || '未命名'}`"
          @click="removeRow(row.id)"
        >
          <TrashIcon class="h-4 w-4" />
        </button>
      </div>
      <p v-if="rows.length === 0" class="rounded-md border border-dashed border-gray-200 bg-gray-50 px-2.5 py-2 text-xs text-gray-400">
        还没有 Header；大多数接口不需要额外 Header。
      </p>
      <button
        type="button"
        class="rounded-md border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
        @click="addRow"
      >+ 添加 Header</button>
    </template>

    <textarea
      v-else
      :value="modelValue"
      rows="4"
      class="block w-full rounded-md border border-gray-300 bg-white px-2.5 py-2 font-mono text-xs shadow-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
      placeholder='{ "Authorization": "Bearer token" }'
      aria-label="Headers JSON"
      @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
    ></textarea>

    <p v-if="displayError" class="text-[11px] text-red-600">{{ displayError }}</p>
    <p v-else class="text-[11px] text-gray-400">敏感值建议用变量占位；保存后仍会按原值写入工具配置。</p>
  </div>
</template>
