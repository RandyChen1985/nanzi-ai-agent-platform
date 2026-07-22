<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { metadataApi, type DbConnectionConfig } from '../../api/metadata'
import { useToast } from '../../composables/useToast'

export type ProfilingTaskInfo = {
  status: number
  total_tables: number
  processed_tables: number
  current_table?: string
  error_message?: string
}

const props = defineProps<{
  show: boolean
  config: DbConnectionConfig | null
  profilingTask?: ProfilingTaskInfo | null
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

const { showToast } = useToast()

const profileStats = ref<any>(null)
const items = ref<any[]>([])
const loading = ref(false)
const searchQ = ref('')
const selectedTag = ref<string | null>(null)
const listMode = ref<'completed' | 'all'>('completed')
const sortMode = ref<'default' | 'relevance' | 'confidence_desc' | 'confidence_asc' | 'name_asc' | 'name_desc' | 'term_asc'>('default')
const page = ref(1)
const pageSize = 40
const total = ref(0)
const pages = ref(0)

const previewTable = ref<string | null>(null)
const previewDetail = ref<any>(null)
const previewLoading = ref(false)

const expandedDataTable = ref<string | null>(null)
const dataPreviewLoading = ref(false)
const dataPreviewError = ref('')
const dataPreviewData = ref<{ columns: { name: string }[]; rows: any[][]; total_count?: number | null } | null>(null)

const relatedTables = ref<{
  table_name: string
  ai_term?: string
  confidence: number
  reason?: string
  join_hint?: string
}[]>([])
const relatedLoading = ref(false)
const relatedMessage = ref<string | null>(null)

const resultsListRef = ref<HTMLElement | null>(null)

const togglingIgnore = ref<Record<string, boolean>>({})

let searchTimer: ReturnType<typeof setTimeout> | null = null

const totalPages = computed(() => Math.max(1, pages.value || Math.ceil(total.value / pageSize)))
const rowSerial = (idx: number) => (page.value - 1) * pageSize + idx + 1
const previewRowSerial = (idx: number) => idx + 1

const scrollResultsToTop = () => {
  nextTick(() => {
    if (resultsListRef.value) resultsListRef.value.scrollTop = 0
  })
}
const previewItem = computed(() => items.value.find((i) => i.table_name === previewTable.value))
const availableTags = computed(() => profileStats.value?.tags || [])

const refreshing = ref(false)

const sortParams = computed(() => {
  switch (sortMode.value) {
    case 'relevance':
      return { sort_by: 'relevance' as const, sort_order: 'desc' as const }
    case 'confidence_desc':
      return { sort_by: 'confidence_score' as const, sort_order: 'desc' as const }
    case 'confidence_asc':
      return { sort_by: 'confidence_score' as const, sort_order: 'asc' as const }
    case 'name_desc':
      return { sort_by: 'table_name' as const, sort_order: 'desc' as const }
    case 'term_asc':
      return { sort_by: 'ai_term' as const, sort_order: 'asc' as const }
    case 'name_asc':
      return { sort_by: 'table_name' as const, sort_order: 'asc' as const }
    default:
      return { sort_by: 'default' as const, sort_order: 'desc' as const }
  }
})

const formatProfileTime = (iso?: string | null) => {
  if (!iso) return ''
  return new Date(iso).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const lastProfiledAtLabel = computed(() => {
  const at = profileStats.value?.last_profiled_at
  if (!at) return ''
  if (props.profilingTask?.status === 1) {
    return `最近更新：${formatProfileTime(at)}`
  }
  return `上次摸排：${formatProfileTime(at)}`
})

const quoteTableName = (tableName: string) => {
  const dbType = props.config?.db_type || 'mysql'
  if (dbType === 'clickhouse') return tableName
  if (dbType === 'postgresql' || dbType === 'postgres' || dbType === 'pg' || dbType === 'oracle' || dbType === 'sqlserver' || dbType === 'mssql') {
    // PostgreSQL 的 schema.table 需要逐段引用，不能整体包成一个标识符。
    return tableName.split('.').map((part) => `"${part.replace(/"/g, '""')}"`).join('.')
  }
  return `\`${tableName.replace(/`/g, '``')}\``
}

