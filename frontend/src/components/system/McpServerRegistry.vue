<script setup lang="ts">
import { ref, onMounted, watch, computed, nextTick } from 'vue'
import axios from '@/utils/axios'
import { copyToClipboard } from '@/utils/clipboard'
import { useToast } from '@/composables/useToast'
import { useUser } from '@/composables/useUser'
import ConfirmModal from '../../components/ConfirmModal.vue'
import Switch from '../Switch.vue'
import McpToolTester from './McpToolTester.vue'
import {
  buildMcpServerNamePrefix,
  normalizeMcpServerNameSuffix,
  resolveEditedServerName,
  stripMcpServerNamePrefix,
  suggestUniqueServerNameSuffix,
} from '@/utils/mcpServerName'
import {
  parseMcpServersPaste,
  suggestMcpNameSuffixFromKey,
} from '@/utils/parseMcpServersPaste'
import { 
  PlusIcon,
  BeakerIcon,
  CodeBracketIcon,
  ListBulletIcon,
  ArrowPathIcon,
  TrashIcon,
  PencilSquareIcon,
  LinkIcon,
  CloudArrowDownIcon,
  MagnifyingGlassIcon,
  SparklesIcon,
  ShoppingBagIcon,
  DocumentDuplicateIcon,
  ChevronLeftIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ArrowRightIcon,
  XMarkIcon,
  CheckCircleIcon,
  InformationCircleIcon,
  DocumentTextIcon,
  EllipsisHorizontalIcon,
} from '@heroicons/vue/24/outline'
import McpServerLogModal from './McpServerLogModal.vue'
import McpServerAgentsDrawer from './McpServerAgentsDrawer.vue'
import { buildJavaMcpSample, buildPythonMcpSample } from './mcpCodeSamples'

const props = withDefaults(defineProps<{
  scope?: 'global' | 'personal'
}>(), {
  scope: 'global'
})

const emit = defineEmits<{
  (e: 'server-count-changed', payload: { scope: 'global' | 'personal'; count: number }): void
  (e: 'share-server', server: any): void
}>()

const { showToast } = useToast()
const { userInfo, hasPermission } = useUser()

// MCP 写操作已按动作分码校验（见 app/api/portal/endpoints/mcp.py），
// 按钮跟着收起来，避免「点了才知道没权限」。
const canCreateMcp = computed(() => hasPermission('element:mcp_management:create'))
const canEditMcp = computed(() => hasPermission('element:mcp_management:edit'))
const canDeleteMcp = computed(() => hasPermission('element:mcp_management:delete'))
const canSyncMcp = computed(() => hasPermission('element:mcp_management:sync'))
const canPublishMcp = computed(() => hasPermission('element:mcp_management:publish'))
const canSave = computed(() => {
  if (props.scope === 'personal') return true
  return userInfo.value?.role === 'admin'
})

const namePrefix = computed(() =>
  buildMcpServerNamePrefix(props.scope, userInfo.value?.user_name),
)

const servers = ref<any[]>([])
const searchQuery = ref('')
const showAddDropdown = ref(false)

/** 只看已启用（运行中）的服务——停用的排不进实际调用 */
const onlyRunning = ref(false)

/** 列表排序：默认「运行中优先 → 最近同步」，服务多了再按需切换 */
type ServerSortKey = 'default' | 'recent' | 'name' | 'tools'
const sortBy = ref<ServerSortKey>('default')

const syncTimestamp = (server: any): number => {
  const parsed = server?.last_sync_at ? new Date(server.last_sync_at).getTime() : 0
  return Number.isNaN(parsed) ? 0 : parsed
}

/** 不改原数组：sort 原地排序会打乱后端顺序，切回默认时无法复原 */
function sortServers(list: any[]): any[] {
  const next = [...list]
  switch (sortBy.value) {
    case 'name':
      return next.sort((a, b) =>
        String(a.server_name || '').localeCompare(String(b.server_name || ''), 'zh-Hans-CN'))
    case 'recent':
      return next.sort((a, b) => syncTimestamp(b) - syncTimestamp(a))
    case 'tools':
      return next.sort((a, b) => (b.tool_count || 0) - (a.tool_count || 0))
    default:
      return next.sort((a, b) => {
        const running = Number(b.enabled_status === 1) - Number(a.enabled_status === 1)
        return running || syncTimestamp(b) - syncTimestamp(a)
      })
  }
}

const filteredServers = computed(() => {
  const base = onlyRunning.value
    ? servers.value.filter((s: any) => s.enabled_status === 1)
    : servers.value
  const query = searchQuery.value.trim().toLowerCase()
  if (!query) return sortServers(base)
  return sortServers(base.filter((s: any) => {
    const nameMatch = (s.server_name || '').toLowerCase().includes(query)
    const descMatch = (s.description || '').toLowerCase().includes(query)
    const urlMatch = (s.url || '').toLowerCase().includes(query)
    const toolMatch = Array.isArray(s.tools) && s.tools.some((t: any) =>
      (t.name || '').toLowerCase().includes(query) || (t.description || '').toLowerCase().includes(query)
    )
    return nameMatch || descMatch || urlMatch || toolMatch
  }))
})

/** 用户只填后缀；保存时与固定前缀拼接 */
const serverNameSuffix = ref('')
/** 编辑态回填的原始完整名：用户没改后缀时必须原样保留，绝不重新拼前缀 */
const editingOriginalName = ref('')
/** 第一步录入方式：手动 / JSON 粘贴 */
const connectionInputTab = ref<'manual' | 'json'>('manual')
const mcpJsonPaste = ref('')
const mcpJsonPasteHint = ref('')

const syncFullServerName = () => {
  newServer.value.server_name = resolveEditedServerName(
    editingOriginalName.value,
    props.scope,
    userInfo.value?.user_name,
    serverNameSuffix.value,
  )
}

watch(serverNameSuffix, () => {
  syncFullServerName()
})

watch(
  () => [props.scope, userInfo.value?.user_name] as const,
  () => {
    syncFullServerName()
  },
)

const getApiErrorMessage = (error: any, fallback: string) => {
  const responseData = error?.response?.data
  const candidates = [
    responseData?.message,
    responseData?.detail,
    responseData?.data?.message,
    responseData?.data?.detail,
  ]
  const message = candidates.find((value) => typeof value === 'string' && value.trim())
  return message || fallback
}

const loading = ref(false)
const showAddModal = ref(false)
const isEditing = ref(false)
const editingId = ref('')

// Tool Tester Logic
const showTester = ref(false)
const toolToTest = ref<any>(null)

const openTester = (tool: any) => {
  toolToTest.value = tool
  showTester.value = true
}

// Outbound Audit Log Modal
const showLogModal = ref(false)
const selectedLogServer = ref<any>(null)

const openLogModal = (server: any) => {
  selectedLogServer.value = server
  showLogModal.value = true
}

const wizardStep = ref<1 | 2 | 3>(1) // 1: Input & Verify, 2: Preview & Name, 3: Success & Publish Guide
const createdServer = ref<any | null>(null)
const publishAllLoading = ref(false)
const verifying = ref(false)
const savingServer = ref(false)
// 编辑态记录库里已保存的开关值：仅凭表单开关无法判断签名私钥是否已经生成。
const savedUserAssertionEnabled = ref(false)
const discoveredTools = ref<any[]>([])
const syncLoading = ref<Record<string, boolean>>({})
const statusLoading = ref<Record<string, boolean>>({})

type McpAgentUsage = {
  id: string
  name: string
  display_name: string
  is_enabled: boolean
  active: boolean
  version_count: number
}

type McpServerUsage = {
  server_id: string
  bound_agent_count: number
  active_agent_count: number
  bound_version_count: number
  agents: McpAgentUsage[]
}

const selectedServerUsage = ref<McpServerUsage | null>(null)
const showAgentUsageDrawer = ref(false)
const usageLoading = ref<Record<string, boolean>>({})
const showStatusConfirm = ref(false)
const statusConfirmServer = ref<any | null>(null)
const statusConfirmUsage = ref<McpServerUsage | null>(null)
const statusConfirmLoading = ref(false)

// Batch Actions Logic
const selectedToolIds = ref<Set<string>>(new Set())
const selectedServer = ref<any>(null)
const tools = ref<any[]>([])
const toolsLoading = ref(false)
/** 工具列表请求序号：快速切换服务时，旧响应不得覆盖新选中服务的工具。 */
let toolsRequestSeq = 0

/**
 * 列表刷新后用新对象重绑当前选中服务。
 *
 * 选中对象是列表项的引用：fetchServers 会整体替换 servers 数组，如果不重绑，
 * 改名 / 改地址 / 同步后的工具计数会一直停在旧对象上（右侧标题和“清理已失效”
 * 按钮都读 selectedServer）。服务已不存在时清空选中，避免操作到已删除的服务。
 */
const reconcileSelectedServer = () => {
  const current = selectedServer.value
  if (!current) return
  const fresh = servers.value.find((item: any) => item.id === current.id)
  if (fresh) {
    selectedServer.value = fresh
    return
  }
  selectedServer.value = null
  selectedServerUsage.value = null
  showAgentUsageDrawer.value = false
  tools.value = []
  selectedToolIds.value = new Set()
  toolsRequestSeq += 1
}

const isSelectedServerEnabled = computed(() => Number(selectedServer.value?.enabled_status) === 1)
const canManageSelectedTools = computed(() => canSave.value && isSelectedServerEnabled.value)
const canManageTool = (tool: any) => canManageSelectedTools.value && tool?.is_available !== false

const isAllSelected = computed(() => {
  const selectableTools = tools.value.filter(tool => tool.is_available !== false)
  return selectableTools.length > 0 && selectedToolIds.value.size === selectableTools.length
})

const toggleSelectAll = () => {
  if (!canManageSelectedTools.value) return
  if (isAllSelected.value) {
    selectedToolIds.value.clear()
  } else {
    tools.value
      .filter(tool => tool.is_available !== false)
      .forEach(tool => selectedToolIds.value.add(tool.id))
  }
}

const toggleSelectTool = (id: string) => {
  if (!canManageSelectedTools.value) return
  if (selectedToolIds.value.has(id)) {
    selectedToolIds.value.delete(id)
  } else {
    selectedToolIds.value.add(id)
  }
}

/** 批量操作条上的「取消选择」：整体替换 Set，确保依赖选中集合的 UI 同步刷新 */
const clearToolSelection = () => {
  selectedToolIds.value = new Set()
}

const batchUpdateStatus = async (published: boolean) => {
  const targetServerId = selectedServer.value?.id
  const ids = tools.value
    .filter(tool => selectedToolIds.value.has(tool.id) && tool.is_available !== false)
    .map(tool => tool.id)
  if (!canManageSelectedTools.value || ids.length === 0) return

  loading.value = true
  try {
    // 并行请求里部分失败是常态（并发上限、单个工具被远端删除），不能因为一个失败
    // 就丢掉其余成功结果、也不刷新界面。allSettled + 统一刷新。
    const results = await Promise.allSettled(
      ids.map(id => axios.put(`/api/portal/mcp/tools/${id}/publish?published=${published}`))
    )
    const failed = results.filter(item => item.status === 'rejected').length
    const succeeded = ids.length - failed
    if (failed > 0) {
      showToast(`已${published ? '发布' : '下线'} ${succeeded} 个，${failed} 个失败`, 'warning')
    } else {
      showToast(`成功${published ? '发布' : '下线'} ${ids.length} 个工具`, 'success')
    }
    if (targetServerId) {
      fetchTools(targetServerId)
      fetchServerUsage(targetServerId).then((usage) => {
        if (selectedServer.value?.id === targetServerId) selectedServerUsage.value = usage
      })
      fetchServers()
    }
    selectedToolIds.value.clear()
  } finally {
    loading.value = false
  }
}

const publishedToolsCount = computed(() => tools.value.filter(tool => tool.is_published && tool.is_available !== false).length)
const isAllToolsUnpublished = computed(() => {
  const availableTools = tools.value.filter(tool => tool.is_available !== false)
  return availableTools.length > 0 && publishedToolsCount.value === 0
})
const isPublishingAllCurrent = ref(false)

const publishAllCurrentServerTools = async () => {
  const targetServerId = selectedServer.value?.id
  if (!targetServerId || !canManageSelectedTools.value) return
  const unpub = tools.value.filter(tool => !tool.is_published && tool.is_available !== false)
  if (unpub.length === 0) return

  isPublishingAllCurrent.value = true
  try {
    const results = await Promise.allSettled(
      unpub.map(tool => axios.put(`/api/portal/mcp/tools/${tool.id}/publish?published=true`))
    )
    const failed = results.filter(item => item.status === 'rejected').length
    if (failed > 0) {
      showToast(`已发布 ${unpub.length - failed} 个，${failed} 个失败`, 'warning')
    } else {
      showToast(`成功发布全部 ${unpub.length} 个工具`, 'success')
    }
    fetchTools(targetServerId)
    fetchServerUsage(targetServerId).then((usage) => {
      if (selectedServer.value?.id === targetServerId) selectedServerUsage.value = usage
    })
    fetchServers()
    selectedToolIds.value.clear()
  } finally {
    isPublishingAllCurrent.value = false
  }
}

// Headers Editing Logic
const headerMode = ref<'simple' | 'advanced'>('simple')
type HeaderPair = {
  key: string
  value: string
  maskedValue?: string
  existing?: boolean
  editing?: boolean
  changed?: boolean
  removed?: boolean
}

const headerPairs = ref<HeaderPair[]>([{ key: '', value: '' }])
const authHeadersTouched = ref(false)
const authorizationEnabled = ref(false)
const authorizationEditing = ref(false)
const authorizationToken = ref('')
// 编辑态记录已保存地址：地址未变时连接测试可复用已保存凭据，地址变更后必须重新输入 Token。
const savedSseUrl = ref('')

const verifyReusesSavedAuth = computed(() => (
  isEditing.value
  && Boolean(editingId.value)
  && Boolean(savedSseUrl.value)
  && newServer.value.sse_url.trim() === savedSseUrl.value
))

const verifyAddressChanged = computed(() => (
  isEditing.value
  && Boolean(editingId.value)
  && Boolean(savedSseUrl.value)
  && newServer.value.sse_url.trim() !== savedSseUrl.value
))

const isAuthorizationHeader = (key: string) => key.trim().toLowerCase() === 'authorization'

const updateAuthorizationEnabled = (enabled: boolean) => {
  authorizationEnabled.value = enabled
  if (enabled) {
    authorizationEditing.value = true
  } else {
    authorizationEditing.value = false
    authorizationToken.value = ''
  }
}

const startAuthorizationEdit = () => {
  authorizationEditing.value = true
  authorizationToken.value = ''
}

const cancelAuthorizationEdit = () => {
  authorizationEditing.value = false
  authorizationToken.value = ''
}

const handleAuthorizationInput = (event: Event) => {
  authorizationToken.value = (event.target as HTMLInputElement).value
}

const addHeaderPair = () => {
  headerPairs.value.push({ key: '', value: '' })
}

const removeHeaderPair = (index: number) => {
  const pair = headerPairs.value[index]
  if (pair?.existing) {
    pair.removed = true
    pair.changed = true
    pair.editing = false
    return
  }
  headerPairs.value.splice(index, 1)
  if (headerPairs.value.length === 0) addHeaderPair()
}

const editHeaderPair = (index: number) => {
  const pair = headerPairs.value[index]
  if (!pair) return
  pair.editing = true
  pair.changed = false
  pair.value = ''
}

const cancelHeaderPairEdit = (index: number) => {
  const pair = headerPairs.value[index]
  if (!pair) return
  pair.editing = false
  pair.changed = false
  pair.value = ''
}

const restoreHeaderPair = (index: number) => {
  const pair = headerPairs.value[index]
  if (!pair) return
  pair.removed = false
  pair.changed = false
}

const newServer = ref({
  server_name: '',
  remark: '',
  sse_url: '',
  auth_headers: '{}',
  enabled_status: 1,
  credential_mode: 'static' as 'static' | 'fixed_token_signed_user',
  user_assertion_enabled: false,
  user_assertion_header: 'X-Nanzi-User-Assertion',
  user_assertion_audience: '',
  user_assertion_key_id: '',
  user_assertion_issuer: 'nanzi-platform',
})

/**
 * 开关已打开但库里还没有这份配置：签名私钥要等保存时才生成，
 * 此时探测请求无法签发断言，必须提供「先保存」的入口。
 */
const userAssertionPendingSave = computed(() => {
  if (!newServer.value.user_assertion_enabled) return false
  return !isEditing.value || !savedUserAssertionEnabled.value
})

