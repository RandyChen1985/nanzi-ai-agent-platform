<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { metadataApi } from '../api/metadata'
import type { Dataset } from '../api/metadata'
import axios from '@/utils/axios'
import SmartImportWizard from '../components/metadata/SmartImportWizard.vue'
import MetadataFlowGuideBanner from '../components/metadata/MetadataFlowGuideBanner.vue'
import HeroSilhouette from '../components/common/HeroSilhouette.vue'
import MetadataDriftAlertsDrawer from '../components/metadata/MetadataDriftAlertsDrawer.vue'
import MetadataCronInspectionModal from '../components/metadata/MetadataCronInspectionModal.vue'
import CopyDatasetWizard from '../components/metadata/CopyDatasetWizard.vue'
import InfoTooltip from '../components/InfoTooltip.vue'
import RowPolicyConfigModal from '../components/metadata/RowPolicyConfigModal.vue'
import { useUser } from '../composables/useUser'
import { useToast } from '../composables/useToast'
import { createSseLineParser } from '../utils/chartRenderer'
import {
  CircleStackIcon,
  CpuChipIcon,
  FolderIcon,
  SparklesIcon,
} from '@heroicons/vue/24/outline'

const { isAdmin, hasPermission } = useUser()
const route = useRoute()

const router = useRouter()
const datasets = ref<Dataset[]>([])
const loading = ref(true)
const showCreateModal = ref(false)
const showCreateMenu = ref(false)
const showImportModal = ref(false)
// 复制新建：向导里自己拉详情，这里只记住源数据集
const showCopyWizard = ref(false)
const copySourceDataset = ref<Dataset | null>(null)
const showSpecModal = ref(false)
const showDeleteModal = ref(false)
const showSyncConfirmModal = ref(false)
const showTestModal = ref(false) 
const showEditDatasetModal = ref(false)
const showPermConfigModal = ref(false)
// 弹窗的编辑逻辑在 RowPolicyConfigModal 里，父页面只负责「配置哪个数据集」
const rowPolicyDataset = ref<any>(null)
const openRowPolicyModal = (ds: any) => {
  rowPolicyDataset.value = ds ? JSON.parse(JSON.stringify(ds)) : null
  showPermConfigModal.value = true
}
const handleRowPolicySaved = () => {
  showPermConfigModal.value = false
  rowPolicyDataset.value = null
  fetchDatasets()
}

// Schema 巡检与漂移告警状态
const driftSummary = ref<Record<number, number>>({})
const driftAlertsDataset = ref<Dataset | null>(null)
const showDriftDrawer = ref(false)
const showCronInspectionModal = ref(false)

const fetchDriftSummary = async () => {
  try {
    const res = await metadataApi.getDriftSummary()
    driftSummary.value = res.data?.datasets || {}
  } catch (e) {
    console.error('Failed to fetch drift summary', e)
  }
}

const isGlobalDriftDrawer = ref(false)

const openDriftAlerts = (ds: Dataset) => {
  isGlobalDriftDrawer.value = false
  driftAlertsDataset.value = ds
  showDriftDrawer.value = true
}

const openGlobalDriftDrawer = () => {
  driftAlertsDataset.value = null
  isGlobalDriftDrawer.value = true
  showDriftDrawer.value = true
}

const handleDriftResolved = () => {
  fetchDriftSummary()
}

// 全局 Schema 漂移待办状态与快捷入口
const showDriftBanner = ref(true)

const totalPendingDriftCount = computed(() => {
  return Object.values(driftSummary.value).reduce((acc, cur) => acc + (cur || 0), 0)
})

const driftDatasetCount = computed(() => {
  return Object.keys(driftSummary.value).filter(id => (driftSummary.value[Number(id)] || 0) > 0).length
})

const openFirstDriftDataset = () => {
  openGlobalDriftDrawer()
}

// 流程引导横幅控制
const FLOW_GUIDE_STORAGE_KEY = 'nanzi_metadata_flow_guide_dismissed'
const showFlowGuide = ref(true)
try {
  if (typeof localStorage !== 'undefined' && localStorage.getItem(FLOW_GUIDE_STORAGE_KEY) === '1') {
    showFlowGuide.value = false
  }
} catch {
  // ignore storage error
}

const handleFlowGuideClose = () => {
  showFlowGuide.value = false
}

const handleFlowGuideDismiss = () => {
  showFlowGuide.value = false
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(FLOW_GUIDE_STORAGE_KEY, '1')
    }
    showToast('已忽略流程指引，后续进入不再主动提示。可随时点击问号查看。', 'info')
  } catch {
    // ignore
  }
}

const restoreFlowGuide = () => {
  showFlowGuide.value = true
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.removeItem(FLOW_GUIDE_STORAGE_KEY)
    }
    showToast('已恢复顶部流程指引', 'success')
  } catch {
    // ignore
  }
}

const handleFlowGuideAction = (type: 'datasource' | 'import' | 'create') => {
  if (type === 'datasource') {
    router.push('/dashboard/data-sources')
  } else if (type === 'import') {
    showImportModal.value = true
  } else if (type === 'create') {
    showCreateModal.value = true
  }
}
const activeSpecTab = ref('concept')
const deletingDataset = ref<{id: number, name: string} | null>(null)
const syncingDataset = ref<any>(null)
const editingDataset = ref<any>(null)


const syncingId = ref<number | null>(null)
const syncLogOpen = ref(false)
const syncLogDataset = ref<Dataset | null>(null)
const syncLogTaskId = ref<string | null>(null)
const syncLogStatus = ref<'running' | 'completed' | 'failed' | 'disconnected'>('running')
const syncLogStage = ref('queued')
const syncLogProgress = ref<number | null>(0)
const syncLogCompletedDocuments = ref<number | null>(null)
const syncLogTotalDocuments = ref<number | null>(null)
const syncLogElapsedMs = ref(0)
const syncLogMessage = ref('正在连接同步日志...')
const syncLogEntries = ref<Array<{ event: string; stage: string; message: string; progress?: number | null; elapsed_ms?: number; error_detail?: string }>>([])
let syncLogAbortController: AbortController | null = null
const enhancing = ref(false)
const defaultDataSource = ref('default_clickhouse')
const viewMode = ref<'grid' | 'list'>(localStorage.getItem('metadata_view_mode') as 'grid' | 'list' || 'grid')
const { showToast } = useToast()










watch(viewMode, (newMode) => {
  localStorage.setItem('metadata_view_mode', newMode)
})

// Search and Filter
const searchQuery = ref('')
type StatusFilter = 'all' | 'active' | 'inactive'
type DatasetSortField = 'display_name' | 'status' | 'table_count' | 'quality_score' | 'rag_sync_status' | 'updated_at'
type SortDirection = 'asc' | 'desc'

const statusFilter = ref<StatusFilter>('all')
const sortField = ref<DatasetSortField>('updated_at')
const sortDirection = ref<SortDirection>('desc')

const toggleSort = (field: DatasetSortField) => {
  if (sortField.value === field) {
    sortDirection.value = sortDirection.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortField.value = field
    sortDirection.value = field === 'display_name' ? 'asc' : 'desc'
  }
}

const compareDatasets = (a: Dataset, b: Dataset): number => {
  const dir = sortDirection.value === 'asc' ? 1 : -1

  // 默认将已启用数据集置顶；用户主动按状态排序时，交给状态列的升降序控制。
  if (sortField.value !== 'status') {
    const statusPriority = Number(b.status === 1) - Number(a.status === 1)
    if (statusPriority !== 0) return statusPriority
  }

  switch (sortField.value) {
    case 'display_name': {
      const aName = (a.display_name || a.name || '').toLowerCase()
      const bName = (b.display_name || b.name || '').toLowerCase()
      return aName.localeCompare(bName, 'zh-CN') * dir
    }
    case 'status':
      return ((a.status ?? 0) - (b.status ?? 0)) * dir
    case 'table_count': {
      const tableDiff = (a.table_count || 0) - (b.table_count || 0)
      if (tableDiff !== 0) return tableDiff * dir
      return ((a.metric_count || 0) - (b.metric_count || 0)) * dir
    }
    case 'quality_score':
      // 未评分（null）排最后：升序时 -1 在前，降序时 -1 在后
      return ((a.quality_score ?? -1) - (b.quality_score ?? -1)) * dir
    case 'rag_sync_status':
      return ((a.rag_sync_status ?? -99) - (b.rag_sync_status ?? -99)) * dir
    case 'updated_at': {
      const aTime = a.updated_at ? new Date(a.updated_at).getTime() : 0
      const bTime = b.updated_at ? new Date(b.updated_at).getTime() : 0
      return (aTime - bTime) * dir
    }
    default:
      return 0
  }
}

const qualityBadgeClass = (score?: number | null): string => {
  if (score === undefined || score === null) return 'bg-gray-50 text-gray-400 border-gray-200'
  if (score >= 90) return 'bg-emerald-50 text-emerald-600 border-emerald-200'
  if (score >= 75) return 'bg-blue-50 text-blue-600 border-blue-200'
  if (score >= 60) return 'bg-amber-50 text-amber-600 border-amber-200'
  return 'bg-rose-50 text-rose-600 border-rose-200'
}

const qualityTooltip = (ds: Dataset): string => {
  const b = ds.quality_breakdown
  if (!b || !b.dimensions?.length) return '暂无质量评分，执行一次 Schema 巡检后生成'
  const lines: string[] = []
  if (b.degraded) {
    lines.push(`⚠️ ${b.degraded_reason || '本次巡检未完整比对，分数仅供参考'}`)
  }
  lines.push(`质量治理分 ${b.score}（${b.level_label}）`)
  lines.push(...b.dimensions.map((d) => `${d.label} ${d.score} 分（问题 ${d.problem_count} 处）`))
  if (ds.quality_scored_at) {
    lines.push(`评分时间：${new Date(ds.quality_scored_at).toLocaleString()}`)
  }
  return lines.join('\n')
}

const displayDatasets = computed(() => {
  let list = datasets.value

  if (statusFilter.value === 'active') {
    list = list.filter((ds) => ds.status === 1)
  } else if (statusFilter.value === 'inactive') {
    list = list.filter((ds) => ds.status !== 1)
  }

  if (searchQuery.value) {
    const q = searchQuery.value.toLowerCase()
    list = list.filter((ds) =>
      ds.name.toLowerCase().includes(q) ||
      (ds.display_name && ds.display_name.toLowerCase().includes(q)) ||
      (ds.description && ds.description.toLowerCase().includes(q))
    )
  }

  return [...list].sort(compareDatasets)
})

const hasActiveFilters = computed(() =>
  !!searchQuery.value || statusFilter.value !== 'all'
)

// 数据集多时（几百条）一次性渲染全部卡片会明显卡顿：先渲染一屏，按需追加
const PAGE_SIZE = 24
const visibleCount = ref(PAGE_SIZE)
const visibleDatasets = computed(() => displayDatasets.value.slice(0, visibleCount.value))
const hasMoreDatasets = computed(
  () => visibleDatasets.value.length < displayDatasets.value.length
)
const loadMoreDatasets = () => {
  visibleCount.value += PAGE_SIZE
}

// ---------------------------------------------------------------------------
// 批量选择（列表视图）
// ---------------------------------------------------------------------------
// 只有能删的人才给复选框：没权限时列表保持原样，不制造「选了删不了」的困惑
const canBatchDelete = computed(() => hasPermission('element:metadata:delete'))
const selectedDatasetIds = ref<number[]>([])
const selectAllRef = ref<HTMLInputElement | null>(null)
const showBatchDeleteModal = ref(false)
const batchDeleting = ref(false)

const selectedCount = computed(() => selectedDatasetIds.value.length)

const selectedDatasetsPreview = computed(() =>
  datasets.value.filter((ds) => selectedDatasetIds.value.includes(ds.id)).slice(0, 6),
)

const allVisibleSelected = computed(
  () =>
    canBatchDelete.value &&
    visibleDatasets.value.length > 0 &&
    visibleDatasets.value.every((ds) => selectedDatasetIds.value.includes(ds.id)),
)

const someVisibleSelected = computed(() =>
  visibleDatasets.value.some((ds) => selectedDatasetIds.value.includes(ds.id)),
)

const isSelected = (id: number) => selectedDatasetIds.value.includes(id)

const toggleSelect = (id: number) => {
  const index = selectedDatasetIds.value.indexOf(id)
  if (index >= 0) selectedDatasetIds.value.splice(index, 1)
  else selectedDatasetIds.value.push(id)
}

const toggleSelectAllVisible = () => {
  const visibleIds = visibleDatasets.value.map((ds) => ds.id)
  if (allVisibleSelected.value) {
    selectedDatasetIds.value = selectedDatasetIds.value.filter((id) => !visibleIds.includes(id))
    return
  }
  selectedDatasetIds.value = Array.from(new Set([...selectedDatasetIds.value, ...visibleIds]))
}

const clearSelection = () => {
  selectedDatasetIds.value = []
}

const openBatchDelete = () => {
  if (!selectedCount.value) return
  showBatchDeleteModal.value = true
}

const confirmBatchDelete = async () => {
  if (!selectedCount.value || batchDeleting.value) return
  batchDeleting.value = true
  try {
    const res = await metadataApi.batchDeleteDatasets([...selectedDatasetIds.value])
    const data = res.data?.data || { deleted: [], missing: [], failed: [] }
    const deletedCount = data.deleted?.length || 0
    const failedCount = data.failed?.length || 0
    if (deletedCount) showToast(`已删除 ${deletedCount} 个数据集`, 'success')
    if (failedCount) showToast(`${failedCount} 个数据集删除失败，请稍后重试`, 'error')
    if (data.missing?.length) showToast(`${data.missing.length} 个数据集已不存在`, 'info')
    showBatchDeleteModal.value = false
    clearSelection()
    await fetchDatasets()
  } catch (e: any) {
    showToast(e?.response?.data?.detail || e?.message || '批量删除失败', 'error')
  } finally {
    batchDeleting.value = false
  }
}

// 半选状态是 DOM property，只能手动同步
watch([allVisibleSelected, someVisibleSelected], () => {
  if (selectAllRef.value) {
    selectAllRef.value.indeterminate = someVisibleSelected.value && !allVisibleSelected.value
  }
})

// 筛选 / 排序变化后回到第一屏，避免停在旧的「加载更多」位置
watch([searchQuery, statusFilter, sortField, sortDirection], () => {
  visibleCount.value = PAGE_SIZE
  // 列表内容变了，之前勾的项可能已不可见，直接清掉避免误删
  clearSelection()
})

// Test Retrieval State
const testQuery = ref('')
const testLoading = ref(false)
const testResult = ref<any>(null)
const showAdvancedSettings = ref(true)
const tempProvider = ref<'default' | 'local' | 'ragflow'>('default')
const tempTopK = ref(5)
const tempThreshold = ref(0.2)
const tempVectorWeight = ref(0.3)

const actualProvider = computed(() => {
  if (tempProvider.value === 'default') {
    return (ragflowConfig.value?.metadata_provider === 'local') ? 'local' : 'ragflow'
  }
  return tempProvider.value
})

const showVectorWeight = computed(() => actualProvider.value === 'ragflow')

const newDataset = ref({
  name: '',
  display_name: '',
  description: '',
  data_source: '',
  enable_data_perm: false,
  row_filter_config: null as any,
  tags: [] as string[]
})
const tagInput = ref('')
/**
 * 业务标签说明（新建 / 编辑数据集共用一份，避免两处各写一段、改一处留一处）。
 *
 * 文案必须说清三件事，都是读代码确认的口径：
 * 1. 标签唯一的实质消费点是 AI 数据集目录——`app/services/ai/config.py` 的
 *    `_format_dataset_menu` 把它拼成 `- Dataset: <name> [标签]` 注入 ChatBI 系统提示
 *    （`chatbi/system_prompt.py`、`clarification.py`、`schema_prefetch.py`）与数据门户
 *    导航生成，所以「会影响 AI 分析」是事实，不是销售话术；
 * 2. 它**不参与**数据权限判定（`dataset_scope_service` 全程不读该字段），改标签不会
 *    放宽或收紧「谁能查」，这句话能挡掉最常见的误解；
 * 3. 智能体「绑定数据集」弹窗的搜索会匹配标签（`utils/metadataDatasetFilter.ts`），
 *    所以标签还承担检索线索的作用。
 */
