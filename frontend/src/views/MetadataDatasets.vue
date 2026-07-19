<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { metadataApi } from '../api/metadata'
import type { Dataset } from '../api/metadata'
import { portalApi, type User, type Role } from '../api/portal'
import axios from '@/utils/axios'
import SmartImportWizard from '../components/metadata/SmartImportWizard.vue'
import RowFilterOptionSelect from '../components/metadata/RowFilterOptionSelect.vue'
import PermissionVariableMenu from '../components/metadata/PermissionVariableMenu.vue'
import { useUser } from '../composables/useUser'
import { useToast } from '../composables/useToast'

const { isAdmin, hasPermission } = useUser()

const router = useRouter()
const datasets = ref<Dataset[]>([])
const loading = ref(false)
const showCreateModal = ref(false)
const showCreateMenu = ref(false)
const showImportModal = ref(false)
const showSpecModal = ref(false)
const showDeleteModal = ref(false)
const showSyncConfirmModal = ref(false)
const showTestModal = ref(false) 
const showEditDatasetModal = ref(false)
const showPermConfigModal = ref(false)
const showPermHelpModal = ref(false)
const showUserSelectorModal = ref(false)
const showRoleSelectorModal = ref(false)
const userSearchQuery = ref('')
const roleSearchQuery = ref('')
const activeSpecTab = ref('concept')
const deletingDataset = ref<{id: number, name: string} | null>(null)
const syncingDataset = ref<any>(null)
const editingDataset = ref<any>(null)
const configuringDataset = ref<any>(null)
const activePolicyTab = ref<'visual' | 'user' | 'role' | 'default' | 'json'>('user')
const allRoles = ref<Role[]>([])
const allUsers = ref<User[]>([])
const datasetColumns = ref<{physical_name: string, term: string}[]>([])
const datasetTables = ref<any[]>([])
const activeMenuPath = ref<string | null>(null) // Tracks 'role-idx-type', e.g., 'role-0-fields'

// 过滤计算属性
const filteredUsers = computed(() => {
  if (!userSearchQuery.value) return allUsers.value
  const query = userSearchQuery.value.toLowerCase()
  return allUsers.value.filter(user => 
    (user.real_name && user.real_name.toLowerCase().includes(query)) ||
    (user.user_name && user.user_name.toLowerCase().includes(query)) ||
    user.id.toString().includes(query)
  )
})

const filteredRoles = computed(() => {
  if (!roleSearchQuery.value) return allRoles.value
  const query = roleSearchQuery.value.toLowerCase()
  return allRoles.value.filter(role => 
    (role.name && role.name.toLowerCase().includes(query)) ||
    (role.code && role.code.toLowerCase().includes(query))
  )
})

const toggleMenu = (path: string) => {
  if (activeMenuPath.value === path) activeMenuPath.value = null
  else activeMenuPath.value = path
}

const closeMenus = () => {
  activeMenuPath.value = null
}

const visualRules = ref({
  user_overrides: {} as any,
  role_policies: {} as any,
  default_policy: [] as any[]
})

const OPERATORS = [
  { label: '等于 (=)', value: '=' },
  { label: '不等于 (!=)', value: '!=' },
  { label: '包含 (IN)', value: 'IN' },
  { label: '模糊匹配 (LIKE)', value: 'LIKE' },
  { label: '大于 (>)', value: '>' },
  { label: '小于 (<)', value: '<' }
]

const BUILTIN_USER_VARIABLES = [
  { label: '用户 ID', value: '{user.id}' },
  { label: '用户名', value: '{user.user_name}' },
  { label: '真实姓名', value: '{user.real_name}' },
  { label: '部门代码', value: '{user.dept_code}' },
  { label: '组织全路径', value: '{user.org_path}' },
  { label: '角色', value: '{user.role}' },
] as const

const CORE_USER_DIMENSION_KEYS = new Set([
  'id', 'user_id', 'user_name', 'real_name', 'dept_code', 'org_path', 'role', 'extra_data',
])

const parseUserExtraDataObject = (raw: unknown): Record<string, unknown> => {
  if (!raw) return {}
  if (typeof raw === 'string') {
    const text = raw.trim()
    if (!text) return {}
    try {
      const parsed = JSON.parse(text)
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {}
    } catch {
      return {}
    }
  }
  if (typeof raw === 'object' && !Array.isArray(raw)) {
    return raw as Record<string, unknown>
  }
  return {}
}

const formatExtraDataSample = (value: unknown): string | undefined => {
  if (value == null) return undefined
  const text = typeof value === 'object' ? JSON.stringify(value) : String(value).trim()
  if (!text) return undefined
  return text.length > 40 ? `${text.slice(0, 37)}...` : text
}

const EXTRA_DATA_USER_VARIABLES = computed(() => {
  const keySamples = new Map<string, string | undefined>()

  const collectFromExtraData = (extraData: unknown) => {
    const extra = parseUserExtraDataObject(extraData)
    for (const [key, value] of Object.entries(extra)) {
      if (CORE_USER_DIMENSION_KEYS.has(key)) continue
      if (!keySamples.has(key)) {
        keySamples.set(key, formatExtraDataSample(value))
      }
    }
  }

  for (const user of allUsers.value) {
    collectFromExtraData(user.extra_data)
  }

  // 兜底：当前登录用户 localStorage 中的 extra_data
  try {
    const userInfoStr = localStorage.getItem('user_info')
    if (userInfoStr) {
      collectFromExtraData(JSON.parse(userInfoStr)?.extra_data)
    }
  } catch {
    // ignore
  }

  return Array.from(keySamples.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, sample]) => ({
      label: key,
      value: `{user.${key}}`,
      sample,
    }))
})
const syncingId = ref<number | null>(null)
const enhancing = ref(false)
const defaultDataSource = ref('default_clickhouse')
const viewMode = ref<'grid' | 'list'>(localStorage.getItem('metadata_view_mode') as 'grid' | 'list' || 'grid')
const { showToast } = useToast()

// JSON视图相关变量
const jsonEditorContent = ref('')
const jsonParseError = ref('')
const parsedJSON = ref<any>(null)
const validationResult = ref<any>(null)

// JSON编辑器变化处理
const onJSONEditorChange = () => {
  try {
    parsedJSON.value = JSON.parse(jsonEditorContent.value)
    jsonParseError.value = ''
  } catch (e: any) {
    jsonParseError.value = e.message
    parsedJSON.value = null
  }
}

// 将 JSON 转换为可视化结构
const syncJsonToVisual = (json: any) => {
  visualRules.value = {
    user_overrides: json?.user_overrides || {},
    role_policies: json?.role_policies || {},
    default_policy: json?.default_policy || []
  }
}

// 将可视化结构转回 JSON
const syncVisualToJson = () => {
  const cleanObj = (obj: any): any => {
    if (Array.isArray(obj)) {
      return obj.map(cleanObj)
    } else if (obj !== null && typeof obj === 'object') {
      const newObj: any = {}
      for (const key in obj) {
        if (!key.startsWith('_')) {
          newObj[key] = cleanObj(obj[key])
        }
      }
      return newObj
    }
    return obj
  }
  return cleanObj(JSON.parse(JSON.stringify(visualRules.value)))
}

// 复制JSON到剪贴板
const copyJSON = async () => {
  try {
    await navigator.clipboard.writeText(jsonEditorContent.value)
    showToast('JSON配置已复制到剪贴板', 'success')
  } catch (e) {
    showToast('复制失败，请手动复制', 'error')
  }
}

// 从可视化同步到JSON编辑器
const syncFromVisual = (silent = false) => {
  const jsonData = syncVisualToJson()
  jsonEditorContent.value = JSON.stringify(jsonData, null, 2)
  onJSONEditorChange()
  if (!silent) showToast('已从可视化编辑器同步配置', 'success')
}

const tableSelectOptions = computed(() =>
  datasetTables.value.map((table: any) => ({
    value: table.physical_name,
    label: table.physical_name,
    remark: table.term || table.description || '',
  })),
)

const getColumnSelectOptions = (tableName: string) => {
  const table = datasetTables.value.find((item: any) => item.physical_name === tableName)
  return (table?.columns || []).map((column: any) => ({
    value: column.physical_name,
    label: column.physical_name,
    remark: column.term || column.description || '',
  }))
}

// 规则校验功能
const validateRules = async () => {
  try {
    const config = syncVisualToJson()
    
    // 模拟后端校验逻辑
    const validation = await validatePermissionConfig(config)
    
    validationResult.value = validation
    
    if (validation.isValid) {
      showToast('规则校验通过！', 'success')
    } else {
      showToast('规则校验失败，请查看详细信息', 'error')
    }
  } catch (e: any) {
    validationResult.value = {
      isValid: false,
      message: '校验过程中发生错误',
      details: [{ type: 'error', message: e.message }]
    }
    showToast('校验失败', 'error')
  }
}

// 追加构建器条件
const appendBuilderCondition = (rule: any) => {
  if (!rule._builder_table || !rule._builder_field || !rule._builder_op || !rule._builder_val) {
    showToast('请先完整填写规则构建器的所有字段', 'warning')
    return
  }
  
  const condition = `${rule._builder_table}.${rule._builder_field} ${rule._builder_op} ${rule._builder_val}`
  
  if (rule.condition) {
    rule.condition = `${rule.condition} AND ${condition}`
  } else {
    rule.condition = condition
  }
  
  // 清空构建器字段
  rule._builder_table = ''
  rule._builder_field = ''
  rule._builder_op = ''
  rule._builder_val = ''
  
  showToast('条件已追加到规则中', 'success')
}

