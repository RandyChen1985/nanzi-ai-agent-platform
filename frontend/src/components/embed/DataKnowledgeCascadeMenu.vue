<script setup lang="ts">
/**
 * 加号菜单「数据&知识库」浮层：数据集 / 知识库两个 tab，点选即挂到本轮输入框。
 *
 * 组件本身不写附件 chip，只负责拉列表与展示已挂载态，勾选通过事件交给 `ChatInput`
 * —— chip 的格式由 `utils/sessionMountChips` 唯一决定，避免和知识库面板、数据门户漂移。
 */
import { computed, onMounted, ref, watch } from 'vue'
import axios from '@/utils/axios'
import {
  countByVisibility,
  filterAndSortMountableItems,
  hasAnyMountableFilter,
  listMountableDataSources,
  type MountableFilters,
  type MountableSortBy,
} from '@/utils/mountableItemFilter'

export interface MountableItem {
  id: string
  name: string
  description?: string
  /** 以下字段仅供筛选/排序，不参与展示；缺失时相关条件自然不生效 */
  dataSource?: string
  tags?: string[]
  tableCount?: number
  docCount?: number
  visibility?: string
  updatedAt?: string | number
}

type MountTab = 'datasets' | 'knowledge'

const props = withDefaults(
  defineProps<{
    /** 本轮已挂载的元数据集 ID */
    activeDatasetIds?: string[]
    /** 本轮已挂载的知识库 ID */
    activeKnowledgeBaseIds?: string[]
    /**
     * 项目会话资源范围：非空时只列范围内的资源（与知识库面板 / 数据门户同口径）。
     * `null` / 未传表示不限制。
     */
    allowedDatasetIds?: string[] | null
    allowedKnowledgeBaseIds?: string[] | null
    /** 智能体不支持数据查询：数据集 tab 置灰 */
    datasetDisabled?: boolean
    /**
     * 智能体已绑定数据集：查询范围由智能体决定，手动挂载不会生效。
     * 与 datasetDisabled 分开，是因为两者要给不同提示——一个是「做不到」，
     * 一个是「不用做」，混成一句会让用户误以为智能体缺少数据能力。
     */
    datasetLocked?: boolean
    /** 智能体不支持知识库：知识库 tab 置灰 */
    knowledgeDisabled?: boolean
    /** 窄屏级联浮层宽度 */
    compact?: boolean
    /** 移动端底部抽屉内铺满宽度 */
    fullWidth?: boolean
    /** 桌面侧栏浮层：高度铺满左侧加号菜单 */
    fillHeight?: boolean
  }>(),
  {
    activeDatasetIds: () => [],
    activeKnowledgeBaseIds: () => [],
    allowedDatasetIds: null,
    allowedKnowledgeBaseIds: null,
    datasetDisabled: false,
    datasetLocked: false,
    knowledgeDisabled: false,
    compact: false,
    fullWidth: false,
    fillHeight: false,
  },
)

const emit = defineEmits<{
  (e: 'toggle-dataset', id: string, name: string): void
  (e: 'toggle-knowledge-base', id: string): void
}>()

const datasets = ref<MountableItem[]>([])
const knowledgeBases = ref<MountableItem[]>([])
const loadingDatasets = ref(false)
const loadingKnowledge = ref(false)
const datasetError = ref(false)
const knowledgeError = ref(false)
const loadedDatasets = ref(false)
const loadedKnowledge = ref(false)
const searchQuery = ref('')
const dataSourceFilter = ref('')
const visibilityFilter = ref('')
const sortBy = ref<MountableSortBy>('name')

const currentFilters = computed<MountableFilters>(() => ({
  keyword: searchQuery.value,
  // 两个页签各用一个维度，切换时互不影响失效
  dataSource: metaDatasetTab.value ? dataSourceFilter.value : '',
  visibility: metaDatasetTab.value ? '' : visibilityFilter.value,
}))

/**
 * 页签本身是否不可用：只有「智能体不支持数据查询」才把数据集页签整个禁掉。
 * 「已绑定数据集」不禁用页签——用户仍要能切过去看清提示与可选范围，只是列表项不能勾。
 */
