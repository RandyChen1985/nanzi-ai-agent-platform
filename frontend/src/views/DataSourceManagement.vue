<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { metadataApi, type DbConnectionConfig } from '../api/metadata'
import { useToast } from '../composables/useToast'
import ConfirmModal from '../components/ConfirmModal.vue'
import DbTableProfileExplorerModal from '../components/metadata/DbTableProfileExplorerModal.vue'

const { showToast } = useToast()

const dbTypes = [
  { id: 'mysql', name: 'MySQL', icon: '🐬', defaultPort: 3306 },
  { id: 'clickhouse', name: 'ClickHouse', icon: '🧊', defaultPort: 9000 },
  { id: 'oracle', name: 'Oracle', icon: '🔴', defaultPort: 1521 },
  { id: 'sqlserver', name: 'SQL Server', icon: '🟦', defaultPort: 1433 },
  { id: 'postgresql', name: 'PostgreSQL', icon: '🐘', defaultPort: 5432 },
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
const isEditing = computed(() => editingId.value !== null)
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
  if (type === 'clickhouse') return 'bg-cyan-100 text-cyan-700 border-cyan-200'
  if (type === 'oracle') return 'bg-red-100 text-red-700 border-red-200'
  if (type === 'sqlserver' || type === 'mssql') return 'bg-indigo-100 text-indigo-700 border-indigo-200'
  if (type === 'postgresql' || type === 'postgres' || type === 'pg') return 'bg-purple-100 text-purple-700 border-purple-200'
  return 'bg-gray-100 text-gray-600 border-gray-200'
}

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
        <h1 class="text-2xl font-bold text-gray-900">数据源管理</h1>
        <p class="text-sm text-gray-500 mt-1">统一维护 ChatBI 元数据导入使用的数据库连接。</p>
      </div>
      <div class="flex items-center gap-2 shrink-0">
        <button
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
    <div v-if="showFormModal" class="fixed inset-0 z-50 overflow-y-auto animate-fade-in" aria-labelledby="modal-title" role="dialog" aria-modal="true">
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
                <span class="text-lg leading-6">{{ db.icon }}</span>
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
                    class="min-w-0 flex-1 px-3 py-2 focus:outline-none"
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

    <div class="w-full">
      <section class="bg-white rounded-xl border border-gray-100 shadow-sm overflow-visible">
        <div class="px-5 py-4 border-b border-gray-100 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div>
            <h2 class="text-base font-black text-gray-900">已保存数据源</h2>
            <p class="text-xs text-gray-500 mt-1">共 {{ configs.length }} 个数据源，元数据导入会读取这份列表。</p>
          </div>
          <div class="relative sm:w-72">
            <input type="search"
              v-model="keyword"
              class="w-full pl-9 pr-3 py-2 rounded-lg border border-gray-200 bg-gray-50 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40 focus:bg-white"
              placeholder="搜索名称、主机、库名..."
            >
            <svg class="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
            </svg>
          </div>
        </div>

        <div v-if="loading" class="p-12 text-center text-sm text-gray-400">加载数据源中...</div>
        <div v-else-if="filteredConfigs.length === 0" class="p-12 text-center">
          <div class="mx-auto w-12 h-12 rounded-full bg-gray-100 flex items-center justify-center text-gray-400 mb-3">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4"/>
            </svg>
          </div>
          <p class="text-sm font-bold text-gray-600">暂无数据源</p>
          <p class="text-xs text-gray-400 mt-1">先在左侧添加并测试连接。</p>
        </div>
        <div v-else class="divide-y divide-gray-100 overflow-visible">
          <div
            v-for="item in filteredConfigs"
            :key="item.id"
            class="p-4 hover:bg-gray-50 transition-colors relative"
            :class="[
              editingId === item.id ? 'bg-primary/5' : '',
              openActionMenuId === item.id ? 'z-40' : 'z-0',
            ]"
          >
            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
              <!-- 左侧基本信息与状态说明 -->
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-2 mb-1 flex-wrap">
                  <h3 class="font-black text-gray-900 truncate">{{ item.name }}</h3>
                  <span class="px-2 py-0.5 rounded border text-[10px] font-black uppercase shrink-0" :class="dbTypeColor(item.db_type)">{{ item.db_type }}</span>
                  <!-- 摸排完成的精致徽章 -->
                  <span 
                    v-if="profilingTasks[item.id] && profilingTasks[item.id].status === 2" 
                    class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-100 text-[10px] font-black shrink-0 shadow-sm shadow-emerald-50"
                  >
                    <span>🤖</span>
                    <span>已生成 {{ profilingTasks[item.id].total_tables }} 张表画像</span>
                  </span>
                  <!-- 摸排失败提示 -->
                  <span 
                    v-if="profilingTasks[item.id] && profilingTasks[item.id].status === 3" 
                    class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-red-50 text-red-600 border border-red-100 text-[10px] font-bold shrink-0"
                    :title="profilingTasks[item.id].error_message"
                  >
                    <span>⚠️</span>
                    <span>分析中断</span>
                  </span>
                  <!-- 用户主动中断 -->
                  <span
                    v-if="profilingTasks[item.id] && profilingTasks[item.id].status === 4"
                    class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-100 text-[10px] font-bold shrink-0"
                    :title="profilingTasks[item.id].error_message"
                  >
                    <span>⏸</span>
                    <span>已中断 {{ profilingTasks[item.id].processed_tables }}/{{ profilingTasks[item.id].total_tables }}</span>
                  </span>
                </div>
                <p class="text-xs font-mono text-gray-500 truncate">{{ item.host }}:{{ item.port }} / {{ item.database_name }}</p>
                <p class="text-xs text-gray-400 mt-1">用户：{{ item.db_user }}</p>
                
                <div class="flex items-center gap-2 flex-wrap mt-2">
                  <!-- 用途备注 -->
                  <div v-if="item.description" class="inline-flex max-w-full items-start gap-1.5 rounded-lg bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800 border border-amber-100">
                    <svg class="w-3.5 h-3.5 mt-0.5 shrink-0 text-amber-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 8h10M7 12h6m-6 4h4M5 4h14a1 1 0 011 1v14l-4-3H5a1 1 0 01-1-1V5a1 1 0 011-1z"/>
                    </svg>
                    <span class="font-bold shrink-0">用途</span>
                    <span class="line-clamp-1">{{ item.description }}</span>
                  </div>

                  <!-- 正在摸排任务进度提示 (轻量化展示在用途旁) -->
                  <div v-if="profilingTasks[item.id] && profilingTasks[item.id].status === 1" class="inline-flex items-center gap-2 bg-blue-50/50 border border-blue-100 rounded-lg px-2.5 py-1.5 text-xs">
                    <span class="font-bold text-primary animate-pulse shrink-0 flex items-center gap-1">
                      <span>🤖 摸排中</span>
                      <span class="font-mono text-gray-500">({{ profilingTasks[item.id].processed_tables }}/{{ profilingTasks[item.id].total_tables }})</span>
                    </span>
                    <!-- 进度条 -->
                    <div class="w-20 bg-gray-200 rounded-full h-1 overflow-hidden shrink-0">
                      <div 
                        class="bg-primary h-full transition-all duration-300"
                        :style="{ width: `${(profilingTasks[item.id].processed_tables / profilingTasks[item.id].total_tables) * 100}%` }"
                      ></div>
                    </div>
                    <span v-if="profilingTasks[item.id].current_table" class="text-[10px] text-gray-400 truncate max-w-[200px]">
                      分析中: {{ profilingTasks[item.id].current_table }}
                    </span>
                  </div>
                </div>
              </div>

              <!-- 右侧操作：画像 + 摸排 + 测试/编辑 + 更多 -->
              <div class="flex items-center gap-1.5 flex-shrink-0">
                <button
                  v-if="canViewTableProfiles(item)"
                  @click="openTableProfiles(item)"
                  class="px-3 py-1.5 rounded-lg border border-primary/30 bg-primary/5 text-primary hover:bg-primary/10 text-xs font-bold transition-all flex items-center gap-1"
                  :title="profilingTasks[item.id]?.status === 1 ? '摸排进行中，可查看已完成的画像' : undefined"
                >
                  <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
                  </svg>
                  <span>{{ viewProfilesButtonLabel(item) }}</span>
                </button>

                <div class="relative">
                  <button
                    @click.stop="toggleActionMenu(item.id, 'profile')"
                    class="px-3 py-1.5 rounded-lg border border-gray-200 bg-white text-gray-700 hover:bg-gray-50 text-xs font-bold transition-all flex items-center gap-1 disabled:opacity-50"
                    :disabled="profilingTasks[item.id]?.status === 1 && cancellingProfilingId === item.id"
                  >
                    <span>{{ profilingTasks[item.id]?.status === 1 ? '摸排中' : '摸排' }}</span>
                    <svg class="w-3 h-3 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7"/>
                    </svg>
                  </button>
                  <div
                    v-if="isActionMenuOpen(item.id, 'profile')"
                    class="absolute right-0 top-full mt-1 w-48 rounded-xl border border-gray-200 bg-white shadow-lg z-50 py-1 text-xs"
                    @click.stop
                  >
                    <template v-if="profilingTasks[item.id]?.status === 1">
                      <button
                        @click="handleProfileMenuAction(item, 'cancel')"
                        :disabled="cancellingProfilingId === item.id"
                        class="w-full text-left px-3 py-2 text-red-600 hover:bg-red-50 font-bold disabled:opacity-50"
                      >
                        {{ cancellingProfilingId === item.id ? '中断中...' : '中断摸排' }}
                      </button>
                    </template>
                    <template v-else>
                      <button
                        @click="handleProfileMenuAction(item, 'incremental')"
                        class="w-full text-left px-3 py-2 text-gray-700 hover:bg-gray-50"
                      >
                        <div class="font-bold">{{ profilingActionLabel(item) }}</div>
                        <div class="text-[10px] text-gray-400 mt-0.5">仅处理未完成及新增表</div>
                      </button>
                      <button
                        v-if="profilingTasks[item.id] && [2, 3, 4].includes(profilingTasks[item.id].status)"
                        @click="handleProfileMenuAction(item, 'full')"
                        class="w-full text-left px-3 py-2 text-orange-600 hover:bg-orange-50 font-bold border-t border-gray-100"
                      >
                        强制全量
                        <div class="text-[10px] text-orange-400 font-normal mt-0.5">覆盖已有画像，重新分析</div>
                      </button>
                    </template>
                  </div>
                </div>

                <button
                  @click="testSavedConnection(item)"
                  :disabled="testingConnectionId === item.id"
                  class="px-3 py-1.5 rounded-lg border border-gray-200 bg-white text-gray-700 hover:bg-gray-50 text-xs font-bold transition-all disabled:opacity-50"
                >
                  {{ testingConnectionId === item.id ? '测试中...' : '测试' }}
                </button>

                <button
                  @click="editConfig(item)"
                  class="px-3 py-1.5 rounded-lg border text-xs font-bold transition-all"
                  :class="editingId === item.id
                    ? 'border-primary/30 bg-primary/5 text-primary'
                    : 'border-gray-200 bg-white text-gray-700 hover:bg-gray-50'"
                >
                  编辑
                </button>

                <div class="relative">
                  <button
                    @click.stop="toggleActionMenu(item.id, 'more')"
                    class="w-8 h-8 rounded-lg border border-gray-200 bg-white text-gray-500 hover:bg-gray-50 hover:text-gray-700 text-base font-bold transition-all flex items-center justify-center"
                    title="更多操作"
                  >
                    ⋯
                  </button>
                  <div
                    v-if="isActionMenuOpen(item.id, 'more')"
                    class="absolute right-0 top-full mt-1 w-36 rounded-xl border border-gray-200 bg-white shadow-lg z-50 py-1 text-xs"
                    @click.stop
                  >
                    <button @click="handleMoreMenuAction(item, 'debug')" class="w-full text-left px-3 py-2 text-gray-700 hover:bg-gray-50">SQL 调试</button>
                    <button @click="handleMoreMenuAction(item, 'copy')" class="w-full text-left px-3 py-2 text-gray-700 hover:bg-gray-50">复制</button>
                    <div class="border-t border-gray-100 my-1"></div>
                    <button
                      @click="handleMoreMenuAction(item, 'delete')"
                      :disabled="deletingId === item.id"
                      class="w-full text-left px-3 py-2 text-red-600 hover:bg-red-50 font-bold disabled:opacity-50"
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
    <div v-if="debugTarget" class="fixed inset-0 z-50 overflow-y-auto" aria-labelledby="modal-title" role="dialog" aria-modal="true">
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
                class="w-full border border-gray-200 rounded-xl px-4 py-3 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-primary/50 resize-y bg-gray-50/50"
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
                      <td v-for="(cell, cIdx) in row" :key="cIdx" class="px-4 py-2 border-r border-gray-100 whitespace-nowrap max-w-[200px] truncate" :title="String(cell)">
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
</template>