const colName = (col: string | { name: string }) => (typeof col === 'string' ? col : col.name)

const loadStats = async () => {
  if (!props.config) return
  try {
    const res = await metadataApi.getDbTableProfileStats(props.config.id)
    profileStats.value = res.data
  } catch {
    profileStats.value = null
  }
}

const loadResults = async (opts?: { silent?: boolean }) => {
  if (!props.config) return
  const silent = opts?.silent && items.value.length > 0
  if (silent) {
    refreshing.value = true
  } else {
    loading.value = true
  }
  try {
    const res = await metadataApi.listDbTableProfiles(props.config.id, {
      page: page.value,
      page_size: pageSize,
      q: searchQ.value.trim() || undefined,
      tag: selectedTag.value || undefined,
      status: listMode.value === 'completed' ? 2 : undefined,
      ...sortParams.value,
    })
    const data = res.data || {}
    items.value = data.items || []
    total.value = data.total || 0
    pages.value = data.pages || 0
    page.value = data.page || page.value

    if (expandedDataTable.value && !items.value.some((i) => i.table_name === expandedDataTable.value)) {
      expandedDataTable.value = null
      dataPreviewData.value = null
      dataPreviewError.value = ''
    }
    if (previewTable.value && !items.value.some((i) => i.table_name === previewTable.value)) {
      previewTable.value = items.value[0]?.table_name || null
      previewDetail.value = null
      if (previewTable.value) {
        loadPreviewDetail(previewTable.value)
        loadRelatedTables(previewTable.value)
      }
    } else if (!previewTable.value && items.value.length) {
      previewTable.value = items.value[0].table_name
      loadPreviewDetail(previewTable.value)
      loadRelatedTables(previewTable.value)
    }
  } catch {
    if (!silent) {
      showToast('获取摸排结果失败', 'error')
      items.value = []
      total.value = 0
    }
  } finally {
    if (silent) {
      refreshing.value = false
    } else {
      loading.value = false
    }
    scrollResultsToTop()
  }
}

const refresh = async (opts?: { silent?: boolean }) => {
  if (!props.show || !props.config) return
  await Promise.all([loadStats(), loadResults(opts)])
}

const resetState = () => {
  profileStats.value = null
  items.value = []
  searchQ.value = ''
  selectedTag.value = null
  listMode.value = 'completed'
  sortMode.value = 'default'
  page.value = 1
  total.value = 0
  pages.value = 0
  previewTable.value = null
  previewDetail.value = null
  expandedDataTable.value = null
  dataPreviewData.value = null
  dataPreviewError.value = ''
  relatedTables.value = []
  relatedMessage.value = null
}

const scheduleSearch = () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    loadResults({ silent: true })
  }, 300)
}

const setListMode = async (mode: 'completed' | 'all') => {
  if (listMode.value === mode) return
  listMode.value = mode
  selectedTag.value = null
  page.value = 1
  await loadResults({ silent: true })
}

const setSortMode = async (sort: typeof sortMode.value) => {
  if (sortMode.value === sort) return
  sortMode.value = sort
  page.value = 1
  await loadResults({ silent: true })
}

const selectTag = async (tag: string | null) => {
  selectedTag.value = selectedTag.value === tag ? null : tag
  page.value = 1
  await loadResults({ silent: true })
}

const loadRelatedTables = async (tableName: string) => {
  if (!props.config) return
  relatedLoading.value = true
  relatedTables.value = []
  relatedMessage.value = null
  try {
    const res = await metadataApi.getDbTableProfileRelated(props.config.id, tableName)
    relatedTables.value = res.data?.items || []
    relatedMessage.value = res.data?.message || null
  } catch {
    relatedTables.value = []
    relatedMessage.value = '加载关联表推荐失败'
  } finally {
    relatedLoading.value = false
  }
}

