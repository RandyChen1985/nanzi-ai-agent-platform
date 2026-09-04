<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import api from '../utils/axios'
import { useUser } from '../composables/useUser'
import { useToast } from '../composables/useToast'
import { copyToClipboard } from '../utils/clipboard'
import ConfirmModal from '../components/ConfirmModal.vue'

type Tab = 'overview' | 'guide' | 'config' | 'clients' | 'methods' | 'audit'
type ResourceWhitelistField = 'allowed_agent_ids' | 'allowed_knowledge_base_ids' | 'allowed_metadata_dataset_ids'
type ResourcePolicySummary = { mode: 'unrestricted' | 'none' | 'restricted'; count: number | null }
type Client = {
  client_id: string
  client_name: string
  client_type: string
  status: 'active' | 'disabled' | 'deleted'
  allowed_grant_types: string[]
  allowed_scopes: string[]
  allowed_agent_ids?: string[] | null
  allowed_knowledge_base_ids?: string[] | null
  allowed_metadata_dataset_ids?: string[] | null
  resource_policy_summary?: Partial<Record<ResourceWhitelistField, ResourcePolicySummary>>
  scope_version: number
  is_shared?: boolean
  needs_token_regeneration?: boolean
  created_by?: string | null
  owner_user_name?: string | null
  owner_real_name?: string | null
  has_issued_token?: boolean
  last_token_issued_at?: string | null
  last_token_issue_method?: 'oauth_authorization' | 'manual_user_token' | null
  active_token_count?: number
  token_total_count?: number
  expiring_token_count?: number
  expired_token_count?: number
  revoked_token_count?: number
  latest_token_expires_at?: string | null
  redirect_uris: string[]
  client_secret?: string | null
}

type AuditLog = {
  id: string
  request_id: string
  client_request_id?: string | null
  client_id: string
  user_id?: string | null
  auth_type: string
  method_name: string
  agent_id?: string | null
  conversation_id?: string | null
  dataset_id?: string | null
  scopes: string[]
  status_code: number
  result_status: string
  error_code?: string | null
  latency_ms?: number | null
  created_at?: string | null
}

type ClientUsage = {
  range: '7d' | '30d' | '90d'
  start_at: string
  end_at: string
  summary: {
    total_calls: number
    completed_calls: number
    success_rate: number
    failed_calls: number
    denied_calls: number
    average_latency_ms: number | null
    p95_latency_ms: number | null
    active_user_count: number
  }
  daily_trend: Array<{ date: string; total: number; completed: number; failed: number; denied: number }>
  method_distribution: Array<{ name: string; total: number; success_rate: number }>
  status_distribution: Array<{ name: string; total: number }>
  auth_distribution: Array<{ name: string; total: number }>
  user_distribution: Array<{ user_id: string; user_name?: string | null; real_name?: string | null; display_name: string; total: number }>
  resource_distribution: Array<{ type: string; name: string; total: number }>
}

type SecurityAuditLog = {
  id: string
  event_type: string
  request_id?: string | null
  client_id?: string | null
  user_id?: string | null
  actor_user_id?: string | null
  result_status: string
  error_code?: string | null
  created_at?: string | null
}

type ClientToken = {
  id: string
  user_id?: string | null
  user_name?: string | null
  real_name?: string | null
  scopes: string[]
  issue_method: 'oauth_authorization' | 'manual_user_token'
  issued_at?: string | null
  expires_at?: string | null
  revoked_at?: string | null
  status: 'active' | 'expired' | 'revoked'
}
type TokenStatusFilter = 'all' | 'active' | 'expiring' | 'expired' | 'revoked'

type Grant = {
  id: string
  client_id: string
  client_name: string
  user_id: string
  scopes: string[]
  resource: string
  status: 'active' | 'revoked'
  consented_at?: string | null
  last_used_at?: string | null
  revoked_at?: string | null
  created_at?: string | null
}

type ClientConfirmAction = 'disable' | 'reset-secret' | 'delete'
type ResourceType = 'agent' | 'knowledge_base' | 'metadata_dataset'
type ResourceOption = { id: string; name: string; description?: string }
type ResourceWhitelistConfig = { field: ResourceWhitelistField; resourceType: ResourceType; title: string; buttonLabel: string }

const { hasPermission, isAdmin, userInfo } = useUser()
const { showToast } = useToast()
const activeTab = ref<Tab>('overview')
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const overview = ref<Record<string, any>>({})
const config = ref<Record<string, boolean | string | number>>({})
const clients = ref<Client[]>([])
const clientPage = ref(1)
const clientTotal = ref(0)
const clientSearch = ref('')
const clientStatus = ref('')
const showClientFilters = ref(false)
const clientActionMenuId = ref<string | null>(null)
const expandedClientIds = ref<Set<string>>(new Set())
const methods = ref<any[]>([])
const auditLogs = ref<AuditLog[]>([])
const auditTotal = ref(0)
const auditPage = ref(1)
const auditPageSize = 20
const auditLoading = ref(false)
const showAuditFilters = ref(false)
const auditStartAt = ref('')
const auditEndAt = ref('')
const securityAuditLogs = ref<SecurityAuditLog[]>([])
const securityAlert = ref<{ alert: boolean; message?: string | null; recent_failure_count: number; rate_limited_count: number }>({ alert: false, recent_failure_count: 0, rate_limited_count: 0 })
const auditTrend = ref<Array<{ at: string; total: number; completed: number; failed: number; denied: number }>>([])
const showAuditTrend = ref(false)
const showSecurityAudit = ref(false)
const auditSummaryRange = ref<'24h' | '7d' | '30d'>('24h')
const auditSummary = ref<Record<string, any>>({})
const auditSummaryLoading = ref(false)
const trendMax = computed(() => Math.max(...auditTrend.value.map(item => item.total), 1))
const showClientUsage = ref(false)
const clientUsageTarget = ref<Client | null>(null)
const clientUsageRange = ref<'7d' | '30d' | '90d'>('30d')
const clientUsage = ref<ClientUsage | null>(null)
const clientUsageLoading = ref(false)
const clientUsageError = ref('')
const clientUsageMax = computed(() => Math.max(...(clientUsage.value?.daily_trend || []).map(item => item.total), 1))
const clientUsageTrendRef = ref<HTMLElement | null>(null)

const selectedAudit = ref<AuditLog | null>(null)
const showCreate = ref(false)
const oneTimeSecret = ref('')
const secretRevealClientId = ref<string | null>(null)
const showClientConfirm = ref(false)
const clientConfirmAction = ref<ClientConfirmAction | null>(null)
const clientConfirmTarget = ref<Client | null>(null)
const showConfirmModal = ref(false)
const confirmModalTitle = ref('请确认操作')
const confirmModalMessage = ref('')
const confirmModalType = ref<'danger' | 'primary' | 'warning'>('danger')
const confirmModalLoading = ref(false)
const confirmModalAction = ref<(() => void | Promise<void>) | null>(null)
const showTokenIssue = ref(false)
const showTokenDetails = ref(false)
const tokenDetailsClient = ref<Client | null>(null)
const clientTokens = ref<ClientToken[]>([])
const tokenDetailsLoading = ref(false)
const tokenStatusFilter = ref<TokenStatusFilter>('active')
const selectedTokenIds = ref<string[]>([])
const tokenDeleteLoading = ref(false)
const tokenClock = ref(Date.now())
const showTokenHelp = ref(false)
const tokenClient = ref<Client | null>(null)
const oneTimeAccessToken = ref('')
const accessTokenInfo = ref<Record<string, any>>({})
const tokenWizardStep = ref<1 | 2>(1)
const clientDetails = ref<Client | null>(null)
const showClientScopeEdit = ref(false)
const clientScopeEditTarget = ref<Client | null>(null)
const showClientEdit = ref(false)
const clientEditTarget = ref<Client | null>(null)
const clientEditForm = reactive({
  client_name: '',
  redirect_uris: '',
  is_shared: false,
})
const resourceWhitelistConfigs: ResourceWhitelistConfig[] = [
  { field: 'allowed_agent_ids', resourceType: 'agent', title: '智能体白名单', buttonLabel: '编辑智能体白名单' },
  { field: 'allowed_knowledge_base_ids', resourceType: 'knowledge_base', title: '知识库白名单', buttonLabel: '编辑知识库白名单' },
  { field: 'allowed_metadata_dataset_ids', resourceType: 'metadata_dataset', title: '元数据集白名单', buttonLabel: '编辑数据集白名单' },
]
const showResourceWhitelistModal = ref(false)
const resourceWhitelistModal = reactive({
  field: 'allowed_agent_ids' as ResourceWhitelistField,
  resourceType: 'agent' as ResourceType,
  title: '智能体白名单',
  target: null as Client | null,
  options: [] as ResourceOption[],
  selectedIds: [] as string[],
  search: '',
  page: 1,
  total: 0,
  hasMore: false,
  loading: false,
  unrestricted: true,
})
const resourceWhitelistConfirm = reactive({
  visible: false,
  resourceLabel: '',
  value: [] as string[],
})

const showGrants = ref(false)
const grants = ref<Grant[]>([])
const grantsLoading = ref(false)

const showPlayground = ref(false)
const playgroundMethod = ref<any>(null)
const playgroundParams = ref('{}')
const playgroundTesting = ref(false)
const playgroundToken = ref('')
const playgroundResponse = ref('')
const playgroundStatus = ref<'success' | 'failed' | ''>('')
const playgroundLatency = ref<number | null>(null)

const exportingAudit = ref(false)
const copied = ref('')
const tokenForm = reactive({
  scopes: [] as string[],
  expires_in: 3600,
})
const clientScopeEditForm = reactive({
  scopes: [] as string[],
})
const auditFilters = reactive({
  client_id: '',
  user_id: '',
  method_name: '',
  agent_id: '',
  dataset_id: '',
  request_id: '',
  auth_type: '',
  result_status: '',
  status_code: '',
})
type AuditFilterKey = keyof typeof auditFilters
type AuditFilterOption = {
  key: AuditFilterKey
  label: string
  kind: 'text' | 'number' | 'select'
  placeholder?: string
  options?: Array<[string, string]>
}
const auditFilterOptions: AuditFilterOption[] = [
  { key: 'client_id', label: 'Client', kind: 'text', placeholder: 'Client ID' },
  { key: 'user_id', label: 'NanZi 用户', kind: 'text', placeholder: 'user_id' },
  { key: 'method_name', label: 'MCP 方法', kind: 'text', placeholder: 'agent_invoke' },
  { key: 'agent_id', label: '智能体', kind: 'text', placeholder: 'agent_id' },
  { key: 'dataset_id', label: '数据集', kind: 'text', placeholder: 'dataset_id' },
  { key: 'request_id', label: '请求 ID', kind: 'text', placeholder: 'request_id' },
  { key: 'auth_type', label: '认证类型', kind: 'select', options: [['user_delegated', '用户授权']] },
  { key: 'result_status', label: '结果状态', kind: 'select', options: [['completed', '成功'], ['failed', '失败'], ['denied', '拒绝']] },
  { key: 'status_code', label: '状态码', kind: 'number', placeholder: '例如 200' },
]
const selectedAuditFilter = ref<AuditFilterKey>('client_id')
const selectedAuditFilterMeta = computed<AuditFilterOption>(() => auditFilterOptions.find(item => item.key === selectedAuditFilter.value) || auditFilterOptions[0]!)
const selectedAuditFilterValue = computed({
  get: () => auditFilters[selectedAuditFilter.value],
  set: (value: string) => { auditFilters[selectedAuditFilter.value] = value },
})
const activeAuditFilterCount = computed(() => Object.values(auditFilters).filter(value => value.trim()).length)
const form = reactive({
  client_name: '',
  redirect_uris: '',
  allowed_grant_types: ['authorization_code'],
  allowed_scopes: ['knowledge:search'],
  is_shared: false,
})
const scopeOptions = [
  ['knowledge:search', '知识库搜索'],
  ['agent:list', '查询可用智能体'],
  ['agent:invoke', '调用智能体'],
  ['conversation:continue', '继续会话'],
  ['metadata:read', '读取元数据'],
  ['metadata:search', '搜索元数据'],
  ['metadata:metrics:read', '读取指标口径'],
] as const
const tokenExpiryOptions = [
  [900, '15 分钟'],
  [3600, '1 小时'],
  [28800, '8 小时'],
  [86400, '1 天'],
  [604800, '7 天'],
  [1296000, '15 天'],
  [2592000, '30 天'],
] as const

const scopeSummary = (client: Client) => {
  const scopes = client.allowed_scopes || []
  if (!scopes.length) return '未配置 Scope'
  const visible = scopes.slice(0, 2).join('、')
  return scopes.length > 2 ? `${visible} 等 ${scopes.length} 项` : visible
}

const resourcePolicySummary = (client: Client, field: ResourceWhitelistField) => {
  const policy = client.resource_policy_summary?.[field]
  if (policy?.mode === 'unrestricted') return '跟随用户权限'
  if (policy?.mode === 'none') return '禁止全部'
  if (policy?.mode === 'restricted') return `已限制 ${policy.count || 0} 项`
  const ids = client[field]
  if (!ids) return '资源策略详情不可见'
  if (ids === null) return '跟随用户权限'
  if (!ids.length) return '禁止全部'
  return `已限制 ${ids.length} 项`
}

const resourcePolicyButtonClass = (client: Client, field: ResourceWhitelistField) => {
  const policy = client.resource_policy_summary?.[field]
  if (policy?.mode === 'unrestricted') return 'border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100'
  if (policy?.mode === 'none') return 'border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100'
  if (policy?.mode === 'restricted') return 'border-amber-200 bg-amber-50 text-amber-700 hover:bg-amber-100'
  const ids = client[field]
  if (!ids) return 'border-slate-200 bg-slate-50 text-slate-500'
  if (ids === null) return 'border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100'
  if (!ids.length) return 'border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100'
  return 'border-amber-200 bg-amber-50 text-amber-700 hover:bg-amber-100'
}

const currentUserId = computed(() => String(userInfo.value?.user_id ?? userInfo.value?.id ?? ''))
const isClientOwner = (client: Client) => (
  !!currentUserId.value && String(client.created_by ?? '') === currentUserId.value
)

const openClientDetails = (client: Client) => {
  clientDetails.value = client
}

const openClientScopeEdit = (client: Client) => {
  if (!canManageClientItem(client) || client.status === 'deleted') return
  clientScopeEditTarget.value = client
  clientScopeEditForm.scopes = [...client.allowed_scopes]
  showClientScopeEdit.value = true
}

const closeClientScopeEdit = (force: boolean | Event = false) => {
  const isForced = typeof force === 'boolean' ? force : false
  if (saving.value && !isForced) return
  showClientScopeEdit.value = false
  clientScopeEditTarget.value = null
  clientScopeEditForm.scopes = []
}

const closeResourceWhitelistModal = (force = false) => {
  if (saving.value && !force) return
  showResourceWhitelistModal.value = false
  resourceWhitelistModal.target = null
  resourceWhitelistModal.options = []
  resourceWhitelistModal.selectedIds = []
  resourceWhitelistModal.search = ''
  resourceWhitelistConfirm.visible = false
}

const loadResourceOptions = async (reset = true) => {
  const client = resourceWhitelistModal.target
  if (!client || resourceWhitelistModal.loading) return
  if (reset) {
    resourceWhitelistModal.page = 1
    resourceWhitelistModal.options = []
  }
  resourceWhitelistModal.loading = true
  try {
    const response = await api.get(`/api/portal/mcp-service/clients/${encodeURIComponent(client.client_id)}/resource-options`, {
      params: {
        resource_type: resourceWhitelistModal.resourceType,
        keyword: resourceWhitelistModal.search.trim() || undefined,
        page: resourceWhitelistModal.page,
        page_size: 50,
      },
    })
    const items = (response.data?.items || []) as ResourceOption[]
    resourceWhitelistModal.options = reset ? items : [...resourceWhitelistModal.options, ...items]
    resourceWhitelistModal.total = Number(response.data?.total || 0)
    resourceWhitelistModal.hasMore = !!response.data?.has_more
  } catch (err: any) {
    error.value = err?.response?.data?.detail || '资源候选列表加载失败'
  } finally {
    resourceWhitelistModal.loading = false
  }
}

const openResourceWhitelist = async (client: Client, config: ResourceWhitelistConfig) => {
  if (!canManageClientItem(client) || client.status === 'deleted') return
  resourceWhitelistModal.field = config.field
  resourceWhitelistModal.resourceType = config.resourceType
  resourceWhitelistModal.title = config.title
  resourceWhitelistModal.target = client
  resourceWhitelistModal.selectedIds = [...(client[config.field] || [])]
  resourceWhitelistModal.unrestricted = client[config.field] === null
  resourceWhitelistModal.search = ''
  showResourceWhitelistModal.value = true
  await loadResourceOptions()
}

const selectAllCurrentResourceOptions = () => {
  if (resourceWhitelistModal.unrestricted) return
  resourceWhitelistModal.selectedIds = Array.from(new Set([
    ...resourceWhitelistModal.selectedIds,
    ...resourceWhitelistModal.options.map(item => item.id),
  ]))
}

const restoreAllAccessibleResources = () => {
  resourceWhitelistModal.unrestricted = true
  resourceWhitelistModal.selectedIds = []
}

const loadMoreResourceOptions = async () => {
  if (!resourceWhitelistModal.hasMore || resourceWhitelistModal.loading) return
  resourceWhitelistModal.page += 1
  await loadResourceOptions(false)
}

const persistResourceWhitelist = async (value: string[] | null) => {
  const client = resourceWhitelistModal.target
  if (!client || !canManageClientItem(client) || saving.value) return
  saving.value = true
  error.value = ''
  try {
    await api.patch(`/api/portal/mcp-service/clients/${encodeURIComponent(client.client_id)}`, { [resourceWhitelistModal.field]: value })
    showToast('资源白名单已更新', 'success')
    await loadClients()
    closeResourceWhitelistModal(true)
  } catch (err: any) {
    error.value = err?.response?.data?.detail || '资源白名单更新失败'
  } finally {
    saving.value = false
  }
}

const saveResourceWhitelist = async () => {
  if (!resourceWhitelistModal.unrestricted && !resourceWhitelistModal.selectedIds.length) {
    resourceWhitelistConfirm.resourceLabel = resourceWhitelistModal.title.replace('白名单', '')
    resourceWhitelistConfirm.value = []
    resourceWhitelistConfirm.visible = true
    return
  }
  const value = resourceWhitelistModal.unrestricted ? null : [...resourceWhitelistModal.selectedIds]
  await persistResourceWhitelist(value)
}

const cancelResourceWhitelistConfirm = () => {
  resourceWhitelistConfirm.visible = false
  resourceWhitelistConfirm.value = []
}

const confirmResourceWhitelistSave = async () => {
  const value = [...resourceWhitelistConfirm.value]
  resourceWhitelistConfirm.visible = false
  resourceWhitelistConfirm.value = []
  await persistResourceWhitelist(value)
}

const canEditConfig = computed(() => hasPermission('element:mcp_service:config:edit'))
const canManageCapability = computed(() => hasPermission('element:mcp_service:capability:manage'))
const canManageClient = computed(() => hasPermission('element:mcp_service:client:manage'))
const canResetSecret = computed(() => hasPermission('element:mcp_service:client:secret_reset'))
const canIssueToken = computed(() => hasPermission('element:mcp_service:client:token_issue'))
const canReadConfig = computed(() => hasPermission('element:mcp_service:config:read'))
const canReadOverview = computed(() => hasPermission('element:mcp_service:overview:read'))
const canReadGuide = computed(() => canReadOverview.value)
const canReadClients = computed(() => hasPermission('element:mcp_service:client:read'))
const canReadMethods = computed(() => hasPermission('element:mcp_service:capability:read'))
const canReadAudit = computed(() => hasPermission('element:mcp_service:audit:read'))
const canReadGrants = computed(() => hasPermission('element:mcp_service:grant:read'))
const canRevokeGrants = computed(() => hasPermission('element:mcp_service:grant:revoke'))
const canManageClientItem = (client: Client) => (
  canManageClient.value && (isAdmin.value || isClientOwner(client))
)
const canResetSecretForClient = (client: Client) => (
  canResetSecret.value && (isAdmin.value || isClientOwner(client))
)
const canRevokeAllClientTokens = (client: Client | null) => (
  !!client && canIssueToken.value && (isAdmin.value || isClientOwner(client))
)
const availableTabs = computed(() => [
  canReadOverview.value ? { id: 'overview' as Tab, label: '服务总览' } : null,
  canReadConfig.value ? { id: 'config' as Tab, label: '服务配置' } : null,
  canReadClients.value ? {
    id: 'clients' as Tab,
    label: '外部 Client',
    badge: clients.value.length ? `${clients.value.length}` : null,
  } : null,
  canReadMethods.value ? {
    id: 'methods' as Tab,
    label: '能力与 Scope',
    badge: methods.value.length ? `${methods.value.length}` : null,
  } : null,
  canReadAudit.value ? {
    id: 'audit' as Tab,
    label: '审计日志',
    hasAlert: !!securityAlert.value?.alert,
  } : null,
  canReadGuide.value ? { id: 'guide' as Tab, label: '使用指南' } : null,
].filter(Boolean) as Array<{ id: Tab; label: string; badge?: string | null; hasAlert?: boolean }>)

const openClientEdit = (client: Client) => {
  if (!canManageClientItem(client) || client.status === 'deleted') return
  clientEditTarget.value = client
  clientEditForm.client_name = client.client_name
  clientEditForm.redirect_uris = (client.redirect_uris || []).join('\n')
  clientEditForm.is_shared = !!client.is_shared
  showClientEdit.value = true
}

const closeClientEdit = (force = false) => {
  const isForced = typeof force === 'boolean' ? force : false
  if (saving.value && !isForced) return
  showClientEdit.value = false
  clientEditTarget.value = null
}

const saveClientEdit = async () => {
  const client = clientEditTarget.value
  if (!client || !canManageClientItem(client) || !clientEditForm.client_name.trim() || saving.value) return
  saving.value = true
  error.value = ''
  try {
    const redirectUris = clientEditForm.redirect_uris
      .split(/\r?\n|,/)
      .map(item => item.trim())
      .filter(Boolean)
    await api.patch(`/api/portal/mcp-service/clients/${encodeURIComponent(client.client_id)}`, {
      client_name: clientEditForm.client_name.trim(),
      redirect_uris: redirectUris,
      is_shared: clientEditForm.is_shared,
    })
    showToast('Client 基本信息已更新', 'success')
    await loadClients()
    closeClientEdit(true)
  } catch (err: any) {
    error.value = err?.response?.data?.detail || 'Client 更新失败'
  } finally {
    saving.value = false
  }
}

const revokeAllClientTokens = async (client: Client) => {
  if (!canRevokeAllClientTokens(client)) return
  openConfirmModal(
    '确认撤销全部 Token',
    `确定要撤销 Client【${client.client_name}】下全部有效 Token 吗？此操作不可逆。`,
    async () => {
      try {
        await api.post(`/api/portal/mcp-service/clients/${client.client_id}/tokens/revoke-all`)
        showToast('已撤销该 Client 下全部有效 Token', 'success')
        await openTokenDetails(client)
        await loadClients()
      } catch (err: any) {
        error.value = err?.response?.data?.detail || '批量撤销 Token 失败'
      }
    },
    'warning',
  )
}

const loadGrants = async () => {
  if (!canReadGrants.value) return
  grantsLoading.value = true
  try {
    const response = await api.get('/api/portal/mcp-service/grants')
    grants.value = response.data || []
  } catch (err: any) {
    error.value = err?.response?.data?.detail || '授权记录加载失败'
  } finally {
    grantsLoading.value = false
  }
}

const revokeGrant = async (grant: Grant) => {
  openConfirmModal(
    '确认解除应用授权',
    `确定要解除对【${grant.client_name || grant.client_id}】的授权吗？该应用已签发的全部 Token 将立即失效。`,
    async () => {
      try {
        await api.post(`/api/portal/mcp-service/grants/${grant.id}/revoke`)
        showToast('已成功解除授权', 'success')
        await loadGrants()
        await loadClients()
      } catch (err: any) {
        error.value = err?.response?.data?.detail || '解除授权失败'
      }
    },
    'warning',
  )
}

const removeAuditFilter = async (key: AuditFilterKey) => {
  auditFilters[key] = ''
  await applyAuditFilters()
}

const RECENT_TOKENS_STORAGE_KEY = 'nanzi_mcp_recent_tokens'