const datasetTabDisabled = computed(() => props.datasetDisabled)
/** 当前 tab 的列表项是否全部禁止勾选（含「已绑定数据集，挂了也不生效」） */
const currentItemsDisabled = computed(() =>
  metaDatasetTab.value ? props.datasetDisabled || props.datasetLocked : props.knowledgeDisabled,
)
const defaultTab = (): MountTab => (datasetTabDisabled.value && !props.knowledgeDisabled ? 'knowledge' : 'datasets')
const activeTab = ref<MountTab>(defaultTab())
/** tab 顺序固定：数据集在前（与两个面板的常见用法一致） */
const MOUNT_TABS: MountTab[] = ['datasets', 'knowledge']

const metaDatasetTab = computed(() => activeTab.value === 'datasets')

const toIdSet = (ids?: string[] | null) =>
  Array.isArray(ids) ? new Set(ids.map((id) => String(id))) : null

const allowedDatasetSet = computed(() => toIdSet(props.allowedDatasetIds))
const allowedKnowledgeSet = computed(() => toIdSet(props.allowedKnowledgeBaseIds))

const activeDatasetSet = computed(() => new Set((props.activeDatasetIds || []).map((id) => String(id))))
const activeKnowledgeSet = computed(() => new Set((props.activeKnowledgeBaseIds || []).map((id) => String(id))))

/** 会话资源范围内的资源（范围未设置时即全量） */
// 范围里存的可能是 ID、也可能是名称（`resourceScope` 的条目各字段并存），
// 因此按 id 与名称双向匹配，避免「范围里有、这里却查不到」而误判成失效。
const isWithinScope = (item: MountableItem, allowed: Set<string> | null) =>
  !allowed || allowed.has(item.id) || allowed.has(item.name)

const scopedDatasets = computed(() =>
  datasets.value.filter((item) => isWithinScope(item, allowedDatasetSet.value)),
)
const scopedKnowledgeBases = computed(() =>
  knowledgeBases.value.filter((item) => isWithinScope(item, allowedKnowledgeSet.value)),
)

const filteredDatasets = computed(() =>
  filterAndSortMountableItems(scopedDatasets.value, currentFilters.value, sortBy.value),
)

const filteredKnowledgeBases = computed(() =>
  filterAndSortMountableItems(scopedKnowledgeBases.value, currentFilters.value, sortBy.value),
)

/**
 * 已挂载但不在当前列表里的 ID（库被删 / 权限被收回 / 落在会话资源范围外）。
 * 不静默丢弃：chip 还在输入框里，用户需要知道它已经失效并能一键移除。
 */
const staleDatasetIds = computed(() =>
  props.activeDatasetIds
    .map((id) => String(id))
    .filter((id) => !scopedDatasets.value.some((item) => item.id === id)),
)
const staleKnowledgeIds = computed(() =>
  props.activeKnowledgeBaseIds
    .map((id) => String(id))
    .filter((id) => !scopedKnowledgeBases.value.some((item) => item.id === id)),
)

const currentStaleIds = computed(() => (metaDatasetTab.value ? staleDatasetIds.value : staleKnowledgeIds.value))
const currentLoading = computed(() => (metaDatasetTab.value ? loadingDatasets.value : loadingKnowledge.value))
const currentError = computed(() => (metaDatasetTab.value ? datasetError.value : knowledgeError.value))
const currentItems = computed(() => (metaDatasetTab.value ? filteredDatasets.value : filteredKnowledgeBases.value))

/** 当前页签的完整列表（未过滤），用于「筛选出 N / 共 M」 */
const currentSourceItems = computed(() => (metaDatasetTab.value ? scopedDatasets.value : scopedKnowledgeBases.value))

const dataSourceOptions = computed(() => listMountableDataSources(scopedDatasets.value))
const visibilityCounts = computed(() => countByVisibility(scopedKnowledgeBases.value))
const hasActiveFilters = computed(() => hasAnyMountableFilter(currentFilters.value))

const currentFilterValue = computed({
  get: () => (metaDatasetTab.value ? dataSourceFilter.value : visibilityFilter.value),
  set: (value: string) => {
    if (metaDatasetTab.value) dataSourceFilter.value = value
    else visibilityFilter.value = value
  },
})

const resetFilters = () => {
  searchQuery.value = ''
  dataSourceFilter.value = ''
  visibilityFilter.value = ''
}

const isActive = (id: string) =>
  metaDatasetTab.value ? activeDatasetSet.value.has(id) : activeKnowledgeSet.value.has(id)

