<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import axios from '../utils/axios'
import ConfirmModal from '../components/ConfirmModal.vue'
import Modal from '../components/Modal.vue'
import { useToast } from '../composables/useToast'
import { useUser } from '../composables/useUser'

type ConfigItem = {
  key: string
  value: string
  description: string
  is_secret: boolean
}

type VectorHealthCheck = {
  name: string
  passed: boolean
  message: string
}

type VectorHealth = {
  ok: boolean
  message: string
  hints: string[]
  redis_host?: string
  redis_port?: number
  redis_db?: number
  checks: VectorHealthCheck[]
}

type SummaryRow = {
  user_id?: string
  user_name?: string
  display_name?: string
  conversation_id: string
  summary_type?: 'session' | 'daily' | string
  date?: string
  title?: string
  summary?: string
  last_active?: number
  turn_count?: number
  session_count?: number
  has_history?: boolean
  has_embedding?: boolean
  reference_count?: number
  score?: number
  key_facts?: string | string[]
  topics?: string | string[]
  decisions?: string | string[]
  open_items?: string | string[]
  entities?: string | string[]
  memory_type?: string
}

const activeTab = ref<'config' | 'data' | 'search'>('config')
const { showToast } = useToast()
const { hasPermission, userInfo } = useUser()

const vectorChecking = ref(true)
const vectorReady = ref(false)
const vectorHealth = ref<VectorHealth | null>(null)

const memoryFeaturesEnabled = computed(() => vectorReady.value)

const canSave = computed(() => hasPermission('element:memory:config_save'))
const canIndex = computed(() => hasPermission('element:memory:config_index'))
const canViewData = computed(() => hasPermission('element:memory:view_data'))
const canDelete = computed(() => hasPermission('element:memory:delete'))
const canTestSearch = computed(() => hasPermission('element:memory:test_search'))
const canViewAllUsers = computed(() => hasPermission('element:memory:view_all_users') || userInfo.value?.role === 'admin')

const configs = ref<ConfigItem[]>([])
const originalConfig = ref<Record<string, string>>({})
const showSecrets = ref<Record<string, boolean>>({})
const configLoading = ref(false)
const savingConfig = ref(false)
const indexStatus = ref<any>(null)

type ConfigFieldType = 'boolean' | 'number' | 'text' | 'secret'

const CONFIG_FIELD_TYPES: Record<string, ConfigFieldType> = {
  memory_service_enabled: 'boolean',
  memory_summary_enabled: 'boolean',
  memory_embedding_base_url: 'text',
  memory_embedding_api_key: 'secret',
  memory_embedding_model: 'text',
  memory_embedding_dimensions: 'number',
  memory_summary_max_sessions: 'number',
  memory_summary_ttl_days: 'number',
  memory_history_ttl_days: 'number',
  memory_summarize_debounce_turns: 'number',
  memory_summarize_debounce_seconds: 'number',
  memory_search_knn_top_k: 'number',
  memory_summarize_min_assistant_chars: 'number',
  memory_base_half_life: 'number',
  memory_consolidation_threshold: 'number',
}

const CONFIG_LABELS: Record<string, string> = {
  memory_service_enabled: '启用记忆服务',
  memory_summary_enabled: '启用会话摘要',
  memory_embedding_base_url: 'Embedding 接口地址',
  memory_embedding_api_key: 'Embedding 密钥 (API Key)',
  memory_embedding_model: 'Embedding 模型',
  memory_embedding_dimensions: 'Embedding 向量维度',
  memory_summary_max_sessions: '每用户最大摘要数',
  memory_summary_ttl_days: '会话摘要有效期 (TTL)',
  memory_history_ttl_days: '会话历史有效期 (TTL)',
  memory_summarize_debounce_turns: '触发摘要最少对话轮数',
  memory_summarize_debounce_seconds: '触发摘要最短间隔',
  memory_search_knn_top_k: '默认记忆检索数量 (Top-K)',
  memory_summarize_min_assistant_chars: '触发摘要最少字符数',
  memory_base_half_life: '记忆保留基础半衰期 (天)',
  memory_consolidation_threshold: '记忆合并相似度阈值',
}

const CONFIG_TIPS: Record<string, string> = {
  memory_service_enabled: '控制跨会话长期记忆（Long-Term Memory）服务的总开关。启用后，系统会记录并利用历史会话摘要；关闭后，对话将不再写入摘要，且大模型的 memory_search 工具将提示服务未启用。',
  memory_summary_enabled: '控制会话摘要自动提取与向量索引写入。启用后，系统会在满足防抖条件时自动使用 LLM 总结历史对话并生成向量索引；关闭后，已有摘要数据变为只读，不再更新。',
  memory_embedding_base_url: '生成记忆向量所使用的 Embedding 服务的 API 基础地址（OpenAI 兼容接口）。如果留空，系统将自动回退使用系统的 llm_base_url 配置。',
  memory_embedding_api_key: '调用 Embedding 服务进行向量化所需的身份验证令牌（API Key）。为保障安全已进行脱敏处理。如果留空，系统将自动回退使用系统的 llm_api_key。',
  memory_embedding_model: '生成向量所使用的具体 Embedding 模型名称（例如 text-embedding-3-small）。请确保该模型与下面的向量维度（Dimensions）匹配。',
  memory_embedding_dimensions: '所选 Embedding 模型输出的向量长度（如 1536 或 384）。更改此项后，必须在上方点击“检查/创建索引”重建 RediSearch 向量索引，否则搜索时会报错。',
  memory_summary_max_sessions: '每个用户在 Redis 中最多保留的会话摘要（Session Summary）文档数量。超过限制后，系统会根据时间顺序淘汰最早的摘要，防止占用过多缓存内存。',
  memory_summary_ttl_days: '会话摘要文档 in Redis 中的物理留存天数。默认 30 天，超时后将自动过期释放。若希望永久保留，可适当设置较大值，或修改 Redis 的过期淘汰策略。',
  memory_history_ttl_days: '用于摘要提取的原始对话历史（List 格式）在 Redis 中的最大留存天数。该期限过后，历史对话列表会自动过期，不再参与摘要合并。',
  memory_summarize_debounce_turns: '触发自动摘要的对话轮数阈值。当用户与智能体交互达到设定的轮数（如 3 轮）后，系统才会在对话空闲时触发 LLM 自动生成或更新该会话的摘要。',
  memory_summarize_debounce_seconds: '两次自动执行摘要提取的最小时间间隔（秒），避免短时间内用户频繁说话导致系统重复、高频地调用 LLM 生成摘要，起到接口防抖与节约 Token 的作用。',
  memory_search_knn_top_k: '通过大模型调用 memory_search 工具或者在检索测试时，默认返回的与当前对话最相似的记忆片段的最大数量（K值）。推荐设置为 3 至 5。',
  memory_summarize_min_assistant_chars: '只有当智能体的回复字符数达到该设定值时，该轮对话才会被计入触发摘要的轮数中。防止“好的”、“收到”等短小的无意义回复触发频繁摘要生成，节约模型算力。',
  memory_base_half_life: '用于控制艾宾浩斯时间敏感重排衰减的速度。数值越大，记忆遗忘速度越慢。默认值为 7.0 天。系统会结合此参数和记忆的被引用次数对检索结果进行后置重排。',
  memory_consolidation_threshold: '凌晨定时进行记忆碎片重构降噪合并时的向量余弦相似度阈值（0.0 至 1.0）。数值越大要求内容越相似才进行合并。默认值为 0.82。',
}

