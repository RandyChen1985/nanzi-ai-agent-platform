<template>
  <Transition name="sync-log-drawer">
    <aside
      v-if="open"
      class="fixed inset-y-0 right-0 z-[110] w-full max-w-md bg-white shadow-2xl border-l border-gray-200 flex flex-col"
      :aria-label="title"
    >
      <div class="px-5 py-4 border-b border-gray-100 flex items-start justify-between">
        <div class="min-w-0">
          <h3 class="text-base font-bold text-gray-900">{{ title }}</h3>
          <p v-if="subtitle" class="mt-1 text-xs text-gray-500">{{ subtitle }}</p>
        </div>
        <button
          class="shrink-0 ml-3 text-gray-400 hover:text-gray-700 text-xl leading-none"
          :aria-label="`关闭${title}`"
          @click="emit('close')"
        >&times;</button>
      </div>

      <div class="px-5 py-4 border-b border-gray-100 space-y-3">
        <div class="flex items-center justify-between text-xs">
          <span class="text-gray-500">状态</span>
          <span :class="statusClass" class="font-semibold">{{ statusText }}</span>
        </div>
        <div class="flex items-center justify-between text-xs">
          <span class="text-gray-500">当前阶段</span>
          <span class="text-gray-800 font-medium truncate max-w-[220px]">{{ stageLabel || stage || '—' }}</span>
        </div>
        <div v-for="item in meta" :key="`${item.label}-${item.value}`" class="flex items-center justify-between text-xs gap-3">
          <span class="text-gray-500 shrink-0">{{ item.label }}</span>
          <span class="text-gray-800 font-medium truncate">{{ item.value }}</span>
        </div>
        <div v-if="done !== null && total !== null" class="flex items-center justify-between text-xs">
          <span class="text-gray-500">处理进度</span>
          <span class="text-gray-800 font-semibold">已完成 {{ done }} / {{ total }}</span>
        </div>
        <div class="h-2 rounded-full bg-gray-100 overflow-hidden">
          <div class="h-full bg-blue-500 transition-all duration-300" :style="{ width: `${progress ?? 0}%` }"></div>
        </div>
        <div class="flex justify-between text-[11px] text-gray-400 gap-3">
          <span class="truncate">{{ message }}</span>
          <span class="shrink-0">{{ elapsedMs }} ms</span>
        </div>
      </div>

      <div class="flex-1 overflow-y-auto px-5 py-4 space-y-3">
        <div
          v-for="(entry, index) in entries"
          :key="`${entry.stage}-${index}`"
          class="relative pl-4 border-l-2"
          :class="entryBorderClass(entry)"
        >
          <div class="text-xs font-medium" :class="isFailedEntry(entry) ? 'text-red-700' : 'text-gray-800'">{{ entry.message }}</div>
          <div class="mt-1 text-[11px] text-gray-400 flex justify-between gap-2">
            <span>{{ entry.stage_label || entry.stage }}</span>
            <span>{{ entry.elapsed_ms ?? 0 }} ms</span>
          </div>
          <div v-if="entry.error_detail" class="mt-1 text-[11px] text-red-600 break-words">{{ entry.error_detail }}</div>
        </div>
        <p v-if="entries.length === 0" class="text-xs text-gray-400">正在等待任务日志...</p>

        <div v-if="failedItems.length > 0" class="rounded-lg border border-red-200 bg-red-50 p-3 space-y-1.5">
          <div class="text-xs font-semibold text-red-700">失败明细（{{ failedItems.length }}）</div>
          <div
            v-for="(item, index) in failedItems"
            :key="`${item.name}-${index}`"
            class="text-[11px] text-red-600 break-words"
          >
            <span class="font-medium">{{ item.name }}</span><span v-if="item.error">：{{ item.error }}</span>
          </div>
        </div>
      </div>

      <div class="px-5 py-3 border-t border-gray-100 text-[11px] text-gray-400">关闭窗口不会取消后台任务</div>
    </aside>
  </Transition>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { createSseLineParser } from '../../utils/chartRenderer'

interface TaskProgressMetaItem {
  label: string
  value: string
}

interface TaskFailedItem {
  name: string
  error: string
}

interface TaskProgressEntry {
  event: string
  stage: string
  stage_label?: string | null
  message: string
  progress?: number | null
  elapsed_ms?: number | null
  error_detail?: string | null
}

type TaskProgressStatus = 'running' | 'completed' | 'failed' | 'disconnected'

const props = defineProps<{
  open: boolean
  title: string
  subtitle?: string
  streamUrl: string
  meta?: TaskProgressMetaItem[]
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'finished', payload: { ok: boolean; message: string }): void
}>()

const status = ref<TaskProgressStatus>('running')
const stage = ref('')
const stageLabel = ref('')
const progress = ref<number | null>(0)
const done = ref<number | null>(null)
const total = ref<number | null>(null)
const elapsedMs = ref(0)
const message = ref('正在连接任务日志...')
const entries = ref<TaskProgressEntry[]>([])
const failedItems = ref<TaskFailedItem[]>([])
let abortController: AbortController | null = null