const normalizeList = (raw: any): any[] => {
  if (Array.isArray(raw)) return raw
  if (Array.isArray(raw?.data)) return raw.data
  if (Array.isArray(raw?.datasets)) return raw.datasets
  return []
}

const isEnabledStatus = (status: any) =>
  status === undefined || status === null || status === 1 || status === '1' || status === 'active'

const loadDatasets = async (force = false) => {
  if (loadingDatasets.value) return
  if (loadedDatasets.value && !force) return
  loadingDatasets.value = true
  datasetError.value = false
  try {
    const res = await axios.get('/api/portal/metadata/datasets/accessible')
    datasets.value = normalizeList(res.data)
      .filter((item) => isEnabledStatus(item?.status))
      .map((item) => {
        const id = String(item?.id ?? item?.name ?? '').trim()
        return {
          id,
          name: String(item?.display_name || item?.name || item?.dataset_name || id),
          description: String(item?.description || item?.remark || item?.notes || `数据源：${item?.data_source || '默认'}`),
          dataSource: String(item?.data_source || '').trim(),
          tags: Array.isArray(item?.tags) ? item.tags.map(String) : [],
          tableCount: Number(item?.table_count ?? 0) || 0,
          updatedAt: item?.updated_at || item?.created_at || '',
        }
      })
      .filter((item) => item.id)
    loadedDatasets.value = true
  } catch (error) {
    console.warn('[DataKnowledgeMount] 加载数据集失败', error)
    datasetError.value = true
  } finally {
    loadingDatasets.value = false
  }
}

const KNOWLEDGE_PAGE_SIZE = 1000
/** 兜底上限：正常用户 1 页就够，避免极端数据量把浮层拖死 */
const KNOWLEDGE_MAX_PAGES = 10

/**
 * 拉全用户可访问的知识库。
 *
 * **不能**照搬知识库面板的 `page_size: 100`：后端 `/ragflow/datasets` 的列表里混着成百上千
 * 个 `meta-*` 元数据数据集，只取第一页再过滤后可能为空（后端接口注释里专门警告过这一点，
 * 项目口径见「KB Permission Guard」：Users/Roles/知识库页/选择器统一 `page_size: 1000`）。
 * 这里从 1000 起步、按后端返回的 `total` 续拉，保证「没打开过面板也能看到自己有权限的全部库」。
 */
const fetchAllKnowledgeBases = async (): Promise<any[]> => {
  const collected: any[] = []
  let total = Number.POSITIVE_INFINITY
  for (let page = 1; page <= KNOWLEDGE_MAX_PAGES; page += 1) {
    const res = await axios.get('/api/portal/ragflow/datasets', {
      params: { page, page_size: KNOWLEDGE_PAGE_SIZE, include_missing: false },
    })
    const raw = res.data
    if (!raw || raw.code !== 0) break
    const list = normalizeList(raw.data?.datasets || raw.data)
    const reportedTotal = Number(raw.total)
    if (Number.isFinite(reportedTotal) && reportedTotal >= 0) total = reportedTotal
    collected.push(...list)
    if (!list.length || list.length < KNOWLEDGE_PAGE_SIZE || collected.length >= total) break
  }
  return collected
}

const loadKnowledgeBases = async (force = false) => {
  if (loadingKnowledge.value) return
  if (loadedKnowledge.value && !force) return
  loadingKnowledge.value = true
  knowledgeError.value = false
  try {
    knowledgeBases.value = (await fetchAllKnowledgeBases())
      .filter((item) => isEnabledStatus(item?.status))
      .map((item) => {
        const id = String(item?.id ?? item?.dataset_id ?? '').trim()
        return {
          id,
          name: String(item?.name || item?.display_name || item?.dataset_name || id),
          description: String(item?.description || item?.summary || item?.notes || '暂无知识库描述'),
          docCount: Number(item?.doc_count ?? item?.document_count ?? 0) || 0,
          visibility: String(item?.visibility || '').trim(),
          updatedAt: item?.update_time || '',
        }
      })
      .filter((item) => item.id)
    loadedKnowledge.value = true
  } catch (error) {
    console.warn('[DataKnowledgeMount] 加载知识库失败', error)
    knowledgeError.value = true
  } finally {
    loadingKnowledge.value = false
  }
}