const businessTagHint =
  '用于描述这个数据集属于什么业务主题（如财务、销售、核心数据）。' +
  '它会随「已授权数据集目录」一起提供给 AI，影响 ChatBI 选库、澄清提问与数据门户推荐；' +
  '但不参与数据权限判定，改标签不会改变谁能查。' +
  '智能体绑定数据集时也可以按业务标签搜索。'
const rowFilterConfigStr = ref('')

const dbConnections = ref<any[]>([])
const showCreateDsDropdown = ref(false)
const showEditDsDropdown = ref(false)

const fetchDbConnections = async () => {
  try {
    const res = await metadataApi.listDbConnectionConfigs()
    dbConnections.value = res.data.data || []
  } catch (e) {
    console.error('Failed to load db connection configs:', e)
  }
}

const toggleDataSourceDropdown = async (mode: 'create' | 'edit') => {
  await fetchDbConnections()
  if (mode === 'create') {
    showCreateDsDropdown.value = !showCreateDsDropdown.value
    showEditDsDropdown.value = false
  } else {
    showEditDsDropdown.value = !showEditDsDropdown.value
    showCreateDsDropdown.value = false
  }
}

const selectDataSource = (name: string, mode: 'create' | 'edit') => {
  if (mode === 'create') {
    newDataset.value.data_source = name
    showCreateDsDropdown.value = false
  } else {
    editingDataset.value.data_source = name
    showEditDsDropdown.value = false
  }
}

const fetchSystemConfig = async () => {
  if (!isAdmin.value) return
  try {
    const res = await axios.get('/api/portal/system/configs')
    const groups = res.data
    // Find external_sql_data_source in data_api category
    if (groups && groups['data_api']) {
       const config = groups['data_api'].find((item: any) => item.key === 'external_sql_data_source')
       if (config && config.value) {
           defaultDataSource.value = config.value
       }
    }
  } catch (e) {
    console.error('Failed to fetch system configs', e)
  }
}

const fetchDatasets = async () => {
  loading.value = true
  try {
    const res = await metadataApi.getDatasets()
    datasets.value = res.data
    // 列表刷新后把已经不存在的 id 从选中集合里摘掉
    if (selectedDatasetIds.value.length) {
      const alive = new Set(datasets.value.map((item) => item.id))
      selectedDatasetIds.value = selectedDatasetIds.value.filter((id) => alive.has(id))
    }
    // 卡片要显示「这个数据集授给了谁」，跟着列表一起刷，避免多一次手动刷新
    void fetchPermissionSummary()
  } catch (e: any) {
    console.error('Failed to fetch datasets', e)
    const errorMessage = e.response?.data?.detail || e.message || '获取数据集列表失败'
    showToast(errorMessage, "error")
    datasets.value = []
  } finally {
    loading.value = false
  }
}

// 卡片上的授权摘要：只给能改权限的人看（授权对象名单本身也是管理信息）。
const permissionSummary = ref<Record<string, any>>({})
const canSeePermissionSummary = computed(
  () => isAdmin.value || hasPermission('element:metadata:edit'),
)
const fetchPermissionSummary = async () => {
  if (!canSeePermissionSummary.value) return
  const ids = datasets.value.map((d) => d.id).filter((id) => Boolean(id))
  if (!ids.length) {
    permissionSummary.value = {}
    return
  }
  try {
    const res = await axios.get('/api/portal/metadata/datasets/permission-summary', {
      params: { ids: ids.join(',') },
    })
    permissionSummary.value = res.data?.data || {}
  } catch (e) {
    console.error('获取数据集授权摘要失败', e)
  }
}
const permissionItem = (ds: Dataset) => permissionSummary.value?.[String(ds.id)] || null
const permissionText = (ds: Dataset) => {
  const item = permissionItem(ds)
  if (!item || !item.total) return '未授权'
  const parts: string[] = []
  if (item.all) parts.push('全员')
  if (item.roles) parts.push(`${item.roles}角色`)
  if (item.users) parts.push(`${item.users}用户`)
  return parts.join('+') || `${item.total} 条`
}
const permissionToneClass = (ds: Dataset) => {
  const item = permissionItem(ds)
  if (!item || !item.total) return 'bg-gray-50 text-gray-500 border-gray-200'
  if (item.all) return 'bg-emerald-50 text-emerald-700 border-emerald-100'
  return 'bg-sky-50 text-sky-700 border-sky-100'
}
const permissionTooltip = (ds: Dataset) => {
  const item = permissionItem(ds)
  if (!item || !item.total) {
    return '这个数据集还没有配授权，成员进不来'
  }
  const lines = [`共 ${item.total} 条授权：${permissionText(ds)}`]
  if (item.narrowed) lines.push(`${item.narrowed} 条只授权了部分表`)
  if (item.row_custom) lines.push(`${item.row_custom} 条带自定义行级条件`)
  if (item.row_none) lines.push(`${item.row_none} 条不做行级过滤`)
  lines.push('点开卡片 → 权限管理 查看明细')
  return lines.join('\n')
}
// 字段级徽标：只在真的有字段规则时出现，没配的数据集不占卡片位置
const columnScopeTooltip = (ds: Dataset) => {
  const item = permissionItem(ds)
  if (!item?.column_rules) return ''
  const tables = item.column_tables || 0
  return `${item.column_rules} 条授权带字段级限制，覆盖 ${tables} 张表；点开卡片 → 权限管理 → 字段范围 看明细`
}

const handleCreate = async () => {
  try {
    // Parse JSON
    if (rowFilterConfigStr.value.trim()) {
      try {
        newDataset.value.row_filter_config = JSON.parse(rowFilterConfigStr.value)
      } catch (e) {
        showToast('权限配置 JSON 格式错误', 'error')
        return
      }
    }

    // If data_source is empty, use the system default
    if (!newDataset.value.data_source) {
        newDataset.value.data_source = defaultDataSource.value
    }
    await metadataApi.createDataset(newDataset.value)
    showCreateModal.value = false
    showToast('数据集已创建，默认为禁用状态，请在列表中审核后手动启用', 'success', 5000)
    newDataset.value = { name: '', display_name: '', description: '', data_source: '', enable_data_perm: false, row_filter_config: null, tags: [] }
    rowFilterConfigStr.value = ''
    fetchDatasets()
  } catch (e) {
    console.error('Create failed', e)
  }
}

const openEditDatasetModal = (ds: any) => {
  editingDataset.value = JSON.parse(JSON.stringify(ds))
  rowFilterConfigStr.value = editingDataset.value.row_filter_config 
    ? JSON.stringify(editingDataset.value.row_filter_config, null, 2) 
    : ''
  showEditDatasetModal.value = true
}

const handleEnhanceMetadata = async (dsId: number) => {
  if (enhancing.value) return
  enhancing.value = true
  try {
    const res = await metadataApi.enhanceDatasetMetadata(dsId)
    if (res.data.code === 200) {
      const data = res.data.data
      if (editingDataset.value) {
        // 自动填充描述
        editingDataset.value.description = data.description
        // 自动合并或替换标签（这里采用替换，更准确）
        editingDataset.value.tags = data.tags
        showToast('AI 已成功为您生成描述和标签', 'success')
      }
    } else {
      showToast(res.data.message || 'AI 生成失败', 'error')
    }
  } catch (e: any) {
    console.error('Enhance metadata failed', e)
    showToast(e.response?.data?.detail || 'AI 辅助生成失败', 'error')
  } finally {
    enhancing.value = false
  }
}

const handleUpdateDataset = async () => {
  if (!editingDataset.value) return
  try {
    // Parse JSON
    if (rowFilterConfigStr.value.trim()) {
      try {
        editingDataset.value.row_filter_config = JSON.parse(rowFilterConfigStr.value)
      } catch (e) {
        showToast('权限配置 JSON 格式错误', 'error')
        return
      }
    } else {
      editingDataset.value.row_filter_config = null
    }

    // If data_source is empty, use the system default
    if (!editingDataset.value.data_source) {
        editingDataset.value.data_source = defaultDataSource.value
    }
    await metadataApi.updateDataset(editingDataset.value.id, editingDataset.value)
    showEditDatasetModal.value = false
    editingDataset.value = null
    fetchDatasets()
  } catch (e) {
    console.error('Update failed', e)
  }
}

const toggleStatus = async (ds: any) => {
  try {
    const newStatus = ds.status === 1 ? 0 : 1
    // Optimistic update
    ds.status = newStatus
    await metadataApi.updateDataset(ds.id, { status: newStatus })
  } catch (e) {
    console.error('Status toggle failed', e)
    // Revert on failure
    ds.status = ds.status === 1 ? 0 : 1
    showToast('状态更新失败', 'error')
  }
}

// 复制新建：把源数据集交给向导，向导内部拉详情、做草稿，最后一次性提交
const openCopyWizard = (ds: Dataset) => {
  copySourceDataset.value = ds
  showCopyWizard.value = true
}

const handleDatasetCopied = (ds: Dataset) => {
  // 只刷新列表 + 提示，不关闭向导：成功页要留着让用户看结果、点「查看变更日志」
  showToast(`已复制新建数据集「${ds.display_name || ds.name}」`, 'success')
  void fetchDatasets()
}

// Smart Import logic migrated to SmartImportWizard

const addTag = (target: any) => {
  if (tagInput.value && !target.tags.includes(tagInput.value)) {
    target.tags.push(tagInput.value)
    tagInput.value = ''
  }
}

const removeTag = (target: any, index: number) => {
  target.tags.splice(index, 1)
}

const goToTables = (id: number) => {
  router.push(`/dashboard/metadata/${id}`)
}

const openDeleteModal = (ds: any) => {
  deletingDataset.value = { id: ds.id, name: ds.display_name || ds.name }
  showDeleteModal.value = true
}

const confirmDelete = async () => {
  if (!deletingDataset.value) return
  try {
    await metadataApi.deleteDataset(deletingDataset.value.id)
    showDeleteModal.value = false
    deletingDataset.value = null
    fetchDatasets()
  } catch (e: any) {
    console.error('Delete failed', e)
    showToast(`删除失败: ${e.message || '未知错误'}`, 'error')
  }
}

const openSyncModal = (ds: any) => {
  if (syncingId.value) return
  syncingDataset.value = ds
  showSyncConfirmModal.value = true
}

const closeSyncLog = () => {
  syncLogOpen.value = false
  syncLogAbortController?.abort()
  syncLogAbortController = null
}

const readSyncLog = async (datasetId: number, taskId: string) => {
  syncLogAbortController?.abort()
  const controller = new AbortController()
  syncLogAbortController = controller
  try {
    // 凭据由同源 HttpOnly Cookie 自动携带（fetch 默认 same-origin）
    const headers: Record<string, string> = { Accept: 'text/event-stream' }

    const response = await fetch(`/api/portal/metadata/datasets/${datasetId}/rag/sync/${taskId}/events`, {
      headers,
      credentials: 'include',
      signal: controller.signal,
    })
    if (!response.ok) throw new Error(`同步日志请求失败（${response.status}）`)
    if (!response.body) throw new Error('同步日志没有返回数据流')

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    const parser = createSseLineParser()
    let terminalReceived = false
    const applyEvent = (dataStr: string) => {
      if (!dataStr || dataStr === '[DONE]') return
      const item = JSON.parse(dataStr)
      syncLogEntries.value.push(item)
      syncLogStage.value = item.stage || syncLogStage.value
      syncLogMessage.value = item.message || syncLogMessage.value
      syncLogProgress.value = item.progress ?? syncLogProgress.value
      syncLogCompletedDocuments.value = item.completed_documents ?? syncLogCompletedDocuments.value
      syncLogTotalDocuments.value = item.total_documents ?? syncLogTotalDocuments.value
      syncLogElapsedMs.value = item.elapsed_ms ?? syncLogElapsedMs.value
      if (item.event === 'completed' || item.event === 'failed') {
        terminalReceived = true
        syncLogStatus.value = item.event
        if (item.event === 'completed') showToast('同步成功', 'success')
        else showToast(item.error_detail || '同步失败', 'error')
      }
    }
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      for (const dataStr of parser.feed(decoder.decode(value, { stream: true }))) applyEvent(dataStr)
      if (terminalReceived) break
    }
    for (const dataStr of parser.flush()) applyEvent(dataStr)
    if (!terminalReceived && !controller.signal.aborted) {
      syncLogStatus.value = 'disconnected'
      syncLogMessage.value = '同步日志连接已断开'
    }
    if (terminalReceived) await fetchDatasets()
  } catch (error: any) {
    if (controller.signal.aborted) return
    syncLogStatus.value = 'disconnected'
    syncLogMessage.value = error.message || '同步日志连接已断开'
  } finally {
    if (syncLogAbortController === controller) syncLogAbortController = null
    syncingId.value = null
  }
}

const confirmSync = async () => {
  if (!syncingDataset.value) return
  const ds = syncingDataset.value
  showSyncConfirmModal.value = false
  
  syncingId.value = ds.id
  try {
    const res: any = await metadataApi.syncToRag(ds.id)
    if (res.data.code === 200) {
      ds.rag_sync_status = 1
      syncLogDataset.value = ds
      syncLogTaskId.value = res.data.data?.task_id || null
      syncLogStatus.value = 'running'
      syncLogStage.value = 'queued'
      syncLogProgress.value = 0
      syncLogCompletedDocuments.value = null
      syncLogTotalDocuments.value = null
      syncLogElapsedMs.value = 0
      syncLogMessage.value = '同步任务已开始'
      syncLogEntries.value = []
      syncLogOpen.value = true
      if (syncLogTaskId.value) await readSyncLog(ds.id, syncLogTaskId.value)
      else throw new Error('同步接口未返回任务编号')
    } else {
      showToast(res.data.message || '同步失败', 'error')
    }
  } catch (e: any) {
    console.error('Sync failed', e)
    showToast('启动同步失败: ' + (e.response?.data?.message || e.message), 'error')
  } finally {
    syncingDataset.value = null
    if (!syncLogTaskId.value) syncingId.value = null
  }
}

// 卡片底部操作收进「更多」菜单：一行并排 6 个图标命中区太小，也看不出各自含义。
const openCardMenuId = ref<number | null>(null)
const toggleCardMenu = (datasetId: number, e?: Event) => {
  e?.stopPropagation()
  closeRowMenu()
  openCardMenuId.value = openCardMenuId.value === datasetId ? null : datasetId
}
const closeCardMenus = () => {
  openCardMenuId.value = null
  showCreateMenu.value = false
}
const handleCardMenuEnter = (datasetId: number, e?: Event) => {
  e?.stopPropagation()
  openCardMenuId.value = null
  goToTables(datasetId)
}
const openPermissionOverview = () => {
  showCreateMenu.value = false
  router.push('/dashboard/metadata-permissions')
}
// 列表视图的「…」菜单：表格容器是 overflow-x-auto，绝对定位会被裁掉，
// 所以整块 Teleport 到 body 并按按钮位置摆（fixed），滚动或点外部即关闭。
const openRowMenuId = ref<number | null>(null)
const rowMenuDataset = ref<Dataset | null>(null)
const rowMenuPos = ref<{ top: number; left: number }>({ top: 0, left: 0 })
const ROW_MENU_WIDTH = 208
const ROW_MENU_HEIGHT = 330

const closeRowMenu = () => {
  openRowMenuId.value = null
  rowMenuDataset.value = null
}

const formatUpdatedAt = (value: string | null | undefined) => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

const toggleRowMenu = (ds: Dataset, event: MouseEvent) => {
  event.stopPropagation()
  openCardMenuId.value = null
  if (openRowMenuId.value === ds.id) {
    closeRowMenu()
    return
  }
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  const left = Math.max(8, Math.min(rect.right - ROW_MENU_WIDTH, window.innerWidth - ROW_MENU_WIDTH - 8))
  // 下方放不下就往上弹（列表最后一行很常见）
  const spaceBelow = window.innerHeight - rect.bottom
  const top =
    spaceBelow > ROW_MENU_HEIGHT
      ? rect.bottom + 6
      : Math.max(8, rect.top - ROW_MENU_HEIGHT - 6)
  rowMenuPos.value = { top, left }
  openRowMenuId.value = ds.id
  rowMenuDataset.value = ds
}