// 模拟后端校验API（实际中应该调用真实API）
const validatePermissionConfig = async (config: any) => {
  // 这里模拟后端的校验逻辑
  const details: any[] = []
  let isValid = true
  
  // 1. 检查JSON结构
  if (!config || typeof config !== 'object') {
    isValid = false
    details.push({ type: 'error', message: '配置必须是一个有效的JSON对象' })
    return { isValid, message: '配置结构错误', details }
  }
  
  // 2. 检查各个策略类型
  const checkRules = (rules: any[], type: string) => {
    if (!Array.isArray(rules)) {
      isValid = false
      details.push({ type: 'error', message: `${type}策略必须是一个数组` })
      return
    }
    
    rules.forEach((rule, idx) => {
      if (!rule.condition || typeof rule.condition !== 'string') {
        isValid = false
        details.push({ type: 'error', message: `${type}策略第${idx + 1}条规则缺少condition字段` })
      } else {
        // 检查condition语法
        try {
          // 简单的语法检查
          if (rule.condition.includes('{user.') && !rule.condition.includes('}')) {
            details.push({ type: 'warning', message: `${type}策略第${idx + 1}条规则的变量占位符可能不完整` })
          }
          
          // 检查SQL注入风险
          if (rule.condition.toLowerCase().includes('drop ') || 
              rule.condition.toLowerCase().includes('delete ') ||
              rule.condition.toLowerCase().includes('truncate ')) {
            isValid = false
            details.push({ type: 'error', message: `${type}策略第${idx + 1}条规则包含不安全的SQL操作` })
          }
        } catch (e) {
          details.push({ type: 'warning', message: `${type}策略第${idx + 1}条规则可能存在语法问题` })
        }
      }
    })
  }
  
  // 检查用户例外策略
  if (config.user_overrides) {
    Object.entries(config.user_overrides).forEach(([userId, rules]: [string, any]) => {
      if (!/^\d+$/.test(userId)) {
        details.push({ type: 'warning', message: `用户ID "${userId}" 不是有效的数字格式` })
      }
      checkRules(rules, `用户${userId}`)
    })
  }
  
  // 检查角色策略
  if (config.role_policies) {
    Object.entries(config.role_policies).forEach(([roleName, rules]: [string, any]) => {
      checkRules(rules, `角色${roleName}`)
    })
  }
  
  // 检查默认策略
  if (config.default_policy) {
    checkRules(config.default_policy, '默认')
  }
  
  return {
    isValid,
    message: isValid ? '配置校验通过，符合优化版SQL重写引擎要求' : '配置存在问题，请根据提示修正',
    details
  }
}



const fetchRoles = async () => {
  try {
    const res = await portalApi.getRoles({ size: 1000 })
    // Flexible parsing for StandardResponse or direct items
    allRoles.value = (res.data as any)?.data?.items || (res.data as any)?.items || []
  } catch (e) {
    console.error('Failed to fetch roles', e)
  }
}

const fetchUsers = async () => {
  try {
    const res = await portalApi.getUsers({ size: 1000 })
    // Flexible parsing for StandardResponse or direct items
    allUsers.value = (res.data as any)?.data?.items || (res.data as any)?.items || []
  } catch (e) {
    console.error('Failed to fetch users', e)
  }
}

const fetchDatasetColumns = async (id: number) => {
  try {
    const res = await metadataApi.getDataset(id)
    datasetTables.value = res.data.tables || []
    const cols: {physical_name: string, term: string}[] = []
    res.data.tables?.forEach(table => {
      table.columns.forEach(col => {
        cols.push({ physical_name: col.physical_name, term: col.term })
      })
    })
    // Unique by name
    const uniqueCols = cols.filter((v, i, a) => a.findIndex(t => t.physical_name === v.physical_name) === i)
    datasetColumns.value = uniqueCols
  } catch (e) {
    console.error('Failed to fetch dataset columns', e)
  }
}

const addNewRolePolicyDirect = () => {
  showRoleSelectorModal.value = true
}

const addNewUserOverrideDirect = () => {
  showUserSelectorModal.value = true
}

const selectRoleFromModal = (role: any) => {
  if (!visualRules.value.role_policies[role.code]) {
    visualRules.value.role_policies = {
      ...visualRules.value.role_policies,
      [role.code]: [{ condition: '' }]
    }
  }
  showRoleSelectorModal.value = false
  roleSearchQuery.value = '' // 清空搜索
  visualRules.value = { ...visualRules.value }
}

const selectUserFromModal = (user: any) => {
  if (!visualRules.value.user_overrides[user.id]) {
    visualRules.value.user_overrides = {
      ...visualRules.value.user_overrides,
      [user.id]: [{ condition: '' }]
    }
  }
  showUserSelectorModal.value = false
  userSearchQuery.value = '' // 清空搜索
  visualRules.value = { ...visualRules.value }
}


const openPermConfigModal = (ds: any) => {
  configuringDataset.value = JSON.parse(JSON.stringify(ds))
  // Initialize default if missing
  if (!configuringDataset.value.row_filter_config) {
    configuringDataset.value.row_filter_config = {
      user_overrides: {},
      role_policies: {},
      default_policy: []
    }
  }
  syncJsonToVisual(configuringDataset.value.row_filter_config)
  // 初始化JSON编辑器内容
  jsonEditorContent.value = JSON.stringify(configuringDataset.value.row_filter_config, null, 2)
  onJSONEditorChange()
  activePolicyTab.value = 'user' // 默认打开用户Tab
  closeMenus() // Reset any open selectors
  showPermConfigModal.value = true
  
  // Load data for selectors
  fetchRoles()
  fetchUsers()
  fetchDatasetColumns(ds.id)
}

const handleSavePermConfig = async () => {
  if (!configuringDataset.value) return
  
  let config = {}
  
  // 只有在启用权限时才进行规则同步和校验
  if (configuringDataset.value.enable_data_perm) {
    config = syncVisualToJson()
    
    // 检查是否存在空 condition
    const checkEmptyConditions = (rules: any[]): boolean => {
      if (!Array.isArray(rules)) return false
      return rules.some(r => !r.condition || r.condition.trim() === '')
    }

    let hasEmpty = false
    if ((config as any).user_overrides) {
      for (const rules of Object.values((config as any).user_overrides)) {
        if (checkEmptyConditions(rules as any[])) { hasEmpty = true; break; }
      }
    }
    if (!hasEmpty && (config as any).role_policies) {
      for (const rules of Object.values((config as any).role_policies)) {
        if (checkEmptyConditions(rules as any[])) { hasEmpty = true; break; }
      }
    }
    if (!hasEmpty && (config as any).default_policy) {
      if (checkEmptyConditions((config as any).default_policy)) hasEmpty = true
    }

    if (hasEmpty) {
      showToast('保存失败：发现无效规则，所有规则的条件(Condition)均不能为空', 'warning')
      return
    }
  }

  try {
    configuringDataset.value.row_filter_config = config

    await metadataApi.updateDataset(configuringDataset.value.id, {
      enable_data_perm: configuringDataset.value.enable_data_perm,
      row_filter_config: configuringDataset.value.row_filter_config
    })
    showPermConfigModal.value = false
    configuringDataset.value = null
    fetchDatasets()
    showToast('权限配置已保存', 'success')
  } catch (e) {
    console.error('Save permissions failed', e)
    showToast('保存权限配置失败', 'error')
  }
}

watch(viewMode, (newMode) => {
  localStorage.setItem('metadata_view_mode', newMode)
})

// Search and Filter
const searchQuery = ref('')
type StatusFilter = 'all' | 'active' | 'inactive'
type DatasetSortField = 'display_name' | 'status' | 'table_count' | 'rag_sync_status' | 'updated_at'
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
  } catch (e: any) {
    console.error('Failed to fetch datasets', e)
    const errorMessage = e.response?.data?.detail || e.message || '获取数据集列表失败'
    alert(errorMessage)
    datasets.value = []
  } finally {
    loading.value = false
  }
}

const handleCreate = async () => {
  try {
    // Parse JSON
    if (rowFilterConfigStr.value.trim()) {
      try {
        newDataset.value.row_filter_config = JSON.parse(rowFilterConfigStr.value)
      } catch (e) {
        alert('权限配置 JSON 格式错误')
        return
      }
    }

    // If data_source is empty, use the system default
    if (!newDataset.value.data_source) {
        newDataset.value.data_source = defaultDataSource.value
    }
    await metadataApi.createDataset(newDataset.value)
    showCreateModal.value = false
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
        alert('权限配置 JSON 格式错误')
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
    alert('状态更新失败')
  }
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
    alert(`删除失败: ${e.message || '未知错误'}`)
  }
}

const openSyncModal = (ds: any) => {
  if (syncingId.value) return
  syncingDataset.value = ds
  showSyncConfirmModal.value = true
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
      // No alert needed, status badge will update
    } else {
      alert(res.data.message || '同步失败')
    }
  } catch (e: any) {
    console.error('Sync failed', e)
    alert('启动同步失败: ' + (e.response?.data?.message || e.message))
  } finally {
    syncingId.value = null
    syncingDataset.value = null
    // Refresh to get potential instant status updates
    setTimeout(fetchDatasets, 2000)
  }
}

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

const getDatasetEmoji = (name: string) => {
  const emojis = ['📊', '📈', '💿', '🗄️', '🧠', '🧊', '🌊', '⚡', '📅', '🛒', '👥', '🔗', '📦', '🏷️', '💎']
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash)
  }
  const index = Math.abs(hash) % emojis.length
  return emojis[index]
}
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

const ragflowApiUrl = computed(() => ragflowConfig.value?.api_url || '未配置')

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

const checkRagFlowConnectivity = async () => {
  engineStatus.value = 'checking'
  errorMessage.value = ''
  try {
    await axios.get('/api/portal/ragflow/datasets', { params: { page_size: 1 } })
    engineStatus.value = 'connected'
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
    await fetchRagFlowConfig()
    if (!isLocalMode.value) {
        checkRagFlowConnectivity()
    } else {
        engineStatus.value = 'connected'
    }
})
</script>