const CONFIG_GROUPS: {
  id: string
  title: string
  description?: string
  keys: string[]
  requireSummary?: boolean
}[] = [
  {
    id: 'switches',
    title: '功能开关',
    description: '总开关关闭后，下方详细配置将隐藏；摘要开关关闭后，Embedding 与摘要写入相关项将隐藏。',
    keys: ['memory_service_enabled', 'memory_summary_enabled'],
  },
  {
    id: 'embedding',
    title: 'Embedding 模型',
    description: '用于会话摘要向量；URL/Key 留空时回退系统 LLM 配置。',
    requireSummary: true,
    keys: [
      'memory_embedding_base_url',
      'memory_embedding_api_key',
      'memory_embedding_model',
      'memory_embedding_dimensions',
    ],
  },
  {
    id: 'summary',
    title: '摘要生成策略',
    requireSummary: true,
    keys: [
      'memory_summarize_debounce_turns',
      'memory_summarize_debounce_seconds',
      'memory_summarize_min_assistant_chars',
      'memory_summary_max_sessions',
      'memory_summary_ttl_days',
    ],
  },
  {
    id: 'retrieval',
    title: '检索与会话存储',
    keys: [
      'memory_search_knn_top_k',
      'memory_history_ttl_days',
      'memory_base_half_life',
      'memory_consolidation_threshold'
    ],
  },
]

const showRebuildConfirm = ref(false)
const showDeleteConfirm = ref(false)
const rowToDelete = ref<SummaryRow | null>(null)

const dataView = ref<'daily' | 'session'>('daily')
const filterUserId = ref<number | ''>('')
const filterUsername = ref('')
const filterKeyword = ref('')
const filterDateFrom = ref('')
const filterDateTo = ref('')
const dailySummaries = ref<SummaryRow[]>([])
const summaries = ref<SummaryRow[]>([])
const dataLoading = ref(false)
const showDetailModal = ref(false)
const detailLoading = ref(false)
const detailHistory = ref<any[]>([])
const detailSessions = ref<SummaryRow[]>([])
const detailSummary = ref<SummaryRow | null>(null)

const searchUserId = ref<number | ''>('')
const searchUsername = ref('')
const searchQuery = ref('')
const searchScope = ref('summary')
const searchConvId = ref('')
const searchLimit = ref(5)
const searchLoading = ref(false)
const searchResult = ref<any>(null)

const currentUserId = computed(() => {
  const u = userInfo.value
  return u?.user_id ?? u?.id ?? null
})

const effectiveFilterUserId = computed(() => {
  if (canViewAllUsers.value && filterUserId.value !== '') return Number(filterUserId.value)
  return currentUserId.value ? Number(currentUserId.value) : null
})

const effectiveSearchUserId = computed(() => {
  if (canViewAllUsers.value && searchUserId.value !== '') return Number(searchUserId.value)
  return currentUserId.value ? Number(currentUserId.value) : null
})

const formatTime = (ts?: number) => {
  if (!ts) return '-'
  return new Date(ts * 1000).toLocaleString()
}

const formatList = (value?: string | string[]) => {
  if (!value) return []
  if (Array.isArray(value)) return value.filter(Boolean)
  try {
    const parsed = JSON.parse(value)
    if (Array.isArray(parsed)) return parsed.map(String).filter(Boolean)
  } catch (_) {
    // fall through
  }
  return String(value).trim() ? [String(value)] : []
}

const getConfigItem = (key: string): ConfigItem | undefined =>
  configs.value.find((c) => c.key === key)

const isConfigTrue = (key: string) => {
  const v = (getConfigItem(key)?.value || '').trim().toLowerCase()
  return v === 'true' || v === '1' || v === 'yes'
}

const serviceEnabled = computed(() => isConfigTrue('memory_service_enabled'))
const summaryEnabled = computed(() => serviceEnabled.value && isConfigTrue('memory_summary_enabled'))

const visibleConfigGroups = computed(() =>
  CONFIG_GROUPS.filter((g) => {
    if (g.id === 'switches') return true
    if (!serviceEnabled.value) return false
    if (g.requireSummary && !summaryEnabled.value) return false
    return true
  })
)

const configFieldType = (key: string): ConfigFieldType => CONFIG_FIELD_TYPES[key] || 'text'

const configLabel = (key: string) => {
  if (CONFIG_LABELS[key]) return CONFIG_LABELS[key]
  const item = getConfigItem(key)
  if (!item) return key
  const short = item.description?.split('（')[0]?.split('；')[0]?.trim()
  return short || key
}

const setConfigBool = (key: string, on: boolean) => {
  const item = getConfigItem(key)
  if (item) item.value = on ? 'true' : 'false'
}

const getConfigNumber = (key: string) => {
  const n = parseInt(getConfigItem(key)?.value || '0', 10)
  return Number.isNaN(n) ? 0 : n
}

const setConfigNumber = (key: string, raw: number | string) => {
  const item = getConfigItem(key)
  if (!item) return
  const n = typeof raw === 'number' ? raw : parseInt(String(raw), 10)
  item.value = Number.isNaN(n) ? '0' : String(Math.max(0, n))
}

const indexStatusLabel = computed(() => {
  if (!indexStatus.value) return null
  if (indexStatus.value.available) return '索引已就绪'
  return indexStatus.value.message || '索引未创建'
})

const runVectorTest = async (force = false) => {
  vectorChecking.value = true
  try {
    const res = await axios.get('/api/portal/memory/redis-vector-test', {
      params: force ? { force: true } : {},
    })
    const data = res.data?.data as VectorHealth
    vectorHealth.value = data
    vectorReady.value = !!data?.ok
    if (data?.ok) {
      await loadPageData()
    }
  } catch (e: any) {
    const detail = e.response?.data?.detail
    vectorHealth.value =
      typeof detail === 'object' && detail !== null
        ? (detail as VectorHealth)
        : {
            ok: false,
            message: detail || 'Redis 向量环境检测失败',
            hints: [
              '请使用 Redis Stack，并将 REDIS_DB 设为 0。',
              '修改 .env 后重启后端，再点击重新检测。',
            ],
            checks: [],
          }
    vectorReady.value = false
  } finally {
    vectorChecking.value = false
  }
}

const loadPageData = async () => {
  if (!vectorReady.value) return
  await fetchConfigs()
  await loadIndexStatus()
  if (canViewData.value) await fetchMemoryData()
}

const fetchConfigs = async () => {
  if (!memoryFeaturesEnabled.value) return
  configLoading.value = true
  try {
    const res = await axios.get('/api/portal/memory/configs')
    configs.value = res.data?.data || []
    originalConfig.value = {}
    configs.value.forEach((c) => {
      originalConfig.value[c.key] = c.value
    })
  } catch (e: any) {
    showToast(e.response?.data?.detail || '加载配置失败', 'error')
  } finally {
    configLoading.value = false
  }
}

const saveConfigs = async () => {
  if (!memoryFeaturesEnabled.value || !canSave.value) return
  const updates = configs.value.filter((c) => c.value !== originalConfig.value[c.key])
  if (updates.length === 0) {
    showToast('没有变更', 'info')
    return
  }
  savingConfig.value = true
  try {
    await axios.put('/api/portal/memory/configs', { updates })
    showToast('配置已保存', 'success')
    await fetchConfigs()
  } catch (e: any) {
    showToast(e.response?.data?.detail || '保存失败', 'error')
  } finally {
    savingConfig.value = false
  }
}

const testEmbedding = async () => {
  if (!memoryFeaturesEnabled.value) return
  try {
    const res = await axios.post('/api/portal/memory/test-embedding')
    showToast(`Embedding 成功，维度 ${res.data?.dimensions}`, 'success')
  } catch (e: any) {
    showToast(e.response?.data?.detail || '测试失败', 'error')
  }
}

const loadIndexStatus = async () => {
  if (!memoryFeaturesEnabled.value) return
  try {
    const res = await axios.get('/api/portal/memory/index/status')
    indexStatus.value = res.data?.data
  } catch (e: any) {
    showToast(e.response?.data?.detail || '获取索引状态失败', 'error')
  }
}