const withRowMenu = (action: (ds: Dataset) => void) => {
  const target = rowMenuDataset.value
  closeRowMenu()
  if (target) action(target)
}

const openPermissionTab = (datasetId: number, view?: 'rows') => {
  closeCardMenus()
  router.push({
    path: `/dashboard/metadata/${datasetId}`,
    query: view ? { tab: 'permissions', permView: view } : { tab: 'permissions' },
  })
}
const openSyncFromMenu = (ds: Dataset) => {
  if (!isEngineReady.value || ds.status !== 1 || isLocalMode.value) return
  closeCardMenus()
  openSyncModal(ds)
}

onUnmounted(() => syncLogAbortController?.abort())

const handleTestRetrieval = async () => {
  if (!testQuery.value.trim()) return
  testLoading.value = true
  testResult.value = null
  try {
    const params: any = {}
    if (tempProvider.value !== 'default') {
      params.metadata_provider = tempProvider.value
    }
    params.ragflow_metadata_top_k = tempTopK.value
    params.ragflow_similarity_threshold = tempThreshold.value
    params.ragflow_vector_weight = tempVectorWeight.value

    const res = await metadataApi.testRetrieval(testQuery.value, params)
    const data = res.data.data
    testResult.value = {
      found: data.hits && data.hits.length > 0,
      context: data.schema_context,
      datasets: data.hits ? data.hits.map((h: any) => h.display_name || h.name) : [],
      provider: data.provider,
      logs: data.logs || []
    }
  } catch (e) {
    console.error('Test failed', e)
  } finally {
    testLoading.value = false
  }
}

const getDatasetIcon = (_name: string) => CircleStackIcon
// --- RAGFlow 连通性探测及配置 ---
type RagFlowConfigSummary = {
  api_url: string
  api_key_configured: boolean
  metadata_provider?: string
}

const ragflowConfig = ref<RagFlowConfigSummary | null>(null)
const engineStatus = ref<'checking' | 'connected' | 'disconnected'>('checking')
const errorMessage = ref('')
const showErrorBanner = ref(true)

const isLocalMode = computed(() => ragflowConfig.value?.metadata_provider === 'local')
const isEngineReady = computed(() => {
  if (isLocalMode.value) return false
  return engineStatus.value === 'connected' && !loading.value
})
const engineStatusText = computed(() => {
  if (isLocalMode.value) return '本地已就绪'
  if (engineStatus.value === 'checking') return '连接中...'
  if (engineStatus.value === 'connected') return '已连接'
  return '未连接'
})
/** 只有断连时那个小圆点才可点重试：正常/检测中点击没有意义，别让它变成可点控件 */
const canRetryEngine = computed(
  () => !isLocalMode.value && engineStatus.value === 'disconnected'
)

/** 元数据侧的 RAGFlow 地址：来自 `ragflow_api_url`，不是知识库那套 `knowledge_ragflow_api_url` */
const metadataRagflowApiUrl = ref('')
const ragflowApiUrl = computed(() => metadataRagflowApiUrl.value || '未配置')

const friendlyRagFlowError = computed(() => {
  if (!errorMessage.value) return ''
  const lower = errorMessage.value.toLowerCase()
  if (
    lower.includes('ragflow') ||
    lower.includes('api_key') ||
    lower.includes('connect') ||
    lower.includes('refused')
  ) {
    return '当前无法连接 RAGFlow 服务，请确认 RAGFlow 服务是否可访问、网关是否正常，以及系统配置中的 RAGFlow 地址/API Key 是否正确。'
  }
  return errorMessage.value
})

const fetchRagFlowConfig = async () => {
  try {
    const response = await axios.get('/api/portal/ragflow/config')
    ragflowConfig.value = response.data?.data || null
  } catch (e) {
    ragflowConfig.value = null
  }
}

/**
 * 元数据模式的连通性探活。
 *
 * 读的是 `ragflow_api_url` / `ragflow_api_key`（系统配置「元数据 & RAG」分组），
 * 不是知识库的 `knowledge_ragflow_*`。此前这里直接打 `/api/portal/ragflow/datasets`
 * ——那是知识库接口（前缀 `knowledge_ragflow`），于是只配了元数据那组地址的用户会
 * 在元数据页看到「knowledge_ragflow_api_url ... is missing」，报的还是别人的配置。
 */
const checkRagFlowConnectivity = async () => {
  engineStatus.value = 'checking'
  errorMessage.value = ''
  try {
    const response = await axios.get('/api/portal/metadata/ragflow/health')
    const data = response.data?.data
    metadataRagflowApiUrl.value = String(data?.api_url || '')
    if (data?.reachable) {
      engineStatus.value = 'connected'
    } else {
      errorMessage.value = data?.error || '连接失败'
      engineStatus.value = 'disconnected'
      showErrorBanner.value = true
    }
  } catch (err: any) {
    errorMessage.value = err.response?.data?.detail || err.message || '连接失败'
    engineStatus.value = 'disconnected'
    showErrorBanner.value = true
  }
}

onMounted(async () => {
    fetchSystemConfig()
    fetchDatasets()
    fetchDbConnections()
    fetchDriftSummary()
    await fetchRagFlowConfig()
    if (!isLocalMode.value) {
        checkRagFlowConnectivity()
    } else {
        engineStatus.value = 'connected'
    }
    if ((route.query.open_cron_inspection === '1' || route.query.open_cron_inspection === 'true') && isAdmin.value) {
        showCronInspectionModal.value = true
    }
})
</script>

