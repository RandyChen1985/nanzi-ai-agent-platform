<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { metadataApi, type DbConnectionConfig } from '../api/metadata'
import { useToast } from '../composables/useToast'
import { useUser } from '../composables/useUser'
import ConfirmModal from '../components/ConfirmModal.vue'
import DbTypeIcon from '../components/common/DbTypeIcon.vue'
import DbTableProfileExplorerModal from '../components/metadata/DbTableProfileExplorerModal.vue'

const { showToast } = useToast()

const { hasPermission } = useUser()
// 后端按 view / edit / delete 分码校验，这里把写操作按钮对齐隐藏，
// 否则没权限的人点下去只会收到 403，界面上看不出「能做什么」。
const canEditDataSource = computed(() => hasPermission('element:data_sources:edit'))
const canDeleteDataSource = computed(() => hasPermission('element:data_sources:delete'))

// 图标不在数据里，统一交给 DbTypeIcon（官方单色 SVG，避免 emoji 跨平台造型不一致）
const dbTypes = [
  { id: 'mysql', name: 'MySQL', defaultPort: 3306 },
  { id: 'doris', name: 'Doris', defaultPort: 9030 },
  { id: 'clickhouse', name: 'ClickHouse', defaultPort: 9000 },
  { id: 'oracle', name: 'Oracle', defaultPort: 1521 },
  { id: 'sqlserver', name: 'SQL Server', defaultPort: 1433 },
  { id: 'postgresql', name: 'PostgreSQL', defaultPort: 5432 },
]

const configs = ref<DbConnectionConfig[]>([])
const loading = ref(false)
const testing = ref(false)
const saving = ref(false)
const deletingId = ref<number | null>(null)
const testingConnectionId = ref<number | null>(null)
const editingId = ref<number | null>(null)
const deleteTarget = ref<DbConnectionConfig | null>(null)
const testPassed = ref(false)
const connError = ref('')
const keyword = ref('')
// 视图模式：卡片 / 列表。默认卡片视图，选择记到 localStorage，和元数据中心、任务中心等页面保持一致。
const viewMode = ref<'card' | 'list'>(
  (localStorage.getItem('data_sources_view_mode') as 'card' | 'list') || 'card'
)
watch(viewMode, (newMode) => {
  localStorage.setItem('data_sources_view_mode', newMode)
  closeActionMenus()
})
// 类型筛选与排序：数据源一多，光靠关键词搜不到「谁还没摸排」，需要能按类型收窄、按画像数排序。
const typeFilter = ref('all')
const sortBy = ref<'default' | 'name' | 'updated' | 'profiles'>('default')
const showSpecsModal = ref(false)
const activeSpecsTab = ref<'concept' | 'security' | 'practice'>('concept')

// SQL 在线调试状态
const debugTarget = ref<DbConnectionConfig | null>(null)
const debugSql = ref('SELECT 1')
const debugLimit = ref(100)
const debugExecuting = ref(false)
const debugError = ref('')
const debugResult = ref<{ columns: { name: string; type: string }[]; rows: any[][]; execution_time_ms?: number } | null>(null)


const form = reactive({
  name: '',
  nameSuffix: '',
  type: 'mysql',
  host: '',
  port: 3306,
  user: '',
  password: '',
  database: '',
  description: '',
})

const filteredConfigs = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  if (!q) return configs.value
  return configs.value.filter((item) =>
    [item.name, item.db_type, item.host, item.db_user, item.database_name]
      .concat(item.description || '')
      .some((value) => String(value || '').toLowerCase().includes(q))
  )
})
const dataSourcePrefix = computed(() => `${form.type}_`)
const dataSourceName = computed(() => `${dataSourcePrefix.value}${form.nameSuffix.trim()}`)

const sanitizeNameSuffix = () => {
  form.nameSuffix = form.nameSuffix.replace(/[^a-zA-Z0-9_]/g, '')
}

const handleNamePaste = (event: ClipboardEvent) => {
  event.preventDefault()
  const input = event.target as HTMLInputElement
  const pasted = event.clipboardData?.getData('text') || ''
  const cleanText = pasted.replace(/[^a-zA-Z0-9_]/g, '')
  const start = input.selectionStart ?? form.nameSuffix.length
  const end = input.selectionEnd ?? start
  form.nameSuffix = `${form.nameSuffix.slice(0, start)}${cleanText}${form.nameSuffix.slice(end)}`
}

const setDbType = (type: string) => {
  const db = dbTypes.find((item) => item.id === type)
  if (!db) return
  form.type = db.id
  form.port = db.defaultPort
  testPassed.value = false
}

const dbTypeColor = (type: string) => {
  if (type === 'mysql') return 'bg-blue-100 text-blue-700 border-blue-200'
  if (type === 'doris') return 'bg-amber-100 text-amber-700 border-amber-200'
  if (type === 'clickhouse') return 'bg-cyan-100 text-cyan-700 border-cyan-200'
  if (type === 'oracle') return 'bg-red-100 text-red-700 border-red-200'
  if (type === 'sqlserver' || type === 'mssql') return 'bg-indigo-100 text-indigo-700 border-indigo-200'
  if (type === 'postgresql' || type === 'postgres' || type === 'pg') return 'bg-purple-100 text-purple-700 border-purple-200'
  return 'bg-gray-100 text-gray-600 border-gray-200'
}

// db_type 历史值存在 mssql / postgres / pg 等别名，统一归一化后再取图标、底色与展示名。
const normalizeDbType = (type: string) => {
  const raw = String(type || '').toLowerCase()
  return raw === 'mssql' ? 'sqlserver' : raw === 'postgres' || raw === 'pg' ? 'postgresql' : raw
}
const dbTypeMeta = (type: string) => dbTypes.find((db) => db.id === normalizeDbType(type)) || null
const dbTypeLabel = (type: string) => dbTypeMeta(type)?.name || String(type || '').toUpperCase()
// 图标底色只取浅色底，避免和徽章的边框/文字色混在一起。
const DB_TYPE_TINTS: Record<string, string> = {
  mysql: 'bg-blue-50',
  doris: 'bg-amber-50',
  clickhouse: 'bg-cyan-50',
  oracle: 'bg-red-50',
  sqlserver: 'bg-indigo-50',
  postgresql: 'bg-purple-50',
}
const dbTypeTint = (type: string) => DB_TYPE_TINTS[normalizeDbType(type)] || 'bg-gray-50'

const toConnectionPayload = () => ({
  type: form.type,
  host: form.host,
  port: form.port,
  user: form.user,
  password: form.password,
  database: form.database,
})

const configToConnectionPayload = (item: DbConnectionConfig) => ({
  type: item.db_type,
  host: item.host,
  port: item.port,
  user: item.db_user,
  password: item.password,
  database: item.database_name,
})

watch(
  () => [form.type, form.host, form.port, form.user, form.password, form.database],
  () => {
    testPassed.value = false
    connError.value = ''
  }
)

const loadConfigs = async () => {
  loading.value = true
  try {
    const res = await metadataApi.listDbConnectionConfigs()
    configs.value = res.data.data || []
  } catch {
    showToast('数据源列表加载失败', 'error')
  } finally {
    loading.value = false
  }
}

const showFormModal = ref(false)

const openCreateForm = () => {
  resetForm()
  showFormModal.value = true
}

const resetForm = () => {
  editingId.value = null
  form.name = ''
  form.nameSuffix = ''
  form.type = 'mysql'
  form.host = ''
  form.port = 3306
  form.user = ''
  form.password = ''
  form.database = ''
  form.description = ''
  testPassed.value = false
  connError.value = ''
}

const editConfig = (item: DbConnectionConfig) => {
  editingId.value = item.id
  form.name = item.name
  const prefix = `${item.db_type}_`
  form.nameSuffix = item.name.startsWith(prefix) ? item.name.slice(prefix.length) : item.name
  form.type = item.db_type
  form.host = item.host
  form.port = item.port
  form.user = item.db_user
  form.password = item.password
  form.database = item.database_name
  form.description = item.description || ''
  testPassed.value = false
  connError.value = ''
  showFormModal.value = true
}