<template>
  <div class="space-y-5" @click="showCreateMenu = false">
    <!-- Header：窄屏压缩工具栏；列表视图移动端可横滑查看 -->
    <div class="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
      <div class="min-w-0">
        <div class="flex items-center gap-2">
          <h1 class="text-2xl font-bold tracking-normal text-gray-900">元数据管理</h1>
          <button
            type="button"
            class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-gray-200 bg-white text-blue-600 shadow-sm transition-colors hover:border-blue-300 hover:bg-blue-50"
            title="元数据规范"
            @click="showSpecModal = true"
          >
            <span class="text-sm font-bold">?</span>
          </button>
        </div>
        <p class="mt-0.5 truncate text-sm text-gray-500">管理业务数据集及其表结构语义</p>
      </div>

      <div class="flex w-full flex-col gap-2.5 sm:w-auto sm:flex-row sm:flex-wrap sm:items-center sm:gap-3 lg:justify-end">
        <div class="relative w-full sm:w-72">
          <span class="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3">
            <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
          </span>
          <input
            v-model="searchQuery"
            type="text"
            placeholder="搜索数据集名称、ID或描述..."
            class="w-full rounded-lg border border-gray-300 bg-white py-2 pl-9 pr-4 text-sm shadow-sm transition-all focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>

        <div class="flex items-center gap-2">
          <select
            v-model="statusFilter"
            class="min-w-0 flex-1 rounded-lg border border-gray-300 bg-white px-2.5 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 sm:w-auto sm:flex-none"
          >
            <option value="all">状态：全部</option>
            <option value="active">已启用</option>
            <option value="inactive">已禁用</option>
          </select>

          <div
            tabindex="0"
            class="relative flex shrink-0 cursor-pointer items-center gap-2 rounded-lg border bg-white px-2.5 py-1.5 text-xs shadow-sm transition-colors group"
            :class="{
              'border-blue-200 text-blue-700': !isLocalMode && engineStatus === 'checking',
              'border-emerald-200 text-emerald-700': isLocalMode || engineStatus === 'connected',
              'border-amber-200 text-amber-700': !isLocalMode && engineStatus === 'disconnected'
            }"
          >
            <span
              class="inline-block h-2 w-2 rounded-full"
              :class="{
                'animate-pulse bg-blue-500': !isLocalMode && engineStatus === 'checking',
                'bg-emerald-500': isLocalMode || engineStatus === 'connected',
                'bg-amber-500': !isLocalMode && engineStatus === 'disconnected'
              }"
            />
            <span class="whitespace-nowrap font-medium">引擎 {{ engineStatusText }}</span>
            <span class="pointer-events-none absolute right-0 top-full z-50 mt-2 hidden w-56 rounded-lg bg-slate-900 p-2.5 text-left font-sans text-xs font-normal text-white shadow-xl group-hover:block group-focus-within:block">
              <div class="mb-1 border-b border-white/10 pb-1 font-medium">知识库引擎信息</div>
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
            </span>
          </div>

          <div
            v-if="hasActiveFilters"
            class="hidden shrink-0 whitespace-nowrap px-1 text-xs text-gray-400 sm:block"
          >
            {{ displayDatasets.length }} / {{ datasets.length }}
          </div>
        </div>

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
              @click="showCreateModal = true; showCreateMenu = false"
            >
              <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
              </svg>
              新建数据集
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
              title="更多创建方式"
              @click="showCreateMenu = !showCreateMenu"
            >
              <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
              </svg>
            </button>
          </div>
          <div
            v-if="showCreateMenu"
            class="absolute right-0 z-20 mt-1.5 w-48 rounded-xl border border-gray-200 bg-white py-1 shadow-lg"
          >
            <button
              v-if="hasPermission('element:metadata:edit')"
              type="button"
              class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50"
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
              class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50"
              @click="showCreateMenu = false; showImportModal = true"
            >
              <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" />
              </svg>
              智能导入 (DDL)
            </button>
          </div>
        </div>
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

    <!-- Loading State -->
    <div v-if="loading" class="space-y-4">
      <!-- Skeleton Loader -->
      <div v-for="i in 3" :key="i" class="h-32 bg-gray-100 rounded-xl animate-pulse"></div>
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
        v-for="ds in displayDatasets" 
        :key="ds.id"
        class="bg-white rounded-xl border border-gray-200 hover:border-blue-300 hover:shadow-md transition-all duration-200 cursor-pointer group overflow-hidden flex flex-col relative"
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
              {{ getDatasetEmoji(ds.name) }}
            </div>
            <div class="flex-1 min-w-0">
              <h3 class="text-sm font-bold text-gray-900 group-hover:text-blue-600 transition-colors truncate">
                {{ ds.display_name }}
              </h3>
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
          </div>

          <p class="mt-2.5 text-xs text-gray-500 line-clamp-2 leading-relaxed min-h-[2.5rem]">
            {{ ds.description || '暂无描述' }}
          </p>

          <div class="mt-2.5 flex items-center justify-between gap-2 text-[10px] text-gray-400">
            <span v-if="ds.updated_at" class="inline-flex items-center gap-1 truncate">
              <svg class="w-3 h-3 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
              {{ new Date(ds.updated_at).toLocaleString() }}
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
        
        <div class="px-4 py-2.5 border-t border-gray-100 flex justify-between items-center bg-gray-50/60">
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
          <div class="flex items-center gap-0.5" @click.stop>
            <button 
              v-if="hasPermission('element:metadata:sync')"
              @click.stop="isEngineReady && ds.status === 1 && !isLocalMode && openSyncModal(ds)" 
              class="transition-colors p-1.5 rounded-md"
              :class="{ 
                 'text-gray-400 hover:text-indigo-500 hover:bg-white': ds.status === 1 && isEngineReady && !isLocalMode,
                 'text-gray-300 cursor-not-allowed opacity-50': ds.status !== 1 || !isEngineReady || isLocalMode,
                 'pointer-events-none': syncingId === ds.id || ds.rag_sync_status === 1 
              }"
              :disabled="!isEngineReady || ds.status !== 1 || isLocalMode"
              :title="isLocalMode ? '本地向量模式下已自动同步，无需手动上传' : (!isEngineReady ? 'RAGFlow 服务未就绪' : (ds.status === 1 ? '同步到 RAGFlow' : '数据集已禁用，无法同步'))"
            >
              <svg v-if="syncingId === ds.id || ds.rag_sync_status === 1" class="w-4 h-4 animate-spin text-indigo-500" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 16h5v5"/></svg>
              <svg v-else class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><path d="M12 16.5V9.75m0 0 3 3m-3-3-3 3M6.75 19.5a4.5 4.5 0 0 1-1.41-8.775 5.25 5.25 0 0 1 10.233-2.33 3 3 0 0 1 3.758 3.848A3.752 3.752 0 0 1 18 19.5H6.75Z"/></svg>
            </button>
            <button 
              v-if="hasPermission('element:metadata:edit')"
              @click.stop="openPermConfigModal(ds)" 
              class="text-gray-400 hover:text-emerald-500 transition-colors p-1.5 hover:bg-white rounded-md"
              title="数据权限配置"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>
            </button>
            <button 
              v-if="hasPermission('element:metadata:edit')"
              @click.stop="openEditDatasetModal(ds)" 
              class="text-gray-400 hover:text-blue-500 transition-colors p-1.5 hover:bg-white rounded-md"
              title="编辑数据集"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
            </button>
            <button 
              v-if="hasPermission('element:metadata:delete')"
              @click.stop="openDeleteModal(ds)" 
              class="text-gray-400 hover:text-red-500 transition-colors p-1.5 hover:bg-white rounded-md"
              title="删除数据集"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
            </button>
            <span class="text-blue-600 text-[11px] font-medium flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity ml-0.5">
              进入 <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- List View：窄屏允许横滑，避免 overflow-hidden 裁切列内容 -->
    <div v-else class="overflow-x-auto rounded-xl border border-gray-100 bg-white shadow-sm" style="-webkit-overflow-scrolling: touch;">
       <div class="min-w-[720px]">
       <div class="grid grid-cols-12 gap-4 border-b border-gray-100 bg-gray-50 px-6 py-3 text-xs font-bold uppercase tracking-wider text-gray-500">
          <button type="button" class="col-span-3 inline-flex items-center gap-1 text-left hover:text-gray-700 transition-colors" @click="toggleSort('display_name')">
            <span>数据集 (Dataset)</span>
            <svg v-if="sortField === 'display_name'" class="w-3.5 h-3.5 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path v-if="sortDirection === 'asc'" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7" />
              <path v-else stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
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
          <button type="button" class="col-span-2 inline-flex items-center gap-1 text-left hover:text-gray-700 transition-colors" @click="toggleSort('rag_sync_status')">
            <span>RAG 状态</span>
            <svg v-if="sortField === 'rag_sync_status'" class="w-3.5 h-3.5 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path v-if="sortDirection === 'asc'" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7" />
              <path v-else stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          <button type="button" class="col-span-1 inline-flex items-center gap-1 text-left hover:text-gray-700 transition-colors" @click="toggleSort('updated_at')">
            <span>更新时间</span>
            <svg v-if="sortField === 'updated_at'" class="w-3.5 h-3.5 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path v-if="sortDirection === 'asc'" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 15l7-7 7 7" />
              <path v-else stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
            </svg>
          </button>
          <div class="col-span-2 text-right">操作</div>
       </div>
       <div class="divide-y divide-gray-100">
          <div 
             v-for="ds in displayDatasets" 
             :key="ds.id" 
             class="grid grid-cols-12 gap-4 px-6 py-4 items-center hover:bg-gray-50 transition-colors cursor-pointer group"
             @click="goToTables(ds.id)"
          >
             <!-- Name & Meta -->
             <div class="col-span-3 flex items-center gap-3">
                <div class="w-10 h-10 rounded-lg bg-gray-50 flex-shrink-0 flex items-center justify-center text-xl border border-gray-100">
                   {{ getDatasetEmoji(ds.name) }}
                </div>
                <div class="min-w-0">
                   <div class="flex items-center gap-2">
                      <h3 class="text-sm font-bold text-gray-900 group-hover:text-primary transition-colors truncate">{{ ds.display_name }}</h3>
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

             <!-- RAG Status -->
             <div class="col-span-2">
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

             <!-- Updated -->
             <div class="col-span-1 text-[10px] text-gray-400 font-mono">
                {{ ds.updated_at ? new Date(ds.updated_at).toLocaleDateString() : '-' }}
             </div>

             <!-- Actions -->
             <div class="col-span-2 flex justify-end items-center gap-1.5">
                <!-- Action Buttons: Admin or specific permission -->
                 <button 
                    v-if="hasPermission('element:metadata:sync')"
                    @click.stop="isEngineReady && ds.status === 1 && !isLocalMode && openSyncModal(ds)" 
                    class="p-1.5 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 rounded transition-colors"
                    :class="{ 
                      'opacity-30 cursor-not-allowed': ds.status !== 1 || !isEngineReady || isLocalMode,
                      'pointer-events-none': syncingId === ds.id || ds.rag_sync_status === 1
                    }"
                    :disabled="!isEngineReady || ds.status !== 1 || isLocalMode"
                    :title="isLocalMode ? '本地向量模式下已自动同步，无需手动上传' : (!isEngineReady ? 'RAGFlow 服务未就绪' : (ds.status === 1 ? '同步 RAG' : '已禁用一线'))"
                 >
                    <svg v-if="syncingId === ds.id || ds.rag_sync_status === 1" class="w-4 h-4 animate-spin text-indigo-500" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 16h5v5"/></svg>
                    <svg v-else class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><path d="M12 16.5V9.75m0 0 3 3m-3-3-3 3M6.75 19.5a4.5 4.5 0 0 1-1.41-8.775 5.25 5.25 0 0 1 10.233-2.33 3 3 0 0 1 3.758 3.848A3.752 3.752 0 0 1 18 19.5H6.75Z"/></svg>
                 </button>
                <button 
                   v-if="hasPermission('element:metadata:edit')"
                   @click.stop="openPermConfigModal(ds)" 
                   class="p-1.5 text-gray-400 hover:text-emerald-600 hover:bg-emerald-50 rounded transition-colors"
                   title="数据权限"
                >
                   <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>
                </button>
                <button 
                   v-if="hasPermission('element:metadata:edit')"
                   @click.stop="openEditDatasetModal(ds)" 
                   class="p-1.5 text-gray-400 hover:text-blue-600 hover:bg-blue-50 rounded transition-colors"
                   title="编辑"
                >
                   <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
                </button>
                <button 
                   v-if="hasPermission('element:metadata:delete')"
                   @click.stop="openDeleteModal(ds)" 
                   class="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded transition-colors"
                   title="删除"
                >
                   <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                </button>
                
                <span class="text-gray-400 text-sm font-medium flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                  查看 <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>
                </span>
             </div>
          </div>
       </div>
       </div>
    </div>

    <!-- Delete Confirmation Modal -->
    <div v-if="showDeleteModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="showDeleteModal = false">
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

    <!-- Sync Confirmation Modal -->
    <div v-if="showSyncConfirmModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="showSyncConfirmModal = false">
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

    <!-- Test Retrieval Modal -->
    <div v-if="showTestModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="showTestModal = false">
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
                   class="flex-1 bg-gray-50 border border-gray-200 rounded-xl px-4 py-3 focus:outline-none focus:ring-2 focus:ring-amber-500 transition-all"
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

    <!-- Spec Modal -->
    <div v-if="showSpecModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="showSpecModal = false">
      <div class="bg-white rounded-xl shadow-2xl w-full max-w-5xl h-[85vh] flex flex-col overflow-hidden border border-gray-100 animate-fade-in-up">
        <!-- Header -->
        <div class="p-6 border-b border-gray-100 flex justify-between items-center bg-purple-50/30">
          <div class="flex items-center gap-3">
             <div class="w-10 h-10 rounded-lg bg-purple-100 text-purple-600 flex items-center justify-center border border-purple-200">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19.428 15.428a2 2 0 00-1.022-.547l-2.384-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z"/></svg>
             </div>
             <div>
               <h2 class="text-xl font-bold text-gray-900">元数据设计规范 (Semantic Layer Spec)</h2>
               <p class="text-xs text-gray-500 font-medium">构建 AI 可理解的业务语义层，提升 Text-to-SQL 准确率。</p>
             </div>
          </div>
          <button @click="showSpecModal = false" class="text-gray-400 hover:text-gray-600 transition-colors">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>

        <!-- Tabs -->
        <div class="flex border-b border-gray-200 bg-white px-6">
           <button 
             v-for="tab in ['concept', 'structure', 'fields', 'practice']" 
             :key="tab"
             @click="activeSpecTab = tab"
             class="px-4 py-3 text-sm font-medium border-b-2 transition-colors capitalize"
             :class="activeSpecTab === tab ? 'border-purple-600 text-purple-700' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'"
           >
             {{ tab === 'concept' ? '核心理念 (Concepts)' : 
                tab === 'structure' ? 'YAML 结构 (Schema)' : 
                tab === 'fields' ? '字段规范 (Fields)' : '最佳实践 (Best Practice)' }}
           </button>
        </div>

        <!-- Content -->
        <div class="flex-1 overflow-y-auto p-8 bg-gray-50/50">
           
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
                 <h3 class="text-lg font-bold text-gray-900 mb-3">📂 文件组织策略</h3>
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
                 <h3 class="text-lg font-bold text-gray-900 mb-3">🤖 RAG 检索策略</h3>
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

    <!-- Create Modal -->
    <div v-if="showCreateModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
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
            <input v-model="newDataset.display_name" type="text" class="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="e.g. 南孜资源数据集">
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

            <label class="block text-sm font-medium text-gray-700 mb-1">标签</label>
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

    <!-- Smart Import Wizard -->
    <SmartImportWizard 
      :show="showImportModal" 
      @close="showImportModal = false"
      @saved="fetchDatasets"
    />

    <!-- Edit Dataset Modal -->
    <div v-if="showEditDatasetModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
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
            <input v-model="editingDataset.name" type="text" disabled class="w-full bg-gray-50 border border-gray-300 rounded-lg px-4 py-2 text-gray-500 cursor-not-allowed" title="物理名称不可修改">
          </div>
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">显示名称 (Display Name)</label>
            <input v-model="editingDataset.display_name" type="text" class="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="e.g. 南孜资源数据集">
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
                <svg v-else class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
                AI 辅助生成 (基于表信息)
              </button>
            </div>
            <textarea v-model="editingDataset.description" rows="3" class="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="描述该数据集包含的内容..."></textarea>
          </div>

          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">标签</label>
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

    <!-- Empty State -->
    <div v-if="!loading && datasets.length === 0" class="text-center py-24 bg-white rounded-xl border border-dashed border-gray-300">
      <div class="text-5xl mb-4 grayscale">📂</div>
      <h3 class="text-lg font-medium text-gray-900">暂无数据集</h3>
      <p class="text-gray-500 mt-1 mb-6">创建一个新的数据集来开始管理元数据。</p>
      <button v-if="hasPermission('element:metadata:edit')" @click="showCreateModal = true" class="text-primary font-medium hover:underline flex items-center justify-center gap-1 mx-auto">
        立即创建 <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
      </button>
    </div>

    <!-- Data Permission Configuration Modal -->
    <div v-if="showPermConfigModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="showPermConfigModal = false">
      <div class="bg-white rounded-2xl shadow-2xl w-full max-w-5xl overflow-hidden border border-gray-100 transform transition-all animate-fade-in-up">
        <!-- Header -->
        <div class="p-6 border-b border-gray-100 flex justify-between items-center bg-emerald-50/30">
          <div class="flex items-center gap-3">
             <div class="w-10 h-10 rounded-lg bg-emerald-100 text-emerald-600 flex items-center justify-center border border-emerald-200">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>
             </div>
             <div>
               <h2 class="text-xl font-bold text-gray-900">数据权限配置</h2>
               <p class="text-xs text-gray-500 font-medium">管理数据集 <b>{{ configuringDataset?.display_name }}</b> 的行级隔离策略。</p>
             </div>
          </div>
          <button @click="showPermConfigModal = false" class="text-gray-400 hover:text-gray-600 transition-colors">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>

        <!-- Body -->
        <div class="p-8 space-y-6 max-h-[75vh] overflow-y-auto custom-scrollbar" @click="closeMenus">
           <div class="flex items-center justify-between p-4 bg-gray-50 rounded-xl border border-gray-100">
             <div>
               <h4 class="text-sm font-bold text-gray-900">启用精细化权限校验</h4>
               <p class="text-xs text-gray-500 mt-0.5">
                 开启后，系统将强制执行下方定义的 SQL 过滤规则。
                 <button @click.prevent="showPermHelpModal = true" class="text-blue-600 hover:text-blue-700 hover:underline font-medium ml-1">帮助指引</button>
               </p>
             </div>
             <label class="relative inline-flex items-center cursor-pointer">
                <input type="checkbox" v-model="configuringDataset.enable_data_perm" class="sr-only peer">
                <div class="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-emerald-300 rounded-full peer peer-checked:after:translate-x-full rtl:peer-checked:after:-translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:start-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-emerald-600"></div>
              </label>
           </div>

           <div v-if="configuringDataset.enable_data_perm" class="space-y-4">
             <!-- Strategy Tabs -->
             <div class="flex items-center justify-between border-b border-gray-100 pb-2">
               <div class="flex p-1 bg-gray-100 rounded-xl w-fit">
                 <button 
                   @click.stop.prevent="activePolicyTab = (activePolicyTab === 'json' ? 'user' : activePolicyTab)"
                   class="px-5 py-2 text-xs font-bold rounded-lg transition-all flex items-center gap-2"
                   :class="activePolicyTab !== 'json' ? 'bg-white text-emerald-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'"
                 >
                   <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
                   可视化编辑
                 </button>
                 <button 
                   @click.stop.prevent="activePolicyTab = 'json'; syncFromVisual(true)"
                   class="px-5 py-2 text-xs font-bold rounded-lg transition-all flex items-center gap-2"
                   :class="activePolicyTab === 'json' ? 'bg-white text-emerald-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'"
                 >
                   <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"/></svg>
                   JSON 视图
                 </button>
               </div>

               <!-- Sub Strategy Tabs (L2, Only for Visual Mode) -->
               <div v-if="activePolicyTab !== 'json'" class="flex p-1 bg-white border border-gray-100 rounded-xl w-fit shadow-sm">
                 <button 
                   @click.stop.prevent="activePolicyTab = 'user'"
                   class="px-4 py-1.5 text-xs font-bold rounded-lg transition-all flex items-center gap-1"
                   :class="activePolicyTab === 'user' ? 'bg-emerald-50 text-emerald-700' : 'text-gray-400 hover:text-gray-600'"
                 >
                   用户例外
                   <span class="text-[10px] opacity-60" v-if="Object.keys(visualRules.user_overrides).length > 0">
                     ({{ Object.keys(visualRules.user_overrides).length }})
                   </span>
                 </button>
                 <button 
                   @click.stop.prevent="activePolicyTab = 'role'"
                   class="px-4 py-1.5 text-xs font-bold rounded-lg transition-all flex items-center gap-1"
                   :class="activePolicyTab === 'role' ? 'bg-emerald-50 text-emerald-700' : 'text-gray-400 hover:text-gray-600'"
                 >
                   角色策略
                   <span class="text-[10px] opacity-60" v-if="Object.keys(visualRules.role_policies).length > 0">
                     ({{ Object.keys(visualRules.role_policies).length }})
                   </span>
                 </button>
                 <button 
                   @click.stop.prevent="activePolicyTab = 'default'"
                   class="px-4 py-1.5 text-xs font-bold rounded-lg transition-all flex items-center gap-1"
                   :class="activePolicyTab === 'default' ? 'bg-emerald-50 text-emerald-700' : 'text-gray-400 hover:text-gray-600'"
                 >
                   默认规则
                   <span class="text-[10px] opacity-60" v-if="visualRules.default_policy.length > 0">
                     ({{ visualRules.default_policy.length }})
                   </span>
                 </button>
               </div>
             </div>

             <!-- Tab Content: Visual Editor -->
             <div v-if="activePolicyTab === 'visual'" class="space-y-4 min-h-[350px]">
                <h5 class="text-xs font-bold text-gray-400 uppercase tracking-wider">可视化规则编辑器</h5>
                
                <!-- Rule Builder Section -->
                <div class="space-y-4">
                  <!-- User Overrides -->
                  <div class="bg-amber-50/30 rounded-xl border border-amber-100 p-4">
                    <h6 class="text-sm font-bold text-amber-700 mb-3">用户例外策略</h6>
                    <div v-if="Object.keys(visualRules.user_overrides).length > 0" class="space-y-3">
                      <div v-for="(rules, userId) in visualRules.user_overrides" :key="userId" class="bg-white rounded-lg p-3 border border-amber-200">
                        <div class="flex items-center justify-between mb-2">
                          <span class="text-sm font-medium text-gray-700">用户ID: {{ userId }}</span>
                          <button @click="delete visualRules.user_overrides[userId]; visualRules = {...visualRules}" class="text-red-500 hover:text-red-700">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                          </button>
                        </div>
                        <div v-for="(rule, idx) in rules" :key="idx" class="mb-2">
                          <div class="flex items-end gap-3 p-3 bg-amber-50/80 rounded-xl border border-amber-200">
                            <div class="flex-1 space-y-1">
                              <label class="block text-[10px] text-amber-600 font-bold ml-1">目标表</label>
                              <RowFilterOptionSelect
                                v-model="rule._builder_table"
                                :options="tableSelectOptions"
                                placeholder="选择表..."
                                button-class="border-amber-300 focus:ring-amber-500"
                                menu-class="hover:bg-amber-50"
                              />
                            </div>
                            <div class="flex-1 space-y-1">
                              <label class="block text-[10px] text-amber-600 font-bold ml-1">字段</label>
                              <RowFilterOptionSelect
                                v-model="rule._builder_field"
                                :options="getColumnSelectOptions(rule._builder_table)"
                                placeholder="选择字段..."
                                :disabled="!rule._builder_table"
                                button-class="border-amber-300 focus:ring-amber-500"
                                menu-class="hover:bg-amber-50"
                              />
                            </div>
                            <div class="flex-1 space-y-1">
                              <label class="block text-[10px] text-amber-600 font-bold ml-1">操作符</label>
                              <select v-model="rule._builder_op" class="h-[2.625rem] bg-white border border-amber-300 rounded-lg px-2 text-xs focus:ring-1 focus:ring-amber-500 outline-none w-28 shadow-sm">
                                <option value="" disabled selected>操作符...</option>
                                <option v-for="op in OPERATORS" :key="op.value" :value="op.value">{{ op.label }}</option>
                              </select>
                            </div>
                            <div class="flex-1 space-y-1">
                              <label class="block text-[10px] text-amber-600 font-bold ml-1">过滤值/变量</label>
                              <div class="relative flex items-center gap-1">
                                <input v-model="rule._builder_val" class="flex-1 w-full h-[2.625rem] bg-white border border-amber-300 rounded-lg px-2 text-xs focus:ring-1 focus:ring-amber-500 outline-none min-w-[120px] shadow-sm" placeholder="e.g. {user.id}">
                                <button @click.stop="toggleMenu(`visual-user-${userId}-${idx}-vars`)" class="flex-shrink-0 h-[2.625rem] w-9 rounded-lg text-amber-600 hover:bg-amber-50 transition-colors border border-amber-100 bg-white flex items-center justify-center font-bold" title="插入内部变量">
                                  { }
                                </button>
                                <PermissionVariableMenu
                                  v-if="activeMenuPath === `visual-user-${userId}-${idx}-vars`"
                                  :builtin-variables="[...BUILTIN_USER_VARIABLES]"
                                  :extra-data-variables="EXTRA_DATA_USER_VARIABLES"
                                  accent="amber"
                                  @select="(v) => { rule._builder_val = v; closeMenus() }"
                                />
                              </div>
                            </div>
                            <button @click="appendBuilderCondition(rule)" class="bg-amber-700 hover:bg-amber-800 text-white w-10 h-[2.625rem] rounded-lg flex items-center justify-center transition-all shadow-md disabled:opacity-30 disabled:scale-100 active:scale-95" :disabled="!rule._builder_table || !rule._builder_field || !rule._builder_op || !rule._builder_val" title="追加条件">
                              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 6v6m0 0v6m0-6h6m-6 0h6"/></svg>
                            </button>
                            <button @click="rules.splice(idx, 1)" class="text-red-500 hover:text-red-700">
                              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                            </button>
                          </div>
                        </div>
                      </div>
                    </div>
                    <div v-else class="text-center py-8">
                      <div class="text-4xl mb-4 opacity-30">👤</div>
                      <p class="text-gray-500 font-medium">暂无用户例外策略</p>
                      <button @click="visualRules.user_overrides['new_user'] = [{ condition: '', _builder_table: '', _builder_field: '', _builder_op: '', _builder_val: '' }]; visualRules = {...visualRules}" class="text-amber-600 hover:text-amber-700 font-medium mt-2">
                        + 添加用户例外
                      </button>
                    </div>
                  </div>
                </div>
                
                <!-- Validation Section -->
                <div class="bg-blue-50 rounded-xl border border-blue-200 p-4">
                  <h6 class="text-sm font-bold text-blue-700 mb-3">规则校验</h6>
                  <div class="space-y-3">
                    <div class="flex items-center justify-between">
                      <span class="text-sm text-gray-700">校验当前配置</span>
                      <button @click="validateRules" class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg text-sm font-medium transition-all shadow-md">
                        🔍 校验规则
                      </button>
                    </div>
                    <div v-if="validationResult" class="mt-3 p-3 rounded-lg" :class="validationResult.isValid ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'">
                      <div class="flex items-center gap-2 mb-2">
                        <div class="w-6 h-6 rounded-full flex items-center justify-center" :class="validationResult.isValid ? 'bg-green-100 text-green-600' : 'bg-red-100 text-red-600'">
                          {{ validationResult.isValid ? '✓' : '✗' }}
                        </div>
                        <span class="font-medium" :class="validationResult.isValid ? 'text-green-700' : 'text-red-700'">
                          {{ validationResult.isValid ? '校验通过' : '校验失败' }}
                        </span>
                      </div>
                      <div class="text-sm" :class="validationResult.isValid ? 'text-green-600' : 'text-red-600'">
                        {{ validationResult.message }}
                      </div>
                      <div v-if="validationResult.details && validationResult.details.length > 0" class="mt-2">
                        <div class="text-xs text-gray-500 mb-1">详细信息:</div>
                        <div class="space-y-1">
                          <div v-for="(detail, idx) in validationResult.details" :key="idx" class="flex items-center gap-2 text-xs p-2 bg-gray-50 rounded">
                            <span :class="detail.type === 'error' ? 'text-red-600' : detail.type === 'warning' ? 'text-yellow-600' : 'text-blue-600'">
                              {{ detail.type === 'error' ? '❌' : detail.type === 'warning' ? '⚠️' : 'ℹ️' }}
                            </span>
                            <span class="flex-1">{{ detail.message }}</span>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
             </div>
             <div v-if="activePolicyTab === 'role'" class="space-y-4 min-h-[350px]">
                <h5 class="text-xs font-bold text-gray-400 uppercase tracking-wider">按角色配置策略</h5>
                
                <div class="space-y-4 max-h-[400px] overflow-y-auto pr-2 custom-scrollbar pb-32">
                  <div v-if="Object.keys(visualRules.role_policies).length > 0">
                    <div v-for="(rules, roleName) in visualRules.role_policies" :key="roleName" class="space-y-4">
                      <div class="p-4 bg-gray-50 rounded-xl border border-gray-200 relative group">
                        <div class="flex items-center gap-2 mb-3">
                          <div class="px-2 py-0.5 bg-emerald-100 text-emerald-700 text-[10px] font-bold rounded uppercase">角色</div>
                          <span class="text-sm font-bold text-gray-700">{{ roleName }}</span>
                          <span class="text-[10px] text-gray-400">({{ allRoles.find(r => r.code === roleName)?.name || '未找到角色名' }})</span>
                          <button @click="delete (visualRules.role_policies as any)[roleName]; visualRules = {...visualRules}" class="ml-auto text-gray-300 hover:text-red-500 transition-colors">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                          </button>
                        </div>
                        
                        <div v-for="(rule, idx) in rules" :key="idx" class="space-y-2 mb-2">
                          <div class="flex flex-col gap-2">
                            <div class="flex items-center gap-2">
                              <input v-model="rule.condition" class="flex-1 text-xs font-mono border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-emerald-500 outline-none" placeholder="e.g. dept_code = {user.dept_code}">
                              <button @click="rules.splice(idx, 1); if(rules.length===0) delete (visualRules.role_policies as any)[roleName]; visualRules = {...visualRules}" class="text-gray-300 hover:text-red-500">
                                 <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
                              </button>
                            </div>
                            
                            <!-- Rule Builder for Role Policy -->
                            <div class="flex items-end gap-3 p-3 bg-emerald-50/80 rounded-xl border border-emerald-200">
                              <div class="space-y-1">
                                <label class="block text-[10px] text-emerald-600 font-bold ml-1">目标表</label>
                                <RowFilterOptionSelect
                                  v-model="rule._builder_table"
                                  :options="tableSelectOptions"
                                  placeholder="选择数据表..."
                                  button-class="border-emerald-300 focus:ring-emerald-500"
                                  menu-class="hover:bg-emerald-50"
                                />
                              </div>
                              
                              <div class="space-y-1">
                                <label class="block text-[10px] text-emerald-600 font-bold ml-1">选择字段</label>
                                <RowFilterOptionSelect
                                  v-model="rule._builder_field"
                                  :options="getColumnSelectOptions(rule._builder_table)"
                                  placeholder="选择字段..."
                                  :disabled="!rule._builder_table"
                                  button-class="border-emerald-300 focus:ring-emerald-500"
                                  menu-class="hover:bg-emerald-50"
                                />
                              </div>

                              <div class="space-y-1">
                                <label class="block text-[10px] text-emerald-600 font-bold ml-1">操作符</label>
                                <select v-model="rule._builder_op" class="h-[2.625rem] bg-white border border-emerald-300 rounded-lg px-2 text-xs focus:ring-1 focus:ring-emerald-500 outline-none w-28 shadow-sm">
                                  <option value="" disabled selected>操作符...</option>
                                  <option v-for="op in OPERATORS" :key="op.value" :value="op.value">{{ op.label }}</option>
                                </select>
                              </div>
                              
                              <div class="flex-1 space-y-1">
                                <label class="block text-[10px] text-emerald-600 font-bold ml-1">过滤值/变量</label>
                                <div class="relative flex items-center gap-1">
                                  <input v-model="rule._builder_val" class="flex-1 w-full h-[2.625rem] bg-white border border-emerald-300 rounded-lg px-2 text-xs focus:ring-1 focus:ring-emerald-500 outline-none min-w-[120px] shadow-sm" placeholder="e.g. {user.id}">
                                  <!-- System variable dropdown trigger -->
                                  <button @click.stop="toggleMenu(`role-${roleName}-${idx}-vars`)" class="flex-shrink-0 h-[2.625rem] w-9 rounded-lg text-emerald-600 hover:bg-emerald-50 transition-colors border border-emerald-100 bg-white flex items-center justify-center font-bold" title="插入内部变量">
                                    { }
                                  </button>
                                  <PermissionVariableMenu
                                    v-if="activeMenuPath === `role-${roleName}-${idx}-vars`"
                                    :builtin-variables="[...BUILTIN_USER_VARIABLES]"
                                    :extra-data-variables="EXTRA_DATA_USER_VARIABLES"
                                    accent="emerald"
                                    @select="(v) => { rule._builder_val = v; closeMenus() }"
                                  />
                                </div>
                              </div>
                              
                              <button @click="appendBuilderCondition(rule)" class="bg-emerald-700 hover:bg-emerald-800 text-white w-10 h-[2.625rem] rounded-lg flex items-center justify-center transition-all shadow-md disabled:opacity-30 disabled:scale-100 active:scale-95" :disabled="!rule._builder_table || !rule._builder_field || !rule._builder_op || !rule._builder_val" title="追加条件">
                                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 6v6m0 0v6m0-6h6m-6 0H6"/></svg>
                              </button>
                            </div>

                            <button @click="rules.push({condition: ''})" class="text-[9px] text-emerald-600 font-bold hover:underline ml-auto">+ 追加条件 (AND)</button>
                          </div>
                        </div>
                      </div>
                    </div>
                    <div class="pt-2 flex justify-center">
                      <button @click="addNewRolePolicyDirect" class="text-[10px] text-emerald-600 font-bold flex items-center gap-1.5 hover:bg-emerald-50 px-4 py-2 rounded-lg transition-all border border-emerald-100 bg-white shadow-sm">
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
                        添加新角色策略
                      </button>
                    </div>
                  </div>
                  <div v-else class="text-center py-8">
                    <div class="w-full py-4 border-2 border-dashed border-emerald-200 rounded-xl text-emerald-400 hover:text-emerald-600 transition-all flex items-center justify-center gap-2 font-medium cursor-pointer" @click="addNewRolePolicyDirect">
                      <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
                      添加角色策略
                    </div>
                  </div>
                </div>
             </div>

             <!-- Tab Content: User Overrides -->
             <div v-if="activePolicyTab === 'user'" class="space-y-4 min-h-[350px]">
                <h5 class="text-xs font-bold text-gray-400 uppercase tracking-wider">特定用户例外 (优先级最高)</h5>
                <div class="space-y-4 max-h-[400px] overflow-y-auto pr-2 custom-scrollbar pb-32">
                  <div v-if="Object.keys(visualRules.user_overrides).length > 0">
                    <div v-for="(rules, uid) in visualRules.user_overrides" :key="uid" class="space-y-4">
                      <div class="p-4 bg-amber-50/30 rounded-xl border border-amber-100 relative group">
                        <div class="flex items-center gap-2 mb-3">
                          <div class="px-2 py-0.5 bg-amber-100 text-amber-700 text-[10px] font-bold rounded uppercase">用户</div>
                          <span class="text-xs font-bold text-amber-800">{{ allUsers.find(u => u.id === Number(uid))?.real_name || uid }} ({{ allUsers.find(u => u.id === Number(uid))?.user_name || 'ID: ' + uid }})</span>
                          <button @click="delete (visualRules.user_overrides as any)[uid]; visualRules = {...visualRules}" class="ml-auto text-gray-300 hover:text-red-500 transition-colors">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                          </button>
                        </div>
                        
                        <div v-for="(rule, idx) in rules" :key="idx" class="space-y-2 mb-2">
                           <div class="flex flex-col gap-2">
                              <div class="flex items-center gap-2">
                                <input v-model="rule.condition" class="flex-1 text-xs font-mono border border-amber-200 rounded-lg px-3 py-2 bg-white" placeholder="e.g. 1=1">
                                <button @click="rules.splice(idx, 1); if(rules.length===0) delete (visualRules.user_overrides as any)[uid]; visualRules = {...visualRules}" class="text-gray-300 hover:text-red-500">
                                   <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
                                </button>
                              </div>
                              
                              <!-- Rule Builder for User Override -->
                              <div class="flex items-end gap-3 p-3 bg-amber-50/80 rounded-xl border border-amber-200">
                                <div class="space-y-1">
                                  <label class="block text-[10px] text-amber-600 font-bold ml-1">目标表</label>
                                  <RowFilterOptionSelect
                                    v-model="rule._builder_table"
                                    :options="tableSelectOptions"
                                    placeholder="选择数据表..."
                                    button-class="border-amber-300 focus:ring-amber-500"
                                    menu-class="hover:bg-amber-50"
                                  />
                                </div>
                                
                                <div class="space-y-1">
                                  <label class="block text-[10px] text-amber-600 font-bold ml-1">选择字段</label>
                                  <RowFilterOptionSelect
                                    v-model="rule._builder_field"
                                    :options="getColumnSelectOptions(rule._builder_table)"
                                    placeholder="选择字段..."
                                    :disabled="!rule._builder_table"
                                    button-class="border-amber-300 focus:ring-amber-500"
                                    menu-class="hover:bg-amber-50"
                                  />
                                </div>

                                <div class="space-y-1">
                                  <label class="block text-[10px] text-amber-600 font-bold ml-1">操作符</label>
                                  <select v-model="rule._builder_op" class="h-[2.625rem] bg-white border border-amber-300 rounded-lg px-2 text-xs focus:ring-1 focus:ring-amber-500 outline-none w-28 shadow-sm">
                                    <option value="" disabled selected>操作符...</option>
                                    <option v-for="op in OPERATORS" :key="op.value" :value="op.value">{{ op.label }}</option>
                                  </select>
                                </div>
                                
                                <div class="flex-1 space-y-1">
                                  <label class="block text-[10px] text-amber-600 font-bold ml-1">过滤值/变量</label>
                                  <div class="relative flex items-center gap-1">
                                    <input v-model="rule._builder_val" class="flex-1 w-full h-[2.625rem] bg-white border border-amber-300 rounded-lg px-2 text-xs focus:ring-1 focus:ring-amber-500 outline-none min-w-[120px] shadow-sm" placeholder="e.g. {user.id}">
                                    <!-- System variable dropdown trigger -->
                                    <button @click.stop="toggleMenu(`user-${uid}-${idx}-vars`)" class="flex-shrink-0 h-[2.625rem] w-9 rounded-lg text-amber-600 hover:bg-amber-50 transition-colors border border-amber-100 bg-white flex items-center justify-center font-bold" title="插入内部变量">
                                      { }
                                    </button>
                                    <PermissionVariableMenu
                                      v-if="activeMenuPath === `user-${uid}-${idx}-vars`"
                                      :builtin-variables="[...BUILTIN_USER_VARIABLES]"
                                      :extra-data-variables="EXTRA_DATA_USER_VARIABLES"
                                      accent="amber"
                                      @select="(v) => { rule._builder_val = v; closeMenus() }"
                                    />
                                  </div>
                                </div>
                                
                                <button @click="appendBuilderCondition(rule)" class="bg-amber-700 hover:bg-amber-800 text-white w-10 h-[2.625rem] rounded-lg flex items-center justify-center transition-all shadow-md disabled:opacity-30 disabled:scale-100 active:scale-95" :disabled="!rule._builder_table || !rule._builder_field || !rule._builder_op || !rule._builder_val" title="追加条件">
                                  <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 6v6m0 0v6m0-6h6m-6 0H6"/></svg>
                                </button>
                              </div>

                              <button @click="rules.push({condition: ''})" class="text-[9px] text-amber-600 font-bold hover:underline ml-auto">+ 追加条件</button>
                           </div>
                        </div>
                      </div>
                    </div>
                    <div class="pt-2 flex justify-center">
                      <button @click="addNewUserOverrideDirect" class="text-[10px] text-amber-600 font-bold flex items-center gap-1.5 hover:bg-amber-50 px-4 py-2 rounded-lg transition-all border border-amber-100 bg-white shadow-sm">
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
                        添加用户例外
                      </button>
                    </div>
                  </div>
                  <div v-else class="text-center py-8">
                    <div class="w-full py-4 border-2 border-dashed border-amber-200 rounded-xl text-amber-400 hover:text-amber-600 transition-all flex items-center justify-center gap-2 font-medium cursor-pointer" @click="addNewUserOverrideDirect">
                      <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
                      添加用户例外
                    </div>
                  </div>
                </div>
             </div>

             <!-- Tab Content: Default Policy -->
             <div v-if="activePolicyTab === 'default'" class="space-y-4 min-h-[350px]">
                <h5 class="text-xs font-bold text-gray-400 uppercase tracking-wider">兜底策略 (无匹配时生效)</h5>
                <div class="p-6 bg-slate-50 rounded-xl border border-slate-200">
                   <p class="text-xs text-slate-500 mb-4">当请求用户不满足任何“用户例外”或“角色策略”时，将强制执行此过滤条件。推荐设为 <code class="bg-slate-200 px-1 rounded">1=0</code> (全部拒绝)。</p>
                   <div v-if="visualRules.default_policy.length > 0" class="space-y-4">
                      <div v-for="(rule, idx) in visualRules.default_policy" :key="idx" class="flex flex-col gap-2 p-3 bg-white rounded-lg border border-slate-200 shadow-sm">
                         <div class="flex items-center gap-2">
                            <input v-model="rule.condition" class="flex-1 text-sm font-mono border border-slate-300 rounded-lg px-4 py-2 focus:ring-2 focus:ring-slate-500 outline-none" placeholder="e.g. 1=0">
                            <button @click="visualRules.default_policy.splice(idx, 1); visualRules = {...visualRules}" class="text-slate-400 hover:text-red-500 transition-colors">
                               <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                            </button>
                         </div>
                         
                         <!-- New single-row builder -->
                         <div class="flex items-end gap-3 p-3 bg-slate-50/80 rounded-xl border border-slate-200">
                           <div class="space-y-1">
                             <label class="block text-[10px] text-slate-400 font-bold ml-1">目标表</label>
                             <RowFilterOptionSelect
                               v-model="rule._builder_table"
                               :options="tableSelectOptions"
                               placeholder="选择数据表..."
                               button-class="border-slate-300 focus:ring-slate-500"
                               menu-class="hover:bg-slate-50"
                             />
                           </div>
                           
                           <div class="space-y-1">
                             <label class="block text-[10px] text-slate-400 font-bold ml-1">选择字段</label>
                             <RowFilterOptionSelect
                               v-model="rule._builder_field"
                               :options="getColumnSelectOptions(rule._builder_table)"
                               placeholder="选择字段..."
                               :disabled="!rule._builder_table"
                               button-class="border-slate-300 focus:ring-slate-500"
                               menu-class="hover:bg-slate-50"
                             />
                           </div>

                           <div class="space-y-1">
                             <label class="block text-[10px] text-slate-400 font-bold ml-1">操作符</label>
                             <select v-model="rule._builder_op" class="h-[2.625rem] bg-white border border-slate-300 rounded-lg px-2 text-xs focus:ring-1 focus:ring-slate-500 outline-none w-28 shadow-sm">
                                <option value="" disabled selected>操作符...</option>
                                <option v-for="op in OPERATORS" :key="op.value" :value="op.value">{{ op.label }}</option>
                             </select>
                           </div>
                           
                           <div class="flex-1 space-y-1">
                             <label class="block text-[10px] text-slate-400 font-bold ml-1">过滤值/变量</label>
                             <div class="relative flex items-center gap-1">
                               <input v-model="rule._builder_val" class="flex-1 w-full h-[2.625rem] bg-white border border-slate-300 rounded-lg px-2 text-xs focus:ring-1 focus:ring-slate-500 outline-none min-w-[120px] shadow-sm" placeholder="e.g. {user.id}">
                               <!-- System variable dropdown trigger -->
                               <button @click.stop="toggleMenu(`default-${idx}-vars`)" class="flex-shrink-0 h-[2.625rem] w-9 rounded-lg text-emerald-600 hover:bg-emerald-50 transition-colors border border-emerald-100 bg-white flex items-center justify-center font-bold" title="插入内部变量">
                                 { }
                               </button>
                               <PermissionVariableMenu
                                 v-if="activeMenuPath === `default-${idx}-vars`"
                                 :builtin-variables="[...BUILTIN_USER_VARIABLES]"
                                 :extra-data-variables="EXTRA_DATA_USER_VARIABLES"
                                 accent="slate"
                                 @select="(v) => { rule._builder_val = v; closeMenus() }"
                               />
                             </div>
                           </div>
                           
                           <button @click="appendBuilderCondition(rule)" class="bg-slate-700 hover:bg-slate-800 text-white w-10 h-[2.625rem] rounded-lg flex items-center justify-center transition-all shadow-md disabled:opacity-30 disabled:scale-100 active:scale-95" :disabled="!rule._builder_table || !rule._builder_field || !rule._builder_op || !rule._builder_val" title="追加条件">
                             <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M12 6v6m0 0v6m0-6h6m-6 0H6"/></svg>
                           </button>
                         </div>
                      </div>
                      <div class="pt-2 flex justify-center">
                         <button @click="visualRules.default_policy.push({condition: ''}); visualRules = {...visualRules}" class="text-[10px] text-blue-600 font-bold flex items-center gap-1.5 hover:bg-blue-50 px-4 py-2 rounded-lg transition-all border border-blue-100 bg-white shadow-sm">
                            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
                            追加一组策略 (OR)
                         </button>
                      </div>
                   </div>
                   <button v-else @click="visualRules.default_policy = [{condition: '1=0'}]; visualRules = {...visualRules}" class="w-full py-4 border-2 border-dashed border-slate-200 rounded-xl text-slate-400 hover:text-slate-600 transition-all flex items-center justify-center gap-2 font-medium">
                      启用默认策略
                   </button>
                </div>
             </div>

             <!-- Tab Content: JSON View -->
             <div v-if="activePolicyTab === 'json'" class="space-y-4 min-h-[350px]">
                <div class="flex items-center justify-between">
                  <h5 class="text-xs font-bold text-gray-400 uppercase tracking-wider">JSON 配置视图 (只读)</h5>
                  <button @click="copyJSON()" class="text-[10px] bg-blue-50 hover:bg-blue-100 text-blue-600 px-3 py-1 rounded-lg transition-colors flex items-center gap-1.5 font-bold">
                    <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 5H6a2 2 0 00-2 2v11a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3"/></svg>
                    复制 JSON
                  </button>
                </div>
                
                <div class="bg-slate-900 rounded-xl overflow-hidden shadow-lg border border-slate-700 relative">
                  <div class="absolute top-3 right-4 flex gap-2">
                    <div class="w-2.5 h-2.5 rounded-full bg-red-500/50"></div>
                    <div class="w-2.5 h-2.5 rounded-full bg-amber-500/50"></div>
                    <div class="w-2.5 h-2.5 rounded-full bg-emerald-500/50"></div>
                  </div>
                  <pre class="p-6 text-[11px] font-mono text-emerald-400 overflow-x-auto max-h-[400px] custom-scrollbar leading-relaxed">{{ jsonEditorContent }}</pre>
                </div>
                <p class="text-[10px] text-gray-400 italic">此视图实时展示可视化编辑器的配置结果，不可直接编辑。</p>
             </div>
           </div>
           
           <div v-else class="py-12 text-center bg-gray-50 rounded-xl border border-dashed border-gray-200">
              <div class="text-3xl mb-2 opacity-30">🔓</div>
              <p class="text-sm text-gray-400 font-medium">当前数据集未启用权限校验，所有授权用户可查询全量数据。</p>
           </div>
        </div>

        <!-- Footer -->
        <div class="bg-gray-50 px-8 py-4 flex justify-end gap-3 border-t border-gray-100">
          <button @click="showPermConfigModal = false" class="px-5 py-2 text-sm font-bold text-gray-500 hover:bg-gray-100 rounded-xl transition-colors">取消</button>
          <button @click="handleSavePermConfig" class="px-8 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl font-bold shadow-lg shadow-emerald-200 transition-all active:scale-95">
            保存配置
          </button>
        </div>
      </div>
    </div>

    <!-- Permission Help Modal -->
    <div v-if="showPermHelpModal" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" @click.self="showPermHelpModal = false">
      <div class="bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col overflow-hidden border border-gray-100 animate-fade-in-up">
        <!-- Header -->
        <div class="p-6 border-b border-gray-100 flex justify-between items-center bg-blue-50/30">
          <div class="flex items-center gap-3">
             <div class="w-10 h-10 rounded-lg bg-blue-100 text-blue-600 flex items-center justify-center border border-blue-200">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
             </div>
             <div>
               <h2 class="text-xl font-bold text-gray-900">数据权限配置指南</h2>
               <p class="text-xs text-gray-500 font-medium">配置行级过滤策略 (Row-Level Security)。</p>
             </div>
          </div>
          <button @click="showPermHelpModal = false" class="text-gray-400 hover:text-gray-600 transition-colors">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>

        <!-- Content -->
        <div class="flex-1 overflow-y-auto p-6 space-y-6 custom-scrollbar">
           <section class="space-y-2">
             <h3 class="font-bold text-gray-800 flex items-center gap-2 text-sm">
               <span class="w-1 h-4 bg-blue-500 rounded-full"></span>
               匹配优先级
             </h3>
             <p class="text-xs text-gray-600 leading-relaxed pl-3 border-l-2 border-gray-100 ml-0.5">
               系统按以下顺序解析规则，匹配到即停止：<br>
               <b class="text-blue-600">1. 用户例外 (user_overrides)</b> &gt; 
               <b class="text-blue-600">2. 角色策略 (role_policies)</b> &gt; 
               <b class="text-blue-600">3. 默认策略 (default_policy)</b>
             </p>
           </section>

           <section class="space-y-2">
             <h3 class="font-bold text-gray-800 flex items-center gap-2 text-sm">
               <span class="w-1 h-4 bg-blue-500 rounded-full"></span>
               支持的动态变量
             </h3>
             <div class="grid grid-cols-2 gap-2 text-[10px]">
               <div class="p-2 bg-gray-50 rounded border border-gray-100 font-mono"><span class="text-blue-600 font-bold">{user.id}</span> 用户唯一ID</div>
               <div class="p-2 bg-gray-50 rounded border border-gray-100 font-mono"><span class="text-blue-600 font-bold">{user.user_name}</span> 登录账号</div>
               <div class="p-2 bg-gray-50 rounded border border-gray-100 font-mono"><span class="text-blue-600 font-bold">{user.real_name}</span> 真实姓名</div>
               <div class="p-2 bg-gray-50 rounded border border-gray-100 font-mono"><span class="text-blue-600 font-bold">{user.dept_code}</span> 部门代码</div>
               <div class="p-2 bg-gray-50 rounded border border-gray-100 font-mono"><span class="text-blue-600 font-bold">{user.org_path}</span> 组织全路径</div>
               <div class="p-2 bg-gray-50 rounded border border-gray-100 font-mono"><span class="text-blue-600 font-bold">{user.role}</span> 系统角色</div>
             </div>
             <p class="text-[10px] text-gray-500 mt-2">以上 6 项为<strong>内置用户属性</strong>，所有用户均可用。</p>
           </section>

           <section class="space-y-2">
             <h3 class="font-bold text-gray-800 flex items-center gap-2 text-sm">
               <span class="w-1 h-4 bg-violet-500 rounded-full"></span>
               extra_data 扩展字段
             </h3>
             <p class="text-[10px] text-gray-600 leading-relaxed">
               来自用户 <code class="text-violet-600 bg-violet-50 px-1 rounded">extra_data</code> JSON 的键，运行时摊平为
               <code class="font-mono text-violet-600">{user.键名}</code>。下拉列表从平台用户聚合展示，并标注「扩展」与内置变量区分。
             </p>
             <div v-if="EXTRA_DATA_USER_VARIABLES.length" class="grid grid-cols-1 gap-1.5 text-[10px] max-h-32 overflow-y-auto">
               <div
                 v-for="v in EXTRA_DATA_USER_VARIABLES"
                 :key="v.value"
                 class="p-2 bg-violet-50 rounded border border-violet-100 font-mono flex items-center justify-between gap-2"
               >
                 <span><span class="text-violet-700 font-bold">{{ v.value }}</span> {{ v.label }}</span>
                 <span class="text-[8px] px-1 py-0.5 rounded bg-violet-100 text-violet-600 font-bold shrink-0">扩展</span>
               </div>
             </div>
             <p v-else class="text-[10px] text-gray-400">当前暂无 extra_data 扩展字段，可在用户管理或第三方用户同步中配置。</p>
           </section>

           <section class="space-y-4">
             <h3 class="font-bold text-gray-800 flex items-center gap-2 text-sm">
               <span class="w-1 h-4 bg-blue-500 rounded-full"></span>
               配置示例
             </h3>
             
             <div class="space-y-2">
               <p class="text-[11px] font-bold text-gray-500 uppercase tracking-wider">示例 1: 按所属部门过滤 (最常用)</p>
               <div class="bg-slate-900 rounded-xl overflow-hidden shadow-lg border border-slate-700">
                 <pre class="p-4 text-[11px] font-mono text-emerald-400 overflow-x-auto">{
  "role_policies": {
    "viewer": [
      { "condition": "dept_code = {user.dept_code}" }
    ]
  }
}</pre>
               </div>
             </div>

             <div class="space-y-2">
               <p class="text-[11px] font-bold text-gray-500 uppercase tracking-wider">示例 2: 组织架构模糊匹配 (树形结构)</p>
               <div class="bg-slate-900 rounded-xl overflow-hidden shadow-lg border border-slate-700">
                 <pre class="p-4 text-[11px] font-mono text-emerald-400 overflow-x-auto">{
  "role_policies": {
    "regional_viewer": [
      { "condition": "org_path LIKE '{user.org_path}%'" }
    ]
  }
}</pre>
               </div>
             </div>

             <div class="space-y-2">
               <p class="text-[11px] font-bold text-gray-500 uppercase tracking-wider">示例 3: 复杂 OR 逻辑 (多维度权限并集)</p>
               <div class="bg-slate-900 rounded-xl overflow-hidden shadow-lg border border-slate-700">
                 <pre class="p-4 text-[11px] font-mono text-emerald-400 overflow-x-auto">{
  "role_policies": {
    "manager": [
      { "condition": "dept_code = {user.dept_code}" },
      { "condition": "region_code = 'SH'" }
    ]
  }
}</pre>
                 <div class="bg-slate-800 px-4 py-2 text-[10px] text-slate-400 border-t border-slate-700 italic">
                   提示：数组内的多条规则将被注入为 (cond1 AND cond2)。若需 OR，请写在一条 condition 内。
                 </div>
               </div>
             </div>
           </section>
        </div>

        <!-- Footer -->
        <div class="p-4 bg-gray-50 border-t border-gray-100 flex justify-end">
          <button @click="showPermHelpModal = false" class="px-8 py-2 bg-blue-600 text-white rounded-xl font-bold hover:bg-blue-700 shadow-lg shadow-blue-200 transition-all text-sm">
            明白了
          </button>
        </div>
      </div>
    </div>

    <!-- Role Selector Modal -->
    <div v-if="showRoleSelectorModal" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" @click.self="showRoleSelectorModal = false">
      <div class="bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col overflow-hidden border border-gray-100 animate-fade-in-up">
        <!-- Header -->
        <div class="p-6 border-b border-gray-100 flex justify-between items-center bg-emerald-50/30">
          <div class="flex items-center gap-3">
             <div class="w-10 h-10 rounded-lg bg-emerald-100 text-emerald-600 flex items-center justify-center border border-emerald-200">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"/></svg>
             </div>
             <div>
               <h2 class="text-xl font-bold text-gray-900">选择角色</h2>
               <p class="text-xs text-gray-500 font-medium">为特定角色配置数据访问权限策略。</p>
             </div>
          </div>
          <button @click="showRoleSelectorModal = false" class="text-gray-400 hover:text-gray-600 transition-colors">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>

        <!-- Content -->
        <div class="flex-1 overflow-y-auto p-6 custom-scrollbar">
          <!-- Search Filter -->
          <div class="mb-4">
            <div class="relative">
              <span class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-gray-400">
                <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
              </span>
              <input 
                v-model="roleSearchQuery" 
                type="text" 
                class="block w-full pl-10 pr-3 py-2 border border-gray-200 rounded-lg leading-5 bg-gray-50 placeholder-gray-500 focus:outline-none focus:ring-1 focus:ring-emerald-500 focus:bg-white sm:text-sm transition-all" 
                placeholder="搜索角色名称或代码..."
              >
            </div>
          </div>

          <div v-if="filteredRoles.length > 0" class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <button 
              v-for="role in filteredRoles" 
              :key="role.code"
              @click="selectRoleFromModal(role)"
              class="flex items-center gap-3 p-3 rounded-lg border border-gray-200 hover:border-emerald-300 hover:bg-emerald-50 transition-all text-left group"
              :disabled="visualRules.role_policies[role.code] !== undefined"
            >
              <div class="w-10 h-10 rounded-lg bg-emerald-100 text-emerald-600 flex items-center justify-center font-bold text-sm border border-emerald-200">
                {{ (role.name || role.code || 'R').charAt(0).toUpperCase() }}
              </div>
              <div class="flex-1 min-w-0">
                <div class="font-medium text-gray-900 text-sm truncate">{{ role.name || '未知角色' }}</div>
                <div class="text-xs text-gray-500 truncate font-mono">{{ role.code }}</div>
              </div>
              <div class="text-xs text-gray-400">
                CODE: {{ role.code }}
              </div>
              <div v-if="visualRules.role_policies[role.code] !== undefined" class="px-2 py-1 bg-green-100 text-green-700 text-[10px] rounded-full font-medium">
                已添加
              </div>
            </button>
          </div>
          <div v-else-if="roleSearchQuery && filteredRoles.length === 0" class="text-center py-12">
            <div class="text-4xl mb-4 opacity-30">🔍</div>
            <p class="text-gray-500 font-medium">未找到匹配的角色</p>
            <p class="text-gray-400 text-sm mt-1">尝试调整搜索关键词</p>
          </div>
          <div v-else class="text-center py-12">
            <div class="text-4xl mb-4 opacity-30">👥</div>
            <p class="text-gray-500 font-medium">暂无角色数据</p>
            <p class="text-gray-400 text-sm mt-1">请先在系统中添加角色</p>
          </div>
        </div>

        <!-- Footer -->
        <div class="p-4 bg-gray-50 border-t border-gray-100 flex justify-end">
          <button @click="showRoleSelectorModal = false" class="px-6 py-2 text-sm font-bold text-gray-500 hover:bg-gray-100 rounded-xl transition-colors">
            取消
          </button>
        </div>
      </div>
    </div>

    <!-- User Selector Modal -->
    <div v-if="showUserSelectorModal" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" @click.self="showUserSelectorModal = false">
      <div class="bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col overflow-hidden border border-gray-100 animate-fade-in-up">
        <!-- Header -->
        <div class="p-6 border-b border-gray-100 flex justify-between items-center bg-amber-50/30">
          <div class="flex items-center gap-3">
             <div class="w-10 h-10 rounded-lg bg-amber-100 text-amber-600 flex items-center justify-center border border-amber-200">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"/></svg>
             </div>
             <div>
               <h2 class="text-xl font-bold text-gray-900">选择用户</h2>
               <p class="text-xs text-gray-500 font-medium">为特定用户配置数据访问权限例外。</p>
             </div>
          </div>
          <button @click="showUserSelectorModal = false" class="text-gray-400 hover:text-gray-600 transition-colors">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>

        <!-- Content -->
        <div class="flex-1 overflow-y-auto p-6 custom-scrollbar">
          <!-- Search Filter -->
          <div class="mb-4">
            <div class="relative">
              <span class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-gray-400">
                <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
              </span>
              <input 
                v-model="userSearchQuery" 
                type="text" 
                class="block w-full pl-10 pr-3 py-2 border border-gray-200 rounded-lg leading-5 bg-gray-50 placeholder-gray-500 focus:outline-none focus:ring-1 focus:ring-amber-500 focus:bg-white sm:text-sm transition-all" 
                placeholder="搜索用户姓名、用户名或ID..."
              >
            </div>
          </div>

          <div v-if="filteredUsers.length > 0" class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <button 
              v-for="user in filteredUsers" 
              :key="user.id"
              @click="selectUserFromModal(user)"
              class="flex items-center gap-3 p-3 rounded-lg border border-gray-200 hover:border-amber-300 hover:bg-amber-50 transition-all text-left group"
              :disabled="visualRules.user_overrides[user.id] !== undefined"
            >
              <div class="w-10 h-10 rounded-full bg-amber-100 text-amber-600 flex items-center justify-center font-bold text-sm border border-amber-200">
                {{ (user.real_name || user.user_name || 'U').charAt(0).toUpperCase() }}
              </div>
              <div class="flex-1 min-w-0">
                <div class="font-medium text-gray-900 text-sm truncate">{{ user.real_name || '未知用户' }}</div>
                <div class="text-xs text-gray-500 truncate">{{ user.user_name }}</div>
              </div>
              <div class="text-xs text-gray-400">
                ID: {{ user.id }}
              </div>
              <div v-if="visualRules.user_overrides[user.id] !== undefined" class="px-2 py-1 bg-green-100 text-green-700 text-[10px] rounded-full font-medium">
                已添加
              </div>
            </button>
          </div>
          <div v-else-if="userSearchQuery && filteredUsers.length === 0" class="text-center py-12">
            <div class="text-4xl mb-4 opacity-30">🔍</div>
            <p class="text-gray-500 font-medium">未找到匹配的用户</p>
            <p class="text-gray-400 text-sm mt-1">尝试调整搜索关键词</p>
          </div>
          <div v-else class="text-center py-12">
            <div class="text-4xl mb-4 opacity-30">👥</div>
            <p class="text-gray-500 font-medium">暂无用户数据</p>
            <p class="text-gray-400 text-sm mt-1">请先在系统中添加用户</p>
          </div>
        </div>

        <!-- Footer -->
        <div class="p-4 bg-gray-50 border-t border-gray-100 flex justify-end">
          <button @click="showUserSelectorModal = false" class="px-6 py-2 text-sm font-bold text-gray-500 hover:bg-gray-100 rounded-xl transition-colors">
            取消
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
