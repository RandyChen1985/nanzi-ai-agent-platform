<script setup lang="ts">
/**
 * 资源广场面板：知识库 + 数据集两段，搜索与筛选共享一组条件。
 *
 * 两段各自加载、各自报错：知识库的规模要问 RAGFlow，数据集只查平台库，
 * 一边的依赖挂了不该让另一边跟着白屏。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { fetchSquareDatasets, fetchSquareKnowledge } from '@/api/resourceSquare'
import type {
  SquareDatasetItem,
  SquareKnowledgeItem,
  SquareStateFilter,
  SquareSummary,
  SquareTab,
  SquareVisibility,
} from '@/types/resourceSquare'
import ApplyAccessModal from '@/components/resource-square/ApplyAccessModal.vue'
import ResourceSquareCard from '@/components/resource-square/ResourceSquareCard.vue'

const route = useRoute()
const router = useRouter()

const PAGE_SIZE = 24

const keyword = ref('')
const tab = ref<SquareTab>('all')
// 从工作台卡片点进来时会带 status=has：
// 工作台上的数字是「我有权限的几个」，落到广场若显示全部就对不上，用户会以为丢了东西
const STATUS_FILTERS: SquareStateFilter[] = ['all', 'apply', 'pending', 'has']
const initialStatus = String(route.query.status || '')
const stateFilter = ref<SquareStateFilter>(
  STATUS_FILTERS.includes(initialStatus as SquareStateFilter)
    ? (initialStatus as SquareStateFilter)
    : 'all',
)
const visibility = ref<SquareVisibility>('all')

const kbItems = ref<SquareKnowledgeItem[]>([])
const dsItems = ref<SquareDatasetItem[]>([])
const kbTotal = ref(0)
const dsTotal = ref(0)
const kbPage = ref(1)
const dsPage = ref(1)
const kbLoading = ref(false)
const dsLoading = ref(false)
const kbLoadingMore = ref(false)
const dsLoadingMore = ref(false)
const kbError = ref('')
const dsError = ref('')
const kbStale = ref(false)
const datasetsEnabled = ref(true)

const kbSummary = ref<SquareSummary>({ apply: 0, pending: 0, has: 0 })
const dsSummary = ref<SquareSummary>({ apply: 0, pending: 0, has: 0 })

/** 顶部统计是两段之和：只显示知识库的数字会让人以为数据集没算进去 */
const summary = computed<SquareSummary>(() => ({
  apply: kbSummary.value.apply + dsSummary.value.apply,
  pending: kbSummary.value.pending + dsSummary.value.pending,
  has: kbSummary.value.has + dsSummary.value.has,
}))

const showKb = computed(() => tab.value !== 'ds')
const showDs = computed(() => tab.value !== 'kb')
const kbHasMore = computed(() => kbItems.value.length < kbTotal.value)
const dsHasMore = computed(() => dsItems.value.length < dsTotal.value)

const applyTarget = ref<{
  resourceType: 'dataset' | 'metadata'
  resourceId: string
  resourceName: string
  resourceMeta?: string
} | null>(null)

async function loadKnowledge(append = false) {
  if (append ? kbLoadingMore.value : kbLoading.value) return
  if (append) {
    kbLoadingMore.value = true
  } else {
    kbLoading.value = true
    kbPage.value = 1
  }
  kbError.value = ''
  try {
    const data = await fetchSquareKnowledge({
      keyword: keyword.value.trim() || undefined,
      visibility: visibility.value,
      status: stateFilter.value,
      page: append ? kbPage.value + 1 : 1,
      size: PAGE_SIZE,
    })
    kbItems.value = append ? [...kbItems.value, ...data.items] : data.items
    kbTotal.value = data.total
    kbStale.value = Boolean(data.stale)
    kbSummary.value = data.summary
    if (append) kbPage.value += 1
  } catch (error) {
    kbError.value = error instanceof Error ? error.message : '读取知识库失败'
    if (!append) kbItems.value = []
  } finally {
    kbLoading.value = false
    kbLoadingMore.value = false
  }
}

async function loadDatasets(append = false) {
  if (append ? dsLoadingMore.value : dsLoading.value) return
  if (append) {
    dsLoadingMore.value = true
  } else {
    dsLoading.value = true
    dsPage.value = 1
  }
  dsError.value = ''
  try {
    const data = await fetchSquareDatasets({
      keyword: keyword.value.trim() || undefined,
      status: stateFilter.value,
      page: append ? dsPage.value + 1 : 1,
      size: PAGE_SIZE,
    })
    dsItems.value = append ? [...dsItems.value, ...data.items] : data.items
    dsTotal.value = data.total
    datasetsEnabled.value = data.enabled
    dsSummary.value = data.summary
    if (append) dsPage.value += 1
  } catch (error) {
    dsError.value = error instanceof Error ? error.message : '读取数据集失败'
    if (!append) dsItems.value = []
  } finally {
    dsLoading.value = false
    dsLoadingMore.value = false
  }
}

async function reloadAll() {
  await Promise.all([loadKnowledge(), loadDatasets()])
}

