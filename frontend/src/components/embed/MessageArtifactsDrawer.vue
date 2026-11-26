<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { artifactApi, type ArtifactListItem } from '@/api/artifact'
import { resolveGeneratedFileHref } from '@/utils/generatedFileUrl'
import { resolveFileTypeVisual } from '@/utils/fileTypeVisual'
import { useToast } from '@/composables/useToast'

/** 展示某条 AI 消息产物的右侧抽屉。panel 内展示该 trace 下的产物列表。 */
const props = defineProps<{
  /** 生成该条消息产物的 AI 消息 trace_id */
  traceId: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

const { showToast } = useToast()

const items = ref<ArtifactListItem[]>([])
const loading = ref(false)
const error = ref('')

const fileVisuals = computed<Record<string, ReturnType<typeof resolveFileTypeVisual>>>(() => {
  const map: Record<string, ReturnType<typeof resolveFileTypeVisual>> = {}
  for (const it of items.value) {
    map[it.id] = resolveFileTypeVisual(it.filename)
  }
  return map
})

const typeLabel = (t: string) => {
  const label = ({ word: 'Word', excel: 'Excel', export: '导出' } as Record<string, string>)[t]
  return label || t || '未知'
}
const formatSize = (size: number) => {
  if (!size) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let v = size
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v >= 10 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`
}

const openArtifact = (it: ArtifactListItem) => {
  if (!it.download_url) {
    showToast('该产出物缺少下载地址', 'warning')
    return
  }
  const href = resolveGeneratedFileHref(it.download_url)
  window.open(href, '_blank', 'noopener,noreferrer')
}

const load = async () => {
  if (!props.traceId) {
    items.value = []
    return
  }
  loading.value = true
  error.value = ''
  try {
    const res = await artifactApi.list({
      page: 1,
      page_size: 50,
      trace_id: props.traceId,
    })
    items.value = res.data?.data?.items ?? []
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '加载产出物失败'
    items.value = []
  } finally {
    loading.value = false
  }
}

watch(
  () => props.traceId,
  (val) => {
    if (val) void load()
  },
)
void load()
</script>

<template>
  <teleport to="body">
    <div class="fixed inset-0 z-[120] overflow-hidden" aria-hidden="true">
      <!-- Backdrop -->
      <transition
        enter-active-class="ease-out duration-300"
        enter-from-class="opacity-0"
        enter-to-class="opacity-100"
        leave-active-class="ease-in duration-200"
        leave-from-class="opacity-100"
        leave-to-class="opacity-0"
      >
        <div
          class="absolute inset-0 bg-gray-500/30 backdrop-blur-xs transition-opacity"
          @click="emit('close')"
        />
      </transition>

      <!-- Panel -->
      <transition
        enter-active-class="transform transition ease-in-out duration-300"
        enter-from-class="translate-x-full"
        enter-to-class="translate-x-0"
        leave-active-class="transform transition ease-in-out duration-300"
        leave-from-class="translate-x-0"
        leave-to-class="translate-x-full"
      >
        <aside
          class="absolute inset-y-0 right-0 w-full max-w-sm sm:max-w-md flex flex-col bg-white dark:bg-gray-900 shadow-2xl border-l border-gray-200 dark:border-gray-800"
        >
          <!-- Header -->
          <div
            class="flex items-center justify-between gap-2 px-4 py-3 border-b border-gray-100 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800 flex-shrink-0"
          >
            <div class="flex items-center gap-2 min-w-0 text-sm font-bold text-gray-800 dark:text-gray-100">
              <svg class="h-4 w-4 text-primary flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              <span class="truncate">AI 产物</span>
              <span
                v-if="items.length > 0"
                class="text-[10px] font-mono text-gray-400 bg-white dark:bg-gray-700 px-1.5 py-px rounded border border-gray-100 dark:border-gray-600 flex-shrink-0"
              >
                {{ items.length }}
              </span>
            </div>
            <button
              type="button"
              class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 p-1.5 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
              title="关闭"
              aria-label="关闭产物面板"
              @click="emit('close')"
            >
              <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          <!-- Body -->
          <div class="flex-1 min-h-0 overflow-y-auto overscroll-y-contain p-3">
            <!-- Loading -->
            <div v-if="loading" class="flex items-center justify-center py-14 text-gray-400 gap-2">
              <svg class="h-5 w-5 text-primary animate-spin" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
              <span class="text-xs font-medium">加载中...</span>
            </div>

            <!-- Error -->
            <div v-else-if="error" class="flex flex-col items-center justify-center py-14 text-gray-500 dark:text-gray-400 gap-2">
              <svg class="h-7 w-7 text-amber-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <span class="text-xs font-medium text-center">{{ error }}</span>
              <button type="button" class="text-xs font-semibold text-primary hover:underline" @click="load">
                重试
              </button>
            </div>

            <!-- Empty -->
            <div v-else-if="items.length === 0" class="flex flex-col items-center justify-center py-14 text-gray-400 gap-2">
              <svg class="h-7 w-7" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" />
              </svg>
              <span class="text-xs font-medium">该消息暂无产出物</span>
            </div>

            <!-- List -->
            <ul v-else class="flex flex-col gap-1.5">
              <li v-for="it in items" :key="it.id">
                <button
                  type="button"
                  class="w-full flex items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors border border-transparent hover:bg-gray-50 dark:hover:bg-gray-700/60 hover:border-gray-100 dark:hover:border-gray-600 group"
                  :title="`打开 ${it.filename}`"
                  @click="openArtifact(it)"
                >
                  <span
                    :class="[
                      'flex-shrink-0 w-9 h-9 rounded-md flex items-center justify-center border text-[9px] font-semibold tracking-tight leading-none',
                      fileVisuals[it.id]?.iconBg || 'bg-gray-100 dark:bg-gray-800',
                    ]"
                  >
                    {{ fileVisuals[it.id]?.icon || 'FILE' }}
                  </span>
                  <span class="flex-1 min-w-0">
                    <span class="block text-xs font-semibold text-gray-800 dark:text-gray-100 truncate">
                      {{ it.filename }}
                    </span>
                    <span class="block text-[10px] text-gray-400 dark:text-gray-500 mt-0.5 flex items-center gap-1.5">
                      <span class="px-1 py-px rounded bg-gray-100 dark:bg-gray-800 text-[9px] font-semibold">{{ typeLabel(it.artifact_type) }}</span>
                      <span>{{ formatSize(it.size) }}</span>
                      <svg class="h-2.5 w-2.5 text-gray-300 dark:text-gray-600 group-hover:text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                      </svg>
                    </span>
                  </span>
                </button>
              </li>
            </ul>
          </div>
        </aside>
      </transition>
    </div>
  </teleport>
</template>