const requestRebuildIndex = () => {
  if (!memoryFeaturesEnabled.value || !summaryEnabled.value) return
  showRebuildConfirm.value = true
}

const confirmRebuildIndex = async () => {
  showRebuildConfirm.value = false
  try {
    const res = await axios.post('/api/portal/memory/index/rebuild')
    showToast(res.data?.data?.message || '完成', 'success')
    await loadIndexStatus()
  } catch (e: any) {
    showToast(e.response?.data?.detail || '操作失败', 'error')
  }
}

const fetchSummaries = async () => {
  if (!memoryFeaturesEnabled.value || !canViewData.value) return
  const uid = effectiveFilterUserId.value
  if (uid == null) {
    showToast('无法确定用户 ID', 'warning')
    return
  }
  dataLoading.value = true
  try {
    const res = await axios.get('/api/portal/memory/summaries', {
      params: {
        user_id:
          canViewAllUsers.value && filterUserId.value !== ''
            ? Number(filterUserId.value)
            : canViewAllUsers.value
              ? undefined
              : uid,
        keyword: filterKeyword.value.trim() || undefined,
        username: canViewAllUsers.value ? filterUsername.value.trim() || undefined : undefined,
      },
    })
    summaries.value = res.data?.data || []
  } catch (e: any) {
    showToast(e.response?.data?.detail || '加载失败', 'error')
  } finally {
    dataLoading.value = false
  }
}

const fetchDailySummaries = async () => {
  if (!memoryFeaturesEnabled.value || !canViewData.value) return
  const uid = effectiveFilterUserId.value
  if (uid == null && !canViewAllUsers.value) {
    showToast('无法确定用户 ID', 'warning')
    return
  }
  dataLoading.value = true
  try {
    const res = await axios.get('/api/portal/memory/daily-summaries', {
      params: {
        user_id:
          canViewAllUsers.value && filterUserId.value !== ''
            ? Number(filterUserId.value)
            : canViewAllUsers.value
              ? undefined
              : uid,
        username: canViewAllUsers.value ? filterUsername.value.trim() || undefined : undefined,
        keyword: filterKeyword.value.trim() || undefined,
        date_from: filterDateFrom.value || undefined,
        date_to: filterDateTo.value || undefined,
      },
    })
    dailySummaries.value = res.data?.data || []
  } catch (e: any) {
    showToast(e.response?.data?.detail || '加载每日摘要失败', 'error')
  } finally {
    dataLoading.value = false
  }
}

const fetchMemoryData = async () => {
  if (dataView.value === 'daily') {
    await fetchDailySummaries()
  } else {
    await fetchSummaries()
  }
}

const switchDataView = async (view: 'daily' | 'session') => {
  dataView.value = view
  await fetchMemoryData()
}

const openDetail = async (row: SummaryRow) => {
  const uid = row.user_id ? Number(row.user_id) : effectiveFilterUserId.value
  if (uid == null) return
  showDetailModal.value = true
  detailLoading.value = true
  detailSummary.value = row
  detailHistory.value = []
  detailSessions.value = []
  try {
    const url =
      row.summary_type === 'daily'
        ? `/api/portal/memory/daily-summaries/${uid}/${row.date}`
        : `/api/portal/memory/summaries/${uid}/${row.conversation_id}`
    const res = await axios.get(url)
    detailSummary.value = res.data?.data?.summary || row
    detailHistory.value = res.data?.data?.history || []
    detailSessions.value = res.data?.data?.sessions || []
  } catch (e: any) {
    showToast(e.response?.data?.detail || '加载明细失败', 'error')
    showDetailModal.value = false
  } finally {
    detailLoading.value = false
  }
}

const closeDetailModal = () => {
  showDetailModal.value = false
  detailSummary.value = null
  detailHistory.value = []
  detailSessions.value = []
}

const requestDeleteRow = (row: SummaryRow) => {
  if (!canDelete.value) return
  rowToDelete.value = row
  showDeleteConfirm.value = true
}

const confirmDeleteRow = async () => {
  const row = rowToDelete.value
  showDeleteConfirm.value = false
  rowToDelete.value = null
  if (!row) return
  const uid = row.user_id ? Number(row.user_id) : effectiveFilterUserId.value
  if (uid == null) return
  try {
    if (row.summary_type === 'daily') {
      await axios.delete(`/api/portal/memory/daily-summaries/${uid}/${row.date}`)
    } else {
      await axios.delete(`/api/portal/memory/summaries/${uid}/${row.conversation_id}`)
    }
    showToast('已删除', 'success')
    if (
      showDetailModal.value &&
      detailSummary.value?.conversation_id === row.conversation_id
    ) {
      closeDetailModal()
    }
    await fetchMemoryData()
  } catch (e: any) {
    showToast(e.response?.data?.detail || '删除失败', 'error')
  }
}

const rebuildDaily = async (row: SummaryRow) => {
  if (!canIndex.value || !row.date) return
  const uid = row.user_id ? Number(row.user_id) : effectiveFilterUserId.value
  if (uid == null) return
  try {
    const res = await axios.post(`/api/portal/memory/daily-summaries/${uid}/${row.date}/rebuild`)
    const data = res.data?.data
    showToast(data?.refreshed ? '每日摘要已重建' : data?.reason || '重建完成', 'success')
    await fetchDailySummaries()
  } catch (e: any) {
    showToast(e.response?.data?.detail || '重建失败', 'error')
  }
}

const consolidating = ref(false)

const runConsolidation = async () => {
  if (!canIndex.value) return
  consolidating.value = true
  try {
    const payload: Record<string, unknown> = {}
    if (canViewAllUsers.value) {
      if (filterUserId.value !== '') {
        payload.user_id = Number(filterUserId.value)
      } else if (effectiveFilterUserId.value != null && filterUserId.value !== '') {
        payload.user_id = effectiveFilterUserId.value
      }
    } else {
      payload.user_id = effectiveFilterUserId.value
    }
    const res = await axios.post('/api/portal/memory/consolidate', payload)
    showToast(res.data?.message || '长时记忆整理合并已触发完成', 'success')
    await fetchMemoryData()
  } catch (e: any) {
    showToast(e.response?.data?.detail || '手动整理失败', 'error')
  } finally {
    consolidating.value = false
  }
}


const runSearchTest = async () => {
  if (!canViewAllUsers.value && effectiveSearchUserId.value == null) {
    showToast('无法确定用户 ID', 'warning')
    return
  }
  searchLoading.value = true
  try {
    const payload: Record<string, unknown> = {
      query: searchQuery.value.trim() || null,
      scope: searchScope.value,
      conversation_id: searchConvId.value.trim() || null,
      limit: searchLimit.value,
    }
    if (canViewAllUsers.value) {
      if (searchUserId.value !== '') {
        payload.user_id = Number(searchUserId.value)
      } else if (searchUsername.value.trim()) {
        payload.username = searchUsername.value.trim()
      } else if (effectiveSearchUserId.value != null) {
        payload.user_id = effectiveSearchUserId.value
      }
    } else {
      payload.user_id = effectiveSearchUserId.value
    }
    const res = await axios.post('/api/portal/memory/search-test', payload)
    searchResult.value = res.data?.data
    const tu = res.data?.data?.target_user
    if (tu?.display_name || tu?.user_id) {
      showToast(
        `已检索用户：${tu.display_name || tu.user_name || ''} (ID ${tu.user_id})`,
        'info'
      )
    }
  } catch (e: any) {
    const detail = e.response?.data?.detail
    showToast(typeof detail === 'string' ? detail : '检索失败', 'error')
  } finally {
    searchLoading.value = false
  }
}