const loadPreviewDetail = async (tableName: string) => {
  if (!props.config) return
  previewLoading.value = true
  previewDetail.value = null
  try {
    const res = await metadataApi.getDbTableProfileDetail(props.config.id, tableName)
    previewDetail.value = res.data
  } catch {
    previewDetail.value = null
  } finally {
    previewLoading.value = false
  }
}

const onRowClick = (profile: any) => {
  if (profile.status !== 2) return
  previewTable.value = profile.table_name
  loadPreviewDetail(profile.table_name)
  loadRelatedTables(profile.table_name)
}

const focusRelatedTable = (tableName: string) => {
  const profile = items.value.find((i) => i.table_name === tableName)
  if (profile) {
    onRowClick(profile)
    return
  }
  previewTable.value = tableName
  loadPreviewDetail(tableName)
  loadRelatedTables(tableName)
}

const toggleDataPreview = async (tableName: string) => {
  if (expandedDataTable.value === tableName) {
    expandedDataTable.value = null
    dataPreviewData.value = null
    dataPreviewError.value = ''
    return
  }
  if (!props.config) return
  expandedDataTable.value = tableName
  dataPreviewLoading.value = true
  dataPreviewError.value = ''
  dataPreviewData.value = null
  try {
    const res = await metadataApi.debugDbConnectionSql(
      props.config.id,
      `SELECT * FROM ${quoteTableName(tableName)}`,
      10,
      true
    )
    if (res.data?.code === 200) {
      dataPreviewData.value = res.data.data
    } else {
      dataPreviewError.value = res.data?.message || '预览失败'
    }
  } catch (e: any) {
    dataPreviewError.value = e.response?.data?.detail || e.message || '预览失败'
  } finally {
    dataPreviewLoading.value = false
  }
}

const toggleProfileIgnore = async (profile: any) => {
  if (!props.config) return
  const tableName = profile.table_name
  const nextVal = profile.is_ignored === 1 ? 0 : 1
  togglingIgnore.value[tableName] = true
  try {
    await metadataApi.toggleDbTableProfileIgnore(props.config.id, tableName, nextVal)
    profile.is_ignored = nextVal
    showToast(`已${nextVal === 1 ? '忽略' : '启用'}表 “${tableName}”`, 'success')
  } catch {
    showToast('更新忽略状态失败', 'error')
  } finally {
    togglingIgnore.value[tableName] = false
  }
}

const confidenceClass = (score?: number) => {
  if (score == null) return 'bg-gray-100 text-gray-500'
  if (score >= 80) return 'bg-emerald-100 text-emerald-700'
  if (score >= 60) return 'bg-amber-100 text-amber-700'
  return 'bg-red-100 text-red-700'
}

const onKeydown = (e: KeyboardEvent) => {
  if (e.key === 'Escape') emit('close')
}

onMounted(() => {
  document.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  if (searchTimer) clearTimeout(searchTimer)
  document.removeEventListener('keydown', onKeydown)
})

watch(
  () => [props.show, props.config?.id] as const,
  async ([visible]) => {
    if (visible && props.config) {
      resetState()
      loading.value = true
      try {
        await refresh()
      } finally {
        loading.value = false
      }
    }
  },
  { immediate: true }
)

watch(
  () => props.profilingTask?.processed_tables,
  () => {
    if (props.show && props.config && props.profilingTask?.status === 1) {
      refresh({ silent: true })
    }
  }
)

watch(page, () => {
  if (props.show) loadResults({ silent: true })
})

defineExpose({ refresh })
</script>