const loadPersistedTokens = (): Array<{ token: string; label: string; time: string; expiresAt: number }> => {
  try {
    const raw = localStorage.getItem(RECENT_TOKENS_STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (Array.isArray(parsed)) {
      const now = Date.now()
      return parsed.filter(item => item && item.token && typeof item.expiresAt === 'number' && item.expiresAt > now)
    }
  } catch {}
  return []
}

const sessionRecentTokens = ref<Array<{ token: string; label: string; time: string; expiresAt: number }>>(loadPersistedTokens())

const savePersistedTokens = () => {
  try {
    localStorage.setItem(RECENT_TOKENS_STORAGE_KEY, JSON.stringify(sessionRecentTokens.value))
  } catch {}
}

const purgeExpiredSessionTokens = () => {
  const now = Date.now()
  sessionRecentTokens.value = sessionRecentTokens.value.filter(item => item.expiresAt > now)
  savePersistedTokens()
}

const activeSessionRecentTokens = computed(() => sessionRecentTokens.value.filter(item => item.expiresAt > Date.now()))

const formatTokenRemaining = (expiresAt: number) => {
  const diffSec = Math.floor((expiresAt - Date.now()) / 1000)
  if (diffSec <= 0) return '已过期'
  if (diffSec < 60) return `剩余 ${diffSec} 秒`
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `剩余 ${diffMin} 分钟`
  const diffHour = Math.floor(diffMin / 60)
  return `剩余 ${diffHour} 小时`
}

const recordSessionToken = (token: string, clientName: string, expiresIn: number) => {
  if (!token || !Number.isFinite(expiresIn) || expiresIn <= 0) return
  purgeExpiredSessionTokens()
  if (!sessionRecentTokens.value.some(t => t.token === token)) {
    const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    sessionRecentTokens.value.unshift({
      token,
      label: `${clientName} (${now})`,
      time: now,
      expiresAt: Date.now() + expiresIn * 1000,
    })
    if (sessionRecentTokens.value.length > 10) {
      sessionRecentTokens.value.pop()
    }
    savePersistedTokens()
  }
}

const openPlayground = (method: any) => {
  playgroundMethod.value = method
  playgroundResponse.value = ''
  playgroundStatus.value = ''
  playgroundLatency.value = null
  if (!playgroundToken.value && activeSessionRecentTokens.value.length > 0 && activeSessionRecentTokens.value[0]?.token) {
    playgroundToken.value = activeSessionRecentTokens.value[0].token
  }
  const defaultParams: Record<string, any> = {}
  if (method.name === 'metadata_list_datasets') {
    defaultParams.limit = 5
  } else if (method.name === 'metadata_search') {
    defaultParams.query = '测试'
  } else if (method.name === 'knowledge_search') {
    defaultParams.query = '知识库检索测试'
    defaultParams.top_k = 3
  } else if (method.name === 'agent_list_allowed') {
    // 无参数
  } else if (method.name === 'agent_invoke') {
    defaultParams.agent_id = 'agent_id_here'
    defaultParams.message = '你好'
  } else if (method.name === 'conversation_continue') {
    defaultParams.conversation_id = 'conversation_id_here'
    defaultParams.message = '继续'
  } else if (method.name === 'metadata_get_dataset' || method.name === 'metadata_get_schema' || method.name === 'metadata_get_metrics') {
    defaultParams.dataset_id = 'dataset_id_here'
  }
  playgroundParams.value = JSON.stringify(defaultParams, null, 2)
  showPlayground.value = true
}

const executePlaygroundTest = async () => {
  if (!playgroundMethod.value || playgroundTesting.value) return
  const tokenToUse = playgroundToken.value.trim()
  if (!tokenToUse) {
    playgroundStatus.value = 'failed'
    playgroundResponse.value = '请先输入 Bearer Access Token。\n\n提示：你可以在「外部 Client」列表中找到已启用的 Client，点击「生成 MCP Access Token」，复制后粘贴至此处进行在线调试。'
    return
  }
  let parsedArgs = {}
  try {
    parsedArgs = JSON.parse(playgroundParams.value || '{}')
  } catch {
    playgroundStatus.value = 'failed'
    playgroundResponse.value = '参数 JSON 格式不合法，请检查后再试'
    return
  }
  playgroundTesting.value = true
  playgroundResponse.value = ''
  playgroundStatus.value = ''
  const startTime = Date.now()
  try {
    const res = await api.post('/api/portal/mcp-service/playground/test', {
      method_name: playgroundMethod.value.name,
      arguments: parsedArgs,
      token: tokenToUse,
    }, {
      headers: {
        'X-Ignore-Auth-Redirect': 'true',
      },
    })
    playgroundLatency.value = res.data.latency_ms ?? (Date.now() - startTime)
    if (res.data.status === 'success') {
      playgroundStatus.value = 'success'
      playgroundResponse.value = JSON.stringify(res.data.response, null, 2)
    } else {
      playgroundStatus.value = 'failed'
      playgroundResponse.value = JSON.stringify(res.data.response || { error: res.data.error || '调用失败' }, null, 2)
    }
  } catch (err: any) {
    playgroundLatency.value = Date.now() - startTime
    playgroundStatus.value = 'failed'
    playgroundResponse.value = JSON.stringify(err?.response?.data || { error: err?.message || '请求发生异常' }, null, 2)
  } finally {
    playgroundTesting.value = false
  }
}

const loadOverview = async () => {
  if (canReadOverview.value) {
    const overviewResponse = await api.get('/api/portal/mcp-service/overview')
    overview.value = overviewResponse.data
  }
  if (canReadConfig.value) {
    config.value = (await api.get('/api/portal/mcp-service/config')).data
  }
}

const loadAuditSummary = async () => {
  if (!canReadAudit.value) return
  auditSummaryLoading.value = true
  try {
    auditSummary.value = (await api.get('/api/portal/mcp-service/audit/summary', {
      params: { range: auditSummaryRange.value },
    })).data
  } catch {
    auditSummary.value = {}
  } finally {
    auditSummaryLoading.value = false
  }
}

const handleAuditSummaryRangeChange = async () => {
  await Promise.all([loadAuditSummary(), loadAuditTrend()])
}

const loadClients = async () => {
  const response = await api.get('/api/portal/mcp-service/clients', {
    params: { page: clientPage.value, page_size: 20, search: clientSearch.value || undefined, status: clientStatus.value || undefined },
  })
  clients.value = response.data?.items || []
  clientTotal.value = Number(response.data?.total || 0)
  const visibleClientIds = new Set(clients.value.map(client => client.client_id))
  expandedClientIds.value = new Set([...expandedClientIds.value].filter(clientId => visibleClientIds.has(clientId)))
}

const applyClientFilters = async () => {
  clientPage.value = 1
  await loadClients()
}

const loadClientUsage = async () => {
  const client = clientUsageTarget.value
  if (!client) return
  clientUsageLoading.value = true
  clientUsageError.value = ''
  clientUsage.value = null
  try {
    const response = await api.get(`/api/portal/mcp-service/clients/${encodeURIComponent(client.client_id)}/usage`, {
      params: { range: clientUsageRange.value },
    })
    clientUsage.value = response.data
  } catch (err: any) {
    clientUsageError.value = err?.response?.data?.detail || '使用统计加载失败，请稍后重试'
  } finally {
    clientUsageLoading.value = false
    if (clientUsage.value) {
      await nextTick()
      requestAnimationFrame(() => {
        if (clientUsageTrendRef.value) {
          clientUsageTrendRef.value.scrollLeft = clientUsageTrendRef.value.scrollWidth
        }
      })
    }
  }
}

const openClientUsage = async (client: Client) => {
  clientActionMenuId.value = null
  clientUsageTarget.value = client
  clientUsageRange.value = '30d'
  showClientUsage.value = true
  await loadClientUsage()
}

const closeClientUsage = () => {
  if (clientUsageLoading.value) return
  showClientUsage.value = false
  clientUsageTarget.value = null
  clientUsage.value = null
  clientUsageError.value = ''
}

const changeClientPage = async (delta: number) => {
  const pageCount = Math.max(1, Math.ceil(clientTotal.value / 20))
  const nextPage = clientPage.value + delta
  if (nextPage < 1 || nextPage > pageCount) return
  clientPage.value = nextPage
  await loadClients()
}

const openTokenDetails = async (client: Client) => {
  tokenDetailsClient.value = client
  showTokenDetails.value = true
  tokenStatusFilter.value = 'active'
  selectedTokenIds.value = []
  clientTokens.value = []
  await loadClientTokens(client)
}

const loadClientTokens = async (client: Client) => {
  tokenDetailsLoading.value = true
  try {
    clientTokens.value = (await api.get(`/api/portal/mcp-service/clients/${client.client_id}/tokens`)).data || []
  } catch (err: any) {
    error.value = err?.response?.data?.detail || 'Token 记录加载失败'
  } finally {
    tokenDetailsLoading.value = false
  }
}

const isTokenExpiring = (token: ClientToken) => {
  if (getTokenStatus(token) !== 'active' || !token.expires_at) return false
  const expiresAt = parseMcpTimestamp(token.expires_at)?.getTime() ?? null
  return expiresAt !== null && Number.isFinite(expiresAt) && expiresAt > tokenClock.value && expiresAt <= tokenClock.value + 24 * 60 * 60 * 1000
}

const getTokenStatus = (token: ClientToken): ClientToken['status'] => {
  if (token.revoked_at) return 'revoked'
  if (!token.expires_at) return token.status
  const expiresAt = parseMcpTimestamp(token.expires_at)?.getTime() ?? null
  return expiresAt !== null && Number.isFinite(expiresAt) && expiresAt <= tokenClock.value ? 'expired' : 'active'
}

const tokenStatusCounts = computed(() => {
  const counts = { all: clientTokens.value.length, active: 0, expiring: 0, expired: 0, revoked: 0 }
  for (const token of clientTokens.value) {
    const status = getTokenStatus(token)
    if (status === 'active') counts.active += 1
    if (isTokenExpiring(token)) counts.expiring += 1
    if (status === 'expired') counts.expired += 1
    if (status === 'revoked') counts.revoked += 1
  }
  return counts
})

const filteredClientTokens = computed(() => clientTokens.value.filter(token => {
  if (tokenStatusFilter.value === 'all') return true
  if (tokenStatusFilter.value === 'expiring') return isTokenExpiring(token)
  return getTokenStatus(token) === tokenStatusFilter.value
}))

const canDeleteClientToken = (token: ClientToken) => (
  canIssueToken.value
  && !!tokenDetailsClient.value
  && (isAdmin.value || isClientOwner(tokenDetailsClient.value) || token.user_id === currentUserId.value)
)

const deletableVisibleTokens = computed(() => filteredClientTokens.value.filter(canDeleteClientToken))
const selectedDeletableTokens = computed(() => deletableVisibleTokens.value.filter(token => selectedTokenIds.value.includes(token.id)))

const allVisibleTokensSelected = computed(() => (
  deletableVisibleTokens.value.length > 0
  && deletableVisibleTokens.value.every(token => selectedTokenIds.value.includes(token.id))
))

const toggleTokenSelection = (token: ClientToken) => {
  if (!canDeleteClientToken(token)) return
  selectedTokenIds.value = selectedTokenIds.value.includes(token.id)
    ? selectedTokenIds.value.filter(id => id !== token.id)
    : [...selectedTokenIds.value, token.id]
}

const toggleAllVisibleTokens = () => {
  const deletableIds = deletableVisibleTokens.value.map(token => token.id)
  selectedTokenIds.value = allVisibleTokensSelected.value
    ? selectedTokenIds.value.filter(id => !deletableIds.includes(id))
    : [...new Set([...selectedTokenIds.value, ...deletableIds])]
}

const deleteClientToken = async (token: ClientToken) => {
  if (!tokenDetailsClient.value || !canDeleteClientToken(token) || tokenDeleteLoading.value) return
  const warning = getTokenStatus(token) === 'active'
    ? '该 Token 当前仍有效，物理删除后将立即失效且无法恢复，确定继续吗？'
    : '物理删除后将无法查看这条 Token 历史记录，确定继续吗？'
  openConfirmModal('确认物理删除 Token', warning, async () => {
    tokenDeleteLoading.value = true
    try {
      await api.delete(`/api/portal/mcp-service/clients/${encodeURIComponent(tokenDetailsClient.value!.client_id)}/tokens/${encodeURIComponent(token.id)}`)
      showToast('Access Token 已物理删除', 'success')
      selectedTokenIds.value = selectedTokenIds.value.filter(id => id !== token.id)
      await loadClientTokens(tokenDetailsClient.value!)
      await loadClients()
    } catch (err: any) {
      error.value = err?.response?.data?.detail || 'Access Token 删除失败'
    } finally {
      tokenDeleteLoading.value = false
    }
  })
}

const deleteSelectedClientTokens = async () => {
  if (!tokenDetailsClient.value || !selectedDeletableTokens.value.length || tokenDeleteLoading.value) return
  const hasActiveToken = selectedDeletableTokens.value.some(token => getTokenStatus(token) === 'active')
  const warning = hasActiveToken
    ? '选中项包含仍有效的 Token，物理删除后将立即失效且无法恢复，确定继续吗？'
    : `确定物理删除选中的 ${selectedDeletableTokens.value.length} 条 Token 历史记录吗？`
  openConfirmModal('确认批量物理删除 Token', warning, async () => {
    tokenDeleteLoading.value = true
    try {
      await api.post(`/api/portal/mcp-service/clients/${encodeURIComponent(tokenDetailsClient.value!.client_id)}/tokens/delete`, {
        token_ids: selectedDeletableTokens.value.map(token => token.id),
      })
      showToast('选中的 Access Token 已物理删除', 'success')
      selectedTokenIds.value = []
      await loadClientTokens(tokenDetailsClient.value!)
      await loadClients()
    } catch (err: any) {
      error.value = err?.response?.data?.detail || 'Access Token 批量删除失败'
    } finally {
      tokenDeleteLoading.value = false
    }
  })
}

const revokeClientToken = async (token: ClientToken) => {
  if (!tokenDetailsClient.value || getTokenStatus(token) !== 'active' || tokenDeleteLoading.value) return
  openConfirmModal('确认撤销 Token', '确定撤销这个 Access Token 吗？撤销后无法恢复。', async () => {
    try {
      await api.post(`/api/portal/mcp-service/clients/${tokenDetailsClient.value!.client_id}/tokens/${token.id}/revoke`)
      showToast('Access Token 已撤销', 'success')
      await loadClientTokens(tokenDetailsClient.value!)
      await loadClients()
    } catch (err: any) {
      error.value = err?.response?.data?.detail || 'Token 撤销失败'
    }
  }, 'warning')
}

const exportAudit = async () => {
  exportingAudit.value = true
  try {
    const params: Record<string, string | number> = {}
    if (auditStartAt.value) params.start_at = auditStartAt.value
    if (auditEndAt.value) params.end_at = auditEndAt.value
    Object.entries(auditFilters).forEach(([key, value]) => {
      if (value.trim()) params[key] = value.trim()
    })
    const response = await api.get('/api/portal/mcp-service/audit/export', {
      params,
      responseType: 'blob',
    })
    const blob = new Blob([response.data], { type: 'text/csv;charset=utf-8;' })
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.setAttribute('download', `mcp-audit-${new Date().toISOString().slice(0, 10)}.csv`)
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)
    showToast('审计日志导出成功', 'success')
  } catch (err: any) {
    error.value = err?.response?.data?.detail || '审计日志导出失败'
  } finally {
    exportingAudit.value = false
  }
}

const loadMethods = async () => {
  methods.value = (await api.get('/api/portal/mcp-service/methods')).data || []
}

const loadAudit = async () => {
  if (!canReadAudit.value) return
  auditLoading.value = true
  try {
    const params: Record<string, string | number> = {
      page: auditPage.value,
      page_size: auditPageSize,
    }
    if (auditStartAt.value) params.start_at = auditStartAt.value
    if (auditEndAt.value) params.end_at = auditEndAt.value
    Object.entries(auditFilters).forEach(([key, value]) => {
      if (value.trim()) params[key] = value.trim()
    })
    const response = await api.get('/api/portal/mcp-service/audit', { params })
    auditLogs.value = response.data?.items || []
    auditTotal.value = Number(response.data?.total || 0)
  } catch (err: any) {
    error.value = err?.response?.data?.detail || 'MCP 审计日志加载失败'
  } finally {
    auditLoading.value = false
  }
}

const loadSecurityAudit = async () => {
  if (!canReadAudit.value) return
  const params: Record<string, string | number> = { page: 1, page_size: 20 }
  if (auditStartAt.value) params.start_at = auditStartAt.value
  if (auditEndAt.value) params.end_at = auditEndAt.value
  const response = await api.get('/api/portal/mcp-service/audit/security', { params })
  securityAuditLogs.value = response.data?.items || []
  securityAlert.value = (await api.get('/api/portal/mcp-service/audit/security/alerts')).data || securityAlert.value
}

const loadAuditTrend = async () => {
  if (!canReadAudit.value) return
  const response = await api.get('/api/portal/mcp-service/audit/trend', {
    params: { range: auditSummaryRange.value },
  })
  auditTrend.value = response.data?.items || []
}

const applyAuditFilters = async () => {
  auditPage.value = 1
  await Promise.all([loadAudit(), loadSecurityAudit()])
}

const resetAuditFilters = async () => {
  Object.keys(auditFilters).forEach((key) => {
    auditFilters[key as keyof typeof auditFilters] = ''
  })
  auditStartAt.value = ''
  auditEndAt.value = ''
  await applyAuditFilters()
}

const changeAuditPage = async (delta: number) => {
  const pageCount = Math.max(1, Math.ceil(auditTotal.value / auditPageSize))
  const nextPage = auditPage.value + delta
  if (nextPage < 1 || nextPage > pageCount) return
  auditPage.value = nextPage
  await loadAudit()
}

const auditResultLabel = (status: string) => ({
  completed: '成功',
  failed: '失败',
  denied: '拒绝',
}[status] || status)

const auditAuthTypeLabel = (authType: string) => authType === 'user_delegated' ? '用户授权' : authType

