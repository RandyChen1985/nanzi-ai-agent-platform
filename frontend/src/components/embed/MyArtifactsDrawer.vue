<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { artifactApi, type ArtifactListItem } from '@/api/artifact'
import { resolveGeneratedFileHref } from '@/utils/generatedFileUrl'
import { buildArtifactDownloadUrl, resolveGeneratedPreviewPlan } from '@/utils/generatedFilePreviewPlan'
import { resolveFileTypeVisual } from '@/utils/fileTypeVisual'
import { useToast } from '@/composables/useToast'
import ReusableResultList from '@/components/embed/ReusableResultList.vue'
import type { ReusableResultListItem } from '@/api/artifact'

const modelValue = defineModel<boolean>({ default: false })

const props = withDefaults(defineProps<{
  conversationId?: string | null
  traceId?: string | null
  initialTab?: 'files' | 'reusable'
  selectedResultId?: string | null
  focusedResultId?: string | null
  reusedResultId?: string | null
}>(), {
  conversationId: null,
  traceId: null,
  initialTab: 'files',
  selectedResultId: null,
  focusedResultId: null,
  reusedResultId: null,
})

const emit = defineEmits<{
  'select-reusable-result': [result: ReusableResultListItem]
  'preview-file': [item: ArtifactListItem]
}>()

const { showToast } = useToast()

const ARTIFACT_TYPES = [
  { value: '', label: '全部' },
  { value: 'word', label: 'Word' },
  { value: 'excel', label: 'Excel' },
  { value: 'markdown', label: 'Markdown' },
  { value: 'export', label: '导出' },
]
const PAGE_SIZE = 50