const ensureTabLoaded = (tab: MountTab = activeTab.value) => {
  if (tab === 'datasets') {
    if (!props.datasetDisabled) void loadDatasets()
    return
  }
  if (!props.knowledgeDisabled) void loadKnowledgeBases()
}

const switchTab = (tab: MountTab) => {
  if (tab === 'datasets' && props.datasetDisabled) return
  if (tab === 'knowledge' && props.knowledgeDisabled) return
  if (activeTab.value === tab) return
  activeTab.value = tab
  searchQuery.value = ''
  dataSourceFilter.value = ''
  visibilityFilter.value = ''
  ensureTabLoaded(tab)
}

const retry = () => {
  if (metaDatasetTab.value) void loadDatasets(true)
  else void loadKnowledgeBases(true)
}

const toggleCurrent = (item: MountableItem) => {
  if (currentItemsDisabled.value) return
  if (metaDatasetTab.value) {
    emit('toggle-dataset', item.id, item.name)
    return
  }
  emit('toggle-knowledge-base', item.id)
}

/** 移除已失效的挂载项（同样是走父组件的 toggle，chip 由纯函数重建） */
const removeStale = () => {
  const ids = currentStaleIds.value
  if (!ids.length) return
  if (metaDatasetTab.value) {
    ids.forEach((id) => emit('toggle-dataset', id, id))
    return
  }
  ids.forEach((id) => emit('toggle-knowledge-base', id))
}

const tabLabel = (tab: MountTab) => (tab === 'datasets' ? '数据集' : '知识库')
const tabCount = (tab: MountTab) => {
  const activeCount = tab === 'datasets' ? props.activeDatasetIds.length : props.activeKnowledgeBaseIds.length
  const total = tab === 'datasets' ? scopedDatasets.value.length : scopedKnowledgeBases.value.length
  return `${activeCount}/${total}`
}

const resetSearch = () => {
  resetFilters()
}

watch(activeTab, (tab) => ensureTabLoaded(tab))
watch(
  () => [datasetTabDisabled.value, props.knowledgeDisabled],
  () => {
    if (datasetTabDisabled.value && props.knowledgeDisabled) return
    if (activeTab.value === 'datasets' && datasetTabDisabled.value) activeTab.value = 'knowledge'
    if (activeTab.value === 'knowledge' && props.knowledgeDisabled) activeTab.value = 'datasets'
  },
)

onMounted(() => ensureTabLoaded())

defineExpose({ resetSearch, reload: () => ensureTabLoaded(activeTab.value) })

const hint = computed(() =>
  metaDatasetTab.value
    ? '勾选后写入本轮附件，提问时优先锁定在所选数据集内检索；再次点击取消。'
    : '勾选后写入本轮附件，检索限定在这些知识库内；再次点击取消。',
)
</script>