<template>
  <div class="space-y-5" @click="closeCardMenus">
    <!-- Header：窄屏压缩工具栏；列表视图移动端可横滑查看。卡片底部是字段矩阵剪影 -->
    <div class="hero-surface flex flex-col gap-3 rounded-2xl border border-gray-200/80 p-4 shadow-sm lg:flex-row lg:items-center lg:justify-between lg:p-5">
      <HeroSilhouette variant="fields" :height="40" />
      <div class="relative z-10 min-w-0">
        <div class="flex items-center gap-2">
          <h1 class="text-2xl font-bold tracking-normal text-gray-900">元数据管理</h1>
          <button
            type="button"
            class="hidden sm:flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-800 dark:hover:text-gray-300 cursor-pointer"
            title="元数据流程与设计规范"
            @click="showSpecModal = true"
          >
            <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093M12 17h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          </button>
          <button
            v-if="!showFlowGuide"
            type="button"
            class="hidden shrink-0 whitespace-nowrap text-xs font-medium text-gray-500 underline-offset-2 transition-colors hover:text-primary hover:underline cursor-pointer sm:inline"
            title="重新在页面顶部显示流程引导"
            @click="restoreFlowGuide"
          >
            <span class="whitespace-nowrap">显示指引</span>
          </button>

          <!-- 引擎连接状态：正常态只留一个小圆点（hover 出地址 / Key），
               检测中和断连才补文字，否则「一个黄点」信息量为零。
               它属于页面级运行前提，锚在标题行 —— 放工具栏里会随筛选条件左右漂移。 -->
          <div
            class="group relative flex shrink-0 items-center gap-1.5"
            :class="[canRetryEngine ? 'cursor-pointer' : 'cursor-help', { 'ml-2': !showFlowGuide }]"
            tabindex="0"
            role="status"
            :aria-label="`引擎${engineStatusText}`"
            @click="canRetryEngine && checkRagFlowConnectivity()"
          >
            <span
              class="inline-block h-2 w-2 shrink-0 rounded-full transition-colors"
              :class="{
                'animate-pulse bg-blue-500': !isLocalMode && engineStatus === 'checking',
                'bg-emerald-500': isLocalMode || engineStatus === 'connected',
                'bg-amber-500': !isLocalMode && engineStatus === 'disconnected'
              }"
            />
            <span
              v-if="!isLocalMode && engineStatus !== 'connected'"
              class="whitespace-nowrap text-xs font-medium"
              :class="engineStatus === 'checking' ? 'text-blue-600' : 'text-amber-600'"
            >
              引擎{{ engineStatusText }}
            </span>

            <!-- 悬浮 Tooltip：完整配置信息只在这里出现，正常态不在标题行占字。
                 窄屏居中对齐，否则从标题左侧向右铺 224px 会顶出屏幕。 -->
            <span class="pointer-events-none absolute left-1/2 top-full z-50 mt-2 hidden w-56 -translate-x-1/2 rounded-lg bg-slate-900 p-2.5 text-left font-sans text-xs font-normal text-white shadow-xl group-hover:block group-focus-within:block sm:left-0 sm:translate-x-0">
              <div class="mb-1 border-b border-white/10 pb-1 font-medium">元数据引擎信息</div>
              <template v-if="isLocalMode">
                <div class="opacity-80">运行模式: 本地 Redis 向量检索</div>
                <div class="mt-0.5 font-medium text-emerald-400 opacity-80">无需连接外部 RAGFlow</div>
              </template>
              <template v-else>
                <div class="break-all opacity-80">地址: {{ ragflowApiUrl }}</div>
                <div class="mt-1 opacity-80">
                  API Key:
                  <span v-if="ragflowConfig?.api_key_configured" class="text-emerald-400">已配置</span>
                  <span v-else class="text-amber-400">未配置</span>
                </div>
              </template>
              <div v-if="canRetryEngine" class="mt-1.5 border-t border-white/10 pt-1.5 text-amber-300">点击圆点重新检测</div>
            </span>
          </div>
        </div>
        <p class="mt-0.5 truncate text-sm text-gray-500">管理业务数据集及其表结构语义</p>
      </div>

      <div class="relative z-10 flex w-full flex-col gap-2.5 sm:w-auto sm:flex-row sm:flex-wrap sm:items-center sm:gap-3 lg:justify-end">
        <!-- 结果计数放在工具栏最前并固定宽度：搜索条件一变数字就变，
             如果跟着一起挤，搜索框会左右跳。没有筛选时显示总数，避免整块出现/消失。 -->
        <div
          class="hidden w-24 shrink-0 whitespace-nowrap text-right text-xs tabular-nums text-gray-400 sm:block"
          :title="hasActiveFilters ? '当前筛选结果 / 全部数据集' : '全部数据集'"
        >
          <template v-if="hasActiveFilters">
            <span class="font-semibold text-gray-700">{{ displayDatasets.length }}</span>
            <span class="text-gray-400"> / {{ datasets.length }}</span>
          </template>
          <template v-else>
            共 <span class="font-semibold text-gray-700">{{ datasets.length }}</span> 个
          </template>
        </div>

        <div class="relative w-full shrink-0 sm:w-72">
          <span class="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3">
            <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
          </span>
          <input
            v-model="searchQuery"
            type="text"
            data-search-no-clear
            placeholder="搜索数据集名称、ID或描述..."
            class="w-full rounded-lg border border-gray-300 bg-white py-2 pl-9 pr-8 text-sm shadow-sm transition-all focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <!-- 自绘清空按钮：type="search" 的原生 ✕ 会挤掉右侧文字区，
               这里右边恒定留 pr-8，有没有按钮都不影响排版 -->
          <button
            v-if="searchQuery"
            type="button"
            class="absolute inset-y-0 right-0 flex items-center pr-2.5 text-gray-400 transition-colors hover:text-gray-600"
            title="清空搜索"
            @click="searchQuery = ''"
          >
            <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2L10 10m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </button>
        </div>

        <select
          v-model="statusFilter"
          class="w-full shrink-0 rounded-lg border border-gray-300 bg-white px-2.5 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 sm:w-auto"
        >
          <option value="all">状态：全部</option>
          <option value="active">已启用</option>
          <option value="inactive">已禁用</option>
        </select>

        <div class="flex items-center gap-2">
          <div class="flex shrink-0 select-none items-center gap-0.5 rounded-lg border border-gray-300 bg-gray-200/60 p-0.5">
            <button
              type="button"
              class="flex items-center justify-center rounded-md p-1.5 transition-all duration-200"
              :class="viewMode === 'grid' ? 'border border-gray-200 bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-800'"
              title="卡片视图"
              @click="viewMode = 'grid'"
            >
              <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
              </svg>
            </button>
            <button
              type="button"
              class="flex items-center justify-center rounded-md p-1.5 transition-all duration-200"
              :class="viewMode === 'list' ? 'border border-gray-200 bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-800'"
              title="列表视图"
              @click="viewMode = 'list'"
            >
              <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
              </svg>
            </button>
          </div>

          <button
            type="button"
            class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-gray-300 bg-white text-gray-700 shadow-sm transition-all hover:bg-gray-50 active:scale-95"
            title="检索测试"
            @click="showTestModal = true"
          >
            <svg class="h-4 w-4 text-amber-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
          </button>
        </div>

        <div
          v-if="hasPermission('element:metadata:edit') || hasPermission('element:metadata:import')"
          class="relative w-full shrink-0 sm:w-auto"
          @click.stop
        >
          <div class="flex w-full items-stretch overflow-hidden rounded-lg shadow-sm sm:w-auto">
            <button
              v-if="hasPermission('element:metadata:edit')"
              type="button"
              class="flex flex-1 items-center justify-center gap-2 bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-all hover:bg-blue-700 active:scale-[0.98] sm:flex-none"
              title="新建数据集"
              @click="showCreateModal = true; showCreateMenu = false"
            >
              <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
              </svg>
              新建
            </button>
            <button
              v-else
              v-has-perm="'element:metadata:import'"
              type="button"
              class="flex flex-1 items-center justify-center gap-2 bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-all hover:bg-blue-700 active:scale-[0.98] sm:flex-none"
              @click="showImportModal = true; showCreateMenu = false"
            >
              <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" />
              </svg>
              智能导入
            </button>
            <button
              v-if="hasPermission('element:metadata:edit') && hasPermission('element:metadata:import')"
              type="button"
              class="border-l border-blue-500 bg-blue-600 px-2 text-white transition-colors hover:bg-blue-700"
              title="更多操作与创建方式"
              @click="showCreateMenu = !showCreateMenu"
            >
              <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
              </svg>
            </button>
          </div>
          <div
            v-if="showCreateMenu"
            class="absolute right-0 z-20 mt-1.5 w-48 rounded-xl border border-gray-200 bg-white py-1 shadow-lg dark:border-gray-700 dark:bg-gray-800"
          >
            <button
              v-if="hasPermission('element:metadata:edit')"
              type="button"
              class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-700"
              @click="showCreateMenu = false; showCreateModal = true"
            >
              <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
              </svg>
              空白新建
            </button>
            <button
              v-has-perm="'element:metadata:import'"
              type="button"
              class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-700"
              @click="showCreateMenu = false; showImportModal = true"
            >
              <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" />
              </svg>
              智能导入 (DDL)
            </button>
            <div class="my-1 border-t border-gray-100 dark:border-gray-700"></div>
            <button
              v-if="hasPermission('element:metadata:edit')"
              type="button"
              class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-700 transition-colors cursor-pointer"
              @click="openPermissionOverview()"
            >
              <svg class="h-4 w-4 shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
              </svg>
              <span>权限总览</span>
            </button>
            <div class="my-1 border-t border-gray-100 dark:border-gray-700"></div>
            <button
              type="button"
              class="flex w-full items-center justify-between px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-700 transition-colors cursor-pointer"
              @click="showCreateMenu = false; openGlobalDriftDrawer()"
            >
              <div class="flex items-center gap-2">
                <svg class="h-4 w-4 text-amber-500 dark:text-amber-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 12h4l3-6 4 12 3-6h4" />
                </svg>
                <span>全局巡检</span>
              </div>
              <span
                v-if="totalPendingDriftCount > 0"
                class="px-1.5 py-0.5 text-[10px] font-bold rounded-full bg-rose-500 text-white leading-none"
              >
                {{ totalPendingDriftCount }}
              </span>
            </button>
            <button
              v-if="isAdmin"
              type="button"
              class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-700 transition-colors cursor-pointer"
              @click="showCreateMenu = false; showCronInspectionModal = true"
            >
              <svg class="h-4 w-4 text-gray-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <span>定时巡检</span>
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 元数据管理全流程指引横幅（支持关闭与忽略提示） -->
    <div v-if="showFlowGuide" class="flex-shrink-0">
      <MetadataFlowGuideBanner
        @close="handleFlowGuideClose"
        @dismiss="handleFlowGuideDismiss"
        @action="handleFlowGuideAction"
      />
    </div>

    <!-- 全局 Schema 漂移待办与运行时物理报错反哺提醒横幅 -->
    <div
      v-if="showDriftBanner && totalPendingDriftCount > 0"
      class="relative rounded-2xl border border-amber-300/90 bg-gradient-to-r from-amber-50 via-orange-50/40 to-amber-50 dark:from-amber-950/40 dark:via-orange-950/20 dark:to-amber-950/30 p-3.5 sm:p-4 text-xs sm:text-sm text-amber-900 dark:text-amber-200 shadow-sm flex flex-col sm:flex-row sm:items-center justify-between gap-3"
    >
      <div class="flex items-start gap-3">
        <div class="w-8 h-8 rounded-xl bg-amber-200/70 dark:bg-amber-900/60 flex items-center justify-center shrink-0 text-base shadow-2xs">
          ⚠️
        </div>
        <div>
          <div class="font-bold text-amber-950 dark:text-amber-100 flex items-center gap-2 flex-wrap">
            <span>Schema 结构漂移待办提醒</span>
            <span class="px-2 py-0.5 rounded-full text-[11px] font-bold bg-amber-200 text-amber-900 dark:bg-amber-900 dark:text-amber-200 border border-amber-300 dark:border-amber-700">
              {{ driftDatasetCount }} 个数据集 · 共 {{ totalPendingDriftCount }} 项待治理
            </span>
          </div>
          <p class="mt-1 text-amber-800/90 dark:text-amber-300/80 text-xs leading-relaxed">
            系统检出底层物理表结构与元数据存在差异（含 <span class="font-semibold text-amber-950 dark:text-amber-200 underline decoration-amber-400 underline-offset-2">AI 运行时物理报错自动反哺</span> 与 <span class="font-semibold text-amber-950 dark:text-amber-200 underline decoration-amber-400 underline-offset-2">结构巡检发现</span> 的失效或新增字段）。
          </p>
        </div>
      </div>

      <div class="shrink-0 flex items-center gap-2 self-end sm:self-auto">
        <button
          type="button"
          class="px-3.5 py-1.5 bg-amber-600 hover:bg-amber-700 active:bg-amber-800 text-white rounded-lg font-semibold text-xs transition-colors shadow-2xs cursor-pointer flex items-center gap-1.5"
          @click="openFirstDriftDataset"
        >
          <span>📋</span> 查看待处置详情
        </button>
        <button
          type="button"
          class="p-1.5 text-amber-600 hover:text-amber-800 dark:text-amber-400 dark:hover:text-amber-200 rounded-lg transition-colors cursor-pointer"
          title="暂时隐藏"
          @click="showDriftBanner = false"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
        </button>
      </div>
    </div>

    <!-- Error Banner -->
    <div v-if="errorMessage && showErrorBanner && !isLocalMode" class="relative rounded-2xl border border-amber-200 bg-amber-50 p-4 pr-10 text-sm text-amber-800 shadow-sm flex items-start gap-3">
      <svg class="w-5 h-5 text-amber-500 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
      </svg>
      <div>
        <div class="font-semibold text-amber-900">RAGFlow 服务连通性故障</div>
        <div class="mt-1 text-amber-800/90">{{ friendlyRagFlowError }}</div>
        <div class="mt-2 text-xs font-mono text-amber-700 bg-amber-100/50 p-2 rounded-lg">
          <div>连接地址: <a :href="ragflowApiUrl" target="_blank" rel="noopener noreferrer" :title="ragflowApiUrl" class="hover:underline truncate max-w-[200px] sm:max-w-[300px] inline-block align-bottom">{{ ragflowApiUrl }}</a></div>
          <div class="mt-0.5">错误日志: {{ errorMessage }}</div>
        </div>
      </div>
      <button @click="showErrorBanner = false" class="absolute top-4 right-4 text-amber-500 hover:text-amber-700 transition-colors" title="关闭">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>

    <!-- Loading State：首帧即显示，避免请求较慢时列表区整块空白 -->
    <div v-if="loading" class="space-y-4" role="status" aria-live="polite" aria-busy="true">
      <div class="flex items-center gap-2 text-xs text-gray-400">
        <svg class="h-3.5 w-3.5 animate-spin text-blue-500" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        <span>正在加载数据集…</span>
      </div>
      <!-- 骨架形态跟随当前视图，避免数据到达前后高度剧烈跳动 -->
      <div v-if="viewMode === 'grid'" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        <div v-for="i in 6" :key="i" class="h-36 bg-gray-100 rounded-xl animate-pulse"></div>
      </div>
      <div v-else class="space-y-2">
        <div v-for="i in 8" :key="i" class="h-[76px] bg-gray-100 rounded-xl animate-pulse"></div>
      </div>
    </div>

    <!-- Filtered Empty State -->
    <div
      v-else-if="datasets.length > 0 && displayDatasets.length === 0"
      class="flex flex-col items-center justify-center py-16"
    >
      <p class="text-sm text-gray-500 mt-4 font-semibold">无匹配的数据集</p>
      <p class="text-xs text-gray-400 mt-1">试试其他关键词，或清除筛选后浏览全部</p>
      <button
        type="button"
        @click="searchQuery = ''; statusFilter = 'all'"
        class="mt-4 px-4 py-2 text-sm font-medium text-blue-600 hover:bg-blue-50 rounded-lg border border-blue-200 transition-colors"
      >
        清除筛选
      </button>
    </div>

    <!-- Grid View：紧凑卡片，降低无效留白 -->
    <div v-else-if="viewMode === 'grid'" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
      <div 
        v-for="ds in visibleDatasets" 
        :key="ds.id"
        class="bg-white rounded-xl border border-gray-200 hover:border-blue-300 hover:shadow-md transition-all duration-200 cursor-pointer group flex flex-col relative"
        @click="goToTables(ds.id)"
      >
        <!-- RAG Sync Badge -->
        <div 
          v-if="!isLocalMode && ds.rag_sync_status !== undefined && ds.rag_sync_status !== 0"
          class="absolute top-3 right-3 px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider border shadow-sm z-10"
          :class="{
            'bg-green-50 text-green-600 border-green-200': ds.rag_sync_status === 2,
            'bg-blue-50 text-blue-600 border-blue-200 animate-pulse': ds.rag_sync_status === 1,
            'bg-red-50 text-red-600 border-red-200': ds.rag_sync_status === -1,
            'bg-amber-50 text-amber-600 border-amber-200': ds.rag_sync_status === 3
          }"
          :title="ds.rag_sync_status === 3 ? '元数据已变更，建议重新同步' : (ds.rag_sync_notes || (ds.rag_synced_at ? `Last synced: ${new Date(ds.rag_synced_at).toLocaleString()}` : ''))"
        >
          {{ ds.rag_sync_status === 3 ? 'Pending' : 'RAG' }}
        </div>

        <div class="p-4 flex-1 min-w-0">
          <div class="flex items-start gap-3 pr-10">
            <div class="w-9 h-9 rounded-lg bg-gray-50 flex-shrink-0 flex items-center justify-center text-lg border border-gray-100">
              <component :is="getDatasetIcon(ds.name)" class="w-5 h-5 text-blue-600" />
            </div>
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-1.5 min-w-0">
                <h3 class="text-sm font-bold text-gray-900 group-hover:text-blue-600 transition-colors truncate">
                  {{ ds.display_name }}
                </h3>
                <span 
                  v-if="driftSummary[ds.id]"
                  @click.stop="openDriftAlerts(ds)"
                  class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-semibold bg-amber-50 text-amber-600 border border-amber-200 hover:bg-amber-100 cursor-pointer shadow-2xs shrink-0"
                  title="存在待处理的 Schema 漂移异常，点击查看"
                >
                  <svg class="w-3 h-3 text-amber-500 animate-pulse" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
                  {{ driftSummary[ds.id] }}
                </span>
              </div>
              <div class="mt-1 flex items-center gap-1.5 min-w-0 flex-wrap">
                <span class="text-[11px] font-mono text-gray-400 truncate">#{{ ds.name }}</span>
                <span
                  v-if="ds.data_source"
                  class="inline-flex items-center px-1.5 py-0.5 bg-gray-50 text-gray-500 rounded text-[9px] border border-gray-200 max-w-[9rem] truncate"
                  :title="ds.data_source"
                >
                  {{ ds.data_source }}
                </span>
              </div>
            </div>
          </div>

          <!-- 统计：行内芯片，替代三块大色块 -->
          <div class="mt-3 flex flex-wrap items-center gap-1.5 text-[11px]">
            <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-blue-50 text-blue-700 border border-blue-100 font-medium">
              表 <b>{{ ds.table_count || 0 }}</b>
            </span>
            <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-amber-50 text-amber-700 border border-amber-100 font-medium">
              指标 <b>{{ ds.metric_count || 0 }}</b>
            </span>
            <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-purple-50 text-purple-700 border border-purple-100 font-medium">
              关系 <b>{{ ds.relationship_count || 0 }}</b>
            </span>
            <span
              class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md border font-medium"
              :class="qualityBadgeClass(ds.quality_score)"
              :title="qualityTooltip(ds)"
            >
              质量分 <b>{{ ds.quality_score ?? '—' }}</b>
              <span v-if="ds.quality_breakdown?.degraded" class="text-amber-500" title="本次巡检未完整比对，分数仅供参考">⚠</span>
            </span>
            <span
              v-if="canSeePermissionSummary"
              class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md border font-medium"
              :class="permissionToneClass(ds)"
              :title="permissionTooltip(ds)"
            >
              <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>
              权限 <b>{{ permissionText(ds) }}</b>
            </span>
            <span
              v-if="canSeePermissionSummary && permissionItem(ds)?.column_rules"
              class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-violet-50 text-violet-700 border border-violet-100 font-medium"
              :title="columnScopeTooltip(ds)"
            >
              <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 17V7m0 10a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2h2a2 2 0 012 2m0 10a2 2 0 002 2h2a2 2 0 002-2M9 7a2 2 0 012-2h2a2 2 0 012 2m0 10V7m0 10a2 2 0 002 2h2a2 2 0 002-2V7a2 2 0 00-2-2h-2a2 2 0 00-2 2"/></svg>
              字段 <b>{{ permissionItem(ds)?.column_tables }}</b> 表
            </span>
          </div>

          <p class="mt-2.5 text-xs text-gray-500 line-clamp-2 leading-relaxed min-h-[2.5rem]">
            {{ ds.description || '暂无描述' }}
          </p>

          <div class="mt-2.5 flex items-center justify-between gap-2 text-[10px] text-gray-400">
            <span v-if="ds.updated_at" class="inline-flex items-center gap-1 truncate">
              <svg class="w-3 h-3 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
              {{ formatUpdatedAt(ds.updated_at) }}
            </span>
            <span v-else />
            <div v-if="ds.tags?.length" class="flex flex-wrap justify-end gap-1 min-w-0">
              <span
                v-for="tag in ds.tags.slice(0, 2)"
                :key="tag"
                class="px-1.5 py-0.5 bg-blue-50 text-blue-600 rounded font-medium border border-blue-100 truncate max-w-[5.5rem]"
              >
                {{ tag }}
              </span>
              <span
                v-if="ds.tags.length > 2"
                class="px-1.5 py-0.5 bg-gray-50 text-gray-400 rounded border border-gray-100"
              >
                +{{ ds.tags.length - 2 }}
              </span>
            </div>
          </div>
        </div>
        
        <div class="px-4 py-2.5 border-t border-gray-100 flex justify-between items-center bg-gray-50/60 rounded-b-xl">
          <div 
            class="flex items-center gap-2" 
            :class="isAdmin || hasPermission('element:metadata:sync') ? 'cursor-pointer' : 'cursor-default opacity-80'"
            @click.stop="(isAdmin || hasPermission('element:metadata:sync')) && toggleStatus(ds)"
          >
            <div 
              class="relative inline-flex h-4 w-7 items-center rounded-full transition-colors duration-200 ease-in-out"
              :class="ds.status === 1 ? 'bg-green-500' : 'bg-gray-300'"
            >
              <span
                class="inline-block h-3 w-3 transform rounded-full bg-white transition-transform duration-200 ease-in-out shadow"
                :class="ds.status === 1 ? 'translate-x-3.5' : 'translate-x-0.5'"
              />
            </div>
            <span class="text-[10px] font-semibold" :class="ds.status === 1 ? 'text-green-600' : 'text-gray-400'">
              {{ ds.status === 1 ? '已启用' : '已禁用' }}
            </span>
          </div>
          <!-- 底部操作收进「更多」菜单：一行挤 6 个图标会让命中区只有 24px，
               也看不出每个图标干什么。菜单样式对齐智能体中心（图标 + 中文）。 -->
          <div class="relative" @click.stop>
            <button
              type="button"
              @click="toggleCardMenu(ds.id, $event)"
              class="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-white border border-transparent hover:border-gray-200 transition-colors cursor-pointer"
              title="更多操作"
            >
              <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                <path d="M6 10a2 2 0 11-4 0 2 2 0 014 0zm6 0a2 2 0 11-4 0 2 2 0 014 0zm6 0a2 2 0 11-4 0 2 2 0 014 0z" />
              </svg>
            </button>
            <div
              v-if="openCardMenuId === ds.id"
              class="absolute right-0 bottom-full z-30 mb-1 w-52 max-h-[19rem] overflow-y-auto rounded-xl border border-gray-100 bg-white py-1 shadow-xl"
            >
              <button
                type="button"
                @click="handleCardMenuEnter(ds.id, $event)"
                class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs text-gray-700 transition-colors hover:bg-gray-50 cursor-pointer"
              >
                <svg class="h-4 w-4 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6" /></svg>
                进入数据集
              </button>
              <button
                type="button"
                @click="closeCardMenus(); openDriftAlerts(ds)"
                class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs transition-colors hover:bg-gray-50 cursor-pointer"
                :class="driftSummary[ds.id] ? 'text-amber-700' : 'text-gray-700'"
                :title="ds.status === 1 ? 'Schema 巡检与差异治理' : 'Schema 巡检与差异治理 (数据集已禁用)'"
              >
                <svg class="h-4 w-4" :class="driftSummary[ds.id] ? 'text-amber-500' : 'text-gray-400'" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 12h4l3-6 4 12 3-6h4" /></svg>
                Schema 巡检
                <span v-if="driftSummary[ds.id]" class="ml-auto h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500"></span>
              </button>
              <button
                v-if="hasPermission('element:metadata:sync')"
                type="button"
                @click="openSyncFromMenu(ds)"
                class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:hover:bg-transparent"
                :class="(ds.status === 1 && isEngineReady && !isLocalMode) ? 'text-gray-700 cursor-pointer' : 'text-gray-300'"
                :disabled="!isEngineReady || ds.status !== 1 || isLocalMode || syncingId === ds.id || ds.rag_sync_status === 1"
                :title="isLocalMode ? '本地向量模式下已自动同步，无需手动上传' : (!isEngineReady ? 'RAGFlow 服务未就绪' : (ds.status === 1 ? '同步到 RAGFlow' : '数据集已禁用，无法同步'))"
              >
                <svg v-if="syncingId === ds.id || ds.rag_sync_status === 1" class="h-4 w-4 animate-spin text-indigo-500" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 16h5v5"/></svg>
                <svg v-else class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><path d="M12 16.5V9.75m0 0 3 3m-3-3-3 3M6.75 19.5a4.5 4.5 0 0 1-1.41-8.775 5.25 5.25 0 0 1 10.233-2.33 3 3 0 0 1 3.758 3.848A3.752 3.752 0 0 1 18 19.5H6.75Z"/></svg>
                {{ (syncingId === ds.id || ds.rag_sync_status === 1) ? '同步中…' : '同步到 RAGFlow' }}
              </button>
              <button
                v-if="hasPermission('element:metadata:edit')"
                type="button"
                @click="openPermissionTab(ds.id)"
                class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs text-gray-700 transition-colors hover:bg-gray-50 cursor-pointer"
              >
                <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>
                权限管理
              </button>
              <button
                v-if="hasPermission('element:metadata:edit')"
                type="button"
                title="元数据管理 → 数据行策略"
                @click="closeCardMenus(); openRowPolicyModal(ds)"
                class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs text-gray-700 transition-colors hover:bg-gray-50 cursor-pointer"
              >
                <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h10"/></svg>
                数据行策略
              </button>
              <button
                v-if="hasPermission('element:metadata:edit')"
                type="button"
                @click="closeCardMenus(); openEditDatasetModal(ds)"
                class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs text-gray-700 transition-colors hover:bg-gray-50 cursor-pointer"
              >
                <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
                编辑数据集
              </button>
              <button
                v-if="hasPermission('element:metadata:edit')"
                type="button"
                @click="closeCardMenus(); openCopyWizard(ds)"
                class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs font-medium text-blue-600 transition-colors hover:bg-blue-50 cursor-pointer"
              >
                <svg class="h-4 w-4 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V5a2 2 0 012-2h6a2 2 0 012 2v10a2 2 0 01-2 2h-2M8 9h6a2 2 0 012 2v10a2 2 0 01-2 2H8a2 2 0 01-2-2V11a2 2 0 012-2z"/></svg>
                复制新建
              </button>
              <template v-if="hasPermission('element:metadata:delete')">
                <div class="my-1 border-t border-gray-100"></div>
                <button
                  type="button"
                  @click="closeCardMenus(); openDeleteModal(ds)"
                  class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs text-red-600 transition-colors hover:bg-red-50 cursor-pointer"
                >
                  <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                  删除数据集
                </button>
              </template>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- List View：窄屏允许横滑，避免 overflow-hidden 裁切列内容 -->
    <div v-else class="overflow-x-auto rounded-xl border border-gray-100 bg-white shadow-sm" style="-webkit-overflow-scrolling: touch;">
       <div class="min-w-[720px]">
       <!-- 批量操作条：选中才出现，避免常态占用高度 -->
       <div
         v-if="selectedCount"
         class="flex flex-wrap items-center gap-3 border-b border-blue-100 bg-blue-50/70 px-6 py-2"
       >
         <span class="text-xs font-medium text-blue-700">已选 <b>{{ selectedCount }}</b> 个数据集</span>
         <button type="button" class="text-xs text-blue-600 hover:underline" @click="clearSelection">取消选择</button>
         <button
           type="button"
           class="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-red-700 disabled:opacity-60"
           :disabled="batchDeleting"
           @click="openBatchDelete"
         >
           <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
           批量删除
         </button>
       </div>
       <div class="grid grid-cols-12 gap-4 border-b border-gray-100 bg-gray-50 px-6 py-3 text-xs font-bold uppercase tracking-wider text-gray-500">
          <div class="col-span-3 flex items-center gap-3">
          <input
            v-if="canBatchDelete"
            ref="selectAllRef"
            type="checkbox"
            class="h-3.5 w-3.5 shrink-0 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
            title="全选当前列表"
            :checked="allVisibleSelected"
            @change="toggleSelectAllVisible"
          />
          <button type="button" class="inline-flex items-center gap-1 text-left hover:text-gray-700 transition-colors" @click="toggleSort('display_name')">
            <span>数据集 (Dataset)</span>
            <svg v-if="sortField === 'display_name'" class="w-3.5 h-3.5 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path v-if="sortDirection === 'asc'" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7" />
              <path v-else stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          </div>
          <button type="button" class="col-span-2 inline-flex items-center gap-1 text-left hover:text-gray-700 transition-colors" @click="toggleSort('status')">
            <span>状态 (Status)</span>
            <svg v-if="sortField === 'status'" class="w-3.5 h-3.5 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path v-if="sortDirection === 'asc'" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7" />
              <path v-else stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          <button type="button" class="col-span-2 inline-flex items-center gap-1 text-left hover:text-gray-700 transition-colors" @click="toggleSort('table_count')">
            <span>统计 (Stats)</span>
            <svg v-if="sortField === 'table_count'" class="w-3.5 h-3.5 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path v-if="sortDirection === 'asc'" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7" />
              <path v-else stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          <button type="button" class="col-span-1 inline-flex items-center gap-1 text-left hover:text-gray-700 transition-colors" @click="toggleSort('quality_score')">
            <span>质量分</span>
            <svg v-if="sortField === 'quality_score'" class="w-3.5 h-3.5 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path v-if="sortDirection === 'asc'" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7" />
              <path v-else stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          <button type="button" class="col-span-1 inline-flex items-center gap-1 text-left hover:text-gray-700 transition-colors" @click="toggleSort('rag_sync_status')">
            <span>RAG</span>
            <svg v-if="sortField === 'rag_sync_status'" class="w-3.5 h-3.5 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path v-if="sortDirection === 'asc'" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7" />
              <path v-else stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          <button type="button" class="col-span-2 inline-flex items-center gap-1 text-left hover:text-gray-700 transition-colors" @click="toggleSort('updated_at')">
            <span>更新时间</span>
            <svg v-if="sortField === 'updated_at'" class="w-3.5 h-3.5 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path v-if="sortDirection === 'asc'" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7" />
              <path v-else stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          <div class="col-span-1 text-right">操作</div>
       </div>
       <div class="divide-y divide-gray-100">
          <div 
             v-for="ds in visibleDatasets" 
             :key="ds.id" 
             class="grid grid-cols-12 gap-4 px-6 py-4 items-center transition-colors cursor-pointer group"
             :class="isSelected(ds.id) ? 'bg-blue-50/50' : 'hover:bg-gray-50'"
             @click="goToTables(ds.id)"
          >
             <!-- Name & Meta -->
             <div class="col-span-3 flex items-center gap-3">
                <input
                   v-if="canBatchDelete"
                   type="checkbox"
                   class="h-3.5 w-3.5 shrink-0 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                   :checked="isSelected(ds.id)"
                   :aria-label="`选择数据集 ${ds.display_name}`"
                   @click.stop
                   @change="toggleSelect(ds.id)"
                />
                <div class="w-10 h-10 rounded-lg bg-gray-50 flex-shrink-0 flex items-center justify-center text-xl border border-gray-100">
                   <component :is="getDatasetIcon(ds.name)" class="w-5 h-5 text-blue-600" />
                </div>
                <div class="min-w-0">
                   <div class="flex items-center gap-2">
                      <h3 class="text-sm font-bold text-gray-900 group-hover:text-primary transition-colors truncate">{{ ds.display_name }}</h3>
                      <span 
                        v-if="driftSummary[ds.id]"
                        @click.stop="openDriftAlerts(ds)"
                        class="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[10px] font-semibold bg-amber-50 text-amber-600 border border-amber-200 hover:bg-amber-100 cursor-pointer shadow-2xs shrink-0"
                        title="存在待处理的 Schema 漂移异常"
                      >
                        <svg class="w-2.5 h-2.5 text-amber-500 animate-pulse" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
                        {{ driftSummary[ds.id] }}
                      </span>
                   </div>
                   <div class="flex items-center gap-2 mt-0.5">
                      <span class="text-xs font-mono text-gray-400">#{{ ds.name }}</span>
                   </div>
                </div>
             </div>

             <!-- Status Toggle -->
             <div class="col-span-2">
                <div 
                  class="flex items-center gap-2" 
                  :class="isAdmin || hasPermission('element:metadata:sync') ? 'cursor-pointer' : 'cursor-default opacity-80'"
                  @click.stop="(isAdmin || hasPermission('element:metadata:sync')) && toggleStatus(ds)"
                >
                  <div 
                    class="relative inline-flex h-4 w-8 items-center rounded-full transition-colors duration-200 ease-in-out shadow-inner"
                    :class="ds.status === 1 ? 'bg-green-500' : 'bg-gray-300'"
                  >
                    <span
                      class="inline-block h-3 w-3 transform rounded-full bg-white transition-transform duration-200 ease-in-out shadow-sm"
                      :class="ds.status === 1 ? 'translate-x-4.5' : 'translate-x-0.5'"
                    />
                  </div>
                  <span class="text-[10px] font-bold uppercase tracking-wider" :class="ds.status === 1 ? 'text-green-600' : 'text-gray-400'">
                    {{ ds.status === 1 ? 'Active' : 'Off' }}
                  </span>
                </div>
             </div>
             <!-- Stats -->
             <div class="col-span-2 flex items-center gap-3">
                <div class="text-center">
                   <div class="text-[9px] text-gray-400 uppercase">Tables</div>
                   <div class="font-bold text-gray-700 text-xs">{{ ds.table_count || 0 }}</div>
                </div>
                <div class="w-px h-4 bg-gray-100"></div>
                <div class="text-center">
                   <div class="text-[9px] text-gray-400 uppercase">Metrics</div>
                   <div class="font-bold text-gray-700 text-xs">{{ ds.metric_count || 0 }}</div>
                </div>
             </div>

             <!-- Quality Score -->
             <div class="col-span-1">
                <span
                   class="inline-flex items-center px-1.5 py-0.5 rounded border text-[10px] font-bold"
                   :class="qualityBadgeClass(ds.quality_score)"
                   :title="qualityTooltip(ds)"
                >
                   {{ ds.quality_score ?? '—' }}
                   <span v-if="ds.quality_breakdown?.degraded" class="text-amber-500 ml-0.5">⚠</span>
                </span>
             </div>

             <!-- RAG Status -->
             <div class="col-span-1">
                <span 
                   v-if="!isLocalMode && ds.rag_sync_status !== undefined && ds.rag_sync_status !== 0"
                   class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider border"
                   :class="{
                      'bg-green-50 text-green-600 border-green-100': ds.rag_sync_status === 2,
                      'bg-blue-50 text-blue-600 border-blue-100 animate-pulse': ds.rag_sync_status === 1,
                      'bg-red-50 text-red-600 border-red-100': ds.rag_sync_status === -1,
                      'bg-amber-50 text-amber-600 border-amber-100': ds.rag_sync_status === 3
                   }"
                >
                   {{ ds.rag_sync_status === 2 ? 'Synced' : (ds.rag_sync_status === 1 ? 'Syncing' : (ds.rag_sync_status === 3 ? 'Modified' : 'Failed')) }}
                </span>
                <span v-else class="text-[10px] text-gray-300">-</span>
             </div>

             <!-- Updated：精确到秒，排查「谁在什么时候改的」时日期不够用 -->
             <div class="col-span-2 text-[10px] text-gray-400 font-mono">
                {{ formatUpdatedAt(ds.updated_at) }}
             </div>

             <!-- Actions：与卡片一致，收进「…」，靠最右 -->
             <div class="col-span-1 flex justify-end items-center">
                <button
                   type="button"
                   class="inline-flex h-7 w-7 items-center justify-center rounded-lg text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700"
                   :class="{ 'bg-gray-100 text-gray-700': openRowMenuId === ds.id }"
                   title="更多操作"
                   @click.stop="toggleRowMenu(ds, $event)"
                >
                   <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 12h.01M12 12h.01M19 12h.01M6 12a1 1 0 11-2 0 1 1 0 012 0zm7 0a1 1 0 11-2 0 1 1 0 012 0zm7 0a1 1 0 11-2 0 1 1 0 012 0z" />
                   </svg>
                </button>
             </div>
          </div>
       </div>
       </div>
    </div>

    <!-- 列表视图的「…」菜单：表格容器是 overflow-x-auto，绝对定位会被裁，这里 Teleport 到 body -->
    <Teleport to="body">
      <div
        v-if="rowMenuDataset"
        class="fixed inset-0 z-[80]"
        @click="closeRowMenu"
        @wheel.passive="closeRowMenu"
        @touchmove.passive="closeRowMenu"
      >
        <div
          class="absolute w-52 max-h-[19rem] overflow-y-auto rounded-xl border border-gray-100 bg-white py-1 shadow-xl"
          :style="{ top: `${rowMenuPos.top}px`, left: `${rowMenuPos.left}px` }"
          @click.stop
        >
          <button
            type="button"
            class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs text-gray-700 transition-colors hover:bg-gray-50"
            @click="withRowMenu((d) => goToTables(d.id))"
          >
            <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6" /></svg>
            进入数据集
          </button>
          <button
            type="button"
            class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs text-gray-700 transition-colors hover:bg-gray-50"
            @click="withRowMenu((d) => openDriftAlerts(d))"
          >
            <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 12h4l3-6 4 12 3-6h4" /></svg>
            Schema 巡检
          </button>
          <button
            v-if="hasPermission('element:metadata:sync')"
            type="button"
            class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:hover:bg-transparent"
            :class="(rowMenuDataset.status === 1 && isEngineReady && !isLocalMode) ? 'text-gray-700' : 'text-gray-300'"
            :disabled="!isEngineReady || rowMenuDataset.status !== 1 || isLocalMode || syncingId === rowMenuDataset.id"
            @click="withRowMenu((d) => openSyncFromMenu(d))"
          >
            <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 16.5V9.75m0 0 3 3m-3-3-3 3M6.75 19.5a4.5 4.5 0 0 1-1.41-8.775 5.25 5.25 0 0 1 10.233-2.33 3 3 0 0 1 3.758 3.848A3.752 3.752 0 0 1 18 19.5H6.75Z" /></svg>
            同步到 RAGFlow
          </button>
          <button
            v-if="hasPermission('element:metadata:edit')"
            type="button"
            class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs text-gray-700 transition-colors hover:bg-gray-50"
            @click="withRowMenu((d) => openPermissionTab(d.id))"
          >
            <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" /></svg>
            权限管理
          </button>
          <button
            v-if="hasPermission('element:metadata:edit')"
            type="button"
            title="元数据管理 → 数据行策略"
            class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs text-gray-700 transition-colors hover:bg-gray-50"
            @click="withRowMenu((d) => openRowPolicyModal(d))"
          >
            <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h10" /></svg>
            数据行策略
          </button>
          <button
            v-if="hasPermission('element:metadata:edit')"
            type="button"
            class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs text-gray-700 transition-colors hover:bg-gray-50"
            @click="withRowMenu((d) => openEditDatasetModal(d))"
          >
            <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
            编辑数据集
          </button>
          <button
            v-if="hasPermission('element:metadata:edit')"
            type="button"
            class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs font-medium text-blue-600 transition-colors hover:bg-blue-50"
            @click="withRowMenu((d) => openCopyWizard(d))"
          >
            <svg class="h-4 w-4 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V5a2 2 0 012-2h6a2 2 0 012 2v10a2 2 0 01-2 2h-2M8 9h6a2 2 0 012 2v10a2 2 0 01-2 2H8a2 2 0 01-2-2V11a2 2 0 012-2z" /></svg>
            复制新建
          </button>
          <template v-if="hasPermission('element:metadata:delete')">
            <div class="my-1 border-t border-gray-100"></div>
            <button
              type="button"
              class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs text-red-600 transition-colors hover:bg-red-50"
              @click="withRowMenu((d) => openDeleteModal(d))"
            >
              <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
              删除数据集
            </button>
          </template>
        </div>
      </div>
    </Teleport>

    <!-- 分批渲染：避免数据量大时一次挂载全部卡片造成卡顿 -->
    <div v-if="!loading && hasMoreDatasets" class="flex flex-col items-center gap-1.5 pt-2">
      <button
        type="button"
        class="px-4 py-2 text-xs font-semibold text-blue-600 bg-white border border-blue-200 rounded-lg hover:bg-blue-50 transition-colors"
        @click="loadMoreDatasets"
      >
        加载更多（已显示 {{ visibleDatasets.length }} / {{ displayDatasets.length }}）
      </button>
      <span class="text-[10px] text-gray-400">列表分批渲染，避免数据量大时卡顿</span>
    </div>

    <!-- Batch Delete Confirmation Modal -->
    <Teleport to="body">
      <div
        v-if="showBatchDeleteModal"
        class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
        @click.self="!batchDeleting && (showBatchDeleteModal = false)"
      >
        <div class="w-full max-w-md overflow-hidden rounded-xl border border-gray-100 bg-white shadow-2xl">
          <div class="p-6">
            <div class="mb-4 flex h-14 w-14 items-center justify-center rounded-full border border-red-100 bg-red-50">
              <svg class="h-7 w-7 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
            </div>
            <h3 class="text-lg font-bold text-gray-900">批量删除 {{ selectedCount }} 个数据集</h3>
            <p class="mt-2 text-sm leading-relaxed text-gray-500">
              将连同其表、字段、关系、指标一起删除，RAGFlow 侧的关联数据集也会同步清理。
              <b class="text-red-600">此操作不可恢复。</b>
            </p>
            <div class="mt-3 max-h-32 space-y-1 overflow-y-auto rounded-lg border border-gray-100 bg-gray-50/60 px-3 py-2 text-xs text-gray-600">
              <div v-for="ds in selectedDatasetsPreview" :key="ds.id" class="truncate">
                · {{ ds.display_name }} <span class="text-gray-400">#{{ ds.name }}</span>
              </div>
              <div v-if="selectedCount > selectedDatasetsPreview.length" class="text-gray-400">
                … 等共 {{ selectedCount }} 个
              </div>
            </div>
          </div>
          <div class="flex justify-end gap-2 bg-gray-50 px-6 py-4">
            <button
              type="button"
              class="rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:opacity-60"
              :disabled="batchDeleting"
              @click="showBatchDeleteModal = false"
            >
              取消
            </button>
            <button
              type="button"
              class="rounded-lg bg-red-600 px-5 py-2 text-sm font-medium text-white shadow-md shadow-red-500/30 transition-colors hover:bg-red-700 disabled:opacity-60"
              :disabled="batchDeleting"
              @click="confirmBatchDelete"
            >
              {{ batchDeleting ? '删除中…' : '确认删除' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Delete Confirmation Modal -->
    <Teleport to="body">
      <div v-if="showDeleteModal" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="showDeleteModal = false">
        <div class="bg-white rounded-xl shadow-2xl w-full max-w-sm overflow-hidden border border-gray-100 transform transition-all animate-fade-in-up">
          <div class="p-6 text-center">
             <div class="w-16 h-16 bg-red-50 rounded-full flex items-center justify-center mx-auto mb-4 border border-red-100">
                <svg class="w-8 h-8 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
             </div>
             <h3 class="text-lg font-bold text-gray-900 mb-2">确认删除数据集?</h3>
             <p class="text-sm text-gray-500 mb-6 leading-relaxed">
               您即将删除 <b>{{ deletingDataset?.name }}</b>。<br>
               此操作将永久删除该数据集及其包含的所有表结构定义，且<span class="text-red-600 font-bold">不可恢复</span>。
             </p>
             <div class="flex gap-3 justify-center">
                <button @click="showDeleteModal = false" class="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 bg-white">取消</button>
                <button @click="confirmDelete" class="px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg text-sm font-medium shadow-md transition-colors shadow-red-500/30">确认删除</button>
             </div>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Sync Confirmation Modal -->
    <Teleport to="body">
      <div v-if="showSyncConfirmModal" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="showSyncConfirmModal = false">
        <div class="bg-white rounded-xl shadow-2xl w-full max-w-sm overflow-hidden border border-gray-100 transform transition-all animate-fade-in-up">
          <div class="p-6 text-center">
             <div class="w-16 h-16 bg-indigo-50 rounded-full flex items-center justify-center mx-auto mb-4 border border-indigo-100">
                <svg class="w-8 h-8 text-indigo-500" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 16h5v5"/></svg>
             </div>
             <h3 class="text-lg font-bold text-gray-900 mb-2">同步到 RAGFlow?</h3>
             <p class="text-sm text-gray-500 mb-6 leading-relaxed">
               即将把 <b>{{ syncingDataset?.display_name }}</b> 的元数据同步到 RAGFlow 知识库。<br>
               这会<b>覆盖</b>远端已有的同名文件并触发重新解析。
             </p>
             <div class="flex gap-3 justify-center">
                <button @click="showSyncConfirmModal = false" class="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 bg-white">取消</button>
                <button @click="confirmSync" class="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-sm font-medium shadow-md transition-colors shadow-indigo-500/30">确认同步</button>
             </div>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Test Retrieval Modal -->
    <Teleport to="body">
      <div v-if="showTestModal" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="showTestModal = false">
        <div class="bg-white rounded-xl shadow-2xl w-full max-w-6xl h-[88vh] flex flex-col overflow-hidden border border-gray-100 animate-fade-in-up">
          <!-- Header -->
          <div class="p-6 border-b border-gray-100 flex justify-between items-center bg-amber-50/30">
            <div class="flex items-center gap-3">
               <div class="w-10 h-10 rounded-lg bg-amber-100 text-amber-600 flex items-center justify-center border border-amber-200">
                  <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
               </div>
               <div>
                 <h2 class="text-xl font-bold text-gray-900">Schema 检索模拟器</h2>
                 <p class="text-xs text-gray-500 font-medium">模拟 AI Agent 的检索过程，验证关键词召回的准确性。</p>
               </div>
            </div>
            <button @click="showTestModal = false" class="text-gray-400 hover:text-gray-600 transition-colors">
              <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
            </button>
          </div>

          <div class="flex-1 overflow-hidden flex flex-col">
             <!-- Search Bar -->
             <div class="p-6 bg-white border-b border-gray-100 flex flex-col gap-4">
                <div class="flex gap-4">
                   <input 
                     v-model="testQuery" 
                     @keyup.enter="handleTestRetrieval"
                     class="flex-1 bg-white border border-gray-200 rounded-xl px-4 py-3 focus:outline-none focus:ring-2 focus:ring-amber-500 transition-all disabled:bg-gray-100"
                     placeholder="输入用户问题，例如：'查询上海机房的PUE'..."
                   >
                   <button 
                     @click="handleTestRetrieval"
                     :disabled="testLoading || !testQuery"
                     class="px-6 py-3 bg-amber-500 hover:bg-amber-600 text-white rounded-xl font-bold shadow-lg shadow-amber-500/20 transition-all disabled:opacity-50 flex items-center gap-2"
                   >
                     <svg v-if="testLoading" class="animate-spin h-5 w-5 text-white" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                     <span v-else>执行检索</span>
                   </button>
                </div>

                <!-- 折叠触发按钮 -->
                <div class="flex items-center">
                  <button 
                    @click="showAdvancedSettings = !showAdvancedSettings"
                    class="text-xs text-gray-500 hover:text-amber-600 transition-colors flex items-center gap-1 font-medium cursor-pointer"
                  >
                    <svg 
                      class="w-4 h-4 transition-transform duration-200" 
                      :class="{ 'rotate-90': showAdvancedSettings }"
                      fill="none" stroke="currentColor" viewBox="0 0 24 24"
                    >
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                    </svg>
                    高级参数配置 (仅临时测试)
                  </button>
                </div>

                <!-- 折叠面板内容 -->
                <div v-if="showAdvancedSettings" class="p-4 bg-gray-50/80 rounded-xl border border-gray-100 grid grid-cols-1 md:grid-cols-2 gap-4 text-xs transition-all duration-300">
                  <!-- Provider -->
                  <div class="flex items-center gap-3">
                    <span class="w-24 text-gray-600 font-medium shrink-0">元数据提供方:</span>
                    <select 
                      v-model="tempProvider"
                      class="bg-white border border-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-amber-500 flex-1 cursor-pointer"
                    >
                      <option value="default">系统默认配置</option>
                      <option value="local">本地检索模式 (local)</option>
                      <option value="ragflow">知识库检索模式 (ragflow)</option>
                    </select>
                  </div>

                  <!-- Top K -->
                  <div class="flex items-center gap-3">
                    <span class="w-24 text-gray-600 font-medium shrink-0 flex items-center gap-1">
                      Top K 数量:
                      <span class="group relative cursor-pointer text-gray-400 hover:text-gray-600">
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                        <span class="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 hidden group-hover:block w-48 bg-slate-900 text-white text-[10px] p-2 rounded shadow-xl leading-normal z-50">
                          元数据检索时返回的 Top K 数量，控制 AI 可感知的表结构上限 (对应配置: ragflow_metadata_top_k)
                        </span>
                      </span>
                    </span>
                    <div class="flex items-center gap-2 flex-1">
                      <input 
                        type="range" min="1" max="20" step="1"
                        v-model.number="tempTopK"
                        class="flex-1 accent-amber-500 cursor-pointer"
                      >
                      <span class="w-8 text-right font-mono font-semibold text-gray-700 bg-white border border-gray-200 rounded px-1 py-0.5">{{ tempTopK }}</span>
                    </div>
                  </div>

                  <!-- Similarity Threshold -->
                  <div class="flex items-center gap-3">
                    <span class="w-24 text-gray-600 font-medium shrink-0 flex items-center gap-1">
                      相似度阈值:
                      <span class="group relative cursor-pointer text-gray-400 hover:text-gray-600">
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                        <span class="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 hidden group-hover:block w-48 bg-slate-900 text-white text-[10px] p-2 rounded shadow-xl leading-normal z-50">
                          语义检索的最低相似度阈值 (0-1)，低于此阈值的匹配项将被忽略，越低召回率越高但越不精准 (对应配置: ragflow_similarity_threshold)
                        </span>
                      </span>
                    </span>
                    <div class="flex items-center gap-2 flex-1">
                      <input 
                        type="range" min="0" max="1" step="0.05"
                        v-model.number="tempThreshold"
                        class="flex-1 accent-amber-500 cursor-pointer"
                      >
                      <span class="w-8 text-right font-mono font-semibold text-gray-700 bg-white border border-gray-200 rounded px-1 py-0.5">{{ tempThreshold.toFixed(2) }}</span>
                    </div>
                  </div>

                  <!-- Vector Weight -->
                  <div v-if="showVectorWeight" class="flex items-center gap-3">
                    <span class="w-24 text-gray-600 font-medium shrink-0 flex items-center gap-1">
                      向量检索权重:
                      <span class="group relative cursor-pointer text-gray-400 hover:text-gray-600">
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                        <span class="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 hidden group-hover:block w-48 bg-slate-900 text-white text-[10px] p-2 rounded shadow-xl leading-normal z-50">
                          RAGFlow 混合检索中向量检索的权重 (0-1)，剩余为全文检索权重 (对应配置: ragflow_vector_weight)
                        </span>
                      </span>
                    </span>
                    <div class="flex items-center gap-2 flex-1">
                      <input 
                        type="range" min="0" max="1" step="0.05"
                        v-model.number="tempVectorWeight"
                        class="flex-1 accent-amber-500 cursor-pointer"
                      >
                      <span class="w-8 text-right font-mono font-semibold text-gray-700 bg-white border border-gray-200 rounded px-1 py-0.5">{{ tempVectorWeight.toFixed(2) }}</span>
                    </div>
                  </div>
                </div>
             </div>

             <!-- Results -->
             <div class="flex-1 overflow-hidden bg-gray-50 flex">
                <!-- Left Column: Results -->
                <div class="flex-1 overflow-y-auto p-6 border-r border-gray-200">
                    <div v-if="!testResult && !testLoading" class="h-full flex flex-col items-center justify-center text-gray-400 opacity-50">
                       <svg class="w-16 h-16 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 16l2.879-2.879m0 0a3 3 0 104.243-4.242 3 3 0 00-4.243 4.242zM21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                       <p>请输入关键词开始测试</p>
                    </div>

                    <div v-else-if="testResult" class="space-y-6">
                       <!-- Status Banner -->
                       <div v-if="testResult.found" class="bg-green-50 border border-green-200 rounded-lg p-4 flex items-start gap-3">
                          <svg class="w-5 h-5 text-green-600 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                          <div>
                             <h4 class="font-bold text-green-800 text-sm">检索成功 (Hit)</h4>
                             <p class="text-xs text-green-700 mt-1">
                                Provider: <b class="uppercase">{{ testResult.provider }}</b> | 
                                命中了 <b>{{ testResult.datasets.length }}</b> 个数据集
                             </p>
                          </div>
                       </div>
                       <div v-else class="bg-red-50 border border-red-200 rounded-lg p-4 flex items-start gap-3">
                          <svg class="w-5 h-5 text-red-600 mt-0.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                          <div>
                            <span class="font-bold text-red-800 text-sm">未找到相关元数据 (Miss)</span>
                            <p class="text-xs text-red-600 mt-1">请查看右侧「执行日志 (Trace)」：含 Redis 连接、索引 num_docs、向量维度、KNN 原始命中与阈值过滤详情。</p>
                          </div>
                       </div>

                       <!-- Context Preview -->
                       <div v-if="testResult.found" class="bg-slate-900 rounded-xl overflow-hidden shadow-lg border border-slate-700">
                          <div class="px-4 py-2 bg-slate-800 border-b border-slate-700 flex justify-between items-center">
                             <span class="text-xs font-mono text-slate-400">Generated Context (YAML/Markdown)</span>
                             <span class="text-[10px] bg-amber-500/20 text-amber-400 px-2 py-0.5 rounded border border-amber-500/30">Inject to LLM</span>
                          </div>
                          <pre class="p-6 text-xs font-mono text-emerald-400 overflow-x-auto whitespace-pre-wrap">{{ testResult.context }}</pre>
                       </div>
                    </div>
                </div>

                <!-- Right Column: Debug Logs -->
                <div class="w-1/3 bg-slate-50 border-l border-gray-200 flex flex-col" v-if="testResult">
                    <div class="px-4 py-3 border-b border-gray-200 bg-white">
                        <h3 class="text-sm font-bold text-gray-700 flex items-center gap-2">
                            <svg class="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"/></svg>
                            执行日志 (Trace)
                        </h3>
                    </div>
                    <div class="flex-1 overflow-y-auto p-4 space-y-2 font-mono text-xs">
                        <div
                          v-for="(log, i) in testResult.logs"
                          :key="i"
                          class="p-2 rounded border text-xs font-mono break-all shadow-sm"
                          :class="String(log).includes('[WARN]') || String(log).includes('[HINT]') || String(log).includes('failed')
                            ? 'bg-amber-50 border-amber-200 text-amber-900'
                            : String(log).includes('[KNN]') || String(log).includes('[Redis]') || String(log).includes('[RediSearch]')
                              ? 'bg-slate-50 border-slate-200 text-slate-700'
                              : 'bg-white border-gray-200 text-gray-600'"
                        >
                            <span class="text-gray-300 mr-2">{{ Number(i) + 1 }}.</span>
                            {{ log }}
                        </div>
                        <div v-if="!testResult.logs || testResult.logs.length === 0" class="text-gray-400 italic text-center mt-10">
                            暂无详细日志
                        </div>
                    </div>
                </div>
             </div>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Spec Modal -->
    <Teleport to="body">
      <div v-if="showSpecModal" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="showSpecModal = false">
        <div class="bg-white rounded-xl shadow-2xl w-full max-w-5xl h-[85vh] flex flex-col overflow-hidden border border-gray-100 animate-fade-in-up">
          <!-- Header -->
          <div class="p-6 border-b border-gray-100 flex justify-between items-center bg-purple-50/30">
            <div class="flex items-center gap-3">
               <div class="w-10 h-10 rounded-lg bg-purple-100 text-purple-600 flex items-center justify-center border border-purple-200">
                  <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19.428 15.428a2 2 0 00-1.022-.547l-2.384-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z"/></svg>
               </div>
               <div>
                 <h2 class="text-xl font-bold text-gray-900">元数据设计规范与全流程指引</h2>
                 <p class="text-xs text-gray-500 font-medium">构建 AI 可理解的业务语义层，提升 Text-to-SQL 准确率。</p>
               </div>
            </div>
            <div class="flex items-center gap-3">
              <button
                v-if="!showFlowGuide"
                type="button"
                @click="restoreFlowGuide"
                class="inline-flex items-center gap-1 text-xs font-medium text-purple-700 bg-purple-100/70 hover:bg-purple-200 px-3 py-1.5 rounded-lg transition-colors cursor-pointer"
              >
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                <span>恢复顶部流程提示</span>
              </button>
              <button @click="showSpecModal = false" class="text-gray-400 hover:text-gray-600 transition-colors cursor-pointer">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
              </button>
            </div>
          </div>

          <!-- Tabs -->
          <div class="flex border-b border-gray-200 bg-white px-6">
             <button 
               v-for="tab in ['flow', 'concept', 'structure', 'fields', 'practice']" 
               :key="tab"
               @click="activeSpecTab = tab"
               class="px-4 py-3 text-sm font-medium border-b-2 transition-colors capitalize cursor-pointer"
               :class="activeSpecTab === tab ? 'border-purple-600 text-purple-700' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'"
             >
               {{ tab === 'flow' ? '全流程指引 (Workflow)' :
                  tab === 'concept' ? '核心理念 (Concepts)' : 
                  tab === 'structure' ? 'YAML 结构 (Schema)' : 
                  tab === 'fields' ? '字段规范 (Fields)' : '最佳实践 (Best Practice)' }}
             </button>
          </div>

          <!-- Content -->
          <div class="flex-1 overflow-y-auto p-8 bg-gray-50/50">
           
             <!-- Tab 0: Workflow Flow -->
             <div v-if="activeSpecTab === 'flow'" class="space-y-6 max-w-4xl mx-auto">
                <div class="bg-gradient-to-r from-blue-50 to-indigo-50 border-l-4 border-blue-600 p-4 rounded-r-lg shadow-2xs">
                   <h3 class="font-bold text-blue-900 mb-1">从数据源到 AI 智能体的端到端流程</h3>
                   <p class="text-xs text-blue-700 leading-relaxed">
                      遵循标准 4 步流程：先连接摸排数据源，再通过 AI 智能解析表结构与枚举，继而完善指标与跨库实体关系，最后同步至向量库供 ChatBI 与 Agent 消费。
                   </p>
                </div>

                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                   <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                      <div>
                         <div class="flex items-center gap-2 mb-2">
                            <span class="w-6 h-6 rounded-full bg-blue-600 text-white flex items-center justify-center text-xs font-bold">1</span>
                            <h4 class="font-bold text-gray-900 text-sm">连接与摸排数据源</h4>
                         </div>
                         <p class="text-xs text-gray-500 leading-relaxed">
                            配置外部关系型/分析型数据库连接（MySQL/PG/ClickHouse/Oracle/SQL Server 等），支持连通性测试与一键异步智能摸排，提取表画像、字段分布与样本。
                         </p>
                      </div>
                      <div class="mt-4 pt-3 border-t border-gray-100 flex justify-end">
                         <button
                            type="button"
                            @click="showSpecModal = false; router.push('/dashboard/data-sources')"
                            class="text-xs text-blue-600 hover:text-blue-800 font-medium inline-flex items-center gap-1 cursor-pointer"
                         >
                            前往数据源管理 &rarr;
                         </button>
                      </div>
                   </div>

                   <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                      <div>
                         <div class="flex items-center gap-2 mb-2">
                            <span class="w-6 h-6 rounded-full bg-indigo-600 text-white flex items-center justify-center text-xs font-bold">2</span>
                            <h4 class="font-bold text-gray-900 text-sm">智能导入与 AI 语义解析</h4>
                         </div>
                         <p class="text-xs text-gray-500 leading-relaxed">
                            支持摸排画像免推理导入、库表直读或 DDL 粘贴。大模型自动提取中文业务名、字段语义、枚举字典与主键标记，并在前端提供向导式审查与保存。
                         </p>
                      </div>
                      <div class="mt-4 pt-3 border-t border-gray-100 flex justify-end">
                         <button
                            type="button"
                            @click="showSpecModal = false; showImportModal = true"
                            class="text-xs text-indigo-600 hover:text-indigo-800 font-medium inline-flex items-center gap-1 cursor-pointer"
                         >
                            向导新建数据集 &rarr;
                         </button>
                      </div>
                   </div>

                   <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                      <div>
                         <div class="flex items-center gap-2 mb-2">
                            <span class="w-6 h-6 rounded-full bg-emerald-600 text-white flex items-center justify-center text-xs font-bold">3</span>
                            <h4 class="font-bold text-gray-900 text-sm">建模指标与实体关联</h4>
                         </div>
                         <p class="text-xs text-gray-500 leading-relaxed">
                            在数据集中维护业务指标（Metrics）与计算公式，配置 1:1、1:N 实体关联条件与跨库 Join 拓扑图谱，支持 AI 智能推荐指标与关系。
                         </p>
                      </div>
                      <div class="mt-4 pt-3 border-t border-gray-100 flex justify-end">
                         <span class="text-xs text-gray-400">点击下方数据集卡片进入配置</span>
                      </div>
                   </div>

                   <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                      <div>
                         <div class="flex items-center gap-2 mb-2">
                            <span class="w-6 h-6 rounded-full bg-amber-600 text-white flex items-center justify-center text-xs font-bold">4</span>
                            <h4 class="font-bold text-gray-900 text-sm">向量同步与智能体挂载</h4>
                         </div>
                         <p class="text-xs text-gray-500 leading-relaxed">
                            一键将数据集导出为高压缩比 YAML 并同步至向量知识库（RAGFlow / Redis），供 ChatBI 与 Agent 智能体精准召回表结构并生成 SQL。
                         </p>
                      </div>
                      <div class="mt-4 pt-3 border-t border-gray-100 flex justify-end">
                         <span class="text-xs text-gray-400">在智能体工作台挂载使用</span>
                      </div>
                   </div>
                </div>
             </div>

             <!-- Tab 1: Concepts -->
             <div v-if="activeSpecTab === 'concept'" class="space-y-6 max-w-4xl mx-auto">
                <div class="bg-blue-50 border-l-4 border-blue-500 p-4 rounded-r-lg">
                   <h3 class="font-bold text-blue-800 mb-2">为什么需要 Semantic Layer?</h3>
                   <p class="text-sm text-blue-700 leading-relaxed">
                      单纯依赖数据库 Schema (DDL) 是不够的。大模型不知道 "pue" 代表 "能源利用效率"，也不知道 "status=1" 代表 "正常"。
                      元数据层通过注入<b>业务语义</b>、<b>枚举含义</b>和<b>计算逻辑</b>，弥补了这一鸿沟。
                   </p>
                </div>
              
                <div class="grid grid-cols-2 gap-6">
                   <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm">
                      <div class="w-8 h-8 bg-green-100 rounded-full flex items-center justify-center text-green-600 mb-3 font-bold">1</div>
                      <h4 class="font-bold text-gray-900 mb-2">这是什么? (Definition)</h4>
                      <p class="text-xs text-gray-500">不仅提供表名，还提供通俗易懂的业务术语和描述。例如：将 `metrics_table` 标记为 "实时能耗监控表"。</p>
                   </div>
                   <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm">
                      <div class="w-8 h-8 bg-indigo-100 rounded-full flex items-center justify-center text-indigo-600 mb-3 font-bold">2</div>
                      <h4 class="font-bold text-gray-900 mb-2">有哪些值? (Enums)</h4>
                      <p class="text-xs text-gray-500">对于分类字段（Status/Type），显式列出所有可能的枚举值及其含义。防止 AI 幻觉生成不存在的状态码。</p>
                   </div>
                   <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm">
                      <div class="w-8 h-8 bg-amber-100 rounded-full flex items-center justify-center text-amber-600 mb-3 font-bold">3</div>
                      <h4 class="font-bold text-gray-900 mb-2">什么关系? (Relationships)</h4>
                      <p class="text-xs text-gray-500">定义跨表的 Join 路径。即使数据库没有外键约束，这里也要告诉 AI 哪两个字段是可以关联的。</p>
                   </div>
                   <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm">
                      <div class="w-8 h-8 bg-rose-100 rounded-full flex items-center justify-center text-rose-600 mb-3 font-bold">4</div>
                      <h4 class="font-bold text-gray-900 mb-2">怎么计算? (Metrics)</h4>
                      <p class="text-xs text-gray-500">预定义复杂的计算公式（如 PUE 计算、同比环比）。AI 只需引用指标名，无需重写复杂 SQL。</p>
                   </div>
                </div>
             </div>

             <!-- Tab 2: Structure -->
             <div v-else-if="activeSpecTab === 'structure'" class="max-w-4xl mx-auto">
                <div class="bg-slate-900 rounded-xl overflow-hidden border border-slate-700 shadow-lg">
                   <div class="bg-slate-800 px-4 py-2 flex justify-between items-center">
                      <span class="text-xs font-mono text-slate-400">sample_metadata.yaml</span>
                      <span class="text-[10px] text-slate-500 uppercase">YAML</span>
                   </div>
                   <pre class="p-6 text-sm font-mono text-emerald-400 overflow-x-auto leading-relaxed">version: "1.0"
  domain: "IDC_Energy" 
  entities:
    - name: "metrics_realtime"
      term: "实时能耗指标表"
      description: "存储各机房实时电力数据，每分钟更新。"
      synonyms: ["实时数据", "监控表"]
      columns:
        - name: "pue"
          term: "PUE值"
          type: "float"
          description: "越低越好 (Total Power / IT Power)"
          examples: [1.2, 1.5]
        
        - name: "room_id"
          term: "机房ID"
          foreign_key: "rooms.id"

        - name: "metric_type"
          term: "指标类型"
          enums:
            - value: "voltage"
              description: "电压 (V)"
            - value: "current"
              description: "电流 (A)"

  relationships:
    - source: "metrics_realtime.room_id"
      target: "rooms.id"
      type: "many_to_one"
      description: "指标归属机房"</pre>
                </div>
             </div>

             <!-- Tab 3: Fields -->
             <div v-else-if="activeSpecTab === 'fields'" class="max-w-4xl mx-auto space-y-6">
                <div class="bg-white rounded-xl border border-gray-200 overflow-hidden shadow-sm">
                   <table class="min-w-full text-sm text-left">
                      <thead class="bg-gray-50 text-gray-700 font-bold uppercase text-xs">
                         <tr>
                            <th class="px-6 py-3 border-b">Scope</th>
                            <th class="px-6 py-3 border-b">Field</th>
                            <th class="px-6 py-3 border-b">Required</th>
                            <th class="px-6 py-3 border-b">Description</th>
                         </tr>
                      </thead>
                      <tbody class="divide-y divide-gray-100">
                         <tr class="hover:bg-gray-50">
                            <td class="px-6 py-3 font-medium text-gray-900" rowspan="4">Entity (Table)</td>
                            <td class="px-6 py-3 font-mono text-purple-600">name</td>
                            <td class="px-6 py-3 text-red-500 font-bold">Yes</td>
                            <td class="px-6 py-3 text-gray-500">数据库物理表名 (e.g. `t_users`)</td>
                         </tr>
                         <tr class="hover:bg-gray-50">
                            <td class="px-6 py-3 font-mono text-purple-600">term</td>
                            <td class="px-6 py-3 text-red-500 font-bold">Yes</td>
                            <td class="px-6 py-3 text-gray-500">业务通用名称 (e.g. "用户表")</td>
                         </tr>
                         <tr class="hover:bg-gray-50">
                            <td class="px-6 py-3 font-mono text-purple-600">description</td>
                            <td class="px-6 py-3 text-red-500 font-bold">Yes</td>
                            <td class="px-6 py-3 text-gray-500">详细描述，包含数据粒度、更新频率等</td>
                         </tr>
                         <tr class="hover:bg-gray-50">
                            <td class="px-6 py-3 font-mono text-purple-600">synonyms</td>
                            <td class="px-6 py-3 text-gray-400">No</td>
                            <td class="px-6 py-3 text-gray-500">同义词列表，用于增强 RAG 检索命中率</td>
                         </tr>

                         <!-- Columns -->
                         <tr class="hover:bg-gray-50 bg-gray-50/30">
                            <td class="px-6 py-3 font-medium text-gray-900" rowspan="4">Column</td>
                            <td class="px-6 py-3 font-mono text-blue-600">name / term</td>
                            <td class="px-6 py-3 text-red-500 font-bold">Yes</td>
                            <td class="px-6 py-3 text-gray-500">物理列名与业务列名</td>
                         </tr>
                         <tr class="hover:bg-gray-50 bg-gray-50/30">
                            <td class="px-6 py-3 font-mono text-blue-600">enums</td>
                            <td class="px-6 py-3 text-amber-500 font-bold">Vital</td>
                            <td class="px-6 py-3 text-gray-500">枚举值列表。极大地帮助 LLM 生成正确的 WHERE 条件。</td>
                         </tr>
                         <tr class="hover:bg-gray-50 bg-gray-50/30">
                            <td class="px-6 py-3 font-mono text-blue-600">examples</td>
                            <td class="px-6 py-3 text-gray-400">No</td>
                            <td class="px-6 py-3 text-gray-500">典型值示例 (Few-shot)，防止幻觉。</td>
                         </tr>
                         <tr class="hover:bg-gray-50 bg-gray-50/30">
                            <td class="px-6 py-3 font-mono text-blue-600">foreign_key</td>
                            <td class="px-6 py-3 text-gray-400">No</td>
                            <td class="px-6 py-3 text-gray-500">显式关联指针 (e.g. `other_table.id`)</td>
                         </tr>
                      </tbody>
                   </table>
                </div>
             </div>

             <!-- Tab 4: Practice -->
             <div v-else-if="activeSpecTab === 'practice'" class="max-w-4xl mx-auto space-y-8">
                <section>
                   <h3 class="text-lg font-bold text-gray-900 mb-3 flex items-center gap-2"><FolderIcon class="w-5 h-5 text-gray-500" /> 文件组织策略</h3>
                   <div class="bg-white p-5 rounded-xl border border-gray-200">
                      <p class="text-sm text-gray-600 mb-4">
                         建议采用 <b>按业务域 (Domain-Driven)</b> 分组的策略。将逻辑紧密相关的表定义在同一个 YAML 文件中。
                      </p>
                      <div class="flex gap-4">
                         <div class="flex-1 bg-gray-50 p-3 rounded border border-gray-100">
                            <h5 class="font-bold text-xs text-gray-500 uppercase mb-2">推荐</h5>
                            <ul class="text-sm text-gray-700 space-y-1 font-mono">
                               <li>meta/resources.yaml</li>
                               <li>meta/energy_metrics.yaml</li>
                               <li>meta/billing.yaml</li>
                            </ul>
                         </div>
                         <div class="flex-1 bg-red-50 p-3 rounded border border-red-100">
                            <h5 class="font-bold text-xs text-red-400 uppercase mb-2">避免</h5>
                            <ul class="text-sm text-gray-700 space-y-1">
                               <li>🚫 整个库一个大文件 (Too large)</li>
                               <li>🚫 一表一个文件 (Fragmented)</li>
                            </ul>
                         </div>
                      </div>
                   </div>
                </section>

                <section>
                   <h3 class="text-lg font-bold text-gray-900 mb-3 flex items-center gap-2"><CpuChipIcon class="w-5 h-5 text-indigo-600" /> RAG 检索策略</h3>
                   <div class="bg-indigo-50 p-5 rounded-xl border border-indigo-100">
                      <ol class="relative border-l border-indigo-200 ml-3 space-y-6">
                         <li class="ml-6">
                            <span class="absolute flex items-center justify-center w-6 h-6 bg-indigo-100 rounded-full -left-3 ring-4 ring-white text-indigo-600 font-bold text-xs">1</span>
                            <h4 class="font-bold text-gray-900 text-sm">User Query</h4>
                            <p class="text-xs text-gray-500 mt-1">用户提问："帮我查一下上海机房昨天的 PUE"</p>
                         </li>
                         <li class="ml-6">
                            <span class="absolute flex items-center justify-center w-6 h-6 bg-indigo-100 rounded-full -left-3 ring-4 ring-white text-indigo-600 font-bold text-xs">2</span>
                            <h4 class="font-bold text-gray-900 text-sm">Vector Search (Retrieval)</h4>
                            <p class="text-xs text-gray-500 mt-1">系统在向量库中检索关键词 "机房", "PUE"。命中 `res_room` 和 `metrics_pue` 两张表。</p>
                         </li>
                         <li class="ml-6">
                            <span class="absolute flex items-center justify-center w-6 h-6 bg-indigo-100 rounded-full -left-3 ring-4 ring-white text-indigo-600 font-bold text-xs">3</span>
                            <h4 class="font-bold text-gray-900 text-sm">Prompt Injection</h4>
                            <p class="text-xs text-gray-500 mt-1">
                               系统仅将 <b>命中的这 2 张表</b> 的 YAML 片段注入到 LLM 上下文。
                               <br>
                               <span class="inline-block mt-1 bg-white px-2 py-0.5 rounded border border-indigo-100 text-indigo-600">精简上下文 = 更高准确率 + 更低成本</span>
                            </p>
                         </li>
                      </ol>
                   </div>
                </section>
             </div>

          </div>
        </div>
      </div>
    </Teleport>

    <!-- Create Modal -->
    <Teleport to="body">
      <div v-if="showCreateModal" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
        <div class="bg-white rounded-xl shadow-2xl w-full max-w-lg overflow-hidden border border-gray-100">
          <div class="p-6 border-b border-gray-100 flex justify-between items-center">
            <h2 class="text-xl font-bold text-gray-900">新建数据集</h2>
            <button @click="showCreateModal = false" class="text-gray-400 hover:text-gray-600 transition-colors">
              <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
            </button>
          </div>
          <div class="p-6 space-y-4">
            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">物理名称 (Unique Name)</label>
              <input v-model="newDataset.name" type="text" class="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="e.g. nanzi_resources">
            </div>
            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">显示名称 (Display Name)</label>
              <input v-model="newDataset.display_name" type="text" class="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="e.g. 资源数据集">
            </div>
            <div>
              <label class="flex items-center text-sm font-medium text-gray-700 mb-1">
                <span>数据源 ID (Data Source ID)</span>
                <span class="inline-block ml-1 text-gray-400 hover:text-gray-600 cursor-help" title="后续 ChatBI 查数会根据这个数据源来连接数据库的">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                </span>
              </label>
              <div class="relative flex gap-2">
                <input v-model="newDataset.data_source" type="text" class="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="e.g. default_clickhouse or mysql_crm_01">
                <button 
                  type="button"
                  @click="toggleDataSourceDropdown('create')"
                  class="px-3 py-2 bg-white border border-gray-300 rounded-lg text-gray-500 hover:text-blue-500 hover:border-blue-500 transition-colors flex-shrink-0"
                  title="选择已配置的数据源"
                >
                  <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"/></svg>
                </button>
                <div v-if="showCreateDsDropdown" class="absolute right-0 top-full mt-1 w-full bg-white border border-gray-200 rounded-lg shadow-xl z-[60] overflow-y-auto max-h-48 py-1">
                  <div v-if="dbConnections.length === 0" class="px-4 py-2 text-xs text-gray-400 italic">暂无已配置的数据源</div>
                  <button
                    v-else
                    v-for="conn in dbConnections"
                    :key="conn.id"
                    @click="selectDataSource(conn.name, 'create')"
                    type="button"
                    class="w-full text-left px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 hover:text-gray-900 transition-colors flex items-center justify-between"
                  >
                    <span class="font-medium font-mono">{{ conn.name }}</span>
                    <span class="text-[10px] uppercase px-1.5 py-0.5 rounded font-bold bg-blue-50 text-blue-600 border border-blue-100">{{ conn.db_type }}</span>
                  </button>
                </div>
              </div>
              <p class="text-xs text-gray-400 mt-1">留空则使用系统默认 ({{ defaultDataSource }})。若需使用 MySQL，建议 ID 包含 'mysql'。</p>
            </div>
            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">描述</label>
              <textarea v-model="newDataset.description" rows="3" class="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="描述该数据集包含的内容..."></textarea>
            </div>

            <div>

              <label class="flex items-center text-sm font-medium text-gray-700 mb-1">
                <span>业务标签</span>
                <InfoTooltip class="ml-1 text-gray-400" align="left" :text="businessTagHint" />
              </label>
              <div class="flex gap-2 mb-2">
                <input v-model="tagInput" @keyup.enter="addTag" type="text" class="flex-1 border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="输入标签并回车">
              </div>
              <div class="flex flex-wrap gap-2">
                <span v-for="(tag, i) in newDataset.tags" :key="i" class="px-2 py-1 bg-blue-50 text-blue-700 text-xs rounded-full flex items-center gap-1 border border-blue-100">
                  {{ tag }}
                  <button @click="removeTag(newDataset, i as number)" class="text-blue-400 hover:text-red-500 ml-1">&times;</button>
                </span>
              </div>
            </div>
          </div>
          <div class="bg-gray-50 p-6 flex justify-end gap-3">
            <button @click="showCreateModal = false" class="px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors">取消</button>
            <button @click="handleCreate" class="bg-blue-600 hover:bg-blue-700 text-white px-6 py-2 rounded-lg transition-all shadow-md font-medium">确认创建</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Smart Import Wizard -->
    <SmartImportWizard 
      :show="showImportModal" 
      @close="showImportModal = false"
      @saved="fetchDatasets"
    />

    <!-- 复制新建向导：改 ID / 名称（实时查重）、逐张核对表与字段、关系、指标，最后一步才落库 -->
    <CopyDatasetWizard
      :show="showCopyWizard"
      :dataset="copySourceDataset"
      @close="showCopyWizard = false"
      @created="handleDatasetCopied"
    />

    <!-- Edit Dataset Modal -->
    <Teleport to="body">
      <div v-if="showEditDatasetModal" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
        <div class="bg-white rounded-xl shadow-2xl w-full max-w-lg overflow-hidden border border-gray-100">
          <div class="p-6 border-b border-gray-100 flex justify-between items-center bg-blue-50/30">
            <h2 class="text-xl font-bold text-gray-900">编辑数据集</h2>
            <button @click="showEditDatasetModal = false" class="text-gray-400 hover:text-gray-600 transition-colors">
              <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
            </button>
          </div>
          <div class="p-6 space-y-4">
            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">物理名称 (Unique Name)</label>
              <input v-model="editingDataset.name" type="text" disabled class="w-full bg-white border border-gray-300 rounded-lg px-4 py-2 text-gray-500 cursor-not-allowed disabled:bg-gray-100" title="物理名称不可修改">
            </div>
            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">显示名称 (Display Name)</label>
              <input v-model="editingDataset.display_name" type="text" class="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="e.g. 资源数据集">
            </div>
            <div>
              <label class="flex items-center text-sm font-medium text-gray-700 mb-1">
                <span>数据源 ID (Data Source ID)</span>
                <span class="inline-block ml-1 text-gray-400 hover:text-gray-600 cursor-help" title="后续 ChatBI 查数会根据这个数据源来连接数据库的">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                </span>
              </label>
              <div class="relative flex gap-2">
                <input v-model="editingDataset.data_source" type="text" class="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="e.g. default_clickhouse or mysql_crm_01">
                <button 
                  type="button"
                  @click="toggleDataSourceDropdown('edit')"
                  class="px-3 py-2 bg-white border border-gray-300 rounded-lg text-gray-500 hover:text-blue-500 hover:border-blue-500 transition-colors flex-shrink-0"
                  title="选择已配置的数据源"
                >
                  <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"/></svg>
                </button>
                <div v-if="showEditDsDropdown" class="absolute right-0 top-full mt-1 w-full bg-white border border-gray-200 rounded-lg shadow-xl z-[60] overflow-y-auto max-h-48 py-1">
                  <div v-if="dbConnections.length === 0" class="px-4 py-2 text-xs text-gray-400 italic">暂无已配置的数据源</div>
                  <button
                    v-else
                    v-for="conn in dbConnections"
                    :key="conn.id"
                    @click="selectDataSource(conn.name, 'edit')"
                    type="button"
                    class="w-full text-left px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 hover:text-gray-900 transition-colors flex items-center justify-between"
                  >
                    <span class="font-medium font-mono">{{ conn.name }}</span>
                    <span class="text-[10px] uppercase px-1.5 py-0.5 rounded font-bold bg-blue-50 text-blue-600 border border-blue-100">{{ conn.db_type }}</span>
                  </button>
                </div>
              </div>
              <p class="text-xs text-gray-400 mt-1">留空则使用系统默认 ({{ defaultDataSource }})。若需使用 MySQL，建议 ID 包含 'mysql'。</p>
            </div>
            <div>
              <div class="flex justify-between items-center mb-1">
                <label class="block text-sm font-medium text-gray-700">描述</label>
                <button 
                  @click="handleEnhanceMetadata(editingDataset.id)"
                  :disabled="enhancing"
                  class="text-[10px] bg-indigo-50 text-indigo-600 hover:bg-indigo-100 px-2 py-0.5 rounded border border-indigo-100 flex items-center gap-1 font-bold transition-all disabled:opacity-50"
                >
                  <svg v-if="enhancing" class="animate-spin h-3 w-3" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                  <SparklesIcon v-else class="w-3 h-3" />
                  AI 辅助生成 (基于表信息)
                </button>
              </div>
              <textarea v-model="editingDataset.description" rows="3" class="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="描述该数据集包含的内容..."></textarea>
            </div>

            <div>
              <label class="flex items-center text-sm font-medium text-gray-700 mb-1">
                <span>业务标签</span>
                <InfoTooltip class="ml-1 text-gray-400" align="left" :text="businessTagHint" />
              </label>
              <div class="flex gap-2 mb-2">
                <input v-model="tagInput" @keyup.enter="addTag(editingDataset)" type="text" class="flex-1 border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="输入标签并回车">
              </div>
              <div class="flex flex-wrap gap-2">
                <span v-for="(tag, i) in editingDataset.tags" :key="i" class="px-2 py-1 bg-blue-50 text-blue-700 text-xs rounded-full flex items-center gap-1 border border-blue-100">
                  {{ tag }}
                  <button @click="removeTag(editingDataset, i as number)" class="text-blue-400 hover:text-red-500 ml-1">&times;</button>
                </span>
              </div>
            </div>
          </div>
          <div class="bg-gray-50 p-6 flex justify-end gap-3">
            <button @click="showEditDatasetModal = false" class="px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors">取消</button>
            <button @click="handleUpdateDataset" class="bg-blue-600 hover:bg-blue-700 text-white px-6 py-2 rounded-lg transition-all shadow-md font-medium">确认更新</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Empty State -->
    <div v-if="!loading && datasets.length === 0" class="text-center py-24 bg-white rounded-xl border border-dashed border-gray-300">
      <FolderIcon class="w-12 h-12 mx-auto mb-4 text-gray-300" />
      <h3 class="text-lg font-medium text-gray-900">暂无数据集</h3>
      <p class="text-gray-500 mt-1 mb-6">创建一个新的数据集来开始管理元数据。</p>
      <button v-if="hasPermission('element:metadata:edit')" @click="showCreateModal = true" class="text-primary font-medium hover:underline flex items-center justify-center gap-1 mx-auto">
        立即创建 <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
      </button>
    </div>

    <!-- 行级隔离策略弹窗：和数据集详情页的「编辑行级策略」是同一个组件 -->
    <RowPolicyConfigModal
      :show="showPermConfigModal"
      :dataset="rowPolicyDataset"
      focus-row-policy
      @close="showPermConfigModal = false"
      @saved="handleRowPolicySaved"
    />

    <!-- 当前元数据同步实时日志 -->
    <Transition name="sync-log-drawer">
      <aside
        v-if="syncLogOpen"
        class="fixed inset-y-0 right-0 z-[110] w-full max-w-md bg-white shadow-2xl border-l border-gray-200 flex flex-col"
        aria-label="元数据同步日志"
      >
        <div class="px-5 py-4 border-b border-gray-100 flex items-start justify-between">
          <div>
            <h3 class="text-base font-bold text-gray-900">同步日志</h3>
            <p class="mt-1 text-xs text-gray-500 truncate max-w-[280px]">{{ syncLogDataset?.display_name || syncLogDataset?.name }}</p>
          </div>
          <button class="text-gray-400 hover:text-gray-700 text-xl leading-none" aria-label="关闭同步日志" @click="closeSyncLog">&times;</button>
        </div>
        <div class="px-5 py-4 border-b border-gray-100 space-y-3">
          <div class="flex items-center justify-between text-xs">
            <span class="text-gray-500">状态</span>
            <span :class="syncLogStatus === 'completed' ? 'text-emerald-600' : syncLogStatus === 'failed' ? 'text-red-600' : syncLogStatus === 'disconnected' ? 'text-amber-600' : 'text-blue-600'" class="font-semibold">
              {{ syncLogStatus === 'completed' ? '已完成' : syncLogStatus === 'failed' ? '失败' : syncLogStatus === 'disconnected' ? '连接中断' : '同步中' }}
            </span>
          </div>
          <div class="flex items-center justify-between text-xs">
            <span class="text-gray-500">当前阶段</span>
            <span class="text-gray-800 font-medium">{{ syncLogStage }}</span>
          </div>
          <div v-if="syncLogTotalDocuments !== null" class="flex items-center justify-between text-xs">
            <span class="text-gray-500">文档进度</span>
            <span class="text-gray-800 font-semibold">已完成 {{ syncLogCompletedDocuments ?? 0 }} / {{ syncLogTotalDocuments }} 个文档</span>
          </div>
          <div class="h-2 rounded-full bg-gray-100 overflow-hidden">
            <div class="h-full bg-blue-500 transition-all duration-300" :style="{ width: `${syncLogProgress ?? 0}%` }"></div>
          </div>
          <div class="flex justify-between text-[11px] text-gray-400 gap-3">
            <span class="truncate">{{ syncLogMessage }}</span>
            <span class="shrink-0">{{ syncLogElapsedMs }} ms</span>
          </div>
        </div>
        <div class="flex-1 overflow-y-auto px-5 py-4 space-y-3">
          <div v-for="(entry, index) in syncLogEntries" :key="`${entry.stage}-${index}`" class="relative pl-4 border-l-2" :class="entry.event === 'failed' ? 'border-red-300' : entry.event === 'completed' ? 'border-emerald-300' : 'border-blue-200'">
            <div class="text-xs font-medium text-gray-800">{{ entry.message }}</div>
            <div class="mt-1 text-[11px] text-gray-400 flex justify-between gap-2">
              <span>{{ entry.stage }}</span><span>{{ entry.elapsed_ms ?? 0 }} ms</span>
            </div>
            <div v-if="entry.error_detail" class="mt-1 text-[11px] text-red-600 break-words">{{ entry.error_detail }}</div>
          </div>
          <p v-if="syncLogEntries.length === 0" class="text-xs text-gray-400">正在等待同步日志...</p>
        </div>
        <div class="px-5 py-3 border-t border-gray-100 text-[11px] text-gray-400">关闭窗口不会取消后台同步任务</div>
      </aside>
    </Transition>

    <!-- Schema 漂移异常待处理抽屉（支持单库模式与全量大盘模式） -->
    <MetadataDriftAlertsDrawer
      :show="showDriftDrawer"
      :visible="showDriftDrawer"
      :is-global="isGlobalDriftDrawer"
      :datasets="datasets"
      :dataset="driftAlertsDataset"
      @close="showDriftDrawer = false"
      @resolved="handleDriftResolved"
    />

    <!-- 全量元数据定时一致性巡检配置与监控弹窗（仅 Admin 可用） -->
    <MetadataCronInspectionModal
      v-if="isAdmin"
      :show="showCronInspectionModal"
      @close="showCronInspectionModal = false"
      @updated="fetchDatasets"
    />
  </div>
</template>

<style scoped>
.sync-log-drawer-enter-active,
.sync-log-drawer-leave-active { transition: transform 0.2s ease, opacity 0.2s ease; }
.sync-log-drawer-enter-from,
.sync-log-drawer-leave-to { transform: translateX(100%); opacity: 0; }
</style>