const stripCopySuffix = (name: string) => name.replace(/_copy(?:_\d+)?$/i, '')

const buildUniqueCopyName = (baseName: string) => {
  const root = stripCopySuffix(baseName) || baseName
  const existing = new Set(configs.value.map((config) => config.name))
  let candidate = `${root}_copy`
  if (!existing.has(candidate)) return candidate
  let n = 2
  while (existing.has(`${root}_copy_${n}`)) n += 1
  return `${root}_copy_${n}`
}

const copyConfig = (item: DbConnectionConfig) => {
  editingId.value = null
  form.name = buildUniqueCopyName(item.name)
  const prefix = `${item.db_type}_`
  form.nameSuffix = form.name.startsWith(prefix) ? form.name.slice(prefix.length) : form.name
  form.type = item.db_type
  form.host = item.host
  form.port = item.port
  form.user = item.db_user
  form.password = item.password
  form.database = item.database_name
  const copyNote = `（复制自 ${item.name}）`
  const description = (item.description || '').trim()
  form.description = description.includes(copyNote)
    ? description
    : `${description}${description ? ' ' : ''}${copyNote}`.trim()
  testPassed.value = false
  connError.value = ''
  showFormModal.value = true
  showToast('已复制配置到表单，请测试并保存', 'success')
}


const toConfigPayload = () => ({
  name: dataSourceName.value,
  db_type: form.type,
  host: form.host,
  port: form.port,
  db_user: form.user,
  password: form.password,
  database_name: form.database,
  description: form.description.trim(),
})

const buildSuggestedName = () => {
  const database = form.database.trim().replace(/[^a-zA-Z0-9_]+/g, '_').replace(/^_+|_+$/g, '')
  if (!database) return form.type
  return database
}

const testConnection = async () => {
  testing.value = true
  connError.value = ''
  try {
    await metadataApi.testDbConnection(toConnectionPayload())
    testPassed.value = true
    if (!form.nameSuffix.trim()) {
      form.nameSuffix = buildSuggestedName()
    }
    showToast('连接测试成功', 'success')
  } catch (e: any) {
    testPassed.value = false
    connError.value = e.response?.data?.detail || e.message || '连接测试失败'
    showToast('连接失败', 'error')
  } finally {
    testing.value = false
  }
}

const testSavedConnection = async (item: DbConnectionConfig) => {
  testingConnectionId.value = item.id
  try {
    await metadataApi.testDbConnection(configToConnectionPayload(item))
    showToast(`连接 ${item.name} 测试成功`, 'success')
  } catch (e: any) {
    showToast(e.response?.data?.detail || '连接测试失败', 'error')
  } finally {
    testingConnectionId.value = null
  }
}

const saveConfig = async () => {
  if (!form.nameSuffix.trim()) {
    showToast('请输入数据源名称', 'warning')
    return
  }
  if (!/^[a-zA-Z0-9_]+$/.test(form.nameSuffix.trim())) {
    showToast('数据源名称仅支持英文、数字与下划线以确保兼容物理匹配', 'warning')
    return
  }
  saving.value = true
  try {
    const payload = toConfigPayload()
    if (editingId.value) {
      const res = await metadataApi.updateDbConnectionConfig(editingId.value, payload)
      configs.value = configs.value.map((item) => item.id === editingId.value ? res.data.data : item)
      showToast('数据源已更新', 'success')
    } else {
      const res = await metadataApi.saveDbConnectionConfig(payload)
      configs.value.unshift(res.data.data)
      showToast('数据源已保存', 'success')
    }
    resetForm()
    showFormModal.value = false
  } catch (e: any) {
    showToast(e.response?.data?.detail || (editingId.value ? '更新失败' : '保存失败'), 'error')
  } finally {
    saving.value = false
  }
}

const requestDeleteConfig = (item: DbConnectionConfig) => {
  deleteTarget.value = item
}

const cancelDeleteConfig = () => {
  deleteTarget.value = null
}

const confirmDeleteConfig = async () => {
  if (!deleteTarget.value) return
  const item = deleteTarget.value
  deletingId.value = item.id
  try {
    await metadataApi.deleteDbConnectionConfig(item.id)
    configs.value = configs.value.filter((config) => config.id !== item.id)
    if (editingId.value === item.id) {
      resetForm()
    }
    showToast('数据源已删除', 'success')
  } catch {
    showToast('删除失败', 'error')
  } finally {
    deletingId.value = null
    deleteTarget.value = null
  }
}

const openSqlDebug = (item: DbConnectionConfig) => {
  debugTarget.value = item
  debugSql.value = item.db_type === 'oracle' ? 'SELECT 1 FROM DUAL' : item.db_type === 'sqlserver' || item.db_type === 'mssql' ? 'SELECT 1' : 'SELECT 1'
  debugLimit.value = 100
  debugError.value = ''
  debugResult.value = null
}

const closeSqlDebug = () => {
  debugTarget.value = null
  debugResult.value = null
  debugError.value = ''
}

const runSqlDebug = async () => {
  if (!debugTarget.value) return
  if (!debugSql.value.trim()) {
    showToast('请输入要调试执行的 SQL 语句', 'warning')
    return
  }

  debugExecuting.value = true
  debugError.value = ''
  debugResult.value = null

  try {
    const res = await metadataApi.debugDbConnectionSql(debugTarget.value.id, debugSql.value, debugLimit.value)
    if (res.data && res.data.code === 200) {
      debugResult.value = res.data.data
      showToast('SQL 执行完成', 'success')
    } else {
      debugError.value = res.data?.message || '执行异常'
    }
  } catch (e: any) {
    debugError.value = e.response?.data?.detail || e.message || 'SQL 执行失败'
    showToast('执行失败', 'error')
  } finally {
    debugExecuting.value = false
  }
}

const profilingTasks = ref<Record<number, { status: number; total_tables: number; processed_tables: number; current_table?: string; error_message?: string }>>({})
const pollingIntervals = reactive<Record<number, any>>({})

// ---------- 卡片 / 列表共用的派生数据 ----------
const availableDbTypes = computed(() => {
  const used = new Set(configs.value.map((item) => normalizeDbType(item.db_type)))
  return dbTypes.filter((db) => used.has(db.id))
})
const hasActiveFilter = computed(() => keyword.value.trim() !== '' || typeFilter.value !== 'all')
const displayConfigs = computed(() => {
  const list = typeFilter.value === 'all'
    ? filteredConfigs.value
    : filteredConfigs.value.filter((item) => normalizeDbType(item.db_type) === typeFilter.value)
  if (sortBy.value === 'default') return list
  const sorted = [...list]
  if (sortBy.value === 'name') {
    sorted.sort((a, b) => a.name.localeCompare(b.name, 'zh-Hans-CN'))
  } else if (sortBy.value === 'updated') {
    sorted.sort((a, b) =>
      String(b.updated_at || b.created_at || '').localeCompare(String(a.updated_at || a.created_at || ''))
    )
  } else {
    sorted.sort((a, b) =>
      (profilingTasks.value[b.id]?.total_tables || 0) - (profilingTasks.value[a.id]?.total_tables || 0)
      || a.name.localeCompare(b.name, 'zh-Hans-CN')
    )
  }
  return sorted
})

