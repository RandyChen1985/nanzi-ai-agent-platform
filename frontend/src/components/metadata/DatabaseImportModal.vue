<script setup lang="ts">
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { metadataApi, type DbConnectionConfig } from '../../api/metadata'
import { useToast } from '../../composables/useToast'

const props = withDefaults(defineProps<{
  show: boolean
  /** 当前数据集内已存在的物理表名，用于禁用重复导入 */
  importedTableNames?: string[]
  /** 追加导入时锁定为数据集已绑定的数据源 name，跳过选数据源步骤 */
  lockedDataSourceName?: string
}>(), {
  importedTableNames: () => [],
  lockedDataSourceName: '',
})

const emit = defineEmits(['close', 'confirm', 'confirm-profile'])

const { showToast } = useToast()
const router = useRouter()

// ─── 步骤控制 ───────────────────────────────────────────────────────────────
const step = ref(1) // 1: Connection, 2: Select Tables
const loading = ref(false)
const testing = ref(false)
const testPassed = ref(false)
const connError = ref('')
const initializingLocked = ref(false)

const isDataSourceLocked = computed(() => !!props.lockedDataSourceName?.trim())

const selectedConfigName = computed(() => {
  if (!selectedConfigId.value) return ''
  return savedConfigs.value.find((c) => c.id === selectedConfigId.value)?.name || ''
})

const step1CanContinue = computed(() => {
  return !!selectedConfigId.value && !!config.value.host && !!config.value.database
})

const step1ActionLabel = computed(() => {
  if (!step1CanContinue.value) return '请选择数据源'
  if (testing.value) return '正在验证连接...'
  if (loading.value) return '正在加载表列表...'
  if (testPassed.value) return '下一步 (浏览表)'
  return '测试并继续'
})

const step1ActionDisabled = computed(() => {
  return !step1CanContinue.value || testing.value || loading.value
})

const lockedConfig = computed(() => {
  const lockedName = props.lockedDataSourceName?.trim()
  if (!lockedName) return null
  return savedConfigs.value.find((c) => c.name === lockedName) || null
})

// ─── 当前导入连接（由已保存数据源填充） ─────────────────────────────────────────────
const config = ref({
  type: 'mysql',
  host: '',
  port: 3306,
  user: '',
  password: '',
  database: ''
})

// ─── 数据源配置（来自服务器） ─────────────────────────────────────────────────
const savedConfigs = ref<DbConnectionConfig[]>([])
const loadingConfigs = ref(false)
const selectedConfigId = ref<number | null>(null)

onMounted(async () => {
  await fetchSavedConfigs()
})

watch(() => props.show, async (show) => {
  if (!show) return
  await fetchSavedConfigs()
  if (isDataSourceLocked.value) {
    await enterLockedDataSourceFlow()
  }
})

const fetchSavedConfigs = async () => {
  loadingConfigs.value = true
  try {
    const res = await metadataApi.listDbConnectionConfigs()
    savedConfigs.value = res.data.data || []
    if (!isDataSourceLocked.value && !selectedConfigId.value && savedConfigs.value.length > 0) {
      applyDataSource(savedConfigs.value[0]!)
    }
  } catch {
    // 加载失败不影响主流程
  } finally {
    loadingConfigs.value = false
  }
}

// ─── 数据库类型列表 ───────────────────────────────────────────────────────────
const dbTypes = [
  { id: 'mysql', name: 'MySQL', icon: '🐬', defaultPort: 3306, disabled: false },
  { id: 'clickhouse', name: 'ClickHouse', icon: '🧊', defaultPort: 9000, disabled: false },
  { id: 'oracle', name: 'Oracle', icon: '🔴', defaultPort: 1521, disabled: false },
  { id: 'sqlserver', name: 'SQL Server', icon: '🟦', defaultPort: 1433, disabled: false },
]

const dbTypeIcon = (type: string) => dbTypes.find((item) => item.id === type)?.icon || '🗄️'

// ─── 从数据源填充导入连接 ────────────────────────────────────────────────────────
const applyDataSource = (c: DbConnectionConfig) => {
  selectedConfigId.value = c.id
  config.value = {
    type: c.db_type,
    host: c.host,
    port: c.port,
    user: c.db_user,
    password: c.password,
    database: c.database_name,
  }
  testPassed.value = false
  connError.value = ''
}

const enterLockedDataSourceFlow = async () => {
  const lockedName = props.lockedDataSourceName?.trim()
  if (!lockedName) return

  initializingLocked.value = true
  connError.value = ''
  step.value = 1

  const matched = savedConfigs.value.find((c) => c.name === lockedName)
  if (!matched) {
    connError.value = `数据集绑定的数据源「${lockedName}」在数据源管理中不存在，请先创建同名配置或修改数据集的数据源 ID`
    initializingLocked.value = false
    return
  }

  applyDataSource(matched)
  testPassed.value = true
  try {
    await handleNext()
  } catch (e: any) {
    connError.value = e.response?.data?.detail || e.message || '加载表列表失败'
    showToast('加载表列表失败', 'error')
  } finally {
    initializingLocked.value = false
  }
}

// ─── 测试连接 ─────────────────────────────────────────────────────────────────
const handleTestConnection = async () => {
  testing.value = true
  connError.value = ''
  try {
    await metadataApi.testDbConnection(config.value)
    testPassed.value = true
    showToast('连接测试成功', 'success')
  } catch (e: any) {
    connError.value = e.response?.data?.detail || e.message || '连接测试失败'
    showToast('连接失败', 'error')
  } finally {
    testing.value = false
  }
}

const handleStep1Continue = async () => {
  if (!step1CanContinue.value) return
  if (!testPassed.value) {
    await handleTestConnection()
    if (!testPassed.value) return
  }
  await handleNext()
}

// ─── 加载表列表（Step 2） ──────────────────────────────────────────────────────
const activeTab = ref<'system' | 'profile'>('system')
const tables = ref<{name: string, comment: string, type?: 'table' | 'view'}[]>([])
const tableProfiles = ref<any[]>([])
const profileStats = ref<any>(null)
const loadingProfiles = ref(false)
const profilesPage = ref(1)
const profilesPageSize = ref(40)
const profilesTotal = ref(0)
const profilesPages = ref(0)
const profilesSort = ref<'default' | 'relevance' | 'confidence_desc' | 'confidence_asc' | 'name_asc' | 'name_desc' | 'term_asc'>('default')
const searchQuery = ref('')
const importFilter = ref<'all' | 'unimported'>('unimported')
const selectedTables = ref<string[]>([])
let profileSearchDebounce: ReturnType<typeof setTimeout> | null = null

const profileDetailsCache = ref<Record<string, any>>({})

const profilePreviewTable = ref<string | null>(null)
const profilePreviewDetail = ref<any>(null)
const profilePreviewLoading = ref(false)
const profileExpandedDataTable = ref<string | null>(null)
const profileDataPreviewLoading = ref(false)
const profileDataPreviewError = ref('')
const profileDataPreviewData = ref<{ columns: { name: string }[]; rows: any[][]; total_count?: number | null } | null>(null)

const profileRelatedTables = ref<{
  table_name: string
  ai_term?: string
  confidence: number
  reason?: string
  join_hint?: string
}[]>([])
const profileRelatedLoading = ref(false)
const profileRelatedMessage = ref<string | null>(null)

const profileListRef = ref<HTMLElement | null>(null)
const profileRefreshing = ref(false)

const profilePreviewItem = computed(() =>
  filteredTableProfiles.value.find((i) => i.table_name === profilePreviewTable.value)
)