/** 筛选变了就回到第一页：停在第三页看新条件的结果只会看到「空」 */
watch([keyword, tab, stateFilter, visibility], () => {
  void reloadAll()
})

function applyKb(item: SquareKnowledgeItem) {
  applyTarget.value = {
    resourceType: 'dataset',
    resourceId: item.dataset_id,
    resourceName: item.name,
    resourceMeta: `知识库 · ${item.visibility === 'public' ? '公共级' : '部门级'} · ${
      item.doc_count === null ? '规模未知' : `${item.doc_count} 个文档`
    }`,
  }
}

function applyDs(item: SquareDatasetItem) {
  applyTarget.value = {
    resourceType: 'metadata',
    resourceId: String(item.id),
    resourceName: `${item.display_name}（${item.name}）`,
    resourceMeta: `数据集 · ${item.table_count} 张表 / ${item.metric_count} 个指标`,
  }
}

/**
 * 知识库只读概览当前不挂入口：点开是空白（原因未定位），
 * 给一个点了就白屏的入口比没有入口更糟。路由与页面保留，修好后再接回来。
 */
function openKb(_item: SquareKnowledgeItem) {
  return
}

function openDs(item: SquareDatasetItem) {
  router.push({ name: 'ResourceSquareDataset', params: { id: String(item.id) } })
}

async function onSubmitted() {
  await reloadAll()
}