const copyToClipboard = (text: string) => {
  if (!text) return
  navigator.clipboard.writeText(text).then(() => {
    showToast('会话 ID 已复制到剪贴板', 'success')
  }).catch(() => {
    showToast('复制失败，请手动选择复制', 'error')
  })
}

const activeHelpKey = ref<string | null>(null)
const toggleHelp = (key: string) => {
  if (activeHelpKey.value === key) {
    activeHelpKey.value = null
  } else {
    activeHelpKey.value = key
  }
}

onMounted(async () => {
  if (!canViewAllUsers.value && currentUserId.value) {
    filterUserId.value = Number(currentUserId.value)
    searchUserId.value = Number(currentUserId.value)
  }
  await runVectorTest(false)
})
</script>

<template>
  <div class="space-y-5">
    <!-- Header（与技能管理顶格一致，由 Dashboard main 统一留白） -->
    <div class="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-4">
      <div class="min-w-0">
        <h1 class="text-2xl font-bold tracking-normal text-gray-900 dark:text-white">记忆工作台</h1>
        <p class="text-sm text-gray-500 mt-1">跨会话摘要、向量检索与 Redis 会话明细管理（配置独立于系统设置）</p>
      </div>
      <div class="flex gap-1 border-b border-gray-200 dark:border-gray-700 shrink-0">
        <button
          v-for="t in [
            { id: 'config', label: '服务配置' },
            { id: 'data', label: '记忆数据' },
            { id: 'search', label: '记忆检索测试' },
          ]"
          :key="t.id"
          type="button"
          class="px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors whitespace-nowrap disabled:opacity-40 disabled:cursor-not-allowed"
          :class="activeTab === t.id ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'"
          :disabled="!memoryFeaturesEnabled && !vectorChecking"
          @click="activeTab = t.id as any"
        >
          {{ t.label }}
        </button>
      </div>
    </div>

    <!-- Redis 向量环境检测 -->
    <div
      v-if="vectorChecking"
      class="flex items-center gap-3 py-8 justify-center bg-white border border-gray-200 rounded-lg shadow-sm"
    >
      <div class="w-8 h-8 border-4 border-blue-600 border-t-transparent rounded-full animate-spin" />
      <span class="text-sm text-gray-600">正在检测 Redis 向量检索环境…</span>
    </div>

    <div
      v-else-if="!vectorReady && vectorHealth"
      class="bg-amber-50 border border-amber-200 rounded-lg p-5 shadow-sm space-y-4"
    >
      <div class="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
        <div>
          <h2 class="text-base font-semibold text-amber-900">记忆服务不可用</h2>
          <p class="text-sm text-amber-800 mt-1">{{ vectorHealth.message }}</p>
          <p class="text-xs text-amber-700 mt-2 font-mono">
            当前连接：{{ vectorHealth.redis_host }}:{{ vectorHealth.redis_port }} / db
            {{ vectorHealth.redis_db }}
          </p>
        </div>
        <button
          type="button"
          class="shrink-0 px-4 py-2 bg-amber-600 text-white rounded-lg text-sm font-medium hover:bg-amber-700 shadow-sm"
          @click="runVectorTest(true)"
        >
          重新检测
        </button>
      </div>
      <ul class="text-sm text-amber-900 list-disc pl-5 space-y-1">
        <li v-for="(hint, i) in vectorHealth.hints" :key="i">{{ hint }}</li>
      </ul>
      <div v-if="vectorHealth.checks?.length" class="border border-amber-200 rounded-lg overflow-hidden bg-white">
        <table class="w-full text-xs">
          <thead class="bg-amber-100/80">
            <tr>
              <th class="text-left p-2 font-medium">检查项</th>
              <th class="text-left p-2 font-medium">结果</th>
              <th class="text-left p-2 font-medium">说明</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in vectorHealth.checks" :key="c.name" class="border-t border-amber-100">
              <td class="p-2 font-mono">{{ c.name }}</td>
              <td class="p-2">
                <span :class="c.passed ? 'text-green-600' : 'text-red-600'">
                  {{ c.passed ? '通过' : '失败' }}
                </span>
              </td>
              <td class="p-2 text-gray-700">{{ c.message }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="text-xs text-amber-800">
        在检测通过前，本页配置、记忆数据、检索测试等功能均已禁用。线上对话中的 memory_search 摘要写入也会受影响。
      </p>
    </div>

    <div
      v-else-if="vectorReady"
      class="bg-green-50 border border-green-200 rounded-lg px-4 py-2 flex flex-wrap items-center justify-between gap-2 text-sm"
    >
      <span class="text-green-800">{{ vectorHealth?.message || 'Redis 向量环境正常' }}</span>
      <button
        type="button"
        class="text-green-700 hover:text-green-900 text-xs underline"
        @click="runVectorTest(true)"
      >
        重新检测
      </button>
    </div>

    <div
      class="relative"
      :class="{ 'opacity-50 pointer-events-none select-none': !memoryFeaturesEnabled && !vectorChecking }"
      :aria-disabled="!memoryFeaturesEnabled"
    >
    <!-- 服务配置 -->
    <div v-show="activeTab === 'config' && memoryFeaturesEnabled" class="space-y-5">
      <div class="flex flex-wrap gap-2">
        <button
          v-if="canSave"
          type="button"
          class="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 shadow-sm"
          :disabled="savingConfig"
          @click="saveConfigs"
        >
          保存配置
        </button>
        <button
          v-if="canSave && summaryEnabled"
          type="button"
          class="px-4 py-2 bg-white border border-gray-300 rounded-lg text-sm hover:bg-gray-50 shadow-sm disabled:opacity-40"
          @click="testEmbedding"
        >
          测试 Embedding
        </button>
        <button
          v-if="canIndex && summaryEnabled"
          type="button"
          class="px-4 py-2 bg-white border border-gray-300 rounded-lg text-sm hover:bg-gray-50 shadow-sm"
          @click="loadIndexStatus"
        >
          刷新索引状态
        </button>
        <button
          v-if="canIndex && summaryEnabled"
          type="button"
          class="px-4 py-2 bg-white border border-amber-200 text-amber-700 rounded-lg text-sm hover:bg-amber-50 shadow-sm"
          @click="requestRebuildIndex"
        >
          检查/创建索引
        </button>
      </div>

      <div
        v-if="summaryEnabled && indexStatus"
        class="bg-white border border-gray-200 rounded-lg px-4 py-3 shadow-sm flex flex-wrap items-center gap-3 text-sm"
      >
        <span
          class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium"
          :class="indexStatus.available ? 'bg-green-100 text-green-800' : 'bg-amber-100 text-amber-800'"
        >
          {{ indexStatus.available ? '索引正常' : '索引未就绪' }}
        </span>
        <span class="text-gray-600">{{ indexStatusLabel }}</span>
        <span v-if="indexStatus.index_name" class="text-gray-400 font-mono text-xs">{{ indexStatus.index_name }}</span>
      </div>

      <div v-if="configLoading" class="flex flex-col items-center justify-center py-16">
        <div class="w-10 h-10 border-4 border-blue-600 border-t-transparent rounded-full animate-spin" />
        <p class="text-sm text-gray-500 mt-4">加载配置中...</p>
      </div>

      <template v-else>
        <section
          v-for="group in visibleConfigGroups"
          :key="group.id"
          class="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden dark:bg-gray-800 dark:border-gray-700"
        >
          <div class="px-4 py-3 border-b border-gray-100 bg-gray-50/80 dark:bg-gray-900/40 dark:border-gray-700">
            <h3 class="text-sm font-semibold text-gray-900 dark:text-white">{{ group.title }}</h3>
            <p v-if="group.description" class="text-xs text-gray-500 mt-0.5">{{ group.description }}</p>
          </div>
          <div class="p-4 grid gap-4 sm:grid-cols-2">
            <template v-for="key in group.keys" :key="key">
              <div
                v-if="getConfigItem(key)"
                class="sm:col-span-1"
                :class="{
                  'sm:col-span-2': configFieldType(key) === 'boolean',
                  'hidden': key === 'memory_summary_enabled' && !serviceEnabled,
                }"
              >
                <!-- 布尔：胶囊开关 -->
                <div
                  v-if="configFieldType(key) === 'boolean'"
                  class="flex items-center justify-between gap-4 py-1"
                >
                  <div class="min-w-0">
                    <div class="flex items-center gap-1">
                      <span class="text-sm font-medium text-gray-800 dark:text-gray-200">{{ configLabel(key) }}</span>
                      <div class="relative group inline-flex items-center">
                        <button
                          v-if="CONFIG_TIPS[key] || getConfigItem(key)?.description"
                          type="button"
                          class="text-gray-400 hover:text-blue-500 dark:hover:text-blue-400 transition-colors p-0.5 inline-flex items-center focus:outline-none"
                        >
                          <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                          </svg>
                        </button>
                        <!-- 悬浮提示框 -->
                        <div class="absolute left-full top-1/2 -translate-y-1/2 ml-2.5 hidden group-hover:block w-72 p-3 bg-gray-900/95 dark:bg-gray-950/95 text-white text-xs rounded-xl shadow-xl border border-gray-800 dark:border-gray-800 backdrop-blur-sm z-[100] pointer-events-none">
                           <div class="leading-relaxed text-left font-normal normal-case break-words whitespace-normal font-sans">
                              {{ CONFIG_TIPS[key] || getConfigItem(key)!.description }}
                           </div>
                           <div class="absolute right-full top-1/2 -translate-y-1/2 border-4 border-transparent border-r-gray-900/95 dark:border-r-gray-950/95"></div>
                        </div>
                      </div>
                    </div>
                    <p class="text-xs text-gray-400 mt-0.5 font-mono">{{ key }}</p>
                  </div>
                  <label class="relative inline-flex items-center cursor-pointer shrink-0">
                    <input
                      type="checkbox"
                      class="sr-only peer"
                      :checked="isConfigTrue(key)"
                      :disabled="!canSave"
                      @change="setConfigBool(key, ($event.target as HTMLInputElement).checked)"
                    />
                    <div
                      class="w-11 h-6 bg-gray-200 rounded-full peer peer-checked:bg-blue-600 peer-disabled:opacity-50 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-blue-300 after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:after:translate-x-full"
                    />
                  </label>
                </div>

                <!-- 数字 -->
                <div v-else-if="configFieldType(key) === 'number'" class="space-y-1">
                  <div class="flex items-center gap-1">
                    <label class="block text-sm font-medium text-gray-800 dark:text-gray-200">{{ configLabel(key) }}</label>
                    <div class="relative group inline-flex items-center">
                      <button
                        v-if="CONFIG_TIPS[key] || getConfigItem(key)?.description"
                        type="button"
                        class="text-gray-400 hover:text-blue-500 dark:hover:text-blue-400 transition-colors p-0.5 inline-flex items-center focus:outline-none"
                      >
                        <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                          <path stroke-linecap="round" stroke-linejoin="round" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                      </button>
                      <!-- 悬浮提示框 -->
                      <div class="absolute left-full top-1/2 -translate-y-1/2 ml-2.5 hidden group-hover:block w-72 p-3 bg-gray-900/95 dark:bg-gray-950/95 text-white text-xs rounded-xl shadow-xl border border-gray-800 dark:border-gray-800 backdrop-blur-sm z-[100] pointer-events-none">
                         <div class="leading-relaxed text-left font-normal normal-case break-words whitespace-normal font-sans">
                            {{ CONFIG_TIPS[key] || getConfigItem(key)!.description }}
                         </div>
                         <div class="absolute right-full top-1/2 -translate-y-1/2 border-4 border-transparent border-r-gray-900/95 dark:border-r-gray-950/95"></div>
                      </div>
                    </div>
                  </div>
                  <p class="text-xs text-gray-400 font-mono">{{ key }}</p>
                  <input
                    type="number"
                    min="0"
                    step="1"
                    class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-blue-500 focus:outline-none disabled:bg-gray-50 dark:bg-gray-900 dark:border-gray-600"
                    :value="getConfigNumber(key)"
                    :disabled="!canSave"
                    @input="setConfigNumber(key, ($event.target as HTMLInputElement).value)"
                  />
                </div>

                <!-- 密钥 -->
                <div v-else-if="configFieldType(key) === 'secret'" class="space-y-1">
                  <div class="flex items-center gap-1">
                    <label class="block text-sm font-medium text-gray-800 dark:text-gray-200">{{ configLabel(key) }}</label>
                    <div class="relative group inline-flex items-center">
                      <button
                        v-if="CONFIG_TIPS[key] || getConfigItem(key)?.description"
                        type="button"
                        class="text-gray-400 hover:text-blue-500 dark:hover:text-blue-400 transition-colors p-0.5 inline-flex items-center focus:outline-none"
                      >
                        <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                          <path stroke-linecap="round" stroke-linejoin="round" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                      </button>
                      <!-- 悬浮提示框 -->
                      <div class="absolute left-full top-1/2 -translate-y-1/2 ml-2.5 hidden group-hover:block w-72 p-3 bg-gray-900/95 dark:bg-gray-950/95 text-white text-xs rounded-xl shadow-xl border border-gray-800 dark:border-gray-800 backdrop-blur-sm z-[100] pointer-events-none">
                         <div class="leading-relaxed text-left font-normal normal-case break-words whitespace-normal font-sans">
                            {{ CONFIG_TIPS[key] || getConfigItem(key)!.description }}
                         </div>
                         <div class="absolute right-full top-1/2 -translate-y-1/2 border-4 border-transparent border-r-gray-900/95 dark:border-r-gray-950/95"></div>
                      </div>
                    </div>
                  </div>
                  <p class="text-xs text-gray-400 font-mono">{{ key }}</p>
                  <div class="flex gap-2">
                    <input
                      v-model="getConfigItem(key)!.value"
                      :type="showSecrets[key] ? 'text' : 'password'"
                      :disabled="!canSave"
                      class="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none dark:bg-gray-900 dark:border-gray-600"
                      placeholder="留空则回退系统 LLM Key"
                    />
                    <button
                      type="button"
                      class="px-3 text-xs text-blue-600 border border-gray-200 rounded-lg hover:bg-gray-50"
                      @click="showSecrets[key] = !showSecrets[key]"
                    >
                      {{ showSecrets[key] ? '隐藏' : '显示' }}
                    </button>
                  </div>
                </div>

                <!-- 文本 -->
                <div v-else class="space-y-1">
                  <div class="flex items-center gap-1">
                    <label class="block text-sm font-medium text-gray-800 dark:text-gray-200">{{ configLabel(key) }}</label>
                    <div class="relative group inline-flex items-center">
                      <button
                        v-if="CONFIG_TIPS[key] || getConfigItem(key)?.description"
                        type="button"
                        class="text-gray-400 hover:text-blue-500 dark:hover:text-blue-400 transition-colors p-0.5 inline-flex items-center focus:outline-none"
                      >
                        <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                          <path stroke-linecap="round" stroke-linejoin="round" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                      </button>
                      <!-- 悬浮提示框 -->
                      <div class="absolute left-full top-1/2 -translate-y-1/2 ml-2.5 hidden group-hover:block w-72 p-3 bg-gray-900/95 dark:bg-gray-950/95 text-white text-xs rounded-xl shadow-xl border border-gray-800 dark:border-gray-800 backdrop-blur-sm z-[100] pointer-events-none">
                         <div class="leading-relaxed text-left font-normal normal-case break-words whitespace-normal font-sans">
                            {{ CONFIG_TIPS[key] || getConfigItem(key)!.description }}
                         </div>
                         <div class="absolute right-full top-1/2 -translate-y-1/2 border-4 border-transparent border-r-gray-900/95 dark:border-r-gray-950/95"></div>
                      </div>
                    </div>
                  </div>
                  <p class="text-xs text-gray-400 font-mono">{{ key }}</p>
                  <input
                    v-model="getConfigItem(key)!.value"
                    type="text"
                    :disabled="!canSave"
                    class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:outline-none dark:bg-gray-900 dark:border-gray-600"
                  />
                </div>


              </div>
            </template>
          </div>
        </section>

        <p v-if="!serviceEnabled" class="text-sm text-gray-500 bg-gray-50 border border-gray-200 rounded-lg px-4 py-3">
          记忆服务总开关已关闭，保存后对话将不再写入摘要，memory_search 工具将提示服务未启用。
        </p>
        <p v-else-if="!summaryEnabled" class="text-sm text-gray-500 bg-gray-50 border border-gray-200 rounded-lg px-4 py-3">
          摘要写入已关闭，仅保留检索与会话存储相关配置；已有摘要数据只读，不再更新。
        </p>
      </template>
    </div>

    <!-- 记忆数据 -->
    <div v-show="activeTab === 'data' && memoryFeaturesEnabled" class="space-y-4">
      <div v-if="!canViewData" class="text-gray-500 text-sm">无「查看记忆数据」权限</div>
      <template v-else>
        <div class="flex flex-col gap-3">
          <div class="flex items-center gap-3">
            <div class="inline-flex w-fit rounded-lg border border-gray-200 bg-white p-1 shadow-sm">
              <button
                v-for="view in [
                  { id: 'daily', label: '每日摘要' },
                  { id: 'session', label: '会话摘要' },
                ]"
                :key="view.id"
                type="button"
                class="px-3 py-1.5 text-sm font-medium rounded-md transition-colors"
                :class="dataView === view.id ? 'bg-blue-600 text-white' : 'text-gray-600 hover:bg-gray-50'"
                @click="switchDataView(view.id as 'daily' | 'session')"
              >
                {{ view.label }}
              </button>
            </div>

            <!-- 手动触发记忆降噪按钮 -->
            <button
              v-if="dataView === 'session' && canIndex"
              type="button"
              class="px-3.5 py-2 text-sm font-medium bg-emerald-50 text-emerald-700 border border-emerald-200 rounded-lg hover:bg-emerald-100 transition-colors flex items-center gap-1.5 shadow-sm disabled:opacity-50"
              :disabled="consolidating"
              @click="runConsolidation"
            >
              <span v-if="consolidating" class="w-4 h-4 border-2 border-emerald-700 border-t-transparent rounded-full animate-spin" />
              <span v-else>🧠</span>
              {{ consolidating ? '整理合并中…' : '一键整理合并' }}
            </button>
          </div>
          <div class="flex flex-wrap gap-3 items-end">
            <div v-if="canViewAllUsers">
              <label class="text-xs text-gray-500 block mb-1">用户 ID</label>
              <input
                v-model.number="filterUserId"
                type="number"
                class="border border-gray-300 rounded-lg px-2 py-2 text-sm w-28 bg-white shadow-sm dark:bg-gray-800"
                placeholder="可选"
              />
            </div>
            <div v-if="canViewAllUsers">
              <label class="text-xs text-gray-500 block mb-1">用户名</label>
              <input
                v-model="filterUsername"
                class="border border-gray-300 rounded-lg px-2 py-2 text-sm w-36 bg-white shadow-sm dark:bg-gray-800"
                placeholder="登录名/姓名"
              />
            </div>
            <div v-if="dataView === 'daily'">
              <label class="text-xs text-gray-500 block mb-1">开始日期</label>
              <input
                v-model="filterDateFrom"
                type="date"
                class="border border-gray-300 rounded-lg px-2 py-2 text-sm bg-white shadow-sm dark:bg-gray-800"
              />
            </div>
            <div v-if="dataView === 'daily'">
              <label class="text-xs text-gray-500 block mb-1">结束日期</label>
              <input
                v-model="filterDateTo"
                type="date"
                class="border border-gray-300 rounded-lg px-2 py-2 text-sm bg-white shadow-sm dark:bg-gray-800"
              />
            </div>
            <div class="flex-1 min-w-[12rem]">
              <label class="text-xs text-gray-500 block mb-1">关键词</label>
              <input
                v-model="filterKeyword"
                class="w-full border border-gray-300 rounded-lg px-2 py-2 text-sm bg-white shadow-sm dark:bg-gray-800"
                :placeholder="dataView === 'daily' ? '主题 / 摘要 / 日期' : '标题 / 摘要 / 会话 ID'"
                @keyup.enter="fetchMemoryData"
              />
            </div>
            <button
              type="button"
              class="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 shadow-sm disabled:opacity-50"
              :disabled="dataLoading"
              @click="fetchMemoryData"
            >
              {{ dataLoading ? '查询中…' : '查询' }}
            </button>
          </div>
        </div>

        <div class="w-full bg-white border border-gray-200 rounded-lg overflow-hidden shadow-sm dark:border-gray-700">
          <div v-if="dataLoading" class="flex justify-center py-16">
            <div class="w-8 h-8 border-4 border-blue-600 border-t-transparent rounded-full animate-spin" />
          </div>
          <div v-else class="overflow-x-auto">
            <table v-if="dataView === 'daily'" class="w-full text-sm min-w-[760px]">
              <thead class="bg-gray-50 dark:bg-gray-800 text-gray-600">
                <tr>
                  <th v-if="canViewAllUsers" class="text-left px-3 py-2.5 font-medium whitespace-nowrap">用户</th>
                  <th class="text-left px-3 py-2.5 font-medium whitespace-nowrap">日期</th>
                  <th class="text-left px-3 py-2.5 font-medium">今日主题</th>
                  <th class="text-left px-3 py-2.5 font-medium whitespace-nowrap">会话数</th>
                  <th class="text-left px-3 py-2.5 font-medium whitespace-nowrap">向量</th>
                  <th class="text-left px-3 py-2.5 font-medium whitespace-nowrap">最后刷新</th>
                  <th class="text-left px-3 py-2.5 font-medium w-40">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="row in dailySummaries"
                  :key="`${row.user_id}-${row.date}`"
                  class="border-t border-gray-100 dark:border-gray-700 hover:bg-gray-50/80 dark:hover:bg-gray-800/50"
                >
                  <td v-if="canViewAllUsers" class="px-3 py-2.5 text-xs whitespace-nowrap">
                    <div class="font-medium text-gray-900">{{ row.display_name || row.user_name || '-' }}</div>
                    <div class="text-gray-400 font-mono">ID {{ row.user_id }}</div>
                  </td>
                  <td class="px-3 py-2.5 font-mono text-xs text-gray-600 whitespace-nowrap">{{ row.date || '-' }}</td>
                  <td class="px-3 py-2.5 max-w-xl">
                    <div class="font-medium text-gray-900 truncate" :title="row.title">{{ row.title || '-' }}</div>
                    <div v-if="row.summary" class="text-xs text-gray-500 truncate mt-0.5" :title="row.summary">
                      {{ row.summary }}
                    </div>
                  </td>
                  <td class="px-3 py-2.5 text-xs text-gray-600 whitespace-nowrap">{{ row.session_count || 0 }}</td>
                  <td class="px-3 py-2.5 whitespace-nowrap">
                    <span
                      class="inline-flex px-2 py-0.5 rounded-full text-xs font-medium"
                      :class="row.has_embedding ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'"
                    >
                      {{ row.has_embedding ? '已向量化' : '无向量' }}
                    </span>
                  </td>
                  <td class="px-3 py-2.5 text-xs text-gray-500 whitespace-nowrap">{{ formatTime(row.last_active) }}</td>
                  <td class="px-3 py-2.5 whitespace-nowrap">
                    <button type="button" class="text-blue-600 text-xs mr-3 hover:underline" @click="openDetail(row)">
                      详情
                    </button>
                    <button
                      v-if="canIndex"
                      type="button"
                      class="text-amber-600 text-xs mr-3 hover:underline"
                      @click="rebuildDaily(row)"
                    >
                      重建
                    </button>
                    <button
                      v-if="canDelete"
                      type="button"
                      class="text-red-600 text-xs hover:underline"
                      @click="requestDeleteRow(row)"
                    >
                      删除摘要
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
            <table v-else class="w-full text-sm min-w-[720px]">
              <thead class="bg-gray-50 dark:bg-gray-800 text-gray-600">
                <tr>
                  <th v-if="canViewAllUsers" class="text-left px-3 py-2.5 font-medium whitespace-nowrap">用户</th>
                  <th class="text-left px-3 py-2.5 font-medium">标题</th>
                  <th class="text-left px-3 py-2.5 font-medium whitespace-nowrap">会话 ID</th>
                  <th class="text-left px-3 py-2.5 font-medium whitespace-nowrap">向量</th>
                  <th class="text-left px-3 py-2.5 font-medium whitespace-nowrap">History</th>
                  <th class="text-left px-3 py-2.5 font-medium whitespace-nowrap">引用</th>
                  <th class="text-left px-3 py-2.5 font-medium whitespace-nowrap">最后活跃</th>
                  <th class="text-left px-3 py-2.5 font-medium w-28">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="row in summaries"
                  :key="`${row.user_id}-${row.conversation_id}`"
                  class="border-t border-gray-100 dark:border-gray-700 hover:bg-gray-50/80 dark:hover:bg-gray-800/50"
                >
                  <td v-if="canViewAllUsers" class="px-3 py-2.5 text-xs whitespace-nowrap">
                    <div class="font-medium text-gray-900">{{ row.display_name || row.user_name || '-' }}</div>
                    <div class="text-gray-400 font-mono">ID {{ row.user_id }}</div>
                  </td>
                  <td class="px-3 py-2.5 max-w-md">
                    <div class="font-medium text-gray-900 truncate" :title="row.title">{{ row.title || '-' }}</div>
                    <div v-if="row.summary" class="text-xs text-gray-500 truncate mt-0.5" :title="row.summary">
                      {{ row.summary }}
                    </div>
                  </td>
                   <td class="px-3 py-2.5 whitespace-nowrap">
                    <div class="flex items-center gap-1.5">
                      <span class="font-mono text-xs text-gray-500 dark:text-gray-400" :title="row.conversation_id">
                        {{ row.conversation_id ? `${row.conversation_id.slice(0, 8)}...` : '-' }}
                      </span>
                      <button
                        v-if="row.conversation_id"
                        type="button"
                        class="text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 p-0.5 rounded transition-colors"
                        title="复制完整会话 ID"
                        @click.stop="copyToClipboard(row.conversation_id)"
                      >
                        <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                          <path stroke-linecap="round" stroke-linejoin="round" d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3" />
                        </svg>
                      </button>
                    </div>
                  </td>
                  <td class="px-3 py-2.5 whitespace-nowrap">
                    <span
                      class="inline-flex px-2 py-0.5 rounded-full text-xs font-medium"
                      :class="row.has_embedding ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'"
                    >
                      {{ row.has_embedding ? '已向量化' : '无向量' }}
                    </span>
                  </td>
                  <td class="px-3 py-2.5 whitespace-nowrap">
                    <span
                      class="inline-flex px-2 py-0.5 rounded-full text-xs font-medium"
                      :class="row.has_history ? 'bg-blue-100 text-blue-800' : 'bg-gray-100 text-gray-500'"
                    >
                      {{ row.has_history ? '有' : '无' }}
                    </span>
                  </td>
                  <td class="px-3 py-2.5 whitespace-nowrap">
                    <span class="text-xs text-gray-700 dark:text-gray-300 font-mono font-semibold bg-gray-100 dark:bg-gray-700/80 px-2 py-0.5 rounded">
                      {{ row.reference_count ?? 0 }} 次
                    </span>
                  </td>
                  <td class="px-3 py-2.5 text-xs text-gray-500 whitespace-nowrap">{{ formatTime(row.last_active) }}</td>
                  <td class="px-3 py-2.5 whitespace-nowrap">
                    <button type="button" class="text-blue-600 text-xs mr-3 hover:underline" @click="openDetail(row)">
                      明细
                    </button>
                    <button
                      v-if="canDelete"
                      type="button"
                      class="text-red-600 text-xs hover:underline"
                      @click="requestDeleteRow(row)"
                    >
                      删除摘要
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
            <p
              v-if="(dataView === 'daily' ? dailySummaries.length : summaries.length) === 0"
              class="p-8 text-center text-gray-400 text-sm"
            >
              {{ dataView === 'daily' ? '暂无每日摘要（需先生成会话摘要）' : '暂无会话摘要（需对话结束后异步生成）' }}
            </p>
          </div>
        </div>
      </template>
    </div>

    <!-- 检索测试 -->
    <div v-show="activeTab === 'search' && memoryFeaturesEnabled" class="space-y-4">
      <div v-if="!canTestSearch" class="text-gray-500 text-sm">无「记忆检索测试」权限</div>
      <template v-else>
        <div class="flex flex-wrap gap-3 items-end">
          <div v-if="canViewAllUsers">
            <label class="text-xs text-gray-500 block mb-1">用户 ID</label>
            <input
              v-model.number="searchUserId"
              type="number"
              class="border border-gray-300 rounded-lg px-2 py-2 text-sm w-28 bg-white shadow-sm dark:bg-gray-800"
              placeholder="可选"
            />
          </div>
          <div v-if="canViewAllUsers">
            <label class="text-xs text-gray-500 block mb-1">用户名</label>
            <input
              v-model="searchUsername"
              class="border border-gray-300 rounded-lg px-2 py-2 text-sm w-36 bg-white shadow-sm dark:bg-gray-800"
              placeholder="登录名/姓名"
            />
          </div>
          <div>
            <label class="text-xs text-gray-500 block mb-1">scope</label>
            <select
              v-model="searchScope"
              class="border border-gray-300 rounded-lg px-2 py-2 text-sm w-32 bg-white shadow-sm dark:bg-gray-800"
            >
              <option value="summary">summary</option>
              <option value="history">history</option>
              <option value="both">both</option>
            </select>
          </div>
          <div>
            <label class="text-xs text-gray-500 block mb-1">conversation_id</label>
            <input
              v-model="searchConvId"
              class="border border-gray-300 rounded-lg px-2 py-2 text-sm w-56 bg-white shadow-sm dark:bg-gray-800 font-mono text-xs"
            />
          </div>
          <div>
            <label class="text-xs text-gray-500 block mb-1">limit</label>
            <input
              v-model.number="searchLimit"
              type="number"
              min="1"
              class="border border-gray-300 rounded-lg px-2 py-2 text-sm w-20 bg-white shadow-sm dark:bg-gray-800"
            />
          </div>
          <button
            type="button"
            class="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 shadow-sm disabled:opacity-50"
            :disabled="searchLoading"
            @click="runSearchTest"
          >
            {{ searchLoading ? '检索中…' : '执行检索' }}
          </button>
        </div>
        <div class="w-full">
          <label class="text-xs text-gray-500 block mb-1">检索问题</label>
          <textarea
            v-model="searchQuery"
            rows="2"
            class="w-full border border-gray-300 rounded-lg px-2 py-2 text-sm bg-white shadow-sm dark:bg-gray-800"
            placeholder="例如：我们最近聊了什么"
            @keyup.enter.ctrl="runSearchTest"
          />
        </div>
        <pre
          v-if="searchResult"
          class="text-xs bg-white border border-gray-200 p-4 rounded-lg overflow-auto max-h-96 shadow-sm"
        >{{ JSON.stringify(searchResult, null, 2) }}</pre>
      </template>
    </div>
    </div>

    <Modal
      :show="showDetailModal"
      :title="detailSummary?.summary_type === 'daily'
        ? `每日摘要 · ${detailSummary.date || ''}`
        : (detailSummary?.title ? `会话明细 · ${detailSummary.title}` : '会话明细')"
      size="max-w-4xl"
      @close="closeDetailModal"
    >
      <div v-if="detailLoading" class="flex justify-center py-12">
        <div class="w-8 h-8 border-4 border-blue-600 border-t-transparent rounded-full animate-spin" />
      </div>
      <div v-else class="space-y-4">
        <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-3 text-xs">
          <div>
            <span class="text-gray-400 block">用户</span>
            <span class="font-medium text-gray-900 dark:text-gray-200">{{ detailSummary?.display_name || detailSummary?.user_name || '-' }}</span>
          </div>
          <div>
            <span class="text-gray-400 block">用户 ID</span>
            <span class="font-mono text-gray-900 dark:text-gray-200">{{ detailSummary?.user_id }}</span>
          </div>
          <div>
            <span class="text-gray-400 block">向量</span>
            <span :class="detailSummary?.has_embedding ? 'text-green-600 font-semibold' : 'text-gray-500'">
              {{ detailSummary?.has_embedding ? '已向量化' : '无向量' }}
            </span>
          </div>
          <div>
            <span class="text-gray-400 block">被引用</span>
            <span class="font-mono text-gray-900 dark:text-gray-200 font-semibold">
              {{ detailSummary?.summary_type === 'daily' ? '-' : `${detailSummary?.reference_count ?? 0} 次` }}
            </span>
          </div>
          <div>
            <span class="text-gray-400 block">对话轮数</span>
            <span class="font-mono text-gray-900 dark:text-gray-200">
              {{ detailSummary?.summary_type === 'daily' ? `${detailSummary?.session_count ?? 0} 个会话` : `${detailSummary?.turn_count ?? 0} 轮` }}
            </span>
          </div>
          <div>
            <span class="text-gray-400 block">{{ detailSummary?.summary_type === 'daily' ? '最后刷新' : '最后活跃' }}</span>
            <span class="text-gray-900 dark:text-gray-200">{{ formatTime(detailSummary?.last_active) }}</span>
          </div>
        </div>
        <div v-if="detailSummary?.summary_type !== 'daily'">
          <span class="text-xs text-gray-400">会话 ID</span>
          <p class="text-xs font-mono text-gray-700 break-all mt-0.5">{{ detailSummary?.conversation_id }}</p>
        </div>
        <div v-if="detailSummary?.summary">
          <h4 class="text-sm font-semibold text-gray-800 mb-1">
            {{ detailSummary?.summary_type === 'daily' ? '今日概览' : '摘要' }}
          </h4>
          <p class="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">{{ detailSummary.summary }}</p>
        </div>
        <div
          v-for="section in [
            { label: detailSummary?.summary_type === 'daily' ? '主题' : '关键事实', value: detailSummary?.summary_type === 'daily' ? detailSummary?.topics : detailSummary?.key_facts },
            { label: '已确认决策', value: detailSummary?.decisions },
            { label: '未完成事项', value: detailSummary?.open_items },
            { label: '实体/关键词', value: detailSummary?.entities },
          ]"
          :key="section.label"
          v-show="formatList(section.value).length > 0"
        >
          <h4 class="text-sm font-semibold text-gray-800 mb-2">{{ section.label }}</h4>
          <div class="flex flex-wrap gap-2">
            <span
              v-for="item in formatList(section.value)"
              :key="item"
              class="inline-flex rounded-full bg-gray-100 px-2.5 py-1 text-xs text-gray-700"
            >
              {{ item }}
            </span>
          </div>
        </div>
        <div v-if="detailSummary?.summary_type === 'daily'">
          <h4 class="text-sm font-semibold text-gray-800 mb-2">
            关联会话
            <span class="text-gray-400 font-normal">（{{ detailSessions.length }} 条）</span>
          </h4>
          <div v-if="detailSessions.length === 0" class="text-sm text-gray-400">当天暂无关联会话摘要</div>
          <div v-else class="border border-gray-100 rounded-lg overflow-hidden">
            <button
              v-for="session in detailSessions"
              :key="session.conversation_id"
              type="button"
              class="w-full text-left px-3 py-2.5 border-b border-gray-100 last:border-b-0 hover:bg-gray-50"
              @click="openDetail({ ...session, user_id: detailSummary?.user_id, display_name: detailSummary?.display_name, user_name: detailSummary?.user_name })"
            >
              <div class="flex items-center justify-between gap-3">
                <span class="font-medium text-sm text-gray-900 truncate">{{ session.title || session.conversation_id }}</span>
                <span
                  class="shrink-0 text-xs px-2 py-0.5 rounded-full"
                  :class="session.has_history ? 'bg-blue-100 text-blue-800' : 'bg-gray-100 text-gray-500'"
                >
                  {{ session.has_history ? '有 history' : '无 history' }}
                </span>
              </div>
              <p v-if="session.summary" class="text-xs text-gray-500 truncate mt-0.5">{{ session.summary }}</p>
              <p class="text-xs font-mono text-gray-400 mt-1">{{ session.conversation_id }}</p>
            </button>
          </div>
        </div>
        <div v-else>
          <h4 class="text-sm font-semibold text-gray-800 mb-2">
            会话消息
            <span class="text-gray-400 font-normal">（{{ detailHistory.length }} 条）</span>
          </h4>
          <div v-if="detailHistory.length === 0" class="text-sm text-gray-400">无 Redis 历史明细</div>
          <div v-else class="space-y-3 border border-gray-100 rounded-lg p-3 bg-gray-50/50 max-h-80 overflow-y-auto">
            <div v-for="(m, i) in detailHistory" :key="i" class="text-sm">
              <span
                class="inline-block px-1.5 py-0.5 rounded text-xs font-medium mr-2"
                :class="m.role === 'user' ? 'bg-blue-100 text-blue-800' : 'bg-gray-200 text-gray-700'"
              >
                {{ m.role }}
              </span>
              <span class="text-gray-700 whitespace-pre-wrap break-words">{{ m.content }}</span>
            </div>
          </div>
        </div>
      </div>
    </Modal>

    <ConfirmModal
      v-if="showRebuildConfirm"
      title="检查/创建索引"
      message="将检查或创建 RediSearch 会话摘要向量索引。若已修改向量维度，请确认后执行并必要时重建已有数据。"
      confirm-text="确定执行"
      type="warning"
      @confirm="confirmRebuildIndex"
      @cancel="showRebuildConfirm = false"
    />
    <ConfirmModal
      v-if="showDeleteConfirm && rowToDelete"
      title="删除摘要"
      :message="rowToDelete.summary_type === 'daily'
        ? `确定删除 ${rowToDelete.date} 的每日摘要？关联会话摘要和 Redis 历史记录不会在这里删除。`
        : `确定删除会话 ${rowToDelete.conversation_id} 的摘要？Redis 历史记录不会在这里删除。`"
      confirm-text="确认删除"
      type="danger"
      @confirm="confirmDeleteRow"
      @cancel="showDeleteConfirm = false; rowToDelete = null"
    />
  </div>
</template>