const buildServerPayload = (server: any) => {
  const isFormPayload = isEditing.value || server === newServer.value
  const payload: Record<string, any> = {
    ...server,
    scope: props.scope,
    credential_mode: server.credential_mode || 'static',
    user_assertion_enabled: Boolean(server.user_assertion_enabled),
    user_assertion_header: server.user_assertion_header || 'X-Nanzi-User-Assertion',
    user_assertion_audience: server.user_assertion_audience || null,
    user_assertion_key_id: server.user_assertion_key_id || null,
    user_assertion_issuer: server.user_assertion_issuer || 'nanzi-platform',
  }

  if (isFormPayload) {
    payload.authorization_enabled = authorizationEnabled.value
    if (authorizationEnabled.value && authorizationEditing.value && authorizationToken.value.trim()) {
      payload.fixed_token = authorizationToken.value.trim()
    }

    if (isEditing.value) {
      delete payload.auth_headers
      const patch: Record<string, string | null> = {}
      headerPairs.value.forEach((pair) => {
        const key = pair.key.trim()
        if (!key || isAuthorizationHeader(key)) return
        if (pair.existing) {
          if (pair.removed && pair.changed) patch[key] = null
          else if (pair.changed && pair.value.trim()) patch[key] = pair.value.trim()
        } else if (pair.value.trim()) {
          patch[key] = pair.value.trim()
        }
      })
      if (Object.keys(patch).length) payload.auth_headers_patch = patch
    } else if (headerMode.value === 'simple') {
      const dynamicHeaders: Record<string, string> = {}
      headerPairs.value.forEach((pair) => {
        const key = pair.key.trim()
        if (key && !isAuthorizationHeader(key) && pair.value.trim()) {
          dynamicHeaders[key] = pair.value.trim()
        }
      })
      payload.auth_headers = JSON.stringify(dynamicHeaders, null, 2)
    } else {
      try {
        const advancedHeaders = JSON.parse(newServer.value.auth_headers || '{}')
        if (advancedHeaders && typeof advancedHeaders === 'object' && !Array.isArray(advancedHeaders)) {
          Object.keys(advancedHeaders).forEach((key) => {
            if (isAuthorizationHeader(key)) delete advancedHeaders[key]
          })
        }
        payload.auth_headers = JSON.stringify(advancedHeaders || {}, null, 2)
      } catch {
        payload.auth_headers = '{}'
      }
    }
  } else if (isEditing.value && !authHeadersTouched.value) {
    // 兼容其他调用方：编辑时未修改认证区域，后端保留原配置。
    delete payload.auth_headers
  }
  return payload
}

const authHelp = ref<{ title: string, content: string } | null>(null)
const openAuthHelp = (title: string, content: string) => {
  authHelp.value = { title, content }
}
const closeAuthHelp = () => {
  authHelp.value = null
}

const showPayloadHelp = ref(false)
const payloadFieldRows = [
  { location: 'HTTP Header', field: 'X-Nanzi-User-Assertion', required: '开启时必有', usage: '业务 MCP 读取完整 JWS，并交给验签中间件。' },
  { location: 'HTTP Header', field: 'X-Request-ID', required: '必有', usage: '关联 NanZi 与业务 MCP 两侧日志。' },
  { location: 'JWT Header', field: 'alg', required: '必有', usage: '签名算法，当前为 EdDSA（Ed25519）。' },
  { location: 'JWT Header', field: 'kid', required: '必有', usage: '公钥版本编号；业务方据此从 JWKS 选择公钥。' },
  { location: 'JWT Header', field: 'typ', required: '必有', usage: '令牌类型，当前为 JWT。' },
  { location: 'JWT Payload', field: 'iss', required: '必有', usage: '签发方，固定为 nanzi-platform；校验 iss。' },
  { location: 'JWT Payload', field: 'aud', required: '必有', usage: '目标 MCP，系统按 MCP ID 自动生成；校验 aud。' },
  { location: 'JWT Payload', field: 'sub', required: '必有', usage: '稳定主体标识，格式为 nanzi:user:{user_id}。' },
  { location: 'user_context', field: 'user_id', required: '必有', usage: 'NanZi 用户 ID；业务方用它关联业务用户。' },
  { location: 'user_context', field: 'user_name / real_name', required: '有值时', usage: '登录名和用户姓名，按用户资料有值情况传递。' },
  { location: 'user_context', field: 'dept_code / org_path', required: '有值时', usage: '部门编码和组织路径，按用户资料有值情况传递。' },
  { location: 'custom_attributes', field: '安全扩展 key-value', required: '必有（可为空对象）', usage: '来自用户资料 extra_data 的安全扩展字段，平台自动过滤敏感 key。' },
  { location: 'JWT Payload', field: 'agent_id', required: '必有', usage: '发起本次调用的智能体 ID。' },
  { location: 'JWT Payload', field: 'agent_version_id', required: '有值时', usage: '当前智能体版本 ID。' },
  { location: 'JWT Payload', field: 'agent_name', required: '有值时', usage: '当前智能体名称。' },
  { location: 'JWT Payload', field: 'request_id', required: '必有', usage: '本次 NanZi 请求链路 ID。' },
  { location: 'JWT Payload', field: 'jti', required: '必有', usage: '本次断言唯一 ID；业务方可存储它进行防重放。' },
  { location: 'JWT Payload', field: 'iat / exp', required: '必有', usage: '签发时间和过期时间，默认有效期 60 秒。' },
]
const openPayloadHelp = () => {
  showPayloadHelp.value = true
}
const closePayloadHelp = () => {
  showPayloadHelp.value = false
}

const copiedMcpValue = ref('')
const mcpAudienceValue = computed(() => {
  const serverId = editingId.value || createdServer.value?.id
  return newServer.value.user_assertion_audience || (
    serverId ? `mcp:${serverId}` : '保存后由系统自动生成'
  )
})
const mcpIssuerValue = computed(() => newServer.value.user_assertion_issuer || 'nanzi-platform')
const mcpJwksUrl = computed(() => {
  const serverId = editingId.value || createdServer.value?.id
  if (!serverId) return ''
  const origin = typeof window !== 'undefined' ? window.location.origin : ''
  return `${origin}/.well-known/nanzi/mcp/${serverId}/jwks.json`
})

const copyMcpValue = async (value: string, label: string) => {
  if (!value || value === '保存后由系统自动生成') {
    showToast('保存 MCP 后才能复制该信息', 'warning')
    return
  }
  const copied = await copyToClipboard(value)
  if (!copied) {
    showToast(`复制${label}失败，请手动复制`, 'error')
    return
  }
  copiedMcpValue.value = label
  showToast(`${label}已复制`, 'success')
  window.setTimeout(() => {
    if (copiedMcpValue.value === label) copiedMcpValue.value = ''
  }, 1600)
}

const copyJwksUrl = () => copyMcpValue(mcpJwksUrl.value, 'JWKS 地址')

const showMcpCodeModal = ref(false)
const mcpCodeLanguage = ref<'python' | 'java'>('python')
const generatedMcpCode = computed(() => {
  const config = {
    audience: mcpAudienceValue.value,
    issuer: mcpIssuerValue.value,
    jwksUrl: mcpJwksUrl.value,
  }
  return mcpCodeLanguage.value === 'java'
    ? buildJavaMcpSample(config)
    : buildPythonMcpSample(config)
})

const openMcpCodeModal = () => {
  if (!mcpJwksUrl.value) {
    showToast('保存 MCP 后才能生成调用模拟代码', 'warning')
    return
  }
  showMcpCodeModal.value = true
}

const copyMcpCode = async () => {
  const copied = await copyToClipboard(generatedMcpCode.value)
  if (copied) showToast(`${mcpCodeLanguage.value === 'python' ? 'Python' : 'Java'} 模拟代码已复制`, 'success')
  else showToast('复制失败，请手动复制模拟代码', 'error')
}

// Sync Header Pairs to JSON string
watch(headerPairs, (newPairs) => {
  if (headerMode.value === 'simple' && !isEditing.value) {
    const obj: Record<string, string> = {}
    newPairs.forEach(p => {
      if (p.key.trim() && !isAuthorizationHeader(p.key)) obj[p.key.trim()] = p.value
    })
    newServer.value.auth_headers = JSON.stringify(obj, null, 2)
  }
}, { deep: true })

// Sync JSON string to Header Pairs
const syncJsonToPairs = () => {
  try {
    const obj = JSON.parse(newServer.value.auth_headers)
    const entries = Object.entries(obj)
    const authorizationEntry = entries.find(([key]) => isAuthorizationHeader(key))
    if (authorizationEntry) {
      authorizationEnabled.value = true
      authorizationEditing.value = true
      authorizationToken.value = String(authorizationEntry[1]).replace(/^Bearer\s+/i, '').trim()
    }
    const pairs = entries
      .filter(([key]) => !isAuthorizationHeader(key))
      .map(([k, v]) => ({ key: k, value: String(v) }))
    headerPairs.value = pairs.length > 0 ? pairs : [{ key: '', value: '' }]
  } catch (e) {
    console.error("Invalid JSON for headers")
  }
}

const toggleHeaderMode = () => {
  if (headerMode.value === 'advanced') {
    syncJsonToPairs()
    headerMode.value = 'simple'
  } else {
    headerMode.value = 'advanced'
  }
}

const fetchServers = async () => {
  loading.value = true
  try {
    const res = await axios.get('/api/portal/mcp/servers', {
      params: { scope: props.scope }
    })
    servers.value = Array.isArray(res.data) ? res.data : []
    reconcileSelectedServer()
    emit('server-count-changed', {
      scope: props.scope,
      count: servers.value.length,
    })
    serversLoaded.value = true
    // 从「收到的分享」点「去我的 MCP 看看」时列表可能刚重建，加载完再落选中
    if (pendingFocusServerId && !tryResolvePendingFocus()) {
      settlePendingFocus(false)
    }
  } catch (e) {
    settlePendingFocus(false)
    showToast(getApiErrorMessage(e, '获取 MCP 服务列表失败'), 'error')
  } finally {
    loading.value = false
  }
}

/** Echo 测试 MCP 的地址带 /mcp/echo/ 前缀；Token 与 audience 绑定实例，不能分享。 */
const isEchoTestServer = (server: any) =>
  String(server?.sse_url || '').includes('/mcp/echo/')