// 画像状态徽章：0 张不能再显示成「成功绿」，否则看不出「还没摸排」和「摸排完成但库里没表」。
type ProfileTone = 'empty' | 'running' | 'done' | 'failed' | 'paused'
const PROFILE_TONE_CLASS: Record<ProfileTone, string> = {
  empty: 'bg-gray-50 text-gray-500 border-gray-200',
  running: 'bg-blue-50 text-blue-700 border-blue-100',
  done: 'bg-emerald-50 text-emerald-700 border-emerald-100',
  failed: 'bg-red-50 text-red-600 border-red-100',
  paused: 'bg-amber-50 text-amber-700 border-amber-100',
}
const PROFILE_DOT_CLASS: Record<ProfileTone, string> = {
  empty: 'bg-gray-300',
  running: 'bg-blue-500 animate-pulse',
  done: 'bg-emerald-500',
  failed: 'bg-red-500',
  paused: 'bg-amber-500',
}
const profileStates = computed<Record<number, { tone: ProfileTone; text: string; title: string }>>(() => {
  const map: Record<number, { tone: ProfileTone; text: string; title: string }> = {}
  for (const item of configs.value) {
    const task = profilingTasks.value[item.id]
    const total = task?.total_tables || 0
    const processed = task?.processed_tables || 0
    if (!task) {
      map[item.id] = { tone: 'empty', text: '待摸排', title: '尚未执行表画像摸排' }
    } else if (task.status === 1) {
      map[item.id] = {
        tone: 'running',
        text: `摸排中 ${processed}/${total}`,
        title: task.current_table ? `正在分析：${task.current_table}` : '摸排进行中',
      }
    } else if (task.status === 2) {
      map[item.id] = total > 0
        ? { tone: 'done', text: `${total} 张画像`, title: `已完成 ${total} 张表/视图的画像` }
        : { tone: 'empty', text: '未发现表', title: '摸排完成，但库内未发现表或视图' }
    } else if (task.status === 3) {
      map[item.id] = { tone: 'failed', text: '分析中断', title: task.error_message || '摸排异常中断' }
    } else if (task.status === 4) {
      map[item.id] = { tone: 'paused', text: `已中断 ${processed}/${total}`, title: '摸排被中断，可继续摸排' }
    } else {
      map[item.id] = { tone: 'empty', text: '待摸排', title: '' }
    }
  }
  return map
})

const progressPercent = (item: DbConnectionConfig) => {
  const task = profilingTasks.value[item.id]
  if (!task || !task.total_tables) return 0
  return Math.min(100, Math.round(((task.processed_tables || 0) / task.total_tables) * 100))
}

const connectionText = (item: DbConnectionConfig) => `${item.host}:${item.port}/${item.database_name}`
const copyConnection = async (item: DbConnectionConfig) => {
  try {
    await navigator.clipboard.writeText(connectionText(item))
    showToast('已复制连接地址', 'success')
  } catch {
    showToast('复制失败，请手动选择文本复制', 'error')
  }
}

const loadTaskStatuses = async () => {
  for (const item of configs.value) {
    try {
      const res = await metadataApi.getDbProfilingTask(item.id)
      if (res.data) {
        profilingTasks.value[item.id] = res.data
        if (res.data.status === 1) {
          startPolling(item.id)
        }
      }
    } catch {
      // 忽略
    }
  }
}

const startPolling = (configId: number) => {
  if (pollingIntervals[configId]) return
  pollingIntervals[configId] = setInterval(async () => {
    try {
      const res = await metadataApi.getDbProfilingTask(configId)
      if (res.data) {
        profilingTasks.value[configId] = res.data
        await refreshProfilesModalIfOpen(configId)
        if (res.data.status !== 1) {
          clearInterval(pollingIntervals[configId])
          delete pollingIntervals[configId]
          if (res.data.status === 2) {
            showToast(`数据源摸排完成！`, 'success')
          } else if (res.data.status === 4) {
            showToast(`摸排已中断，已完成 ${res.data.processed_tables}/${res.data.total_tables} 张表`, 'info')
          }
        }
      } else {
        clearInterval(pollingIntervals[configId])
        delete pollingIntervals[configId]
      }
    } catch {
      clearInterval(pollingIntervals[configId])
      delete pollingIntervals[configId]
    }
  }, 2000)
}

const profileTarget = ref<DbConnectionConfig | null>(null)
const profileFullReset = ref(false)
const cancellingProfilingId = ref<number | null>(null)

const requestProfiling = (item: DbConnectionConfig, full = false) => {
  profileTarget.value = item
  profileFullReset.value = full
}

const cancelProfiling = () => {
  profileTarget.value = null
  profileFullReset.value = false
}

const confirmProfiling = async () => {
  if (!profileTarget.value) return
  const item = profileTarget.value
  const full = profileFullReset.value
  profileTarget.value = null
  profileFullReset.value = false
  await triggerProfiling(item, full)
}

const triggerProfiling = async (item: DbConnectionConfig, full = false) => {
  try {
    const res = await metadataApi.triggerDbProfiling(item.id, full)
    showToast(
      full
        ? '已提交强制全量摸排，将重新分析所有表'
        : '已提交摸排任务，将处理未完成及新增表',
      'success'
    )
    profilingTasks.value[item.id] = res.data
    startPolling(item.id)
  } catch (e: any) {
    showToast(e.response?.data?.detail || '启动摸排失败', 'error')
  }
}

const cancelRunningProfiling = async (item: DbConnectionConfig) => {
  if (cancellingProfilingId.value === item.id) return
  cancellingProfilingId.value = item.id
  try {
    const res = await metadataApi.cancelDbProfiling(item.id)
    profilingTasks.value[item.id] = res.data
    showToast('已发送中断请求，当前表处理完成后停止', 'info')
  } catch (e: any) {
    showToast(e.response?.data?.detail || '中断失败', 'error')
  } finally {
    cancellingProfilingId.value = null
  }
}

const profilingActionLabel = (item: DbConnectionConfig) => {
  const task = profilingTasks.value[item.id]
  if (!task) return '智能摸排'
  if (task.status === 1) return '摸排中...'
  if (task.status === 2) return '增量摸排'
  if (task.status === 4) return '继续摸排'
  if (task.status === 3) return '继续摸排'
  return '智能摸排'
}

/** 有已完成画像时可查看（进行中、已中断、已完成） */
const canViewTableProfiles = (item: DbConnectionConfig) => {
  const task = profilingTasks.value[item.id]
  if (!task) return false
  if (task.status === 2) return (task.total_tables ?? 0) > 0
  return (task.processed_tables ?? 0) > 0
}

const viewProfilesButtonLabel = (item: DbConnectionConfig) => {
  const task = profilingTasks.value[item.id]
  if (!task || task.status === 2) return '查看画像'
  if (task.status === 1 || task.status === 4 || task.status === 3) {
    return `查看画像 (${task.processed_tables}/${task.total_tables})`
  }
  return '查看画像'
}

const openActionMenu = ref<'profile' | 'more' | null>(null)
const openActionMenuId = ref<number | null>(null)

const closeActionMenus = () => {
  openActionMenu.value = null
  openActionMenuId.value = null
}

const toggleActionMenu = (itemId: number, menu: 'profile' | 'more') => {
  if (openActionMenuId.value === itemId && openActionMenu.value === menu) {
    closeActionMenus()
    return
  }
  openActionMenuId.value = itemId
  openActionMenu.value = menu
}

const isActionMenuOpen = (itemId: number, menu: 'profile' | 'more') =>
  openActionMenuId.value === itemId && openActionMenu.value === menu

const handleProfileMenuAction = (
  item: DbConnectionConfig,
  action: 'incremental' | 'full' | 'cancel'
) => {
  closeActionMenus()
  if (action === 'cancel') {
    cancelRunningProfiling(item)
    return
  }
  requestProfiling(item, action === 'full')
}

const handleMoreMenuAction = (item: DbConnectionConfig, action: string) => {
  closeActionMenus()
  if (action === 'debug') openSqlDebug(item)
  else if (action === 'copy') copyConfig(item)
  else if (action === 'delete') requestDeleteConfig(item)
}

const onDocumentClick = () => closeActionMenus()

// 查看摸排分析结果 Modal 状态
const showProfilesTarget = ref<DbConnectionConfig | null>(null)
const profileExplorerRef = ref<InstanceType<typeof DbTableProfileExplorerModal> | null>(null)

const openTableProfiles = (item: DbConnectionConfig) => {
  showProfilesTarget.value = item
}

const closeTableProfiles = () => {
  showProfilesTarget.value = null
}

const refreshProfilesModalIfOpen = async (configId: number) => {
  if (showProfilesTarget.value?.id !== configId) return
  await profileExplorerRef.value?.refresh({ silent: true })
}

const activeProfileTask = computed(() => {
  if (!showProfilesTarget.value) return null
  return profilingTasks.value[showProfilesTarget.value.id] || null
})

