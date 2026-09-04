<script setup lang="ts">
import { ref, onMounted, watch, computed } from 'vue'
import axios from '@/utils/axios'
import { copyToClipboard } from '@/utils/clipboard'
import { useToast } from '@/composables/useToast'
import { useUser } from '@/composables/useUser'
import ConfirmModal from '../../components/ConfirmModal.vue'
import Switch from '../Switch.vue'
import McpToolTester from './McpToolTester.vue'
import {
  buildDefaultMcpServerName,
  buildMcpServerNamePrefix,
  composeMcpServerName,
  normalizeMcpServerNameSuffix,
  stripMcpServerNamePrefix,
} from '@/utils/mcpServerName'
import {
  parseMcpServersPaste,
  suggestMcpNameSuffixFromKey,
} from '@/utils/parseMcpServersPaste'
import { 
  PlusIcon,
  BeakerIcon,
  EyeIcon,
  EyeSlashIcon,
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
  CheckCircleIcon,
  InformationCircleIcon,
} from '@heroicons/vue/24/outline'

const props = withDefaults(defineProps<{
  scope?: 'global' | 'personal'
}>(), {
  scope: 'global'
})

const { showToast } = useToast()
const { userInfo } = useUser()
const canSave = computed(() => {
  if (props.scope === 'personal') return true
  return userInfo.value?.role === 'admin'
})

const namePrefix = computed(() =>
  buildMcpServerNamePrefix(props.scope, userInfo.value?.user_name),
)

/** 用户只填后缀；保存时与固定前缀拼接 */
const serverNameSuffix = ref('')
/** 第一步录入方式：手动 / JSON 粘贴 */
const connectionInputTab = ref<'manual' | 'json'>('manual')
const mcpJsonPaste = ref('')
const mcpJsonPasteHint = ref('')