/** 同步时间放完整时间会在窄卡片里挤成一坨：显示相对时间，完整值留给 title */
function formatRelativeTime(value: string | null | undefined): string {
  if (!value) return ''
  const ts = new Date(value).getTime()
  if (Number.isNaN(ts)) return ''
  const diff = Date.now() - ts
  if (diff < 60_000) return '刚刚同步'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前同步`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前同步`
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)} 天前同步`
  return `${new Date(ts).toLocaleDateString('zh-CN')} 同步`
}

/** 参数名只做展示：坏 schema 不能把整个工具列表的渲染打断 */
function toolParamNames(tool: any): string[] {
  try {
    const schema = typeof tool?.parameter_schema === 'string'
      ? JSON.parse(tool.parameter_schema)
      : tool?.parameter_schema
    const properties = schema?.properties
    return properties && typeof properties === 'object' ? Object.keys(properties) : []
  } catch {
    return []
  }
}

/** 卡片「⋯」操作菜单：fixed 定位，避免被列表滚动容器裁掉 */
const actionMenu = ref<{ server: any; x: number; top: number | null; bottom: number | null } | null>(null)

function openActionMenu(event: MouseEvent, server: any) {
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  const width = 192
  const x = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8))
  // 下方放不下时向上展开；用 bottom 定位，菜单底部正好贴住按钮顶部，不依赖高度估算
  const openUp = window.innerHeight - rect.bottom < 240
  actionMenu.value = {
    server,
    x,
    top: openUp ? null : rect.bottom + 6,
    bottom: openUp ? window.innerHeight - rect.top + 6 : null,
  }
}

function closeActionMenu() {
  actionMenu.value = null
}

function runServerAction(action: 'share' | 'logs' | 'edit' | 'sync' | 'delete') {
  const server = actionMenu.value?.server
  closeActionMenu()
  if (!server) return
  if (action === 'share') emit('share-server', server)
  else if (action === 'logs') openLogModal(server)
  else if (action === 'edit') openEditModal(server)
  else if (action === 'sync') syncTools(server.id)
  else if (action === 'delete') confirmDeleteServer(server)
}

const createEchoTestMcp = async () => {
  if (!canSave.value) return
  if (props.scope === 'personal') {
    if (!canCreateMcp.value) {
      showToast('没有创建个人 MCP 服务的权限', 'error')
      return
    }
  } else if (!canEditMcp.value) {
    showToast('没有创建 Echo 测试 MCP 的权限', 'error')
    return
  }

  try {
    const response = await axios.post('/api/portal/mcp/servers/echo-test', null, {
      params: { scope: props.scope },
    })
    await fetchServers()
    const created = servers.value.find((server: any) => server.id === response.data?.id)
    if (created) selectServer(created)
    showToast(
      props.scope === 'personal'
        ? '个人 Echo 测试 MCP 已就绪，可用于验证本人调用链路的用户身份透传'
        : 'Echo 测试 MCP 已就绪，所有智能体都可以挂载',
      'success',
    )
  } catch (error: any) {
    showToast(getApiErrorMessage(error, '创建 Echo 测试 MCP 失败'), 'error')
  }
}

const resetWizard = () => {
  isEditing.value = false
  editingId.value = ''
  editingOriginalName.value = ''
  wizardStep.value = 1
  createdServer.value = null
  publishAllLoading.value = false
  verifying.value = false
  savingServer.value = false
  savedUserAssertionEnabled.value = false
  discoveredTools.value = []
  newServer.value = {
    server_name: '',
    remark: '',
    sse_url: '',
    auth_headers: '{}',
    enabled_status: 1,
    credential_mode: 'static',
    user_assertion_enabled: false,
    user_assertion_header: 'X-Nanzi-User-Assertion',
    user_assertion_audience: '',
    user_assertion_key_id: '',
    user_assertion_issuer: 'nanzi-platform',
  }
  serverNameSuffix.value = ''
  connectionInputTab.value = 'manual'
  mcpJsonPaste.value = ''
  mcpJsonPasteHint.value = ''
  headerPairs.value = [{ key: '', value: '' }]
  headerMode.value = 'simple'
  authHeadersTouched.value = false
  authorizationEnabled.value = false
  authorizationEditing.value = false
  authorizationToken.value = ''
  savedSseUrl.value = ''
}

const closeWizard = () => {
  showAddModal.value = false
  resetWizard()
}

const openAddModal = (initialTab: 'manual' | 'json' = 'manual') => {
  // 这条路径也被页面级引导入口（市场/JSON 粘贴）调用，不只是按钮点击，
  // 所以权限在函数里再兜一次：没有 create 码直接提示，不打开向导。
  if (!canCreateMcp.value) {
    showToast('没有创建 MCP 服务的权限', 'error')
    return
  }
  resetWizard()
  connectionInputTab.value = initialTab
  showAddModal.value = true
}

/** 列表是否已经加载过一次（用于判断「找不到」是没加载完还是真没有） */
const serversLoaded = ref(false)
let pendingFocusServerId = ''
let pendingFocusResolve: ((found: boolean) => void) | null = null

const settlePendingFocus = (found: boolean) => {
  const resolve = pendingFocusResolve
  pendingFocusResolve = null
  pendingFocusServerId = ''
  resolve?.(found)
}

/** 列表可能还没加载完，先记下要选中的服务，等 fetchServers 回来再兑现。 */
const tryResolvePendingFocus = (): boolean => {
  if (!pendingFocusServerId) return false
  const found = servers.value.find((item: any) => item.id === pendingFocusServerId)
  if (!found) return false
  selectServer(found)
  settlePendingFocus(true)
  return true
}

/**
 * 选中指定服务，并告知是否真的选中了。
 * 返回 false 的场景：列表已加载完但没有这条（例如接受分享后又把它删了）。
 */
const focusServer = (serverId: string): Promise<boolean> => {
  if (!serverId) return Promise.resolve(false)
  return new Promise<boolean>((resolve) => {
    pendingFocusResolve?.(false)
    pendingFocusResolve = resolve
    pendingFocusServerId = serverId
    if (serversLoaded.value && !tryResolvePendingFocus()) {
      settlePendingFocus(false)
    }
  })
}

defineExpose({
  openAddModal,
  resetWizard,
  focusServer,
})

const openEditModal = (server: any) => {
  isEditing.value = true
  editingId.value = server.id
  // 记住原始完整名：无前缀（平台 Echo）/历史无用户名前缀的名字不会被重新拼前缀改写。
  editingOriginalName.value = String(server.server_name || '')
  // 只有库里已经开启（且已有私钥）时才允许直接签发断言做探测。
  savedUserAssertionEnabled.value = Boolean(server.user_assertion_enabled)
  wizardStep.value = 1
  serverNameSuffix.value = stripMcpServerNamePrefix(
    server.server_name,
    props.scope,
    userInfo.value?.user_name,
  )
  newServer.value = {
    server_name: server.server_name,
    remark: server.remark || '',
    sse_url: server.sse_url,
    auth_headers: '{}',
    enabled_status: server.enabled_status,
    credential_mode: server.credential_mode || 'static',
    user_assertion_enabled: Boolean(server.user_assertion_enabled),
    user_assertion_header: server.user_assertion_header || 'X-Nanzi-User-Assertion',
    user_assertion_audience: server.user_assertion_audience || '',
    user_assertion_key_id: server.user_assertion_key_id || '',
    user_assertion_issuer: server.user_assertion_issuer || 'nanzi-platform',
  }
  authorizationEnabled.value = Boolean(server.authorization_configured)
  authorizationEditing.value = false
  authorizationToken.value = ''
  savedSseUrl.value = String(server.sse_url || '').trim()
  const maskedHeaders = Object.entries(server.masked_auth_headers || {})
  headerPairs.value = maskedHeaders.length
    ? maskedHeaders.map(([key, value]) => ({
      key,
      value: '',
      maskedValue: String(value),
      existing: true,
      editing: false,
      changed: false,
      removed: false,
    }))
    : [{ key: '', value: '' }]
  syncFullServerName()
  authHeadersTouched.value = false
  showAddModal.value = true
}

const toggleServerStatus = async (server: any, enabled: boolean) => {
  if (!canSave.value || statusLoading.value[server.id]) return false

  const nextStatus = enabled ? 1 : 0
  if (Number(server.enabled_status) === nextStatus) return true

  statusLoading.value[server.id] = true
  try {
    const response = await axios.put(`/api/portal/mcp/servers/${server.id}`, {
      ...buildServerPayload(server),
      server_name: server.server_name,
      remark: server.remark || '',
      sse_url: server.sse_url,
      enabled_status: nextStatus,
    })
    const savedStatus = Number(response.data?.enabled_status ?? nextStatus)
    server.enabled_status = savedStatus
    if (selectedServer.value?.id === server.id) {
      selectedServer.value = { ...selectedServer.value, enabled_status: savedStatus }
      fetchTools(server.id)
    }
    showToast(savedStatus === 1 ? 'MCP 服务已启用' : 'MCP 服务已禁用', 'success')
    return true
  } catch (e: any) {
    showToast(getApiErrorMessage(e, '更新 MCP 服务状态失败'), 'error')
    return false
  } finally {
    statusLoading.value[server.id] = false
  }
}

const fetchServerUsage = async (serverId: string): Promise<McpServerUsage | null> => {
  usageLoading.value[serverId] = true
  try {
    const response = await axios.get(`/api/portal/mcp/servers/${serverId}/usage`)
    return response.data
  } catch (e: any) {
    showToast(getApiErrorMessage(e, '获取 MCP 使用情况失败'), 'error')
    return null
  } finally {
    usageLoading.value[serverId] = false
  }
}

const refreshSelectedServerUsage = async () => {
  const serverId = selectedServer.value?.id
  if (!serverId) return
  const usage = await fetchServerUsage(serverId)
  // 刷新期间用户可能已切到别的服务；只在还是同一个服务时写回，避免串数据。
  if (usage && selectedServer.value?.id === serverId) selectedServerUsage.value = usage
}

const openAgentUsageDrawer = async () => {
  if (!selectedServer.value) return
  showAgentUsageDrawer.value = true
  // 正在刷新（连点或切服务后的自动拉取）时不重复发起：抽屉先展示已有数据。
  if (usageLoading.value[selectedServer.value.id]) return
  // 统计行里的数据可能是几分钟前拉的；打开时静默刷新，失败则保留旧值（fetchServerUsage 已提示）。
  await refreshSelectedServerUsage()
}

const closeAgentUsageDrawer = () => {
  showAgentUsageDrawer.value = false
}

const formatUsageImpact = (usage: McpServerUsage | null, action: '禁用' | '删除') => {
  if (!usage || usage.bound_agent_count === 0) {
    return `${action}后，关联的 MCP 工具将立即不可用。`
  }

  const names = usage.agents
    .slice(0, 5)
    .map(agent => agent.display_name || agent.name)
    .join('、')
  const more = usage.agents.length > 5 ? ` 等 ${usage.agents.length} 个智能体` : ''
  return `${action}后，关联的 MCP 工具将立即不可用。\n受影响智能体：${names}${more}\n其中当前生效：${usage.active_agent_count} 个。`
}

const handleServerStatusChange = async (server: any, enabled: boolean) => {
  if (enabled) {
    await toggleServerStatus(server, true)
    return
  }

  if (statusLoading.value[server.id]) return
  statusLoading.value[server.id] = true
  try {
    const usage = await fetchServerUsage(server.id)
    if (!usage) return
    statusConfirmServer.value = server
    statusConfirmUsage.value = usage
    showStatusConfirm.value = true
  } finally {
    statusLoading.value[server.id] = false
  }
}

const executeStatusChange = async () => {
  if (!statusConfirmServer.value) return
  statusConfirmLoading.value = true
  const success = await toggleServerStatus(statusConfirmServer.value, false)
  statusConfirmLoading.value = false
  if (success) {
    showStatusConfirm.value = false
    statusConfirmServer.value = null
    statusConfirmUsage.value = null
  }
}

const cancelStatusConfirm = () => {
  if (statusConfirmLoading.value) return
  showStatusConfirm.value = false
  statusConfirmServer.value = null
  statusConfirmUsage.value = null
}

const applyMcpJsonPaste = (options?: { connect?: boolean }) => {
  const result = parseMcpServersPaste(mcpJsonPaste.value)
  if (!result.ok) {
    mcpJsonPasteHint.value = result.error
    showToast(result.error, 'warning')
    return false
  }
  const entry = result.entries[0]
  if (!entry) {
    mcpJsonPasteHint.value = '未解析到有效的 MCP 配置'
    showToast(mcpJsonPasteHint.value, 'warning')
    return false
  }
  newServer.value.sse_url = entry.url

  const headerEntries = Object.entries(entry.headers || {})
  const authorizationEntry = headerEntries.find(([key]) => isAuthorizationHeader(key))
  authorizationEnabled.value = Boolean(authorizationEntry)
  authorizationEditing.value = Boolean(authorizationEntry)
  authorizationToken.value = authorizationEntry
    ? String(authorizationEntry[1]).replace(/^Bearer\s+/i, '').trim()
    : ''
  const dynamicHeaderEntries = headerEntries.filter(([key]) => !isAuthorizationHeader(key))
  if (dynamicHeaderEntries.length) {
    headerMode.value = 'simple'
    headerPairs.value = dynamicHeaderEntries.map(([key, value]) => ({ key, value }))
    newServer.value.auth_headers = JSON.stringify(Object.fromEntries(dynamicHeaderEntries), null, 2)
  } else {
    headerPairs.value = [{ key: '', value: '' }]
    newServer.value.auth_headers = '{}'
  }

  const suggested = suggestMcpNameSuffixFromKey(entry.key)
  if (suggested) {
    serverNameSuffix.value = suggested
    syncFullServerName()
  }

  if (entry.key) {
    newServer.value.remark = `来自配置：${entry.key}${entry.type ? `（${entry.type}）` : ''}`
  }

  mcpJsonPasteHint.value = result.warning
    || `已解析「${entry.key}」→ ${entry.url}`

  if (options?.connect) {
    // JSON 粘贴同样是新建流程：开关未保存时先跳过断言探测，保存时再生成密钥。
    void handleVerify({ skipUserAssertion: userAssertionPendingSave.value })
  } else {
    showToast(`已从 JSON 解析：${entry.key}`, 'success')
  }
  return true
}

/**
 * 名字后缀为空时按地址推导一个不冲突的后缀。
 *
 * 第一步表单里没有后缀输入框，用户直接点「保存并启用透传」时后缀仍是空的；
 * 这里在保存前补齐，避免"唯一的主按钮点了没反应"。
 */
const ensureServerNameSuffixFromUrl = () => {
  if (normalizeMcpServerNameSuffix(serverNameSuffix.value)) return false
  const existingNames = servers.value
    .filter((server: any) => server.id !== editingId.value)
    .map((server: any) => server.server_name)
  serverNameSuffix.value = suggestUniqueServerNameSuffix(
    newServer.value.sse_url,
    props.scope,
    userInfo.value?.user_name,
    existingNames,
  )
  syncFullServerName()
  return true
}

const handleVerify = async (options?: { skipUserAssertion?: boolean }) => {
  if (!newServer.value.sse_url) {
    showToast(connectionInputTab.value === 'json' ? '请先粘贴并解析有效的 MCP JSON' : '请输入服务地址', 'warning')
    return
  }
  
  verifying.value = true
  try {
    const payload = buildServerPayload(newServer.value)
    if (options?.skipUserAssertion) {
      // 未保存的透传配置还没有私钥：本次只验证地址与 Token，断言等保存后再测。
      payload.user_assertion_enabled = false
    }
    if (isEditing.value && editingId.value) {
      // 编辑态带上 server_id：后端复用已保存的凭据完成探测（地址变更也沿用）。
      payload.server_id = editingId.value
    }
    const res = await axios.post('/api/portal/mcp/verify', payload)
    discoveredTools.value = res.data.tools
    wizardStep.value = 2
    ensureServerNameSuffixFromUrl()
    showToast('连接成功，已发现工具', 'success')
  } catch (e: any) {
    showToast(getApiErrorMessage(e, '连接失败，请检查地址或认证信息'), 'error')
  } finally {
    verifying.value = false
  }
}

const addServer = async (options?: { closeAfterSave?: boolean }) => {
  // 按钮已用 savingServer 禁用，这里再兜一层：双击/回车连发不能变成两次 POST/PUT。
  if (savingServer.value) return false
  // 新建走 POST /servers（后端要求 create 码）；编辑走 PUT（要求 edit 码）。
  // 按钮隐藏 + openAddModal 守卫已挡一层，这里再兜底，避免任何入口绕到 403。
  if (!isEditing.value && !canCreateMcp.value) {
    showToast('没有创建 MCP 服务的权限', 'error')
    return false
  }
  if (isEditing.value && !canEditMcp.value) {
    showToast('没有编辑 MCP 服务的权限', 'error')
    return false
  }
  syncFullServerName()
  if (!normalizeMcpServerNameSuffix(serverNameSuffix.value)) {
    showToast('请填写服务名称后缀', 'warning')
    return false
  }
  if (!newServer.value.server_name || !newServer.value.sse_url) {
    showToast('请填写完整信息', 'warning')
    return false
  }
  if (authorizationEnabled.value && authorizationEditing.value && !authorizationToken.value.trim()) {
    showToast('请输入 Authorization Token', 'warning')
    return false
  }
  if (headerMode.value === 'advanced') {
    try {
      JSON.parse(newServer.value.auth_headers)
      syncJsonToPairs()
    }
    catch (e) { showToast('JSON 格式错误', 'error'); return false }
  }

  savingServer.value = true
  try {
    const payload = buildServerPayload(newServer.value)
    if (isEditing.value) {
      await axios.put(`/api/portal/mcp/servers/${editingId.value}`, payload)
      savedUserAssertionEnabled.value = Boolean(payload.user_assertion_enabled)
      showToast(
        options?.closeAfterSave && payload.user_assertion_enabled
          ? '已保存：用户身份传递已开启，签名密钥已生成；再次进入编辑点「连接并发现工具」可验证验签链路'
          : '更新成功',
        'success',
      )
      closeWizard()
      fetchServers()
      return true
    }

    const res = await axios.post('/api/portal/mcp/servers', payload)
    savedUserAssertionEnabled.value = Boolean(payload.user_assertion_enabled)
    if (options?.closeAfterSave) {
      showToast(
        payload.user_assertion_enabled
          ? '已保存：用户身份传递已开启，签名密钥已生成；可在列表中编辑该服务测试连接'
          : '添加成功',
        'success',
      )
      closeWizard()
      fetchServers()
      return true
    }
    showToast('添加成功', 'success')
    createdServer.value = res.data
    await fetchServers()
    const matched = servers.value.find((s: any) => s.id === res.data?.id) || res.data
    selectServer(matched)
    wizardStep.value = 3
    return true
  } catch (e: any) {
    showToast(getApiErrorMessage(e, '操作失败'), 'error')
    return false
  } finally {
    savingServer.value = false
  }
}

/**
 * 开关在库里尚未开启时，签名私钥还不存在，第一步探测必然失败。
 * 这里先保存并生成密钥，再让用户回到编辑态做一次带断言的完整测试。
 */
const saveAndEnableUserAssertion = async () => {
  // 第一步没有后缀输入框：先按地址补齐，否则唯一的主按钮只会提示"请填写后缀"。
  ensureServerNameSuffixFromUrl()
  await addServer({ closeAfterSave: true })
}

const publishAllCreatedTools = async () => {
  const targetServerId = createdServer.value?.id || selectedServer.value?.id
  if (!targetServerId) {
    closeWizard()
    return
  }
  publishAllLoading.value = true
  try {
    let serverTools = tools.value
    if (!serverTools.length || selectedServer.value?.id !== targetServerId) {
      const res = await axios.get(`/api/portal/mcp/servers/${targetServerId}/tools`)
      serverTools = Array.isArray(res.data) ? res.data : []
    }
    const unpublishedTools = serverTools.filter((t: any) => !t.is_published && t.is_available !== false)
    if (unpublishedTools.length > 0) {
      const results = await Promise.allSettled(
        unpublishedTools.map((t: any) =>
          axios.put(`/api/portal/mcp/tools/${t.id}/publish?published=true`)
        )
      )
      const failed = results.filter(item => item.status === 'rejected').length
      if (failed > 0) {
        showToast(`已发布 ${unpublishedTools.length - failed} 个，${failed} 个失败`, 'warning')
      } else {
        showToast(`成功发布全部 ${unpublishedTools.length} 个工具`, 'success')
      }
    } else {
      showToast('没有需要发布的工具', 'info')
    }
    // 部分失败也刷新列表，让用户看到真实状态后手动处理。
    if (selectedServer.value?.id === targetServerId) fetchTools(targetServerId)
    fetchServers()
    closeWizard()
  } catch (e: any) {
    showToast(getApiErrorMessage(e, '批量发布失败，请前往右侧列表手动操作'), 'error')
  } finally {
    publishAllLoading.value = false
  }
}

// Deletion Logic
const showDeleteConfirm = ref(false)
const serverToDelete = ref<string | null>(null)
const deleteServerUsage = ref<McpServerUsage | null>(null)
const deleteLoading = ref(false)

const confirmDeleteServer = async (server: any) => {
  const usage = await fetchServerUsage(server.id)
  if (!usage) return
  serverToDelete.value = server.id
  deleteServerUsage.value = usage
  showDeleteConfirm.value = true
}

const executeDeleteServer = async () => {
  if (!serverToDelete.value) return
  const deletingId = serverToDelete.value
  deleteLoading.value = true
  try {
    const response = await axios.delete(`/api/portal/mcp/servers/${deletingId}`)
    // 后端会顺手清理版本里的死引用；告诉用户不用再手动去智能体里解绑。
    const affectedVersions = Number(response.data?.affected_agent_versions || 0)
    showToast(
      affectedVersions > 0
        ? `删除成功，已从 ${affectedVersions} 个智能体版本中移除失效引用`
        : '删除成功',
      'success',
    )
    showDeleteConfirm.value = false
    serverToDelete.value = null
    deleteServerUsage.value = null
    fetchServers()
    if (selectedServer.value?.id === deletingId) {
      selectedServer.value = null
      selectedServerUsage.value = null
    }
  } catch (e) {
    showToast(getApiErrorMessage(e, '删除失败'), 'error')
  } finally {
    deleteLoading.value = false
  }
}

const cancelDeleteServer = () => {
  if (deleteLoading.value) return
  showDeleteConfirm.value = false
  serverToDelete.value = null
  deleteServerUsage.value = null
}

const syncGuardServerId = ref<string | null>(null)
const syncGuardMessage = ref('')
const showSyncGuardConfirm = ref(false)

const syncTools = async (id: string, options?: { force?: boolean }) => {
  if (syncLoading.value[id]) return

  syncLoading.value[id] = true
  try {
    const response = await axios.post(
      `/api/portal/mcp/servers/${id}/sync`,
      null,
      options?.force ? { params: { force: true } } : undefined,
    )
    const remoteDeletedCount = Number(response.data?.remote_deleted_count || 0)
    showToast(
      remoteDeletedCount > 0
        ? `同步成功，已标记 ${remoteDeletedCount} 个远端已删除工具`
        : '同步成功',
      'success',
    )
    fetchServers()
    if (selectedServer.value?.id === id) {
        fetchTools(id)
        fetchServerUsage(id).then((usage) => {
          if (selectedServer.value?.id === id) selectedServerUsage.value = usage
        })
    }
  } catch (e: any) {
    if (!options?.force && e?.response?.status === 409) {
      // 远端返回 0 个工具、本地仍有可用工具：后端默认拒绝，避免一次异常把工具全下线。
      syncGuardServerId.value = id
      syncGuardMessage.value = String(
        e?.response?.data?.detail || '远端返回 0 个工具，疑似异常，已拒绝本次同步。',
      )
      showSyncGuardConfirm.value = true
      return
    }
    showToast(getApiErrorMessage(e, '同步失败'), 'error')
  } finally {
    syncLoading.value[id] = false
  }
}

const confirmForceSync = async () => {
  const id = syncGuardServerId.value
  showSyncGuardConfirm.value = false
  syncGuardServerId.value = null
  if (id) await syncTools(id, { force: true })
}

const cancelForceSync = () => {
  showSyncGuardConfirm.value = false
  syncGuardServerId.value = null
}

/**
 * 清理「远端已删除」工具：只删失效缓存与智能体版本里的死引用，
 * 不动服务配置与出站调用审计（删服务才会连带清空审计日志）。
 */
const showCleanupConfirm = ref(false)
const cleanupStaleLoading = ref(false)
const staleToolNames = computed(() =>
  tools.value
    .filter((tool: any) => tool.is_available === false)
    .map((tool: any) => tool.tool_name),
)
const staleToolCount = computed(
  () => Number(selectedServer.value?.stale_tool_count || staleToolNames.value.length),
)

const cleanupStaleTools = async () => {
  const server = selectedServer.value
  if (!server || cleanupStaleLoading.value) return
  cleanupStaleLoading.value = true
  try {
    const response = await axios.delete(`/api/portal/mcp/servers/${server.id}/stale-tools`)
    showToast(response.data?.message || '已清理远端已删除的工具', 'success')
    showCleanupConfirm.value = false
    // 后端已删干净：先就地把计数清零，避免刷新返回前按钮和提示还挂着
    if (selectedServer.value?.id === server.id) selectedServer.value.stale_tool_count = 0
    fetchServers()
    fetchTools(server.id)
  } catch (e: any) {
    showToast(getApiErrorMessage(e, '清理失败'), 'error')
  } finally {
    cleanupStaleLoading.value = false
  }
}

const fetchTools = async (serverId: string) => {
  const seq = ++toolsRequestSeq
  toolsLoading.value = true
  try {
    const res = await axios.get(`/api/portal/mcp/servers/${serverId}/tools`)
    if (seq !== toolsRequestSeq || selectedServer.value?.id !== serverId) return
    tools.value = Array.isArray(res.data) ? res.data : []
  } catch (e) {
    if (seq === toolsRequestSeq) {
      showToast(getApiErrorMessage(e, '获取工具列表失败'), 'error')
    }
  } finally {
    if (seq === toolsRequestSeq) toolsLoading.value = false
  }
}

const selectServer = (server: any) => {
  selectedServer.value = server
  selectedServerUsage.value = null
  selectedToolIds.value = new Set()
  // 先清空，避免新服务的标题下短暂展示上一个服务的工具；同时让旧请求失效。
  tools.value = []
  toolsRequestSeq += 1
  fetchTools(server.id)
  fetchServerUsage(server.id).then((usage) => {
    if (selectedServer.value?.id === server.id) selectedServerUsage.value = usage
  })
}

const serverToolsPanelRef = ref<HTMLElement | null>(null)

/** 发布状态：0 已发布和全部已发布绝不能同一种绿色，否则列表在替异常背书。 */
const serverPublishState = (server: any): 'disabled' | 'empty' | 'none' | 'partial' | 'full' => {
  if (server.enabled_status !== 1) return 'disabled'
  const total = Number(server.tool_count || 0)
  const published = Number(server.published_tool_count || 0)
  if (total <= 0) return 'empty'
  if (published <= 0) return 'none'
  if (published < total) return 'partial'
  return 'full'
}

const unpublishedToolCount = (server: any) =>
  Math.max(0, Number(server.tool_count || 0) - Number(server.published_tool_count || 0))

/**
 * 卡片上的「去发布」：选中服务并把视线带到右侧的一键发布条。
 * 不直接执行批量发布——一次改变多个工具的可见性该由用户在面板里确认。
 */
const goPublishServerTools = (server: any) => {
  selectServer(server)
  nextTick(() => {
    serverToolsPanelRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  })
}

/** 移动端从工具详情返回服务列表 */
const clearSelectedServer = () => {
  selectedServer.value = null
  selectedServerUsage.value = null
  showAgentUsageDrawer.value = false
  tools.value = []
  selectedToolIds.value = new Set()
  toolsRequestSeq += 1
}

const togglePublish = async (tool: any) => {
  if (!canManageTool(tool)) return
  try {
    const newStatus = !tool.is_published
    await axios.put(`/api/portal/mcp/tools/${tool.id}/publish?published=${newStatus}`)
    tool.is_published = newStatus
    showToast(newStatus ? '工具已发布' : '工具已下线', 'success')
    // 列表卡片上的“N 已发布”来自 server 记录；只改 tool 会留下旧计数。
    fetchServers()
    if (selectedServer.value) {
      fetchServerUsage(selectedServer.value.id).then((usage) => {
        if (selectedServer.value) selectedServerUsage.value = usage
      })
    }
  } catch (e) {
    showToast(getApiErrorMessage(e, '操作失败'), 'error')
  }
}

onMounted(fetchServers)
</script>

<template>
  <div class="flex h-full min-h-[28rem] min-w-0 flex-col gap-3 lg:min-h-0 lg:flex-row lg:gap-6">
    <!-- Left: Server List — 移动端选中服务后隐藏，避免左右挤成一条 -->
    <div
      class="flex w-full flex-col overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm lg:w-[360px] lg:shrink-0 xl:w-[400px]"
      :class="selectedServer ? 'hidden lg:flex' : 'flex'"
    >
      <!--
        市场入口：有服务时收成一行链接，低频外链不该占列表首屏；
        列表为空时才展开成引导卡。
      -->
      <a
        v-if="!loading && servers.length > 0"
        href="https://modelscope.cn/mcp"
        target="_blank"
        rel="noopener noreferrer"
        class="flex items-center gap-1.5 border-b border-gray-100 bg-gray-50/60 px-3 py-1.5 text-[11px] text-gray-500 transition-colors hover:text-indigo-600 dark:border-gray-800 dark:bg-gray-800/40 dark:text-gray-400 dark:hover:text-indigo-300 sm:px-4"
      >
        <ShoppingBagIcon class="h-3.5 w-3.5 shrink-0" />
        <span class="truncate">去魔搭发现更多 MCP 服务</span>
        <span class="ml-auto shrink-0" aria-hidden="true">→</span>
      </a>

      <!-- Market Guide (only when the list is empty) -->
      <div
        v-else-if="!loading"
        class="border-b border-white/10 px-3 py-2 text-white transition-colors duration-300 sm:px-4 sm:py-2.5"
        :class="props.scope === 'personal' ? 'bg-slate-950' : 'bg-slate-900'"
      >
        <div class="flex items-center justify-between gap-3">
          <div class="min-w-0 flex-1">
            <h4
              class="flex items-center text-sm font-black transition-colors duration-300"
              :class="props.scope === 'personal' ? 'text-emerald-400' : 'text-indigo-400'"
            >
              <ShoppingBagIcon class="mr-1.5 h-4 w-4 shrink-0" />
              探索 MCP 市场
            </h4>
            <p class="mt-0.5 text-[10px] leading-relaxed text-slate-400 truncate">
              {{ props.scope === 'personal' ? '去魔搭寻找并接入私有扩展' : '去魔搭寻找更多公共工具集' }}
            </p>
          </div>
          <div class="shrink-0">
            <a
              href="https://modelscope.cn/mcp"
              target="_blank"
              rel="noopener noreferrer"
              class="inline-flex items-center rounded px-2.5 py-1 text-[10px] font-bold text-white shadow-sm transition-all duration-200 whitespace-nowrap"
              :class="props.scope === 'personal' ? 'bg-emerald-600 hover:bg-emerald-500' : 'bg-indigo-600 hover:bg-indigo-500'"
            >
              立即前往市场
              <MagnifyingGlassIcon class="ml-1 h-3 w-3" />
            </a>
          </div>
        </div>
      </div>

      <div class="flex flex-col gap-2 border-b border-gray-100 bg-gray-50/80 px-3 py-2 sm:px-4 sm:py-2.5">
        <!-- 第一行：搜索 + 新建。搜索是列表页第一动作，吃满剩余宽度 -->
        <div class="flex items-center gap-2">
          <div class="relative min-w-0 flex-1">
            <MagnifyingGlassIcon class="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
            <input
              v-model="searchQuery"
              type="search"
              placeholder="搜索服务或工具..."
              class="h-8 w-full rounded-md border border-gray-200 bg-white pl-8 pr-7 text-xs leading-none text-gray-700 shadow-sm transition-all placeholder:text-gray-400 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200"
            />
            <button
              v-if="searchQuery"
              type="button"
              @click="searchQuery = ''"
              class="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
              aria-label="清空搜索"
            >
              <XMarkIcon class="h-3.5 w-3.5" />
            </button>
          </div>

          <span v-if="props.scope === 'global' && !canSave" class="shrink-0 rounded border border-amber-200/60 bg-amber-50 px-1.5 py-0.5 text-[9px] font-normal text-amber-600">
            只读
          </span>

          <!-- 新建服务 -->
          <div v-if="canSave" class="relative ml-auto flex shrink-0 items-center">
            <!-- 个人 MCP: 添加 + 创建 Echo 测试（同一 create 码收敛） -->
            <div
              v-if="props.scope === 'personal' && canCreateMcp"
              class="relative inline-flex h-8 items-stretch rounded-md shadow-sm"
            >
              <button
                type="button"
                @click="resetWizard(); showAddModal = true"
                class="inline-flex h-full items-center justify-center rounded-l-md bg-emerald-600 px-2.5 text-[11px] font-bold leading-none text-white transition-all hover:bg-emerald-700 sm:px-3"
              >
                <PlusIcon class="mr-1 h-3.5 w-3.5 shrink-0" />
                <span>添加</span>
              </button>
              <button
                type="button"
                @click.stop="showAddDropdown = !showAddDropdown"
                class="inline-flex h-full items-center justify-center rounded-r-md border-l border-white/20 bg-emerald-600 px-1.5 text-white transition-all hover:bg-emerald-700"
                title="更多添加选项"
              >
                <ChevronDownIcon class="h-3.5 w-3.5 shrink-0 transition-transform duration-200" :class="{ 'rotate-180': showAddDropdown }" />
              </button>

              <div
                v-if="showAddDropdown"
                class="fixed inset-0 z-20 cursor-default"
                @click="showAddDropdown = false"
              ></div>

              <div
                v-if="showAddDropdown"
                class="absolute right-0 top-full z-30 mt-1.5 w-52 origin-top-right rounded-xl border border-gray-100 bg-white p-1.5 shadow-xl ring-1 ring-black/5 dark:border-gray-700 dark:bg-gray-800"
                @click.stop
              >
                <button
                  type="button"
                  @click="showAddDropdown = false; resetWizard(); showAddModal = true"
                  class="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs font-semibold text-gray-700 transition-colors hover:bg-blue-50 hover:text-primary dark:text-gray-200 dark:hover:bg-gray-700"
                >
                  <PlusIcon class="h-4 w-4 text-primary shrink-0" />
                  <div class="min-w-0 flex-1">
                    <div>添加 MCP 服务</div>
                    <div class="text-[10px] font-normal text-gray-400">配置自定义工具服务</div>
                  </div>
                </button>

                <div class="my-1 border-t border-gray-100 dark:border-gray-700"></div>
                <p class="px-2.5 pb-0.5 pt-1 text-[10px] font-normal text-gray-400">测试工具</p>
                <button
                  type="button"
                  @click="showAddDropdown = false; createEchoTestMcp()"
                  class="mt-0.5 flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs font-semibold text-indigo-700 transition-colors hover:bg-indigo-50 dark:text-indigo-400 dark:hover:bg-indigo-950/40"
                  title="创建个人 Echo 测试 MCP；不会展示固定 Token 或用户身份签名原文"
                >
                  <BeakerIcon class="h-4 w-4 text-indigo-600 shrink-0" />
                  <div class="min-w-0 flex-1">
                    <div>创建 Echo 测试 MCP</div>
                    <div class="text-[10px] font-normal text-gray-400">个人联调诊断服务</div>
                  </div>
                </button>
              </div>
            </div>

            <!-- 平台 MCP: 分裂式添加按钮 + 更多操作下拉
                 主按钮只给 canCreateMcp：只有 edit 码的角色（如内置 Echo、改服务配置）
                 不该看到「添加」，否则能打开新建向导、填完才在 POST /servers 上撞 403。
                 箭头只给 canEditMcp：「添加 MCP 服务」需 create，Echo 测试需 edit，
                 没有 edit 时下拉里没有可选项，留一个空箭头等于噪音。 -->
            <div
              v-else-if="props.scope !== 'personal' && (canCreateMcp || canEditMcp)"
              class="relative inline-flex h-8 items-stretch rounded-md shadow-sm"
            >
              <button
                v-if="canCreateMcp"
                type="button"
                @click="resetWizard(); showAddModal = true"
                class="inline-flex h-full items-center justify-center bg-primary px-2.5 text-[11px] font-bold leading-none text-white transition-all hover:bg-primary-dark sm:px-3"
                :class="canEditMcp ? 'rounded-l-md' : 'rounded-md'"
              >
                <PlusIcon class="mr-1 h-3.5 w-3.5 shrink-0" />
                <span>添加</span>
              </button>
              <button
                v-if="canEditMcp"
                type="button"
                @click.stop="showAddDropdown = !showAddDropdown"
                class="inline-flex h-full items-center justify-center bg-primary px-1.5 text-white transition-all hover:bg-primary-dark"
                :class="canCreateMcp ? 'rounded-r-md border-l border-white/20' : 'rounded-md'"
                title="更多添加选项"
              >
                <ChevronDownIcon class="h-3.5 w-3.5 shrink-0 transition-transform duration-200" :class="{ 'rotate-180': showAddDropdown }" />
              </button>

              <!-- 遮罩关闭 -->
              <div
                v-if="showAddDropdown"
                class="fixed inset-0 z-20 cursor-default"
                @click="showAddDropdown = false"
              ></div>

              <!-- 下拉浮层 -->
              <div
                v-if="showAddDropdown"
                class="absolute right-0 top-full z-30 mt-1.5 w-52 origin-top-right rounded-xl border border-gray-100 bg-white p-1.5 shadow-xl ring-1 ring-black/5 dark:border-gray-700 dark:bg-gray-800"
                @click.stop
              >
                <button
                  v-if="canCreateMcp"
                  type="button"
                  @click="showAddDropdown = false; resetWizard(); showAddModal = true"
                  class="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs font-semibold text-gray-700 transition-colors hover:bg-blue-50 hover:text-primary dark:text-gray-200 dark:hover:bg-gray-700"
                >
                  <PlusIcon class="h-4 w-4 text-primary shrink-0" />
                  <div class="min-w-0 flex-1">
                    <div>添加 MCP 服务</div>
                    <div class="text-[10px] font-normal text-gray-400">配置自定义工具服务</div>
                  </div>
                </button>

                <div class="my-1 border-t border-gray-100 dark:border-gray-700"></div>
                <p class="px-2.5 pb-0.5 pt-1 text-[10px] font-normal text-gray-400">测试工具</p>
                <button
                  v-if="canEditMcp"
                  type="button"
                  @click="showAddDropdown = false; createEchoTestMcp()"
                  class="mt-0.5 flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs font-semibold text-indigo-700 transition-colors hover:bg-indigo-50 dark:text-indigo-400 dark:hover:bg-indigo-950/40"
                  title="创建平台内置 Echo 测试 MCP；不会展示固定 Token 或用户身份签名原文"
                >
                  <BeakerIcon class="h-4 w-4 text-indigo-600 shrink-0" />
                  <div class="min-w-0 flex-1">
                    <div>创建 Echo 测试 MCP</div>
                    <div class="text-[10px] font-normal text-gray-400">平台内置联调诊断服务</div>
                  </div>
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- 第二行：筛选 + 排序 + 当前条数。固定两行，避免窄左栏把新建挤到下一行 -->
        <div class="flex flex-wrap items-center gap-2">
          <button
            type="button"
            class="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md border px-2 text-[11px] transition-colors"
            :class="onlyRunning
              ? 'border-indigo-200 bg-indigo-50 text-indigo-700 dark:border-indigo-800 dark:bg-indigo-950/40 dark:text-indigo-300'
              : 'border-gray-200 bg-white text-gray-500 hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-400'"
            :aria-pressed="onlyRunning"
            title="只显示已启用的 MCP 服务"
            @click="onlyRunning = !onlyRunning"
          >
            <span class="h-1.5 w-1.5 rounded-full" :class="onlyRunning ? 'bg-indigo-500' : 'bg-gray-300'"></span>
            仅运行中
          </button>

          <select
            v-model="sortBy"
            class="h-8 shrink-0 rounded-md border border-gray-200 bg-white px-1.5 text-[11px] text-gray-600 shadow-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300"
            aria-label="服务排序方式"
          >
            <option value="default">默认排序</option>
            <option value="recent">最近同步</option>
            <option value="name">名称</option>
            <option value="tools">工具数</option>
          </select>

          <span class="ml-auto shrink-0 text-[10px] text-gray-400">
            {{ filteredServers.length }} / {{ servers.length }} 个服务
          </span>
        </div>
      </div>

      <div class="custom-scrollbar max-h-[min(52vh,28rem)] flex-1 overflow-y-auto lg:max-h-none">
        <!-- 骨架屏：列表高度不跳，比单个 spinner 更少突兀 -->
        <div v-if="loading" class="space-y-2 p-3">
          <div
            v-for="index in 3"
            :key="index"
            class="animate-pulse rounded-lg border border-gray-100 p-3 dark:border-gray-800"
          >
            <div class="h-3 w-1/2 rounded bg-gray-100 dark:bg-gray-800"></div>
            <div class="mt-2 h-2.5 w-3/4 rounded bg-gray-100 dark:bg-gray-800"></div>
            <div class="mt-3 h-2.5 w-2/5 rounded bg-gray-100 dark:bg-gray-800"></div>
          </div>
        </div>
        <div v-else-if="servers.length === 0" class="p-8 text-center text-sm text-gray-400">
          暂无配置 MCP 服务
        </div>
        <div v-else-if="filteredServers.length === 0" class="p-8 text-center">
          <p class="text-xs text-gray-400">
            {{ searchQuery ? '未找到匹配的 MCP 服务' : '没有运行中的 MCP 服务' }}
          </p>
          <button
            type="button"
            @click="searchQuery = ''; onlyRunning = false"
            class="mt-2 text-[11px] font-medium text-primary hover:underline"
          >
            清空搜索与筛选
          </button>
        </div>
        <div v-else class="[&>*+*]:border-t [&>*+*]:border-t-gray-50 dark:[&>*+*]:border-t-gray-800">
          <div
            v-for="server in filteredServers"
            :key="server.id"
            @click="selectServer(server)"
            class="group relative cursor-pointer border-l-4 p-3 transition-colors sm:p-4"
            :class="selectedServer?.id === server.id
              ? 'border-primary bg-primary/10 ring-1 ring-inset ring-primary/25 hover:bg-primary/15 dark:bg-primary/15 dark:ring-primary/25 dark:hover:bg-primary/20'
              : 'border-transparent hover:bg-gray-50 dark:hover:bg-gray-800/50'"
            :aria-current="selectedServer?.id === server.id ? 'true' : undefined"
          >
            <div class="flex items-start justify-between gap-2">
              <div class="min-w-0 flex-1">
                <span
                  class="block truncate text-sm font-bold"
                  :class="selectedServer?.id === server.id ? 'text-primary' : 'text-gray-900 dark:text-gray-100'"
                >{{ server.server_name }}</span>
                <div v-if="server.user_assertion_enabled || isEchoTestServer(server)" class="mt-1 flex flex-wrap items-center gap-1">
                  <span
                    v-if="server.user_assertion_enabled"
                    class="inline-flex rounded border border-indigo-100 bg-indigo-50 px-1.5 py-0.5 text-[9px] font-semibold text-indigo-700 dark:border-indigo-500/30 dark:bg-indigo-500/10 dark:text-indigo-300"
                  >已启用用户身份签名</span>
                  <span
                    v-if="isEchoTestServer(server)"
                    class="inline-flex rounded border border-emerald-100 bg-emerald-50 px-1.5 py-0.5 text-[9px] font-semibold text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300"
                  >平台测试 MCP / 所有智能体可挂载</span>
                </div>
                <p
                  v-if="server.remark"
                  class="mt-1 line-clamp-2 text-[11px] leading-snug text-gray-500"
                  :title="server.remark"
                >{{ server.remark }}</p>
              </div>

              <!-- 状态只留圆点 + 开关：「运行中」文字与开关是同一件事，重复说反而占位 -->
              <div class="flex shrink-0 items-center gap-2" @click.stop>
                <span
                  class="h-1.5 w-1.5 rounded-full"
                  :class="server.enabled_status === 1 ? 'bg-emerald-500' : 'bg-gray-300 dark:bg-gray-600'"
                  :title="server.enabled_status === 1 ? '运行中' : '已禁用'"
                ></span>
                <Switch
                  :model-value="server.enabled_status === 1"
                  :disabled="!canSave || !canEditMcp || statusLoading[server.id]"
                  :loading="statusLoading[server.id]"
                  :aria-label="`${server.server_name}${server.enabled_status === 1 ? '禁用' : '启用'}`"
                  @update:model-value="handleServerStatusChange(server, $event)"
                />
                <button
                  type="button"
                  class="rounded p-1 text-gray-400 transition-all hover:bg-white hover:text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary dark:hover:bg-gray-800 dark:hover:text-gray-200"
                  :class="actionMenu?.server?.id === server.id
                    ? 'bg-gray-100 text-gray-600 opacity-100 dark:bg-gray-800 dark:text-gray-200'
                    : 'opacity-100 lg:opacity-0 lg:group-hover:opacity-100 lg:focus-visible:opacity-100'"
                  :aria-label="`${server.server_name} 的更多操作`"
                  title="更多操作"
                  @click="openActionMenu($event, server)"
                >
                  <EllipsisHorizontalIcon class="h-4 w-4" />
                </button>
              </div>
            </div>

            <p
              v-if="server.shared_from"
              class="mb-1 mt-1 text-[10px] font-medium text-emerald-600 dark:text-emerald-400"
            >
              来自 {{ server.shared_from.source_real_name || server.shared_from.source_user_name }} 的分享
            </p>

            <!-- 底部只留「一眼要判断」的指标：工具数、发布情况、远端失效、分享数、最近同步 -->
            <div class="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px]">
              <span class="rounded bg-gray-100 px-1.5 py-0.5 text-gray-500 dark:bg-gray-800 dark:text-gray-400">
                {{ server.tool_count }} 工具
              </span>
              <!-- 发布状态按比例着色：0 已发布若沿用绿色，等于替「智能体完全看不到」背书 -->
              <span
                v-if="serverPublishState(server) === 'disabled'"
                class="rounded bg-gray-100 px-1.5 py-0.5 font-bold text-gray-400 dark:bg-gray-800 dark:text-gray-500"
              >服务已禁用</span>
              <span
                v-else-if="serverPublishState(server) === 'empty'"
                class="rounded bg-gray-100 px-1.5 py-0.5 text-gray-400 dark:bg-gray-800 dark:text-gray-500"
              >暂无工具</span>
              <span
                v-else-if="serverPublishState(server) === 'none'"
                class="cursor-help rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 font-bold text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300"
                v-tooltip="'MCP 工具发布后，智能体才能在版本配置里勾选、问答时调用。当前 0 个已发布，这个服务对智能体完全不可见。'"
              >未发布 · 智能体不可见</span>
              <span
                v-else-if="serverPublishState(server) === 'partial'"
                class="cursor-help rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 font-bold text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300"
                v-tooltip="`还有 ${unpublishedToolCount(server)} 个工具未发布，智能体只能看到已发布的 ${server.published_tool_count} 个。`"
              >{{ server.published_tool_count }} / {{ server.tool_count }} 已发布</span>
              <span
                v-else
                class="rounded bg-green-50 px-1.5 py-0.5 font-bold text-green-600 dark:bg-green-900/30 dark:text-green-400"
              >{{ server.published_tool_count }} 已发布</span>
              <!-- 「远端已删除」不是错误而是存量提示：上次同步时远端没再返回这些工具。
                   文案必须解释「为什么出现 + 影响什么 + 怎么恢复」，否则用户只会当成故障。 -->
              <span
                v-if="server.stale_tool_count > 0"
                class="cursor-help text-amber-600"
                v-tooltip="`这 ${server.stale_tool_count} 个工具在上次同步时已从远端消失，常见原因是远端改名、下架，或凭证与权限收窄导致 tools/list 少返回。它们仍保留在列表里供核对，但不可发布、不可执行，也不会被智能体调用；远端恢复同名工具后，下次同步会恢复可用性，但已下线的工具需要重新发布。`"
              >{{ server.stale_tool_count }} 个远端已删除</span>
              <span
                v-if="server.share_count > 0"
                class="rounded bg-emerald-50 px-1.5 py-0.5 font-medium text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300"
              >
                已分享 {{ server.share_count }} 次
              </span>
              <span
                v-if="server.last_sync_at"
                class="ml-auto shrink-0 text-gray-400"
                :title="new Date(server.last_sync_at).toLocaleString()"
              >{{ formatRelativeTime(server.last_sync_at) }}</span>
            </div>

            <!-- 「0 发布」只报数字，用户会当成配置已完成；这里点破后果并给一个直达动作 -->
            <div
              v-if="serverPublishState(server) === 'none' || serverPublishState(server) === 'partial'"
              class="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5"
            >
              <span class="text-[10px] leading-snug text-amber-700/90 dark:text-amber-300/80">
                <template v-if="serverPublishState(server) === 'none'">工具已同步，但一个都没发布，智能体看不到</template>
                <template v-else>还有 {{ unpublishedToolCount(server) }} 个工具未发布</template>
              </span>
              <button
                v-if="canPublishMcp"
                type="button"
                class="text-[10px] font-bold text-amber-700 underline decoration-amber-300 underline-offset-2 transition-colors hover:text-amber-900 dark:text-amber-300 dark:hover:text-amber-200"
                @click.stop="goPublishServerTools(server)"
              >去发布 →</button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Right: Tool List — 移动端仅在选中服务后展示 -->
    <div
      ref="serverToolsPanelRef"
      class="flex min-h-[22rem] min-w-0 flex-1 flex-col overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm lg:min-h-0"
      :class="selectedServer ? 'flex' : 'hidden lg:flex'"
    >
      <div v-if="!selectedServer" class="flex flex-1 flex-col items-center justify-center text-gray-400">
        <SparklesIcon class="mb-4 h-12 w-12 opacity-20" />
        <p class="text-sm">请在左侧选择一个 MCP 服务查看工具</p>
      </div>
      
      <template v-else>
        <div class="flex flex-col gap-3 border-b border-gray-200 bg-slate-50 p-3 sm:flex-row sm:items-center sm:justify-between sm:p-4">
          <div class="flex min-w-0 flex-1 items-start gap-2">
            <button
              type="button"
              class="-ml-1 mt-0.5 rounded-md p-1.5 text-gray-500 hover:bg-white hover:text-gray-800 lg:hidden"
              aria-label="返回服务列表"
              @click="clearSelectedServer"
            >
              <ChevronLeftIcon class="h-5 w-5" />
            </button>
            <input 
              v-if="canSave"
              type="checkbox" 
              :checked="isAllSelected" 
              :disabled="!canManageSelectedTools"
              @change="toggleSelectAll"
              class="mr-1 mt-1 h-4 w-4 rounded border-gray-400 text-primary focus:ring-primary sm:mr-2" 
            />
            <div class="min-w-0">
              <h3 class="truncate text-sm font-bold text-slate-800">{{ selectedServer.server_name }} 工具</h3>
              <p class="mt-0.5 text-[10px] text-amber-600" v-if="!isSelectedServerEnabled">服务已禁用，工具暂不可测试、发布或下线</p>
              <p class="mt-0.5 text-[10px] text-slate-500" v-else-if="selectedToolIds.size === 0">发布后的工具智能体才可见</p>
              <p class="mt-0.5 text-[10px] font-black text-primary" v-else>已选中 {{ selectedToolIds.size }} 个项</p>
              <div v-if="selectedServerUsage" class="mt-1">
                <button
                  type="button"
                  class="group -mx-1 inline-flex max-w-full flex-wrap items-center gap-x-2 gap-y-0.5 rounded-md px-1 py-0.5 text-left text-[10px] transition-colors hover:bg-white hover:shadow-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
                  :title="selectedServerUsage.bound_agent_count > 0
                    ? `查看绑定该服务的 ${selectedServerUsage.bound_agent_count} 个智能体`
                    : '查看绑定智能体（当前没有智能体绑定）'"
                  aria-label="查看绑定该 MCP 服务的智能体清单"
                  @click="openAgentUsageDrawer"
                >
                  <span class="text-slate-500">绑定 {{ selectedServerUsage.bound_agent_count }} 个智能体</span>
                  <span class="text-emerald-600">生效 {{ selectedServerUsage.active_agent_count }} 个</span>
                  <span class="text-slate-400">{{ selectedServerUsage.bound_version_count }} 个版本配置</span>
                  <span class="inline-flex items-center gap-0.5 font-medium text-slate-400 transition-colors group-hover:text-primary">
                    查看<ChevronRightIcon class="h-3 w-3" />
                  </span>
                </button>
              </div>
              <span v-else-if="usageLoading[selectedServer.id]" class="mt-1 text-[10px] text-slate-400">正在统计使用情况...</span>
              <p
                v-if="selectedServer.sse_url"
                class="mt-1 flex min-w-0 items-center gap-1 font-mono text-[10px] text-gray-400"
                :title="selectedServer.sse_url"
              >
                <LinkIcon class="h-3 w-3 shrink-0" />
                <span class="truncate">{{ selectedServer.sse_url }}</span>
              </p>
            </div>
          </div>
          
          <div class="flex flex-wrap items-center gap-2 pl-8 sm:max-w-[60%] sm:justify-end sm:pl-0">
            <button
              v-if="canSave"
              type="button"
              @click="openLogModal(selectedServer)"
              class="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-gray-200 bg-white text-slate-500 shadow-sm transition-all hover:border-indigo-200 hover:bg-indigo-50/60 hover:text-indigo-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-200 active:scale-[0.96]"
              v-tooltip="'查看该服务的出站调用日志'"
              aria-label="查看该服务的出站调用日志"
            >
              <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <path d="M14 2v6h6" />
                <path d="M8 13h8" />
                <path d="M8 17h5" />
              </svg>
            </button>
            <button
              v-if="canSave && canSyncMcp"
              type="button"
              @click="syncTools(selectedServer.id)"
              :disabled="syncLoading[selectedServer.id]"
              class="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-gray-200 bg-white text-slate-500 shadow-sm transition-all hover:border-primary/30 hover:bg-primary/5 hover:text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 active:scale-[0.96] disabled:cursor-wait disabled:opacity-60"
              v-tooltip="syncLoading[selectedServer.id] ? '正在同步远端工具…' : '重新同步远端工具列表'"
              :aria-label="syncLoading[selectedServer.id] ? '正在同步远端工具' : '重新同步远端工具列表'"
            >
              <svg
                class="h-4 w-4"
                :class="syncLoading[selectedServer.id] ? 'animate-spin' : ''"
                viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
              >
                <path d="M21 12a9 9 0 1 1-2.636-6.364" />
                <path d="M21 3v6h-6" />
              </svg>
            </button>
            <!-- 「远端已删除」是存量噪音：给一个有权限的人可执行的动作，而不是只有提示 -->
            <button
              v-if="canSave && canDeleteMcp && staleToolCount > 0"
              type="button"
              class="relative inline-flex h-8 w-8 items-center justify-center rounded-lg border border-amber-200 bg-amber-50/60 text-amber-600 shadow-sm transition-all hover:border-amber-300 hover:bg-amber-50 hover:text-amber-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-200 active:scale-[0.96]"
              :class="cleanupStaleLoading ? 'cursor-wait opacity-60' : ''"
              :disabled="cleanupStaleLoading"
              @click="showCleanupConfirm = true"
              v-tooltip="`清理 ${staleToolCount} 个远端已删除的工具`"
              :aria-label="`清理 ${staleToolCount} 个远端已删除的工具`"
            >
              <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M3 6h18" />
                <path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2" />
                <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                <path d="M10 11v6M14 11v6" />
              </svg>
              <span class="absolute -right-1.5 -top-1.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-amber-500 px-1 text-[9px] font-bold leading-none text-white shadow-sm">{{ staleToolCount }}</span>
            </button>
          </div>
        </div>

        <!-- 批量操作条：选中工具后独立成层，不与头部图标工具栏混排 -->
        <div
          v-if="canSave && canPublishMcp && selectedToolIds.size > 0"
          class="flex flex-wrap items-center gap-2 border-b border-blue-100 bg-blue-50/70 px-3 py-2 sm:px-4 animate-fade-in"
        >
          <span class="inline-flex items-center gap-1.5 text-[11px] font-semibold text-blue-700">
            <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M9 11l3 3L22 4" />
              <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
            </svg>
            批量操作
            <span class="rounded-full bg-blue-100 px-1.5 py-0.5 text-[10px] font-bold text-blue-700">{{ selectedToolIds.size }}</span>
          </span>
          <div class="ml-auto flex flex-wrap items-center gap-2">
            <button
              type="button"
              @click="clearToolSelection"
              class="rounded-md px-2 py-1 text-[11px] font-semibold text-slate-500 transition-colors hover:bg-white hover:text-slate-700"
            >
              取消选择
            </button>
            <button
              type="button"
              @click="batchUpdateStatus(true)"
              :disabled="!canManageSelectedTools"
              class="inline-flex items-center gap-1.5 rounded-md bg-green-600 px-3 py-1.5 text-[11px] font-bold text-white shadow-sm transition-all hover:bg-green-700 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50"
            >
              <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0" />
                <circle cx="12" cy="12" r="3" />
              </svg>
              批量发布
            </button>
            <button
              type="button"
              @click="batchUpdateStatus(false)"
              :disabled="!canManageSelectedTools"
              class="inline-flex items-center gap-1.5 rounded-md bg-slate-600 px-3 py-1.5 text-[11px] font-bold text-white shadow-sm transition-all hover:bg-slate-700 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50"
            >
              <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49" />
                <path d="M14.084 14.158a3 3 0 0 1-4.242-4.242" />
                <path d="M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143" />
                <path d="m2 2 20 20" />
              </svg>
              批量下线
            </button>
          </div>
        </div>

        <!-- 全待发布提示条（当服务启用且工具全部为待发布时展示） -->
        <div 
          v-if="isSelectedServerEnabled && isAllToolsUnpublished"
          class="flex flex-col gap-2 border-b border-amber-200 bg-amber-50/90 px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between sm:px-4 animate-fade-in"
        >
          <div class="flex items-center gap-2 min-w-0">
            <InformationCircleIcon class="h-4 w-4 shrink-0 text-amber-600" />
            <span class="text-xs text-amber-900 leading-snug">
              当前服务下所有工具均为 <strong class="text-amber-800 underline decoration-amber-300">待发布</strong> 状态，智能体在配置与问答中<strong>无法搜索或调用</strong>这些工具。
            </span>
          </div>
          <button
            v-if="canManageSelectedTools && canPublishMcp"
            @click="publishAllCurrentServerTools"
            :disabled="isPublishingAllCurrent"
            class="inline-flex shrink-0 items-center justify-center rounded-md bg-amber-600 px-3 py-1.5 text-xs font-bold text-white shadow-sm transition-all hover:bg-amber-700 active:scale-95 disabled:opacity-50"
          >
            <ArrowPathIcon v-if="isPublishingAllCurrent" class="mr-1.5 h-3.5 w-3.5 animate-spin" />
            <SparklesIcon v-else class="mr-1.5 h-3.5 w-3.5" />
            {{ isPublishingAllCurrent ? '正在发布...' : '一键全部发布' }}
          </button>
        </div>

        <div class="custom-scrollbar flex-1 overflow-y-auto p-3 sm:p-4">
          <div v-if="toolsLoading" class="p-12 text-center">
            <ArrowPathIcon class="mx-auto h-8 w-8 animate-spin text-gray-200" />
          </div>
          <div v-else-if="tools.length === 0" class="p-12 text-center">
            <p class="text-sm text-gray-400">该服务下暂无同步到的工具，请点击同步按钮。</p>
          </div>
          <div v-else class="grid grid-cols-1 gap-3 sm:gap-4">
            <div 
              v-for="tool in tools" 
              :key="tool.id" 
              @click="canManageTool(tool) && toggleSelectTool(tool.id)"
              class="group flex min-w-0 flex-col gap-3 rounded-lg border p-3 transition-all sm:flex-row sm:items-start sm:justify-between sm:p-4"
              :class="[
                canManageTool(tool) ? 'cursor-pointer' : 'cursor-default',
                selectedToolIds.has(tool.id) ? 'border-primary bg-blue-50/50 shadow-sm' : 'border-gray-100 bg-gray-50/30 hover:border-primary/30'
              ]"
            >
              <div class="flex min-w-0 flex-1 items-start pr-0 sm:pr-4">
                <input 
                  v-if="canSave"
                  type="checkbox" 
                  :checked="selectedToolIds.has(tool.id)" 
                  :disabled="!canManageTool(tool)"
                  @click.stop="toggleSelectTool(tool.id)"
                  class="mr-3 mt-1 h-3.5 w-3.5 rounded border-gray-300 text-primary focus:ring-primary" 
                />
                <div class="min-w-0 flex-1">
                  <div class="mb-1 flex flex-wrap items-center gap-1.5">
                    <span class="text-sm font-bold text-gray-900 break-all">{{ tool.tool_name }}</span>
                    <span v-if="tool.usage_count > 0" class="inline-flex items-center rounded bg-blue-100 px-1.5 py-0.5 text-[9px] font-medium text-blue-700" title="被智能体引用次数">
                      <LinkIcon class="mr-0.5 h-3 w-3" />{{ tool.usage_count }}
                    </span>
                    <span v-if="!isSelectedServerEnabled" class="rounded border border-amber-100 bg-amber-50 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-tighter text-amber-600">服务已禁用</span>
                    <span
                      v-else-if="tool.is_available === false"
                      class="cursor-help rounded border border-amber-100 bg-amber-50 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-tighter text-amber-600"
                      v-tooltip="'该工具在最近一次同步时已从远端消失，因此不能勾选给智能体、不能发布，测试台也无法执行。远端重新提供同名工具后，再次同步可恢复可用性；发布状态需重新确认（或重新发布）。若远端已改名，旧记录需删除整个服务重建。'"
                    >远端已删除</span>
                    <span v-else-if="tool.is_published" class="rounded border border-green-100 bg-green-50 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-tighter text-green-600">已发布</span>
                    <span v-else class="rounded border border-gray-200 bg-gray-100 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-tighter text-gray-400">待发布</span>
                  </div>
                  <p class="line-clamp-2 text-xs leading-relaxed text-gray-600 dark:text-gray-400">
                    {{ tool.tool_description || '暂无描述' }}
                  </p>
                  <div class="mt-2 flex flex-wrap gap-1" v-if="toolParamNames(tool).length">
                    <code
                      v-for="paramName in toolParamNames(tool)"
                      :key="paramName"
                      class="rounded border bg-white px-1 text-[9px] text-gray-500 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-400"
                    >{{ paramName }}</code>
                  </div>
                </div>
              </div>
              <div v-if="canSave" class="flex shrink-0 flex-row items-center justify-end gap-2 sm:flex-col sm:items-end sm:space-y-0 sm:gap-2">
                <button 
                  @click.stop="openTester(tool)"
                  :disabled="!canManageTool(tool)"
                  class="inline-flex h-7 w-7 items-center justify-center rounded-lg border border-indigo-100 bg-white text-indigo-600 shadow-sm transition-all hover:border-indigo-200 hover:bg-indigo-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-200 active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-40 opacity-100 lg:opacity-0 lg:group-hover:opacity-100 lg:focus-visible:opacity-100"
                  v-tooltip="'在线测试'"
                  aria-label="在线测试"
                >
                  <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M10 2v7.31" />
                    <path d="M14 9.3V2" />
                    <path d="M8.5 2h7" />
                    <path d="M14 9.3a6.5 6.5 0 1 1-4 0" />
                    <path d="M5.52 16h12.96" />
                  </svg>
                </button>
                <button 
                  @click.stop="togglePublish(tool)"
                  :disabled="!canManageTool(tool) || !canPublishMcp"
                  class="inline-flex h-7 w-7 items-center justify-center rounded-lg border shadow-sm transition-all focus:outline-none focus-visible:ring-2 active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-40 opacity-100 lg:opacity-0 lg:group-hover:opacity-100 lg:focus-visible:opacity-100"
                  :class="tool.is_published ? 'border-gray-200 bg-white text-gray-500 hover:border-red-200 hover:bg-red-50/60 hover:text-red-600 focus-visible:ring-red-200' : 'border-transparent bg-primary text-white hover:bg-primary-dark focus-visible:ring-primary/30'"
                  v-tooltip="tool.is_published ? '下线该工具，智能体将不再可见' : '发布该工具，智能体配置中即可勾选'"
                  :aria-label="tool.is_published ? '下线该工具' : '发布该工具'"
                >
                  <svg v-if="tool.is_published" class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49" />
                    <path d="M14.084 14.158a3 3 0 0 1-4.242-4.242" />
                    <path d="M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143" />
                    <path d="m2 2 20 20" />
                  </svg>
                  <svg v-else class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0" />
                    <circle cx="12" cy="12" r="3" />
                  </svg>
                </button>
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>

    <!-- Tool Tester Drawer -->
    <McpToolTester 
      v-if="toolToTest"
      :tool="toolToTest" 
      :is-open="showTester" 
      @close="showTester = false" 
    />

    <!-- Add Server Modal (Connection Wizard) -->
    <div v-if="showAddModal" class="fixed inset-0 z-[60] flex items-end justify-center bg-gray-900/50 p-0 backdrop-blur-sm sm:items-center sm:p-4">
      <div class="flex max-h-[92dvh] w-full max-w-lg flex-col overflow-hidden rounded-t-2xl bg-white shadow-2xl animate-fade-in-up sm:rounded-xl">
        <!-- Wizard Header -->
        <div class="shrink-0 border-b border-gray-100 bg-gray-50/50 p-4 sm:p-6">
          <div class="flex items-center justify-between mb-4">
            <h3 class="text-lg font-bold text-gray-900">
              {{ isEditing ? '编辑配置' : (wizardStep === 1 ? '第一步：建立连接' : (wizardStep === 2 ? '第二步：确认工具并命名' : '第三步：完成与发布指引')) }}
            </h3>
            <div class="flex items-center space-x-1.5" v-if="!isEditing">
              <div class="h-2 rounded-full transition-all duration-300" :class="wizardStep === 1 ? 'bg-primary w-5' : 'bg-gray-200 w-2'"></div>
              <div class="h-2 rounded-full transition-all duration-300" :class="wizardStep === 2 ? 'bg-primary w-5' : 'bg-gray-200 w-2'"></div>
              <div class="h-2 rounded-full transition-all duration-300" :class="wizardStep === 3 ? 'bg-green-600 w-5' : 'bg-gray-200 w-2'"></div>
            </div>
          </div>
          <p class="text-xs text-gray-500">
            {{ wizardStep === 1
              ? (connectionInputTab === 'json'
                ? '粘贴 mcpServers JSON，解析后将自动连接并发现工具。'
                : '手动填写服务地址与鉴权，然后连接并发现工具。')
              : (wizardStep === 2
                ? `探测成功！共发现 ${discoveredTools.length} 个工具。请检查列表并为该服务命名。`
                : 'MCP 服务已成功接入系统，请发布工具以便在智能体中使用。') }}
          </p>
        </div>

        <!-- Wizard Step 1: Input -->
        <div v-if="wizardStep === 1" class="flex flex-1 flex-col space-y-5 overflow-y-auto p-4 sm:p-6">
          <div
            v-if="!isEditing"
            class="flex items-center gap-1 rounded-lg bg-gray-100 dark:bg-gray-800 p-0.5"
          >
            <button
              type="button"
              class="flex-1 py-1.5 text-center text-xs font-semibold rounded-md transition-colors"
              :class="connectionInputTab === 'manual'
                ? 'bg-white shadow-sm text-gray-900'
                : 'text-gray-500 hover:text-gray-700'"
              @click="connectionInputTab = 'manual'"
            >
              手动填写
            </button>
            <button
              type="button"
              class="flex-1 py-1.5 text-center text-xs font-semibold rounded-md transition-colors flex items-center justify-center gap-1"
              :class="connectionInputTab === 'json'
                ? 'bg-white shadow-sm text-indigo-700'
                : 'text-gray-500 hover:text-gray-700'"
              @click="connectionInputTab = 'json'"
            >
              <DocumentDuplicateIcon class="w-3.5 h-3.5" />
              JSON 粘贴
            </button>
          </div>

          <!-- JSON 粘贴 Tab -->
          <div v-if="connectionInputTab === 'json' && !isEditing" class="space-y-3">
            <p class="text-[11px] text-gray-500 leading-relaxed">
              支持 Cursor / Claude Desktop 的
              <code class="px-1 bg-gray-100 rounded text-[10px]">mcpServers</code>
              配置（含 streamable_http）。点击下方按钮将解析并直接连接发现工具。
            </p>
            <textarea
              v-model="mcpJsonPaste"
              rows="8"
              placeholder='{ "mcpServers": { "mcp-trends-hub": { "type": "streamable_http", "url": "https://..." } } }'
              class="w-full px-3 py-2 text-xs border border-gray-200 rounded-lg focus:ring-2 focus:ring-primary outline-none font-mono bg-white text-gray-800 disabled:bg-gray-100"
              @keydown.stop
            />
            <p v-if="mcpJsonPasteHint" class="text-[10px] text-indigo-700 leading-snug">{{ mcpJsonPasteHint }}</p>
            <p v-if="newServer.sse_url" class="text-[10px] text-gray-500 font-mono truncate" :title="newServer.sse_url">
              当前地址：{{ newServer.sse_url }}
            </p>
          </div>

          <!-- 手动填写区域与通用用户身份传递配置 -->
          <div class="contents">
            <div v-if="connectionInputTab === 'manual' || isEditing" class="order-1">
              <label class="block text-xs font-bold text-gray-700 uppercase tracking-wider mb-1.5 flex items-center">
                <LinkIcon class="w-3 h-3 mr-1" /> 服务地址（SSE / HTTP）
              </label>
              <input v-model="newServer.sse_url" placeholder="https://..." class="w-full px-3 py-2 text-sm border rounded-lg focus:ring-2 focus:ring-primary outline-none font-mono" />
              <p class="text-[10px] text-gray-400 mt-1">支持 MCP SSE 与 streamable HTTP（如 ModelScope）；连接时自动探测协议。</p>
            </div>

            <div class="order-3 rounded-lg border border-indigo-100 bg-indigo-50/50 p-3">
              <div class="flex items-center justify-between gap-3">
                <div class="min-w-0">
                  <div class="flex items-center gap-1.5">
                    <label class="text-xs font-bold text-gray-700">开启用户身份传递</label>
                    <button
                      type="button"
                      class="inline-flex h-4 w-4 items-center justify-center rounded-full border border-indigo-300 text-[10px] font-bold text-indigo-600"
                      title="用户身份传递说明"
                      @click="openAuthHelp('开启用户身份传递', '开启后，系统会把当前登录用户和当前智能体生成短期签名 UserContext，通过 X-Nanzi-User-Assertion 发送给当前 MCP。关闭时完全沿用原有身份认证方式。')"
                    >?</button>
                  </div>
                  <p class="mt-1 text-[10px] leading-relaxed text-gray-500">
                    关闭时保持原有 MCP 调用方式；开启后仅为当前 MCP 增加签名用户身份。签名私钥由系统自动生成并加密保存，业务方只使用公钥验签。
                  </p>
                  <p v-if="userAssertionPendingSave" class="mt-1 text-[10px] font-semibold leading-relaxed text-indigo-600">
                    当前开关尚未保存：签名私钥在保存时才生成，未保存前无法发送带签名的连接测试。
                  </p>
                </div>
                <Switch
                  :model-value="newServer.user_assertion_enabled"
                  aria-label="开启用户身份传递"
                  @update:model-value="newServer.user_assertion_enabled = $event"
                />
              </div>

              <div v-if="newServer.user_assertion_enabled" class="mt-4 space-y-3 border-t border-indigo-100 pt-3">
                <div>
                  <div class="mb-1 flex items-center gap-1.5">
                    <label class="text-[11px] font-semibold text-gray-700">MCP Audience（系统生成）</label>
                    <button
                      type="button"
                      class="inline-flex h-4 w-4 items-center justify-center rounded-full border border-indigo-300 text-[10px] font-bold text-indigo-600"
                      title="Audience 说明"
                      @click="openAuthHelp('MCP Audience 在哪里使用？', '系统会按当前 MCP 的 server_id 自动生成 Audience，例如 mcp:当前MCP的ID。业务方把这个只读值配置为验签时的 aud 期望值，用来防止其他 MCP 接受本 MCP 的身份断言。用户不需要填写。')"
                    >?</button>
                  </div>
                  <div class="flex items-center gap-2">
                    <input
                      :value="mcpAudienceValue"
                      readonly
                      aria-label="当前 MCP Audience"
                      class="min-w-0 flex-1 rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm font-mono text-gray-600 outline-none disabled:bg-gray-100 read-only:bg-gray-100"
                    />
                    <button
                      v-if="mcpAudienceValue !== '保存后由系统自动生成'"
                      type="button"
                      class="shrink-0 rounded border border-indigo-200 bg-indigo-50 px-2 py-1.5 text-[10px] font-semibold text-indigo-700 hover:bg-indigo-100"
                      aria-label="复制 Audience"
                      @click="copyMcpValue(mcpAudienceValue, 'Audience')"
                    >{{ copiedMcpValue === 'Audience' ? '已复制' : '复制 Audience' }}</button>
                  </div>
                </div>

                <div>
                  <div class="mb-1 flex items-center gap-1.5">
                    <label class="text-[11px] font-semibold text-gray-700">签名 Issuer（系统固定）</label>
                    <button
                      type="button"
                      class="inline-flex h-4 w-4 items-center justify-center rounded-full border border-indigo-300 text-[10px] font-bold text-indigo-600"
                      title="Issuer 说明"
                      @click="openAuthHelp('签名 Issuer 在哪里使用？', 'Issuer 表示这份用户身份签名由谁签发。系统固定使用 nanzi-platform。业务方把这个只读值配置为验签时的 iss 期望值，用户不需要填写。')"
                    >?</button>
                  </div>
                  <div class="flex items-center gap-2">
                    <input
                      :value="mcpIssuerValue"
                      readonly
                      aria-label="当前 MCP 签名 Issuer"
                      class="min-w-0 flex-1 rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm font-mono text-gray-600 outline-none disabled:bg-gray-100 read-only:bg-gray-100"
                    />
                    <button
                      type="button"
                      class="shrink-0 rounded border border-indigo-200 bg-indigo-50 px-2 py-1.5 text-[10px] font-semibold text-indigo-700 hover:bg-indigo-100"
                      aria-label="复制 Issuer"
                      @click="copyMcpValue(mcpIssuerValue, 'Issuer')"
                    >{{ copiedMcpValue === 'Issuer' ? '已复制' : '复制 Issuer' }}</button>
                  </div>
                </div>

                <div class="rounded-md border border-indigo-100 bg-white/70 p-2.5 text-[10px] leading-relaxed text-gray-500">
                  <div class="flex items-center gap-1.5 font-semibold text-gray-700">
                    <span>默认透传字段</span>
                    <button
                      type="button"
                      class="inline-flex h-4 w-4 items-center justify-center rounded-full border border-indigo-300 text-[10px] font-bold text-indigo-600"
                      title="默认透传字段说明"
                      @click="openPayloadHelp"
                    >?</button>
                  </div>
                  <p class="mt-1">用户身份结构为 user_context + custom_attributes，业务 MCP 通过验签后的 user_context.user_id 关联业务用户。</p>
                </div>

                <div class="rounded-md border border-indigo-100 bg-white/70 p-2.5 text-[10px] leading-relaxed text-gray-500">
                  <div class="flex items-center gap-1.5 font-semibold text-gray-700">
                    <span>公钥获取地址（JWKS）</span>
                    <button
                      type="button"
                      class="inline-flex h-4 w-4 items-center justify-center rounded-full border border-indigo-300 text-[10px] font-bold text-indigo-600"
                      title="业务方如何使用"
                      @click="openAuthHelp('业务方如何使用公钥验签？', `业务 MCP 不需要获取或保存 NanZi 私钥，只需配置当前 MCP 的 JWKS 地址。示例：GET ${mcpJwksUrl || 'https://<NanZi域名>/.well-known/nanzi/mcp/<server_id>/jwks.json'}，缓存返回的公钥；收到 X-Nanzi-User-Assertion 后，根据 JWT Header 的 kid 选择公钥并验证签名，同时校验 iss、aud、exp、iat 和 jti。验签成功后，从 user_context.user_id 关联业务用户。例如：const userId = claims.user_context.user_id。关闭本开关的 MCP 不会发送这个 Header。`)"
                    >?</button>
                  </div>
                  <div v-if="mcpJwksUrl" class="mt-2 flex items-center gap-2">
                    <input
                      :value="mcpJwksUrl"
                      readonly
                      aria-label="当前 MCP 公钥获取地址"
                      class="min-w-0 flex-1 rounded border border-gray-200 bg-white px-2 py-1.5 font-mono text-[10px] text-gray-600 outline-none disabled:bg-gray-100 read-only:bg-gray-100"
                    />
                    <button
                      type="button"
                      class="shrink-0 rounded border border-indigo-200 bg-indigo-50 px-2 py-1.5 font-semibold text-indigo-700 hover:bg-indigo-100"
                      aria-label="复制 JWKS 地址"
                      @click="copyJwksUrl"
                    >{{ copiedMcpValue === 'JWKS 地址' ? '已复制' : '复制 JWKS 地址' }}</button>
                  </div>
                  <p v-else class="mt-1">保存 MCP 后，系统会生成当前 MCP 专属地址；业务方访问该地址获取公钥。</p>
                </div>

                <div class="flex items-center justify-between gap-3 rounded-md border border-indigo-200 bg-indigo-50/70 p-2.5">
                  <div class="min-w-0 text-[10px] leading-relaxed text-indigo-800">
                    <div class="font-semibold">业务方接入示例</div>
                    <p>自动带入上面的 Audience、Issuer 和 JWKS 地址，生成可复制的验签示例。</p>
                  </div>
                  <button
                    type="button"
                    class="shrink-0 rounded-lg bg-indigo-600 px-3 py-2 text-[10px] font-semibold text-white shadow-sm hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
                    :disabled="!mcpJwksUrl"
                    @click="openMcpCodeModal"
                  >一键生成调用模拟代码</button>
                </div>
              </div>
            </div>
          
            <!-- Authorization Editor -->
            <div v-if="connectionInputTab === 'manual' || isEditing" class="order-2 rounded-lg border border-indigo-100 bg-indigo-50/50 p-3">
              <div class="flex items-center justify-between gap-3">
                <div class="min-w-0">
                  <label class="block text-xs font-bold text-gray-700">Authorization</label>
                  <p class="mt-1 text-[10px] leading-relaxed text-gray-500">Bearer 前缀固定，只填写 Token。</p>
                </div>
                <Switch
                  :model-value="authorizationEnabled"
                  aria-label="Authorization 开关"
                  @update:model-value="updateAuthorizationEnabled"
                />
              </div>
              <div v-if="authorizationEnabled" class="mt-3 flex items-center gap-2">
                <span class="shrink-0 rounded border border-indigo-100 bg-white px-2 py-2 font-mono text-xs text-gray-500">Bearer</span>
                <input
                  :value="authorizationEditing ? authorizationToken : '********'"
                  type="password"
                  :readonly="isEditing && !authorizationEditing"
                  placeholder="请输入 Token"
                  aria-label="Authorization Token"
                  class="min-w-0 flex-1 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-mono outline-none focus:ring-1 focus:ring-primar read-only:bg-gray-100y"
                  @input="handleAuthorizationInput"
                />
                <button
                  v-if="isEditing && !authorizationEditing"
                  type="button"
                  class="shrink-0 rounded border border-indigo-200 bg-white px-2.5 py-2 text-[10px] font-semibold text-indigo-700 hover:bg-indigo-50"
                  @click="startAuthorizationEdit"
                >编辑</button>
                <button
                  v-else-if="isEditing && authorizationEditing"
                  type="button"
                  class="shrink-0 rounded border border-gray-200 bg-white px-2.5 py-2 text-[10px] font-semibold text-gray-600 hover:bg-gray-50"
                  @click="cancelAuthorizationEdit"
                >取消</button>
              </div>
              <p v-if="isEditing && authorizationEnabled" class="mt-2 text-[10px] leading-relaxed text-amber-600">已配置 Token 不会回显；点击“编辑”后填写新 Token，保存后才会替换。</p>
              <p v-if="verifyReusesSavedAuth && authorizationEnabled && !authorizationEditing" class="mt-1 text-[10px] leading-relaxed text-gray-500">“连接并发现工具”与保存都会沿用已保存的 Token。</p>
              <p v-if="verifyAddressChanged && authorizationEnabled && !authorizationEditing" class="mt-1 text-[10px] leading-relaxed text-gray-500">地址已变更，测试与保存会继续沿用已保存的 Token；如需更换请点击“编辑”。</p>
            </div>

            <!-- Dynamic Headers Editor -->
            <div v-if="connectionInputTab === 'manual' || isEditing" class="order-2">
              <div class="mb-2 flex items-center justify-between gap-3">
                <div>
                  <label class="block text-xs font-bold uppercase tracking-wider text-gray-700">其他 Header（可选）</label>
                  <p class="mt-1 text-[10px] leading-relaxed text-gray-400">Authorization 已单独配置；这里填写其他 Header。</p>
                </div>
                <button v-if="!isEditing" type="button" @click="toggleHeaderMode" class="flex items-center text-[10px] font-bold text-primary hover:underline">
                  <component :is="headerMode === 'simple' ? CodeBracketIcon : ListBulletIcon" class="mr-1 h-3 w-3" />
                  切换到{{ headerMode === 'simple' ? '高级 JSON' : '可视化列表' }}
                </button>
              </div>

              <div v-if="headerMode === 'simple'" class="space-y-3">
                <div class="max-h-[180px] space-y-2 overflow-y-auto rounded-lg border border-gray-100 bg-gray-50 p-3 custom-scrollbar">
                  <div v-for="(pair, index) in headerPairs" :key="index" class="flex items-center gap-2">
                    <template v-if="pair.existing && !pair.editing && !pair.removed">
                      <input :value="pair.key" readonly class="min-w-0 flex-1 rounded border bg-white px-3 py-1.5 text-xs text-gray-600 outline-non read-only:bg-gray-100e" />
                      <input :value="pair.maskedValue || '********'" readonly type="password" class="min-w-0 flex-1 rounded border bg-white px-3 py-1.5 text-xs text-gray-600 outline-non read-only:bg-gray-100e" />
                      <button type="button" class="shrink-0 rounded border border-indigo-200 bg-white px-2 py-1.5 text-[10px] font-semibold text-indigo-700 hover:bg-indigo-50" @click="editHeaderPair(index)">编辑</button>
                      <button type="button" class="shrink-0 p-1.5 text-gray-400 hover:text-red-500" aria-label="删除 Header" @click="removeHeaderPair(index)">
                        <TrashIcon class="h-4 w-4" />
                      </button>
                    </template>
                    <template v-else-if="pair.existing && pair.removed">
                      <span class="min-w-0 flex-1 text-xs text-gray-400 line-through">{{ pair.key }}</span>
                      <span class="flex-1 text-[10px] text-red-500">保存后删除</span>
                      <button type="button" class="shrink-0 text-[10px] font-semibold text-indigo-700 hover:underline" @click="restoreHeaderPair(index)">撤销</button>
                    </template>
                    <template v-else>
                      <input v-model="pair.key" :readonly="pair.existing" placeholder="名称（如 X-Tenant）" class="min-w-0 flex-1 rounded border px-3 py-1.5 text-xs outline-none focus:ring-1 focus:ring-primar read-only:bg-gray-100y" @input="pair.changed = true; authHeadersTouched = true" />
                      <input v-model="pair.value" :type="pair.existing ? 'password' : 'text'" :placeholder="pair.existing ? '请输入新值' : '内容（Value）'" class="min-w-0 flex-1 rounded border px-3 py-1.5 text-xs outline-none focus:ring-1 focus:ring-primary" @input="pair.changed = true; authHeadersTouched = true" />
                      <button v-if="pair.existing" type="button" class="shrink-0 rounded border border-gray-200 bg-white px-2 py-1.5 text-[10px] font-semibold text-gray-600 hover:bg-gray-50" @click="cancelHeaderPairEdit(index)">取消</button>
                      <button v-else type="button" class="shrink-0 p-1.5 text-gray-400 hover:text-red-500" aria-label="删除 Header" @click="removeHeaderPair(index)">
                        <TrashIcon class="h-4 w-4" />
                      </button>
                    </template>
                  </div>
                  <button type="button" @click="addHeaderPair" class="mt-2 flex items-center text-[10px] font-bold text-primary hover:underline">
                    <PlusIcon class="mr-1 h-3 w-3" /> 继续添加
                  </button>
                </div>
              </div>
              <div v-else>
                <textarea v-model="newServer.auth_headers" rows="4" @input="authHeadersTouched = true" class="w-full rounded-lg border bg-gray-900 px-3 py-2 font-mono text-sm text-green-400 outline-none focus:ring-2 focus:ring-primary" placeholder='{}'></textarea>
              </div>
            </div>
          </div>
        </div>

        <!-- Wizard Step 2: Preview -->
        <div v-else-if="wizardStep === 2" class="flex-1 space-y-5 overflow-y-auto p-4 sm:p-6">
          <div>
            <label class="block text-xs font-bold text-gray-700 uppercase tracking-wider mb-1.5">服务显示名称</label>
            <div class="flex items-stretch rounded-lg border border-gray-200 overflow-hidden focus-within:ring-2 focus-within:ring-primary/40">
              <span
                class="shrink-0 px-2.5 py-2 text-sm font-mono bg-gray-100 text-gray-500 border-r border-gray-200 select-all"
                :title="namePrefix"
              >{{ namePrefix }}</span>
              <input
                v-model="serverNameSuffix"
                type="text"
                placeholder="自定义后缀，如 hcp"
                class="flex-1 min-w-0 border-0 px-3 py-2 text-sm font-mono outline-none focus:ring-0"
                @keydown.stop
              />
            </div>
            <p class="mt-1.5 text-[10px] text-gray-400 leading-relaxed">
              完整名称：
              <span class="font-mono text-gray-600">{{ newServer.server_name || `${namePrefix}…` }}</span>
            </p>
          </div>

          <div>
            <label class="block text-xs font-bold text-gray-700 uppercase tracking-wider mb-1.5">
              备注
              <span class="ml-1 font-normal text-gray-400 normal-case tracking-normal">选填</span>
            </label>
            <textarea
              v-model="newServer.remark"
              rows="2"
              maxlength="500"
              placeholder="简要说明该 MCP 的用途，便于在挂载与列表中识别"
              class="w-full px-3 py-2 text-sm border rounded-lg focus:ring-2 focus:ring-primary outline-none resize-none"
            />
          </div>
          
          <div>
            <label class="block text-xs font-bold text-gray-700 uppercase tracking-wider mb-2">发现的工具预览</label>
            <div class="bg-gray-50 rounded-lg border border-gray-100 max-h-[250px] overflow-y-auto divide-y divide-gray-200 custom-scrollbar">
              <div v-for="tool in discoveredTools" :key="tool.name" class="p-3">
                <div class="text-xs font-bold text-gray-800">{{ tool.name }}</div>
                <div class="text-[10px] text-gray-500 line-clamp-1">{{ tool.description || '无描述' }}</div>
              </div>
            </div>
          </div>
        </div>

        <!-- Wizard Step 3: Success & Publish Guide -->
        <div v-else-if="wizardStep === 3" class="flex-1 space-y-4 overflow-y-auto p-4 sm:p-6">
          <!-- 成功状态卡片 -->
          <div class="rounded-xl border border-green-200 bg-green-50/70 p-4 text-center">
            <div class="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-green-100 text-green-600 mb-2">
              <CheckCircleIcon class="h-7 w-7" />
            </div>
            <h4 class="text-sm font-bold text-gray-900">MCP 服务添加成功！</h4>
            <p class="mt-1 text-xs text-gray-600">
              已成功接入 <span class="font-mono font-bold text-gray-800">{{ createdServer?.server_name || newServer.server_name }}</span>，
              共发现 <span class="font-bold text-green-700">{{ discoveredTools.length }}</span> 个工具。
            </p>
          </div>

          <!-- 重要提示与发布操作指引 -->
          <div class="rounded-xl border border-amber-200 bg-amber-50/60 p-4 space-y-3">
            <div class="flex items-start gap-2.5">
              <span class="inline-flex items-center justify-center px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-500 text-white shrink-0 mt-0.5">
                重要提示
              </span>
              <div class="space-y-1 text-xs text-amber-950 leading-relaxed">
                <p class="font-bold">
                  新接入的 MCP 工具默认处于 <span class="text-amber-800 underline decoration-amber-400">「未发布」</span> 状态。
                </p>
                <p class="text-gray-600 text-[11px]">
                  未发布的工具不会出现在智能体编排挂载列表中。必须先进行<span class="font-semibold text-gray-900">「发布」</span>，智能体方可正常识别与调用。
                </p>
              </div>
            </div>

            <!-- 操作引导提示 -->
            <div class="pt-2.5 border-t border-amber-200/60 space-y-2">
              <div class="text-[11px] font-bold text-gray-700 flex items-center gap-1">
                <SparklesIcon class="w-3.5 h-3.5 text-amber-600" />
                如何发布工具？
              </div>
              <div class="grid grid-cols-1 gap-2 text-[11px]">
                <div class="flex items-start gap-2 rounded-lg bg-white/80 p-2.5 border border-amber-100">
                  <span class="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-amber-100 text-[10px] font-bold text-amber-800 mt-0.5">1</span>
                  <span class="text-gray-700 leading-relaxed">
                    点击下方 <strong>【一键全部发布】</strong> 按钮，系统将立即将该服务下的所有工具批量发布。
                  </span>
                </div>
                <div class="flex items-start gap-2 rounded-lg bg-white/80 p-2.5 border border-amber-100">
                  <span class="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-amber-100 text-[10px] font-bold text-amber-800 mt-0.5">2</span>
                  <span class="text-gray-700 leading-relaxed">
                    或点击 <strong>【前往工具列表】</strong>，在右侧工具列表中选择工具点击 <strong>【发布】</strong> 或 <strong>【批量发布】</strong>。
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Wizard Footer -->
        <div class="flex shrink-0 flex-col-reverse gap-3 border-t border-gray-100 bg-gray-50 p-4 sm:flex-row sm:items-center sm:justify-between sm:p-6">
          <button 
            v-if="wizardStep !== 3"
            @click="closeWizard" 
            class="px-4 py-2 text-sm font-medium text-gray-500 hover:text-gray-700"
          >
            取消
          </button>
          <button 
            v-else
            @click="closeWizard" 
            class="px-4 py-2 text-sm font-medium text-gray-500 hover:text-gray-700"
          >
            稍后手动发布
          </button>
          
          <div class="flex flex-col gap-2 sm:flex-row sm:space-x-3 sm:gap-0">
            <button v-if="wizardStep === 2" @click="wizardStep = 1" class="px-4 py-2 text-sm font-medium text-primary hover:underline">返回修改</button>
            
            <button 
              v-if="wizardStep === 1 && connectionInputTab === 'json' && !isEditing"
              @click="applyMcpJsonPaste({ connect: true })" 
              :disabled="verifying"
              class="flex items-center justify-center rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-bold text-white shadow-lg shadow-indigo-600/20 transition-all hover:bg-indigo-700 disabled:opacity-50 sm:px-6 sm:py-2"
            >
              <ArrowPathIcon v-if="verifying" class="mr-2 h-4 w-4 animate-spin" />
              {{ verifying ? '正在连接并发现工具...' : '解析并连接发现工具' }}
            </button>

            <template v-else-if="wizardStep === 1 && userAssertionPendingSave">
              <button
                @click="handleVerify({ skipUserAssertion: true })"
                :disabled="verifying || savingServer"
                class="px-4 py-2 text-sm font-medium text-gray-500 hover:text-gray-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                仅测试连接（不含透传）
              </button>
              <button
                @click="saveAndEnableUserAssertion"
                :disabled="savingServer || verifying"
                class="flex items-center justify-center rounded-lg bg-green-600 px-4 py-2.5 text-sm font-bold text-white shadow-lg shadow-green-600/20 transition-all hover:bg-green-700 disabled:cursor-not-allowed disabled:opacity-50 sm:px-6 sm:py-2"
              >
                <ArrowPathIcon v-if="savingServer" class="mr-2 h-4 w-4 animate-spin" />
                {{ savingServer ? '正在保存...' : '保存并启用透传' }}
              </button>
            </template>

            <button 
              v-else-if="wizardStep === 1"
              @click="handleVerify()" 
              :disabled="verifying"
              class="flex items-center justify-center rounded-lg bg-primary px-4 py-2.5 text-sm font-bold text-white shadow-lg shadow-primary/20 transition-all hover:bg-primary-dark disabled:opacity-50 sm:px-6 sm:py-2"
            >
              <ArrowPathIcon v-if="verifying" class="mr-2 h-4 w-4 animate-spin" />
              {{ verifying ? '正在尝试建立连接...' : '连接并发现工具' }}
            </button>
            
            <button 
              v-else-if="wizardStep === 2"
              @click="addServer()" 
              :disabled="savingServer || (!isEditing && !canCreateMcp)"
              class="rounded-lg bg-green-600 px-4 py-2.5 text-sm font-bold text-white shadow-lg shadow-green-600/20 transition-all hover:bg-green-700 active:scale-95 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-green-600 sm:px-6 sm:py-2"
            >
              {{ savingServer ? '保存中...' : isEditing ? '保存修改' : '确认并完成添加' }}
            </button>

            <template v-else-if="wizardStep === 3">
              <button
                @click="closeWizard"
                class="flex items-center justify-center rounded-lg border border-gray-300 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 active:scale-95 sm:px-5 sm:py-2"
              >
                前往工具列表
              </button>
              <button
                v-if="canPublishMcp"
                @click="publishAllCreatedTools"
                :disabled="publishAllLoading"
                class="flex items-center justify-center rounded-lg bg-green-600 px-4 py-2.5 text-sm font-bold text-white shadow-lg shadow-green-600/20 transition-all hover:bg-green-700 active:scale-95 disabled:opacity-50 sm:px-6 sm:py-2"
              >
                <ArrowPathIcon v-if="publishAllLoading" class="mr-2 h-4 w-4 animate-spin" />
                <SparklesIcon v-else class="mr-1.5 h-4 w-4" />
                {{ publishAllLoading ? '正在批量发布...' : '一键全部发布' }}
              </button>
            </template>
          </div>
        </div>
      </div>
    </div>

    <!-- 服务卡片「⋯」操作菜单：fixed 定位，避免被列表滚动容器裁剪 -->
    <div v-if="actionMenu" class="fixed inset-0 z-[60]" @click="closeActionMenu"></div>
    <div
      v-if="actionMenu"
      class="fixed z-[61] w-48 rounded-xl border border-gray-100 bg-white p-1 shadow-xl ring-1 ring-black/5 dark:border-gray-700 dark:bg-gray-800"
      :style="actionMenu.top !== null
        ? { left: `${actionMenu.x}px`, top: `${actionMenu.top}px` }
        : { left: `${actionMenu.x}px`, bottom: `${actionMenu.bottom}px` }"
      @click.stop
    >
      <button
        v-if="props.scope === 'personal' && !isEchoTestServer(actionMenu.server)"
        type="button"
        class="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs font-semibold text-gray-700 transition-colors hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-700"
        @click="runServerAction('share')"
      >
        <svg class="h-4 w-4 shrink-0 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 12v7a2 2 0 002 2h12a2 2 0 002-2v-7M16 6l-4-4-4 4M12 2v13" />
        </svg>
        分享给同事
      </button>
      <button
        v-if="canSave"
        type="button"
        class="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs font-semibold text-gray-700 transition-colors hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-700"
        @click="runServerAction('logs')"
      >
        <DocumentTextIcon class="h-4 w-4 shrink-0 text-indigo-500" />
        调用日志
      </button>
      <template v-if="canSave">
        <div class="my-1 border-t border-gray-100 dark:border-gray-700"></div>
        <button
          v-if="canEditMcp"
          type="button"
          class="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs font-semibold text-gray-700 transition-colors hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-700"
          @click="runServerAction('edit')"
        >
          <PencilSquareIcon class="h-4 w-4 shrink-0 text-gray-400" />
          编辑配置
        </button>
        <button
          v-if="canSyncMcp"
          type="button"
          class="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs font-semibold text-gray-700 transition-colors hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-700"
          @click="runServerAction('sync')"
        >
          <ArrowPathIcon
            class="h-4 w-4 shrink-0 text-gray-400"
            :class="actionMenu?.server && syncLoading[actionMenu.server.id] ? 'animate-spin' : ''"
          />
          同步工具
        </button>
        <button
          v-if="canDeleteMcp"
          type="button"
          class="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs font-semibold text-red-600 transition-colors hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-500/10"
          @click="runServerAction('delete')"
        >
          <TrashIcon class="h-4 w-4 shrink-0" />
          删除服务
        </button>
      </template>
    </div>

    <!-- MCP 接入模拟代码 Modal -->
    <div
      v-if="showMcpCodeModal"
      class="fixed inset-0 z-[80] flex items-center justify-center bg-gray-900/40 p-4 backdrop-blur-sm"
      @click.self="showMcpCodeModal = false"
    >
      <div class="flex max-h-[90vh] w-full max-w-6xl flex-col overflow-hidden rounded-xl bg-white shadow-2xl">
        <div class="flex items-start justify-between gap-3 border-b border-gray-100 p-5">
          <div>
            <h4 class="text-base font-bold text-gray-900">业务方 MCP 调用模拟代码</h4>
            <p class="mt-1 text-xs leading-relaxed text-gray-500">
              一份能直接跑起来的 Echo MCP，{{ mcpCodeLanguage === 'python' ? '保存成 .py 文件即可启动' : '按 ①②③ 合并进现有工程' }}；代码里标出了验签嵌在请求链路的哪一层。
            </p>
          </div>
          <button type="button" class="text-xl leading-none text-gray-400 hover:text-gray-700" @click="showMcpCodeModal = false">×</button>
        </div>

        <div class="flex flex-wrap items-center gap-2 border-b border-gray-100 bg-slate-50 px-5 py-2.5 text-[11px]">
          <span class="font-semibold text-gray-700">嵌入位置</span>
          <span class="rounded bg-white px-2 py-0.5 font-mono text-gray-600 ring-1 ring-gray-200">HTTP 请求</span>
          <ArrowRightIcon class="h-3 w-3 shrink-0 text-gray-400" />
          <span class="rounded bg-indigo-50 px-2 py-0.5 font-semibold text-indigo-700 ring-1 ring-indigo-200">① 认证中间件 / Filter（验签，失败直接 401）</span>
          <ArrowRightIcon class="h-3 w-3 shrink-0 text-gray-400" />
          <span class="rounded bg-white px-2 py-0.5 font-mono text-gray-600 ring-1 ring-gray-200">② MCP 端点 /mcp</span>
          <ArrowRightIcon class="h-3 w-3 shrink-0 text-gray-400" />
          <span class="rounded bg-emerald-50 px-2 py-0.5 font-semibold text-emerald-700 ring-1 ring-emerald-200">③ 业务工具 echo（读已验签身份）</span>
        </div>

        <div class="flex items-center justify-between border-b border-gray-100 px-5 py-3">
          <div class="flex gap-2">
            <button
              type="button"
              class="rounded-md px-3 py-1.5 text-xs font-semibold"
              :class="mcpCodeLanguage === 'python' ? 'bg-indigo-100 text-indigo-700' : 'text-gray-500 hover:bg-gray-100'"
              @click="mcpCodeLanguage = 'python'"
            >Python</button>
            <button
              type="button"
              class="rounded-md px-3 py-1.5 text-xs font-semibold"
              :class="mcpCodeLanguage === 'java' ? 'bg-indigo-100 text-indigo-700' : 'text-gray-500 hover:bg-gray-100'"
              @click="mcpCodeLanguage = 'java'"
            >Java</button>
          </div>
          <button
            type="button"
            class="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-xs font-semibold text-indigo-700 hover:bg-indigo-100"
            @click="copyMcpCode"
          >复制全部代码</button>
        </div>

        <pre class="m-0 flex-1 overflow-auto bg-gray-950 p-5 text-xs leading-6 text-gray-100"><code>{{ generatedMcpCode }}</code></pre>

        <div class="border-t border-gray-100 bg-gray-50 px-5 py-3">
          <div class="grid gap-2.5 text-[11px] leading-relaxed text-gray-600 sm:grid-cols-3">
            <div>
              <div class="font-semibold text-gray-800">代码放哪里</div>
              <p class="mt-0.5">整段复制：Python 存成 business_echo_mcp.py；Java 按 ①②③ 合并进工程。别只截 verify 函数——中间件挂载和工具注册才决定它嵌在哪一层。</p>
            </div>
            <div>
              <div class="font-semibold text-gray-800">三个值从哪来</div>
              <p class="mt-0.5">Audience 校验 aud，Issuer 校验 iss，JWKS 地址按 kid 取公钥；都来自本页只读配置，代码已自动带入。</p>
            </div>
            <div>
              <div class="font-semibold text-gray-800">怎么确认跑通</div>
              <p class="mt-0.5">在 MCP 测试台调用 echo，返回 user_id 即通过。401 多半是固定 Token 或断言校验失败；工具层报 replay 说明同一条断言被用了两次。</p>
            </div>
          </div>
          <p class="mt-2 border-t border-gray-200 pt-2 text-[11px] leading-relaxed text-gray-500">
            <code class="rounded bg-gray-200 px-1">user_context.user_id</code> 用于关联业务用户，
            <code class="rounded bg-gray-200 px-1">custom_attributes</code> 已过滤敏感 key。
            <template v-if="mcpCodeLanguage === 'python'">
              Python 按 MCP SDK 2.x 编写，1.x 换成 <code class="rounded bg-gray-200 px-1">FastMCP</code>；非 ASGI 框架把验签函数放进自己的中间件。
            </template>
            <template v-else>
              Java 片段基于 Spring AI 的 MCP Server starter，需要合并进现有工程后再编译。
            </template>
          </p>
        </div>
      </div>
    </div>

    <!-- Default Payload Fields Modal -->
    <div
      v-if="showPayloadHelp"
      class="fixed inset-0 z-[80] flex items-center justify-center bg-gray-900/40 p-4 backdrop-blur-sm"
      @click.self="closePayloadHelp"
    >
      <div class="flex max-h-[88vh] w-full max-w-5xl flex-col overflow-hidden rounded-xl bg-white shadow-2xl">
        <div class="flex items-start justify-between gap-3 border-b border-gray-100 p-5">
          <div>
            <h4 class="text-base font-bold text-gray-900">默认透传字段（完整结构）</h4>
            <p class="mt-1 text-xs leading-relaxed text-gray-500">以下信息只在开启用户身份传递后，随当前 MCP 调用发送。业务方先验签，再读取用户和智能体信息。</p>
          </div>
          <button type="button" class="text-xl leading-none text-gray-400 hover:text-gray-700" @click="closePayloadHelp">×</button>
        </div>

        <div class="flex-1 overflow-auto p-5">
          <div class="overflow-hidden rounded-lg border border-gray-200">
            <table class="min-w-full divide-y divide-gray-200 text-left text-xs">
              <thead class="bg-gray-50 text-[11px] font-semibold text-gray-600">
                <tr>
                  <th class="whitespace-nowrap px-3 py-2.5">字段位置</th>
                  <th class="whitespace-nowrap px-3 py-2.5">字段</th>
                  <th class="whitespace-nowrap px-3 py-2.5">是否必有</th>
                  <th class="px-3 py-2.5">业务方使用方式</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-gray-100 bg-white text-gray-600">
                <tr v-for="row in payloadFieldRows" :key="`${row.location}-${row.field}`" class="align-top hover:bg-indigo-50/30">
                  <td class="whitespace-nowrap px-3 py-2 font-medium text-indigo-700">{{ row.location }}</td>
                  <td class="whitespace-nowrap px-3 py-2 font-mono text-gray-800">{{ row.field }}</td>
                  <td class="whitespace-nowrap px-3 py-2">{{ row.required }}</td>
                  <td class="px-3 py-2 leading-relaxed">{{ row.usage }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="mt-4 grid gap-3 text-[11px] leading-relaxed text-gray-600 sm:grid-cols-2">
            <div class="rounded-lg border border-amber-100 bg-amber-50/70 p-3">
              <div class="font-semibold text-amber-900">过滤规则：不会透传的敏感字段</div>
              <p class="mt-1">password、token、api_key、authorization、cookie、secret、private_key、session_token 等敏感字段会自动过滤。</p>
            </div>
            <div class="rounded-lg border border-blue-100 bg-blue-50/70 p-3">
              <div class="font-semibold text-blue-900">当前版本暂不包含</div>
              <p class="mt-1">当前第一期不传 `tenant_id`、`scope` 和完整权限树；业务权限仍由业务 MCP 自己判断。</p>
            </div>
          </div>
        </div>

        <div class="flex justify-end border-t border-gray-100 bg-gray-50 p-4">
          <button type="button" class="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white hover:bg-primary-dark" @click="closePayloadHelp">知道了</button>
        </div>
      </div>
    </div>

    <!-- UserContext Help Modal -->
    <div
      v-if="authHelp"
      class="fixed inset-0 z-[80] flex items-center justify-center bg-gray-900/40 p-4 backdrop-blur-sm"
      @click.self="closeAuthHelp"
    >
      <div class="w-full max-w-md rounded-xl bg-white p-5 shadow-2xl">
        <div class="flex items-start justify-between gap-3">
          <h4 class="text-base font-bold text-gray-900">{{ authHelp.title }}</h4>
          <button type="button" class="text-xl leading-none text-gray-400 hover:text-gray-700" @click="closeAuthHelp">×</button>
        </div>
        <p class="mt-3 whitespace-pre-line text-sm leading-7 text-gray-600">{{ authHelp.content }}</p>
        <div class="mt-5 flex justify-end">
          <button type="button" class="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white hover:bg-primary-dark" @click="closeAuthHelp">知道了</button>
        </div>
      </div>
    </div>

    <!-- Delete Confirmation -->
    <ConfirmModal 
      v-if="showDeleteConfirm"
      title="删除 MCP 服务"
      :message="`确定要删除该服务及其缓存的所有工具吗？此操作不可恢复。\n${formatUsageImpact(deleteServerUsage, '删除')}\n删除时会自动从智能体版本配置中移除这些引用，无需再去智能体里手动解绑。`"
      type="danger"
      :loading="deleteLoading"
      @confirm="executeDeleteServer"
      @cancel="cancelDeleteServer"
    />

    <ConfirmModal
      v-if="showStatusConfirm"
      title="禁用 MCP 服务"
      :message="formatUsageImpact(statusConfirmUsage, '禁用')"
      type="warning"
      confirm-text="确认禁用"
      :loading="statusConfirmLoading"
      @confirm="executeStatusChange"
      @cancel="cancelStatusConfirm"
    />

    <!-- 清理远端已删除工具：只删失效缓存，不动服务配置与调用日志 -->
    <ConfirmModal
      v-if="showCleanupConfirm"
      title="清理远端已删除的工具"
      :message="`确定要删除该服务下 ${staleToolCount} 条已失效工具的缓存记录吗？\n这些工具在最近一次同步时已从远端消失，本来就不能发布、不能执行，也不会被智能体调用。清理不会改动服务配置，也不会删除出站调用日志；若远端之后恢复同名工具，需要重新同步并在智能体版本配置里重新勾选。`"
      type="warning"
      confirm-text="确认清理"
      :loading="cleanupStaleLoading"
      :details="staleToolNames"
      details-label="将被清理的工具"
      @confirm="cleanupStaleTools"
      @cancel="showCleanupConfirm = false"
    />

    <ConfirmModal
      v-if="showSyncGuardConfirm"
      title="远端返回 0 个工具"
      :message="`${syncGuardMessage}\n\n确认远端确实已清空全部工具吗？继续会把这些工具标记为远端已删除，已发布工具将被下线。`"
      type="warning"
      confirm-text="仍要同步"
      @confirm="confirmForceSync"
      @cancel="cancelForceSync"
    />

    <!-- Outbound Audit Log Modal -->
    <McpServerLogModal
      :visible="showLogModal"
      :server="selectedLogServer"
      @close="showLogModal = false"
    />

    <!-- 绑定智能体清单：只读抽屉，数据复用 selectedServerUsage，不额外请求 -->
    <McpServerAgentsDrawer
      :visible="showAgentUsageDrawer"
      :server="selectedServer"
      :usage="selectedServerUsage"
      :loading="!!(selectedServer && usageLoading[selectedServer.id])"
      @close="closeAgentUsageDrawer"
      @refresh="refreshSelectedServerUsage"
    />
  </div>
</template>

<style scoped>
.animate-fade-in-up { animation: fadeInUp 0.3s ease-out; }
@keyframes fadeInUp { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
.custom-scrollbar::-webkit-scrollbar { width: 4px; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: #e5e7eb; border-radius: 4px; }
</style>