const profileRowSerial = (idx: number) => (profilesPage.value - 1) * profilesPageSize.value + idx + 1
const profilePreviewRowSerial = (idx: number) => idx + 1

const scrollProfileListToTop = () => {
  nextTick(() => {
    if (profileListRef.value) profileListRef.value.scrollTop = 0
  })
}

const colName = (col: string | { name: string }) => (typeof col === 'string' ? col : col.name)

// PostgreSQL 内部保留 schema.table 用于查询，界面只展示实际表名。
const displayTableName = (tableName: string) => {
  const value = String(tableName || '')
  const dotIndex = value.lastIndexOf('.')
  return dotIndex >= 0 ? value.slice(dotIndex + 1) : value
}

const confidenceClass = (score?: number) => {
  if (score == null) return 'bg-gray-100 text-gray-500'
  if (score >= 80) return 'bg-emerald-100 text-emerald-700'
  if (score >= 60) return 'bg-amber-100 text-amber-700'
  return 'bg-red-100 text-red-700'
}

const quoteTableName = (tableName: string) => {
  const dbType = lockedConfig.value?.db_type || config.value.type || 'mysql'
  if (dbType === 'clickhouse') return tableName
  if (dbType === 'oracle' || dbType === 'sqlserver' || dbType === 'mssql') {
    return `"${tableName.replace(/"/g, '""')}"`
  }
  return `\`${tableName.replace(/`/g, '``')}\``
}

const loadProfileRelatedTables = async (tableName: string) => {
  if (!selectedConfigId.value) return
  profileRelatedLoading.value = true
  profileRelatedTables.value = []
  profileRelatedMessage.value = null
  try {
    const res = await metadataApi.getDbTableProfileRelated(selectedConfigId.value, tableName)
    profileRelatedTables.value = res.data?.items || []
    profileRelatedMessage.value = res.data?.message || null
  } catch {
    profileRelatedTables.value = []
    profileRelatedMessage.value = '加载关联表推荐失败'
  } finally {
    profileRelatedLoading.value = false
  }
}

const loadProfilePreviewDetail = async (tableName: string) => {
  if (!selectedConfigId.value) return
  if (profileDetailsCache.value[tableName]) {
    profilePreviewDetail.value = profileDetailsCache.value[tableName]
    return
  }
  profilePreviewLoading.value = true
  profilePreviewDetail.value = null
  try {
    const res = await metadataApi.getDbTableProfileDetail(selectedConfigId.value, tableName)
    profileDetailsCache.value[tableName] = res.data
    profilePreviewDetail.value = res.data
  } catch {
    profilePreviewDetail.value = null
  } finally {
    profilePreviewLoading.value = false
  }
}

const onProfileRowClick = (profile: any) => {
  if (profile.status !== 2) return
  profilePreviewTable.value = profile.table_name
  loadProfilePreviewDetail(profile.table_name)
  loadProfileRelatedTables(profile.table_name)
}

const focusProfileRelatedTable = (tableName: string) => {
  const profile = filteredTableProfiles.value.find((i) => i.table_name === tableName)
  if (profile) {
    onProfileRowClick(profile)
    return
  }
  profilePreviewTable.value = tableName
  loadProfilePreviewDetail(tableName)
  loadProfileRelatedTables(tableName)
}

const isRelatedTableSelected = (tableName: string) => selectedTables.value.includes(tableName)

const addRelatedToSelection = (tableName: string) => {
  if (isTableImported(tableName) || isRelatedTableSelected(tableName)) return
  selectedTables.value.push(tableName)
  showToast(`已加入已选：${tableName}`, 'success')
}

const addAllRelatedToSelection = () => {
  const toAdd = profileRelatedTables.value
    .map((r) => r.table_name)
    .filter((t) => !isTableImported(t) && !isRelatedTableSelected(t))
  if (!toAdd.length) {
    showToast('关联表均已在已选列表中', 'info')
    return
  }
  selectedTables.value = [...selectedTables.value, ...toAdd]
  showToast(`已加入 ${toAdd.length} 张关联表`, 'success')
}

const toggleProfileDataPreview = async (tableName: string) => {
  if (profileExpandedDataTable.value === tableName) {
    profileExpandedDataTable.value = null
    profileDataPreviewData.value = null
    profileDataPreviewError.value = ''
    return
  }
  if (!selectedConfigId.value) return
  profileExpandedDataTable.value = tableName
  profileDataPreviewLoading.value = true
  profileDataPreviewError.value = ''
  profileDataPreviewData.value = null
  try {
    const res = await metadataApi.debugDbConnectionSql(
      selectedConfigId.value,
      `SELECT * FROM ${quoteTableName(tableName)}`,
      10,
      true
    )
    if (res.data?.code === 200) {
      profileDataPreviewData.value = res.data.data
    } else {
      profileDataPreviewError.value = res.data?.message || '预览失败'
    }
  } catch (e: any) {
    profileDataPreviewError.value = e.response?.data?.detail || e.message || '预览失败'
  } finally {
    profileDataPreviewLoading.value = false
  }
}

const syncProfilePreviewSelection = () => {
  const list = filteredTableProfiles.value
  if (profileExpandedDataTable.value && !list.some((i) => i.table_name === profileExpandedDataTable.value)) {
    profileExpandedDataTable.value = null
    profileDataPreviewData.value = null
    profileDataPreviewError.value = ''
  }
  if (profilePreviewTable.value && !list.some((i) => i.table_name === profilePreviewTable.value)) {
    profilePreviewTable.value = list[0]?.table_name || null
    profilePreviewDetail.value = null
    if (profilePreviewTable.value) {
      loadProfilePreviewDetail(profilePreviewTable.value)
      loadProfileRelatedTables(profilePreviewTable.value)
    }
  } else if (!profilePreviewTable.value && list.length) {
    profilePreviewTable.value = list[0].table_name
    loadProfilePreviewDetail(list[0].table_name)
    loadProfileRelatedTables(list[0].table_name)
  }
}