const items = ref<ArtifactListItem[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const error = ref('')
const activeType = ref('')
const activeTab = ref<'files' | 'reusable'>('files')
const outputScope = ref<'conversation' | 'message'>('conversation')
const bodyRef = ref<HTMLElement | null>(null)

const typeLabel = (t: string) =>
  ARTIFACT_TYPES.find((x) => x.value === t)?.label || t || '未知'
const formatTraceId = (traceId?: string | null) => {
  const value = String(traceId || '').trim()
  if (!value) return ''
  return value.length <= 12 ? value : `${value.slice(0, 8)}…${value.slice(-4)}`
}
const artifactTraceLabel = (item: ArtifactListItem) => {
  const traceId = String(item.trace_id || '').trim()
  if (!traceId) return '未关联消息'
  return traceId === props.traceId ? '当前消息' : `来源消息 · ${formatTraceId(traceId)}`
}
const artifactTraceTitle = (traceId?: string | null) => {
  const value = String(traceId || '').trim()
  return value ? `trace_id：${value}` : '该产物未关联消息'
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
const formatTime = (iso?: string | null) => {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const fileVisuals = computed<Record<string, ReturnType<typeof resolveFileTypeVisual>>>(() => {
  const map: Record<string, ReturnType<typeof resolveFileTypeVisual>> = {}
  for (const it of items.value) {
    map[it.id] = resolveFileTypeVisual(it.filename)
  }
  return map
})

const isMobile = ref(
  typeof window !== 'undefined' && window.matchMedia('(max-width: 639px)').matches,
)

const load = async () => {
  if (!props.conversationId) {
    items.value = []
    total.value = 0
    return
  }
  loading.value = true
  error.value = ''
  try {
    const res = await artifactApi.list({
      page: page.value,
      page_size: PAGE_SIZE,
      artifact_type: activeType.value || undefined,
      conversation_id: props.conversationId || undefined,
      trace_id: outputScope.value === 'message' ? props.traceId || undefined : undefined,
    })
    const data = res.data?.data
    items.value = data?.items ?? []
    total.value = data?.total ?? 0
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '加载产出物失败'
    items.value = []
  } finally {
    loading.value = false
  }
}

const refresh = () => {
  page.value = 1
  void load()
}

const changeType = (val: string) => {
  activeType.value = val
  refresh()
}

const changeScope = (scope: 'conversation' | 'message') => {
  if (outputScope.value === scope) return
  outputScope.value = scope
  if (activeTab.value === 'files') refresh()
}

const scrollHandler = () => {
  const el = bodyRef.value
  if (!el || loading.value) return
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 40) {
    if (items.value.length < total.value) {
      page.value += 1
      void loadMore()
    }
  }
}

const loadMore = async () => {
  loading.value = true
  try {
    const res = await artifactApi.list({
      page: page.value,
      page_size: PAGE_SIZE,
      artifact_type: activeType.value || undefined,
      conversation_id: props.conversationId || undefined,
      trace_id: outputScope.value === 'message' ? props.traceId || undefined : undefined,
    })
    const data = res.data?.data
    const more = data?.items ?? []
    items.value = items.value.concat(more)
    total.value = data?.total ?? total.value
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '加载更多失败', 'error')
  } finally {
    loading.value = false
  }
}

/** 该产物类型能否在画布中预览（`.ppt`、未知扩展名等只能下载） */
const canPreviewArtifact = (it: ArtifactListItem) =>
  resolveGeneratedPreviewPlan(it.filename).kind !== 'download-only'

/**
 * 下载：追加 `download=1` 强制 attachment。
 * 后端对 html / pdf / 图片默认给 inline，不追加时「下载」会退化成新标签页预览。
 */
const downloadArtifact = (it: ArtifactListItem) => {
  if (!it.download_url) {
    showToast('该产出物缺少下载地址', 'warning')
    return
  }
  const href = buildArtifactDownloadUrl(resolveGeneratedFileHref(it.download_url))
  window.open(href, '_blank', 'noopener,noreferrer')
}

/** 预览：交给父组件在画布中打开（画布状态归 useWorkspaceCanvas，抽屉不构造画布数据） */
const previewArtifact = (it: ArtifactListItem) => {
  if (!it.download_url) {
    showToast('该产出物缺少下载地址', 'warning')
    return
  }
  if (!canPreviewArtifact(it)) {
    showToast('该格式暂不支持预览，已为你下载原文件', 'info')
    downloadArtifact(it)
    return
  }
  emit('preview-file', it)
}

/** 主体点击：可预览则预览，否则直接下载 —— 不留「点了没反应」的死区 */
const activateArtifact = (it: ArtifactListItem) => {
  if (canPreviewArtifact(it)) previewArtifact(it)
  else downloadArtifact(it)
}

const closeDrawer = () => {
  modelValue.value = false
}

const keyHandler = (e: KeyboardEvent) => {
  if (e.key === 'Escape' && modelValue.value) closeDrawer()
}

watch(modelValue, (open) => {
  if (open) {
    activeTab.value = props.initialTab
    outputScope.value = 'conversation'
    if (activeTab.value === 'files') refresh()
  }
})

watch(() => props.initialTab, (tab) => {
  if (modelValue.value) activeTab.value = tab
})

watch(() => props.traceId, (traceId, previousTraceId) => {
  if (!modelValue.value || traceId === previousTraceId) return
  outputScope.value = 'conversation'
  if (activeTab.value === 'files') refresh()
})

const selectReusableResult = (result: ReusableResultListItem) => {
  emit('select-reusable-result', result)
}

onMounted(() => {
  window.addEventListener('keydown', keyHandler)
})

onUnmounted(() => {
  window.removeEventListener('keydown', keyHandler)
})
</script>

<template>
  <Teleport to="body">
    <div
      v-show="modelValue"
      class="fixed inset-0 z-[125] overflow-hidden"
    >
      <transition
        enter-active-class="ease-out duration-300"
        enter-from-class="opacity-0"
        enter-to-class="opacity-100"
        leave-active-class="ease-in duration-200"
        leave-from-class="opacity-100"
        leave-to-class="opacity-0"
      >
        <div
          v-show="modelValue"
          class="absolute inset-0 bg-gray-500/30 backdrop-blur-xs transition-opacity"
          @click="closeDrawer"
        />
      </transition>

      <transition
        enter-active-class="ease-out duration-300"
        enter-from-class="translate-x-full"
        enter-to-class="translate-x-0"
        leave-active-class="ease-in duration-200"
        leave-from-class="translate-x-0"
        leave-to-class="translate-x-full"
      >
        <div
          v-show="modelValue"
          :class="[
            isMobile
              ? 'absolute inset-0 flex w-full flex-col overflow-hidden'
              : 'absolute inset-y-0 right-0 pl-0 sm:pl-10 max-w-full flex',
          ]"
        >
          <div
            :class="[
              'flex flex-col bg-white dark:bg-gray-900 shadow-2xl border-gray-200 dark:border-gray-700 pointer-events-auto min-w-0',
              isMobile ? 'w-full h-full border-0' : 'w-96 sm:w-[28rem] max-w-full h-full border-l',
            ]"
          >
            <!-- Header -->
            <div class="px-4 py-3 border-b border-gray-100 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50 flex items-center justify-between gap-3 flex-shrink-0">
              <div class="flex items-center gap-2 min-w-0">
                <svg class="h-5 w-5 text-gray-500 dark:text-gray-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                <h3 class="text-sm font-black text-gray-800 dark:text-gray-100 truncate">{{ activeTab === 'files' ? '我的产出' : '可复用结果' }}</h3>
                <span v-if="activeTab === 'files' && total > 0" class="text-[10px] text-gray-400 font-mono bg-white dark:bg-gray-700 px-1.5 py-0.5 rounded border border-gray-100 dark:border-gray-600 flex-shrink-0">
                  {{ total }}
                </span>
              </div>
              <button
                type="button"
                class="text-gray-400 hover:text-gray-500 dark:hover:text-gray-300 p-1.5 rounded-md hover:bg-gray-150 dark:hover:bg-gray-800 transition-colors"
                title="关闭 (Esc)"
                aria-label="关闭产出物"
                @click="closeDrawer"
              >
                <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <!-- Output tabs -->
            <div class="flex items-center gap-1 px-4 py-2 border-b border-gray-100 dark:border-gray-700 bg-white dark:bg-gray-900/40 flex-shrink-0 overflow-x-auto no-scrollbar">
              <button
                type="button"
                class="px-2.5 py-1 rounded-lg text-xs font-semibold whitespace-nowrap transition-colors"
                :class="activeTab === 'files' ? 'bg-primary/10 text-primary' : 'text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'"
                @click="activeTab = 'files'; refresh()"
              >
                文件产物
              </button>
              <button
                type="button"
                class="px-2.5 py-1 rounded-lg text-xs font-semibold whitespace-nowrap transition-colors"
                :class="activeTab === 'reusable' ? 'bg-primary/10 text-primary' : 'text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'"
                @click="activeTab = 'reusable'"
              >
                可复用结果
              </button>
            </div>

            <!-- Output scope -->
            <div v-if="props.traceId" class="flex items-center gap-1 px-4 py-2 border-b border-gray-100 dark:border-gray-700 bg-white dark:bg-gray-900/40 flex-shrink-0">
              <span class="mr-1 text-[11px] text-gray-400 dark:text-gray-500">查看范围</span>
              <button
                type="button"
                class="rounded-lg px-2.5 py-1 text-xs font-semibold whitespace-nowrap transition-colors"
                :class="outputScope === 'conversation' ? 'bg-primary/10 text-primary' : 'text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'"
                @click="changeScope('conversation')"
              >
                本会话全部
              </button>
              <button
                type="button"
                class="rounded-lg px-2.5 py-1 text-xs font-semibold whitespace-nowrap transition-colors"
                :class="outputScope === 'message' ? 'bg-primary/10 text-primary' : 'text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'"
                @click="changeScope('message')"
              >
                本次消息
              </button>
            </div>

            <!-- File type filter -->
            <div v-if="activeTab === 'files'" class="flex items-center gap-1 px-4 py-2 border-b border-gray-100 dark:border-gray-700 bg-white dark:bg-gray-900/40 flex-shrink-0 overflow-x-auto no-scrollbar">
              <button
                v-for="t in ARTIFACT_TYPES"
                :key="t.value"
                type="button"
                class="px-2.5 py-1 rounded-lg text-xs font-semibold whitespace-nowrap transition-colors"
                :class="
                  activeType === t.value
                    ? 'bg-primary/10 text-primary'
                    : 'text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'
                "
                @click="changeType(t.value)"
              >
                {{ t.label }}
              </button>
            </div>

            <!-- Body -->
            <div
              ref="bodyRef"
              class="flex-1 overflow-y-auto overscroll-y-contain p-3 sm:p-4 bg-white dark:bg-gray-900/60 min-h-0 touch-pan-y"
              @scroll.passive="scrollHandler"
            >
              <ReusableResultList
                v-if="activeTab === 'reusable'"
                :conversation-id="props.conversationId || ''"
                :trace-id="props.traceId"
                :scope="outputScope"
                :selected-result-id="props.selectedResultId"
                :focused-result-id="props.focusedResultId"
                :reused-result-id="props.reusedResultId"
                @select="selectReusableResult"
              />

              <template v-else>
              <!-- Loading -->
              <div v-if="loading && items.length === 0" class="flex flex-col items-center justify-center py-16 text-gray-400 gap-3">
                <svg class="h-6 w-6 text-primary animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                <span class="text-xs font-medium">加载中...</span>
              </div>

              <!-- Error -->
              <div v-else-if="error && items.length === 0" class="flex flex-col items-center justify-center py-16 text-gray-500 dark:text-gray-400 gap-3">
                <svg class="h-8 w-8 text-amber-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <span class="text-xs font-medium text-center">{{ error }}</span>
                <button
                  type="button"
                  class="text-xs font-semibold text-primary hover:underline"
                  @click="refresh"
                >
                  重试
                </button>
              </div>

              <!-- Empty -->
              <div v-else-if="items.length === 0" class="flex flex-col items-center justify-center py-16 text-gray-400 gap-3">
                <svg class="h-8 w-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" />
                </svg>
                <span class="text-xs font-medium">暂无 AI 产出物</span>
              </div>

              <!-- List -->
              <ul v-else class="flex flex-col gap-2">
                <li v-for="it in items" :key="it.id" class="group">
                  <div class="w-full flex items-start gap-2 rounded-xl p-3 transition-colors border border-transparent hover:bg-gray-50 dark:hover:bg-gray-800/60 hover:border-gray-100 dark:hover:border-gray-700">
                    <!-- 主体：点击 = 预览（不可预览的类型改为直接下载） -->
                    <button
                      type="button"
                      class="flex flex-1 min-w-0 items-start gap-3 text-left"
                      :title="canPreviewArtifact(it) ? `预览 ${it.filename}` : `下载 ${it.filename}`"
                      @click="activateArtifact(it)"
                    >
                      <span
                        :class="[
                          'flex-shrink-0 w-10 h-10 rounded-lg flex items-center justify-center border mt-0.5 text-lg',
                          fileVisuals[it.id]?.iconBg || 'bg-gray-100 dark:bg-gray-800',
                        ]"
                      >
                        {{ fileVisuals[it.id]?.icon || '📎' }}
                      </span>
                      <span class="flex-1 min-w-0">
                        <span class="block text-sm font-semibold text-gray-800 dark:text-gray-100 truncate">
                          {{ it.filename }}
                        </span>
                        <span class="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-gray-400 dark:text-gray-500">
                          <span class="px-1 py-px rounded bg-gray-100 dark:bg-gray-800 text-[10px] font-semibold">{{ typeLabel(it.artifact_type) }}</span>
                          <span>{{ formatSize(it.size) }}</span>
                          <span v-if="formatTime(it.created_at)">{{ formatTime(it.created_at) }}</span>
                          <span
                            class="rounded px-1 py-px text-[10px] font-semibold"
                            :class="it.trace_id === props.traceId ? 'bg-primary/10 text-primary' : 'bg-gray-100 dark:bg-gray-800'"
                            :title="artifactTraceTitle(it.trace_id)"
                          >
                            {{ artifactTraceLabel(it) }}
                          </span>
                        </span>
                      </span>
                    </button>
                    <!-- 动作区：预览 / 下载（桌面端 hover 或聚焦时显现，触屏常显） -->
                    <span class="flex items-center gap-0.5 flex-shrink-0 self-center">
                      <button
                        v-if="canPreviewArtifact(it)"
                        type="button"
                        class="p-1.5 rounded-lg text-gray-400 hover:text-primary hover:bg-gray-100 dark:hover:bg-gray-800 transition-all focus-visible:opacity-100"
                        :class="isMobile ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'"
                        :title="`预览 ${it.filename}`"
                        :aria-label="`预览 ${it.filename}`"
                        @click.stop="previewArtifact(it)"
                      >
                        <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                        </svg>
                      </button>
                      <button
                        type="button"
                        class="p-1.5 rounded-lg text-gray-400 hover:text-primary hover:bg-gray-100 dark:hover:bg-gray-800 transition-all focus-visible:opacity-100"
                        :class="isMobile ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'"
                        :title="`下载 ${it.filename}`"
                        :aria-label="`下载 ${it.filename}`"
                        @click.stop="downloadArtifact(it)"
                      >
                        <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                        </svg>
                      </button>
                    </span>
                  </div>
                </li>
                <li v-if="loading" class="flex items-center justify-center py-4">
                  <svg class="h-5 w-5 text-primary animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                  </svg>
                </li>
              </ul>
              </template>
            </div>
          </div>
        </div>
      </transition>
    </div>
  </Teleport>
</template>

<style scoped>
.no-scrollbar::-webkit-scrollbar {
  display: none;
}
.no-scrollbar {
  -ms-overflow-style: none;
  scrollbar-width: none;
}
</style>