async function focusSection() {
  const section = String(route.query.section || '')
  if (!section) return
  await nextTick()
  const el = document.getElementById(
    section === 'datasets' ? 'square-datasets' : 'square-knowledge',
  )
  el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

onMounted(async () => {
  await reloadAll()
  await focusSection()
})
</script>

<template>
  <section class="space-y-4">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 class="text-base font-semibold text-gray-900 dark:text-gray-100">资源广场</h2>
        <p class="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
          公共级与部门级知识库、平台数据集，找到需要的资源直接申请。
        </p>
      </div>
      <div class="flex flex-wrap gap-2 text-[11.5px] font-semibold">
        <span class="rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 dark:border-gray-700 dark:bg-gray-800">
          可申请 <b class="text-primary">{{ summary.apply }}</b>
        </span>
        <span class="rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 dark:border-gray-700 dark:bg-gray-800">
          审批中 <b class="text-amber-600">{{ summary.pending }}</b>
        </span>
        <span class="rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 dark:border-gray-700 dark:bg-gray-800">
          已有权限 <b class="text-emerald-600">{{ summary.has }}</b>
        </span>
      </div>
    </div>

    <div
      class="flex flex-wrap items-center gap-2 rounded-xl border border-gray-100 bg-white p-3 dark:border-gray-700 dark:bg-gray-800"
    >
      <input
        v-model="keyword"
        type="search"
        class="min-w-[200px] flex-1 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
        placeholder="搜索名称 / 描述 / 归属部门 / 标签"
      />
      <div class="flex rounded-lg bg-gray-100 p-0.5 dark:bg-gray-700/60">
        <button
          v-for="item in ([
            { key: 'all', label: '全部' },
            { key: 'kb', label: '知识库' },
            { key: 'ds', label: '数据集' },
          ] as const)"
          :key="item.key"
          type="button"
          class="rounded-md px-2.5 py-1 text-xs font-semibold"
          :class="tab === item.key ? 'bg-white text-primary dark:bg-gray-800' : 'text-gray-500'"
          @click="tab = item.key"
        >
          {{ item.label }}
        </button>
      </div>
      <select
        v-model="visibility"
        class="rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1.5 text-xs dark:border-gray-600 dark:bg-gray-900 dark:text-gray-200"
      >
        <option value="all">全部范围</option>
        <option value="public">公共级</option>
        <option value="team">部门级</option>
      </select>
      <div class="flex rounded-lg bg-gray-100 p-0.5 dark:bg-gray-700/60">
        <button
          v-for="item in ([
            { key: 'all', label: '全部状态' },
            { key: 'apply', label: '可申请' },
            { key: 'pending', label: '审批中' },
            { key: 'has', label: '已授权' },
          ] as const)"
          :key="item.key"
          type="button"
          class="rounded-md px-2.5 py-1 text-xs font-semibold"
          :class="stateFilter === item.key ? 'bg-white text-primary dark:bg-gray-800' : 'text-gray-500'"
          @click="stateFilter = item.key"
        >
          {{ item.label }}
        </button>
      </div>
    </div>

    <!-- 知识库 -->
    <div v-if="showKb" id="square-knowledge" class="space-y-2.5">
      <div class="flex items-center gap-2">
        <h3 class="text-sm font-semibold text-gray-900 dark:text-gray-100">知识库</h3>
        <span class="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-semibold text-gray-500 dark:bg-gray-700 dark:text-gray-400">
          {{ kbTotal }}
        </span>
        <span class="ml-auto hidden text-[11px] text-gray-400 sm:block">
          公共级与部门级；个人私有库不出现，也不接受申请
        </span>
      </div>

      <div v-if="kbStale && !kbError" class="rounded-xl border border-blue-100 bg-blue-50/70 px-3 py-2 text-[11px] text-blue-700 dark:border-blue-900/40 dark:bg-blue-900/20 dark:text-blue-300">
        知识库规模与更新时间暂时读不到（知识库引擎未响应），文档数显示为「—」。
      </div>
      <div v-if="kbError" class="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
        {{ kbError }}
        <button type="button" class="ml-2 font-semibold underline" @click="loadKnowledge()">重试</button>
      </div>
      <div v-else-if="kbLoading" class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <div v-for="i in 6" :key="i" class="h-40 animate-pulse rounded-2xl bg-gray-100 dark:bg-gray-800/60" />
      </div>
      <div v-else-if="kbItems.length" class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <ResourceSquareCard
          v-for="item in kbItems"
          :key="item.dataset_id"
          kind="kb"
          :name="item.name"
          :description="item.description"
          :visibility="item.visibility"
          :owner="item.owner"
          :badges="item.tags"
          :metrics="[
            { label: 'docs', value: item.doc_count === null ? '文档数未知' : `${item.doc_count} 个文档` },
            { label: 'chunks', value: item.chunk_count === null ? '切片未知' : `${item.chunk_count} 切片` },
          ]"
          :has-access="item.has_access"
          :request-status="item.request_status"
          @apply="applyKb(item)"
          @open="openKb(item)"
        />
      </div>
      <div v-else class="rounded-xl border border-dashed border-gray-200 px-4 py-8 text-center text-xs text-gray-400 dark:border-gray-700">
        没有匹配的知识库，换个关键词或清空筛选试试。
      </div>

      <div v-if="kbHasMore && !kbLoading && !kbError" class="text-center">
        <button
          type="button"
          class="rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-gray-600 hover:border-primary hover:text-primary disabled:opacity-60 dark:border-gray-600 dark:text-gray-300"
          :disabled="kbLoadingMore"
          @click="loadKnowledge(true)"
        >
          {{ kbLoadingMore ? '加载中…' : `显示更多（还有 ${kbTotal - kbItems.length} 个）` }}
        </button>
      </div>
    </div>

    <!-- 数据集 -->
    <div v-if="showDs" id="square-datasets" class="space-y-2.5">
      <div class="flex items-center gap-2">
        <h3 class="text-sm font-semibold text-gray-900 dark:text-gray-100">数据集</h3>
        <span class="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-semibold text-gray-500 dark:bg-gray-700 dark:text-gray-400">
          {{ dsTotal }}
        </span>
        <span class="ml-auto hidden text-[11px] text-gray-400 sm:block">
          全部启用数据集；审批通过后可进入只读概览
        </span>
      </div>

      <div v-if="!datasetsEnabled" class="rounded-xl border border-dashed border-gray-200 px-4 py-8 text-center text-xs text-gray-400 dark:border-gray-700">
        管理员暂未开放数据集目录，需要数据请联系管理员手工授权。
      </div>
      <div v-else-if="dsError" class="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
        {{ dsError }}
        <button type="button" class="ml-2 font-semibold underline" @click="loadDatasets()">重试</button>
      </div>
      <div v-else-if="dsLoading" class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <div v-for="i in 6" :key="i" class="h-40 animate-pulse rounded-2xl bg-gray-100 dark:bg-gray-800/60" />
      </div>
      <div v-else-if="dsItems.length" class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <ResourceSquareCard
          v-for="item in dsItems"
          :key="item.id"
          kind="ds"
          :name="item.name"
          :subtitle="item.display_name"
          :description="item.description"
          :owner="item.data_source"
          :badges="item.tags"
          :metrics="[
            { label: 'tables', value: `${item.table_count} 张表` },
            { label: 'metrics', value: `${item.metric_count} 个指标` },
            { label: 'quality', value: item.quality_score === null ? '质量分未知' : `质量 ${item.quality_score}` },
          ]"
          :has-access="item.has_access"
          :request-status="item.request_status"
          @apply="applyDs(item)"
          @open="openDs(item)"
        />
      </div>
      <div v-else class="rounded-xl border border-dashed border-gray-200 px-4 py-8 text-center text-xs text-gray-400 dark:border-gray-700">
        没有匹配的数据集，换个关键词试试。
      </div>

      <div v-if="dsHasMore && !dsLoading && !dsError" class="text-center">
        <button
          type="button"
          class="rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-gray-600 hover:border-primary hover:text-primary disabled:opacity-60 dark:border-gray-600 dark:text-gray-300"
          :disabled="dsLoadingMore"
          @click="loadDatasets(true)"
        >
          {{ dsLoadingMore ? '加载中…' : `显示更多（还有 ${dsTotal - dsItems.length} 个）` }}
        </button>
      </div>
    </div>

    <ApplyAccessModal
      v-if="applyTarget"
      :visible="Boolean(applyTarget)"
      :resource-type="applyTarget.resourceType"
      :resource-id="applyTarget.resourceId"
      :resource-name="applyTarget.resourceName"
      :resource-meta="applyTarget.resourceMeta"
      @close="applyTarget = null"
      @submitted="onSubmitted"
    />
  </section>
</template>