const profileSortParams = computed(() => {
  switch (profilesSort.value) {
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

const loadTableProfiles = async (opts?: { silent?: boolean }) => {
  if (!selectedConfigId.value) return
  const silent = opts?.silent && tableProfiles.value.length > 0
  if (silent) {
    profileRefreshing.value = true
  } else {
    loadingProfiles.value = true
  }
  try {
    const [statsRes, pageRes] = await Promise.all([
      metadataApi.getDbTableProfileStats(selectedConfigId.value),
      metadataApi.listDbTableProfiles(selectedConfigId.value, {
        page: profilesPage.value,
        page_size: profilesPageSize.value,
        q: searchQuery.value.trim() || undefined,
        tag: selectedProfileTag.value || undefined,
        status: 2,
        ...profileSortParams.value,
      }),
    ])
    profileStats.value = statsRes.data
    const data = pageRes.data || {}
    tableProfiles.value = data.items || []
    profilesTotal.value = data.total || 0
    profilesPages.value = data.pages || 0
    profilesPage.value = data.page || profilesPage.value
    syncProfilePreviewSelection()
  } catch {
    if (!silent) {
      tableProfiles.value = []
      profileStats.value = null
    }
  } finally {
    if (silent) {
      profileRefreshing.value = false
    } else {
      loadingProfiles.value = false
    }
    scrollProfileListToTop()
  }
}

const goImportProfilePage = async (page: number) => {
  if (page < 1 || (profilesPages.value > 0 && page > profilesPages.value)) return
  profilesPage.value = page
  await loadTableProfiles({ silent: true })
}

const setProfilesSort = async (sort: typeof profilesSort.value) => {
  if (profilesSort.value === sort) return
  profilesSort.value = sort
  profilesPage.value = 1
  await loadTableProfiles({ silent: true })
}

const selectedProfileTag = ref<string | null>(null)

const toggleProfileTag = async (tag: string) => {
  if (selectedProfileTag.value === tag) {
    selectedProfileTag.value = null
  } else {
    selectedProfileTag.value = tag
  }
  profilesPage.value = 1
  await loadTableProfiles({ silent: true })
}

const clearProfileTag = async () => {
  if (!selectedProfileTag.value) return
  selectedProfileTag.value = null
  profilesPage.value = 1
  await loadTableProfiles({ silent: true })
}

const availableTags = computed(() => profileStats.value?.tags || [])

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

const lastProfiledAtText = computed(() => {
  const at = profileStats.value?.last_profiled_at
  if (!at) return ''
  return `上次摸排：${formatProfileTime(at)}`
})

const filteredTableProfiles = computed(() => {
  let list = tableProfiles.value
  if (importFilter.value === 'unimported') {
    list = list.filter((t) => !isTableImported(t.table_name))
  }
  return list
})

const selectableFilteredProfiles = computed(() =>
  filteredTableProfiles.value.filter((t) => !isTableImported(t.table_name)),
)


const importedTableSet = computed(() => {
  const set = new Set<string>()
  for (const name of props.importedTableNames || []) {
    const normalized = String(name || '').trim().toLowerCase()
    if (normalized) set.add(normalized)
  }
  return set
})

/** 仅向已有数据集追加表时为 true；新建数据集导入时不应出现「已在本数据集」 */
const hasDatasetContext = computed(() => importedTableSet.value.size > 0)

const isTableImported = (tableName: string) => {
  return importedTableSet.value.has(String(tableName || '').trim().toLowerCase())
}

const importableTables = computed(() => tables.value.filter((t) => !isTableImported(t.name)))

const filteredTables = computed(() => {
  let list = tables.value
  if (importFilter.value === 'unimported') {
    list = list.filter((t) => !isTableImported(t.name))
  }
  if (!searchQuery.value) return list
  const q = searchQuery.value.toLowerCase()
  return list.filter(t =>
    t.name.toLowerCase().includes(q) ||
    (t.comment && t.comment.toLowerCase().includes(q))
  )
})

const selectableFilteredTables = computed(() =>
  filteredTables.value.filter((t) => !isTableImported(t.name)),
)

const importedCountInRemote = computed(() => {
  if (!hasDatasetContext.value) return 0
  return tables.value.filter((t) => isTableImported(t.name)).length
})

const profileSuccessTotal = computed(() => {
  const stats = profileStats.value
  if (!stats) return 0
  if (stats.success_count) return stats.success_count
  return (stats.importable_success_count ?? 0) + (stats.ignored_count ?? 0)
})

/** 摸排 Tab：当前还可勾选的画像表数（含摸排建议忽略的表，由用户自行决定） */
const profileImportableTotal = computed(() => {
  const pool = profileStats.value?.success_count ?? profilesTotal.value ?? 0
  if (!hasDatasetContext.value) return pool
  return Math.max(0, pool - importedCountInRemote.value)
})

const profileSkippedImportedCount = computed(() =>
  hasDatasetContext.value ? importedCountInRemote.value : 0
)

const profileSuggestedIgnoreCount = computed(() => profileStats.value?.ignored_count ?? 0)

const profileTabBadgeText = computed(() => {
  const importable = profileImportableTotal.value
  const total = profileSuccessTotal.value
  if (hasDatasetContext.value && total > 0 && importable !== total) {
    return `${importable}/${total}`
  }
  return String(importable || total || '')
})

watch(searchQuery, () => {
  if (activeTab.value !== 'profile' || !selectedConfigId.value || step.value !== 2) return
  if (profileSearchDebounce) clearTimeout(profileSearchDebounce)
  profileSearchDebounce = setTimeout(async () => {
    profilesPage.value = 1
    await loadTableProfiles({ silent: true })
  }, 350)
})

watch(activeTab, async (tab) => {
  if (tab === 'profile' && selectedConfigId.value && step.value === 2) {
    profilesPage.value = 1
    profilesSort.value = 'default'
    selectedProfileTag.value = null
    profilePreviewTable.value = null
    profilePreviewDetail.value = null
    profileExpandedDataTable.value = null
    await loadTableProfiles()
  }
})

watch(importFilter, () => {
  if (activeTab.value === 'profile') syncProfilePreviewSelection()
})

const handleNext = async () => {
  loading.value = true
  connError.value = ''
  try {
    const res = await metadataApi.listDbTables(config.value)
    tables.value = res.data.data
    selectedTables.value = selectedTables.value.filter((name) => !isTableImported(name))
    importFilter.value = hasDatasetContext.value && importedCountInRemote.value > 0 ? 'unimported' : 'all'

    // 预加载已摸排的表结构草稿
    selectedProfileTag.value = null
    profilePreviewTable.value = null
    profilePreviewDetail.value = null
    profileExpandedDataTable.value = null
    profilesPage.value = 1
    await loadTableProfiles()
    activeTab.value = (profileStats.value?.success_count || 0) > 0 ? 'profile' : 'system'

    step.value = 2
  } catch (e: any) {
    connError.value = e.response?.data?.detail || e.message || '获取表列表失败'
    showToast('获取表列表失败', 'error')
  } finally {
    loading.value = false
  }
}

const handleConfirm = async () => {
  if (selectedTables.value.length === 0) {
    showToast('请选择至少一个表', 'warning')
    return
  }
  loading.value = true
  try {
    if (activeTab.value === 'profile') {
      if (!selectedConfigId.value) {
        showToast('请先选择数据源', 'warning')
        return
      }
      const res = await metadataApi.importPreviewFromProfiles(
        selectedConfigId.value,
        selectedTables.value
      )
      emit('confirm-profile', {
        preview: res.data.data,
        dataSourceName: selectedConfigName.value,
      })
      showToast('已复用摸排画像，将跳过重复 AI 分析', 'success')
      handleClose()
      return
    }

    const res = await metadataApi.getDbDdl(config.value, selectedTables.value)
    emit('confirm', {
      ddl: res.data.data,
      dataSourceName: selectedConfigName.value,
    })
    handleClose()
  } catch (e: any) {
    showToast(e.response?.data?.detail || (activeTab.value === 'profile' ? '复用摸排画像失败' : '抓取 DDL 失败'), 'error')
  } finally {
    loading.value = false
  }
}

const toggleTable = (tableName: string) => {
  if (isTableImported(tableName)) return
  const idx = selectedTables.value.indexOf(tableName)
  if (idx > -1) {
    selectedTables.value.splice(idx, 1)
  } else {
    selectedTables.value.push(tableName)
  }
}

const toggleAll = () => {
  const selectable = activeTab.value === 'system'
    ? selectableFilteredTables.value.map((t) => t.name)
    : selectableFilteredProfiles.value.map((t) => t.table_name)
  if (selectable.length === 0) return
  const allSelected = selectable.every((name) => selectedTables.value.includes(name))
  if (allSelected) {
    selectedTables.value = selectedTables.value.filter((name) => !selectable.includes(name))
  } else {
    const merged = new Set([...selectedTables.value, ...selectable])
    selectedTables.value = Array.from(merged)
  }
}

const handleClose = () => {
  step.value = 1
  selectedTables.value = []
  searchQuery.value = ''
  importFilter.value = 'unimported'
  testPassed.value = false
  connError.value = ''
  initializingLocked.value = false
  profileDetailsCache.value = {}
  profilePreviewTable.value = null
  profilePreviewDetail.value = null
  profileExpandedDataTable.value = null
  profileDataPreviewData.value = null
  profileDataPreviewError.value = ''
  emit('close')
}

const goDataSourceManagement = () => {
  handleClose()
  router.push('/dashboard/data-sources')
}

// db_type 标签颜色
const dbTypeColor = (type: string) => {
  if (type === 'mysql') return 'bg-blue-100 text-blue-700'
  if (type === 'clickhouse') return 'bg-cyan-100 text-cyan-700'
  if (type === 'oracle') return 'bg-red-100 text-red-700'
  if (type === 'sqlserver' || type === 'mssql') return 'bg-indigo-100 text-indigo-700'
  return 'bg-gray-100 text-gray-600'
}
</script>

<template>
  <div v-if="show" class="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
    <div class="bg-white rounded-2xl shadow-2xl w-full max-w-6xl overflow-hidden border border-gray-100 flex flex-col max-h-[92vh] animate-fade-in-up">

      <!-- Header -->
      <div class="p-6 border-b border-gray-100 flex justify-between items-center bg-gray-50/50 shrink-0">
        <div class="flex items-center gap-3">
          <div class="w-10 h-10 rounded-xl bg-primary/10 text-primary flex items-center justify-center border border-primary/20">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"/></svg>
          </div>
          <div>
            <h2 class="text-xl font-bold text-gray-900">从数据库加载 DDL</h2>
            <p class="text-xs text-gray-500 font-medium">从已保存数据源读取表结构定义。</p>
          </div>
        </div>
        <button @click="handleClose" class="text-gray-400 hover:text-gray-600 transition-colors">
          <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
        </button>
      </div>

      <!-- Content -->
      <div class="flex-1 overflow-y-auto" :class="step === 2 ? 'p-4' : 'p-6'">

        <!-- Step 1: Data Source -->
        <div v-if="step === 1 && !isDataSourceLocked" class="space-y-4">

          <div v-if="loadingConfigs" class="py-12 text-center text-sm text-gray-400">
            加载数据源中...
          </div>

          <div v-else-if="savedConfigs.length === 0" class="py-12 px-6 text-center border border-dashed border-gray-200 rounded-2xl bg-gray-50/60">
            <div class="w-12 h-12 mx-auto mb-4 rounded-2xl bg-primary/10 text-primary flex items-center justify-center">
              <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4"/></svg>
            </div>
            <h3 class="text-base font-bold text-gray-800 mb-1">暂无数据源</h3>
            <p class="text-xs text-gray-500 mb-5">请先在数据源管理中添加连接，再回来读取数据库表结构。</p>
            <button
              @click="goDataSourceManagement"
              class="px-5 py-2 bg-primary hover:bg-primary-dark text-white rounded-xl text-sm font-bold shadow-lg shadow-primary/20 transition-all"
            >
              去添加数据源
            </button>
          </div>

          <div v-else class="space-y-4">
            <div class="flex items-start justify-between gap-4">
              <div>
                <h3 class="text-sm font-bold text-gray-800">选择数据源</h3>
                <p class="text-xs text-gray-500 mt-1">选中后将验证连接，通过后即可浏览表列表。</p>
              </div>
              <button
                @click="goDataSourceManagement"
                class="px-3 py-1.5 rounded-lg border border-gray-200 text-xs font-bold text-gray-600 hover:bg-gray-50 hover:text-primary transition-all"
              >
                管理数据源
              </button>
            </div>

            <div class="grid grid-cols-1 gap-3 max-h-80 overflow-y-auto pr-1">
              <button
                v-for="c in savedConfigs"
                :key="c.id"
                @click="applyDataSource(c)"
                class="w-full flex items-center gap-4 p-4 rounded-xl border-2 text-left transition-all"
                :class="selectedConfigId === c.id ? 'border-primary bg-primary/5 shadow-sm' : 'border-gray-100 bg-white hover:border-gray-200 hover:bg-gray-50'"
              >
                <div class="w-10 h-10 rounded-xl bg-gray-50 border border-gray-100 flex items-center justify-center text-lg shrink-0">
                  {{ dbTypeIcon(c.db_type) }}
                </div>
                <div class="flex-1 min-w-0">
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="text-sm font-bold text-gray-800 truncate">{{ c.name }}</span>
                    <span class="px-1.5 py-0.5 rounded text-[9px] font-bold uppercase" :class="dbTypeColor(c.db_type)">{{ c.db_type }}</span>
                    <span
                      v-if="selectedConfigId === c.id && testing"
                      class="px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-blue-50 text-blue-600 border border-blue-100"
                    >验证中...</span>
                    <span
                      v-else-if="selectedConfigId === c.id && testPassed"
                      class="px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-green-50 text-green-600 border border-green-100 flex items-center gap-0.5"
                    >
                      <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"/></svg>
                      已连接
                    </span>
                    <span
                      v-else-if="selectedConfigId === c.id && connError"
                      class="px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-red-50 text-red-600 border border-red-100"
                    >连接失败</span>
                  </div>
                  <p class="text-[11px] text-gray-400 truncate font-mono mt-1">{{ c.host }}:{{ c.port }} / {{ c.database_name }}</p>
                  <p class="text-[11px] text-gray-400 truncate mt-1">用户：{{ c.db_user || '-' }}</p>
                  <div v-if="c.description" class="mt-2 inline-flex max-w-full items-start gap-1.5 rounded-lg bg-amber-50 px-2 py-1 text-[11px] text-amber-800 border border-amber-100">
                    <svg class="w-3 h-3 mt-0.5 shrink-0 text-amber-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 8h10M7 12h6m-6 4h4M5 4h14a1 1 0 011 1v14l-4-3H5a1 1 0 01-1-1V5a1 1 0 011-1z"/>
                    </svg>
                    <span class="font-bold shrink-0">用途</span>
                    <span class="line-clamp-2">{{ c.description }}</span>
                  </div>
                </div>
                <div
                  class="w-5 h-5 rounded-full border flex items-center justify-center shrink-0"
                  :class="selectedConfigId === c.id ? 'border-primary bg-primary text-white' : 'border-gray-200 bg-white'"
                >
                  <svg v-if="selectedConfigId === c.id" class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"/></svg>
                </div>
              </button>
            </div>
          </div>

          <div v-if="config.type === 'clickhouse' && selectedConfigId" class="p-4 bg-blue-50/50 border border-blue-100 rounded-xl">
            <div class="flex gap-3">
              <div class="text-blue-500 mt-0.5">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
              </div>
              <div class="text-[11px] text-blue-700 leading-normal">
                <p class="font-bold mb-1">ClickHouse 连接提示：</p>
                <p>系统使用原生 TCP 协议。常用端口为 <strong class="underline decoration-blue-300">9000</strong>。如果您之前使用的是 8123 (HTTP 端口)，请尝试更改为 9000。</p>
              </div>
            </div>
          </div>

          <div v-if="connError" class="p-3 bg-red-50 border border-red-100 rounded-lg text-xs text-red-600 leading-relaxed font-mono">
            ⚠️ {{ connError }}
          </div>
        </div>

        <!-- 追加导入：锁定数据源加载中或配置缺失 -->
        <div v-else-if="step === 1 && isDataSourceLocked" class="space-y-4">
          <div v-if="initializingLocked" class="py-12 text-center text-sm text-gray-500">
            <svg class="animate-spin h-8 w-8 mx-auto mb-3 text-primary" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
            正在加载数据集数据源「{{ lockedDataSourceName }}」的表列表...
          </div>
          <div v-else-if="connError" class="py-8 px-6 text-center border border-red-100 rounded-2xl bg-red-50/60">
            <p class="text-sm text-red-600 leading-relaxed">{{ connError }}</p>
          </div>
        </div>

        <!-- Step 2: Select Tables -->
        <div v-else class="h-full flex flex-col gap-2.5 min-h-0">
          <div
            v-if="isDataSourceLocked"
            class="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-blue-50 border border-blue-100 shrink-0 text-[11px] min-w-0"
            title="追加导入仅允许从该数据源选表，不可切换其他数据源"
          >
            <span class="text-sm shrink-0 leading-none">{{ dbTypeIcon(lockedConfig?.db_type || config.type) }}</span>
            <span class="font-bold text-blue-800 shrink-0">数据源已锁定</span>
            <span class="text-blue-200 shrink-0">|</span>
            <span class="font-mono font-bold text-blue-900 shrink-0">{{ lockedDataSourceName }}</span>
            <span
              v-if="lockedConfig?.db_type"
              class="px-1 py-0.5 rounded text-[9px] font-bold uppercase shrink-0"
              :class="dbTypeColor(lockedConfig.db_type)"
            >
              {{ lockedConfig.db_type }}
            </span>
            <span
              v-if="lockedConfig"
              class="text-blue-600/70 font-mono truncate min-w-0"
            >
              {{ lockedConfig.host }}:{{ lockedConfig.port }}/{{ lockedConfig.database_name }}
            </span>
          </div>

          <!-- 双 Tab 切换 -->
          <div class="flex border-b border-gray-100 shrink-0 text-sm">
            <button
              :class="['px-4 py-2 font-bold transition-all border-b-2 flex items-center gap-1.5', activeTab === 'system' ? 'border-primary text-primary' : 'border-transparent text-gray-500 hover:text-gray-700']"
              @click="activeTab = 'system'"
            >
              <span>系统表直连 (实时)</span>
              <span v-if="tables.length > 0" class="px-1.5 py-0.5 text-[10px] bg-primary/10 text-primary rounded-full font-bold">
                {{ tables.length }}
              </span>
            </button>
            <button
              :class="['px-4 py-2 font-bold transition-all border-b-2 flex items-center gap-1.5', activeTab === 'profile' ? 'border-primary text-primary' : 'border-transparent text-gray-500 hover:text-gray-700']"
              @click="activeTab = 'profile'"
            >
              <span>🤖 智能摸排浏览</span>
              <span
                v-if="profileTabBadgeText"
                class="px-1.5 py-0.5 text-[10px] bg-primary/10 text-primary rounded-full font-bold"
                :title="profileSuccessTotal > profileImportableTotal ? `可导入 ${profileImportableTotal} / 摸排成功 ${profileSuccessTotal}` : undefined"
              >
                {{ profileTabBadgeText }}
              </span>
            </button>
          </div>

          <div
            v-if="activeTab === 'profile'"
            class="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-gray-500 px-1 shrink-0"
          >
            <template v-if="lastProfiledAtText">
              <span>🕐</span>
              <span>{{ lastProfiledAtText }}</span>
              <span class="text-gray-300">·</span>
            </template>
            <span v-if="profileSuccessTotal > 0">
              摸排成功 <strong class="text-gray-700">{{ profileSuccessTotal }}</strong> 张
            </span>
            <span v-if="profileImportableTotal > 0" class="text-gray-400">
              · 可勾选导入 <strong class="text-primary">{{ profileImportableTotal }}</strong> 张
            </span>
            <span v-if="profileSuggestedIgnoreCount > 0" class="text-amber-600">
              · 其中 {{ profileSuggestedIgnoreCount }} 张摸排建议忽略（仍可导入）
            </span>
            <span v-if="profileSkippedImportedCount > 0" class="text-amber-600">
              · 本数据集已有 {{ profileSkippedImportedCount }} 张（不可重复导入）
            </span>
            <span class="text-emerald-600 font-bold">· 导入时直接复用画像，无需重复 AI 分析</span>
          </div>

          <div class="flex items-center gap-2 bg-gray-50 p-2 rounded-xl border border-gray-200 shrink-0">
            <svg class="w-5 h-5 text-gray-400 ml-2 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
            <input v-model="searchQuery" type="search" class="flex-1 min-w-0 bg-transparent border-none focus:ring-0 text-sm py-1" placeholder="搜索表名或备注...">
            <template v-if="activeTab === 'profile'">
              <select
                :value="profilesSort"
                class="text-xs font-semibold text-gray-600 bg-white border border-gray-200 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/20 shrink-0"
                title="排序方式"
                @change="setProfilesSort(($event.target as HTMLSelectElement).value as typeof profilesSort)"
              >
                <option value="default">默认排序</option>
                <option value="relevance">相关度优先</option>
                <option value="confidence_desc">可信度 高→低</option>
                <option value="confidence_asc">可信度 低→高</option>
                <option value="name_asc">表名 A→Z</option>
                <option value="name_desc">表名 Z→A</option>
                <option value="term_asc">中文术语 A→Z</option>
              </select>
            </template>
            <template v-if="activeTab === 'system'">
              <select
                v-model="importFilter"
                class="text-xs font-semibold text-gray-600 bg-white border border-gray-200 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/20 shrink-0"
              >
                <option value="unimported">未导入</option>
                <option value="all">全部</option>
              </select>
            </template>
            <button
              @click="toggleAll"
              :disabled="activeTab === 'system' ? selectableFilteredTables.length === 0 : selectableFilteredProfiles.length === 0"
              class="text-primary text-xs font-bold px-3 py-1 hover:bg-white rounded-lg transition-colors border border-transparent hover:border-gray-200 disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
            >
              {{
                activeTab === 'system'
                  ? (selectableFilteredTables.length > 0 && selectableFilteredTables.every((t) => selectedTables.includes(t.name)) ? '全部取消' : '全选')
                  : (selectableFilteredProfiles.length > 0 && selectableFilteredProfiles.every((t) => selectedTables.includes(t.table_name)) ? '全部取消' : '全选')
              }}
            </button>
          </div>

          <!-- 系统表直连列表 -->
          <div
            v-if="activeTab === 'system'"
            class="flex-1 overflow-y-auto border border-gray-100 rounded-xl divide-y divide-gray-50 min-h-0"
          >
               <div
                 v-for="table in filteredTables"
                 :key="table.name"
                 @click="toggleTable(table.name)"
                 class="p-4 flex items-center gap-4 transition-colors"
                 :class="isTableImported(table.name)
                   ? 'bg-gray-50/80 cursor-not-allowed opacity-70'
                   : 'hover:bg-blue-50/30 cursor-pointer'"
               >
                  <div class="w-5 h-5 rounded border-2 flex items-center justify-center transition-all shrink-0"
                       :class="isTableImported(table.name)
                         ? 'border-gray-200 bg-gray-100'
                         : selectedTables.includes(table.name)
                           ? 'bg-primary border-primary'
                           : 'border-gray-200 bg-white'">
                     <svg v-if="!isTableImported(table.name) && selectedTables.includes(table.name)" class="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"/></svg>
                     <svg v-else-if="isTableImported(table.name)" class="w-3 h-3 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"/></svg>
                  </div>
                  <div class="flex flex-col gap-0.5 min-w-0 flex-1">
                    <div class="flex items-center gap-2 flex-wrap">
                      <span class="text-sm font-mono truncate" :class="isTableImported(table.name) ? 'text-gray-400' : 'text-gray-700'">{{ displayTableName(table.name) }}</span>
                      <span
                        v-if="isTableImported(table.name)"
                        class="px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider bg-gray-100 text-gray-500 border border-gray-200"
                      >
                        已导入
                      </span>
                      <span
                        v-else-if="table.type"
                        class="px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider"
                        :class="table.type === 'view' ? 'bg-amber-100 text-amber-700 border border-amber-200' : 'bg-blue-100 text-blue-700 border border-blue-200'"
                      >
                        {{ table.type === 'view' ? '视图' : '表' }}
                      </span>
                    </div>
                    <span v-if="table.comment" class="text-[11px] text-gray-400 truncate">{{ table.comment }}</span>
                  </div>
               </div>
               <div v-if="filteredTables.length === 0" class="p-12 text-center text-gray-400 text-sm italic">
                  {{ importFilter === 'unimported' ? '暂无可导入的新表' : '未找到匹配的表' }}
               </div>
          </div>

          <!-- 智能摸排浏览：三栏探索器布局 -->
          <div
            v-else
            class="flex flex-1 min-h-0 border border-gray-100 rounded-xl overflow-hidden bg-white"
          >
            <div v-if="loadingProfiles && tableProfiles.length === 0" class="flex-1 flex items-center justify-center text-sm text-gray-400">
              加载智能摸排数据中...
            </div>
            <div v-else-if="profilesTotal === 0 && !profileRefreshing" class="flex-1 flex items-center justify-center px-6 text-center text-gray-400 text-sm leading-relaxed">
              <div>
                <p class="font-bold text-gray-500 mb-1">暂无智能摸排分析数据</p>
                <p class="text-xs">请先前往数据源管理对该配置执行「智能摸排」</p>
              </div>
            </div>
            <template v-else>
              <!-- 左栏筛选 -->
              <div class="w-40 shrink-0 border-r bg-gray-50/80 flex flex-col overflow-y-auto custom-scrollbar">
                <div class="p-2 space-y-1">
                  <div class="px-1 text-[10px] font-bold text-gray-400 uppercase mb-1">导入范围</div>
                  <button
                    type="button"
                    class="w-full text-left px-2.5 py-1.5 rounded-lg text-xs font-semibold transition-colors"
                    :class="importFilter === 'unimported' ? 'bg-primary text-white' : 'text-gray-600 hover:bg-white'"
                    @click="importFilter = 'unimported'"
                  >仅可选</button>
                  <button
                    type="button"
                    class="w-full text-left px-2.5 py-1.5 rounded-lg text-xs font-semibold transition-colors"
                    :class="importFilter === 'all' ? 'bg-primary text-white' : 'text-gray-600 hover:bg-white'"
                    @click="importFilter = 'all'"
                  >全部可选</button>
                  <button
                    type="button"
                    class="w-full text-left px-2.5 py-1.5 rounded-lg text-xs font-semibold text-primary hover:bg-primary/10 transition-colors"
                    :disabled="selectableFilteredProfiles.length === 0"
                    @click="toggleAll"
                  >
                    {{ selectableFilteredProfiles.length > 0 && selectableFilteredProfiles.every((t) => selectedTables.includes(t.table_name)) ? '取消全选' : '全选当前结果' }}
                  </button>
                </div>
                <div v-if="availableTags.length" class="border-t p-2 flex-1 min-h-0">
                  <div class="px-1 text-[10px] font-bold text-gray-400 uppercase mb-1">标签</div>
                  <button
                    type="button"
                    class="w-full text-left px-2 py-1 rounded-md text-[11px] mb-0.5 transition-colors"
                    :class="!selectedProfileTag ? 'bg-primary/10 text-primary font-bold' : 'text-gray-600 hover:bg-white'"
                    @click="clearProfileTag"
                  >全部 ({{ profileSuccessTotal }})</button>
                  <button
                    v-for="tag in availableTags.slice(0, 20)"
                    :key="tag.name"
                    type="button"
                    class="w-full text-left px-2 py-1 rounded-md text-[11px] truncate transition-colors"
                    :class="selectedProfileTag === tag.name ? 'bg-primary/10 text-primary font-bold' : 'text-gray-600 hover:bg-white'"
                    @click="toggleProfileTag(tag.name)"
                  >{{ tag.name }} ({{ tag.count }})</button>
                </div>
              </div>

              <!-- 中栏列表 -->
              <div class="flex-1 flex flex-col min-w-0">
                <div class="px-3 py-2 border-b flex items-center justify-between text-[11px] text-gray-500 shrink-0">
                  <span>共 {{ profilesTotal }} 张 · 当前 {{ filteredTableProfiles.length }} 张</span>
                  <div v-if="profilesPages > 1" class="flex items-center gap-1.5">
                    <button type="button" class="px-2 py-0.5 rounded border border-gray-200 hover:bg-gray-50 disabled:opacity-40" :disabled="profilesPage <= 1 || loadingProfiles" @click="goImportProfilePage(profilesPage - 1)">上一页</button>
                    <span>{{ profilesPage }}/{{ profilesPages }}</span>
                    <button type="button" class="px-2 py-0.5 rounded border border-gray-200 hover:bg-gray-50 disabled:opacity-40" :disabled="profilesPage >= profilesPages || loadingProfiles" @click="goImportProfilePage(profilesPage + 1)">下一页</button>
                  </div>
                </div>
                <div ref="profileListRef" class="flex-1 overflow-y-auto custom-scrollbar" :class="profileRefreshing ? 'opacity-60 pointer-events-none' : ''">
                  <div v-if="filteredTableProfiles.length === 0" class="py-16 text-center text-gray-400 text-xs px-4">
                    {{ importFilter === 'unimported' ? '当前页暂无可导入的新表' : '未找到匹配的表' }}
                  </div>
                  <div
                    v-for="(profile, idx) in filteredTableProfiles"
                    :key="profile.table_name"
                    class="border-b"
                    :class="[
                      profilePreviewTable === profile.table_name ? 'bg-primary/5' : '',
                      isTableImported(profile.table_name) ? 'opacity-70' : '',
                    ]"
                  >
                    <div
                      class="px-3 py-2.5 flex items-start gap-2.5 group transition-colors"
                      :class="profile.status === 2 ? 'cursor-pointer' : 'cursor-default'"
                      @click="onProfileRowClick(profile)"
                    >
                      <span
                        class="shrink-0 w-7 text-right text-[10px] font-semibold text-gray-400 tabular-nums pt-1"
                        :title="`第 ${profileRowSerial(idx)} 条`"
                      >{{ profileRowSerial(idx) }}</span>
                      <div
                        class="w-5 h-5 rounded border-2 flex items-center justify-center transition-all shrink-0 mt-0.5"
                        :class="isTableImported(profile.table_name)
                          ? 'border-gray-200 bg-gray-100 cursor-not-allowed'
                          : selectedTables.includes(profile.table_name)
                            ? 'bg-primary border-primary cursor-pointer'
                            : 'border-gray-200 bg-white cursor-pointer hover:border-primary/40'"
                        @click.stop="toggleTable(profile.table_name)"
                      >
                        <svg v-if="!isTableImported(profile.table_name) && selectedTables.includes(profile.table_name)" class="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"/></svg>
                        <svg v-else-if="isTableImported(profile.table_name)" class="w-3 h-3 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"/></svg>
                      </div>
                      <div class="min-w-0 flex-1">
                        <div class="flex items-start justify-between gap-2">
                          <div class="min-w-0 flex-1 flex items-center gap-1.5 flex-wrap">
                            <span class="text-sm font-mono font-bold text-gray-800 break-all">{{ displayTableName(profile.table_name) }}</span>
                            <span v-if="isTableImported(profile.table_name)" class="text-[9px] px-1 py-0.5 rounded font-bold bg-gray-100 text-gray-500">已导入</span>
                            <span v-else class="text-[9px] px-1 py-0.5 rounded font-black shrink-0" :class="profile.table_type === 'view' ? 'bg-amber-100 text-amber-700' : 'bg-blue-100 text-blue-700'">{{ profile.table_type === 'view' ? 'VIEW' : 'TABLE' }}</span>
                            <span v-if="profile.confidence_score != null" class="text-[9px] px-1 py-0.5 rounded font-bold shrink-0" :class="confidenceClass(profile.confidence_score)">{{ profile.confidence_score }}分</span>
                            <span v-if="profile.is_ignored === 1" class="text-[9px] text-orange-600 font-bold">建议忽略</span>
                          </div>
                          <button
                            v-if="profile.status === 2"
                            type="button"
                            class="shrink-0 flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-semibold border transition-colors"
                            :class="profileExpandedDataTable === profile.table_name
                              ? 'bg-primary text-white border-primary'
                              : 'bg-white text-gray-500 border-gray-200 opacity-0 group-hover:opacity-100 hover:border-primary/40 hover:text-primary'"
                            @click.stop="toggleProfileDataPreview(profile.table_name)"
                          >
                            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/></svg>
                            预览数据
                          </button>
                        </div>
                        <div v-if="profile.ai_term" class="text-xs text-primary font-semibold mt-0.5 break-words">{{ profile.ai_term }}</div>
                        <div v-if="profile.ai_description" class="text-[11px] text-gray-500 mt-0.5 line-clamp-2 leading-relaxed">{{ profile.ai_description }}</div>
                      </div>
                    </div>
                    <div v-if="profileExpandedDataTable === profile.table_name" class="px-3 pb-2.5 ml-8 mr-2" @click.stop>
                      <div v-if="profileDataPreviewLoading" class="py-3 text-center text-gray-400 text-xs">
                        <span class="inline-block w-4 h-4 border-2 border-primary/20 border-t-primary rounded-full animate-spin mr-2 align-middle" />
                        正在加载前 10 条数据...
                      </div>
                      <div v-else-if="profileDataPreviewError" class="py-2 px-3 bg-red-50 border border-red-100 rounded-lg text-red-600 text-[11px]">{{ profileDataPreviewError }}</div>
                      <div v-else-if="profileDataPreviewData" class="bg-white border border-gray-200 rounded-lg overflow-hidden shadow-sm">
                        <div class="px-3 py-1.5 border-b bg-gray-50 flex items-center justify-between">
                          <span class="text-[10px] font-bold text-gray-400">数据预览 (最多 10 行)</span>
                          <span class="text-[10px] text-gray-400">
                            <template v-if="profileDataPreviewData.total_count != null">
                              {{ profileDataPreviewData.rows?.length || 0 }}/{{ profileDataPreviewData.total_count }} 条
                            </template>
                            <template v-else>{{ profileDataPreviewData.rows?.length || 0 }} 行</template>
                          </span>
                        </div>
                        <div class="overflow-x-auto custom-scrollbar max-h-40">
                          <table class="min-w-full divide-y divide-gray-100">
                            <thead class="bg-gray-50 sticky top-0">
                              <tr>
                                <th class="px-2 py-1.5 text-left text-[9px] font-bold text-gray-400 uppercase whitespace-nowrap w-8">#</th>
                                <th v-for="col in profileDataPreviewData.columns" :key="colName(col)" class="px-2 py-1.5 text-left text-[9px] font-bold text-gray-400 uppercase whitespace-nowrap">{{ colName(col) }}</th>
                              </tr>
                            </thead>
                            <tbody class="divide-y divide-gray-50">
                              <tr v-for="(row, rIdx) in profileDataPreviewData.rows" :key="rIdx">
                                <td class="px-2 py-1 text-[10px] text-gray-400 tabular-nums font-semibold">{{ profilePreviewRowSerial(rIdx) }}</td>
                                <td v-for="(cell, cIdx) in row" :key="cIdx" class="px-2 py-1 text-[10px] text-gray-600 whitespace-nowrap max-w-[140px] truncate" :title="cell === null || cell === undefined ? 'NULL' : String(cell)">{{ cell === null || cell === undefined ? 'NULL' : cell }}</td>
                              </tr>
                            </tbody>
                          </table>
                        </div>
                        <div v-if="!profileDataPreviewData.rows?.length" class="py-4 text-center text-gray-400 text-[11px] italic">表中暂无数据</div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <!-- 右栏预览 -->
              <div class="w-64 shrink-0 border-l bg-gray-50/50 flex flex-col overflow-hidden">
                <div class="px-3 py-2 border-b text-[11px] font-bold text-gray-500 shrink-0">表预览</div>
                <div v-if="!profilePreviewTable" class="flex-1 flex items-center justify-center text-gray-400 text-xs px-3 text-center">点击表查看字段画像</div>
                <div v-else class="flex-1 overflow-y-auto custom-scrollbar p-3 space-y-2">
                  <div class="font-mono text-sm font-bold text-gray-800 break-all">{{ displayTableName(profilePreviewTable) }}</div>
                  <template v-if="profilePreviewItem">
                    <div v-if="profilePreviewItem.ai_term" class="text-xs text-primary font-semibold">{{ profilePreviewItem.ai_term }}</div>
                    <div v-if="profilePreviewItem.ai_description" class="text-[11px] text-gray-600 leading-relaxed">{{ profilePreviewItem.ai_description }}</div>
                    <div v-if="profilePreviewItem.confidence_score != null" class="text-[10px] flex items-center gap-1">
                      <span class="text-gray-400">可信度</span>
                      <span class="px-1 py-0.5 rounded font-bold" :class="confidenceClass(profilePreviewItem.confidence_score)">{{ profilePreviewItem.confidence_score }} 分</span>
                    </div>
                    <div v-if="profilePreviewItem.ai_tags?.length" class="flex flex-wrap gap-1">
                      <span v-for="tg in profilePreviewItem.ai_tags" :key="tg" class="text-[9px] px-1.5 py-0.5 bg-white border rounded-full text-gray-600">{{ tg }}</span>
                    </div>
                  </template>

                  <div class="mt-2 border border-primary/20 rounded-lg bg-primary/5 overflow-hidden">
                    <div class="px-2.5 py-1.5 border-b border-primary/10 flex items-center justify-between gap-2">
                      <span class="text-[10px] font-bold text-primary">可能关联的表</span>
                      <button
                        v-if="profileRelatedTables.length"
                        type="button"
                        class="text-[9px] font-bold text-primary hover:text-primary/80 px-1.5 py-0.5 rounded hover:bg-primary/10"
                        @click="addAllRelatedToSelection"
                      >全部加入</button>
                    </div>
                    <div v-if="profileRelatedLoading" class="px-2.5 py-3 text-[10px] text-gray-400 italic">分析关联中...</div>
                    <div v-else-if="!profileRelatedTables.length" class="px-2.5 py-2 text-[10px] text-gray-500 leading-relaxed">
                      {{ profileRelatedMessage || '暂无推荐，请确认该表已完成摸排' }}
                    </div>
                    <ul v-else class="max-h-32 overflow-y-auto custom-scrollbar divide-y divide-primary/10">
                      <li
                        v-for="rel in profileRelatedTables"
                        :key="rel.table_name"
                        class="px-2.5 py-2 hover:bg-white/70 transition-colors"
                      >
                        <div class="flex items-start justify-between gap-2">
                          <button
                            type="button"
                            class="min-w-0 flex-1 text-left"
                            @click="focusProfileRelatedTable(rel.table_name)"
                          >
                            <div class="font-mono text-[10px] font-bold text-gray-800 truncate" :title="rel.table_name">{{ displayTableName(rel.table_name) }}</div>
                            <div v-if="rel.ai_term" class="text-[9px] text-primary truncate">{{ rel.ai_term }}</div>
                            <div class="text-[9px] text-gray-500 mt-0.5 line-clamp-2" :title="rel.reason">{{ rel.reason }}</div>
                          </button>
                          <div class="shrink-0 flex flex-col items-end gap-1">
                            <span class="text-[9px] font-bold text-emerald-700 bg-emerald-50 px-1 py-0.5 rounded">{{ Math.round(rel.confidence * 100) }}%</span>
                            <button
                              type="button"
                              class="text-[9px] font-bold px-1.5 py-0.5 rounded border transition-colors"
                              :class="isTableImported(rel.table_name)
                                ? 'text-gray-300 border-gray-100 cursor-not-allowed'
                                : isRelatedTableSelected(rel.table_name)
                                  ? 'text-gray-400 border-gray-200 cursor-default'
                                  : 'text-primary border-primary/30 hover:bg-primary/10'"
                              :disabled="isTableImported(rel.table_name) || isRelatedTableSelected(rel.table_name)"
                              @click.stop="addRelatedToSelection(rel.table_name)"
                            >{{ isTableImported(rel.table_name) ? '已导入' : (isRelatedTableSelected(rel.table_name) ? '已选' : '加入') }}</button>
                          </div>
                        </div>
                        <div v-if="rel.join_hint" class="mt-1 text-[8px] font-mono text-gray-400 truncate" :title="rel.join_hint">{{ rel.join_hint }}</div>
                      </li>
                    </ul>
                  </div>

                  <div v-if="profilePreviewLoading" class="text-[11px] text-gray-400 italic py-4 text-center">加载字段画像...</div>
                  <div v-else-if="profilePreviewDetail?.columns_profile?.length" class="border border-gray-100 rounded-lg overflow-hidden bg-white">
                    <div class="px-2 py-1.5 bg-gray-50 border-b text-[10px] font-bold text-gray-400">字段 ({{ profilePreviewDetail.columns_profile.length }})</div>
                    <div class="max-h-[38vh] overflow-y-auto custom-scrollbar">
                      <table class="w-full text-[10px]">
                        <thead class="sticky top-0 bg-gray-50 text-gray-400">
                          <tr>
                            <th class="px-2 py-1 font-bold border-b">字段</th>
                            <th class="px-2 py-1 font-bold border-b">术语</th>
                          </tr>
                        </thead>
                        <tbody class="divide-y divide-gray-50">
                          <tr v-for="col in profilePreviewDetail.columns_profile" :key="col.name || col.column_name">
                            <td class="px-2 py-1 font-mono text-gray-700 align-top">{{ col.name || col.column_name }}</td>
                            <td class="px-2 py-1 text-primary align-top">{{ col.term || '-' }}</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              </div>
            </template>
          </div>

          <div class="text-xs text-gray-400 font-medium px-2 space-y-0.5 shrink-0">
            <div>
              已选择 <span class="text-primary font-bold">{{ selectedTables.length }}</span>
              / 可导入 {{ activeTab === 'system' ? importableTables.length : profileImportableTotal }} 个表
              <span v-if="activeTab === 'profile' && profilesPages > 1" class="text-gray-300">（当前页浏览）</span>
            </div>
            <div v-if="activeTab === 'profile' && profileSkippedImportedCount > 0" class="text-[11px] text-amber-600">
              全选仅包含可新导入的表，{{ profileSkippedImportedCount }} 张已在本数据集不可重复勾选
            </div>
            <div v-else-if="activeTab === 'system' && importedCountInRemote > 0" class="text-[11px] text-gray-400">
              当前数据集已有 {{ importedCountInRemote }} 张表，已自动禁用重复导入
            </div>
          </div>
        </div>
      </div>

      <!-- Footer -->
      <div class="px-5 py-2.5 border-t border-gray-100 flex justify-between items-center gap-3 bg-gray-50/30 shrink-0">
        <button @click="handleClose" class="px-4 py-1.5 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-100 shrink-0">取消</button>

        <!-- Step 1: 验证 → 继续 连贯操作 -->
        <div v-if="step === 1 && !isDataSourceLocked" class="flex items-center gap-2 min-w-0 flex-1 justify-end">
          <div v-if="selectedConfigId" class="hidden sm:flex items-center gap-1.5 text-xs text-gray-500 min-w-0 mr-1">
            <template v-if="testing">
              <svg class="animate-spin h-3.5 w-3.5 text-blue-500 shrink-0" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
              <span class="truncate">验证 <strong class="text-gray-700">{{ selectedConfigName }}</strong> 连接中</span>
            </template>
            <template v-else-if="testPassed">
              <svg class="w-3.5 h-3.5 text-green-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"/></svg>
              <span class="text-green-600 font-medium shrink-0">连接正常</span>
              <button
                type="button"
                @click="handleTestConnection"
                :disabled="testing"
                class="text-primary hover:underline shrink-0 disabled:opacity-50"
              >重新测试</button>
            </template>
            <template v-else-if="connError">
              <span class="text-red-500 shrink-0">连接失败，请重试</span>
            </template>
            <template v-else>
              <span class="truncate">已选 <strong class="text-gray-700">{{ selectedConfigName }}</strong>，点击继续验证</span>
            </template>
          </div>

          <button
            @click="handleStep1Continue"
            :disabled="step1ActionDisabled"
            class="px-5 py-1.5 rounded-lg text-sm font-bold shadow-sm transition-all flex items-center gap-1.5 shrink-0 disabled:opacity-50"
            :class="testPassed && !testing && !loading
              ? 'bg-primary hover:bg-primary-dark text-white'
              : 'bg-blue-600 hover:bg-blue-700 text-white'"
          >
            <svg v-if="testing || loading" class="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
            <svg v-else-if="testPassed" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6"/></svg>
            <span>{{ step1ActionLabel }}</span>
          </button>
        </div>

        <!-- Step 2 左侧信息 -->
        <div v-else-if="step === 2 && isDataSourceLocked" class="flex items-center gap-1.5 text-xs text-gray-500 min-w-0 flex-1">
          <span class="font-bold text-gray-700 shrink-0">数据集数据源</span>
          <span class="px-1.5 py-0.5 rounded bg-primary/10 text-primary font-mono text-[11px] border border-primary/20 truncate">{{ lockedDataSourceName }}</span>
        </div>
        <div v-else-if="step === 2" class="flex-1">
          <button @click="step = 1" class="text-xs font-bold text-gray-400 hover:text-gray-600 transition-all flex items-center gap-1">
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/></svg>
            更换数据源
          </button>
        </div>
        <div v-else class="flex-1"></div>

        <!-- Step 2 确认按钮 -->
        <div v-if="step === 2" class="flex gap-2 shrink-0">
          <button
            @click="handleConfirm"
            :disabled="loading || selectedTables.length === 0"
            class="px-5 py-1.5 bg-green-600 hover:bg-green-700 text-white rounded-lg text-sm font-bold shadow-sm transition-all flex items-center gap-1.5 disabled:opacity-50"
          >
            <svg v-if="loading" class="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
            <span>{{ activeTab === 'profile' ? '使用摸排画像导入' : '确认导入' }} ({{ selectedTables.length }})</span>
          </button>
        </div>
      </div>

    </div>
  </div>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar { width: 5px; height: 5px; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 6px; }
.animate-fade-in-up {
  animation: fadeInUp 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}
@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(20px) scale(0.95); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
</style>
