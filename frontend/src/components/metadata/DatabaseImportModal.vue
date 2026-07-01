<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { metadataApi, type DbConnectionConfig } from '../../api/metadata'
import { useToast } from '../../composables/useToast'

const props = withDefaults(defineProps<{
  show: boolean
  /** 当前数据集内已存在的物理表名，用于禁用重复导入 */
  importedTableNames?: string[]
}>(), {
  importedTableNames: () => [],
})

const emit = defineEmits(['close', 'confirm'])

const { showToast } = useToast()
const router = useRouter()

// ─── 步骤控制 ───────────────────────────────────────────────────────────────
const step = ref(1) // 1: Connection, 2: Select Tables
const loading = ref(false)
const testing = ref(false)
const testPassed = ref(false)
const connError = ref('')

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
  if (show) {
    await fetchSavedConfigs()
  }
})

const fetchSavedConfigs = async () => {
  loadingConfigs.value = true
  try {
    const res = await metadataApi.listDbConnectionConfigs()
    savedConfigs.value = res.data.data || []
    if (!selectedConfigId.value && savedConfigs.value.length > 0) {
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

// ─── 加载表列表（Step 2） ──────────────────────────────────────────────────────
const tables = ref<{name: string, comment: string, type?: 'table' | 'view'}[]>([])
const searchQuery = ref('')
const importFilter = ref<'all' | 'unimported'>('unimported')
const selectedTables = ref<string[]>([])

const importedTableSet = computed(() => {
  const set = new Set<string>()
  for (const name of props.importedTableNames || []) {
    const normalized = String(name || '').trim().toLowerCase()
    if (normalized) set.add(normalized)
  }
  return set
})

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

const importedCountInRemote = computed(() =>
  tables.value.filter((t) => isTableImported(t.name)).length,
)

const handleNext = async () => {
  loading.value = true
  connError.value = ''
  try {
    const res = await metadataApi.listDbTables(config.value)
    tables.value = res.data.data
    selectedTables.value = selectedTables.value.filter((name) => !isTableImported(name))
    importFilter.value = importedCountInRemote.value > 0 ? 'unimported' : 'all'
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
    const res = await metadataApi.getDbDdl(config.value, selectedTables.value)
    emit('confirm', res.data.data)
    handleClose()
  } catch {
    showToast('抓取 DDL 失败', 'error')
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
  const selectable = selectableFilteredTables.value.map((t) => t.name)
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
    <div class="bg-white rounded-2xl shadow-2xl w-full max-w-2xl overflow-hidden border border-gray-100 flex flex-col max-h-[92vh] animate-fade-in-up">

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
      <div class="flex-1 overflow-y-auto p-6">

        <!-- Step 1: Data Source -->
        <div v-if="step === 1" class="space-y-4">

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
                <p class="text-xs text-gray-500 mt-1">元数据导入会使用选中的数据源读取表列表和 DDL。</p>
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
                  <div class="flex items-center gap-2">
                    <span class="text-sm font-bold text-gray-800 truncate">{{ c.name }}</span>
                    <span class="px-1.5 py-0.5 rounded text-[9px] font-bold uppercase" :class="dbTypeColor(c.db_type)">{{ c.db_type }}</span>
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

        <!-- Step 2: Select Tables -->
        <div v-else class="h-full flex flex-col gap-4">
          <div class="flex items-center gap-2 bg-gray-50 p-2 rounded-xl border border-gray-200">
            <svg class="w-5 h-5 text-gray-400 ml-2 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
            <input v-model="searchQuery" type="text" class="flex-1 min-w-0 bg-transparent border-none focus:ring-0 text-sm py-1" placeholder="搜索表名...">
            <select
              v-model="importFilter"
              class="text-xs font-semibold text-gray-600 bg-white border border-gray-200 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/20 shrink-0"
            >
              <option value="unimported">未导入</option>
              <option value="all">全部</option>
            </select>
            <button
              @click="toggleAll"
              :disabled="selectableFilteredTables.length === 0"
              class="text-primary text-xs font-bold px-3 py-1 hover:bg-white rounded-lg transition-colors border border-transparent hover:border-gray-200 disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
            >
              {{
                selectableFilteredTables.length > 0
                && selectableFilteredTables.every((t) => selectedTables.includes(t.name))
                  ? '全部取消'
                  : '全选'
              }}
            </button>
          </div>

          <div class="flex-1 overflow-y-auto border border-gray-100 rounded-xl divide-y divide-gray-50">
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
                    <span class="text-sm font-mono truncate" :class="isTableImported(table.name) ? 'text-gray-400' : 'text-gray-700'">{{ table.name }}</span>
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

          <div class="text-xs text-gray-400 font-medium px-2 space-y-0.5">
            <div>
              已选择 <span class="text-primary font-bold">{{ selectedTables.length }}</span>
              / 可导入 {{ importableTables.length }} 个表
            </div>
            <div v-if="importedCountInRemote > 0" class="text-[11px] text-gray-400">
              当前数据集已有 {{ importedCountInRemote }} 张表，已自动禁用重复导入
            </div>
          </div>
        </div>
      </div>

      <!-- Footer -->
      <div class="p-6 border-t border-gray-100 flex justify-between items-center bg-gray-50/30 shrink-0">
        <!-- 左侧：第一步显示"测试连接"，第二步返回数据源选择 -->
        <div v-if="step === 1" class="flex items-center gap-2">
          <button
            @click="handleTestConnection"
            :disabled="testing || !selectedConfigId || !config.host || !config.database"
            class="px-5 py-2 bg-blue-50 hover:bg-blue-100 text-blue-600 rounded-xl text-sm font-bold transition-all flex items-center gap-2 disabled:opacity-50 border border-blue-100"
          >
            <svg v-if="testing" class="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
            <svg v-else-if="testPassed" class="w-4 h-4 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"/></svg>
            <span>{{ testing ? '正在连接...' : testPassed ? '连接成功' : '测试连接' }}</span>
          </button>
        </div>
        <div v-else>
          <button @click="step = 1" class="text-sm font-bold text-gray-400 hover:text-gray-600 transition-all flex items-center gap-1">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/></svg>
            更换数据源
          </button>
        </div>

        <div class="flex gap-3">
          <button @click="handleClose" class="px-6 py-2 border border-gray-300 rounded-xl text-sm font-medium text-gray-700 hover:bg-gray-100">取消</button>

          <button
            v-if="step === 1"
            @click="handleNext"
            :disabled="loading || !selectedConfigId || !config.host || !config.database || !testPassed"
            class="px-8 py-2 bg-primary hover:bg-primary-dark text-white rounded-xl text-sm font-bold shadow-lg shadow-primary/20 transition-all flex items-center gap-2 disabled:opacity-50"
          >
            <svg v-if="loading" class="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
            <span>下一步 (浏览表)</span>
          </button>

          <button
            v-else
            @click="handleConfirm"
            :disabled="loading || selectedTables.length === 0"
            class="px-8 py-2 bg-green-600 hover:bg-green-700 text-white rounded-xl text-sm font-bold shadow-lg shadow-green-500/20 transition-all flex items-center gap-2 disabled:opacity-50"
          >
            <svg v-if="loading" class="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
            <span>确认导入 ({{ selectedTables.length }})</span>
          </button>
        </div>
      </div>

    </div>
  </div>
</template>

<style scoped>
.animate-fade-in-up {
  animation: fadeInUp 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}
@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(20px) scale(0.95); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
</style>