<template>
  <div
    class="flex flex-col bg-white dark:bg-gray-800 overflow-hidden border border-gray-200 dark:border-gray-700"
    :class="fullWidth
      ? 'w-full max-h-[min(70vh,30rem)] rounded-none border-x-0 border-b-0 shadow-none'
      : fillHeight
        ? 'h-full w-[min(26rem,calc(100vw-1.5rem))] max-h-none rounded-xl shadow-xl'
        : compact
          ? 'w-[min(24rem,calc(100vw-1.25rem))] max-h-[min(58vh,28rem)] rounded-xl shadow-xl'
          : 'w-[min(26rem,calc(100vw-1.5rem))] max-h-[min(60vh,30rem)] rounded-xl shadow-xl'"
    role="menu"
    aria-label="数据与知识库"
    data-testid="data-knowledge-cascade"
  >
    <div class="p-3 pb-2 shrink-0 space-y-2.5">
      <div class="relative">
        <span class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
          <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
        </span>
        <input
          v-model="searchQuery"
          type="search"
          :placeholder="metaDatasetTab ? '搜索数据集' : '搜索知识库'"
          class="w-full pl-9 pr-3 py-2 bg-gray-100 dark:bg-gray-900/80 border-0 rounded-lg focus:ring-2 focus:ring-primary/40 focus:outline-none text-sm text-gray-800 dark:text-gray-100 placeholder:text-gray-400"
          data-testid="data-knowledge-search"
          @click.stop
          @keydown.stop
        />
      </div>

      <div class="flex items-center gap-1 rounded-lg bg-gray-50 dark:bg-gray-900/50 p-0.5" role="tablist" aria-label="资源类型">
        <button
          v-for="tab in MOUNT_TABS"
          :key="tab"
          type="button"
          role="tab"
          :aria-selected="activeTab === tab"
          :disabled="tab === 'datasets' ? datasetDisabled : knowledgeDisabled"
          :data-testid="`data-knowledge-tab-${tab}`"
          class="flex-1 min-w-0 py-1.5 rounded-md text-xs font-semibold transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          :class="activeTab === tab
            ? 'bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm'
            : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'"
          @click.stop="switchTab(tab)"
        >
          <span class="block truncate">
            {{ tabLabel(tab) }}
            <span class="ml-0.5 text-[10px] font-normal text-gray-400">{{ tabCount(tab) }}</span>
          </span>
        </button>
      </div>

      <!--
        筛选：数据集按数据源、知识库按可见范围，外加排序。
        刻意只各留一个维度——浮层只有 26rem 宽，堆多了会把列表挤没；
        522 个数据集真正缺的是「按来源收窄」这一下。
      -->
      <div class="flex items-center gap-1.5">
        <select
          v-model="currentFilterValue"
          :aria-label="metaDatasetTab ? '按数据源筛选' : '按可见范围筛选'"
          class="h-[25px] min-w-0 flex-1 cursor-pointer appearance-none truncate rounded-md border border-gray-200 bg-white py-0 pl-2 pr-5 text-[11px] font-semibold text-gray-600 outline-none dark:border-gray-600 dark:bg-gray-700 dark:text-gray-200"
          style="background-image: url(&quot;data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%239ca3af' stroke-width='2.5'><path stroke-linecap='round' stroke-linejoin='round' d='M6 9l6 6 6-6'/></svg>&quot;); background-repeat: no-repeat; background-position: right 4px center; background-size: 11px"
          data-testid="data-knowledge-filter"
        >
          <template v-if="metaDatasetTab">
            <option value="">全部数据源</option>
            <option v-for="source in dataSourceOptions" :key="source" :value="source">{{ source }}</option>
          </template>
          <template v-else>
            <option value="">全部范围</option>
            <option value="public">授权可见（{{ visibilityCounts.public }}）</option>
            <option value="team">TEAM 级（{{ visibilityCounts.team }}）</option>
            <option value="private">个人私有（{{ visibilityCounts.private }}）</option>
          </template>
        </select>

        <select
          v-model="sortBy"
          aria-label="排序方式"
          class="h-[25px] shrink-0 cursor-pointer appearance-none rounded-md border border-gray-200 bg-white py-0 pl-2 pr-5 text-[11px] font-semibold text-gray-600 outline-none dark:border-gray-600 dark:bg-gray-700 dark:text-gray-200"
          style="background-image: url(&quot;data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%239ca3af' stroke-width='2.5'><path stroke-linecap='round' stroke-linejoin='round' d='M6 9l6 6 6-6'/></svg>&quot;); background-repeat: no-repeat; background-position: right 4px center; background-size: 11px"
          data-testid="data-knowledge-sort"
        >
          <option value="name">名称</option>
          <option value="size">{{ metaDatasetTab ? '表最多' : '文档最多' }}</option>
          <option value="updated">最近更新</option>
        </select>

        <span
          v-if="hasActiveFilters"
          class="shrink-0 text-[10px] font-semibold tabular-nums text-gray-400 dark:text-gray-500"
        >筛选出 {{ currentItems.length }}</span>
      </div>

      <p
        v-if="currentItemsDisabled"
        class="text-[10px] text-amber-600 dark:text-amber-400 leading-relaxed px-0.5"
      >
        {{ metaDatasetTab
          ? (datasetLocked
            ? '当前智能体已绑定数据集，查询范围由智能体决定，手动挂载不会生效。'
            : '当前智能体不支持数据查询，无法挂载数据集。')
          : '当前智能体不支持知识库能力，无法挂载知识库。' }}
      </p>
    </div>

    <div
      class="overflow-y-auto overscroll-y-contain px-2 pb-1 custom-scrollbar"
      :class="fillHeight ? 'flex-1 min-h-0' : 'flex-1 min-h-[10rem]'"
      data-testid="data-knowledge-list"
    >
      <div v-if="currentLoading" class="flex flex-col items-center justify-center py-12 opacity-50">
        <div class="w-5 h-5 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        <span class="text-xs font-medium text-gray-400 mt-2">加载中...</span>
      </div>

      <div v-else-if="currentError" class="text-center py-10 px-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 font-medium">
          {{ metaDatasetTab ? '数据集列表加载失败' : '知识库列表加载失败' }}
        </p>
        <button
          type="button"
          class="mt-2 text-xs font-semibold text-primary hover:underline"
          data-testid="data-knowledge-retry"
          @click.stop="retry"
        >
          重试
        </button>
      </div>

      <div v-else-if="currentItems.length === 0" class="text-center py-10 px-4">
        <p class="text-sm text-gray-400 font-medium">
          {{ hasActiveFilters ? '没有匹配项' : (metaDatasetTab ? '暂无可挂载的数据集' : '暂无可挂载的知识库') }}
        </p>
        <p v-if="!searchQuery.trim()" class="text-xs text-gray-400/80 mt-2 leading-relaxed">
          <template v-if="metaDatasetTab">
            前往
            <a class="text-blue-600 hover:underline font-semibold" href="/dashboard/metadata" target="_blank" rel="noopener noreferrer" @click.stop>数据集管理</a>
            创建或申请数据集
          </template>
          <template v-else>
            前往
            <a class="text-emerald-600 hover:underline font-semibold" href="/dashboard/knowledge-bases" target="_blank" rel="noopener noreferrer" @click.stop>知识库管理</a>
            创建或申请知识库
          </template>
        </p>
      </div>

      <template v-else>
        <button
          v-for="item in currentItems"
          :key="item.id"
          type="button"
          role="menuitemcheckbox"
          :aria-checked="isActive(item.id)"
          :data-testid="`data-knowledge-item-${item.id}`"
          :disabled="currentItemsDisabled"
          class="w-full flex items-start gap-2.5 px-2.5 py-2 mb-0.5 text-left rounded-lg transition-colors disabled:cursor-not-allowed"
          :class="currentItemsDisabled
            ? 'opacity-55'
            : (isActive(item.id)
              ? 'bg-emerald-50/80 dark:bg-emerald-950/30'
              : 'hover:bg-gray-50 dark:hover:bg-gray-700/50')"
          @click.stop="toggleCurrent(item)"
        >
          <span
            class="mt-0.5 h-4 w-4 shrink-0 rounded border flex items-center justify-center transition-colors"
            :class="isActive(item.id)
              ? 'bg-emerald-500 border-emerald-500 text-white'
              : 'border-gray-300 dark:border-gray-600 text-transparent'"
            aria-hidden="true"
          >
            <svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
            </svg>
          </span>
          <span class="min-w-0 flex-1">
            <span class="block text-xs font-semibold text-gray-800 dark:text-gray-100 truncate" :title="item.name">
              {{ item.name }}
            </span>
            <span
              v-if="item.description"
              class="block text-[10px] text-gray-400 dark:text-gray-500 leading-snug mt-0.5 line-clamp-2"
            >{{ item.description }}</span>
          </span>
          <span
            v-if="isActive(item.id)"
            class="shrink-0 mt-0.5 text-[9px] font-bold text-emerald-600 dark:text-emerald-400"
          >已挂载</span>
        </button>
      </template>
    </div>

    <div
      v-if="currentStaleIds.length"
      class="shrink-0 border-t border-amber-100 dark:border-amber-900/50 bg-amber-50/60 dark:bg-amber-950/20 px-3 py-1.5 flex items-center justify-between gap-2"
      data-testid="data-knowledge-stale"
    >
      <span class="text-[10px] text-amber-700 dark:text-amber-400 leading-snug">
        {{ currentStaleIds.length }} 项已挂载但不在列表（已删除 / 无权限 / 超出会话资源范围）
      </span>
      <button
        type="button"
        class="shrink-0 text-[10px] font-semibold text-amber-700 dark:text-amber-400 hover:underline"
        @click.stop="removeStale"
      >移除</button>
    </div>

    <p class="shrink-0 border-t border-gray-100 dark:border-gray-700/60 px-3 py-2 text-[10px] text-gray-400 dark:text-gray-500 leading-relaxed">
      {{ hint }}
    </p>
  </div>
</template>