<template>
  <div
    v-if="show && config"
    class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-gray-900/60 backdrop-blur-sm"
    @click.self="emit('close')"
  >
    <div class="bg-white rounded-2xl shadow-2xl w-full max-w-6xl h-[85vh] flex flex-col overflow-hidden border border-gray-100">
      <!-- Header -->
      <div class="px-5 py-3 border-b flex items-center justify-between shrink-0 bg-gradient-to-r from-primary/5 to-white">
        <div class="flex items-center gap-2.5 min-w-0">
          <div class="p-1.5 rounded-lg bg-primary text-white shrink-0 text-sm">🤖</div>
          <div class="min-w-0">
            <h3 class="font-bold text-gray-900 text-sm truncate">数据源摸排资产：{{ config.name }}</h3>
            <p class="text-[10px] text-gray-500">业务备注 · 字段画像 · 数据预览</p>
            <p v-if="lastProfiledAtLabel" class="text-[10px] text-gray-400 mt-0.5">{{ lastProfiledAtLabel }}</p>
          </div>
        </div>
        <button type="button" class="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg shrink-0" @click="emit('close')">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
        </button>
      </div>

      <!-- Stats strip -->
      <div v-if="profileStats" class="px-5 py-2 border-b bg-gray-50/80 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] shrink-0">
        <span>已成功画像 <strong class="text-gray-800">{{ profileStats.success_count ?? 0 }}</strong>/{{ profileStats.total ?? 0 }}</span>
        <span class="text-gray-300">|</span>
        <span>物理表 <strong>{{ profileStats.table_count ?? 0 }}</strong></span>
        <span>视图 <strong>{{ profileStats.view_count ?? 0 }}</strong></span>
        <span>字段画像 <strong>{{ profileStats.field_count ?? 0 }}</strong></span>
        <span
          v-if="profilingTask?.status === 1"
          class="text-blue-600 font-bold"
        >
          · 摸排中 {{ profilingTask.processed_tables }}/{{ profilingTask.total_tables }}
          <span v-if="profilingTask.current_table" class="font-mono font-normal">({{ profilingTask.current_table }})</span>
        </span>
        <span v-else-if="profilingTask?.status === 4" class="text-amber-600 font-bold">
          · 已中断，保留 {{ profileStats.success_count ?? profilingTask.processed_tables }} 张
        </span>
      </div>

      <!-- Search -->
      <div class="px-5 py-2.5 border-b shrink-0 flex items-center gap-2">
        <div class="relative flex-1 min-w-0">
          <svg class="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
          </svg>
          <input
            v-model="searchQ"
            type="search"
            placeholder="搜索表名 / 业务备注 / 描述 / 标签..."
            class="w-full pl-9 pr-4 py-2 text-sm border border-gray-200 rounded-xl focus:ring-2 focus:ring-primary/30 focus:border-primary outline-none bg-gray-50 focus:bg-white"
            @input="scheduleSearch"
          >
        </div>
        <select
          :value="sortMode"
          class="shrink-0 text-xs font-semibold text-gray-600 bg-white border border-gray-200 rounded-xl px-3 py-2 focus:outline-none focus:ring-2 focus:ring-primary/20"
          title="排序方式"
          @change="setSortMode(($event.target as HTMLSelectElement).value as typeof sortMode)"
        >
          <option value="default">默认排序</option>
          <option value="relevance">相关度优先</option>
          <option value="confidence_desc">可信度 高→低</option>
          <option value="confidence_asc">可信度 低→高</option>
          <option value="name_asc">表名 A→Z</option>
          <option value="name_desc">表名 Z→A</option>
          <option value="term_asc">中文术语 A→Z</option>
        </select>
      </div>

      <div class="flex flex-1 min-h-0">
        <!-- Left filters -->
        <div class="w-44 shrink-0 border-r bg-gray-50/80 flex flex-col overflow-y-auto custom-scrollbar">
          <div class="p-2 space-y-1">
            <div class="px-1 text-[10px] font-bold text-gray-400 uppercase mb-1">显示范围</div>
            <button
              type="button"
              class="w-full text-left px-2.5 py-1.5 rounded-lg text-xs font-semibold transition-colors"
              :class="listMode === 'completed' ? 'bg-primary text-white' : 'text-gray-600 hover:bg-white'"
              @click="setListMode('completed')"
            >
              仅已完成 ({{ profileStats?.success_count ?? 0 }})
            </button>
            <button
              type="button"
              class="w-full text-left px-2.5 py-1.5 rounded-lg text-xs font-semibold transition-colors"
              :class="listMode === 'all' ? 'bg-primary text-white' : 'text-gray-600 hover:bg-white'"
              @click="setListMode('all')"
            >
              全部表 ({{ profileStats?.total ?? 0 }})
            </button>
          </div>
          <div v-if="availableTags.length" class="border-t p-2 flex-1 min-h-0">
            <div class="px-1 text-[10px] font-bold text-gray-400 uppercase mb-1">标签</div>
            <button
              type="button"
              class="w-full text-left px-2 py-1 rounded-md text-[11px] mb-0.5 transition-colors"
              :class="!selectedTag ? 'bg-primary/10 text-primary font-bold' : 'text-gray-600 hover:bg-white'"
              @click="selectTag(null)"
            >
              全部 ({{ listMode === 'completed' ? (profileStats?.success_count ?? total) : (profileStats?.total ?? total) }})
            </button>
            <button
              v-for="tag in availableTags.slice(0, 24)"
              :key="tag.name"
              type="button"
              class="w-full text-left px-2 py-1 rounded-md text-[11px] truncate transition-colors"
              :class="selectedTag === tag.name ? 'bg-primary/10 text-primary font-bold' : 'text-gray-600 hover:bg-white'"
              :title="`${tag.name} (${tag.count})`"
              @click="selectTag(tag.name)"
            >
              {{ tag.name }} <span class="text-gray-400">({{ tag.count }})</span>
            </button>
          </div>
        </div>

        <!-- Center list -->
        <div class="flex-1 flex flex-col min-w-0">
          <div class="px-4 py-2 border-b flex items-center justify-between text-[11px] text-gray-500 shrink-0">
            <span>共 {{ total }} 张表</span>
            <div v-if="totalPages > 1" class="flex items-center gap-2">
              <button type="button" class="px-2 py-0.5 rounded border border-gray-200 hover:bg-gray-50 disabled:opacity-40" :disabled="page <= 1 || loading" @click="page--">上一页</button>
              <span>{{ page }} / {{ totalPages }}</span>
              <button type="button" class="px-2 py-0.5 rounded border border-gray-200 hover:bg-gray-50 disabled:opacity-40" :disabled="page >= totalPages || loading" @click="page++">下一页</button>
            </div>
          </div>

          <div ref="resultsListRef" class="flex-1 overflow-y-auto custom-scrollbar relative">
            <div v-if="loading && !items.length" class="py-16 text-center text-gray-400 text-sm">加载中...</div>
            <div v-else-if="!items.length" class="py-16 text-center text-gray-400 text-xs px-6">
              <template v-if="listMode === 'completed' && profilingTask?.status === 1">暂无已完成画像，请稍候…</template>
              <template v-else>暂无匹配的摸排表记录</template>
            </div>

            <div v-else :class="refreshing ? 'opacity-60 pointer-events-none' : ''">
            <div
              v-for="(profile, idx) in items"
              :key="profile.table_name"
              class="border-b"
              :class="[
                previewTable === profile.table_name ? 'bg-primary/5' : '',
                profile.is_ignored === 1 ? 'opacity-75' : '',
              ]"
            >
              <div
                class="px-4 py-2.5 flex items-start gap-3 group transition-colors"
                :class="profile.status === 2 ? 'cursor-pointer' : 'cursor-default'"
                @click="onRowClick(profile)"
              >
                <span
                  class="shrink-0 w-7 text-right text-[10px] font-semibold text-gray-400 tabular-nums pt-1"
                  :title="`第 ${rowSerial(idx)} 条`"
                >{{ rowSerial(idx) }}</span>
                <div class="shrink-0 pt-0.5" @click.stop>
                  <button
                    type="button"
                    :disabled="togglingIgnore[profile.table_name]"
                    class="relative inline-flex h-4 w-7 shrink-0 cursor-pointer rounded-full border border-transparent transition-colors"
                    :class="profile.is_ignored === 1 ? 'bg-red-500' : 'bg-emerald-500'"
                    :title="profile.is_ignored === 1 ? '点击恢复启用' : '点击忽略'"
                    @click="toggleProfileIgnore(profile)"
                  >
                    <span
                      class="pointer-events-none inline-block h-3 w-3 transform rounded-full bg-white shadow transition"
                      :class="profile.is_ignored === 1 ? 'translate-x-3' : 'translate-x-0'"
                    />
                  </button>
                </div>

                <div class="min-w-0 flex-1">
                  <div class="flex items-start justify-between gap-2">
                    <div class="min-w-0 flex-1 flex items-center gap-1.5 flex-wrap">
                      <span class="text-sm font-mono font-bold text-gray-800 break-all">{{ profile.table_name }}</span>
                      <span
                        class="text-[9px] px-1 py-0.5 rounded font-black shrink-0"
                        :class="profile.table_type === 'view' ? 'bg-amber-100 text-amber-700' : 'bg-blue-100 text-blue-700'"
                      >{{ profile.table_type === 'view' ? 'VIEW' : 'TABLE' }}</span>
                      <span
                        v-if="profile.confidence_score != null && profile.status === 2"
                        class="text-[9px] px-1 py-0.5 rounded font-bold shrink-0"
                        :class="confidenceClass(profile.confidence_score)"
                      >{{ profile.confidence_score }}分</span>
                      <span v-if="profile.status === 3" class="text-[9px] px-1 py-0.5 rounded bg-red-50 text-red-500 font-bold">失败</span>
                      <span v-else-if="profile.status === 1" class="text-[9px] px-1 py-0.5 rounded bg-blue-50 text-blue-600 font-bold">分析中</span>
                      <span v-else-if="profile.status === 0" class="text-[9px] px-1 py-0.5 rounded bg-gray-100 text-gray-500 font-bold">待处理</span>
                      <span v-if="profile.is_ignored === 1" class="text-[9px] text-red-500 font-bold">已忽略</span>
                    </div>
                    <button
                      v-if="profile.status === 2"
                      type="button"
                      class="shrink-0 flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-semibold border transition-colors"
                      :class="expandedDataTable === profile.table_name
                        ? 'bg-primary text-white border-primary'
                        : 'bg-white text-gray-500 border-gray-200 opacity-0 group-hover:opacity-100 hover:border-primary/40 hover:text-primary'"
                      @click.stop="toggleDataPreview(profile.table_name)"
                    >
                      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/></svg>
                      预览数据
                    </button>
                  </div>
                  <div v-if="profile.ai_term" class="text-xs text-primary font-semibold mt-0.5 break-words">{{ profile.ai_term }}</div>
                  <div v-if="profile.ai_description" class="text-[11px] text-gray-500 mt-0.5 break-words leading-relaxed line-clamp-2">{{ profile.ai_description }}</div>
                  <div v-if="profile.confidence_reason" class="text-[10px] text-gray-400 mt-0.5 truncate" :title="profile.confidence_reason">
                    原因：{{ profile.confidence_reason }}
                  </div>
                  <div v-if="profile.ai_tags?.length" class="flex flex-wrap gap-1 mt-1">
                    <span
                      v-for="tag in profile.ai_tags.slice(0, 6)"
                      :key="tag"
                      class="text-[9px] px-1.5 py-0.5 bg-gray-100 rounded-full text-gray-500"
                    >{{ tag }}</span>
                  </div>
                </div>
              </div>

              <!-- Inline data preview -->
              <div v-if="expandedDataTable === profile.table_name" class="px-4 pb-3 ml-10 mr-2" @click.stop>
                <div v-if="dataPreviewLoading" class="py-4 text-center text-gray-400 text-xs">
                  <span class="inline-block w-4 h-4 border-2 border-primary/20 border-t-primary rounded-full animate-spin mr-2 align-middle" />
                  正在加载前 10 条数据...
                </div>
                <div v-else-if="dataPreviewError" class="py-2 px-3 bg-red-50 border border-red-100 rounded-lg text-red-600 text-[11px]">
                  {{ dataPreviewError }}
                </div>
                <div v-else-if="dataPreviewData" class="bg-white border border-gray-200 rounded-lg overflow-hidden shadow-sm">
                  <div class="px-3 py-1.5 border-b bg-gray-50 flex items-center justify-between">
                    <span class="text-[10px] font-bold text-gray-400">数据预览 (最多 10 行)</span>
                    <span class="text-[10px] text-gray-400">
                      <template v-if="dataPreviewData.total_count != null">
                        {{ dataPreviewData.rows?.length || 0 }}/{{ dataPreviewData.total_count }} 条
                      </template>
                      <template v-else>{{ dataPreviewData.rows?.length || 0 }} 行</template>
                    </span>
                  </div>
                  <div class="overflow-x-auto custom-scrollbar max-h-48">
                    <table class="min-w-full divide-y divide-gray-100">
                      <thead class="bg-gray-50 sticky top-0">
                        <tr>
                          <th class="px-2 py-1.5 text-left text-[9px] font-bold text-gray-400 uppercase whitespace-nowrap w-8">#</th>
                          <th
                            v-for="col in dataPreviewData.columns"
                            :key="colName(col)"
                            class="px-2 py-1.5 text-left text-[9px] font-bold text-gray-400 uppercase whitespace-nowrap"
                          >{{ colName(col) }}</th>
                        </tr>
                      </thead>
                      <tbody class="divide-y divide-gray-50">
                        <tr v-for="(row, rIdx) in dataPreviewData.rows" :key="rIdx" class="hover:bg-gray-50/80">
                          <td class="px-2 py-1 text-[10px] text-gray-400 tabular-nums font-semibold">{{ previewRowSerial(rIdx) }}</td>
                          <td
                            v-for="(cell, cIdx) in row"
                            :key="cIdx"
                            class="px-2 py-1 text-[10px] text-gray-600 whitespace-nowrap max-w-[160px] truncate"
                            :title="cell === null || cell === undefined ? 'NULL' : String(cell)"
                          >{{ cell === null || cell === undefined ? 'NULL' : cell }}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  <div v-if="!dataPreviewData.rows?.length" class="py-6 text-center text-gray-400 text-[11px] italic">表中暂无数据</div>
                </div>
              </div>
            </div>
            </div>
          </div>
        </div>

        <!-- Right preview panel -->
        <div class="w-72 shrink-0 border-l bg-gray-50/50 flex flex-col overflow-hidden">
          <div class="px-3 py-2 border-b text-[11px] font-bold text-gray-500 shrink-0">表预览</div>
          <div v-if="!previewTable" class="flex-1 flex items-center justify-center text-gray-400 text-xs px-4 text-center">点击左侧表查看字段画像</div>
          <div v-else class="flex-1 overflow-y-auto custom-scrollbar p-3 space-y-2">
            <div class="font-mono text-sm font-bold text-gray-800 break-all">{{ previewTable }}</div>
            <template v-if="previewItem">
              <div v-if="previewItem.ai_term" class="text-xs text-primary font-semibold">{{ previewItem.ai_term }}</div>
              <div v-if="previewItem.ai_description" class="text-[11px] text-gray-600 leading-relaxed">{{ previewItem.ai_description }}</div>
              <div v-if="previewItem.confidence_score != null" class="flex items-center gap-1 text-[10px]">
                <span class="text-gray-400">可信度</span>
                <span class="px-1 py-0.5 rounded font-bold" :class="confidenceClass(previewItem.confidence_score)">{{ previewItem.confidence_score }} 分</span>
              </div>
              <div v-if="previewItem.ai_tags?.length" class="flex flex-wrap gap-1">
                <span v-for="tg in previewItem.ai_tags" :key="tg" class="text-[9px] px-1.5 py-0.5 bg-white border rounded-full text-gray-600">{{ tg }}</span>
              </div>
            </template>

            <!-- Related tables -->
            <div class="mt-2 border border-primary/20 rounded-lg bg-primary/5 overflow-hidden">
              <div class="px-2.5 py-1.5 border-b border-primary/10 flex items-center justify-between gap-2">
                <span class="text-[10px] font-bold text-primary">可能关联的表</span>
              </div>
              <div v-if="relatedLoading" class="px-2.5 py-3 text-[10px] text-gray-400 italic">分析关联中...</div>
              <div v-else-if="!relatedTables.length" class="px-2.5 py-2 text-[10px] text-gray-500 leading-relaxed">
                {{ relatedMessage || '暂无推荐，请确认该表已完成摸排' }}
              </div>
              <ul v-else class="max-h-36 overflow-y-auto custom-scrollbar divide-y divide-primary/10">
                <li
                  v-for="rel in relatedTables"
                  :key="rel.table_name"
                  class="px-2.5 py-2 hover:bg-white/70 transition-colors"
                >
                  <button
                    type="button"
                    class="w-full text-left"
                    @click="focusRelatedTable(rel.table_name)"
                  >
                    <div class="flex items-start justify-between gap-2">
                      <div class="min-w-0 flex-1">
                        <div class="font-mono text-[10px] font-bold text-gray-800 truncate" :title="rel.table_name">{{ rel.table_name }}</div>
                        <div v-if="rel.ai_term" class="text-[9px] text-primary truncate">{{ rel.ai_term }}</div>
                        <div class="text-[9px] text-gray-500 mt-0.5 line-clamp-2" :title="rel.reason">{{ rel.reason }}</div>
                      </div>
                      <span class="shrink-0 text-[9px] font-bold text-emerald-700 bg-emerald-50 px-1 py-0.5 rounded">{{ Math.round(rel.confidence * 100) }}%</span>
                    </div>
                    <div v-if="rel.join_hint" class="mt-1 text-[8px] font-mono text-gray-400 truncate" :title="rel.join_hint">{{ rel.join_hint }}</div>
                  </button>
                </li>
              </ul>
            </div>

            <div v-if="previewLoading" class="text-[11px] text-gray-400 italic py-4 text-center">加载字段画像...</div>
            <div v-else-if="previewDetail?.columns_profile?.length" class="mt-2 border border-gray-100 rounded-lg overflow-hidden bg-white">
              <div class="px-2 py-1.5 bg-gray-50 border-b text-[10px] font-bold text-gray-400">
                字段画像 ({{ previewDetail.columns_profile.length }})
              </div>
              <div class="max-h-[45vh] overflow-y-auto custom-scrollbar">
                <table class="w-full text-left text-[10px]">
                  <thead class="sticky top-0 bg-gray-50 text-gray-400">
                    <tr>
                      <th class="px-2 py-1 font-bold border-b w-[28%]">物理字段</th>
                      <th class="px-2 py-1 font-bold border-b w-[22%]">术语</th>
                      <th class="px-2 py-1 font-bold border-b">说明</th>
                    </tr>
                  </thead>
                  <tbody class="divide-y divide-gray-50">
                    <tr v-for="col in previewDetail.columns_profile" :key="col.name || col.column_name" class="hover:bg-gray-50 align-top">
                      <td class="px-2 py-1 font-mono text-gray-700">{{ col.name || col.column_name }}</td>
                      <td class="px-2 py-1 text-primary">{{ col.term || '-' }}</td>
                      <td class="px-2 py-1 text-gray-500 leading-snug">{{ col.desc || col.description || '-' }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
            <div v-else-if="previewItem?.status !== 2" class="text-[11px] text-gray-400 italic">该表尚未完成画像分析</div>
          </div>
        </div>
      </div>

      <!-- Footer -->
      <div class="px-5 py-2.5 border-t bg-white shrink-0 flex justify-end">
        <button type="button" class="px-4 py-1.5 text-sm border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50" @click="emit('close')">
          关闭窗口
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar { width: 5px; height: 5px; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 6px; }
</style>
