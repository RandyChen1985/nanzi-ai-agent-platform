<script setup lang="ts">
/**
 * 数据行策略（行级隔离）配置弹窗。
 *
 * 原来内联在元数据管理列表页里，详情页想改只能跳回列表页再点菜单。抽成组件后，
 * 列表页的「数据行策略」菜单和数据集详情页的「编辑行级策略」按钮打开的是同一套
 * UI 和同一份逻辑，不存在两处实现漂移的问题。
 */
import { computed, nextTick, ref, watch } from 'vue'
import { metadataApi } from '../../api/metadata'
import { portalApi, type User, type Role } from '../../api/portal'
import RowFilterOptionSelect from './RowFilterOptionSelect.vue'
import PermissionVariableMenu from './PermissionVariableMenu.vue'
import { useToast } from '../../composables/useToast'
import { copyToClipboard } from '../../utils/clipboard'
import { BUILTIN_USER_VARIABLES, collectExtraDataVariables } from '../../utils/userVariables'
import { parseRowFilterCondition } from '../../utils/rowFilterCondition'
import {
  ExclamationTriangleIcon,
  InformationCircleIcon,
  LockOpenIcon,
  MagnifyingGlassIcon,
  UserIcon,
} from '@heroicons/vue/24/outline'

const props = defineProps<{
  show: boolean
  /** 目标数据集；编辑的是它的副本，取消不会改脏父页面数据 */
  dataset: any | null
  /** 打开后直接滚到策略区（两个入口都希望落在规则上，而不是弹窗顶部） */
  focusRowPolicy?: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'saved'): void
}>()

const close = () => emit('close')

// 防连点：失败时用户容易反复点保存，一屏能弹好几个一模一样的 toast
const saving = ref(false)
// 打开瞬间要先拉详情校正配置，这段时间禁止保存，避免把半成品写回去
const opening = ref(false)

const { showToast } = useToast()

const showPermHelpModal = ref(false)
const showUserSelectorModal = ref(false)

const showRoleSelectorModal = ref(false)
const userSearchQuery = ref('')
const roleSearchQuery = ref('')

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