const statusText = computed(() => {
  if (status.value === 'completed') return '已完成'
  if (status.value === 'failed') return '失败'
  if (status.value === 'disconnected') return '连接中断'
  return '运行中'
})

const statusClass = computed(() => {
  if (status.value === 'completed') return 'text-emerald-600'
  if (status.value === 'failed') return 'text-red-600'
  if (status.value === 'disconnected') return 'text-amber-600'
  return 'text-blue-600'
})

const isFailedEntry = (entry: TaskProgressEntry) => entry.event === 'failed' || !!entry.error_detail

const entryBorderClass = (entry: TaskProgressEntry) => {
  if (isFailedEntry(entry)) return 'border-red-300'
  if (entry.event === 'completed') return 'border-emerald-300'
  return 'border-blue-200'
}

const abortCurrent = () => {
  abortController?.abort()
  abortController = null
}

const resetState = () => {
  status.value = 'running'
  stage.value = ''
  stageLabel.value = ''
  progress.value = 0
  done.value = null
  total.value = null
  elapsedMs.value = 0
  message.value = '正在连接任务日志...'
  entries.value = []
  failedItems.value = []
}

const readNumber = (value: unknown): number | null => {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))) return Number(value)
  return null
}

const readFailedItems = (value: unknown): TaskFailedItem[] => {
  if (!Array.isArray(value)) return []
  return value
    .filter((item) => item !== null && typeof item === 'object')
    .map((item: any) => ({ name: String(item.name ?? ''), error: String(item.error ?? '') }))
}

const startStream = async (url: string) => {
  abortCurrent()
  const controller = new AbortController()
  abortController = controller
  try {
    // 凭据由同源 HttpOnly Cookie 自动携带（fetch 默认 same-origin）
    const response = await fetch(url, {
      headers: { Accept: 'text/event-stream' },
      credentials: 'include',
      signal: controller.signal,
    })
    if (!response.ok) throw new Error(`任务日志请求失败（${response.status}）`)
    if (!response.body) throw new Error('任务日志没有返回数据流')

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    const parser = createSseLineParser()
    let terminalReceived = false

    const applyEvent = (dataStr: string) => {
      if (!dataStr || dataStr === '[DONE]') return
      let item: any
      try {
        item = JSON.parse(dataStr)
      } catch {
        return
      }
      if (!item || typeof item !== 'object') return

      const eventName = typeof item.event === 'string' && item.event ? item.event : 'progress'
      const eventStage = typeof item.stage === 'string' && item.stage ? item.stage : stage.value
      const eventMessage =
        typeof item.message === 'string' && item.message
          ? item.message
          : typeof item.error_detail === 'string' && item.error_detail
            ? item.error_detail
            : message.value

      const entry: TaskProgressEntry = {
        event: eventName,
        stage: eventStage,
        stage_label: typeof item.stage_label === 'string' ? item.stage_label : null,
        message: eventMessage,
        progress: readNumber(item.progress),
        elapsed_ms: readNumber(item.elapsed_ms),
        error_detail: typeof item.error_detail === 'string' ? item.error_detail : null,
      }
      entries.value.push(entry)

      stage.value = eventStage
      if (typeof item.stage_label === 'string' && item.stage_label) stageLabel.value = item.stage_label
      message.value = eventMessage
      const nextProgress = readNumber(item.progress)
      if (nextProgress !== null) progress.value = nextProgress
      const nextDone = readNumber(item.done)
      if (nextDone !== null) done.value = nextDone
      const nextTotal = readNumber(item.total)
      if (nextTotal !== null) total.value = nextTotal
      const nextElapsed = readNumber(item.elapsed_ms)
      if (nextElapsed !== null) elapsedMs.value = nextElapsed
      if (Array.isArray(item.failed_items)) failedItems.value = readFailedItems(item.failed_items)

      if (eventName === 'completed' || eventName === 'failed') {
        terminalReceived = true
        status.value = eventName
        emit('finished', { ok: eventName === 'completed', message: eventMessage })
      }
    }

    while (true) {
      const { done: streamDone, value } = await reader.read()
      if (streamDone) break
      for (const dataStr of parser.feed(decoder.decode(value, { stream: true }))) applyEvent(dataStr)
      if (terminalReceived) break
    }
    for (const dataStr of parser.flush()) applyEvent(dataStr)
    if (!terminalReceived && !controller.signal.aborted) {
      status.value = 'disconnected'
      message.value = '任务日志连接已断开'
    }
  } catch (error: any) {
    if (controller.signal.aborted) return
    status.value = 'disconnected'
    message.value = error?.message || '任务日志连接已断开'
  } finally {
    if (abortController === controller) abortController = null
  }
}

watch(
  () => [props.open, props.streamUrl],
  ([isOpen, url]) => {
    abortCurrent()
    if (isOpen && url) {
      resetState()
      void startStream(url)
    }
  },
  { immediate: true },
)

onUnmounted(() => abortCurrent())

defineExpose({ abortCurrent })
</script>

<style scoped>
.sync-log-drawer-enter-active,
.sync-log-drawer-leave-active { transition: transform 0.2s ease, opacity 0.2s ease; }
.sync-log-drawer-enter-from,
.sync-log-drawer-leave-to { transform: translateX(100%); opacity: 0; }
</style>