onMounted(async () => {
  document.addEventListener('click', onDocumentClick)
  await loadConfigs()
  await loadTaskStatuses()
})

onUnmounted(() => {
  document.removeEventListener('click', onDocumentClick)
  Object.values(pollingIntervals).forEach((interval) => clearInterval(interval))
})
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
      <div>
        <div class="flex items-center gap-2.5">
          <h1 class="text-2xl font-bold text-gray-900">数据源管理</h1>
          <button
            type="button"
            class="hidden sm:flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-800 dark:hover:text-gray-300 cursor-pointer"
            v-tooltip="'数据源连接与表画像摸排规范'"
            aria-label="数据源连接与表画像摸排规范"
            @click="showSpecsModal = true"
          >
            <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093M12 17h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          </button>
        </div>
        <p class="text-sm text-gray-500 mt-1">统一维护 ChatBI 元数据导入使用的数据库连接与表结构画像摸排。</p>
      </div>
      <div class="flex items-center gap-2 shrink-0">
        <button
          v-if="canEditDataSource"
          @click="openCreateForm"
          class="px-4 py-2 rounded-lg bg-primary hover:bg-primary-dark text-white text-sm font-bold shadow-lg shadow-primary/20 transition-all flex items-center gap-1.5"
        >
          <span>➕</span>
          <span>添加数据源</span>
        </button>
        <button
          @click="loadConfigs"
          class="px-4 py-2 rounded-lg border border-gray-200 bg-white text-sm font-bold text-gray-600 hover:bg-gray-50 transition-colors"
        >
          刷新列表
        </button>
      </div>
    </div>

    <!-- 数据源配置表单 Modal (Premium Design) -->
    <Teleport to="body">
      <div v-if="showFormModal" class="fixed inset-0 z-[100] overflow-y-auto animate-fade-in" aria-labelledby="modal-title" role="dialog" aria-modal="true">
        <div class="flex items-end justify-center min-h-screen pt-4 px-4 pb-20 text-center sm:block sm:p-0">
          <div @click="showFormModal = false" class="fixed inset-0 bg-gray-500 bg-opacity-75 transition-opacity" aria-hidden="true"></div>
          <span class="hidden sm:inline-block sm:align-middle sm:h-screen" aria-hidden="true">&#8203;</span>
          <div class="inline-block align-bottom bg-white rounded-2xl text-left overflow-hidden shadow-2xl transform transition-all sm:my-8 sm:align-middle sm:max-w-xl sm:w-full border border-gray-100">
            <!-- Modal 头部 -->
            <div class="bg-gray-50 px-6 py-4 border-b border-gray-100 flex items-center justify-between">
              <div>
                <h3 class="text-base font-black text-gray-900">{{ editingId ? '编辑数据源' : '添加数据源' }}</h3>
                <p class="text-xs text-gray-500 mt-0.5">{{ editingId ? '修改后需重新测试连接，通过后保存更新。' : '测试通过后保存，元数据导入时会从这里选择读取。' }}</p>
              </div>
              <button @click="showFormModal = false" class="text-gray-400 hover:text-gray-600 transition-colors text-xl font-bold">
                &times;
              </button>
            </div>
          
            <!-- Modal 主体 -->
            <div class="p-6 space-y-4 max-h-[70vh] overflow-y-auto custom-scrollbar">
              <div class="-mx-1 overflow-x-auto pb-1 custom-scrollbar">
                <div class="flex w-max gap-2 px-1">
                <button
                  v-for="db in dbTypes"
                  :key="db.id"
                  @click="setDbType(db.id)"
                  class="w-28 min-w-28 p-2 rounded-xl border-2 transition-all flex flex-col items-center gap-1.5"
                  :class="form.type === db.id ? 'border-primary bg-primary/5 text-primary' : 'border-gray-100 hover:border-gray-200 text-gray-700'"
                >
                  <DbTypeIcon :type="db.id" :size="24" />
                  <span class="text-[11px] font-black whitespace-nowrap">{{ db.name }}</span>
                </button>
                </div>
              </div>

              <div>
                <label class="block text-xs font-bold text-gray-400 uppercase tracking-wider mb-1.5">数据源名称</label>
                  <div class="flex items-center w-full border border-gray-200 rounded-lg focus-within:ring-2 focus-within:ring-primary/50 overflow-hidden text-sm">
                    <span class="shrink-0 px-3 py-2 bg-gray-50 text-gray-500 font-bold border-r border-gray-200">{{ dataSourcePrefix }}</span>
                    <input
                      v-model="form.nameSuffix"
                      @input="sanitizeNameSuffix"
                      @paste="handleNamePaste"
                      class="min-w-0 flex-1 border-0 px-3 py-2 focus:outline-none focus:ring-0"
                      placeholder="如：ods、production、analytics"
                    >
                  </div>
                  <p class="mt-1.5 text-[10px] leading-relaxed text-gray-500 bg-gray-50 border border-gray-100 rounded-lg px-2.5 py-1.5">
                    前缀由数据库类型自动生成；后缀仅支持英文、数字和下划线，ChatBI 将据此前缀识别 SQL 方言。
                </p>
              </div>

              <div>
                <label class="block text-xs font-bold text-gray-400 uppercase tracking-wider mb-1.5">备注/用途说明</label>
                <textarea
                  v-model="form.description"
                  rows="2"
                  class="w-full border border-gray-200 rounded-lg px-4 py-2 text-xs focus:outline-none focus:ring-2 focus:ring-primary/50 resize-none"
                  placeholder="如：用于 ChatBI 生产库元数据导入"
                ></textarea>
              </div>

              <div class="grid grid-cols-2 gap-3">
                <div>
                  <label class="block text-xs font-bold text-gray-400 uppercase tracking-wider mb-1.5">主机 (Host)</label>
                  <input v-model="form.host" class="w-full border border-gray-200 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-primary/50 text-sm" placeholder="127.0.0.1">
                </div>
                <div>
                  <label class="block text-xs font-bold text-gray-400 uppercase tracking-wider mb-1.5">端口 (Port)</label>
                  <input v-model.number="form.port" type="number" class="w-full border border-gray-200 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-primary/50 text-sm">
                </div>
                <div>
                  <label class="block text-xs font-bold text-gray-400 uppercase tracking-wider mb-1.5">用户名 (User)</label>
                  <input v-model="form.user" class="w-full border border-gray-200 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-primary/50 text-sm" placeholder="root">
                </div>
                <div>
                  <label class="block text-xs font-bold text-gray-400 uppercase tracking-wider mb-1.5">密码 (Password)</label>
                  <input v-model="form.password" type="password" class="w-full border border-gray-200 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-primary/50 text-sm" placeholder="******">
                </div>
              </div>

              <div>
                <label class="block text-xs font-bold text-gray-400 uppercase tracking-wider mb-1.5">数据库/库名 (Database)</label>
                <input v-model="form.database" class="w-full border border-blue-200 bg-blue-50/20 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-primary/50 text-sm" placeholder="e.g. metadata_db">
              </div>

              <div v-if="form.type === 'clickhouse'" class="p-3 bg-blue-50/50 border border-blue-100 rounded-xl text-[11px] text-blue-700 leading-relaxed">
                <p class="font-bold mb-1">ClickHouse 连接提示：</p>
                <p>原生 TCP 常用端口为 9000；如果是 HTTP 端口 8123 请改为 9000。</p>
              </div>

              <div v-if="connError" class="p-3 bg-red-50 border border-red-100 rounded-lg text-xs text-red-600 leading-relaxed font-mono">
                {{ connError }}
              </div>
            </div>
          
            <!-- Modal 尾部 -->
            <div class="bg-gray-50 px-6 py-4 border-t border-gray-100 flex justify-between items-center">
              <button
                @click="testConnection"
                :disabled="testing || !form.host || !form.database"
                class="px-4 py-2 rounded-lg bg-blue-50 text-blue-600 border border-blue-100 hover:bg-blue-100 text-xs font-bold disabled:opacity-50 transition-colors"
              >
                {{ testing ? '正在连接...' : testPassed ? '连接成功' : '测试连接' }}
              </button>
              <div class="flex gap-2">
                <button @click="showFormModal = false" class="px-4 py-2 rounded-lg border border-gray-200 text-gray-500 hover:bg-gray-50 text-xs font-bold transition-colors">取消</button>
                <button
                  @click="saveConfig"
                  :disabled="saving || !testPassed || !form.nameSuffix.trim() || !form.host || !form.database"
                  class="px-5 py-2 rounded-lg bg-primary text-white hover:bg-primary-dark text-xs font-bold shadow-lg shadow-primary/20 disabled:opacity-50 transition-colors"
                >
                  {{ saving ? '保存中...' : editingId ? '保存修改' : '保存数据源' }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

    <div class="w-full">
      <section class="bg-white rounded-xl border border-gray-100 shadow-sm overflow-visible">
        <div class="px-5 py-4 border-b border-gray-100 flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3">
          <div>
            <h2 class="text-base font-black text-gray-900">已保存数据源</h2>
            <p class="text-xs text-gray-500 mt-1">
              共 {{ configs.length }} 个数据源，元数据导入会读取这份列表。
              <span v-if="hasActiveFilter" class="text-gray-400">当前筛选出 {{ displayConfigs.length }} 个</span>
            </p>
          </div>
          <div class="flex w-full flex-wrap items-center gap-2 lg:w-auto">
            <div class="relative order-first w-full sm:order-none sm:w-56">
              <input type="search"
                v-model="keyword"
                class="w-full pl-9 pr-3 py-2 rounded-lg border border-gray-200 bg-gray-50 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40 focus:bg-white"
                placeholder="搜索名称、主机、库名..."
              >
              <svg class="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
              </svg>
            </div>
            <select
              v-if="availableDbTypes.length > 1"
              v-model="typeFilter"
              class="rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-xs font-bold text-gray-600 focus:outline-none focus:ring-2 focus:ring-primary/30"
              v-tooltip="'按数据库类型筛选'"
              aria-label="按数据库类型筛选"
            >
              <option value="all">全部类型</option>
              <option v-for="db in availableDbTypes" :key="db.id" :value="db.id">{{ db.name }}</option>
            </select>
            <select
              v-model="sortBy"
              class="rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-xs font-bold text-gray-600 focus:outline-none focus:ring-2 focus:ring-primary/30"
              v-tooltip="'排序方式'"
              aria-label="排序方式"
            >
              <option value="default">默认排序</option>
              <option value="name">名称 A→Z</option>
              <option value="updated">最近更新</option>
              <option value="profiles">画像数量</option>
            </select>
            <!-- 卡片 / 列表视图切换：与元数据中心、任务中心等页面保持同一套交互 -->
            <div
              class="ml-auto flex shrink-0 select-none items-center gap-0.5 rounded-lg border border-gray-200 bg-gray-200/60 p-0.5 sm:ml-0"
              data-testid="data-source-view-mode-toggle"
            >
              <button
                type="button"
                class="flex items-center justify-center rounded-md p-1.5 transition-all duration-200"
                :class="viewMode === 'card' ? 'border border-gray-200 bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-800'"
                v-tooltip="'卡片视图'"
                aria-label="卡片视图"
                :aria-pressed="viewMode === 'card'"
                @click="viewMode = 'card'"
              >
                <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
                </svg>
              </button>
              <button
                type="button"
                class="flex items-center justify-center rounded-md p-1.5 transition-all duration-200"
                :class="viewMode === 'list' ? 'border border-gray-200 bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-800'"
                v-tooltip="'列表视图'"
                aria-label="列表视图"
                :aria-pressed="viewMode === 'list'"
                @click="viewMode = 'list'"
              >
                <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
                </svg>
              </button>
            </div>
          </div>
        </div>

        <!-- 加载骨架：卡片/列表各自形态，避免用一行灰字撑满整屏 -->
        <div
          v-if="loading"
          :class="viewMode === 'card' ? 'grid grid-cols-1 gap-4 rounded-b-xl bg-gray-50/40 p-4 md:grid-cols-2 xl:grid-cols-3' : 'divide-y divide-gray-100'"
        >
          <div
            v-for="i in 3"
            :key="i"
            class="animate-pulse"
            :class="viewMode === 'card' ? 'rounded-xl border border-gray-100 bg-white p-4' : 'px-4 py-4'"
          >
            <div class="h-4 w-32 rounded bg-gray-100"></div>
            <div class="mt-3 h-3 w-full rounded bg-gray-100"></div>
            <div class="mt-2 h-3 w-2/3 rounded bg-gray-100"></div>
          </div>
        </div>

        <div v-else-if="displayConfigs.length === 0" class="p-12 text-center">
          <div class="mx-auto w-12 h-12 rounded-full bg-gray-100 flex items-center justify-center text-gray-400 mb-3">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4"/>
            </svg>
          </div>
          <p class="text-sm font-bold text-gray-600">{{ configs.length === 0 ? '暂无数据源' : '没有匹配的数据源' }}</p>
          <p class="text-xs text-gray-400 mt-1">{{ configs.length === 0 ? '先在上方添加并测试连接。' : '换个关键词，或清除类型筛选后再看。' }}</p>
          <button
            v-if="configs.length > 0"
            type="button"
            class="mt-4 rounded-lg border border-blue-200 px-4 py-2 text-xs font-bold text-blue-600 transition-colors hover:bg-blue-50"
            @click="keyword = ''; typeFilter = 'all'"
          >
            清除筛选
          </button>
        </div>

        <div
          v-else
          :class="viewMode === 'card'
            ? 'grid grid-cols-1 gap-4 rounded-b-xl bg-gray-50/40 p-4 md:grid-cols-2 xl:grid-cols-3'
            : 'divide-y divide-gray-100 overflow-visible'"
        >
          <div
            v-for="item in displayConfigs"
            :key="item.id"
            class="relative"
            :class="[
              viewMode === 'card'
                ? 'flex flex-col rounded-xl border border-gray-200 bg-white shadow-sm transition-shadow duration-200 hover:shadow-md'
                : 'flex flex-col gap-2 px-4 py-2.5 transition-colors hover:bg-gray-50 sm:flex-row sm:items-center sm:gap-4',
              editingId === item.id
                ? (viewMode === 'card' ? 'border-primary/40 bg-primary/5' : 'bg-primary/5')
                : (viewMode === 'card' ? 'bg-white' : ''),
              openActionMenuId === item.id ? 'z-40' : 'z-0',
            ]"
          >
            <!-- 信息区：卡片内为纵向堆叠；列表内与操作区同行、两行信息 -->
            <div class="min-w-0 flex-1" :class="viewMode === 'card' ? 'p-4' : ''">
              <div class="flex items-center gap-2.5">
                <span
                  class="flex shrink-0 items-center justify-center rounded-lg border border-gray-100"
                  :class="[viewMode === 'card' ? 'h-9 w-9 text-base' : 'h-8 w-8 text-sm', dbTypeTint(item.db_type)]"
                  v-tooltip="dbTypeLabel(item.db_type)"
                >
                  <DbTypeIcon :type="item.db_type" :size="viewMode === 'card' ? 22 : 18" />
                </span>
                <div class="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
                  <h3 class="truncate text-sm font-black text-gray-900">{{ item.name }}</h3>
                  <span class="shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-black uppercase" :class="dbTypeColor(item.db_type)">{{ dbTypeLabel(item.db_type) }}</span>
                  <span
                    class="inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-bold"
                    :class="PROFILE_TONE_CLASS[profileStates[item.id]?.tone || 'empty']"
                    v-tooltip="profileStates[item.id]?.title"
                  >
                    <span class="h-1.5 w-1.5 rounded-full" :class="PROFILE_DOT_CLASS[profileStates[item.id]?.tone || 'empty']"></span>
                    <span>{{ profileStates[item.id]?.text }}</span>
                  </span>
                </div>
              </div>

              <!-- 连接信息：主机端口可一键复制，库名独立成 chip，用户弱化成灰色小字 -->
              <div class="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                <button
                  type="button"
                  class="group/copy inline-flex min-w-0 items-center gap-1 rounded font-mono text-gray-600 transition-colors hover:text-blue-600"
                  v-tooltip="`点击复制 ${connectionText(item)}`"
                  @click="copyConnection(item)"
                >
                  <span class="truncate">{{ item.host }}:{{ item.port }}</span>
                  <svg class="h-3 w-3 shrink-0 text-gray-300 transition-colors group-hover/copy:text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/>
                  </svg>
                </button>
                <span class="inline-flex min-w-0 items-center gap-1">
                  <span class="text-gray-400">库</span>
                  <code class="truncate rounded border border-gray-100 bg-gray-50 px-1.5 py-0.5 font-mono text-[11px] text-gray-600">{{ item.database_name }}</code>
                </span>
                <span class="inline-flex items-center gap-1 text-gray-400">
                  <span>用户</span>
                  <span class="font-mono text-gray-500">{{ item.db_user }}</span>
                </span>
                <!-- 用途：从琥珀色块降级为一行说明；列表里与连接信息同行，卡片里换行占满。
                     纯展示文本不挂 tooltip：备注本身可读，且列表里它撑满剩余宽度，悬停整行右侧都会误弹提示。 -->
                <span
                  v-if="item.description"
                  class="flex min-w-0 items-start gap-1.5"
                  :class="viewMode === 'card' ? 'w-full' : 'flex-1'"
                >
                  <span class="mt-px shrink-0 text-[10px] font-bold uppercase tracking-wider text-gray-400">用途</span>
                  <span class="min-w-0 text-gray-600" :class="viewMode === 'card' ? 'line-clamp-2' : 'line-clamp-1'">{{ item.description }}</span>
                </span>
              </div>

              <!-- 摸排进度：整行进度条，比原来 80px 的小条看得清 -->
              <div v-if="profilingTasks[item.id]?.status === 1" class="mt-2 flex items-center gap-2">
                <div class="h-1.5 flex-1 overflow-hidden rounded-full bg-gray-100">
                  <div class="h-full rounded-full bg-primary transition-all duration-300" :style="{ width: progressPercent(item) + '%' }"></div>
                </div>
                <span class="shrink-0 font-mono text-[10px] text-gray-400">
                  {{ profilingTasks[item.id]?.processed_tables }}/{{ profilingTasks[item.id]?.total_tables }}
                </span>
              </div>
            </div>

            <!-- 操作区：低频动作图标化 + tooltip，卡片内收纳为底部工具条 -->
            <div
              :class="viewMode === 'card'
                ? 'mt-auto flex items-center gap-1 rounded-b-xl border-t border-gray-100 bg-gray-50/60 px-3 py-2'
                : 'flex shrink-0 items-center justify-end gap-1 border-t border-gray-100 pt-2 sm:border-0 sm:pt-0'"
            >
              <button
                v-if="canViewTableProfiles(item)"
                type="button"
                class="inline-flex h-8 shrink-0 items-center gap-1 rounded-lg border border-primary/30 bg-primary/5 px-2.5 text-xs font-bold text-primary transition-colors hover:bg-primary/10"
                v-tooltip="viewProfilesButtonLabel(item)"
                @click="openTableProfiles(item)"
              >
                <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/>
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/>
                </svg>
                <span>查看画像</span>
              </button>

              <div class="ml-auto flex items-center gap-1">
              <div v-if="canEditDataSource" class="relative">
                <button
                  type="button"
                  class="inline-flex h-8 items-center justify-center gap-0.5 rounded-lg border border-transparent px-1.5 text-gray-500 transition-colors hover:border-gray-200 hover:bg-gray-100 hover:text-gray-700 disabled:opacity-50"
                  :disabled="profilingTasks[item.id]?.status === 1 && cancellingProfilingId === item.id"
                  v-tooltip="'智能摸排（增量 / 全量 / 中断）'"
                  aria-label="智能摸排"
                  @click.stop="toggleActionMenu(item.id, 'profile')"
                >
                  <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 00-2.456 2.456z"/>
                  </svg>
                  <svg class="h-3 w-3 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7"/>
                  </svg>
                </button>
                <div
                  v-if="isActionMenuOpen(item.id, 'profile')"
                  class="absolute right-0 top-full z-50 mt-1 w-48 rounded-xl border border-gray-200 bg-white shadow-lg py-1 text-xs"
                  @click.stop
                >
                  <template v-if="profilingTasks[item.id]?.status === 1">
                    <button
                      v-if="canEditDataSource"
                      type="button"
                      class="w-full text-left px-3 py-2 font-bold text-red-600 hover:bg-red-50 disabled:opacity-50"
                      :disabled="cancellingProfilingId === item.id"
                      @click="handleProfileMenuAction(item, 'cancel')"
                    >
                      {{ cancellingProfilingId === item.id ? '中断中...' : '中断摸排' }}
                    </button>
                  </template>
                  <template v-else>
                    <button
                      v-if="canEditDataSource"
                      type="button"
                      class="w-full text-left px-3 py-2 text-gray-700 hover:bg-gray-50"
                      @click="handleProfileMenuAction(item, 'incremental')"
                    >
                      <div class="font-bold">{{ profilingActionLabel(item) }}</div>
                      <div class="mt-0.5 text-[10px] text-gray-400">仅处理未完成及新增表</div>
                    </button>
                    <button
                      v-if="canEditDataSource && [2, 3, 4].includes(profilingTasks[item.id]?.status || 0)"
                      type="button"
                      class="w-full border-t border-gray-100 px-3 py-2 font-bold text-orange-600 hover:bg-orange-50"
                      @click="handleProfileMenuAction(item, 'full')"
                    >
                      强制全量
                      <div class="mt-0.5 text-[10px] font-normal text-orange-400">覆盖已有画像，重新分析</div>
                    </button>
                  </template>
                </div>
              </div>

              <button
                v-if="canEditDataSource"
                type="button"
                class="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-transparent text-gray-500 transition-colors hover:border-gray-200 hover:bg-gray-100 hover:text-emerald-600 disabled:opacity-50"
                :disabled="testingConnectionId === item.id"
                v-tooltip="'测试连接'"
                aria-label="测试连接"
                @click="testSavedConnection(item)"
              >
                <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/>
                </svg>
              </button>

              <button
                v-if="canEditDataSource"
                type="button"
                class="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-transparent transition-colors hover:border-gray-200 hover:bg-gray-100"
                :class="editingId === item.id ? 'border-primary/30 bg-primary/5 text-primary' : 'text-gray-500 hover:text-blue-600'"
                v-tooltip="'编辑数据源'"
                aria-label="编辑数据源"
                @click="editConfig(item)"
              >
                <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/>
                </svg>
              </button>

              <div class="relative">
                <button
                  v-if="canEditDataSource || canDeleteDataSource"
                  type="button"
                  class="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-transparent text-gray-500 transition-colors hover:border-gray-200 hover:bg-gray-100 hover:text-gray-700"
                  v-tooltip="'更多操作（SQL 调试 / 复制 / 删除）'"
                  aria-label="更多操作"
                  @click.stop="toggleActionMenu(item.id, 'more')"
                >
                  <svg class="h-4 w-4" fill="currentColor" viewBox="0 0 24 24">
                    <circle cx="5" cy="12" r="1.6"/>
                    <circle cx="12" cy="12" r="1.6"/>
                    <circle cx="19" cy="12" r="1.6"/>
                  </svg>
                </button>
                <div
                  v-if="isActionMenuOpen(item.id, 'more')"
                  class="absolute right-0 top-full z-50 mt-1 w-36 rounded-xl border border-gray-200 bg-white shadow-lg py-1 text-xs"
                  @click.stop
                >
                  <button v-if="canEditDataSource" type="button" class="w-full text-left px-3 py-2 text-gray-700 hover:bg-gray-50" @click="handleMoreMenuAction(item, 'debug')">SQL 调试</button>
                  <button v-if="canEditDataSource" type="button" class="w-full text-left px-3 py-2 text-gray-700 hover:bg-gray-50" @click="handleMoreMenuAction(item, 'copy')">复制</button>
                  <div class="my-1 border-t border-gray-100"></div>
                  <button
                    v-if="canDeleteDataSource"
                    type="button"
                    class="w-full text-left px-3 py-2 font-bold text-red-600 hover:bg-red-50 disabled:opacity-50"
                    :disabled="deletingId === item.id"
                    @click="handleMoreMenuAction(item, 'delete')"
                  >
                    {{ deletingId === item.id ? '删除中' : '删除' }}
                  </button>
                </div>
              </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>

    <ConfirmModal
      v-if="deleteTarget"
      title="确认删除数据源"
      :message="`确定要删除数据源 “${deleteTarget.name}” 吗？此操作不可恢复。`"
      confirm-text="确认删除"
      cancel-text="取消"
      type="danger"
      @confirm="confirmDeleteConfig"
      @cancel="cancelDeleteConfig"
    />

    <!-- SQL 在线调试 Modal (Premium Design) -->
    <Teleport to="body">
      <div v-if="debugTarget" class="fixed inset-0 z-[100] overflow-y-auto" aria-labelledby="modal-title" role="dialog" aria-modal="true">
        <div class="flex items-end justify-center min-h-screen pt-4 px-4 pb-20 text-center sm:block sm:p-0">
          <!-- Backdrop -->
          <div @click="closeSqlDebug" class="fixed inset-0 bg-gray-500 bg-opacity-75 transition-opacity" aria-hidden="true"></div>

          <span class="hidden sm:inline-block sm:align-middle sm:h-screen" aria-hidden="true">&#8203;</span>

          <!-- Modal Content Container -->
          <div class="inline-block align-bottom bg-white rounded-2xl text-left overflow-hidden shadow-2xl transform transition-all sm:my-8 sm:align-middle sm:max-w-4xl sm:w-full border border-gray-100">
            <div class="bg-gray-50 px-6 py-4 border-b border-gray-100 flex items-center justify-between">
              <div class="flex items-center gap-2">
                <span class="text-xl">🛠️</span>
                <div>
                  <h3 class="text-base font-black text-gray-900">数据源直连调试: {{ debugTarget.name }}</h3>
                  <p class="text-xs text-gray-500 mt-0.5">本地直连驱动测试 · 仅允许执行只读 SELECT/WITH 查询</p>
                </div>
              </div>
              <button @click="closeSqlDebug" class="text-gray-400 hover:text-gray-600 transition-colors text-xl font-bold">
                &times;
              </button>
            </div>

            <div class="p-6 space-y-4">
              <div>
                <label class="block text-xs font-bold text-gray-400 uppercase tracking-wider mb-2">输入调试 SQL 语句</label>
                <textarea
                  v-model="debugSql"
                  rows="5"
                  class="w-full border border-gray-200 rounded-xl px-4 py-3 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-primary/50 resize-y bg-white disabled:bg-gray-100"
                  placeholder="SELECT * FROM table_name LIMIT 10"
                ></textarea>
              </div>

              <div class="flex items-center justify-between gap-4">
                <div class="flex items-center gap-2">
                  <label class="text-xs font-bold text-gray-400">限制行数 (Max Limit):</label>
                  <select v-model.number="debugLimit" class="border border-gray-200 rounded-lg px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-primary bg-white">
                    <option :value="10">10 行</option>
                    <option :value="50">50 行</option>
                    <option :value="100">100 行</option>
                    <option :value="500">500 行</option>
                  </select>
                </div>
                <button
                  @click="runSqlDebug"
                  :disabled="debugExecuting || !debugSql.trim()"
                  class="px-5 py-2 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 text-sm font-bold shadow-lg shadow-emerald-600/20 disabled:opacity-50 transition-colors flex items-center gap-2"
                >
                  <span v-if="debugExecuting" class="animate-spin h-4 w-4 border-2 border-white border-t-transparent rounded-full"></span>
                  <span>{{ debugExecuting ? '正在执行...' : '执行查询' }}</span>
                </button>
              </div>

              <!-- Error Banner -->
              <div v-if="debugError" class="p-4 bg-red-50 border border-red-100 rounded-xl text-xs text-red-700 font-mono leading-relaxed break-all">
                <div class="font-bold mb-1">❌ SQL 执行错误:</div>
                {{ debugError }}
              </div>

              <!-- Results Section -->
              <div v-if="debugResult" class="space-y-2">
                <div class="flex items-center justify-between text-xs text-gray-500 bg-gray-50 rounded-lg px-3 py-2 border border-gray-100">
                  <span>📊 查询返回行数: <strong class="text-gray-900">{{ debugResult.rows.length }}</strong> 行</span>
                  <span v-if="debugResult.execution_time_ms">⏱️ 数据库耗时: <strong class="text-gray-900">{{ debugResult.execution_time_ms.toFixed(2) }}</strong> ms</span>
                </div>

                <div class="border border-gray-100 rounded-xl overflow-hidden max-h-[300px] overflow-y-auto overflow-x-auto custom-scrollbar">
                  <table class="w-full text-left border-collapse text-xs font-mono">
                    <thead>
                      <tr class="bg-gray-50 border-b border-gray-100 text-gray-400 font-bold uppercase">
                        <th v-for="col in debugResult.columns" :key="col.name" class="px-4 py-2 border-r border-gray-100 shrink-0 whitespace-nowrap">
                          {{ col.name }} <span class="text-[9px] text-gray-300 font-normal">({{ col.type }})</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody class="divide-y divide-gray-100 text-gray-700">
                      <tr v-if="debugResult.rows.length === 0">
                        <td :colspan="debugResult.columns.length" class="text-center py-8 text-gray-400 italic bg-white">
                          查询成功，但返回数据为空。
                        </td>
                      </tr>
                      <tr v-else v-for="(row, rIdx) in debugResult.rows" :key="rIdx" class="hover:bg-gray-50 bg-white">
                        <td v-for="(cell, cIdx) in row" :key="cIdx" class="px-4 py-2 border-r border-gray-100 whitespace-nowrap max-w-[200px] truncate" v-tooltip="String(cell)">
                          {{ cell === null ? 'NULL' : cell }}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            </div>

            <div class="bg-gray-50 px-6 py-4 border-t border-gray-100 flex justify-end">
              <button @click="closeSqlDebug" class="px-4 py-2 bg-white border border-gray-200 text-gray-600 rounded-lg hover:bg-gray-50 text-xs font-bold transition-colors">
                关闭调试
              </button>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

    <DbTableProfileExplorerModal
      ref="profileExplorerRef"
      :show="!!showProfilesTarget"
      :config="showProfilesTarget"
      :profiling-task="activeProfileTask"
      @close="closeTableProfiles"
    />

    <!-- 智能摸排确认 Modal -->
    <ConfirmModal
      v-if="profileTarget"
      :title="profileFullReset ? '确认强制全量摸排' : '确认启动智能摸排'"
      :message="profileFullReset
        ? `将对数据源 “${profileTarget.name}” 下所有表和视图强制重新进行 AI 分析，已有成功画像将被覆盖。大库可能耗时较长且消耗大量 Token，运行中可随时点击「中断」停止。确认强制全量吗？`
        : `将对数据源 “${profileTarget.name}” 下未完成、失败及新增的表/视图进行摸排分析，已有成功画像将保留。中断后再次「继续摸排」会从剩余表接着跑，不会重复分析已完成的表。大库可能耗时较长，运行中可随时点击「中断」停止。确认启动吗？`"
      :confirm-text="profileFullReset ? '确认强制全量' : '确认启动'"
      cancel-text="取消"
      type="warning"
      @confirm="confirmProfiling"
      @cancel="cancelProfiling"
    />
  </div>
    <!-- 数据源管理规范与表画像摸排指南 Modal -->
    <Teleport to="body">
      <div v-if="showSpecsModal" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="showSpecsModal = false">
        <div class="bg-white rounded-2xl shadow-2xl w-full max-w-5xl h-[85vh] flex flex-col overflow-hidden border border-gray-100 animate-fade-in-up">
          <!-- Header -->
          <div class="p-6 border-b border-gray-100 flex justify-between items-center bg-blue-50/30">
            <div class="flex items-center gap-3">
               <div class="w-10 h-10 rounded-xl bg-blue-600 text-white flex items-center justify-center shadow-md shadow-blue-500/20" style="background-color: #2563eb; color: #ffffff;">
                  <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"/></svg>
               </div>
               <div>
                 <h2 class="text-xl font-bold text-gray-900">数据源管理设计规范与表画像摸排指南</h2>
                 <p class="text-xs text-gray-500 font-medium mt-0.5">外部多引擎数据库连接维护、安全只读隔离、异步表结构画像摸排与元数据中心打通。</p>
               </div>
            </div>
            <button @click="showSpecsModal = false" class="text-gray-400 hover:text-gray-600 transition-colors cursor-pointer">
              <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
            </button>
          </div>

          <!-- Tabs -->
          <div class="flex border-b border-gray-200 bg-white px-6">
             <button 
               v-for="tab in ['concept', 'security', 'practice']" 
               :key="tab"
               @click="activeSpecsTab = tab as any"
               class="px-4 py-3 text-sm font-medium border-b-2 transition-colors cursor-pointer"
               :class="activeSpecsTab === tab ? 'border-blue-600 text-blue-700' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'"
             >
               {{ tab === 'concept' ? '核心架构与表画像流程 (Architecture & Profiling)' :
                  tab === 'security' ? '连接配置与安全隔离规范 (Security & Connections)' : '故障排查与最佳实践 (Best Practice)' }}
             </button>
          </div>

          <!-- Content -->
          <div class="flex-1 overflow-y-auto p-6 sm:p-8 bg-gray-50/50">
             <!-- Tab 1: Architecture & Profiling -->
             <div v-if="activeSpecsTab === 'concept'" class="space-y-6 max-w-4xl mx-auto">
                <div class="bg-gradient-to-r from-blue-50 to-indigo-50 border-l-4 border-blue-600 p-4 rounded-r-xl shadow-2xs">
                   <h3 class="font-bold text-blue-900 mb-1">物理连接与元数据中心解耦设计</h3>
                   <p class="text-xs text-blue-700 leading-relaxed">
                      数据源管理是平台的底层物理连接层。在此配置好 MySQL、PostgreSQL、ClickHouse、Oracle、SQL Server 连接后，业务人员在「元数据中心」即可直接按库表一键导入语义，无需重复输入账号密码。
                   </p>
                </div>

                <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
                   <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm space-y-2">
                      <h4 class="font-bold text-gray-900 text-sm flex items-center gap-1.5">
                         <span class="w-2 h-2 rounded-full bg-blue-500"></span> 1. 多类型数据库统一接入
                      </h4>
                      <p class="text-gray-500 leading-relaxed">
                         支持 MySQL 5.7/8.0、PostgreSQL、ClickHouse、Oracle 11g/19c 与 Microsoft SQL Server，提供一键连通性测试与参数校验。
                      </p>
                   </div>
                   <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm space-y-2">
                      <h4 class="font-bold text-gray-900 text-sm flex items-center gap-1.5">
                         <span class="w-2 h-2 rounded-full bg-emerald-500"></span> 2. 异步表结构画像摸排 (Profiling)
                      </h4>
                      <p class="text-gray-500 leading-relaxed">
                         后台智能摸排引擎自动扫描库内全部表的总行数、字段注释、主外键关系及枚举值基数（Cardinality），无需人工介入即可生成高清数据库拓扑。
                      </p>
                   </div>
                   <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm space-y-2">
                      <h4 class="font-bold text-gray-900 text-sm flex items-center gap-1.5">
                         <span class="w-2 h-2 rounded-full bg-indigo-500"></span> 3. 在线 SQL 调试沙箱
                      </h4>
                      <p class="text-gray-500 leading-relaxed">
                         内置 SQL 执行沙箱，支持对数据源进行即时采样查询（默认限制 Limit 100 条并统计执行毫秒），方便排查网络延迟与表结构可用性。
                      </p>
                   </div>
                   <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm space-y-2">
                      <h4 class="font-bold text-gray-900 text-sm flex items-center gap-1.5">
                         <span class="w-2 h-2 rounded-full bg-amber-500"></span> 4. 增量与全量摸排策略
                      </h4>
                      <p class="text-gray-500 leading-relaxed">
                         支持只针对新增/修改表进行「增量摸排」，或针对整个数据库执行「全量重新摸排」，保障元数据语义始终与线上业务库同步。
                      </p>
                   </div>
                </div>
             </div>

             <!-- Tab 2: Security & Connections -->
             <div v-else-if="activeSpecsTab === 'security'" class="space-y-4 max-w-4xl mx-auto text-xs">
                <div class="bg-white p-6 rounded-2xl border border-gray-200 shadow-sm space-y-4 text-gray-650 leading-relaxed">
                   <h4 class="font-bold text-gray-900 text-base">安全配置与最小权限原则</h4>
                   <div class="p-4 bg-amber-50/60 rounded-xl border border-amber-200 space-y-2">
                      <span class="font-bold text-amber-900 text-sm">🔒 推荐配置：只读账号（Read-Only User）</span>
                      <p class="text-gray-600">为防止误操作或 SQL 注入风险，强烈建议为平台分配仅包含 <code>SELECT</code>、<code>SHOW</code>、<code>DESCRIBE</code> 权限的数据库只读账号，严禁配置 <code>DROP</code>、<code>DELETE</code>、<code>ALTER</code> 权限。</p>
                   </div>
                   <div class="space-y-2">
                      <h5 class="font-bold text-gray-800 text-sm">网络与端口安全建议：</h5>
                      <ul class="list-disc list-inside text-gray-600 space-y-1">
                         <li>若数据库部署在私有 VPC 或内网，请确保平台宿主机或 Docker 容器网络能够解析 Host 并路由至指定端口；</li>
                         <li>建议配置数据库白名单，仅允许平台服务器 IP 地址发起连接；</li>
                         <li>ClickHouse 请注意区分原生 TCP 端口（如 9000）与 HTTP 协议端口（如 8123）。</li>
                      </ul>
                   </div>
                </div>
             </div>

             <!-- Tab 3: Best Practice -->
             <div v-else-if="activeSpecsTab === 'practice'" class="space-y-4 max-w-4xl mx-auto text-xs">
                <div class="bg-white p-6 rounded-2xl border border-gray-200 shadow-sm space-y-4 text-gray-650 leading-relaxed">
                   <h4 class="font-bold text-gray-900 text-base">常见连通性问题排查与最佳实践</h4>
                   <div class="space-y-3">
                      <div class="p-3.5 bg-gray-50 rounded-xl border border-gray-150 space-y-1">
                         <span class="font-bold text-gray-900">1. 连接超时 (Connection Timeout)</span>
                         <p class="text-gray-600 leading-relaxed">请检查数据库宿主机防火墙（iptables/Security Group）是否放行对应端口，以及数据库是否开启了远程登录授权（如 MySQL 的 <code>'user'@'%'</code>）。</p>
                      </div>
                      <div class="p-3.5 bg-gray-50 rounded-xl border border-gray-150 space-y-1">
                         <span class="font-bold text-gray-900">2. 字符集与中文乱码</span>
                         <p class="text-gray-600 leading-relaxed">建议数据库统一采用 <code>utf8mb4</code> 字符集，避免表注释或字段内容在画像摸排与大模型理解时产生乱码。</p>
                      </div>
                      <div class="p-3.5 bg-gray-50 rounded-xl border border-gray-150 space-y-1">
                         <span class="font-bold text-gray-900">3. 大库表摸排优化</span>
                         <p class="text-gray-600 leading-relaxed">若数据库表数量超过 500 张，表结构画像摸排将在后台异步队列执行，可在卡片中实时查看摸排进度条，无需停留在当前页面等待。</p>
                      </div>
                   </div>
                </div>
             </div>
          </div>
        </div>
      </div>
    </Teleport>

</template>