const syncFullServerName = () => {
  newServer.value.server_name = composeMcpServerName(
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

const servers = ref<any[]>([])
const loading = ref(false)
const showAddModal = ref(false)
const isEditing = ref(false)
const editingId = ref('')
const editingAuthHeadersConfigured = ref(false)

// Tool Tester Logic
const showTester = ref(false)
const toolToTest = ref<any>(null)

const openTester = (tool: any) => {
  toolToTest.value = tool
  showTester.value = true
}

const wizardStep = ref<1 | 2 | 3>(1) // 1: Input & Verify, 2: Preview & Name, 3: Success & Publish Guide
const createdServer = ref<any | null>(null)
const publishAllLoading = ref(false)
const verifying = ref(false)
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

const batchUpdateStatus = async (published: boolean) => {
  const ids = tools.value
    .filter(tool => selectedToolIds.value.has(tool.id) && tool.is_available !== false)
    .map(tool => tool.id)
  if (!canManageSelectedTools.value || ids.length === 0) return
  
  loading.value = true
  try {
    // Batch update in parallel
    await Promise.all(ids.map(id => 
      axios.put(`/api/portal/mcp/tools/${id}/publish?published=${published}`)
    ))
    
    showToast(`成功${published ? '发布' : '下线'} ${ids.length} 个工具`, 'success')
    if (selectedServer.value) {
      fetchTools(selectedServer.value.id)
      fetchServerUsage(selectedServer.value.id).then((usage) => {
        if (selectedServer.value) selectedServerUsage.value = usage
      })
      fetchServers()
    }
    selectedToolIds.value.clear()
  } catch (e) {
    showToast(getApiErrorMessage(e, '批量操作失败'), 'error')
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
  if (!selectedServer.value || !canManageSelectedTools.value) return
  const unpub = tools.value.filter(tool => !tool.is_published && tool.is_available !== false)
  if (unpub.length === 0) return

  isPublishingAllCurrent.value = true
  try {
    await Promise.all(unpub.map(tool => 
      axios.put(`/api/portal/mcp/tools/${tool.id}/publish?published=true`)
    ))
    showToast(`成功发布全部 ${unpub.length} 个工具`, 'success')
    fetchTools(selectedServer.value.id)
    fetchServerUsage(selectedServer.value.id).then((usage) => {
      if (selectedServer.value) selectedServerUsage.value = usage
    })
    fetchServers()
    selectedToolIds.value.clear()
  } catch (e) {
    showToast(getApiErrorMessage(e, '批量发布失败'), 'error')
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
  if (mcpCodeLanguage.value === 'java') {
    return `// 依赖：com.nimbusds:nimbus-jose-jwt
// 下面三个值来自 NanZi MCP 管理页面的只读配置，请复制到业务 MCP 的 Secret / 配置中心。
private static final String NANZI_MCP_AUDIENCE = "${mcpAudienceValue.value}"; // 用于校验 aud
private static final String NANZI_MCP_ISSUER = "${mcpIssuerValue.value}"; // 用于校验 iss
private static final String NANZI_MCP_JWKS_URL = "${mcpJwksUrl.value}"; // 用于获取公钥

public Map<String, Object> verifyNanZiUser(String fixedToken, String assertion, ReplayStore replayStore)
        throws Exception {
    // 1. 先按业务 MCP 原有方式校验 Authorization 固定 Token。
    if (fixedToken == null || fixedToken.isBlank()) {
        throw new SecurityException("invalid MCP client token");
    }

    // 2. 根据 JWT Header 的 kid，从当前 MCP 的 JWKS 选择公钥并验签。
    JWKSet jwkSet = JWKSet.load(new URL(NANZI_MCP_JWKS_URL));
    SignedJWT jwt = SignedJWT.parse(assertion);
    JWK jwk = jwkSet.getKeyByKeyId(jwt.getHeader().getKeyID());
    if (!(jwk instanceof OctetKeyPair keyPair)
            || !jwt.verify(new Ed25519Verifier(keyPair))) {
        throw new SecurityException("invalid NanZi User Assertion");
    }

    JWTClaimsSet claims = jwt.getJWTClaimsSet();
    if (!NANZI_MCP_ISSUER.equals(claims.getIssuer())
            || !NANZI_MCP_AUDIENCE.equals(claims.getAudience().get(0))
            || claims.getExpirationTime().before(new Date())) {
        throw new SecurityException("invalid NanZi User Assertion claims");
    }

    // 3. 验签成功后，用 user_context.user_id 关联业务系统用户。
    Map<String, Object> userContext = (Map<String, Object>) claims.getClaim("user_context");
    if (userContext == null || userContext.get("user_id") == null) {
        throw new SecurityException("missing user context");
    }
    String userId = String.valueOf(userContext.get("user_id"));
    if (!("nanzi:user:" + userId).equals(claims.getSubject())) {
        throw new SecurityException("user subject mismatch");
    }
    if (claims.getClaim("agent_id") == null || claims.getClaim("request_id") == null
            || claims.getJWTID() == null) {
        throw new SecurityException("missing assertion identity");
    }
    long ttlSeconds = Math.max(1, (claims.getExpirationTime().getTime() - System.currentTimeMillis()) / 1000);
    if (!replayStore.claim(claims.getJWTID(), ttlSeconds)) {
        throw new SecurityException("replayed NanZi user assertion");
    }
    return Map.of(
            "user_id", userContext.get("user_id"),
            "user_name", userContext.get("user_name"),
            "agent_id", claims.getClaim("agent_id"),
            "request_id", claims.getClaim("request_id"));
}

@FunctionalInterface
interface ReplayStore {
    // 使用 Redis SETNX + EXPIRE 等原子操作；已存在的 jti 返回 false。
    boolean claim(String jti, long ttlSeconds);
}
`
  }

  return `# 依赖：PyJWT、cryptography、httpx、redis
# 下面三个值来自 NanZi MCP 管理页面的只读配置，请复制到业务 MCP 的 Secret / 配置中心。
NANZI_MCP_AUDIENCE = "${mcpAudienceValue.value}"  # 用于校验 aud
NANZI_MCP_ISSUER = "${mcpIssuerValue.value}"  # 用于校验 iss
NANZI_MCP_JWKS_URL = "${mcpJwksUrl.value}"  # 用于自动获取公钥

import hmac
import os
import time
import jwt
from redis import Redis
from jwt import PyJWKClient

jwk_client = PyJWKClient(NANZI_MCP_JWKS_URL)
redis_client = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)

def verify_nanzi_user(authorization: str, assertion: str, expected_token: str) -> dict:
    # 1. 先按业务 MCP 原有方式校验 Authorization 固定 Token。
    expected_authorization = f"Bearer {expected_token}"
    if not hmac.compare_digest(authorization or "", expected_authorization):
        raise PermissionError("invalid MCP client token")

    # 2. 根据 JWT Header 的 kid 获取公钥并验签，同时校验 iss、aud、exp。
    signing_key = jwk_client.get_signing_key_from_jwt(assertion).key
    claims = jwt.decode(
        assertion,
        signing_key,
        algorithms=["EdDSA"],
        issuer=NANZI_MCP_ISSUER,
        audience=NANZI_MCP_AUDIENCE,
        options={"require": ["iss", "aud", "sub", "exp", "iat", "jti", "agent_id", "request_id"]},
    )

    # 3. 验签成功后，用 user_context.user_id 关联业务系统用户。
    user_context = claims["user_context"]
    if not isinstance(user_context, dict) or not user_context.get("user_id"):
        raise PermissionError("missing user context")
    if claims.get("sub") != f"nanzi:user:{user_context['user_id']}":
        raise PermissionError("user subject mismatch")
    ttl = max(1, int(claims["exp"] - time.time()))
    if not redis_client.set(f"mcp:user-assertion:{claims['jti']}", "1", nx=True, ex=ttl):
        raise PermissionError("replayed NanZi user assertion")
    return {
        "user_id": user_context["user_id"],
        "user_name": user_context.get("user_name"),
        "agent_id": claims.get("agent_id"),
        "request_id": claims.get("request_id"),
    }
`
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
    servers.value = res.data
  } catch (e) {
    showToast(getApiErrorMessage(e, '获取 MCP 服务列表失败'), 'error')
  } finally {
    loading.value = false
  }
}

const createEchoTestMcp = async () => {
  if (props.scope !== 'global' || !canSave.value) return

  try {
    const response = await axios.post('/api/portal/mcp/servers/echo-test')
    await fetchServers()
    const created = servers.value.find((server: any) => server.id === response.data?.id)
    if (created) selectServer(created)
    showToast('Echo 测试 MCP 已就绪，所有智能体都可以挂载', 'success')
  } catch (error: any) {
    showToast(getApiErrorMessage(error, '创建 Echo 测试 MCP 失败'), 'error')
  }
}

const resetWizard = () => {
  isEditing.value = false
  editingId.value = ''
  editingAuthHeadersConfigured.value = false
  wizardStep.value = 1
  createdServer.value = null
  publishAllLoading.value = false
  verifying.value = false
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
}

const closeWizard = () => {
  showAddModal.value = false
  resetWizard()
}

const openAddModal = (initialTab: 'manual' | 'json' = 'manual') => {
  resetWizard()
  connectionInputTab.value = initialTab
  showAddModal.value = true
}

defineExpose({
  openAddModal,
  resetWizard,
})

const openEditModal = (server: any) => {
  isEditing.value = true
  editingId.value = server.id
  editingAuthHeadersConfigured.value = Boolean(server.auth_headers_configured)
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
      server_name: server.server_name,
      remark: server.remark || '',
      sse_url: server.sse_url,
      enabled_status: nextStatus,
      ...buildServerPayload(server),
    })
    const savedStatus = Number(response.data?.enabled_status ?? nextStatus)
    server.enabled_status = savedStatus
    if (selectedServer.value?.id === server.id) {
      selectedServer.value = { ...selectedServer.value, enabled_status: savedStatus }
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
    void handleVerify()
  } else {
    showToast(`已从 JSON 解析：${entry.key}`, 'success')
  }
  return true
}

const handleVerify = async () => {
  if (!newServer.value.sse_url) {
    showToast(connectionInputTab.value === 'json' ? '请先粘贴并解析有效的 MCP JSON' : '请输入服务地址', 'warning')
    return
  }
  
  verifying.value = true
  try {
    const res = await axios.post('/api/portal/mcp/verify', buildServerPayload(newServer.value))
    discoveredTools.value = res.data.tools
    wizardStep.value = 2
    if (!normalizeMcpServerNameSuffix(serverNameSuffix.value)) {
        try {
            const url = new URL(newServer.value.sse_url)
            let suffix = normalizeMcpServerNameSuffix(url.hostname) || 'server'
            let candidate = composeMcpServerName(props.scope, userInfo.value?.user_name, suffix)
            let counter = 1
            while (servers.value.some((s: any) => s.server_name === candidate && s.id !== editingId.value)) {
                counter++
                candidate = composeMcpServerName(
                  props.scope,
                  userInfo.value?.user_name,
                  `${suffix}-${counter}`,
                )
            }
            serverNameSuffix.value = stripMcpServerNamePrefix(
              candidate,
              props.scope,
              userInfo.value?.user_name,
            )
        } catch {
            serverNameSuffix.value = normalizeMcpServerNameSuffix(
              buildDefaultMcpServerName(props.scope, userInfo.value?.user_name, 'server').replace(
                namePrefix.value,
                '',
              ),
            ) || 'server'
        }
        syncFullServerName()
    }
    showToast('连接成功，已发现工具', 'success')
  } catch (e: any) {
    showToast(getApiErrorMessage(e, '连接失败，请检查地址或认证信息'), 'error')
  } finally {
    verifying.value = false
  }
}

const addServer = async () => {
  syncFullServerName()
  if (!normalizeMcpServerNameSuffix(serverNameSuffix.value)) {
    showToast('请填写服务名称后缀', 'warning')
    return
  }
  if (!newServer.value.server_name || !newServer.value.sse_url) {
    showToast('请填写完整信息', 'warning')
    return
  }
  if (authorizationEnabled.value && authorizationEditing.value && !authorizationToken.value.trim()) {
    showToast('请输入 Authorization Token', 'warning')
    return
  }
  if (headerMode.value === 'advanced') {
    try {
      JSON.parse(newServer.value.auth_headers)
      syncJsonToPairs()
    }
    catch (e) { showToast('JSON 格式错误', 'error'); return }
  }

  try {
    const payload = buildServerPayload(newServer.value)
    if (isEditing.value) {
      await axios.put(`/api/portal/mcp/servers/${editingId.value}`, payload)
      showToast('更新成功', 'success')
      closeWizard()
      fetchServers()
    } else {
      const res = await axios.post('/api/portal/mcp/servers', payload)
      showToast('添加成功', 'success')
      createdServer.value = res.data
      await fetchServers()
      const matched = servers.value.find((s: any) => s.id === res.data?.id) || res.data
      selectServer(matched)
      wizardStep.value = 3
    }
  } catch (e: any) {
    showToast(getApiErrorMessage(e, '操作失败'), 'error')
  }
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
      serverTools = res.data || []
    }
    const unpublishedTools = serverTools.filter((t: any) => !t.is_published && t.is_available !== false)
    if (unpublishedTools.length > 0) {
      await Promise.all(unpublishedTools.map((t: any) => 
        axios.put(`/api/portal/mcp/tools/${t.id}/publish?published=true`)
      ))
    }
    showToast(`成功发布全部 ${serverTools.length} 个工具`, 'success')
    fetchTools(targetServerId)
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
    await axios.delete(`/api/portal/mcp/servers/${deletingId}`)
    showToast('删除成功', 'success')
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

const syncTools = async (id: string) => {
  if (syncLoading.value[id]) return
  
  syncLoading.value[id] = true
  try {
    const response = await axios.post(`/api/portal/mcp/servers/${id}/sync`)
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
    showToast(getApiErrorMessage(e, '同步失败'), 'error')
  } finally {
    syncLoading.value[id] = false
  }
}

const fetchTools = async (serverId: string) => {
  toolsLoading.value = true
  try {
    const res = await axios.get(`/api/portal/mcp/servers/${serverId}/tools`)
    tools.value = res.data
  } catch (e) {
    showToast(getApiErrorMessage(e, '获取工具列表失败'), 'error')
  } finally {
    toolsLoading.value = false
  }
}

const selectServer = (server: any) => {
  selectedServer.value = server
  selectedServerUsage.value = null
  selectedToolIds.value = new Set()
  fetchTools(server.id)
  fetchServerUsage(server.id).then((usage) => {
    if (selectedServer.value?.id === server.id) selectedServerUsage.value = usage
  })
}

/** 移动端从工具详情返回服务列表 */
const clearSelectedServer = () => {
  selectedServer.value = null
  selectedServerUsage.value = null
  tools.value = []
  selectedToolIds.value = new Set()
}

const togglePublish = async (tool: any) => {
  if (!canManageTool(tool)) return
  try {
    const newStatus = !tool.is_published
    await axios.put(`/api/portal/mcp/tools/${tool.id}/publish?published=${newStatus}`)
    tool.is_published = newStatus
    showToast(newStatus ? '工具已发布' : '工具已下线', 'success')
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
  <div class="flex h-full min-h-[28rem] flex-col gap-3 lg:min-h-0 lg:flex-row lg:gap-6">
    <!-- Left: Server List — 移动端选中服务后隐藏，避免左右挤成一条 -->
    <div
      class="flex w-full flex-col overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm lg:w-1/3"
      :class="selectedServer ? 'hidden lg:flex' : 'flex'"
    >
      <!-- Market Guide (High Contrast with Dynamic Scope Theme) -->
      <div 
        class="border-b border-white/10 p-3 text-white transition-colors duration-300 sm:p-4"
        :class="props.scope === 'personal' ? 'bg-slate-950' : 'bg-slate-900'"
      >
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0 flex-1">
            <h4 
              class="flex items-center text-sm font-black transition-colors duration-300"
              :class="props.scope === 'personal' ? 'text-emerald-400' : 'text-indigo-400'"
            >
              <ShoppingBagIcon class="mr-1.5 h-4 w-4 shrink-0" />
              探索 MCP 市场
            </h4>
            <p class="mt-1 text-[10px] leading-relaxed text-slate-400 sm:line-clamp-none">
              {{ props.scope === 'personal' ? '去魔搭寻找并接入私有扩展' : '去魔搭寻找更多公共工具集' }}
            </p>
            <div class="mt-2">
              <a 
                href="https://modelscope.cn/mcp" 
                target="_blank" 
                class="inline-flex items-center rounded px-2 py-1 text-[10px] font-bold text-white shadow-sm transition-all duration-200"
                :class="props.scope === 'personal' ? 'bg-emerald-600 hover:bg-emerald-500' : 'bg-indigo-600 hover:bg-indigo-500'"
              >
                立即前往市场
                <MagnifyingGlassIcon class="ml-1 h-3 w-3" />
              </a>
            </div>
          </div>
        </div>
      </div>

      <div class="flex items-center justify-between gap-2 border-b border-gray-100 bg-gray-50/80 p-3 sm:p-4">
        <div class="flex min-w-0 flex-wrap items-center gap-2">
          <h3 class="text-[11px] font-bold uppercase tracking-wider text-gray-500 sm:text-xs">
            {{ props.scope === 'personal' ? '已连接服务' : '已连接服务 (平台)' }}
          </h3>
          <span v-if="props.scope === 'global' && !canSave" class="rounded border border-amber-200/60 bg-amber-50 px-1.5 py-0.5 text-[10px] font-normal text-amber-600">
            管理员可编辑
          </span>
        </div>
        <div v-if="canSave" class="flex shrink-0 items-center gap-1.5">
          <button
            v-if="props.scope === 'global'"
            type="button"
            @click="createEchoTestMcp"
            class="flex items-center rounded-md border border-indigo-200 bg-indigo-50 px-2 py-1.5 text-[10px] font-bold text-indigo-700 transition-colors hover:bg-indigo-100 sm:px-2.5"
            title="创建平台内置 Echo 测试 MCP；不会展示固定 Token 或用户身份签名原文"
          >
            <BeakerIcon class="mr-1 h-3.5 w-3.5" />
            创建 Echo 测试 MCP
          </button>
          <button
            type="button"
            @click="resetWizard(); showAddModal = true"
            class="flex items-center rounded-md px-2.5 py-1.5 text-[11px] font-bold text-white shadow-sm transition-all sm:px-3"
            :class="props.scope === 'personal' ? 'bg-emerald-600 hover:bg-emerald-700' : 'bg-primary hover:bg-primary-dark'"
          >
            <PlusIcon class="mr-1 h-3.5 w-3.5" />
            添加
          </button>
        </div>
      </div>

      <div class="custom-scrollbar max-h-[min(52vh,28rem)] flex-1 overflow-y-auto lg:max-h-none">
        <div v-if="loading" class="p-8 text-center">
          <ArrowPathIcon class="mx-auto h-6 w-6 animate-spin text-gray-300" />
        </div>
        <div v-else-if="servers.length === 0" class="p-8 text-center text-sm italic text-gray-400">
          暂无配置 MCP 服务
        </div>
        <div v-else class="divide-y divide-gray-50">
          <div 
            v-for="server in servers" 
            :key="server.id"
            @click="selectServer(server)"
            class="cursor-pointer p-3 transition-all hover:bg-blue-50/30 sm:p-4"
            :class="selectedServer?.id === server.id ? 'border-l-4 border-primary bg-blue-50' : 'border-l-4 border-transparent'"
          >
            <div class="mb-1.5 flex items-start justify-between gap-2">
              <div class="min-w-0 flex-1">
                <span class="block truncate text-sm font-bold text-gray-900">{{ server.server_name }}</span>
                <span
                  v-if="server.user_assertion_enabled"
                  class="mt-1 inline-flex rounded border border-indigo-100 bg-indigo-50 px-1.5 py-0.5 text-[9px] font-semibold text-indigo-700"
                >已启用用户身份签名</span>
                <span
                  v-if="server.server_name === 'NanZi Echo 测试 MCP'"
                  class="mt-1 ml-1 inline-flex rounded border border-emerald-100 bg-emerald-50 px-1.5 py-0.5 text-[9px] font-semibold text-emerald-700"
                >平台测试 MCP / 所有智能体可挂载</span>
                <p v-if="server.remark" class="mt-0.5 line-clamp-2 text-[11px] leading-snug text-gray-500">{{ server.remark }}</p>
              </div>
              <div class="flex shrink-0 items-center gap-2" @click.stop>
                <span
                  class="text-[10px] font-semibold"
                  :class="server.enabled_status === 1 ? 'text-emerald-600' : 'text-gray-400'"
                >
                  {{ server.enabled_status === 1 ? '运行中' : '已禁用' }}
                </span>
                <Switch
                  :model-value="server.enabled_status === 1"
                  :disabled="!canSave || statusLoading[server.id]"
                  :loading="statusLoading[server.id]"
                  :aria-label="`${server.server_name}${server.enabled_status === 1 ? '禁用' : '启用'}`"
                  @update:model-value="handleServerStatusChange(server, $event)"
                />
              </div>
            </div>
            <div class="mb-2 flex items-center truncate font-mono text-[10px] text-gray-400">
              <LinkIcon class="mr-1 h-3 w-3 shrink-0" />
              <span class="truncate">{{ server.sse_url }}</span>
            </div>
            <div class="flex flex-wrap items-center justify-between gap-2">
              <span class="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-500">
                {{ server.tool_count }} 工具 /
                <span v-if="server.enabled_status === 1" class="font-bold text-green-600">{{ server.published_tool_count }} 已发布</span>
                <span v-else class="font-bold text-gray-400">服务已禁用</span>
                <span v-if="server.stale_tool_count > 0" class="ml-1 text-amber-600">{{ server.stale_tool_count }} 个远端已删除</span>
              </span>
              <div class="flex items-center gap-1">
                <span class="mr-1 hidden text-[9px] italic text-gray-400 sm:inline" v-if="server.last_sync_at">同步于 {{ new Date(server.last_sync_at).toLocaleString() }}</span>
                <div v-if="canSave" class="flex space-x-0.5" @click.stop>
                  <button type="button" @click="openEditModal(server)" class="rounded p-1.5 text-gray-400 transition-colors hover:bg-white hover:text-blue-500" title="编辑配置">
                    <PencilSquareIcon class="h-4 w-4" />
                  </button>
                  <button type="button" @click="syncTools(server.id)" :disabled="syncLoading[server.id]" class="rounded p-1.5 text-gray-400 transition-colors hover:bg-white hover:text-primary">
                    <CloudArrowDownIcon class="h-4 w-4" :class="syncLoading[server.id] ? 'animate-bounce' : ''" />
                  </button>
                  <button type="button" @click="confirmDeleteServer(server)" class="rounded p-1.5 text-gray-400 transition-colors hover:bg-white hover:text-red-500">
                    <TrashIcon class="h-4 w-4" />
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Right: Tool List — 移动端仅在选中服务后展示 -->
    <div
      class="flex min-h-[22rem] flex-1 flex-col overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm lg:min-h-0"
      :class="selectedServer ? 'flex' : 'hidden lg:flex'"
    >
      <div v-if="!selectedServer" class="flex flex-1 flex-col items-center justify-center text-gray-400">
        <SparklesIcon class="mb-4 h-12 w-12 opacity-20" />
        <p class="text-sm">请在左侧选择一个 MCP 服务查看工具</p>
      </div>
      
      <template v-else>
        <div class="flex flex-col gap-3 border-b border-gray-200 bg-slate-50 p-3 sm:flex-row sm:items-center sm:justify-between sm:p-4">
          <div class="flex min-w-0 items-start gap-2">
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
              <div v-if="selectedServerUsage" class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px]">
                <span class="text-slate-500">绑定 {{ selectedServerUsage.bound_agent_count }} 个智能体</span>
                <span class="text-emerald-600">生效 {{ selectedServerUsage.active_agent_count }} 个</span>
                <span class="text-slate-400">{{ selectedServerUsage.bound_version_count }} 个版本配置</span>
              </div>
              <span v-else-if="usageLoading[selectedServer.id]" class="mt-1 text-[10px] text-slate-400">正在统计使用情况...</span>
            </div>
          </div>
          
          <div class="flex flex-wrap items-center gap-2 pl-8 sm:pl-0 lg:pl-0">
            <div v-if="canSave && selectedToolIds.size > 0" class="flex items-center space-x-2 rounded-lg border border-gray-200 bg-white p-1 shadow-sm animate-fade-in">
              <button @click="batchUpdateStatus(true)" :disabled="!canManageSelectedTools" class="rounded bg-green-600 px-3 py-1 text-[10px] font-bold text-white shadow-sm transition-all hover:bg-green-700 disabled:cursor-not-allowed disabled:opacity-50">批量发布</button>
              <button @click="batchUpdateStatus(false)" :disabled="!canManageSelectedTools" class="rounded bg-slate-600 px-3 py-1 text-[10px] font-bold text-white shadow-sm transition-all hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-50">批量下线</button>
            </div>
            <button v-if="canSave" @click="syncTools(selectedServer.id)" :disabled="syncLoading[selectedServer.id]" class="flex items-center rounded border border-gray-200 bg-white px-2 py-1 text-[11px] font-bold text-primary hover:underline">
              <ArrowPathIcon class="mr-1 h-3.5 w-3.5" :class="syncLoading[selectedServer.id] ? 'animate-spin' : ''" />
              刷新
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
            v-if="canManageSelectedTools"
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
              class="group flex flex-col gap-3 rounded-lg border p-3 transition-all sm:flex-row sm:items-start sm:justify-between sm:p-4"
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
                    <span v-else-if="tool.is_available === false" class="rounded border border-amber-100 bg-amber-50 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-tighter text-amber-600">远端已删除</span>
                    <span v-else-if="tool.is_published" class="rounded border border-green-100 bg-green-50 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-tighter text-green-600">已发布</span>
                    <span v-else class="rounded border border-gray-200 bg-gray-100 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-tighter text-gray-400">待发布</span>
                  </div>
                  <p class="line-clamp-2 text-xs italic leading-relaxed text-gray-500">
                    {{ tool.tool_description || '暂无描述' }}
                  </p>
                  <div class="mt-2 flex flex-wrap gap-1" v-if="tool.parameter_schema">
                    <code class="rounded border bg-white px-1 text-[9px] text-gray-400" v-for="(_, p) in JSON.parse(tool.parameter_schema).properties" :key="p">{{ p }}</code>
                  </div>
                </div>
              </div>
              <div v-if="canSave" class="flex shrink-0 flex-row items-center justify-end gap-2 sm:flex-col sm:items-end sm:space-y-0 sm:gap-2">
                <button 
                  @click.stop="openTester(tool)"
                  :disabled="!canManageTool(tool)"
                  class="flex items-center rounded-md border border-indigo-100 bg-white px-3 py-1.5 text-[11px] font-bold text-indigo-600 shadow-sm transition-colors hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-40 opacity-100 lg:opacity-0 lg:group-hover:opacity-100"
                  title="在线测试"
                >
                  <BeakerIcon class="mr-1.5 h-3.5 w-3.5" />
                  测试
                </button>
                <button 
                  @click.stop="togglePublish(tool)"
                  :disabled="!canManageTool(tool)"
                  class="flex items-center rounded-md border px-3 py-1.5 text-[11px] font-bold shadow-sm transition-colors disabled:cursor-not-allowed disabled:opacity-40 opacity-100 lg:opacity-0 lg:group-hover:opacity-100"
                  :class="tool.is_published ? 'border-gray-200 bg-white text-gray-600 hover:text-red-600' : 'border-transparent bg-primary text-white hover:bg-primary-dark'"
                >
                  <component :is="tool.is_published ? EyeSlashIcon : EyeIcon" class="mr-1.5 h-3.5 w-3.5" />
                  {{ tool.is_published ? '下线' : '发布' }}
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
              class="w-full px-3 py-2 text-xs border border-gray-200 rounded-lg focus:ring-2 focus:ring-primary outline-none font-mono bg-gray-50 text-gray-800"
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
                      class="min-w-0 flex-1 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5 text-sm font-mono text-gray-600 outline-none"
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
                      class="min-w-0 flex-1 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5 text-sm font-mono text-gray-600 outline-none"
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
                      class="min-w-0 flex-1 rounded border border-gray-200 bg-gray-50 px-2 py-1.5 font-mono text-[10px] text-gray-600 outline-none"
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
                  class="min-w-0 flex-1 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-mono outline-none focus:ring-1 focus:ring-primary"
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
                <p v-if="isEditing" class="text-[10px] leading-relaxed text-amber-600">
                  认证信息不会回显；已配置项显示为 ********，点击“编辑”后填写新值。
                </p>
                <div class="max-h-[180px] space-y-2 overflow-y-auto rounded-lg border border-gray-100 bg-gray-50 p-3 custom-scrollbar">
                  <div v-for="(pair, index) in headerPairs" :key="index" class="flex items-center gap-2">
                    <template v-if="pair.existing && !pair.editing && !pair.removed">
                      <input :value="pair.key" readonly class="min-w-0 flex-1 rounded border bg-white px-3 py-1.5 text-xs text-gray-600 outline-none" />
                      <input :value="pair.maskedValue || '********'" readonly type="password" class="min-w-0 flex-1 rounded border bg-white px-3 py-1.5 text-xs text-gray-600 outline-none" />
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
                      <input v-model="pair.key" :readonly="pair.existing" placeholder="名称（如 X-Tenant）" class="min-w-0 flex-1 rounded border px-3 py-1.5 text-xs outline-none focus:ring-1 focus:ring-primary" @input="pair.changed = true; authHeadersTouched = true" />
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
                class="flex-1 min-w-0 px-3 py-2 text-sm font-mono outline-none"
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
                <div class="text-[10px] text-gray-500 line-clamp-1 italic">{{ tool.description || '无描述' }}</div>
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

            <button 
              v-else-if="wizardStep === 1"
              @click="handleVerify" 
              :disabled="verifying"
              class="flex items-center justify-center rounded-lg bg-primary px-4 py-2.5 text-sm font-bold text-white shadow-lg shadow-primary/20 transition-all hover:bg-primary-dark disabled:opacity-50 sm:px-6 sm:py-2"
            >
              <ArrowPathIcon v-if="verifying" class="mr-2 h-4 w-4 animate-spin" />
              {{ verifying ? '正在尝试建立连接...' : '连接并发现工具' }}
            </button>
            
            <button 
              v-else-if="wizardStep === 2"
              @click="addServer" 
              class="rounded-lg bg-green-600 px-4 py-2.5 text-sm font-bold text-white shadow-lg shadow-green-600/20 transition-all hover:bg-green-700 active:scale-95 sm:px-6 sm:py-2"
            >
              {{ isEditing ? '保存修改' : '确认并完成添加' }}
            </button>

            <template v-else-if="wizardStep === 3">
              <button
                @click="closeWizard"
                class="flex items-center justify-center rounded-lg border border-gray-300 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 active:scale-95 sm:px-5 sm:py-2"
              >
                前往工具列表
              </button>
              <button
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

    <!-- MCP 接入模拟代码 Modal -->
    <div
      v-if="showMcpCodeModal"
      class="fixed inset-0 z-[80] flex items-center justify-center bg-gray-900/40 p-4 backdrop-blur-sm"
      @click.self="showMcpCodeModal = false"
    >
      <div class="flex max-h-[88vh] w-full max-w-3xl flex-col overflow-hidden rounded-xl bg-white shadow-2xl">
        <div class="flex items-start justify-between gap-3 border-b border-gray-100 p-5">
          <div>
            <h4 class="text-base font-bold text-gray-900">业务方 MCP 调用模拟代码</h4>
            <p class="mt-1 text-xs leading-relaxed text-gray-500">代码已自动带入当前 MCP 的 Audience、Issuer 和 JWKS 地址。复制后放入业务 MCP 的验签中间件，并替换固定 Token 读取方式。</p>
          </div>
          <button type="button" class="text-xl leading-none text-gray-400 hover:text-gray-700" @click="showMcpCodeModal = false">×</button>
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

        <div class="border-t border-gray-100 bg-gray-50 px-5 py-3 text-[11px] leading-relaxed text-gray-600">
          使用位置：Audience 填业务方的 <code class="rounded bg-gray-200 px-1">aud</code> 校验配置，Issuer 填 <code class="rounded bg-gray-200 px-1">iss</code> 校验配置，JWKS 地址填公钥发现配置。验签成功后从 <code class="rounded bg-gray-200 px-1">user_context.user_id</code> 关联业务用户。
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
      :message="`确定要删除该服务及其缓存的所有工具吗？此操作不可恢复。\n${formatUsageImpact(deleteServerUsage, '删除')}`"
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
  </div>
</template>

<style scoped>
.animate-fade-in-up { animation: fadeInUp 0.3s ease-out; }
@keyframes fadeInUp { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
.custom-scrollbar::-webkit-scrollbar { width: 4px; }
.custom-scrollbar::-webkit-scrollbar-thumb { background: #e5e7eb; border-radius: 4px; }
</style>