const EXTRA_DATA_USER_VARIABLES = computed(() => collectExtraDataVariables(allUsers.value))

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
  const ok = await copyToClipboard(jsonEditorContent.value)
  if (ok) {
    showToast('JSON配置已复制到剪贴板', 'success')
  } else {
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
  // 构建器里的是打开配置时从已有 condition 回填来的：再点 + 应当替换原条件，
  // 否则会拼出 `A = 1 AND A = 1` 这种重复片段。
  const replacing = Boolean(rule._builder_from_condition && rule.condition && rule.condition.trim())
  
  if (replacing) {
    rule.condition = condition
  } else if (rule.condition) {
    rule.condition = `${rule.condition} AND ${condition}`
  } else {
    rule.condition = condition
  }
  
  // 清空构建器字段
  rule._builder_table = ''
  rule._builder_field = ''
  rule._builder_op = ''
  rule._builder_val = ''
  rule._builder_from_condition = false
  
  showToast(replacing ? '已用编辑框内容替换原条件' : '条件已追加到规则中', 'success')
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

/** 拉详情并回填表结构；返回值供打开弹窗时校正行级配置，失败返回 null */
const fetchDatasetColumns = async (id: number) => {
  try {
    const res = await metadataApi.getDataset(id)
    const detail = res.data
    datasetTables.value = detail.tables || []
    const cols: {physical_name: string, term: string}[] = []
    detail.tables?.forEach((table: any) => {
      table.columns.forEach((col: any) => {
        cols.push({ physical_name: col.physical_name, term: col.term })
      })
    })
    // Unique by name
    const uniqueCols = cols.filter((v, i, a) => a.findIndex(t => t.physical_name === v.physical_name) === i)
    datasetColumns.value = uniqueCols
    return detail
  } catch (e) {
    console.error('Failed to fetch dataset columns', e)
    return null
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

/**
 * 把已有规则的 condition 反向解析回「目标表 / 字段 / 操作符 / 过滤值」四个框。
 *
 * 依赖 datasetTables：下拉组件的 modelValue 只要在 options 里找不到就渲染成占位符，
 * 所以表名或字段不在当前数据集内的条件一律跳过，否则会变成「看着是空白、其实是脏值」。
 * 已经手动填过构建器的规则也不覆盖。
 */
const hydrateBuilderFromConditions = () => {
  const tables = datasetTables.value || []
  if (!tables.length) return

  const columnIndex = new Map<string, Set<string>>()
  tables.forEach((table: any) => {
    const tableName = String(table?.physical_name || '')
    if (!tableName) return
    const columns = (table.columns || []).map((column: any) => String(column?.physical_name || ''))
    columnIndex.set(tableName, new Set(columns))
  })

  let hydratedCount = 0
  const applyRule = (rule: any) => {
    if (!rule || typeof rule !== 'object') return
    if (rule._builder_table || rule._builder_field || rule._builder_op || rule._builder_val) return

    const parts = parseRowFilterCondition(rule.condition)
    if (!parts) return
    const columns = columnIndex.get(parts.table)
    if (!columns || !columns.has(parts.field)) return

    rule._builder_table = parts.table
    rule._builder_field = parts.field
    rule._builder_op = parts.op
    rule._builder_val = parts.value
    rule._builder_from_condition = true
    hydratedCount += 1
  }

  const walkPolicyMap = (bucket: any) => {
    if (!bucket || typeof bucket !== 'object') return
    Object.values(bucket).forEach((rules: any) => {
      if (Array.isArray(rules)) rules.forEach(applyRule)
    })
  }

  walkPolicyMap(visualRules.value.user_overrides)
  walkPolicyMap(visualRules.value.role_policies)
  if (Array.isArray(visualRules.value.default_policy)) {
    visualRules.value.default_policy.forEach(applyRule)
  }

  if (hydratedCount > 0) visualRules.value = { ...visualRules.value }
}

/**
 * 打开时把父组件传进来的数据集复制一份，编辑的是副本：
 * 取消时父页面的数据不会被改脏。
 */
const openConfig = async () => {
  const ds = props.dataset
  if (!ds) return
  const datasetId = ds.id

  activePolicyTab.value = 'user' // 默认打开用户Tab
  closeMenus() // Reset any open selectors
  fetchRoles()
  fetchUsers()

  // 先给个初值：模板里有 v-model 直接绑 configuringDataset.*，不能是 null
  configuringDataset.value = JSON.parse(JSON.stringify(ds))

  // 再拿详情校正：列表接口给的对象不一定带全行级配置，用它来初始化的话，
  // 用户什么都不改直接保存就会把一份空配置写回去（后端会按 fail-closed 拒绝）。
  // fetchDatasetColumns 内部本来就是拉详情，顺手把表结构一起取回来，不多一次请求。
  opening.value = true
  const detail = await fetchDatasetColumns(datasetId)
  opening.value = false
  if (!detail) {
    showToast('读取数据集行级配置失败，请稍后重试', 'error')
    close()
    return
  }
  // 详情里的字段更可信，优先用它
  if (detail.enable_data_perm !== undefined) {
    configuringDataset.value.enable_data_perm = Boolean(detail.enable_data_perm)
  }
  if (detail.row_filter_config !== undefined) {
    configuringDataset.value.row_filter_config = detail.row_filter_config
  }
  if (detail.display_name) configuringDataset.value.display_name = detail.display_name

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

  // 「数据行策略」入口直接落到策略区，省掉一次手滚
  if (props.focusRowPolicy) {
    await nextTick()
    rowPolicySectionRef.value?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }

  // 回填依赖 datasetTables，必须等表结构到位；期间用户切了别的数据集就不再回填
  if (configuringDataset.value?.id !== datasetId) return
  hydrateBuilderFromConditions()
}

/** 递归找「至少一条带 condition 的规则」——后端也是这个口径（fail-closed） */
const hasAnyRowRule = (config: any): boolean => {
  const walk = (value: any): boolean => {
    if (Array.isArray(value)) {
      return value.some(
        (item) =>
          item && typeof item === 'object' && String((item as any).condition || '').trim() !== '',
      )
    }
    if (value && typeof value === 'object') {
      return Object.values(value).some(walk)
    }
    return false
  }
  return walk(config)
}

const handleSavePermConfig = async () => {
  if (!configuringDataset.value || saving.value) return

  let config: any = {}

  // 只有在启用权限时才进行规则同步和校验
  if (configuringDataset.value.enable_data_perm) {
    config = syncVisualToJson()

    // 检查是否存在空 condition
    const checkEmptyConditions = (rules: any[]): boolean => {
      if (!Array.isArray(rules)) return false
      return rules.some((r) => !r.condition || r.condition.trim() === '')
    }

    let hasEmpty = false
    if (config.user_overrides) {
      for (const rules of Object.values(config.user_overrides)) {
        if (checkEmptyConditions(rules as any[])) {
          hasEmpty = true
          break
        }
      }
    }
    if (!hasEmpty && config.role_policies) {
      for (const rules of Object.values(config.role_policies)) {
        if (checkEmptyConditions(rules as any[])) {
          hasEmpty = true
          break
        }
      }
    }
    if (!hasEmpty && config.default_policy) {
      if (checkEmptyConditions(config.default_policy)) hasEmpty = true
    }

    if (hasEmpty) {
      showToast('保存失败：发现无效规则，所有规则的条件(Condition)均不能为空', 'warning')
      return
    }

    // 和后端 _reject_unusable_row_filter_config 同一口径：开了校验却一条规则都没有，
    // 运行期兜底是 1=0，所有人都会查不到数据。在这里拦住，别让用户撞 400 才看到一句
    // 「保存失败」——原因和怎么改都得说清楚。
    if (!hasAnyRowRule(config)) {
      showToast(
        '启用精细化校验必须至少配一条行级规则，否则所有人都查不到数据。要放开所有人请先关掉上面的开关。',
        'warning',
      )
      return
    }
  }

  saving.value = true
  try {
    configuringDataset.value.row_filter_config = config

    await metadataApi.updateDataset(configuringDataset.value.id, {
      enable_data_perm: configuringDataset.value.enable_data_perm,
      row_filter_config: configuringDataset.value.row_filter_config
    })
    configuringDataset.value = null
    emit('saved')
    showToast('权限配置已保存', 'success')
  } catch (e: any) {
    console.error('Save permissions failed', e)
    // 后端的校验文案写得很具体（比如「启用数据权限时必须至少配置一条行级规则」），
    // 直接吞成「保存失败」等于把用户唯一的线索扔了。
    const detail = e?.response?.data?.detail
    const message =
      typeof detail === 'string' && detail.trim()
        ? detail.trim()
        : e?.response?.data?.message || '保存权限配置失败，请稍后重试'
    showToast(message, 'error')
  } finally {
    saving.value = false
  }
}

const rowPolicySectionRef = ref<HTMLElement | null>(null)

// 父组件先设 dataset 再置 show=true，这里只在真正打开时初始化一次
watch(
  () => props.show,
  (val) => {
    if (val) void openConfig()
  },
  { immediate: true },
)
</script>

<template>
    <!-- Data Permission Configuration Modal -->
    <Teleport to="body">
      <div v-if="show" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="close">
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
            <button @click="close" class="text-gray-400 hover:text-gray-600 transition-colors">
              <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
            </button>
          </div>

          <!-- Body -->
          <div class="p-8 space-y-6 max-h-[75vh] overflow-y-auto custom-scrollbar" @click="closeMenus">
             <div ref="rowPolicySectionRef" class="flex items-center justify-between p-4 bg-gray-50 rounded-xl border border-gray-100">
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
                              <button @click="appendBuilderCondition(rule)" class="bg-amber-700 hover:bg-amber-800 text-white w-10 h-[2.625rem] rounded-lg flex items-center justify-center transition-all shadow-md disabled:opacity-30 disabled:scale-100 active:scale-95" :disabled="!rule._builder_table || !rule._builder_field || !rule._builder_op || !rule._builder_val" :title="rule._builder_from_condition ? '用编辑框内容替换上方条件' : '追加条件'">
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
                        <UserIcon class="w-10 h-10 mx-auto mb-4 text-gray-300" />
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
                          <MagnifyingGlassIcon class="w-4 h-4 inline-block mr-1" /> 校验规则
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
                                <ExclamationTriangleIcon v-if="detail.type === 'error' || detail.type === 'warning'" class="w-4 h-4" />
                                <InformationCircleIcon v-else class="w-4 h-4" />
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
                              
                                <button @click="appendBuilderCondition(rule)" class="bg-emerald-700 hover:bg-emerald-800 text-white w-10 h-[2.625rem] rounded-lg flex items-center justify-center transition-all shadow-md disabled:opacity-30 disabled:scale-100 active:scale-95" :disabled="!rule._builder_table || !rule._builder_field || !rule._builder_op || !rule._builder_val" :title="rule._builder_from_condition ? '用编辑框内容替换上方条件' : '追加条件'">
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
                                
                                  <button @click="appendBuilderCondition(rule)" class="bg-amber-700 hover:bg-amber-800 text-white w-10 h-[2.625rem] rounded-lg flex items-center justify-center transition-all shadow-md disabled:opacity-30 disabled:scale-100 active:scale-95" :disabled="!rule._builder_table || !rule._builder_field || !rule._builder_op || !rule._builder_val" :title="rule._builder_from_condition ? '用编辑框内容替换上方条件' : '追加条件'">
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
                           
                             <button @click="appendBuilderCondition(rule)" class="bg-slate-700 hover:bg-slate-800 text-white w-10 h-[2.625rem] rounded-lg flex items-center justify-center transition-all shadow-md disabled:opacity-30 disabled:scale-100 active:scale-95" :disabled="!rule._builder_table || !rule._builder_field || !rule._builder_op || !rule._builder_val" :title="rule._builder_from_condition ? '用编辑框内容替换上方条件' : '追加条件'">
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
                      <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/></svg>
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
                <LockOpenIcon class="w-8 h-8 mx-auto mb-2 text-gray-300" />
                <p class="text-sm text-gray-400 font-medium">当前数据集未启用权限校验，所有授权用户可查询全量数据。</p>
             </div>
          </div>

          <!-- Footer -->
          <div class="bg-gray-50 px-8 py-4 flex justify-end gap-3 border-t border-gray-100">
            <button @click="close" class="px-5 py-2 text-sm font-bold text-gray-500 hover:bg-gray-100 rounded-xl transition-colors">取消</button>
            <button
              @click="handleSavePermConfig"
              :disabled="saving || opening"
              class="px-8 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl font-bold shadow-lg shadow-emerald-200 transition-all active:scale-95 disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {{ opening ? '读取中…' : saving ? '保存中…' : '保存配置' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Permission Help Modal -->
    <Teleport to="body">
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
    </Teleport>

    <!-- Role Selector Modal -->
    <Teleport to="body">
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
                  type="search"
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
              <MagnifyingGlassIcon class="w-10 h-10 mx-auto mb-4 text-gray-300" />
              <p class="text-gray-500 font-medium">未找到匹配的角色</p>
              <p class="text-gray-400 text-sm mt-1">尝试调整搜索关键词</p>
            </div>
            <div v-else class="text-center py-12">
              <UserIcon class="w-10 h-10 mx-auto mb-4 text-gray-300" />
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
    </Teleport>

    <!-- User Selector Modal -->
    <Teleport to="body">
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
                  type="search"
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
              <MagnifyingGlassIcon class="w-10 h-10 mx-auto mb-4 text-gray-300" />
              <p class="text-gray-500 font-medium">未找到匹配的用户</p>
              <p class="text-gray-400 text-sm mt-1">尝试调整搜索关键词</p>
            </div>
            <div v-else class="text-center py-12">
              <UserIcon class="w-10 h-10 mx-auto mb-4 text-gray-300" />
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
    </Teleport>
</template>