const parseMcpTimestamp = (value?: string | null) => {
  if (!value) return null
  const rawValue = value.trim()
  if (!rawValue) return null
  const isoValue = rawValue.includes('T') ? rawValue : rawValue.replace(' ', 'T')
  const normalizedValue = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(isoValue) ? isoValue : `${isoValue}Z`
  const parsed = new Date(normalizedValue)
  return Number.isFinite(parsed.getTime()) ? parsed : null
}
const formatAuditTime = (value?: string | null) => parseMcpTimestamp(value)?.toLocaleString('zh-CN', { hour12: false }) || '—'
const trendBarHeight = (total: number) => `${Math.max(6, Math.round((total / trendMax.value) * 88))}px`
const usageBarWidth = (total: number, max: number) => `${Math.max(total ? 4 : 0, Math.round(total / Math.max(max, 1) * 100))}%`
const usagePercent = (total: number, overall: number) => overall ? `${Math.round(total / overall * 100)}%` : '0%'
const usageStatusLabel = (name: string) => ({ completed: '成功', failed: '失败', denied: '拒绝' }[name] || name)
const usageAuthLabel = (name: string) => name === 'user_delegated' ? '用户授权' : name
const usageResourceLabel = (name: string) => name || '其他'
const formatClientTime = (value?: string | null) => parseMcpTimestamp(value)?.toLocaleString('zh-CN', { hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).replace(/\//g, '-') || '—'
const remainingTokenDays = (value?: string | null) => {
  const expiresAt = parseMcpTimestamp(value)?.getTime() ?? null
  if (expiresAt === null || !Number.isFinite(expiresAt)) return null
  const remainingMs = expiresAt - tokenClock.value
  return remainingMs <= 0 ? 0 : Math.ceil(remainingMs / (24 * 60 * 60 * 1000))
}
const tokenRemainingLabel = (token: ClientToken) => {
  const days = remainingTokenDays(token.expires_at)
  return days === null ? '—' : days === 0 ? '已过期' : `还剩 ${days} 天`
}
const toggleClientExpanded = (clientId: string) => {
  const next = new Set(expandedClientIds.value)
  if (next.has(clientId)) next.delete(clientId)
  else next.add(clientId)
  expandedClientIds.value = next
}

const load = async () => {
  loading.value = true
  error.value = ''
  try {
    if (!availableTabs.value.some(tab => tab.id === activeTab.value)) {
      activeTab.value = availableTabs.value[0]?.id || 'overview'
    }
    await loadOverview()
    if (canReadAudit.value) await loadAuditSummary()
    if (canReadClients.value) await loadClients()
    if (canReadMethods.value) await loadMethods()
    if (canReadAudit.value) {
      await loadAudit()
      await loadSecurityAudit()
      await loadAuditTrend()
    }
  } catch (err: any) {
    error.value = err?.response?.data?.detail || 'MCP 服务台数据加载失败'
  } finally {
    loading.value = false
  }
}

const toggleConfig = async (key: string) => {
  const canChange = key === 'platform_enabled' ? canEditConfig.value : canManageCapability.value
  if (!canReadConfig.value || !canChange) return
  saving.value = true
  try {
    const response = await api.patch('/api/portal/mcp-service/config', { [key]: !config.value[key] })
    config.value = response.data
    await loadOverview()
  } catch (err: any) {
    error.value = err?.response?.data?.detail || '开关保存失败'
  } finally {
    saving.value = false
  }
}

const updateRateLimit = async (key: 'rate_limit_client_per_minute' | 'rate_limit_user_per_minute') => {
  if (!canEditConfig.value) return
  saving.value = true
  try {
    const value = Math.max(0, Math.min(100000, Number(config.value[key] || 0)))
    const response = await api.patch('/api/portal/mcp-service/config', { [key]: value })
    config.value = response.data
  } catch (err: any) {
    error.value = err?.response?.data?.detail || '限流配置保存失败'
  } finally {
    saving.value = false
  }
}

const copyValue = async (key: string, value: string) => {
  try {
    const copiedSuccessfully = await copyToClipboard(value)
    if (!copiedSuccessfully) {
      error.value = '复制失败，请手动复制'
      return
    }
  } catch {
    error.value = '复制失败，请手动复制'
    return
  }
  copied.value = key
  window.setTimeout(() => { if (copied.value === key) copied.value = '' }, 1400)
}

const guideSelectedToken = ref('')
const effectiveGuideToken = computed(() => guideSelectedToken.value.trim() || oneTimeAccessToken.value || '${NANZI_PLATFORM_MCP_ACCESS_TOKEN}')

const mcpJson = computed(() => JSON.stringify({
  mcpServers: {
    'nanzi-platform': {
      url: overview.value.mcp_endpoint || '/mcp/platform',
      headers: {
        Authorization: `Bearer ${effectiveGuideToken.value}`,
      },
    },
  },
}, null, 2))

const copyMcpJson = async () => {
  await copyValue('mcp-json', mcpJson.value)
}

const oauthBaseUrl = computed(() => String(overview.value.authorization_server || 'https://nanzi.example.com').replace(/\/$/, ''))
const mcpEndpoint = computed(() => String(overview.value.mcp_endpoint || `${oauthBaseUrl.value}/mcp/platform`))
const mcpResource = computed(() => String(overview.value.resource || mcpEndpoint.value))
type EndpointHelpKey = 'endpoint' | 'resource' | 'oauth' | 'protected'
const endpointHelpItems = computed(() => [
  {
    key: 'endpoint' as EndpointHelpKey,
    label: 'MCP Endpoint',
    value: overview.value.mcp_endpoint,
    description: 'MCP 请求实际发送到这里。',
    usage: '把它填入 Cursor、Claude Desktop 或业务方 MCP 客户端的 url；后续 tools/list 和 tools/call 都请求这个地址。',
  },
  {
    key: 'resource' as EndpointHelpKey,
    label: 'Audience / Resource',
    value: overview.value.resource,
    description: 'OAuth2 获取 Token 时使用。',
    usage: '业务方换取 Access Token 时作为 resource 参数提交，用于把 Token 限定给 NanZi Platform MCP；它不是单独的 HTTP 调用地址。',
  },
  {
    key: 'oauth' as EndpointHelpKey,
    label: '授权服务器 Metadata',
    value: overview.value.authorization_server_metadata,
    description: 'OAuth2 客户端读取授权端点。',
    usage: '支持 OAuth2 自动发现的客户端读取这个 JSON 地址，以获得 authorize、token、revoke 等标准端点和 PKCE 支持信息。',
  },
  {
    key: 'protected' as EndpointHelpKey,
    label: 'Protected Resource Metadata',
    value: overview.value.protected_resource_metadata,
    description: 'MCP 客户端发现授权服务器和资源信息。',
    usage: 'MCP 客户端在需要认证或收到 401 时，可通过这个地址发现应该使用哪个授权服务器以及对应的 resource。',
  },
])
const endpointHelpKey = ref<EndpointHelpKey>('endpoint')
const showEndpointHelp = ref(false)
const endpointHelp = computed(() => endpointHelpItems.value.find(item => item.key === endpointHelpKey.value) || endpointHelpItems.value[0])
const openEndpointHelp = (key: EndpointHelpKey) => {
  endpointHelpKey.value = key
  showEndpointHelp.value = true
}
const authorizationCodeCurl = computed(() => [
  '# 适用：需要代表具体用户的 CRM/门户系统',
  `NANZI_BASE_URL="${oauthBaseUrl.value}"`,
  `NANZI_MCP_ENDPOINT="${mcpEndpoint.value}"`,
  `NANZI_RESOURCE="${mcpResource.value}"`,
  'NANZI_CLIENT_ID="从 MCP 服务台复制的 client_id"',
  'NANZI_CLIENT_SECRET="创建 Client 时只显示一次的 client_secret"',
  'CALLBACK_CODE="授权回调中的 code"',
  'PKCE_CODE_VERIFIER="发起授权时生成并保存的 code_verifier"',
  'TOKEN_RESPONSE=$(curl --request POST "$NANZI_BASE_URL/oauth/token" --user "$NANZI_CLIENT_ID:$NANZI_CLIENT_SECRET" --header "Content-Type: application/x-www-form-urlencoded" --data-urlencode "grant_type=authorization_code" --data-urlencode "code=$CALLBACK_CODE" --data-urlencode "redirect_uri=$NANZI_REDIRECT_URI" --data-urlencode "code_verifier=$PKCE_CODE_VERIFIER" --data-urlencode "resource=$NANZI_RESOURCE")',
  'ACCESS_TOKEN=$(printf "%s" "$TOKEN_RESPONSE" | jq -r ".access_token")',
  'curl --request POST "$NANZI_MCP_ENDPOINT" --header "Authorization: Bearer $ACCESS_TOKEN" --header "Content-Type: application/json" --data \'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\'',
].join('\n'))
const authorizationCodePython = computed(() => `# 适用：需要代表具体用户的 CRM/门户系统
# 用户先在浏览器完成 NanZi 登录和授权；下面代码运行在业务方后端回调中。
import os
import requests

base_url = os.environ.get("NANZI_BASE_URL", "${oauthBaseUrl.value}").rstrip("/")
token_response = requests.post(
    f"{base_url}/oauth/token",
    auth=(os.environ["NANZI_CLIENT_ID"], os.environ["NANZI_CLIENT_SECRET"]),
    data={
        "grant_type": "authorization_code",
        "code": callback_code,
        "redirect_uri": os.environ["NANZI_REDIRECT_URI"],
        "code_verifier": pkce_code_verifier,
        "resource": "${mcpResource.value}",
    },
    timeout=15,
)
token_response.raise_for_status()
access_token = token_response.json()["access_token"]
# 此 Access Token 已绑定用户；后续调用仍只携带 Bearer Token。
mcp_response = requests.post(
    "${mcpEndpoint.value}",
    headers={"Authorization": f"Bearer {access_token}"},
    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    timeout=30,
)
mcp_response.raise_for_status()`)

const generatedMcpJson = computed(() => JSON.stringify({
  mcpServers: {
    'nanzi-platform': {
      url: overview.value.mcp_endpoint || '/mcp/platform',
      headers: {
        Authorization: `Bearer ${oneTimeAccessToken.value}`,
      },
    },
  },
}, null, 2))

const copyGeneratedMcpJson = async () => {
  await copyValue('generated-mcp-json', generatedMcpJson.value)
}

const openTokenIssue = (client: Client) => {
  if (!canIssueToken.value || client.status !== 'active') return
  tokenClient.value = client
  tokenForm.scopes = [...client.allowed_scopes]
  tokenForm.expires_in = 3600
  tokenWizardStep.value = 1
  oneTimeAccessToken.value = ''
  accessTokenInfo.value = {}
  showTokenIssue.value = true
}

const issueCurrentUserToken = async () => {
  if (!tokenClient.value || !tokenForm.scopes.length) return
  saving.value = true
  error.value = ''
  try {
    const response = await api.post(
      `/api/portal/mcp-service/clients/${encodeURIComponent(tokenClient.value.client_id)}/user-access-token`,
      {
        scopes: tokenForm.scopes,
        expires_in: tokenForm.expires_in,
      },
    )
    oneTimeAccessToken.value = response.data.access_token || ''
    accessTokenInfo.value = response.data || {}
    recordSessionToken(oneTimeAccessToken.value, tokenClient.value?.client_name || 'Client', Number(response.data.expires_in))
    await loadClients()
    tokenWizardStep.value = 2
  } catch (err: any) {
    error.value = err?.response?.data?.detail || '当前用户 Token 生成失败'
  } finally {
    saving.value = false
  }
}

const closeTokenWizard = () => {
  showTokenIssue.value = false
  tokenWizardStep.value = 1
  oneTimeAccessToken.value = ''
  accessTokenInfo.value = {}
}

const openConfirmModal = (
  title: string,
  message: string,
  action: () => void | Promise<void>,
  type: 'danger' | 'primary' | 'warning' = 'danger',
) => {
  confirmModalTitle.value = title
  confirmModalMessage.value = message
  confirmModalType.value = type
  confirmModalAction.value = action
  showConfirmModal.value = true
}

const closeConfirmModal = () => {
  if (confirmModalLoading.value) return
  showConfirmModal.value = false
  confirmModalAction.value = null
}

const submitConfirmModal = async () => {
  const action = confirmModalAction.value
  if (!action || confirmModalLoading.value) return
  confirmModalLoading.value = true
  try {
    await action()
  } finally {
    confirmModalLoading.value = false
    showConfirmModal.value = false
    confirmModalAction.value = null
  }
}

const openClientConfirm = (action: ClientConfirmAction, client: Client) => {
  if (action === 'disable' && !canManageClientItem(client)) return
  if (action === 'reset-secret' && !canResetSecretForClient(client)) return
  if (action === 'delete' && !canManageClientItem(client)) return
  clientConfirmAction.value = action
  clientConfirmTarget.value = client
  showClientConfirm.value = true
}

const closeClientConfirm = (force: boolean | Event = false) => {
  const isForced = typeof force === 'boolean' ? force : false
  if (saving.value && !isForced) return
  showClientConfirm.value = false
  clientConfirmAction.value = null
  clientConfirmTarget.value = null
}

const scopeLabel = (scope: string) => scopeOptions.find(item => item[0] === scope)?.[1] || scope

const createClient = async () => {
  saving.value = true
  error.value = ''
  try {
    const response = await api.post('/api/portal/mcp-service/clients', {
      ...form,
      redirect_uris: form.redirect_uris.split(/\r?\n|,/).map(item => item.trim()).filter(Boolean),
    })
    oneTimeSecret.value = response.data.client_secret || ''
    secretRevealClientId.value = null
    showCreate.value = false
    form.client_name = ''
    form.redirect_uris = ''
    form.allowed_grant_types = ['authorization_code']
    form.allowed_scopes = ['knowledge:search']
    await loadClients()
  } catch (err: any) {
    error.value = err?.response?.data?.detail || 'Client 创建失败'
  } finally {
    saving.value = false
  }
}

const saveClientScopes = async () => {
  const client = clientScopeEditTarget.value
  if (!client || !canManageClientItem(client) || !clientScopeEditForm.scopes.length || saving.value) return
  const currentScopes = [...(client.allowed_scopes || [])].sort()
  const nextScopes = [...clientScopeEditForm.scopes].sort()
  if (JSON.stringify(currentScopes) === JSON.stringify(nextScopes)) {
    showToast('Scope 未变化，Client Secret 和 Access Token 均未变化', 'info')
    closeClientScopeEdit(true)
    return
  }
  saving.value = true
  error.value = ''
  try {
    await api.patch(`/api/portal/mcp-service/clients/${encodeURIComponent(client.client_id)}`, {
      allowed_scopes: clientScopeEditForm.scopes,
    })
    await loadClients()
    closeClientScopeEdit(true)
  } catch (err: any) {
    error.value = err?.response?.data?.detail || 'Client Scope 更新失败'
  } finally {
    saving.value = false
  }
}

const toggleClient = async (client: Client) => {
  if (!canManageClientItem(client)) return
  if (client.status === 'active') {
    openClientConfirm('disable', client)
    return
  }
  saving.value = true
  error.value = ''
  try {
    await api.patch(`/api/portal/mcp-service/clients/${encodeURIComponent(client.client_id)}`, {
      status: 'active',
    })
    await loadClients()
  } catch (err: any) {
    error.value = err?.response?.data?.detail || 'Client 状态更新失败'
  } finally {
    saving.value = false
  }
}

const confirmClientAction = async () => {
  const client = clientConfirmTarget.value
  const action = clientConfirmAction.value
  if (!client || !action || saving.value) return
  saving.value = true
  error.value = ''
  try {
    if (action === 'disable') {
      await api.patch(`/api/portal/mcp-service/clients/${encodeURIComponent(client.client_id)}`, {
        status: 'disabled',
      })
      await loadClients()
    } else if (action === 'reset-secret') {
      const response = await api.post(`/api/portal/mcp-service/clients/${encodeURIComponent(client.client_id)}/secret`)
      oneTimeSecret.value = response.data.client_secret || ''
      secretRevealClientId.value = client.client_id
      await loadClients()
    } else {
      await api.delete(`/api/portal/mcp-service/clients/${encodeURIComponent(client.client_id)}`)
      await loadClients()
    }
    closeClientConfirm(true)
  } catch (err: any) {
    error.value = err?.response?.data?.detail || (
      action === 'disable' ? 'Client 停用失败' : (action === 'delete' ? 'Client 删除失败' : 'Client Secret 重置失败')
    )
  } finally {
    saving.value = false
  }
}

const resetSecret = (client: Client) => {
  if (!canResetSecretForClient(client)) return
  openClientConfirm('reset-secret', client)
}

const removeClient = (client: Client) => {
  if (!canManageClientItem(client) || client.status === 'deleted') return
  openClientConfirm('delete', client)
}

let sessionTokenCleanupTimer: number | undefined
onMounted(() => {
  sessionTokenCleanupTimer = window.setInterval(() => {
    tokenClock.value = Date.now()
    purgeExpiredSessionTokens()
  }, 1_000)
  load()
})
onUnmounted(() => {
  if (sessionTokenCleanupTimer !== undefined) {
    window.clearInterval(sessionTokenCleanupTimer)
  }
})
</script>

<template>
  <div class="flex min-h-full flex-col space-y-4 text-slate-800">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div class="flex items-center gap-3">
            <h1 class="text-3xl font-black tracking-tight">MCP 服务台</h1>
            <span class="rounded-full bg-indigo-100 px-3 py-1 text-xs font-bold text-indigo-700">NanZi Platform MCP</span>
          </div>
          <p class="mt-2 text-sm text-slate-500">支持人工生成当前用户 Token，也支持外部系统通过 OAuth2 调用 NanZi 平台能力。</p>
        </div>
        <button
          type="button"
          class="workbench-refresh-btn inline-flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm font-medium text-gray-500 transition-colors hover:bg-blue-50 hover:text-blue-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-200 disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="loading"
          :aria-busy="loading"
          :aria-label="loading ? '刷新中' : '刷新 MCP 服务台'"
          @click="load"
        >
          <svg
            class="h-3.5 w-3.5"
            :class="{ 'animate-spin': loading }"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M4 4v5h.6m14.8 2A8 8 0 004.6 9m0 0H9m11 11v-5h-.6m0 0A8 8 0 014.6 13m14.8 2H15"
            />
          </svg>
          <span>{{ loading ? '刷新中' : '刷新' }}</span>
        </button>
      </div>

      <div v-if="error" class="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{{ error }}</div>
      <div v-if="oneTimeSecret && !secretRevealClientId" class="rounded-2xl border border-amber-200 bg-amber-50 p-4">
        <div class="flex items-center justify-between">
          <div class="font-bold text-amber-900">Client Secret 只显示本次，请立即复制保存</div>
          <button type="button" class="text-xs font-bold text-amber-700 hover:text-amber-900 underline" @click="oneTimeSecret = ''; secretRevealClientId = null">已保存并关闭</button>
        </div>
        <div class="mt-3 flex gap-2">
          <code class="min-w-0 flex-1 break-all rounded-lg bg-white px-3 py-2 text-sm">{{ oneTimeSecret }}</code>
          <button class="rounded-lg bg-amber-500 px-3 py-2 text-sm font-bold text-white" @click="copyValue('secret', oneTimeSecret)">{{ copied === 'secret' ? '已复制' : '复制' }}</button>
        </div>
      </div>

      <div v-if="availableTabs.length" class="flex flex-nowrap gap-2 overflow-x-auto border-b border-slate-200">
        <button
          v-for="tab in availableTabs"
          :key="tab.id"
          class="relative flex shrink-0 items-center gap-2 whitespace-nowrap border-b-2 px-4 py-3 text-sm font-bold transition-colors"
          :class="activeTab === tab.id ? 'border-indigo-600 text-indigo-700' : 'border-transparent text-slate-500 hover:text-slate-700'"
          @click="activeTab = tab.id"
        >
          <span>{{ tab.label }}</span>
          <span
            v-if="tab.badge"
            class="rounded-full px-2 py-0.5 text-[11px] font-semibold transition-colors"
            :class="activeTab === tab.id ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100 text-slate-600'"
          >
            {{ tab.badge }}
          </span>
          <span
            v-if="tab.hasAlert"
            class="relative flex h-2 w-2"
            title="检测到近期安全或限流事件"
          >
            <span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-rose-400 opacity-75"></span>
            <span class="relative inline-flex h-2 w-2 rounded-full bg-rose-500"></span>
          </span>
        </button>
      </div>
      <div v-else class="rounded-2xl border border-amber-200 bg-amber-50 p-5 text-sm text-amber-800">当前账号只有菜单权限，尚未分配 MCP 服务台的具体查看权限。</div>

      <section v-if="activeTab === 'guide' && canReadGuide" class="space-y-5">
        <!-- 动态配置注入与 Token 选择器 -->
        <div class="rounded-2xl border border-indigo-100 bg-gradient-to-r from-indigo-50/80 via-white to-blue-50/80 p-5 shadow-sm">
          <div class="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div class="flex items-center gap-3">
              <span class="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-indigo-600 font-bold text-white shadow-sm">⚡</span>
              <div>
                <h3 class="text-sm font-bold text-slate-800">动态 Token 实时注入配置</h3>
                <p class="mt-0.5 text-xs text-slate-500">
                  选择或粘贴你在服务台生成的 MCP Access Token，下方所有配置代码块将实时填充为完整可用的真实配置，一键复制即用。
                </p>
              </div>
            </div>
            <div class="flex flex-wrap items-center gap-2">
              <div v-if="activeSessionRecentTokens.length" class="flex items-center gap-1.5">
                <span class="text-xs text-slate-500">最近生成:</span>
                <select
                  v-model="guideSelectedToken"
                  class="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-mono text-slate-700 outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
                >
                  <option value="">手动输入或使用占位符</option>
                  <option v-for="tok in activeSessionRecentTokens" :key="tok.token" :value="tok.token">
                    {{ tok.label }}
                  </option>
                </select>
              </div>
              <input
                v-model="guideSelectedToken"
                class="min-w-[220px] flex-1 rounded-xl border border-slate-200 bg-white px-3 py-2 font-mono text-xs text-slate-700 outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
                placeholder="粘贴已生成的 Bearer Token"
              />
              <button
                v-if="guideSelectedToken"
                type="button"
                class="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-500 hover:bg-slate-50 transition"
                @click="guideSelectedToken = ''"
              >
                重置
              </button>
            </div>
          </div>
        </div>

        <div class="rounded-2xl bg-white p-6 shadow-sm">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 class="text-lg font-black">推荐：人工 / 低代码客户端</h2>
              <p class="mt-1 text-sm text-slate-500">适合 Cursor、Claude Desktop、Dify、Coze、n8n 等需要手动填写 MCP 配置的客户端。</p>
            </div>
            <span class="rounded-full bg-emerald-50 px-3 py-1 text-xs font-bold text-emerald-700">大多数接入优先使用</span>
          </div>
          <div class="mt-5 grid gap-3 md:grid-cols-5">
            <div v-for="(step, index) in [
              ['准备 Client', '有管理权限的人创建一次，普通用户使用已启用 Client。'],
              ['登录 NanZi', '使用要代表的用户登录平台。'],
              ['生成当前用户 Token', '在“外部 Client”点击生成，选择有效期和 Scope。'],
              ['复制 Token', 'Token 只显示本次，请立即复制保存。'],
              ['粘贴到客户端', '把 Token 放入 Authorization: Bearer Header。'],
            ]" :key="step[0]" class="rounded-xl border border-emerald-100 bg-emerald-50/50 p-4">
              <span class="inline-flex h-7 w-7 items-center justify-center rounded-full bg-emerald-600 text-xs font-black text-white">{{ index + 1 }}</span>
              <h3 class="mt-3 text-sm font-black text-slate-800">{{ step[0] }}</h3>
              <p class="mt-1 text-xs leading-5 text-slate-500">{{ step[1] }}</p>
            </div>
          </div>
          <div class="mt-5 rounded-xl border border-emerald-100 bg-emerald-50 p-4 text-sm leading-6 text-emerald-950">
            <p class="font-bold">这个 Token 代表谁？</p>
            <p class="mt-1">代表当前登录用户本人。Token 到期后，重新登录或返回 Client 卡片重新生成，不需要理解 OAuth2 授权跳转。</p>
            <p class="mt-1 font-bold">页面生成的 Token 和 OAuth2 获取的 Access Token 调用方式相同，都是 `Authorization: Bearer &lt;access_token&gt;`。</p>
          </div>
        </div>

        <div class="rounded-2xl bg-white p-6 shadow-sm">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 class="text-lg font-black">复制配置到 Cursor / 低代码客户端</h2>
              <p class="mt-1 text-sm text-slate-500">先在上面的 Client 卡片生成当前用户 Token，再把它设置到环境变量中；也可以替换为 OAuth2 动态获取的 Access Token。</p>
            </div>
            <button type="button" class="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-bold text-white hover:bg-indigo-700" @click="copyMcpJson">{{ copied === 'mcp-json' ? '已复制' : '复制 MCP JSON' }}</button>
          </div>
          <pre class="mt-4 overflow-x-auto rounded-xl bg-slate-950 p-4 text-xs leading-6 text-slate-100"><code>{{ mcpJson }}</code></pre>
          <div class="mt-4 grid gap-3 md:grid-cols-2">
            <div class="rounded-xl border border-slate-200 p-4">
              <h3 class="text-sm font-black">人工 Token 模式</h3>
              <p class="mt-1 text-xs leading-5 text-slate-500">Cursor、Claude Desktop、Dify 等支持静态 Header 的客户端，使用服务台生成的当前用户 Token；到期后重新生成并替换环境变量。</p>
            </div>
            <div class="rounded-xl border border-slate-200 p-4">
              <h3 class="text-sm font-black">支持 OAuth 自动发现的客户端</h3>
              <p class="mt-1 text-xs leading-5 text-slate-500">程序化或支持 OAuth 的客户端可填写 MCP Endpoint，读取 Protected Resource Metadata，并通过 Authorization Code + PKCE 打开 NanZi 授权页。</p>
            </div>
          </div>
          <p class="mt-4 text-xs leading-5 text-slate-500">不要把真实 Token、NanZi 用户 API Key 或 Client Secret 提交到代码仓库。Client Secret 只用于 OAuth Token Endpoint，Access Token 才用于调用 MCP。</p>
        </div>

        <div class="rounded-2xl bg-white p-6 shadow-sm">
          <div class="flex items-center justify-between">
            <div>
              <h2 class="text-lg font-black">主流客户端详细配置指南</h2>
              <p class="mt-1 text-sm text-slate-500">快速将 NanZi Platform MCP 接入你常用的桌面工具与工作流系统。</p>
            </div>
            <span class="rounded-full bg-indigo-50 px-3 py-1 text-xs font-bold text-indigo-700">配置路径 & 示例</span>
          </div>

          <div class="mt-5 grid gap-4 md:grid-cols-2">
            <!-- Claude Desktop 配置指南 -->
            <div class="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
              <div class="flex items-center gap-2">
                <span class="text-base font-bold text-slate-800">Claude Desktop</span>
                <span class="rounded-full bg-slate-200 px-2 py-0.5 text-[11px] font-bold text-slate-600">桌面端</span>
              </div>
              <p class="mt-1.5 text-xs text-slate-500">打开本地 Claude Desktop 配置文件进行配置：</p>
              <div class="mt-2 space-y-1.5 font-mono text-xs">
                <div class="rounded-lg bg-slate-200/70 p-2 break-all text-slate-700">
                  <span class="font-bold text-slate-500">macOS:</span> ~/Library/Application Support/Claude/claude_desktop_config.json
                </div>
                <div class="rounded-lg bg-slate-200/70 p-2 break-all text-slate-700">
                  <span class="font-bold text-slate-500">Windows:</span> %APPDATA%\Claude\claude_desktop_config.json
                </div>
              </div>
              <p class="mt-3 text-xs leading-5 text-slate-600">
                将上方「复制 MCP JSON」的内容合并到配置文件的 <code class="rounded bg-slate-200 px-1 py-0.5 text-slate-800">"mcpServers"</code> 节点下，保存后完全退出并重启 Claude Desktop 即可。
              </p>
            </div>

            <!-- Dify / Coze / n8n 低代码集成指南 -->
            <div class="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
              <div class="flex items-center gap-2">
                <span class="text-base font-bold text-slate-800">Dify / Coze / n8n</span>
                <span class="rounded-full bg-indigo-100 px-2 py-0.5 text-[11px] font-bold text-indigo-800">工作流 / Agent</span>
              </div>
              <p class="mt-1.5 text-xs text-slate-500">在工作流与低代码智能体编排平台中接入：</p>
              <ul class="mt-2 space-y-2 text-xs leading-5 text-slate-600">
                <li class="flex items-start gap-1.5">
                  <span class="font-bold text-indigo-600">•</span>
                  <span><strong>Dify:</strong> 在「工具」-「自定义 MCP 工具」中填入本平台的 MCP SSE/HTTP 地址，鉴权 Header 选择 <code class="font-mono text-slate-800">Authorization: Bearer &lt;Token&gt;</code>。</span>
                </li>
                <li class="flex items-start gap-1.5">
                  <span class="font-bold text-indigo-600">•</span>
                  <span><strong>Coze / 扣子:</strong> 在插件/工具中配置外部 HTTP API，请求头携带当前用户签发的 Access Token。</span>
                </li>
                <li class="flex items-start gap-1.5">
                  <span class="font-bold text-indigo-600">•</span>
                  <span><strong>n8n:</strong> 使用 HTTP Request 节点调用 MCP JSON-RPC 接口或使用 Community MCP 节点，指定 Bearer Auth 凭据。</span>
                </li>
              </ul>
            </div>
          </div>
        </div>

        <div class="rounded-2xl bg-white p-6 shadow-sm">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 class="text-lg font-black">程序化系统接入</h2>
              <p class="mt-1 text-sm text-slate-500">适合 CRM、OA、后台服务和无人值守任务，通过标准 OAuth2 动态获取绑定用户的 Access Token。</p>
            </div>
            <span class="rounded-full bg-indigo-50 px-3 py-1 text-xs font-bold text-indigo-700">OAuth2 / OIDC 标准方向</span>
          </div>
          <div class="mt-5 grid gap-3 md:grid-cols-2">
            <div class="rounded-xl border border-slate-200 bg-slate-50 p-4"><h3 class="text-sm font-black">需要关联用户</h3><p class="mt-1 text-xs leading-5 text-slate-500">使用 Authorization Code + PKCE，用户在 NanZi 授权页确认后，Access Token 会绑定对应 user_id。</p></div>
            <div class="rounded-xl border border-slate-200 bg-slate-50 p-4"><h3 class="text-sm font-black">调用方式</h3><p class="mt-1 text-xs leading-5 text-slate-500">调用 MCP 时统一携带 Authorization: Bearer；NanZi 服务端校验 Token、Scope、Client 和当前用户角色权限。</p></div>
          </div>
          <div class="mt-5 rounded-xl border border-blue-100 bg-blue-50 p-4 text-sm leading-6 text-blue-900">
            <p class="font-bold">程序化流程</p>
            <p class="mt-1">注册 Client → 调用 OAuth2 Token Endpoint → 缓存 Access Token 到期时间 → 携带 Bearer 调用 MCP → 到期后重新获取。程序化系统不应把浏览器登录 Cookie 或 NanZi 用户 API Key 传给第三方。</p>
          </div>
        </div>

        <div class="rounded-2xl bg-white p-6 shadow-sm">
          <div>
            <h2 class="text-lg font-black">Client Secret 怎么用？</h2>
            <p class="mt-1 text-sm leading-6 text-slate-500">Client Secret 只用于获取 Access Token，是“外部系统证明自己是谁”的凭证；它不是 Bearer Token，也不直接发送给 MCP 接口。</p>
          </div>
          <div class="mt-5 overflow-x-auto rounded-xl border border-slate-200">
            <table class="min-w-[760px] w-full text-left text-sm">
              <thead class="bg-slate-50 text-xs text-slate-500"><tr class="border-b border-slate-200"><th class="p-3">凭证</th><th class="p-3">放在哪里</th><th class="p-3">作用</th><th class="p-3">是否代表用户</th></tr></thead>
              <tbody>
                <tr class="border-b border-slate-100"><td class="p-3 font-mono font-bold text-amber-700">client_id + client_secret</td><td class="p-3">业务方后端 Token Endpoint 请求</td><td class="p-3">证明外部 Client，并兑换用户授权后的 Access Token</td><td class="p-3">本身不代表用户；用户登录并同意授权后，兑换出的 Token 才绑定该用户</td></tr>
                <tr><td class="p-3 font-mono font-bold text-indigo-700">Authorization: Bearer &lt;access_token&gt;</td><td class="p-3">每次 MCP 请求</td><td class="p-3">调用 NanZi Platform MCP</td><td class="p-3">始终代表完成 NanZi 登录授权的用户，并受该用户角色和权限限制</td></tr>
              </tbody>
            </table>
          </div>
          <div class="mt-4 rounded-xl border border-amber-100 bg-amber-50 p-4 text-sm leading-6 text-amber-950">
            <p class="font-bold">先判断使用场景</p>
            <ul class="mt-2 list-disc space-y-1 pl-5">
              <li>CRM 需要按当前员工权限查询或调用：使用 Authorization Code + PKCE。员工登录 NanZi 并授权，业务后端再用 Client Secret 换取绑定该员工的 Token。</li>
              <li>Cursor、Claude Desktop 等人工配置：通常直接在 NanZi 服务台生成当前用户 Token，不需要把 Client Secret 放进客户端配置。</li>
            </ul>
          </div>
        </div>

        <div class="rounded-2xl bg-white p-6 shadow-sm">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 class="text-lg font-black">示例一：用户授权后用 Client Secret 换 Token</h2>
              <p class="mt-1 text-sm text-slate-500">用户完成 NanZi 登录和授权后，业务方后端使用回调 code、PKCE verifier 和 Client Secret 换取用户 Token。</p>
            </div>
            <button type="button" class="rounded-xl border border-indigo-200 px-4 py-2 text-sm font-bold text-indigo-700 hover:bg-indigo-50" @click="copyValue('authorization-code-curl', authorizationCodeCurl)">{{ copied === 'authorization-code-curl' ? '已复制' : '复制 curl 示例' }}</button>
          </div>
          <pre class="mt-4 max-h-96 overflow-auto rounded-xl bg-slate-950 p-4 text-xs leading-6 text-slate-100"><code>{{ authorizationCodeCurl }}</code></pre>
        </div>

        <div class="rounded-2xl bg-white p-6 shadow-sm">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 class="text-lg font-black">示例二：Python（requests）实现用户授权换 Token</h2>
              <p class="mt-1 text-sm text-slate-500">用户先打开 NanZi 授权页并登录；授权回调后，下面的换 Token 代码必须运行在业务方后端，不能放在浏览器前端。</p>
            </div>
            <button type="button" class="rounded-xl border border-indigo-200 px-4 py-2 text-sm font-bold text-indigo-700 hover:bg-indigo-50" @click="copyValue('authorization-code-python', authorizationCodePython)">{{ copied === 'authorization-code-python' ? '已复制' : '复制 Python 示例' }}</button>
          </div>
          <pre class="mt-4 max-h-96 overflow-auto rounded-xl bg-slate-950 p-4 text-xs leading-6 text-slate-100"><code>{{ authorizationCodePython }}</code></pre>
          <div class="mt-4 rounded-xl border border-blue-100 bg-blue-50 p-4 text-sm leading-6 text-blue-900">
            <p class="font-bold">这段流程的关键点</p>
            <p class="mt-1">Client Secret 证明“哪个业务系统”在换 Token；用户在 NanZi 授权页登录，证明“哪个用户”；NanZi 最终签发的 Access Token 同时绑定 Client、用户、Scope 和 MCP Resource。后续调用 MCP 时只发送 Bearer Token。</p>
          </div>
          <p class="mt-4 text-xs leading-5 text-slate-500">不要把 Client Secret 放进 Cursor、浏览器、移动端或提交到代码仓库；不要把 Client Secret 填到 MCP JSON 的 Authorization Header。MCP JSON 里应该放 Access Token，或者放由客户端运行时替换的 Token 环境变量。</p>
        </div>

        <div class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-black">当前接入信息</h2>
          <div class="mt-4 grid gap-3">
            <div v-for="item in endpointHelpItems" :key="item.key" class="group relative flex flex-wrap items-center gap-3 rounded-xl bg-slate-50 p-3">
              <div class="w-full sm:w-56"><div class="flex items-center gap-1.5 text-sm font-bold text-slate-700"><span>{{ item.label }}</span><button type="button" class="inline-flex h-5 w-5 items-center justify-center rounded-full border border-indigo-300 text-xs font-black text-indigo-600 hover:bg-indigo-50" :aria-label="`查看${item.label}说明`" @click="openEndpointHelp(item.key)">?</button></div><div class="mt-1 text-xs text-slate-500">{{ item.description }}</div></div>
              <code class="min-w-0 flex-1 break-all pr-10 text-xs text-slate-600">{{ item.value || '启动并完成配置后显示' }}</code>
              <button v-if="item.value" type="button" class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-slate-200/70 bg-white/80 text-sm font-bold text-indigo-600 opacity-0 shadow-sm transition-opacity hover:border-indigo-200 hover:bg-white focus-visible:opacity-100 group-hover:opacity-100 max-md:opacity-100" :aria-label="`复制${item.label}地址`" :title="copied === item.key ? '已复制' : `复制${item.label}地址`" @click="copyValue(item.key, item.value)">{{ copied === item.key ? '✓' : '⧉' }}</button>
            </div>
          </div>
        </div>
      </section>

      <section v-if="activeTab === 'overview' && canReadOverview" class="space-y-5">
        <div v-if="canReadAudit" class="rounded-2xl bg-white p-6 shadow-sm">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 class="text-lg font-black">MCP 调用概览</h2>
              <p class="mt-1 text-sm text-slate-500">基于你有权限查看的审计日志统计。</p>
            </div>
            <select v-model="auditSummaryRange" class="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-bold text-slate-600" aria-label="审计统计周期" :disabled="auditSummaryLoading" @change="loadAuditSummary">
              <option value="24h">近 24 小时</option>
              <option value="7d">近 7 天</option>
              <option value="30d">近 30 天</option>
            </select>
          </div>
          <div class="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <!-- 调用次数 -->
            <div class="group rounded-2xl border border-slate-100 bg-slate-50/80 p-4 transition-all duration-200 hover:border-slate-300 hover:shadow-xs">
              <div class="flex items-center justify-between">
                <span class="text-xs font-bold text-slate-500">调用次数</span>
                <span class="flex h-7 w-7 items-center justify-center rounded-lg bg-white text-slate-500 shadow-2xs transition-transform group-hover:scale-110">
                  <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <line x1="18" y1="20" x2="18" y2="10" />
                    <line x1="12" y1="20" x2="12" y2="4" />
                    <line x1="6" y1="20" x2="6" y2="14" />
                  </svg>
                </span>
              </div>
              <div class="mt-2 text-2xl font-black text-slate-800">{{ auditSummary.total_calls ?? '—' }}</div>
            </div>

            <!-- 成功率 -->
            <div class="group rounded-2xl border border-emerald-100 bg-emerald-50/60 p-4 transition-all duration-200 hover:border-emerald-300 hover:shadow-xs">
              <div class="flex items-center justify-between">
                <span class="text-xs font-bold text-emerald-800">成功率</span>
                <span class="flex h-7 w-7 items-center justify-center rounded-lg bg-white text-emerald-600 shadow-2xs transition-transform group-hover:scale-110">
                  <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                    <polyline points="22 4 12 14.01 9 11.01" />
                  </svg>
                </span>
              </div>
              <div class="mt-2 text-2xl font-black text-emerald-700">{{ auditSummary.success_rate != null ? auditSummary.success_rate + '%' : '—' }}</div>
            </div>

            <!-- 失败 / 拒绝 -->
            <div class="group rounded-2xl border border-rose-100 bg-rose-50/60 p-4 transition-all duration-200 hover:border-rose-300 hover:shadow-xs">
              <div class="flex items-center justify-between">
                <span class="text-xs font-bold text-rose-800">失败 / 拒绝</span>
                <span class="flex h-7 w-7 items-center justify-center rounded-lg bg-white text-rose-600 shadow-2xs transition-transform group-hover:scale-110">
                  <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="12" cy="12" r="10" />
                    <line x1="12" y1="8" x2="12" y2="12" />
                    <line x1="12" y1="16" x2="12.01" y2="16" />
                  </svg>
                </span>
              </div>
              <div class="mt-2 text-2xl font-black text-rose-700">{{ auditSummary.failed_or_denied ?? '—' }}</div>
              <div class="mt-1 text-[11px] text-rose-600">失败 {{ auditSummary.failed_calls ?? 0 }} · 拒绝 {{ auditSummary.denied_calls ?? 0 }}</div>
            </div>

            <!-- P95 耗时 -->
            <div class="group rounded-2xl border border-indigo-100 bg-indigo-50/60 p-4 transition-all duration-200 hover:border-indigo-300 hover:shadow-xs">
              <div class="flex items-center justify-between">
                <span class="text-xs font-bold text-indigo-800">P95 耗时</span>
                <span class="flex h-7 w-7 items-center justify-center rounded-lg bg-white text-indigo-600 shadow-2xs transition-transform group-hover:scale-110">
                  <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
                  </svg>
                </span>
              </div>
              <div class="mt-2 text-2xl font-black text-indigo-700">{{ auditSummary.p95_latency_ms != null ? auditSummary.p95_latency_ms + ' ms' : '—' }}</div>
            </div>
          </div>
        </div>

        <div class="grid gap-4 md:grid-cols-3">
          <!-- 服务状态 -->
          <div class="group flex items-center justify-between rounded-2xl border border-slate-100 bg-white p-5 shadow-xs transition-all duration-200 hover:border-emerald-200 hover:shadow-md">
            <div>
              <div class="text-xs font-bold uppercase tracking-wider text-slate-400">服务状态</div>
              <div class="mt-1.5 flex items-center gap-2">
                <span class="text-2xl font-black" :class="overview.platform_enabled ? 'text-emerald-600' : 'text-slate-400'">
                  {{ overview.platform_enabled ? '已启用' : '已关闭' }}
                </span>
                <span
                  class="inline-block h-2 w-2 rounded-full"
                  :class="overview.platform_enabled ? 'bg-emerald-500 animate-pulse' : 'bg-slate-300'"
                />
              </div>
              <div class="mt-1 text-xs text-slate-400">Platform MCP 核心入口</div>
            </div>
            <div
              class="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl transition-transform duration-200 group-hover:scale-110"
              :class="overview.platform_enabled ? 'bg-emerald-50 text-emerald-600' : 'bg-slate-100 text-slate-400'"
            >
              <svg class="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="2" y="2" width="20" height="8" rx="2" ry="2" />
                <rect x="2" y="14" width="20" height="8" rx="2" ry="2" />
                <line x1="6" y1="6" x2="6.01" y2="6" />
                <line x1="6" y1="18" x2="6.01" y2="18" />
              </svg>
            </div>
          </div>

          <!-- 活跃 Client -->
          <div class="group flex items-center justify-between rounded-2xl border border-slate-100 bg-white p-5 shadow-xs transition-all duration-200 hover:border-indigo-200 hover:shadow-md">
            <div>
              <div class="text-xs font-bold uppercase tracking-wider text-slate-400">活跃 Client</div>
              <div class="mt-1.5 text-2xl font-black text-slate-800">{{ overview.active_client_count ?? 0 }}</div>
              <div class="mt-1 text-xs text-slate-400">外部系统与授权接入数</div>
            </div>
            <div class="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-indigo-50 text-indigo-600 transition-transform duration-200 group-hover:scale-110">
              <svg class="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
                <circle cx="9" cy="7" r="4" />
                <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
                <path d="M16 3.13a4 4 0 0 1 0 7.75" />
              </svg>
            </div>
          </div>

          <!-- 已发布方法 -->
          <div class="group flex items-center justify-between rounded-2xl border border-slate-100 bg-white p-5 shadow-xs transition-all duration-200 hover:border-purple-200 hover:shadow-md">
            <div>
              <div class="text-xs font-bold uppercase tracking-wider text-slate-400">已发布方法</div>
              <div class="mt-1.5 text-2xl font-black text-slate-800">{{ overview.published_method_count ?? 0 }}</div>
              <div class="mt-1 text-xs text-slate-400">平台提供的 MCP Tools</div>
            </div>
            <div class="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-purple-50 text-purple-600 transition-transform duration-200 group-hover:scale-110">
              <svg class="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="16 18 22 12 16 6" />
                <polyline points="8 6 2 12 8 18" />
              </svg>
            </div>
          </div>
        </div>

        <div class="rounded-2xl bg-white p-6 shadow-sm"><h2 class="text-lg font-black">外部系统接入信息</h2><div class="mt-4 grid gap-3"><div v-for="item in endpointHelpItems" :key="item.key" class="group relative flex flex-wrap items-center gap-3 rounded-xl bg-slate-50 p-3"><span class="flex w-full items-center gap-1.5 text-sm font-bold text-slate-600 sm:w-48"><span>{{ item.label }}</span><button type="button" class="inline-flex h-5 w-5 items-center justify-center rounded-full border border-indigo-300 text-xs font-black text-indigo-600 hover:bg-indigo-50" :aria-label="`查看${item.label}说明`" @click="openEndpointHelp(item.key)">?</button></span><code class="min-w-0 flex-1 break-all pr-10 text-xs">{{ item.value || '—' }}</code><button v-if="item.value" type="button" class="absolute right-3 top-1/2 inline-flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-full border border-slate-200/70 bg-white/80 text-sm font-bold text-indigo-600 opacity-0 shadow-sm transition-opacity hover:border-indigo-200 hover:bg-white focus-visible:opacity-100 group-hover:opacity-100 max-md:opacity-100" :aria-label="`复制${item.label}地址`" :title="copied === item.key ? '已复制' : `复制${item.label}地址`" @click="copyValue(item.key, item.value)">{{ copied === item.key ? '✓' : '⧉' }}</button></div></div></div>
      </section>

      <section v-else-if="activeTab === 'config' && canReadConfig" class="space-y-5">
        <div class="rounded-2xl bg-white p-6 shadow-sm">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div><h2 class="text-lg font-black">Platform MCP 服务配置</h2><p class="mt-1 text-sm text-slate-500">这些开关只属于 NanZi Platform MCP，不影响「MCP 工具集」中的出站 MCP 服务。</p></div>
            <span class="rounded-full bg-slate-100 px-3 py-1 text-xs font-bold text-slate-500">总开关 → 能力组 → Client</span>
          </div>
          <div class="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <button
              v-for="item in ([['platform_enabled', 'Platform MCP'], ['agent_enabled', '智能体'], ['conversation_enabled', '会话'], ['knowledge_enabled', '知识库'], ['metadata_enabled', '元数据']] as const)"
              :key="item[0]"
              type="button"
              role="switch"
              :aria-checked="config[item[0]] === true"
              :aria-label="`${item[1]}开关`"
              class="group flex cursor-pointer items-center justify-between gap-4 rounded-xl border p-4 text-left transition-all duration-200 disabled:cursor-not-allowed disabled:opacity-75 hover:shadow-xs"
              :class="config[item[0]] === true ? 'border-emerald-200 bg-emerald-50/70 hover:border-emerald-300' : 'border-slate-200 bg-slate-50/70 hover:border-slate-300'"
              :disabled="(item[0] === 'platform_enabled' ? !canEditConfig : !canManageCapability) || saving"
              @click="toggleConfig(item[0])"
            >
              <span class="min-w-0">
                <span class="flex items-center gap-2">
                  <span
                    class="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg transition-colors"
                    :class="config[item[0]] === true ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-200 text-slate-500'"
                  >
                    <!-- Platform MCP: Server -->
                    <svg v-if="item[0] === 'platform_enabled'" class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <rect x="2" y="2" width="20" height="8" rx="2" ry="2" />
                      <rect x="2" y="14" width="20" height="8" rx="2" ry="2" />
                      <line x1="6" y1="6" x2="6.01" y2="6" />
                      <line x1="6" y1="18" x2="6.01" y2="18" />
                    </svg>
                    <!-- 智能体: Bot -->
                    <svg v-else-if="item[0] === 'agent_enabled'" class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <rect x="3" y="11" width="18" height="10" rx="2" />
                      <circle cx="12" cy="5" r="2" />
                      <path d="M12 7v4" />
                      <line x1="8" y1="16" x2="8.01" y2="16" stroke-width="2" />
                      <line x1="16" y1="16" x2="16.01" y2="16" stroke-width="2" />
                    </svg>
                    <!-- 会话: Chat -->
                    <svg v-else-if="item[0] === 'conversation_enabled'" class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                    </svg>
                    <!-- 知识库: Book -->
                    <svg v-else-if="item[0] === 'knowledge_enabled'" class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                      <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
                    </svg>
                    <!-- 元数据: Database -->
                    <svg v-else-if="item[0] === 'metadata_enabled'" class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <ellipse cx="12" cy="5" rx="9" ry="3" />
                      <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
                      <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
                    </svg>
                  </span>
                  <span class="block text-sm font-bold text-slate-800">{{ item[1] }}</span>
                </span>
                <span class="mt-2 block text-xs font-bold" :class="config[item[0]] === true ? 'text-emerald-700' : 'text-slate-500'">
                  {{ config[item[0]] ? '已开启' : '已关闭' }}
                </span>
                <span class="mt-1 block text-xs font-normal text-slate-500">
                  {{ item[0] === 'platform_enabled' ? '控制整个服务' : '控制对应能力组' }}
                </span>
              </span>
              <span
                class="relative inline-flex h-6 w-11 shrink-0 rounded-full transition-colors duration-200"
                :class="config[item[0]] === true ? 'bg-emerald-500' : 'bg-slate-300'"
                aria-hidden="true"
              >
                <span
                  class="pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow-sm transition-transform duration-200"
                  :class="config[item[0]] === true ? 'translate-x-5' : 'translate-x-0.5'"
                />
              </span>
            </button>
          </div>
          <p v-if="!canEditConfig && !canManageCapability" class="mt-4 text-xs text-slate-400">当前账号只有配置查看权限，不能修改开关。</p>
        </div>
        <div class="rounded-2xl bg-white p-6 shadow-sm">
          <div>
            <div class="flex items-center gap-2">
              <span class="flex h-7 w-7 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600">
                <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
                </svg>
              </span>
              <h2 class="text-lg font-black text-slate-800">调用限流</h2>
            </div>
            <p class="mt-1 text-sm text-slate-500">按固定一分钟窗口限制调用次数，单位：次/分钟；设置为 0 表示关闭对应限制。</p>
          </div>
          <div class="mt-4 grid gap-3 sm:grid-cols-2">
            <label class="group rounded-xl border border-slate-200/80 bg-slate-50 p-4 text-sm font-bold text-slate-700 transition hover:border-indigo-200 hover:bg-slate-50/80">
              <div class="flex items-center justify-between">
                <span>单个 Client 每分钟上限</span>
                <span class="text-slate-400 group-hover:text-indigo-500 transition-colors">
                  <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
                    <line x1="8" y1="21" x2="16" y2="21" />
                    <line x1="12" y1="17" x2="12" y2="21" />
                  </svg>
                </span>
              </div>
              <span class="mt-2 flex items-center rounded-lg border border-slate-200 bg-white focus-within:border-indigo-500 focus-within:ring-2 focus-within:ring-indigo-100"><input v-model.number="config.rate_limit_client_per_minute" type="number" min="0" max="100000" class="min-w-0 flex-1 rounded-l-lg border-0 bg-transparent px-3 py-2 font-normal outline-none focus:ring-0" :disabled="!canEditConfig || saving" @change="updateRateLimit('rate_limit_client_per_minute')" /><span class="shrink-0 border-l border-slate-200 px-3 py-2 text-xs font-bold text-slate-400">次/分钟</span></span>
            </label>
            <label class="group rounded-xl border border-slate-200/80 bg-slate-50 p-4 text-sm font-bold text-slate-700 transition hover:border-indigo-200 hover:bg-slate-50/80">
              <div class="flex items-center justify-between">
                <span>单个用户每分钟上限</span>
                <span class="text-slate-400 group-hover:text-indigo-500 transition-colors">
                  <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                    <circle cx="12" cy="7" r="4" />
                  </svg>
                </span>
              </div>
              <span class="mt-2 flex items-center rounded-lg border border-slate-200 bg-white focus-within:border-indigo-500 focus-within:ring-2 focus-within:ring-indigo-100"><input v-model.number="config.rate_limit_user_per_minute" type="number" min="0" max="100000" class="min-w-0 flex-1 rounded-l-lg border-0 bg-transparent px-3 py-2 font-normal outline-none focus:ring-0" :disabled="!canEditConfig || saving" @change="updateRateLimit('rate_limit_user_per_minute')" /><span class="shrink-0 border-l border-slate-200 px-3 py-2 text-xs font-bold text-slate-400">次/分钟</span></span>
            </label>
          </div>
        </div>
        <div class="rounded-2xl bg-white p-6 text-sm text-slate-600 shadow-sm">
          <div class="flex items-center gap-2">
            <span class="flex h-7 w-7 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
              <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="16" x2="12" y2="12" />
                <line x1="12" y1="8" x2="12.01" y2="8" />
              </svg>
            </span>
            <h2 class="text-lg font-black text-slate-800">生效规则</h2>
          </div>
          <p class="mt-3">Platform MCP 总开关开启后，只有已开启的能力组才会发布到 MCP 工具列表；具体调用仍需通过 Client 的 Scope、用户授权和资源权限校验。</p>
        </div>
      </section>

      <section v-else-if="activeTab === 'clients' && canReadClients" class="rounded-2xl bg-white p-6 shadow-sm">
        <div class="mb-5 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 class="text-lg font-black">外部 Client</h2>
            <p class="mt-1 text-sm text-slate-500">Secret 只在创建或重置时显示一次。</p>
          </div>
          <div class="flex flex-wrap gap-2">
            <button v-if="canReadGrants" type="button" class="rounded-xl border border-indigo-200 px-4 py-2 text-sm font-bold text-indigo-700 hover:bg-indigo-50" @click="showGrants = true; loadGrants()">已授权应用 (Grants)</button>
            <button v-if="canReadGuide" type="button" class="rounded-xl border border-indigo-200 px-4 py-2 text-sm font-bold text-indigo-700 hover:bg-indigo-50" @click="activeTab = 'guide'">？使用指南</button>
            <button v-if="canManageClient" type="button" class="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-bold text-white hover:bg-indigo-700" @click="showCreate = true">创建 Client</button>
          </div>
        </div>
        <div class="mb-5 flex items-center justify-between gap-3">
          <span v-if="isAdmin" class="text-xs font-bold text-indigo-700">管理员视角：查看全部用户的 Client</span>
          <span v-else class="text-xs text-slate-500">展示当前账号创建及全员共享的 Client</span>
          <button type="button" class="shrink-0 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-600 hover:bg-slate-50" @click="showClientFilters = !showClientFilters">{{ showClientFilters ? '收起筛选' : '展开筛选' }}</button>
        </div>
        <div v-if="showClientFilters" class="mb-5 flex flex-wrap items-center gap-3 rounded-xl bg-slate-50 p-3">
          <input v-model="clientSearch" class="min-w-[220px] flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm" placeholder="搜索 Client 名称、ID 或所属用户" @keyup.enter="applyClientFilters" />
          <select v-model="clientStatus" class="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm"><option value="">全部状态</option><option value="active">启用</option><option value="disabled">停用</option></select>
          <button type="button" class="rounded-lg bg-indigo-600 px-4 py-2 text-xs font-bold text-white" @click="applyClientFilters">查询</button>
          <span class="text-xs text-slate-500">共 {{ clientTotal }} 个</span>
        </div>
        <div v-if="!clients.length" class="rounded-2xl border border-dashed border-slate-200 bg-slate-50/60 p-10 text-center">
          <div class="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-indigo-50 text-2xl text-indigo-600 shadow-xs">
            🔌
          </div>
          <div class="mt-4 text-base font-bold text-slate-800">暂无外部 Client</div>
          <p class="mx-auto mt-1.5 max-w-md text-xs leading-relaxed text-slate-500">
            创建 OAuth Client 后，外部系统（如 Cursor、Claude Desktop、Dify、Coze）可通过 OAuth2 授权访问 NanZi 平台的各项智能体与知识库能力。
          </p>
          <div class="mt-5">
            <button
              v-if="canManageClient"
              type="button"
              class="inline-flex items-center gap-2 rounded-xl bg-indigo-600 px-5 py-2.5 text-xs font-bold text-white shadow-sm transition hover:bg-indigo-700"
              @click="showCreate = true"
            >
              <span>+ 创建第一个 Client</span>
            </button>
          </div>
        </div>
        <div v-else class="space-y-3">
          <div v-for="client in clients" :key="client.client_id" class="rounded-2xl border border-slate-200 p-5 transition-all hover:border-indigo-300 hover:shadow-xs">
            <div v-if="client.needs_token_regeneration" class="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-900">
              <div><span class="font-bold">当前 Client 需要重新生成 MCP Access Token</span><span class="ml-1">原有 Access Token 已失效，请重新生成 MCP Access Token。</span></div>
              <button v-if="canIssueToken" type="button" class="shrink-0 rounded-lg bg-amber-600 px-3 py-1.5 font-bold text-white hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-50" :disabled="client.status !== 'active' || !client.allowed_scopes.length" @click="openTokenIssue(client)">立即生成</button>
            </div>
            <div class="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
              <div class="min-w-0">
                <div class="flex items-center gap-2">
                  <div class="text-base font-black text-slate-800">{{ client.client_name }}</div>
                  <span v-if="client.is_shared" class="inline-flex items-center gap-1 rounded-full border border-blue-200 bg-gradient-to-r from-blue-50 to-indigo-50 px-2.5 py-0.5 text-[11px] font-bold text-indigo-700 shadow-xs">
                    <svg class="h-3 w-3 text-indigo-500" viewBox="0 0 20 20" fill="currentColor"><path d="M9 6a3 3 0 11-6 0 3 3 0 016 0zM17 6a3 3 0 11-6 0 3 3 0 016 0zM12.93 17c.046-.327.07-.66.07-1a6.97 6.97 0 00-1.5-4.33A5 5 0 0119 16v1h-6.07zM6 11a5 5 0 015 5v1H1v-1a5 5 0 015-5z"/></svg>
                    全员共享
                  </span>
                </div>
                <div class="mt-1 text-xs text-slate-500">所属用户：<span class="font-bold text-slate-700">{{ client.owner_real_name || client.owner_user_name || '未知用户' }}</span><span class="ml-1">· ID {{ client.created_by || '—' }}</span></div>
                <div class="group mt-1.5 flex flex-wrap items-center gap-2">
                  <span class="rounded-full bg-slate-100 px-2 py-1 text-[11px] font-bold text-slate-600">Client ID</span>
                  <button
                    type="button"
                    class="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-slate-50 px-2.5 py-0.5 font-mono text-xs text-slate-600 transition hover:border-indigo-300 hover:bg-indigo-50 hover:text-indigo-700 cursor-pointer"
                    :title="copied === 'client-id-' + client.client_id ? '已复制！' : '点击快速复制 Client ID'"
                    @click="copyValue('client-id-' + client.client_id, client.client_id)"
                  >
                    <code class="break-all">{{ client.client_id }}</code>
                    <span class="text-[10px] font-bold" :class="copied === 'client-id-' + client.client_id ? 'text-emerald-600' : 'text-slate-400 group-hover:text-indigo-500'">
                      {{ copied === 'client-id-' + client.client_id ? '✓ 已复制' : '复制' }}
                    </span>
                  </button>
                </div>
                <p class="mt-1 text-xs text-slate-400">Client ID 用于 Token Endpoint，需配合 Client Secret 获取 Access Token；不能直接调用 MCP。</p>
              </div>
              <div data-testid="client-actions" class="relative flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 xl:justify-end">
                <button v-if="canIssueToken" type="button" class="inline-flex shrink-0 items-center justify-center whitespace-nowrap rounded-xl border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs font-bold text-indigo-700 transition hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-50" :disabled="client.status !== 'active' || !client.allowed_scopes.length" @click="openTokenIssue(client)">生成 MCP Access Token</button>
                <button v-if="canReadClients" type="button" class="inline-flex shrink-0 items-center justify-center whitespace-nowrap rounded-xl border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs font-bold text-indigo-700 hover:bg-indigo-100" @click="openTokenDetails(client)">Token 管理 <span class="ml-1 rounded-full bg-white px-1.5 py-0.5 text-[10px]">{{ client.token_total_count || 0 }}</span></button>
                <button type="button" class="inline-flex shrink-0 items-center justify-center whitespace-nowrap rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-600 hover:bg-slate-50" @click="clientActionMenuId = clientActionMenuId === client.client_id ? null : client.client_id">更多操作 <span class="ml-1">⌄</span></button>
                <button
                  type="button"
                  class="inline-flex h-9 w-9 items-center justify-center rounded-lg text-indigo-700 transition hover:bg-indigo-50 hover:text-indigo-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2"
                  :aria-label="expandedClientIds.has(client.client_id) ? '收起 Client 详情' : '展开 Client 详情'"
                  :title="expandedClientIds.has(client.client_id) ? '收起详情' : '展开详情'"
                  :aria-expanded="expandedClientIds.has(client.client_id)"
                  :aria-controls="'client-details-' + client.client_id"
                  @click="toggleClientExpanded(client.client_id)"
                >
                  <svg class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path v-if="expandedClientIds.has(client.client_id)" d="m6 9 6 6 6-6" />
                    <path v-else d="m9 6 6 6-6 6" />
                  </svg>
                </button>
                <div v-if="clientActionMenuId === client.client_id" class="absolute right-0 top-11 z-20 w-44 rounded-xl border border-slate-200 bg-white p-1.5 shadow-xl">
                  <button v-if="canManageClientItem(client)" type="button" class="block w-full rounded-lg px-3 py-2 text-left text-xs font-bold text-indigo-700 hover:bg-indigo-50" @click="clientActionMenuId = null; openClientEdit(client)">编辑基本信息</button>
                  <button v-if="canReadAudit" type="button" class="block w-full rounded-lg px-3 py-2 text-left text-xs font-bold text-indigo-700 hover:bg-indigo-50" @click="openClientUsage(client)">使用统计</button>
                  <button v-if="canManageClientItem(client)" type="button" class="block w-full rounded-lg px-3 py-2 text-left text-xs font-bold text-indigo-700 hover:bg-indigo-50" @click="clientActionMenuId = null; openClientScopeEdit(client)">编辑 Scope</button>
                  <button v-if="canManageClientItem(client)" type="button" class="block w-full rounded-lg px-3 py-2 text-left text-xs font-bold text-indigo-700 hover:bg-indigo-50" @click="clientActionMenuId = null; toggleClient(client)">{{ client.status === 'active' ? '停用 Client' : '启用 Client' }}</button>
                  <button v-if="canResetSecretForClient(client)" type="button" class="block w-full rounded-lg px-3 py-2 text-left text-xs font-bold text-amber-700 hover:bg-amber-50" @click="clientActionMenuId = null; resetSecret(client)">重置 Secret</button>
                  <button v-if="canManageClientItem(client)" type="button" class="block w-full rounded-lg px-3 py-2 text-left text-xs font-bold text-rose-700 hover:bg-rose-50" @click="clientActionMenuId = null; removeClient(client)">删除 Client</button>
                </div>
              </div>
            </div>
            <div v-if="oneTimeSecret && secretRevealClientId === client.client_id" class="mt-4 rounded-xl border border-amber-200/80 bg-amber-50/80 p-4">
              <div class="flex items-center justify-between">
                <div class="font-bold text-amber-900">Client Secret 已重置，请立即复制保存</div>
                <button type="button" class="text-xs font-bold text-amber-700 hover:text-amber-900 underline" @click="oneTimeSecret = ''; secretRevealClientId = null">已保存并关闭</button>
              </div>
              <div class="mt-3 flex min-w-0 items-center gap-2"><code class="min-w-0 flex-1 break-all rounded-lg bg-white px-3 py-2 text-sm text-slate-700">{{ oneTimeSecret }}</code><button type="button" class="shrink-0 rounded-lg bg-amber-500 px-3 py-2 text-sm font-bold text-white hover:bg-amber-600" @click="copyValue('secret', oneTimeSecret)">{{ copied === 'secret' ? '已复制' : '复制' }}</button></div>
            </div>
            <p class="mt-2 text-right text-[11px] text-slate-400">
              <template v-if="client.has_issued_token">最近签发：{{ formatClientTime(client.last_token_issued_at) }} · {{ client.last_token_issue_method === 'oauth_authorization' ? 'OAuth 用户授权' : '手动签发' }}</template>
              <template v-else>尚未生成 Access Token</template>
            </p>
            <div class="mt-3 flex flex-wrap gap-1.5 text-[11px]">
              <span class="rounded-full bg-slate-100 px-2.5 py-1 font-bold text-slate-600">Token {{ client.token_total_count || 0 }}</span>
              <span class="rounded-full bg-emerald-50 px-2.5 py-1 font-bold text-emerald-700">有效 {{ client.active_token_count || 0 }}</span>
              <span class="rounded-full bg-amber-50 px-2.5 py-1 font-bold text-amber-700">即将过期 {{ client.expiring_token_count || 0 }}</span>
              <span class="rounded-full bg-orange-50 px-2.5 py-1 font-bold text-orange-700">已过期 {{ client.expired_token_count || 0 }}</span>
              <span class="rounded-full bg-slate-100 px-2.5 py-1 font-bold text-slate-500">已撤销 {{ client.revoked_token_count || 0 }}</span>
            </div>
            <div class="mt-3 flex flex-wrap items-center gap-2 rounded-xl border border-indigo-100 bg-indigo-50/40 px-3 py-2">
              <span class="mr-1 text-xs font-black text-slate-700">资源访问</span>
              <template v-for="config in resourceWhitelistConfigs" :key="config.field">
                <button v-if="canManageClientItem(client)" type="button" class="rounded-lg border px-2.5 py-1.5 text-[11px] font-bold shadow-sm transition" :class="resourcePolicyButtonClass(client, config.field)" :title="resourcePolicySummary(client, config.field)" @click="openResourceWhitelist(client, config)">{{ config.buttonLabel }}</button>
              </template>
            </div>
            <div v-if="expandedClientIds.has(client.client_id)" :id="'client-details-' + client.client_id">
            <div class="mt-4 grid gap-3 md:grid-cols-3">
              <div class="rounded-xl border border-slate-100 bg-slate-50 px-3 py-3"><div class="text-[11px] font-bold text-slate-400">Token 状态</div><div class="mt-1 text-sm font-bold" :class="(client.active_token_count || 0) > 0 ? 'text-emerald-700' : 'text-slate-600'">{{ (client.active_token_count || 0) > 0 ? '状态正常' : (client.has_issued_token ? '暂无有效 Token' : '尚未生成') }}</div></div>
              <div class="rounded-xl border border-slate-100 bg-slate-50 px-3 py-3"><div class="text-[11px] font-bold text-slate-400">有效 Token 数量</div><div class="mt-1 text-sm font-bold text-slate-700">{{ client.active_token_count || 0 }} 个</div></div>
              <div class="rounded-xl border border-slate-100 bg-slate-50 px-3 py-3"><div class="text-[11px] font-bold text-slate-400">最近过期时间</div><div class="mt-1 text-sm font-bold text-slate-700">{{ client.latest_token_expires_at ? formatClientTime(client.latest_token_expires_at) : '—' }}</div><div class="mt-1 text-xs font-bold" :class="remainingTokenDays(client.latest_token_expires_at) === 0 ? 'text-rose-600' : 'text-slate-500'">{{ remainingTokenDays(client.latest_token_expires_at) === null ? '—' : remainingTokenDays(client.latest_token_expires_at) === 0 ? '已过期' : `还剩 ${remainingTokenDays(client.latest_token_expires_at)} 天` }}</div></div>
            </div>
            <div class="mt-4 border-t border-slate-100 pt-4">
              <div class="mb-3 flex items-center gap-2">
                <span class="text-xs font-bold uppercase tracking-wide text-slate-500">权限摘要与回调</span>
                <span class="text-[11px] text-slate-400">调用权限会同时受用户自身权限限制</span>
              </div>
              <div class="grid gap-3 md:grid-cols-3">
                <div class="rounded-xl bg-slate-50 px-3 py-3">
                  <div class="text-[11px] font-bold text-slate-400">授权方式</div>
                  <div class="mt-1 text-sm font-bold text-slate-700">用户授权</div>
                  <div class="mt-1 text-xs text-slate-500">Authorization Code + PKCE</div>
                </div>
                <div class="rounded-xl bg-slate-50 px-3 py-3">
                  <div class="text-[11px] font-bold text-slate-400">Scope</div>
                  <div class="mt-1 text-sm font-bold text-slate-700">{{ client.allowed_scopes.length }} 项已授权</div>
                  <div class="mt-1 truncate text-xs text-slate-500" :title="client.allowed_scopes.join('、')">{{ scopeSummary(client) }}</div>
                </div>
                <div class="rounded-xl bg-slate-50 px-3 py-3">
                  <div class="text-[11px] font-bold text-slate-400">回调地址 (Redirect URIs)</div>
                  <div class="mt-1 max-h-12 overflow-y-auto space-y-0.5">
                    <div v-for="uri in (client.redirect_uris || [])" :key="uri" class="truncate font-mono text-xs text-slate-600" :title="uri">{{ uri }}</div>
                    <div v-if="!(client.redirect_uris || []).length" class="text-xs text-slate-400">未配置</div>
                  </div>
                </div>
              </div>
              <div class="mt-4 grid gap-3 md:grid-cols-3">
                <div v-for="config in resourceWhitelistConfigs" :key="config.field" class="rounded-xl border border-indigo-100 bg-indigo-50/50 p-3">
                  <div class="text-xs font-bold text-slate-500">{{ config.title }}</div>
                  <div class="mt-1 text-sm font-black text-slate-800">{{ resourcePolicySummary(client, config.field) }}</div>
                  <p class="mt-1 text-[11px] leading-4 text-slate-500">实际权限仍受当前用户权限限制</p>
                </div>
              </div>
              <p class="mt-3 rounded-xl border border-blue-100 bg-blue-50 px-3 py-2 text-xs leading-5 text-blue-900">资源权限按“当前用户权限 ∩ Client 白名单”生效；未配置白名单时跟随用户权限，配置空白名单时表示该 Client 不可访问此类资源。</p>
              <div class="mt-3 flex justify-end">
                <button type="button" class="text-xs font-bold text-indigo-700 hover:text-indigo-900" @click="openClientDetails(client)">查看权限详情 →</button>
              </div>
            </div>
            </div>
            <div data-testid="client-status" aria-label="Client 状态" class="mt-3 flex items-center justify-end gap-2 text-xs">
              <span class="h-2 w-2 rounded-full" :class="client.status === 'active' ? 'bg-emerald-500' : 'bg-slate-400'" aria-hidden="true"></span>
              <span class="font-bold" :class="client.status === 'active' ? 'text-emerald-700' : 'text-slate-500'">状态：{{ client.status === 'active' ? '启用' : '停用' }}</span>
            </div>
          </div>
          <div class="flex items-center justify-end gap-3 pt-2 text-xs text-slate-500"><span>第 {{ clientPage }} / {{ Math.max(1, Math.ceil(clientTotal / 20)) }} 页</span><button type="button" class="rounded-lg border border-slate-200 px-3 py-1.5 disabled:opacity-40" :disabled="clientPage <= 1" @click="changeClientPage(-1)">上一页</button><button type="button" class="rounded-lg border border-slate-200 px-3 py-1.5 disabled:opacity-40" :disabled="clientPage >= Math.ceil(clientTotal / 20)" @click="changeClientPage(1)">下一页</button></div>
        </div>
      </section>

      <section v-else-if="activeTab === 'methods' && canReadMethods" class="rounded-2xl bg-white p-4 shadow-sm sm:p-6">
        <div class="flex items-center justify-between">
          <h2 class="text-lg font-black">能力与 Scope</h2>
          <span class="text-xs text-slate-500">支持只读 MCP 方法在线探针测试</span>
        </div>
        <div class="mt-4 hidden overflow-x-auto md:block">
          <table class="w-full min-w-[680px] text-left text-sm">
            <thead><tr class="border-b text-slate-500"><th class="p-3">方法</th><th class="p-3">Scope</th><th class="p-3">能力组</th><th class="p-3">身份/权限模式</th><th class="p-3">状态</th><th class="p-3">操作</th></tr></thead>
            <tbody>
              <tr v-for="method in methods" :key="method.name" class="border-b last:border-0">
                <td class="p-3 font-mono font-bold">{{ method.name }}</td>
                <td class="p-3 font-mono text-indigo-700">{{ method.scope }}</td>
                <td class="p-3">{{ method.capability_group }}</td>
                <td class="p-3">必须用户授权</td>
                <td class="p-3" :class="method.implemented && method.enabled ? 'text-emerald-600' : 'text-slate-400'">{{ !method.implemented ? '待接入' : (method.enabled ? '已启用' : '已关闭') }}</td>
                <td class="p-3">
                  <button v-if="method.implemented && method.enabled" type="button" class="rounded-lg bg-indigo-50 px-2.5 py-1 text-xs font-bold text-indigo-700 hover:bg-indigo-100" @click="openPlayground(method)">在线调试</button>
                  <span v-else class="text-xs text-slate-400">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="mt-4 space-y-3 md:hidden">
          <article v-for="method in methods" :key="method.name" class="rounded-xl border border-slate-200 bg-slate-50/70 p-4">
            <div class="break-all font-mono text-sm font-black text-slate-900">{{ method.name }}</div>
            <div class="mt-3 flex flex-wrap gap-2 text-xs">
              <code class="break-all rounded-full bg-indigo-50 px-2.5 py-1 font-bold text-indigo-700">{{ method.scope }}</code>
              <span class="rounded-full bg-slate-200 px-2.5 py-1 font-semibold text-slate-600">{{ method.capability_group }}</span>
            </div>
            <div class="mt-3 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-xs text-slate-500">
              <span>身份：必须用户授权</span>
              <span class="font-bold" :class="method.implemented && method.enabled ? 'text-emerald-600' : 'text-slate-400'">{{ !method.implemented ? '待接入' : (method.enabled ? '已启用' : '已关闭') }}</span>
            </div>
            <div v-if="method.implemented && method.enabled" class="mt-3 border-t border-slate-100 pt-2 flex justify-end">
              <button type="button" class="rounded-lg bg-indigo-50 px-2.5 py-1 text-xs font-bold text-indigo-700 hover:bg-indigo-100" @click="openPlayground(method)">在线调试</button>
            </div>
          </article>
        </div>
      </section>

      <section v-else-if="activeTab === 'audit' && canReadAudit" class="rounded-2xl bg-white p-6 shadow-sm">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 class="text-lg font-black">MCP 入站调用审计</h2>
            <p class="mt-1 text-sm text-slate-500">查看外部系统调用 NanZi Platform MCP 的记录；这里只展示审计字段，不展示 Token、Secret 或原始请求头。</p>
            <p class="mt-1 text-sm text-slate-500">{{ isAdmin ? '管理员可查看全部 MCP 入站调用记录。' : '其他用户仅能查看自己发起的调用记录。' }}</p>
          </div>
          <div class="flex items-center gap-2">
            <span class="rounded-full bg-slate-100 px-3 py-1 text-xs font-bold text-slate-600">共 {{ auditTotal }} 条</span>
            <button
              type="button"
              class="rounded-lg border border-indigo-200 bg-white px-3 py-2 text-xs font-bold text-indigo-700 hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-50"
              :disabled="exportingAudit"
              @click="exportAudit"
            >
              {{ exportingAudit ? '正在导出…' : '导出 CSV' }}
            </button>
          </div>
        </div>

        <div class="mt-3 flex flex-wrap items-center justify-between gap-2">
          <div class="flex flex-wrap items-center gap-1.5">
            <template v-for="item in auditFilterOptions" :key="item.key">
              <span
                v-if="auditFilters[item.key] && auditFilters[item.key].trim()"
                class="inline-flex items-center gap-1 rounded-full border border-indigo-200/60 bg-indigo-50 px-2.5 py-1 text-xs font-medium text-indigo-700"
              >
                <span>{{ item.label }}: {{ auditFilters[item.key] }}</span>
                <button
                  type="button"
                  class="ml-0.5 rounded-full px-1 font-bold text-indigo-500 hover:bg-indigo-200 hover:text-indigo-800"
                  :aria-label="`清除 ${item.label} 筛选`"
                  @click="removeAuditFilter(item.key)"
                >×</button>
              </span>
            </template>
            <span v-if="!activeAuditFilterCount" class="text-xs text-slate-400">无已生效筛选条件</span>
          </div>
          <button
            type="button"
            class="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-600 hover:bg-slate-50"
            @click="showAuditFilters = !showAuditFilters"
          >
            <span>{{ showAuditFilters ? '收起筛选' : '展开筛选' }}</span>
            <span v-if="activeAuditFilterCount" class="rounded-full bg-indigo-600 px-1.5 py-0.2 text-[10px] font-bold text-white">{{ activeAuditFilterCount }}</span>
          </button>
        </div>
        <div v-if="showAuditFilters" class="mt-3 space-y-3">
          <div class="flex flex-nowrap items-center gap-3 overflow-x-auto pb-1">
          <label class="flex shrink-0 items-center gap-2 text-xs font-bold text-slate-500">开始 <input v-model="auditStartAt" type="datetime-local" class="rounded-lg border border-slate-200 bg-white px-2 py-2 text-xs font-normal" /></label>
          <label class="flex shrink-0 items-center gap-2 text-xs font-bold text-slate-500">结束 <input v-model="auditEndAt" type="datetime-local" class="rounded-lg border border-slate-200 bg-white px-2 py-2 text-xs font-normal" /></label>
          <label class="shrink-0 text-xs font-bold text-slate-500">过滤对象</label>
          <select v-model="selectedAuditFilter" class="w-44 shrink-0 rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm">
            <option v-for="item in auditFilterOptions" :key="item.key" :value="item.key">{{ item.label }}</option>
          </select>
          <select v-if="selectedAuditFilterMeta.kind === 'select'" v-model="selectedAuditFilterValue" class="min-w-56 flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm">
            <option value="">全部</option>
            <option v-for="item in selectedAuditFilterMeta.options || []" :key="item[0]" :value="item[0]">{{ item[1] }}</option>
          </select>
          <label class="shrink-0 text-xs font-bold text-slate-500">过滤值</label>
          <input v-if="selectedAuditFilterMeta.kind !== 'select'" v-model="selectedAuditFilterValue" :inputmode="selectedAuditFilterMeta.kind === 'number' ? 'numeric' : undefined" class="min-w-56 flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm" :class="selectedAuditFilterMeta.key === 'method_name' ? 'font-mono' : ''" :placeholder="selectedAuditFilterMeta.placeholder" @keyup.enter="applyAuditFilters" />
          <button type="button" class="shrink-0 rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-xs font-bold text-slate-600 hover:bg-slate-100" @click="resetAuditFilters">重置</button>
          <button type="button" class="shrink-0 rounded-lg bg-indigo-600 px-4 py-2.5 text-xs font-bold text-white hover:bg-indigo-700 disabled:opacity-50" :disabled="auditLoading" @click="applyAuditFilters">{{ auditLoading ? '查询中…' : '查询' }}</button>
          </div>
        </div>

        <div class="mt-5 rounded-xl border border-indigo-100 bg-indigo-50/50 p-4">
          <div class="flex items-center justify-between gap-3">
            <h3 class="min-w-0 flex-1 text-sm font-black text-slate-800">
              <button
                type="button"
                class="flex w-full items-center gap-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 focus-visible:ring-offset-2"
                aria-controls="audit-trend-content"
                :aria-expanded="showAuditTrend"
                @click="showAuditTrend = !showAuditTrend"
              >
                <span>调用趋势</span>
                <span aria-hidden="true" class="text-xs text-slate-400">{{ showAuditTrend ? '⌃' : '⌄' }}</span>
              </button>
            </h3>
            <select v-model="auditSummaryRange" class="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs font-bold" @change="handleAuditSummaryRangeChange">
              <option value="24h">近 24 小时</option><option value="7d">近 7 天</option><option value="30d">近 30 天</option>
            </select>
          </div>
          <div id="audit-trend-content" v-if="showAuditTrend">
            <div v-if="auditTrend.length" class="mt-4 flex h-28 items-end gap-1 overflow-x-auto">
              <div v-for="item in auditTrend" :key="item.at" class="flex min-w-8 flex-1 flex-col items-center justify-end gap-1" :title="formatAuditTime(item.at) + '：' + item.total + ' 次'">
                <div class="flex h-20 w-full items-end"><div class="w-full rounded-t bg-indigo-400 transition-[height]" :style="{ height: trendBarHeight(item.total) }" /></div>
                <span class="text-[9px] text-slate-400">{{ item.total }}</span>
              </div>
            </div>
            <div v-else class="py-6 text-center text-xs text-slate-400">当前周期暂无调用数据</div>
          </div>
        </div>

        <div class="mt-5 rounded-xl border border-amber-100 bg-amber-50/40 p-4">
          <h3 class="text-sm font-black text-slate-800">
            <button
              type="button"
              class="flex w-full items-center justify-between gap-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 focus-visible:ring-offset-2"
              aria-controls="oauth-security-content"
              :aria-expanded="showSecurityAudit"
              @click="showSecurityAudit = !showSecurityAudit"
            >
              <span>OAuth 安全事件</span>
              <span class="flex items-center gap-2 text-xs font-normal text-slate-500">
                <span v-if="securityAlert.alert" class="rounded-full bg-rose-100 px-2 py-0.5 font-bold text-rose-700">有安全告警</span>
                <span>共 {{ securityAuditLogs.length }} 条</span>
                <span aria-hidden="true" class="text-xs text-slate-400">{{ showSecurityAudit ? '⌃' : '⌄' }}</span>
              </span>
            </button>
          </h3>
          <div id="oauth-security-content" v-if="showSecurityAudit">
            <div v-if="securityAlert.alert" class="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-bold text-rose-700">{{ securityAlert.message || '检测到近期安全异常，请及时检查审计日志。' }} 失败/拒绝 {{ securityAlert.recent_failure_count }} 次，限流 {{ securityAlert.rate_limited_count }} 次。</div>
            <div v-if="!securityAuditLogs.length" class="py-6 text-center text-xs text-slate-400">当前筛选范围暂无 OAuth 安全事件</div>
            <div v-else class="mt-3 overflow-x-auto">
              <table class="min-w-[720px] w-full text-left text-xs">
                <thead class="border-b border-amber-100 text-slate-500"><tr><th class="p-2">时间</th><th class="p-2">事件</th><th class="p-2">Client</th><th class="p-2">用户</th><th class="p-2">结果</th></tr></thead>
                <tbody><tr v-for="log in securityAuditLogs" :key="log.id" class="border-b border-amber-100/60 last:border-0"><td class="whitespace-nowrap p-2 text-slate-500">{{ formatAuditTime(log.created_at) }}</td><td class="p-2 font-mono text-indigo-700">{{ log.event_type }}</td><td class="p-2 font-mono">{{ log.client_id || '—' }}</td><td class="p-2">{{ log.user_id || log.actor_user_id || '—' }}</td><td class="p-2">{{ log.result_status }}</td></tr></tbody>
              </table>
            </div>
          </div>
        </div>

        <div v-if="auditLoading" class="py-12 text-center text-sm text-slate-500">审计日志加载中…</div>
        <div v-else-if="!auditLogs.length" class="py-12 text-center text-sm text-slate-500">暂无符合条件的 MCP 调用记录</div>
        <div v-else class="mt-5 overflow-x-auto rounded-xl border border-slate-200">
          <table class="min-w-[1080px] w-full text-left text-sm">
            <thead class="bg-slate-50 text-xs text-slate-500"><tr class="border-b border-slate-200"><th class="p-3">时间</th><th class="p-3">Client / 用户</th><th class="p-3">方法</th><th class="p-3">资源关联</th><th class="p-3">认证 / Scope</th><th class="p-3">结果</th><th class="p-3">耗时</th><th class="p-3">操作</th></tr></thead>
            <tbody>
              <tr v-for="log in auditLogs" :key="log.id" class="border-b border-slate-100 last:border-0">
                <td class="p-3 whitespace-nowrap text-xs text-slate-500">{{ formatAuditTime(log.created_at) }}</td>
                <td class="p-3"><div class="font-bold text-slate-700">{{ log.client_id }}</div><div class="mt-1 text-xs text-slate-500">{{ log.user_id ? `user_id=${log.user_id}` : '历史记录无用户身份' }}</div></td>
                <td class="p-3 font-mono text-xs font-bold text-indigo-700">{{ log.method_name }}</td>
                <td class="p-3 text-xs text-slate-500"><div v-if="log.agent_id">agent={{ log.agent_id }}</div><div v-if="log.conversation_id">conversation={{ log.conversation_id }}</div><div v-if="log.dataset_id">dataset={{ log.dataset_id }}</div><span v-if="!log.agent_id && !log.conversation_id && !log.dataset_id">—</span></td>
                <td class="p-3 text-xs text-slate-500"><div>{{ auditAuthTypeLabel(log.auth_type) }}</div><div class="mt-1 max-w-48 break-words text-[11px]">{{ log.scopes.join('、') || '—' }}</div></td>
                <td class="p-3"><span class="rounded-full px-2 py-1 text-xs font-bold" :class="log.result_status === 'completed' ? 'bg-emerald-100 text-emerald-700' : (log.result_status === 'denied' ? 'bg-amber-100 text-amber-700' : 'bg-rose-100 text-rose-700')">{{ auditResultLabel(log.result_status) }} {{ log.status_code }}</span><div v-if="log.error_code" class="mt-1 text-xs text-rose-600">{{ log.error_code }}</div></td>
                <td class="p-3 whitespace-nowrap text-xs text-slate-500">{{ log.latency_ms ?? '—' }}{{ log.latency_ms != null ? ' ms' : '' }}</td>
                <td class="p-3"><button type="button" class="whitespace-nowrap text-xs font-bold text-indigo-700 hover:text-indigo-900" @click="selectedAudit = log">查看详情</button></td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="auditTotal" class="mt-4 flex flex-wrap items-center justify-between gap-3 text-xs text-slate-500">
          <span>第 {{ auditPage }} / {{ Math.max(1, Math.ceil(auditTotal / auditPageSize)) }} 页</span>
          <div class="flex gap-2"><button type="button" class="rounded-lg border border-slate-200 px-3 py-1.5 font-bold disabled:cursor-not-allowed disabled:opacity-40" :disabled="auditLoading || auditPage <= 1" @click="changeAuditPage(-1)">上一页</button><button type="button" class="rounded-lg border border-slate-200 px-3 py-1.5 font-bold disabled:cursor-not-allowed disabled:opacity-40" :disabled="auditLoading || auditPage >= Math.ceil(auditTotal / auditPageSize)" @click="changeAuditPage(1)">下一页</button></div>
        </div>
      </section>

      <div
        v-if="showClientUsage && clientUsageTarget"
        class="fixed inset-0 z-50 overflow-y-auto bg-slate-900/50 p-4"
        @click.self="closeClientUsage"
      >
        <div class="flex min-h-full items-center justify-center">
          <div class="w-full max-w-6xl max-h-[calc(100vh-2rem)] overflow-y-auto rounded-2xl bg-white shadow-2xl">
            <header class="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-slate-100 bg-white px-6 py-5">
              <div class="min-w-0">
                <h2 class="text-xl font-black text-slate-800">使用统计</h2>
                <p class="mt-1 break-all text-sm text-slate-500">{{ clientUsageTarget.client_name }} · {{ clientUsageTarget.client_id }}</p>
                <p class="mt-1 text-xs text-slate-400">{{ isAdmin ? '管理员：统计该 Client 的全部用户调用' : '仅统计当前用户发起的调用' }}</p>
              </div>
              <div class="flex shrink-0 items-center gap-3">
                <select v-model="clientUsageRange" class="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-600" :disabled="clientUsageLoading" aria-label="使用统计周期" @change="loadClientUsage">
                  <option value="7d">近 7 天</option>
                  <option value="30d">近 30 天</option>
                  <option value="90d">近 90 天</option>
                </select>
                <button type="button" class="text-2xl text-slate-400 hover:text-slate-600" aria-label="关闭使用统计" @click="closeClientUsage">×</button>
              </div>
            </header>

            <div v-if="clientUsageLoading" class="px-6 py-16 text-center text-sm text-slate-500">使用统计加载中…</div>
            <div v-else-if="clientUsageError" class="px-6 py-16 text-center">
              <p class="text-sm text-rose-600">{{ clientUsageError }}</p>
              <button type="button" class="mt-4 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-bold text-white hover:bg-indigo-700" @click="loadClientUsage">重新加载</button>
            </div>
            <div v-else-if="clientUsage" class="space-y-5 px-6 py-5">
              <div class="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
                <div class="rounded-xl border border-slate-100 bg-slate-50/80 p-3"><div class="flex items-center gap-1.5 text-[11px] font-bold text-slate-500"><svg class="h-4 w-4 shrink-0 text-indigo-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 19V5"/><path d="M4 19h17"/><path d="M8 16v-5"/><path d="M12 16V8"/><path d="M16 16v-3"/></svg>调用总量</div><div class="mt-1 text-lg font-black text-slate-800">{{ clientUsage.summary.total_calls }}</div></div>
                <div class="rounded-xl border border-slate-100 bg-slate-50/80 p-3"><div class="flex items-center gap-1.5 text-[11px] font-bold text-slate-500"><svg class="h-4 w-4 shrink-0 text-emerald-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16 9"/></svg>成功率</div><div class="mt-1 text-lg font-black text-emerald-600">{{ clientUsage.summary.success_rate }}%</div></div>
                <div class="rounded-xl border border-slate-100 bg-slate-50/80 p-3"><div class="flex items-center gap-1.5 text-[11px] font-bold text-slate-500"><svg class="h-4 w-4 shrink-0 text-rose-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m12 3 9 16H3L12 3Z"/><path d="M12 9v4"/><path d="M12 16h.01"/></svg>失败 / 拒绝</div><div class="mt-1 text-lg font-black text-rose-600">{{ clientUsage.summary.failed_calls }} / {{ clientUsage.summary.denied_calls }}</div></div>
                <div class="rounded-xl border border-slate-100 bg-slate-50/80 p-3"><div class="flex items-center gap-1.5 text-[11px] font-bold text-slate-500"><svg class="h-4 w-4 shrink-0 text-indigo-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>活跃用户</div><div class="mt-1 text-lg font-black text-indigo-600">{{ clientUsage.summary.active_user_count }}</div></div>
                <div class="rounded-xl border border-slate-100 bg-slate-50/80 p-3"><div class="flex items-center gap-1.5 text-[11px] font-bold text-slate-500"><svg class="h-4 w-4 shrink-0 text-slate-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>平均耗时</div><div class="mt-1 text-lg font-black text-slate-800">{{ clientUsage.summary.average_latency_ms == null ? '—' : `${clientUsage.summary.average_latency_ms} ms` }}</div></div>
                <div class="rounded-xl border border-slate-100 bg-slate-50/80 p-3"><div class="flex items-center gap-1.5 text-[11px] font-bold text-slate-500"><svg class="h-4 w-4 shrink-0 text-slate-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 15a8 8 0 1 1 16 0"/><path d="M12 15 16 9"/><path d="M4 19h16"/></svg>P95 耗时</div><div class="mt-1 text-lg font-black text-slate-800">{{ clientUsage.summary.p95_latency_ms == null ? '—' : `${clientUsage.summary.p95_latency_ms} ms` }}</div></div>
              </div>

              <div class="grid gap-5 lg:grid-cols-2">
                <section class="rounded-xl border border-slate-200 p-4">
                  <div class="flex items-center justify-between gap-3"><h3 class="text-sm font-black text-slate-800">每日调用趋势</h3><div class="flex gap-2 text-[10px] text-slate-500"><span><i class="mr-1 inline-block h-2 w-2 rounded-full bg-indigo-400" />成功</span><span><i class="mr-1 inline-block h-2 w-2 rounded-full bg-rose-400" />失败</span><span><i class="mr-1 inline-block h-2 w-2 rounded-full bg-amber-400" />拒绝</span></div></div>
                  <div v-if="!clientUsage.daily_trend.length" class="py-10 text-center text-xs text-slate-400">当前周期暂无调用数据</div>
                  <div v-else ref="clientUsageTrendRef" class="mt-4 flex h-44 items-end gap-1 overflow-x-auto">
                    <div v-for="item in clientUsage.daily_trend" :key="item.date" class="flex min-w-8 flex-none flex-col items-center justify-end gap-1" :title="`${item.date}：${item.total} 次`">
                      <div class="flex h-32 w-full items-end"><div class="flex w-full flex-col justify-end overflow-hidden rounded-t transition-[height]" :style="{ height: `${Math.max(item.total ? 8 : 0, Math.round(item.total / clientUsageMax * 120))}px` }"><div class="w-full bg-indigo-400" :style="{ height: `${item.total ? item.completed / item.total * 100 : 0}%` }" /><div class="w-full bg-rose-400" :style="{ height: `${item.total ? item.failed / item.total * 100 : 0}%` }" /><div class="w-full bg-amber-400" :style="{ height: `${item.total ? item.denied / item.total * 100 : 0}%` }" /></div></div>
                      <span class="text-[10px] text-slate-500">{{ item.total }}</span><span class="text-[9px] text-slate-400">{{ item.date.slice(5) }}</span>
                    </div>
                  </div>
                  <div v-if="clientUsage.daily_trend.length" class="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500"><span>成功 {{ clientUsage.summary.completed_calls }}</span><span>失败 {{ clientUsage.summary.failed_calls }}</span><span>拒绝 {{ clientUsage.summary.denied_calls }}</span></div>
                </section>

                <section class="rounded-xl border border-slate-200 p-4">
                  <h3 class="text-sm font-black text-slate-800">按方法分布</h3>
                  <div v-if="!clientUsage.method_distribution.length" class="py-10 text-center text-xs text-slate-400">当前周期暂无调用数据</div>
                  <div v-else class="mt-4 space-y-3">
                    <div v-for="item in clientUsage.method_distribution" :key="item.name"><div class="flex items-center justify-between gap-3 text-xs"><code class="min-w-0 break-all font-bold text-indigo-700">{{ item.name }}</code><span class="shrink-0 text-slate-500">{{ item.total }} 次 · {{ item.success_rate }}%</span></div><div class="mt-1 h-2 rounded-full bg-slate-100"><div class="h-2 rounded-full bg-indigo-500" :style="{ width: usageBarWidth(item.total, clientUsage.summary.total_calls) }" /></div></div>
                  </div>
                </section>

                <section class="rounded-xl border border-slate-200 p-4">
                  <h3 class="text-sm font-black text-slate-800">结果状态分布</h3>
                  <div v-if="!clientUsage.status_distribution.length" class="py-10 text-center text-xs text-slate-400">当前周期暂无调用数据</div>
                  <div v-else class="mt-4 space-y-3"><div v-for="item in clientUsage.status_distribution" :key="item.name"><div class="flex items-center justify-between text-xs"><span class="font-bold text-slate-700">{{ usageStatusLabel(item.name) }}</span><span class="text-slate-500">{{ item.total }} 次 · {{ usagePercent(item.total, clientUsage.summary.total_calls) }}</span></div><div class="mt-1 h-2 rounded-full bg-slate-100"><div class="h-2 rounded-full bg-emerald-500" :style="{ width: usageBarWidth(item.total, clientUsage.summary.total_calls) }" /></div></div></div>
                </section>

                <section class="rounded-xl border border-slate-200 p-4">
                  <h3 class="text-sm font-black text-slate-800">认证类型分布</h3>
                  <div v-if="!clientUsage.auth_distribution.length" class="py-10 text-center text-xs text-slate-400">当前周期暂无调用数据</div>
                  <div v-else class="mt-4 space-y-3"><div v-for="item in clientUsage.auth_distribution" :key="item.name"><div class="flex items-center justify-between text-xs"><span class="font-bold text-slate-700">{{ usageAuthLabel(item.name) }}</span><span class="text-slate-500">{{ item.total }} 次 · {{ usagePercent(item.total, clientUsage.summary.total_calls) }}</span></div><div class="mt-1 h-2 rounded-full bg-slate-100"><div class="h-2 rounded-full bg-purple-500" :style="{ width: usageBarWidth(item.total, clientUsage.summary.total_calls) }" /></div></div></div>
                </section>

                <section class="rounded-xl border border-slate-200 p-4">
                  <h3 class="text-sm font-black text-slate-800">用户调用排行</h3>
                  <div v-if="!clientUsage.user_distribution.length" class="py-10 text-center text-xs text-slate-400">当前周期暂无调用数据</div>
                  <div v-else class="mt-4 space-y-2"><div v-for="item in clientUsage.user_distribution.slice(0, 10)" :key="item.user_id" class="flex items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2 text-xs"><span class="min-w-0 truncate text-slate-700" :title="`${item.display_name} · user_id=${item.user_id}`"><span class="font-bold">{{ item.display_name }}</span><span v-if="item.real_name && item.user_name" class="ml-1 text-slate-400">@{{ item.user_name }}</span></span><span class="shrink-0 font-bold text-indigo-700">{{ item.total }} 次 · {{ usagePercent(item.total, clientUsage.summary.total_calls) }}</span></div></div>
                </section>

                <section class="rounded-xl border border-slate-200 p-4">
                  <h3 class="text-sm font-black text-slate-800">资源关联排行</h3>
                  <div v-if="!clientUsage.resource_distribution.length" class="py-10 text-center text-xs text-slate-400">当前周期暂无调用数据</div>
                  <div v-else class="mt-4 space-y-2"><div v-for="item in clientUsage.resource_distribution.slice(0, 10)" :key="`${item.type}-${item.name}`" class="flex items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2 text-xs"><span class="min-w-0 break-all text-slate-700"><span class="mr-1 rounded bg-indigo-50 px-1.5 py-0.5 font-bold text-indigo-700">{{ item.type }}</span>{{ usageResourceLabel(item.name) }}</span><span class="shrink-0 font-bold text-indigo-700">{{ item.total }} 次</span></div></div>
                </section>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div
        v-if="clientDetails"
        class="fixed inset-0 z-50 overflow-y-auto bg-slate-900/50 p-4"
        @click.self="clientDetails = null"
      >
        <div class="flex min-h-full items-center justify-center">
          <div class="w-full max-w-2xl rounded-2xl bg-white shadow-2xl">
            <div class="flex items-start justify-between border-b border-slate-100 px-6 py-5">
              <div>
                <h2 class="text-xl font-black text-slate-800">权限详情</h2>
                <p class="mt-1 text-sm text-slate-500">{{ clientDetails.client_name }} · {{ clientDetails.client_id }}</p>
              </div>
              <button type="button" class="text-2xl text-slate-400 hover:text-slate-600" aria-label="关闭权限详情" @click="clientDetails = null">×</button>
            </div>
            <div class="space-y-4 px-6 py-5 text-sm">
              <div class="rounded-xl border border-slate-200 bg-slate-50 p-4">
                <div class="text-xs font-bold text-slate-400">授权方式</div>
                <div class="mt-1 font-bold text-slate-700">用户授权（Authorization Code + PKCE）</div>
                <div class="mt-1 text-xs text-slate-500">所有 Access Token 都必须绑定完成 NanZi 登录授权的用户。</div>
              </div>
              <div class="rounded-xl border border-slate-200 p-4">
                <div class="text-xs font-bold text-slate-400">允许 Scope（{{ clientDetails.allowed_scopes.length }} 项）</div>
                <div class="mt-2 flex flex-wrap gap-2">
                  <code v-for="scope in clientDetails.allowed_scopes" :key="scope" class="rounded-lg bg-indigo-50 px-2.5 py-1.5 text-xs font-bold text-indigo-700">{{ scope }}</code>
                  <span v-if="!clientDetails.allowed_scopes.length" class="text-xs text-slate-500">未配置</span>
                </div>
              </div>
              <div class="rounded-xl border border-blue-100 bg-blue-50 p-4">
                <div class="text-xs font-bold text-blue-700">资源权限</div>
                <p class="mt-2 text-sm leading-6 text-blue-950">智能体、知识库、元数据集等资源，按当前登录用户权限与 Client 白名单的交集判断。</p>
                <p class="mt-2 text-xs leading-5 text-blue-800">未配置白名单时跟随用户权限；配置空白名单时，该 Client 对此类资源没有访问权限。</p>
              </div>
            </div>
            <div class="flex justify-end border-t border-slate-100 px-6 py-4">
              <button type="button" class="rounded-xl bg-indigo-600 px-5 py-2 font-bold text-white hover:bg-indigo-700" @click="clientDetails = null">知道了</button>
            </div>
          </div>
        </div>
      </div>

      <div
        v-if="showCreate"
        class="fixed inset-0 z-50 overflow-y-auto bg-slate-900/50 p-4"
        @click.self="showCreate = false"
      >
        <div class="flex min-h-full items-center justify-center">
          <div class="flex max-h-[calc(100vh-2rem)] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
            <div class="flex shrink-0 items-center justify-between border-b border-slate-100 px-6 py-5">
              <div>
                <h2 class="text-xl font-black">创建 Confidential Client</h2>
                <p class="mt-1 text-xs font-normal text-slate-500">先配置外部系统允许使用的授权方式和能力范围。</p>
              </div>
              <button type="button" class="text-2xl text-slate-400 hover:text-slate-600" aria-label="关闭创建 Client 弹框" @click="showCreate = false">×</button>
            </div>

            <div class="min-h-0 flex-1 overflow-y-auto px-6 py-5">
              <div class="space-y-4">
                <label class="block text-sm font-bold">接入名称<input v-model="form.client_name" class="mt-2 w-full rounded-xl border border-slate-200 p-3" placeholder="例如 CRM 系统" /></label>
                <div>
                  <div class="text-sm font-bold">允许授权模式</div>
                  <div class="mt-2 flex items-start gap-2 rounded-xl border border-indigo-100 bg-indigo-50 p-3 text-sm"><span class="mt-0.5 text-indigo-600">✓</span><span><span class="block font-bold text-indigo-950">用户授权（Authorization Code + PKCE）</span><span class="mt-1 block text-xs font-normal text-indigo-800">唯一授权方式；Access Token 始终绑定完成 NanZi 登录授权的用户。</span></span></div>
                </div>
                <label class="block text-sm font-bold">Redirect URI（每行一个）<span class="ml-1.5 rounded-full bg-slate-100 px-1.5 py-0.5 text-xs font-normal text-slate-500">选填</span><textarea v-model="form.redirect_uris" class="mt-2 min-h-24 w-full rounded-xl border border-slate-200 p-3 font-normal" placeholder="https://crm.example.com/oauth/callback" /><span class="mt-1 block text-xs font-normal text-slate-500">未填写时使用默认回调地址 https://localhost/oauth/callback；人工手动生成 Token 可留空。程序 OAuth 使用真实业务回调时，请填写并保持地址完全一致。</span></label>
                <label class="flex cursor-pointer items-center gap-3 rounded-xl border border-slate-200 bg-slate-50 p-3.5 text-sm font-bold text-slate-700">
                  <input v-model="form.is_shared" type="checkbox" class="h-4 w-4 rounded text-indigo-600 focus:ring-indigo-500" />
                  <div>
                    <span>全员共享 Client</span>
                    <span class="mt-0.5 block text-xs font-normal text-slate-500">勾选后，平台其他用户在外部 Client 列表中可见，并能为该 Client 签发个人 Token。</span>
                  </div>
                </label>
                <div>
                  <div class="flex items-center justify-between gap-3">
                    <div>
                      <div class="text-sm font-bold">允许 Scope</div>
                      <p class="mt-1 text-xs font-normal text-slate-500">勾选后，外部系统只能申请这些能力。</p>
                    </div>
                    <span class="shrink-0 rounded-full bg-indigo-50 px-2.5 py-1 text-xs font-bold text-indigo-700">{{ form.allowed_scopes.length }} 项</span>
                  </div>
                  <div class="mt-2 max-h-56 overflow-y-auto rounded-xl border border-slate-200 bg-slate-50 p-3">
                    <div class="grid gap-2 sm:grid-cols-2">
                      <label v-for="item in scopeOptions" :key="item[0]" class="flex items-start gap-2 rounded-lg bg-white p-3 text-xs font-normal shadow-sm"><input v-model="form.allowed_scopes" type="checkbox" :value="item[0]" class="mt-1" /><span><span class="block font-bold text-slate-700">{{ item[1] }}</span><code class="mt-1 block text-[11px] text-slate-400">{{ item[0] }}</code></span></label>
                    </div>
                  </div>
                </div>
                <div class="rounded-xl border border-blue-100 bg-blue-50 p-4 text-xs leading-5 text-blue-900">创建 Client 后，可在 Client 卡片的资源访问区域配置白名单；实际调用仍会按 Access Token 代表的当前用户权限取交集。</div>
              </div>
            </div>

            <div class="flex shrink-0 justify-end gap-3 border-t border-slate-100 bg-white px-6 py-4">
              <button type="button" class="rounded-xl px-4 py-2 font-bold text-slate-500 hover:bg-slate-50" @click="showCreate = false">取消</button>
              <button type="button" class="rounded-xl bg-indigo-600 px-5 py-2 font-bold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50" :disabled="saving || !form.client_name || !form.allowed_grant_types.length || !form.allowed_scopes.length" @click="createClient">创建并显示 Secret</button>
            </div>
          </div>
        </div>
      </div>

      <div
        v-if="showClientScopeEdit && clientScopeEditTarget"
        class="fixed inset-0 z-50 overflow-y-auto bg-slate-900/50 p-4"
        @click.self="closeClientScopeEdit"
      >
        <div class="flex min-h-full items-center justify-center">
          <div class="flex max-h-[calc(100vh-2rem)] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
            <div class="flex shrink-0 items-center justify-between border-b border-slate-100 px-6 py-5">
              <div>
                <h2 class="text-xl font-black">编辑 Client Scope</h2>
                <p class="mt-1 text-xs font-normal text-slate-500">调整这个 Client 可以申请的 MCP 方法范围。</p>
              </div>
              <button type="button" class="text-2xl text-slate-400 hover:text-slate-600" aria-label="关闭编辑 Scope 弹框" :disabled="saving" @click="closeClientScopeEdit">×</button>
            </div>

            <div class="min-h-0 flex-1 overflow-y-auto px-6 py-5">
              <div class="space-y-4">
                <div class="rounded-xl bg-slate-50 p-4">
                  <div class="text-sm font-black text-slate-800">{{ clientScopeEditTarget.client_name }}</div>
                  <code class="mt-1 block break-all text-xs text-slate-500">{{ clientScopeEditTarget.client_id }}</code>
                </div>
                <div>
                  <div class="flex items-center justify-between gap-3">
                    <div>
                      <div class="text-sm font-bold">允许 Scope</div>
                      <p class="mt-1 text-xs text-slate-500">外部系统只能在这里勾选的 Scope 中申请用户 Token。</p>
                    </div>
                    <span class="shrink-0 rounded-full bg-indigo-50 px-2.5 py-1 text-xs font-bold text-indigo-700">{{ clientScopeEditForm.scopes.length }} 项</span>
                  </div>
                  <div class="mt-2 max-h-64 overflow-y-auto rounded-xl border border-slate-200 bg-slate-50 p-3">
                    <div class="grid gap-2 sm:grid-cols-2">
                      <label v-for="item in scopeOptions" :key="item[0]" class="flex items-start gap-2 rounded-lg bg-white p-3 text-xs font-normal shadow-sm">
                        <input v-model="clientScopeEditForm.scopes" type="checkbox" :value="item[0]" class="mt-1" />
                        <span><span class="block font-bold text-slate-700">{{ item[1] }}</span><code class="mt-1 block text-[11px] text-slate-400">{{ item[0] }}</code></span>
                      </label>
                    </div>
                  </div>
                </div>
                <div class="rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs leading-5 text-amber-900">
                  <div class="font-bold">保存后会发生什么？</div>
                  <p class="mt-1">Scope 变更会让该 Client 已有的 Access Token 和授权关系失效。保存后，外部系统需要重新完成用户授权并获取 Token。</p>
                </div>
              </div>
            </div>

            <div class="flex shrink-0 justify-end gap-3 border-t border-slate-100 bg-white px-6 py-4">
              <button type="button" class="rounded-xl px-4 py-2 font-bold text-slate-500 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50" :disabled="saving" @click="closeClientScopeEdit">取消</button>
              <button type="button" class="rounded-xl bg-indigo-600 px-5 py-2 font-bold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50" :disabled="saving || !clientScopeEditForm.scopes.length" @click="saveClientScopes">{{ saving ? '保存中…' : '保存 Scope' }}</button>
            </div>
          </div>
        </div>
      </div>

      <div
        v-if="showResourceWhitelistModal && resourceWhitelistModal.target"
        class="fixed inset-0 z-50 overflow-y-auto bg-slate-900/50 p-4"
        @click.self="closeResourceWhitelistModal()"
      >
        <div class="flex min-h-full items-center justify-center">
          <div class="flex max-h-[calc(100vh-2rem)] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
            <div class="flex shrink-0 items-center justify-between border-b border-slate-100 px-6 py-5">
              <div>
                <h2 class="text-xl font-black">编辑{{ resourceWhitelistModal.title }}</h2>
                <p class="mt-1 text-xs font-normal text-slate-500">{{ resourceWhitelistModal.target.client_name }} · 当前用户可访问资源候选</p>
              </div>
              <button type="button" class="text-2xl text-slate-400 hover:text-slate-600" aria-label="关闭资源白名单弹框" :disabled="saving" @click="() => closeResourceWhitelistModal()">×</button>
            </div>

            <div class="min-h-0 flex-1 overflow-y-auto px-6 py-5">
              <div class="space-y-4">
                <div class="grid gap-2 sm:grid-cols-2">
                  <label class="flex cursor-pointer items-start gap-3 rounded-xl border border-indigo-100 bg-indigo-50 p-4 text-sm">
                    <input :checked="resourceWhitelistModal.unrestricted" type="radio" name="resource-whitelist-mode" class="mt-0.5 h-4 w-4 text-indigo-600 focus:ring-indigo-500" @change="resourceWhitelistModal.unrestricted = true" />
                    <span><span class="block font-bold text-indigo-950">跟随用户权限（推荐）</span><span class="mt-1 block text-xs font-normal text-indigo-800">不额外限制 Client；每个用户仍只能访问自己的授权资源。</span></span>
                  </label>
                  <label class="flex cursor-pointer items-start gap-3 rounded-xl border border-slate-200 bg-white p-4 text-sm">
                    <input :checked="!resourceWhitelistModal.unrestricted" type="radio" name="resource-whitelist-mode" class="mt-0.5 h-4 w-4 text-indigo-600 focus:ring-indigo-500" @change="resourceWhitelistModal.unrestricted = false" />
                    <span><span class="block font-bold text-slate-800">仅允许指定资源</span><span class="mt-1 block text-xs font-normal text-slate-500">只允许下方勾选的资源；一个都不选则全部禁止。</span></span>
                  </label>
                </div>
                <div class="flex flex-wrap items-center gap-2">
                  <input v-model="resourceWhitelistModal.search" class="min-w-0 flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm" placeholder="搜索资源名称或 ID" @keyup.enter="loadResourceOptions()" />
                  <button type="button" class="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-600 hover:bg-slate-50" @click="loadResourceOptions()">查询</button>
                  <button type="button" class="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs font-bold text-indigo-700 hover:bg-indigo-100 disabled:opacity-50" :disabled="resourceWhitelistModal.unrestricted" @click="selectAllCurrentResourceOptions">勾选当前搜索结果</button>
                  <button type="button" class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-bold text-amber-700 hover:bg-amber-100" @click="restoreAllAccessibleResources">取消限制，跟随用户权限</button>
                </div>
                <div class="flex items-center justify-between text-xs text-slate-500">
                  <span>{{ resourceWhitelistModal.unrestricted ? '当前设置：跟随用户权限' : (resourceWhitelistModal.selectedIds.length ? `已选择 ${resourceWhitelistModal.selectedIds.length} 项资源` : '当前设置：禁止访问全部资源') }}</span>
                  <span>候选 {{ resourceWhitelistModal.total }} 项</span>
                </div>
                <div class="max-h-80 overflow-y-auto rounded-xl border border-slate-200 bg-slate-50 p-3">
                  <div v-if="resourceWhitelistModal.loading && !resourceWhitelistModal.options.length" class="py-8 text-center text-xs text-slate-400">加载中…</div>
                  <div v-else-if="!resourceWhitelistModal.options.length" class="py-8 text-center text-xs text-slate-400">当前用户暂无可选资源</div>
                  <label v-for="item in resourceWhitelistModal.options" v-else :key="item.id" class="flex items-start gap-3 rounded-lg bg-white p-3 text-xs shadow-sm" :class="resourceWhitelistModal.unrestricted ? 'opacity-60' : ''">
                    <input v-model="resourceWhitelistModal.selectedIds" type="checkbox" :value="item.id" :disabled="resourceWhitelistModal.unrestricted" class="mt-0.5 h-4 w-4 rounded text-indigo-600 focus:ring-indigo-500" />
                    <span class="min-w-0"><span class="block font-bold text-slate-700">{{ item.name }}</span><code class="mt-1 block break-all text-[11px] text-slate-400">{{ item.id }}</code><span v-if="item.description" class="mt-1 block text-slate-500">{{ item.description }}</span></span>
                  </label>
                  <button v-if="resourceWhitelistModal.hasMore" type="button" class="mt-3 w-full rounded-lg border border-slate-200 bg-white py-2 text-xs font-bold text-indigo-700 hover:bg-indigo-50 disabled:opacity-50" :disabled="resourceWhitelistModal.loading" @click="loadMoreResourceOptions">{{ resourceWhitelistModal.loading ? '加载中…' : '加载更多' }}</button>
                </div>
              </div>
            </div>

            <div class="flex shrink-0 justify-end gap-3 border-t border-slate-100 bg-white px-6 py-4">
              <button type="button" class="rounded-xl px-4 py-2 font-bold text-slate-500 hover:bg-slate-50 disabled:opacity-50" :disabled="saving" @click="() => closeResourceWhitelistModal()">取消</button>
              <button type="button" class="rounded-xl bg-indigo-600 px-5 py-2 font-bold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50" :disabled="saving" @click="saveResourceWhitelist">{{ saving ? '保存中…' : '保存白名单' }}</button>
            </div>
          </div>
        </div>
      </div>

      <div
        v-if="resourceWhitelistConfirm.visible"
        class="fixed inset-0 z-[60] flex items-center justify-center bg-slate-900/50 p-4"
        role="dialog"
        aria-modal="true"
        aria-labelledby="resource-whitelist-confirm-title"
      >
        <div class="w-full max-w-md rounded-2xl bg-white shadow-2xl">
          <div class="flex items-start justify-between border-b border-slate-100 px-6 py-5">
            <div>
              <h2 id="resource-whitelist-confirm-title" class="text-lg font-black text-slate-800">确认禁止全部资源</h2>
              <p class="mt-1 text-sm text-slate-500">正在设置：{{ resourceWhitelistConfirm.resourceLabel }}</p>
            </div>
            <button type="button" class="text-2xl text-slate-400 hover:text-slate-600" aria-label="关闭确认弹框" @click="cancelResourceWhitelistConfirm">×</button>
          </div>
          <div class="px-6 py-5">
            <div class="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm leading-6 text-rose-900">
              确定禁止该 Client 访问全部{{ resourceWhitelistConfirm.resourceLabel }}吗？
              <div class="mt-1 text-xs text-rose-700">保存后，该 Client 将无法访问此类资源；如需恢复，可选择“跟随用户权限”。</div>
            </div>
          </div>
          <div class="flex justify-end gap-3 border-t border-slate-100 px-6 py-4">
            <button type="button" class="rounded-xl px-4 py-2 font-bold text-slate-500 hover:bg-slate-50" @click="cancelResourceWhitelistConfirm">取消</button>
            <button type="button" class="rounded-xl bg-rose-600 px-5 py-2 font-bold text-white hover:bg-rose-700 disabled:cursor-not-allowed disabled:opacity-50" :disabled="saving" @click="confirmResourceWhitelistSave">{{ saving ? '保存中…' : '确认禁止' }}</button>
          </div>
        </div>
      </div>

      <div
        v-if="showTokenIssue"
        class="fixed inset-0 z-50 overflow-y-auto bg-slate-900/50 p-4"
        @click.self="closeTokenWizard"
      >
        <div class="flex min-h-full items-center justify-center">
          <div class="flex max-h-[calc(100vh-2rem)] w-full max-w-xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
            <div class="flex shrink-0 items-center justify-between border-b border-slate-100 px-6 py-5">
              <div>
                <h2 class="text-xl font-black">{{ tokenWizardStep === 1 ? '生成 MCP Access Token' : 'Token 已生成，复制配置' }}</h2>
                <p class="mt-1 text-xs font-normal text-slate-500">{{ tokenWizardStep === 1 ? '生成后只能代表当前登录用户，不支持选择或代发其他用户身份。' : '可以单独复制 Access Token，也可以复制完整 MCP JSON 直接粘贴到客户端。' }}</p>
              </div>
              <button type="button" class="text-2xl text-slate-400 hover:text-slate-600" aria-label="关闭生成用户 Token 弹框" @click="closeTokenWizard">×</button>
            </div>
            <div class="min-h-0 flex-1 overflow-y-auto px-6 py-5">
              <div class="mb-5 flex items-center gap-2 text-xs font-bold">
                <span class="rounded-full px-3 py-1.5" :class="tokenWizardStep === 1 ? 'bg-indigo-600 text-white' : 'bg-emerald-100 text-emerald-700'">1. 配置并生成</span>
                <span class="h-px w-8 bg-slate-200" />
                <span class="rounded-full px-3 py-1.5" :class="tokenWizardStep === 2 ? 'bg-indigo-600 text-white' : 'bg-slate-100 text-slate-400'">2. 复制并使用</span>
              </div>

              <div v-if="tokenWizardStep === 1" class="space-y-4">
                <div class="rounded-xl border border-blue-100 bg-blue-50 p-4 text-sm leading-6 text-blue-900">
                  <div class="font-bold">当前登录用户身份</div>
                  <p class="mt-1">后端从当前登录会话读取 user_id。管理员登录生成管理员 Token，demo 用户登录生成 demo 用户 Token。</p>
                </div>
                <div class="rounded-xl bg-slate-50 p-4 text-sm">
                  <div class="font-bold">绑定 Client</div>
                  <div class="mt-1 text-slate-600">{{ tokenClient?.client_name }}（{{ tokenClient?.client_id }}）</div>
                </div>
                <label class="block text-sm font-bold">有效期
                  <select v-model.number="tokenForm.expires_in" class="mt-2 w-full rounded-xl border border-slate-200 bg-white p-3">
                    <option v-for="item in tokenExpiryOptions" :key="item[0]" :value="item[0]">{{ item[1] }}</option>
                  </select>
                  <span class="mt-1 block text-xs font-normal text-slate-500">Token 到期后需要重新登录平台并生成；最长 30 天。</span>
                </label>
                <div>
                  <div class="flex items-center justify-between gap-3">
                    <div>
                      <div class="text-sm font-bold">本次允许的 Scope</div>
                      <p class="mt-1 text-xs font-normal text-slate-500">只能从当前 Client 已配置的 Scope 中选择。</p>
                    </div>
                    <span class="shrink-0 rounded-full bg-indigo-50 px-2.5 py-1 text-xs font-bold text-indigo-700">{{ tokenForm.scopes.length }} 项</span>
                  </div>
                  <div class="mt-2 max-h-48 overflow-y-auto rounded-xl border border-slate-200 bg-slate-50 p-3">
                    <label v-for="scope in (tokenClient?.allowed_scopes || [])" :key="scope" class="flex items-start gap-2 rounded-lg bg-white p-3 text-xs shadow-sm">
                      <input v-model="tokenForm.scopes" type="checkbox" :value="scope" class="mt-1" />
                      <span><span class="block font-bold text-slate-700">{{ scopeLabel(scope) }}</span><code class="mt-1 block text-[11px] text-slate-400">{{ scope }}</code></span>
                    </label>
                    <div v-if="!(tokenClient?.allowed_scopes || []).length" class="p-3 text-xs text-slate-500">当前 Client 没有配置可用 Scope，不能生成 Token。</div>
                  </div>
                </div>
              </div>

              <div v-else class="space-y-4">
                <div class="rounded-xl border border-emerald-100 bg-emerald-50 p-4 text-sm leading-6 text-emerald-950">
                  <div class="font-bold">已生成当前用户 Token</div>
                  <p class="mt-1">代表用户：{{ accessTokenInfo.user_name || '当前登录用户' }}（user_id={{ accessTokenInfo.user_id }}）；有效期 {{ accessTokenInfo.expires_in }} 秒。</p>
                </div>
                <div class="rounded-xl border border-slate-200 p-4">
                  <div class="flex flex-wrap items-center justify-between gap-2">
                    <div class="font-bold">Access Token</div>
                    <button type="button" class="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-indigo-700" @click="copyValue('access-token', oneTimeAccessToken)">{{ copied === 'access-token' ? '已复制' : '复制 Access Token' }}</button>
                  </div>
                  <code class="mt-3 block max-h-24 overflow-y-auto break-all rounded-lg bg-slate-950 p-3 text-xs leading-5 text-slate-100">{{ oneTimeAccessToken }}</code>
                  <p class="mt-2 text-xs leading-5 text-slate-500">单独调用时使用：<code>Authorization: Bearer &lt;access_token&gt;</code></p>
                </div>
                <div class="rounded-xl border border-indigo-100 bg-indigo-50/50 p-4">
                  <div class="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <div class="font-bold">MCP JSON 配置</div>
                      <p class="mt-1 text-xs text-slate-500">已写入当前 Endpoint 和刚生成的 Token，可直接复制粘贴。</p>
                    </div>
                    <button type="button" class="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-indigo-700" @click="copyGeneratedMcpJson">{{ copied === 'generated-mcp-json' ? '已复制' : '复制 MCP JSON' }}</button>
                  </div>
                  <pre class="mt-3 max-h-56 overflow-auto rounded-lg bg-slate-950 p-3 text-xs leading-5 text-slate-100"><code>{{ generatedMcpJson }}</code></pre>
                </div>
                <div class="rounded-xl border border-amber-100 bg-amber-50 p-3 text-xs leading-5 text-amber-900">安全提示：上面的 JSON 包含完整 Access Token，只粘贴到可信客户端，不要提交到代码仓库或发送给其他人。</div>
              </div>
            </div>
            <div class="flex shrink-0 justify-end gap-3 border-t border-slate-100 bg-white px-6 py-4">
              <button v-if="tokenWizardStep === 1" type="button" class="rounded-xl px-4 py-2 font-bold text-slate-500 hover:bg-slate-50" @click="closeTokenWizard">取消</button>
              <button v-else type="button" class="rounded-xl px-4 py-2 font-bold text-slate-500 hover:bg-slate-50" @click="tokenWizardStep = 1">上一步</button>
              <button v-if="tokenWizardStep === 1" type="button" class="rounded-xl bg-indigo-600 px-5 py-2 font-bold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50" :disabled="saving || !tokenForm.scopes.length" @click="issueCurrentUserToken">生成并进入下一步</button>
              <button v-else type="button" class="rounded-xl bg-indigo-600 px-5 py-2 font-bold text-white hover:bg-indigo-700" @click="closeTokenWizard">完成</button>
            </div>
          </div>
        </div>
      </div>

      <div v-if="showTokenDetails && tokenDetailsClient" class="fixed inset-0 z-50 overflow-y-auto bg-slate-900/50 p-4" @click.self="showTokenDetails = false">
        <div class="flex min-h-full items-center justify-center">
          <div class="flex max-h-[calc(100vh-2rem)] w-full max-w-4xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
            <div class="flex shrink-0 items-center justify-between border-b border-slate-100 px-6 py-5">
              <div>
                <div class="flex items-center gap-3">
                  <h2 class="text-xl font-black">Token 生命周期</h2>
                  <button
                    v-if="canRevokeAllClientTokens(tokenDetailsClient) && clientTokens.some(t => getTokenStatus(t) === 'active')"
                    type="button"
                    class="rounded-lg border border-rose-200 bg-rose-50 px-2.5 py-1 text-xs font-bold text-rose-700 hover:bg-rose-100"
                    @click="revokeAllClientTokens(tokenDetailsClient)"
                  >
                    一键撤销全部 Token
                  </button>
                </div>
                <p class="mt-1 text-xs text-slate-500">{{ tokenDetailsClient.client_name }} · 仅展示脱敏元数据，不展示 Token 原文。</p>
              </div>
              <button type="button" class="text-2xl text-slate-400" aria-label="关闭 Token 管理" @click="showTokenDetails = false">×</button>
            </div>
            <div class="min-h-0 flex-1 overflow-y-auto px-6 py-5">
              <div class="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-100 bg-slate-50 p-3">
                <div class="flex flex-wrap gap-1.5 text-[11px] font-bold">
                  <button
                    type="button"
                    class="cursor-pointer rounded-full px-2.5 py-1 transition-all"
                    :class="tokenStatusFilter === 'all' ? 'bg-slate-700 text-white shadow-xs ring-2 ring-slate-700/20' : 'bg-white text-slate-600 border border-slate-200/60 hover:bg-slate-100 hover:text-slate-800'"
                    @click="tokenStatusFilter = 'all'"
                  >
                    全部 {{ tokenStatusCounts.all }}
                  </button>
                  <button
                    type="button"
                    class="cursor-pointer rounded-full px-2.5 py-1 transition-all"
                    :class="tokenStatusFilter === 'active' ? 'bg-emerald-600 text-white shadow-xs ring-2 ring-emerald-600/20' : 'bg-emerald-50 text-emerald-700 border border-emerald-200/60 hover:bg-emerald-100'"
                    @click="tokenStatusFilter = 'active'"
                  >
                    有效 {{ tokenStatusCounts.active }}
                  </button>
                  <button
                    type="button"
                    class="cursor-pointer rounded-full px-2.5 py-1 transition-all"
                    :class="tokenStatusFilter === 'expiring' ? 'bg-amber-500 text-white shadow-xs ring-2 ring-amber-500/20' : 'bg-amber-50 text-amber-700 border border-amber-200/60 hover:bg-amber-100'"
                    @click="tokenStatusFilter = 'expiring'"
                  >
                    24 小时内到期 {{ tokenStatusCounts.expiring }}
                  </button>
                  <button
                    type="button"
                    class="cursor-pointer rounded-full px-2.5 py-1 transition-all"
                    :class="tokenStatusFilter === 'expired' ? 'bg-orange-500 text-white shadow-xs ring-2 ring-orange-500/20' : 'bg-orange-50 text-orange-700 border border-orange-200/60 hover:bg-orange-100'"
                    @click="tokenStatusFilter = 'expired'"
                  >
                    已过期 {{ tokenStatusCounts.expired }}
                  </button>
                  <button
                    type="button"
                    class="cursor-pointer rounded-full px-2.5 py-1 transition-all"
                    :class="tokenStatusFilter === 'revoked' ? 'bg-slate-600 text-white shadow-xs ring-2 ring-slate-600/20' : 'bg-slate-200 text-slate-600 border border-slate-300/60 hover:bg-slate-300'"
                    @click="tokenStatusFilter = 'revoked'"
                  >
                    已撤销 {{ tokenStatusCounts.revoked }}
                  </button>
                </div>
                <div class="flex flex-wrap items-center gap-2">
                  <label class="text-xs font-bold text-slate-500">筛选状态</label>
                  <select v-model="tokenStatusFilter" class="rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs font-bold text-slate-600">
                    <option value="all">全部</option>
                    <option value="active">有效</option>
                    <option value="expiring">24 小时内到期</option>
                    <option value="expired">已过期</option>
                    <option value="revoked">已撤销</option>
                  </select>
                  <button
                    v-if="selectedDeletableTokens.length"
                    type="button"
                    class="rounded-lg bg-rose-600 px-3 py-2 text-xs font-bold text-white hover:bg-rose-700 disabled:cursor-not-allowed disabled:opacity-50"
                    :disabled="tokenDeleteLoading"
                    @click="deleteSelectedClientTokens"
                  >
                    {{ tokenDeleteLoading ? '删除中…' : `删除已选 Token (${selectedDeletableTokens.length})` }}
                  </button>
                </div>
              </div>
              <div v-if="tokenDetailsLoading" class="py-10 text-center text-sm text-slate-500">Token 记录加载中…</div>
              <div v-else-if="!filteredClientTokens.length" class="rounded-xl bg-slate-50 p-8 text-center text-sm text-slate-500">当前筛选下暂无 Token 记录</div>
              <div v-else class="overflow-x-auto rounded-xl border border-slate-200">
                <table class="min-w-[760px] w-full text-left text-xs">
                  <thead class="bg-slate-50 text-slate-500">
                    <tr>
                      <th class="w-10 p-3"><input type="checkbox" :checked="allVisibleTokensSelected" :disabled="!deletableVisibleTokens.length || tokenDeleteLoading" aria-label="全选可删除 Token" @change="toggleAllVisibleTokens" /></th>
                      <th class="p-3">Scope 范围</th>
                      <th class="p-3">生成方式</th>
                      <th class="p-3">时间信息</th>
                      <th class="p-3">状态</th>
                      <th class="p-3">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="token in filteredClientTokens" :key="token.id" class="border-t border-slate-100">
                      <td class="p-3"><input type="checkbox" :checked="selectedTokenIds.includes(token.id)" :disabled="!canDeleteClientToken(token) || tokenDeleteLoading" :aria-label="`选择 Token ${token.id}`" @change="toggleTokenSelection(token)" /></td>
                      <td class="p-3">
                        <div class="flex max-w-[200px] flex-wrap gap-1">
                          <span v-for="sc in (token.scopes || [])" :key="sc" class="rounded bg-indigo-50 px-1.5 py-0.5 font-mono text-[10px] text-indigo-700">{{ sc }}</span>
                          <span v-if="!(token.scopes || []).length" class="text-slate-400">—</span>
                        </div>
                      </td>
                      <td class="p-3">{{ token.issue_method === 'oauth_authorization' ? 'OAuth 用户授权' : '服务台手动生成' }}</td>
                      <td class="whitespace-nowrap p-3 text-slate-500">
                        <div>生成：{{ formatAuditTime(token.issued_at) }}</div>
                        <div>过期：{{ formatAuditTime(token.expires_at) }}</div>
                        <div :class="tokenRemainingLabel(token) === '已过期' ? 'font-bold text-rose-600' : 'text-slate-500'">{{ tokenRemainingLabel(token) }}</div>
                      </td>
                      <td class="p-3">
                        <span :class="getTokenStatus(token) === 'active' ? 'font-bold text-emerald-600' : (getTokenStatus(token) === 'expired' ? 'text-amber-600' : 'text-slate-400')">
                          {{ getTokenStatus(token) === 'active' ? '有效' : getTokenStatus(token) === 'expired' ? '已过期' : '已撤销' }}
                        </span>
                      </td>
                      <td class="p-3">
                        <div class="flex flex-wrap gap-2">
                          <button v-if="getTokenStatus(token) === 'active' && canDeleteClientToken(token)" type="button" class="font-bold text-amber-700 hover:text-amber-900 disabled:opacity-50" :disabled="tokenDeleteLoading" @click="revokeClientToken(token)">撤销</button>
                          <button v-if="canDeleteClientToken(token)" type="button" class="font-bold text-rose-700 hover:text-rose-900 disabled:opacity-50" :disabled="tokenDeleteLoading" @click="deleteClientToken(token)">物理删除</button>
                          <span v-if="getTokenStatus(token) !== 'active' && !canDeleteClientToken(token)" class="text-slate-400">—</span>
                        </div>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
            <div class="flex shrink-0 justify-end border-t border-slate-100 px-6 py-4"><button type="button" class="rounded-xl bg-indigo-600 px-5 py-2 font-bold text-white" @click="showTokenDetails = false">关闭</button></div>
          </div>
        </div>
      </div>

      <!-- Client 基本信息编辑弹窗 -->
      <div
        v-if="showClientEdit && clientEditTarget"
        class="fixed inset-0 z-50 overflow-y-auto bg-slate-900/50 p-4"
        @click.self="() => closeClientEdit()"
      >
        <div class="flex min-h-full items-center justify-center">
          <div class="flex max-h-[calc(100vh-2rem)] w-full max-w-lg flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
            <div class="flex shrink-0 items-center justify-between border-b border-slate-100 px-6 py-5">
              <div>
                <h2 class="text-xl font-black text-slate-800">编辑 Client 基本信息</h2>
                <p class="mt-1 text-xs text-slate-500">{{ clientEditTarget.client_name }} · {{ clientEditTarget.client_id }}</p>
              </div>
              <button type="button" class="text-2xl text-slate-400 hover:text-slate-600" aria-label="关闭编辑" :disabled="saving" @click="() => closeClientEdit()">×</button>
            </div>
            <div class="min-h-0 flex-1 overflow-y-auto px-6 py-5">
              <div class="space-y-4">
                <label class="block text-sm font-bold text-slate-700">
                  Client 名称
                  <input v-model="clientEditForm.client_name" class="mt-2 w-full rounded-xl border border-slate-200 p-3 text-sm font-normal" placeholder="例如 CRM 生产系统" />
                </label>
                <label class="block text-sm font-bold text-slate-700">
                  Redirect URIs（每行一个）
                  <textarea v-model="clientEditForm.redirect_uris" class="mt-2 min-h-24 w-full rounded-xl border border-slate-200 p-3 font-mono text-xs font-normal" placeholder="https://crm.example.com/oauth/callback" />
                  <span class="mt-1 block text-xs font-normal text-slate-500">外部系统 OAuth 回调地址，需保持精确匹配。</span>
                </label>
                <label class="flex cursor-pointer items-center gap-3 rounded-xl border border-slate-200 bg-slate-50 p-3.5 text-sm font-bold text-slate-700">
                  <input v-model="clientEditForm.is_shared" type="checkbox" class="h-4 w-4 rounded text-indigo-600 focus:ring-indigo-500" />
                  <div>
                    <span>全员共享 Client</span>
                    <span class="mt-0.5 block text-xs font-normal text-slate-500">勾选后，平台其他普通用户也能复用该 Client 并生成个人 Token。</span>
                  </div>
                </label>
              </div>
            </div>
            <div class="flex shrink-0 justify-end gap-3 border-t border-slate-100 bg-white px-6 py-4">
              <button type="button" class="rounded-xl px-4 py-2 font-bold text-slate-500 hover:bg-slate-50" :disabled="saving" @click="() => closeClientEdit()">取消</button>
              <button type="button" class="rounded-xl bg-indigo-600 px-5 py-2 font-bold text-white hover:bg-indigo-700 disabled:opacity-50" :disabled="saving || !clientEditForm.client_name.trim()" @click="saveClientEdit">{{ saving ? '保存中…' : '保存修改' }}</button>
            </div>
          </div>
        </div>
      </div>

      <!-- 应用授权管理 (Grants) 弹窗 -->
      <div
        v-if="showGrants"
        class="fixed inset-0 z-50 overflow-y-auto bg-slate-900/50 p-4"
        @click.self="showGrants = false"
      >
        <div class="flex min-h-full items-center justify-center">
          <div class="flex max-h-[calc(100vh-2rem)] w-full max-w-4xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
            <div class="flex shrink-0 items-center justify-between border-b border-slate-100 px-6 py-5">
              <div>
                <h2 class="text-xl font-black text-slate-800">已授权的外部应用 (OAuth Grants)</h2>
                <p class="mt-1 text-xs text-slate-500">{{ isAdmin ? '管理员可查看并管理全平台的授权关系' : '展示当前账号已同意授权访问 NanZi 平台的外部系统' }}</p>
              </div>
              <button type="button" class="text-2xl text-slate-400 hover:text-slate-600" aria-label="关闭授权管理" @click="showGrants = false">×</button>
            </div>
            <div class="min-h-0 flex-1 overflow-y-auto px-6 py-5">
              <div v-if="grantsLoading" class="py-10 text-center text-sm text-slate-500">授权记录加载中…</div>
              <div v-else-if="!grants.length" class="rounded-xl bg-slate-50 p-8 text-center text-sm text-slate-500">暂无已授权的应用</div>
              <div v-else class="overflow-x-auto rounded-xl border border-slate-200">
                <table class="min-w-[760px] w-full text-left text-xs">
                  <thead class="bg-slate-50 text-slate-500">
                    <tr>
                      <th class="p-3">应用名称 / Client ID</th>
                      <th v-if="isAdmin" class="p-3">授权用户 ID</th>
                      <th class="p-3">授予 Scope</th>
                      <th class="p-3">授权时间</th>
                      <th class="p-3">最近使用</th>
                      <th class="p-3">状态</th>
                      <th v-if="canRevokeGrants" class="p-3">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="grant in grants" :key="grant.id" class="border-t border-slate-100">
                      <td class="p-3">
                        <div class="font-bold text-slate-700">{{ grant.client_name }}</div>
                        <div class="font-mono text-[11px] text-slate-400">{{ grant.client_id }}</div>
                      </td>
                      <td v-if="isAdmin" class="p-3 font-mono text-slate-600">{{ grant.user_id }}</td>
                      <td class="p-3">
                        <div class="flex max-w-[220px] flex-wrap gap-1">
                          <span v-for="sc in grant.scopes" :key="sc" class="rounded bg-indigo-50 px-1.5 py-0.5 font-mono text-[10px] text-indigo-700">{{ sc }}</span>
                        </div>
                      </td>
                      <td class="whitespace-nowrap p-3 text-slate-500">{{ formatAuditTime(grant.consented_at) }}</td>
                      <td class="whitespace-nowrap p-3 text-slate-500">{{ grant.last_used_at ? formatAuditTime(grant.last_used_at) : '—' }}</td>
                      <td class="p-3">
                        <span :class="grant.status === 'active' ? 'font-bold text-emerald-600' : 'text-slate-400'">
                          {{ grant.status === 'active' ? '生效中' : '已解除' }}
                        </span>
                      </td>
                      <td v-if="canRevokeGrants" class="p-3">
                        <button
                          v-if="grant.status === 'active'"
                          type="button"
                          class="font-bold text-rose-700 hover:text-rose-900"
                          @click="revokeGrant(grant)"
                        >
                          解除授权
                        </button>
                        <span v-else class="text-slate-400">—</span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
            <div class="flex shrink-0 justify-end border-t border-slate-100 px-6 py-4">
              <button type="button" class="rounded-xl bg-indigo-600 px-5 py-2 font-bold text-white hover:bg-indigo-700" @click="showGrants = false">关闭</button>
            </div>
          </div>
        </div>
      </div>

      <!-- MCP 在线调试 Playground 探针弹窗 -->
      <div
        v-if="showPlayground && playgroundMethod"
        class="fixed inset-0 z-50 overflow-y-auto bg-slate-900/50 p-4"
        @click.self="showPlayground = false"
      >
        <div class="flex min-h-full items-center justify-center">
          <div class="flex max-h-[calc(100vh-2rem)] w-full max-w-3xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
            <div class="flex shrink-0 items-center justify-between border-b border-slate-100 px-6 py-5">
              <div>
                <div class="flex items-center gap-2">
                  <h2 class="text-xl font-black text-slate-800">MCP 在线探针调试</h2>
                  <span class="rounded bg-indigo-50 px-2 py-0.5 font-mono text-xs font-bold text-indigo-700">{{ playgroundMethod.name }}</span>
                </div>
                <p class="mt-1 text-xs text-slate-500">发起真实的 JSON-RPC 2.0 探针调用并测试只读方法回显，实时检验鉴权与数据响应。</p>
              </div>
              <button type="button" class="text-2xl text-slate-400 hover:text-slate-600" aria-label="关闭调试探针" @click="showPlayground = false">×</button>
            </div>
            <div class="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 py-5 text-sm">
              <div class="grid gap-3 sm:grid-cols-3">
                <div class="rounded-xl bg-slate-50 p-3">
                  <span class="text-xs font-bold text-slate-400">所需 Scope</span>
                  <div class="mt-1 font-mono text-xs font-bold text-indigo-700">{{ playgroundMethod.scope }}</div>
                </div>
                <div class="rounded-xl bg-slate-50 p-3">
                  <span class="text-xs font-bold text-slate-400">所属能力组</span>
                  <div class="mt-1 text-xs font-bold text-slate-700">{{ playgroundMethod.capability_group }}</div>
                </div>
                <div class="rounded-xl bg-slate-50 p-3">
                  <span class="text-xs font-bold text-slate-400">测试耗时</span>
                  <div class="mt-1 text-xs font-bold text-slate-700">{{ playgroundLatency != null ? `${playgroundLatency} ms` : '—' }}</div>
                </div>
              </div>

              <div>
                <div class="flex items-center justify-between">
                  <label class="block text-xs font-bold text-slate-700">
                    调用 Bearer Token <span class="text-rose-500">*</span>
                  </label>
                  <span class="text-[11px] text-slate-400">选择有效状态的 Token 或手动输入</span>
                </div>

                <!-- 下拉选择有效 Token -->
                <div class="mt-2 flex flex-wrap items-center gap-2">
                  <div class="min-w-[240px] flex-1">
                    <select
                      v-model="playgroundToken"
                      class="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-mono text-slate-700 outline-none transition focus:border-indigo-500 focus:bg-white focus:ring-1 focus:ring-indigo-500"
                    >
                      <option value="">-- 选择有效状态的 Token 或下方手动粘贴 --</option>
                      <option
                        v-for="tok in activeSessionRecentTokens"
                        :key="tok.token"
                        :value="tok.token"
                      >
                        {{ tok.label }} ({{ formatTokenRemaining(tok.expiresAt) }})
                      </option>
                    </select>
                  </div>
                  <button
                    v-if="playgroundToken"
                    type="button"
                    class="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-500 transition hover:bg-slate-50"
                    @click="playgroundToken = ''"
                  >
                    清空
                  </button>
                </div>

                <!-- 手动输入或显示当前选中 -->
                <div class="mt-2">
                  <input
                    v-model="playgroundToken"
                    class="w-full rounded-xl border border-slate-200 bg-white p-2.5 font-mono text-xs outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
                    placeholder="或在此手动粘贴你已生成的 MCP Access Token"
                  />
                </div>

                <!-- 药丸快速标签列表 -->
                <div v-if="activeSessionRecentTokens.length" class="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
                  <span class="text-[11px] text-slate-400">快捷选用最近 Token:</span>
                  <button
                    v-for="tok in activeSessionRecentTokens"
                    :key="tok.token"
                    type="button"
                    class="inline-flex items-center gap-1 rounded-lg border px-2 py-0.5 font-mono text-[11px] transition"
                    :class="playgroundToken === tok.token ? 'border-indigo-500 bg-indigo-50 text-indigo-700 font-bold' : 'border-slate-200 bg-slate-50 text-slate-600 hover:border-indigo-300 hover:bg-indigo-50'"
                    :title="tok.token"
                    @click="playgroundToken = tok.token"
                  >
                    <span>{{ tok.label }}</span>
                    <span class="text-[10px] text-emerald-600">({{ formatTokenRemaining(tok.expiresAt) }})</span>
                  </button>
                </div>
                <div v-else class="mt-2 rounded-xl border border-amber-200/80 bg-amber-50/80 p-3 text-xs text-amber-900">
                  <div class="flex items-center justify-between">
                    <span class="flex items-center gap-1.5 font-bold">
                      <svg class="h-4 w-4 shrink-0 text-amber-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="12" cy="12" r="10" />
                        <line x1="12" y1="8" x2="12" y2="12" />
                        <line x1="12" y1="16" x2="12.01" y2="16" />
                      </svg>
                      本机未暂存 Token 明文
                    </span>
                    <button
                      type="button"
                      class="text-xs font-bold text-indigo-700 underline hover:text-indigo-900"
                      @click="playgroundMethod = null; activeTab = 'clients'"
                    >
                      前往「外部 Client」签发 ➔
                    </button>
                  </div>
                  <div class="mt-1 text-[11px] leading-relaxed text-amber-800">
                    按 OAuth2 安全规范，数据库仅保存 SHA-256 密文哈希，无法逆向还原历史明文。若您持有已生成的有效 Token 可直接粘贴；或者前往「外部 Client」签发一次，生成后本机将自动记忆并在此常驻列出供随时选用。
                  </div>
                </div>
              </div>

              <div>
                <div class="flex items-center justify-between">
                  <label class="text-xs font-bold text-slate-600">请求参数 arguments (JSON)</label>
                  <span class="text-[11px] text-slate-400">JSON-RPC 2.0 tools/call 结构</span>
                </div>
                <textarea
                  v-model="playgroundParams"
                  rows="4"
                  class="mt-1.5 w-full rounded-xl border border-slate-200 bg-slate-900 p-3 font-mono text-xs text-slate-100 outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
                  placeholder="{}"
                />
              </div>

              <div>
                <div class="flex items-center justify-between">
                  <label class="text-xs font-bold text-slate-600">响应结果</label>
                  <span
                    v-if="playgroundStatus"
                    class="rounded-full px-2 py-0.5 text-[11px] font-bold"
                    :class="playgroundStatus === 'success' ? 'bg-emerald-100 text-emerald-700' : 'bg-rose-100 text-rose-700'"
                  >
                    {{ playgroundStatus === 'success' ? '调用成功' : '调用失败' }}
                  </span>
                </div>
                <pre class="mt-1.5 max-h-60 overflow-y-auto rounded-xl border border-slate-200 bg-slate-950 p-3 font-mono text-xs leading-5 text-slate-100"><code>{{ playgroundResponse || '点击下方「发送探针请求」后在此显示响应回显…' }}</code></pre>
              </div>
            </div>
            <div class="flex shrink-0 justify-end gap-3 border-t border-slate-100 bg-white px-6 py-4">
              <button type="button" class="rounded-xl px-4 py-2 font-bold text-slate-500 hover:bg-slate-50" @click="showPlayground = false">关闭</button>
              <button
                type="button"
                class="rounded-xl bg-indigo-600 px-5 py-2 font-bold text-white hover:bg-indigo-700 disabled:opacity-50"
                :disabled="playgroundTesting"
                @click="executePlaygroundTest"
              >
                {{ playgroundTesting ? '发送中…' : '发送探针请求' }}
              </button>
            </div>
          </div>
        </div>
      </div>

      <div
        v-if="showClientConfirm && clientConfirmTarget && clientConfirmAction"
        class="fixed inset-0 z-50 overflow-y-auto bg-slate-900/50 p-4"
        @click.self="closeClientConfirm"
      >
        <div class="flex min-h-full items-center justify-center">
          <div class="w-full max-w-lg rounded-2xl bg-white shadow-2xl">
            <div class="flex items-center justify-between border-b border-slate-100 px-6 py-5">
              <div>
                <h2 class="text-xl font-black">{{ clientConfirmAction === 'reset-secret' ? '确认重置 Client Secret' : (clientConfirmAction === 'delete' ? '确认删除 Client' : '确认停用 Client') }}</h2>
                <p class="mt-1 text-xs font-normal text-slate-500">请确认你了解本次操作对已有凭证的影响。</p>
              </div>
              <button type="button" class="text-2xl text-slate-400 hover:text-slate-600" aria-label="关闭确认弹框" :disabled="saving" @click="closeClientConfirm">×</button>
            </div>
            <div class="space-y-4 px-6 py-5 text-sm">
              <div class="rounded-xl bg-slate-50 p-4">
                <div class="font-bold text-slate-800">{{ clientConfirmTarget.client_name }}</div>
                <code class="mt-1 block break-all text-xs text-slate-500">{{ clientConfirmTarget.client_id }}</code>
              </div>
              <div v-if="clientConfirmAction === 'disable'" class="rounded-xl border border-amber-200 bg-amber-50 p-4 leading-6 text-amber-900">
                <div class="font-bold">停用后会发生什么？</div>
                <p class="mt-1">该 Client 下已有的 Access Token、Refresh Token 会立即失效，正在使用这些凭证的调用会被拒绝。重新启用后，需要重新获取 Token。</p>
              </div>
              <div v-else-if="clientConfirmAction === 'reset-secret'" class="rounded-xl border border-amber-200 bg-amber-50 p-4 leading-6 text-amber-900">
                <div class="font-bold">重置后会发生什么？</div>
                <p class="mt-1">旧 Client Secret 立即失效；该 Client 下已有的 Access Token、Refresh Token 会立即失效。业务方需要保存新 Secret，并重新获取 Access Token。</p>
              </div>
              <div v-else class="rounded-xl border border-rose-200 bg-rose-50 p-4 leading-6 text-rose-900">
                <div class="font-bold">删除后会发生什么？</div>
                <p class="mt-1">该 Client 会被软删除并从默认列表隐藏；已有的 Access Token、Refresh Token 和用户授权关系会立即失效，不能再次启用。Client 和审计记录会保留，便于追溯。</p>
              </div>
            </div>
            <div class="flex justify-end gap-3 border-t border-slate-100 px-6 py-4">
              <button type="button" class="rounded-xl px-4 py-2 font-bold text-slate-500 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50" :disabled="saving" @click="closeClientConfirm">取消</button>
              <button type="button" class="rounded-xl px-5 py-2 font-bold text-white disabled:cursor-not-allowed disabled:opacity-50" :class="clientConfirmAction === 'delete' ? 'bg-rose-600 hover:bg-rose-700' : 'bg-amber-600 hover:bg-amber-700'" :disabled="saving" @click="confirmClientAction">{{ saving ? '处理中…' : (clientConfirmAction === 'reset-secret' ? '确认重置 Secret' : (clientConfirmAction === 'delete' ? '确认删除' : '确认停用')) }}</button>
            </div>
          </div>
        </div>
      </div>

      <div
        v-if="showTokenHelp"
        class="fixed inset-0 z-50 overflow-y-auto bg-slate-900/50 p-4"
        @click.self="showTokenHelp = false"
      >
        <div class="flex min-h-full items-center justify-center">
          <div class="flex max-h-[calc(100vh-2rem)] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
            <div class="flex shrink-0 items-center justify-between border-b border-slate-100 px-6 py-5">
              <div>
                <h2 class="text-xl font-black">如何使用 MCP Token？</h2>
                <p class="mt-1 text-xs font-normal text-slate-500">人工登录可直接生成用户 Token；程序化系统使用 OAuth2 Authorization Code + PKCE 获取用户 Token。</p>
              </div>
              <button type="button" class="text-2xl text-slate-400 hover:text-slate-600" aria-label="关闭 Token 使用帮助" @click="showTokenHelp = false">×</button>
            </div>
            <div class="min-h-0 flex-1 overflow-y-auto px-6 py-5 text-sm leading-6 text-slate-600">
              <div class="space-y-4">
                <section class="rounded-xl border border-indigo-100 bg-indigo-50 p-4">
                  <h3 class="font-black text-indigo-950">方式一：人工登录后直接生成</h3>
                  <ol class="mt-2 list-decimal space-y-1 pl-5">
                    <li>使用目标用户登录 NanZi，进入“外部 Client”，点击“生成 MCP Access Token”。</li>
                    <li>选择有效期和 Scope，生成结果只显示本次，请复制保存。</li>
                    <li>调用 MCP 时，把它放到请求头：<code>Authorization: Bearer &lt;access_token&gt;</code>。</li>
                  </ol>
                  <p class="mt-2 text-xs text-indigo-800">这个 Token 的 user_id 由当前登录会话确定；页面没有用户选择框，因此不能用管理员页面替 demo 用户发 Token。</p>
                </section>
                <section class="rounded-xl border border-slate-200 p-4">
                  <h3 class="font-black text-slate-900">方式二：程序化用户授权（OAuth2）</h3>
                  <p class="mt-1">CRM 或门户系统先引导用户打开 NanZi 授权页并登录；回调拿到 code 后，业务方后端使用 Client ID、Client Secret 和 PKCE verifier 换取绑定该用户的 Access Token，然后使用 Bearer Header 调用 MCP。</p>
                  <pre class="mt-3 overflow-x-auto rounded-lg bg-slate-950 p-3 text-xs leading-5 text-slate-100"><code>POST {{ overview.authorization_server || '/oauth' }}/oauth/token
Authorization: Basic &lt;client_id:client_secret&gt;
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code&amp;code=&lt;callback_code&gt;&amp;redirect_uri=&lt;registered_redirect_uri&gt;&amp;code_verifier=&lt;pkce_code_verifier&gt;&amp;resource={{ overview.resource || '/mcp/platform' }}</code></pre>
                  <p class="mt-2 text-xs text-slate-500">程序化场景不要把用户在浏览器里的登录 Cookie 或 NanZi 用户 API Key 传给第三方；Authorization Code + PKCE 会通过 NanZi 授权页建立 user_id 关联。Client Secret 只在后端 Token Endpoint 使用。</p>
                </section>
                <section class="rounded-xl border border-amber-100 bg-amber-50 p-4 text-xs text-amber-900">
                  <span class="font-bold">安全提示：</span>Client Secret 和 Access Token 都是敏感凭证。Secret 只用于获取 Token，Access Token 只用于调用 MCP；不要提交到代码仓库，泄露后请停用 Client 或重置 Secret。
                </section>
              </div>
            </div>
            <div class="flex shrink-0 justify-end border-t border-slate-100 bg-white px-6 py-4">
              <button type="button" class="rounded-xl bg-indigo-600 px-5 py-2 font-bold text-white hover:bg-indigo-700" @click="showTokenHelp = false">知道了</button>
            </div>
          </div>
        </div>
      </div>

      <div
        v-if="showEndpointHelp && endpointHelp"
        class="fixed inset-0 z-50 overflow-y-auto bg-slate-900/50 p-4"
        @click.self="showEndpointHelp = false"
      >
        <div class="flex min-h-full items-center justify-center">
          <div class="w-full max-w-2xl rounded-2xl bg-white shadow-2xl">
            <div class="flex items-center justify-between border-b border-slate-100 px-6 py-5">
              <div>
                <h2 class="text-xl font-black">{{ endpointHelp.label }} 是什么？</h2>
                <p class="mt-1 text-xs font-normal text-slate-500">{{ endpointHelp.description }}</p>
              </div>
              <button type="button" class="text-2xl text-slate-400 hover:text-slate-600" :aria-label="`关闭${endpointHelp.label}说明`" @click="showEndpointHelp = false">×</button>
            </div>
            <div class="space-y-4 px-6 py-5 text-sm leading-6 text-slate-600">
              <div class="rounded-xl border border-indigo-100 bg-indigo-50 p-4">
                <div class="font-bold text-indigo-950">当前地址</div>
                <div class="mt-2 flex items-start gap-2">
                  <code class="min-w-0 flex-1 break-all rounded-lg bg-white p-3 text-xs text-slate-700">{{ endpointHelp.value || '启动并完成配置后显示' }}</code>
                  <button v-if="endpointHelp.value" type="button" class="shrink-0 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-bold text-white hover:bg-indigo-700" @click="copyValue(`help-${endpointHelp.key}`, endpointHelp.value)">{{ copied === `help-${endpointHelp.key}` ? '已复制' : '复制地址' }}</button>
                </div>
              </div>
              <div class="rounded-xl bg-slate-50 p-4">
                <div class="font-bold text-slate-800">在哪里使用？</div>
                <p class="mt-1">{{ endpointHelp.usage }}</p>
              </div>
              <div v-if="endpointHelp.key === 'endpoint'" class="rounded-xl border border-amber-100 bg-amber-50 p-4 text-xs text-amber-900">调用这个地址时，还需要在请求头携带 <code>Authorization: Bearer &lt;access_token&gt;</code>。这个地址本身不是 Token，也不能替代 Access Token。</div>
              <div v-else-if="endpointHelp.key === 'resource'" class="rounded-xl border border-amber-100 bg-amber-50 p-4 text-xs text-amber-900">它通常和 MCP Endpoint 的值一致，但用途不同：Endpoint 是发请求的地址，Resource 是 OAuth2 用来限定 Token 目标的标识。</div>
            </div>
            <div class="flex justify-end border-t border-slate-100 bg-white px-6 py-4">
              <button type="button" class="rounded-xl bg-indigo-600 px-5 py-2 font-bold text-white hover:bg-indigo-700" @click="showEndpointHelp = false">知道了</button>
            </div>
          </div>
        </div>
      </div>

      <div
        v-if="selectedAudit"
        class="fixed inset-0 z-50 overflow-y-auto bg-slate-900/50 p-4"
        @click.self="selectedAudit = null"
      >
        <div class="flex min-h-full items-center justify-center">
          <div class="w-full max-w-2xl rounded-2xl bg-white shadow-2xl">
            <div class="flex items-center justify-between border-b border-slate-100 px-6 py-5">
              <div><h2 class="text-xl font-black">审计详情</h2><p class="mt-1 text-xs text-slate-500">仅显示本次调用的业务审计信息。</p></div>
              <button type="button" class="text-2xl text-slate-400 hover:text-slate-600" aria-label="关闭审计详情" @click="selectedAudit = null">×</button>
            </div>
            <div class="grid gap-3 px-6 py-5 text-sm sm:grid-cols-2">
              <div><span class="text-slate-500">时间</span><div class="mt-1 font-medium">{{ formatAuditTime(selectedAudit.created_at) }}</div></div>
              <div><span class="text-slate-500">结果</span><div class="mt-1 font-medium">{{ auditResultLabel(selectedAudit.result_status) }}（{{ selectedAudit.status_code }}）</div></div>
              <div><span class="text-slate-500">NanZi 请求 ID</span><code class="mt-1 block break-all text-xs">{{ selectedAudit.request_id }}</code></div>
              <div><span class="text-slate-500">外部 Client</span><code class="mt-1 block break-all text-xs">{{ selectedAudit.client_id }}</code></div>
              <div><span class="text-slate-500">用户 ID</span><code class="mt-1 block break-all text-xs">{{ selectedAudit.user_id || '历史记录无用户身份' }}</code></div>
              <div><span class="text-slate-500">认证类型</span><div class="mt-1">{{ auditAuthTypeLabel(selectedAudit.auth_type) }}</div></div>
              <div><span class="text-slate-500">MCP 方法</span><code class="mt-1 block break-all text-xs">{{ selectedAudit.method_name }}</code></div>
              <div><span class="text-slate-500">耗时</span><div class="mt-1">{{ selectedAudit.latency_ms ?? '—' }}{{ selectedAudit.latency_ms != null ? ' ms' : '' }}</div></div>
              <div class="sm:col-span-2"><span class="text-slate-500">Scope</span><code class="mt-1 block break-all text-xs">{{ selectedAudit.scopes.join('、') || '—' }}</code></div>
              <div v-if="selectedAudit.client_request_id" class="sm:col-span-2"><span class="text-slate-500">外部请求 ID</span><code class="mt-1 block break-all text-xs">{{ selectedAudit.client_request_id }}</code></div>
              <div v-if="selectedAudit.error_code" class="sm:col-span-2"><span class="text-slate-500">错误码</span><code class="mt-1 block break-all text-xs text-rose-600">{{ selectedAudit.error_code }}</code></div>
              <div class="sm:col-span-2 rounded-xl border border-blue-100 bg-blue-50 p-3 text-xs leading-5 text-blue-900">安全提示：审计页面和接口均不展示 Access Token、Client Secret、Refresh Token、用户密码或原始请求 Header。</div>
            </div>
            <div class="flex justify-end border-t border-slate-100 px-6 py-4"><button type="button" class="rounded-xl bg-indigo-600 px-5 py-2 font-bold text-white hover:bg-indigo-700" @click="selectedAudit = null">关闭</button></div>
          </div>
        </div>
      </div>

      <ConfirmModal
        v-if="showConfirmModal"
        :title="confirmModalTitle"
        :message="confirmModalMessage"
        :type="confirmModalType"
        :loading="confirmModalLoading"
        confirm-text="确认操作"
        @confirm="submitConfirmModal"
        @cancel="closeConfirmModal"
      />
  </div>
</template>
