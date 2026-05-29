<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import axios from '@/utils/axios'
import { useToast } from '../composables/useToast'
import { useUser } from '../composables/useUser'
import { modelApi, type AIModel } from '../api/model'
import ModelRegistry from '../components/system/ModelRegistry.vue'
import ToolRegistry from '../components/system/ToolRegistry.vue'
import McpServerRegistry from '../components/system/McpServerRegistry.vue'
import RagFlowResourceSelector from '../components/RagFlowResourceSelector.vue'
import ConfirmModal from '../components/ConfirmModal.vue'
import {
  CircleStackIcon,
  CheckCircleIcon,
  XCircleIcon,
  CommandLineIcon,
  MagnifyingGlassIcon,
  Cog6ToothIcon,
  EyeIcon,
  EyeSlashIcon,
  CpuChipIcon,
  AdjustmentsHorizontalIcon,
  SparklesIcon,
  WrenchScrewdriverIcon,
  TrashIcon,
  ServerStackIcon,
  PlayIcon
} from '@heroicons/vue/24/outline'

const { hasPermission } = useUser()
const canSave = hasPermission('element:system:config_save')

const activeTab = ref<'diagnostics' | 'configs' | 'models' | 'tools' | 'mcp'>('configs')

// --- Diagnostics Logic ---
const logs = ref<string[]>([])
const loading = ref<{ [key: string]: boolean }> ({
  redis: false,
  redis_scan: false,
  redis_flush: false,
  redis_vector: false
})
const results = ref<{ [key: string]: 'success' | 'failed' | null }> ({
  redis: null,
  redis_vector: null
})

type VectorHealthCheck = {
  name: string
  passed: boolean
  message: string
}

type VectorHealth = {
  ok: boolean
  message: string
  hints?: string[]
  redis_host?: string
  redis_port?: number
  redis_db?: number
  checks?: VectorHealthCheck[]
}

const redisVectorHealth = ref<VectorHealth | null>(null)

const { showToast } = useToast()
const showClearConfirm = ref(false)

const appendLog = (msg: string) => {
  const timestamp = new Date().toLocaleTimeString()
  logs.value.push(`[${timestamp}] ${msg}`)
}

const testConnection = async (component: string) => {
  loading.value[component] = true
  results.value[component] = null
  appendLog(`>>> 开始测试 ${component} 连接...`)

  try {
    const response = await axios.post(`/api/portal/system/test-connection/${component}`)
    const data = response.data

    // Append server logs
    if (data.logs && Array.isArray(data.logs)) {
      data.logs.forEach((log: string) => appendLog(log))
    }

    if (data.status === 'success') {
      results.value[component] = 'success'
      showToast(`${component} 连接成功`, 'success')
      appendLog(`>>> ✅ ${component} 测试通过`)
    } else if (data.status === 'skipped') {
      results.value[component] = null
      showToast(`${component} 已跳过`, 'info')
      appendLog(`>>> ⚠️ ${component} 测试跳过: ${data.message}`)
    } else {
      results.value[component] = 'failed'
      showToast(`${component} 连接失败`, 'error')
      appendLog(`>>> ❌ ${component} 测试失败: ${data.message}`)
    }
  } catch (error: any) {
    results.value[component] = 'failed'
    const msg = error.response?.data?.detail || error.message
    showToast(`测试请求失败: ${msg}`, 'error')
    appendLog(`>>> ❌ 请求异常: ${msg}`)
  } finally {
    loading.value[component] = false
  }
}

const scanRedisKeys = async () => {
  loading.value['redis_scan'] = true
  appendLog('>>> 开始扫描 Redis Keys...')
  try {
     const response = await axios.post('/api/portal/system/redis/keys')
     const { count, keys } = response.data
     appendLog(`>>> 📊 Redis Keys 总数: ${count}`)
     appendLog('>>> ----------------------------')
     if (keys.length === 0) {
         appendLog('>>> (无数据)')
     } else {
         keys.forEach((k: string, i: number) => {
             appendLog(`${i+1}. ${k}`)
         })
     }
     appendLog('>>> ----------------------------')
     appendLog('>>> ✅ 扫描完成')
     showToast('扫描成功', 'success')
  } catch (e: any) {
    const msg = e.response?.data?.detail || e.message
    appendLog(`>>> ❌ 扫描失败: ${msg}`)
    showToast('扫描失败', 'error')
  } finally {
    loading.value['redis_scan'] = false
  }
}

const testRedisVectorSearch = async (force = true) => {
  loading.value.redis_vector = true
  results.value.redis_vector = null
  appendLog('>>> 开始检测 Redis 向量搜索能力...')

  try {
    const response = await axios.get('/api/portal/memory/redis-vector-test', {
      params: force ? { force: true } : {},
    })
    const data = response.data?.data as VectorHealth
    redisVectorHealth.value = data
    results.value.redis_vector = data?.ok ? 'success' : 'failed'

    appendLog(`>>> ${data?.ok ? '✅' : '❌'} ${data?.message || 'Redis 向量搜索检测完成'}`)
    if (data?.redis_host) {
      appendLog(`>>> 连接: ${data.redis_host}:${data.redis_port ?? '-'} / db ${data.redis_db ?? '-'}`)
    }
    if (data?.checks?.length) {
      data.checks.forEach((check) => {
        appendLog(`>>> ${check.passed ? '✅' : '❌'} ${check.name}: ${check.message}`)
      })
    }

    showToast(data?.ok ? 'Redis 向量搜索可用' : 'Redis 向量搜索不可用', data?.ok ? 'success' : 'error')
  } catch (e: any) {
    const detail = e.response?.data?.detail
    const data =
      typeof detail === 'object' && detail !== null
        ? (detail as VectorHealth)
        : {
            ok: false,
            message: detail || e.message || 'Redis 向量搜索检测失败',
            hints: ['请确认 Redis Stack / RediSearch 模块已启用，并检查 Redis 连接配置。'],
            checks: [],
          }
    redisVectorHealth.value = data
    results.value.redis_vector = 'failed'
    appendLog(`>>> ❌ ${data.message}`)
    showToast('Redis 向量搜索检测失败', 'error')
  } finally {
    loading.value.redis_vector = false
  }
}

const openClearConfirm = () => {
  showClearConfirm.value = true
}

const executeClearKeys = async () => {
  loading.value['redis_flush'] = true
  showClearConfirm.value = false
  appendLog('>>> 正在清空 Redis Keys...')

  try {
     const response = await axios.post('/api/portal/system/redis/flush')
     const { message } = response.data
     appendLog(`>>> ✅ ${message}`)
     showToast('Redis 已清空', 'success')

     // 自动重新扫描
     scanRedisKeys()
  } catch (e: any) {
    const msg = e.response?.data?.detail || e.message
    appendLog(`>>> ❌ 清空失败: ${msg}`)
    showToast('清空失败', 'error')
  } finally {
    loading.value['redis_flush'] = false
  }
}

const clearLogs = () => {
  logs.value = []
}

// --- Model Data for Param Configs ---
const models = ref<AIModel[]>([])
const fetchModelsForConfigs = async () => {
    try {
        const res = await modelApi.list()
        models.value = res.data
    } catch (e: any) {
        console.error('Failed to fetch models for config dropdown')
    }
}

// --- Config Logic ---
interface ConfigItem {
  key: string
  value: string
  description: string
  is_secret: boolean
}

const configGroups = ref<{ [category: string]: ConfigItem[] }>({})
const orderedCategories = computed(() => {
  if (!configGroups.value) return []
  const order = ['agent', 'data_api', 'metadata', 'general']
  const keys = Object.keys(configGroups.value)
  return keys.sort((a, b) => {
    const idxA = order.indexOf(a)
    const idxB = order.indexOf(b)
    if (idxA !== -1 && idxB !== -1) return idxA - idxB
    if (idxA !== -1) return -1
    if (idxB !== -1) return 1
    return a.localeCompare(b)
  })
})
const originalConfigs = ref<{ [key: string]: string }>({})
const configLoading = ref(false)
const saving = ref(false)
const showSecrets = ref<{ [key: string]: boolean }>({})

const fetchConfigs = async () => {
  configLoading.value = true
  try {
    const res = await axios.get('/api/portal/system/configs')
    configGroups.value = res.data
    originalConfigs.value = {}
    for (const cat in res.data) {
        res.data[cat].forEach((item: ConfigItem) => {
            originalConfigs.value[item.key] = item.value
        })
    }
  } catch (e: any) {
    showToast('获取系统配置失败', 'error')
  } finally {
    configLoading.value = false
  }
}

const saveConfigs = async () => {
  saving.value = true
  try {
    const updates: ConfigItem[] = []
    for (const cat in configGroups.value) {
      const items = configGroups.value[cat]
      if (items) {
          items.forEach(item => {
              if (item.value !== originalConfigs.value[item.key]) {
                  updates.push(item)
              }
          })
      }
    }
    if (updates.length === 0) {
        showToast('没有检测到配置变更', 'info')
        saving.value = false
        return
    }
    await axios.put('/api/portal/system/configs', { updates })
    showToast(`成功更新 ${updates.length} 项配置`, 'success')
    await fetchConfigs()
  } catch (e: any) {
     showToast(`保存失败: ${e.response?.data?.detail || e.message}`, 'error')
  } finally {
    saving.value = false
  }
}

const toggleSecret = (key: string) => {
  showSecrets.value[key] = !showSecrets.value[key]
}

const getCategoryLabel = (cat: string) => {
  const map: Record<string, string> = {
    'data_api': '智能报表 (ChatBI)',
    'metadata': '元数据与 RAG 设置 (Metadata & RAG)',
    'agent': '智能体设置 (AI Agent)',
    'general': '常规设置 (General Settings)',
    'other': '其他参数 (Other Parameters)'
  }
  return map[cat] || cat.toUpperCase()
}

const getCategoryIcon = (cat: string) => {
  const map: Record<string, any> = {
    'data_api': CircleStackIcon,
    'agent': CpuChipIcon,
    'metadata': SparklesIcon,
    'general': AdjustmentsHorizontalIcon
  }
  return map[cat] || AdjustmentsHorizontalIcon
}

const isLongText = (item: ConfigItem) => {
  if (item.key.toLowerCase().includes('prompt')) return true
  if (item.value && (item.value.length > 60 || item.value.includes('\n'))) return true
  return false
}

const showRagSelector = ref(false)
const workingConfigItem = ref<ConfigItem | null>(null)
const showModelExplanation = ref(false)
const activeExplanationItem = ref<ConfigItem | null>(null)

const showExplanation = (item: ConfigItem) => {
  if (item.key === 'llm_model_name') {
    showModelExplanation.value = true
  } else {
    activeExplanationItem.value = item
  }
}

const getCategoryTip = (key: string) => {
  const tips: Record<string, string> = {
    'llm_temperature': '温度系数，范围为 0.0 至 1.0。值较低（如 0.0）会使模型的回答更加确定、精准（适合数据查询与逻辑推理）；值较高（如 0.7）则模型更具创造性与多样性。',
    'agent_max_iterations': 'ReAct 智能体单次对话的最大思考与工具调用轮数限制。建议设定在 10-20 之间，过小可能导致任务未完成便终止，过大可能因死循环消耗过多 Token。',
    'agent_max_context_turns': '智能体能够保留的最大历史上下文轮数。设置合理的值能防止发送给大模型的消息体过长，从而节约 Token 并加速模型响应。',
    'external_sql_api_url': '用于远程安全沙箱中执行生成 SQL 查询的 API 服务网关地址。直连物理执行模式（local）下此配置项将被忽略。',
    'external_sql_api_key': '用于调用远程安全 SQL 执行服务的身份验证 Token，请确保保密。',
    'data_api_timeout_seconds': '查数接口执行物理数据库查询时的最大等待超时。若查询的数据量非常大（如报表统计），可适当调大此值。',
    'schema_api_timeout_seconds': '平台抓取或读取数据库 Schema 结构信息时的超时时间，通常设为 10 秒即可。',
    'metadata_provider': '定义系统通过何种途径获取数据库表和字段的描述信息。local 表示直接读取本地手工填写的元数据字典，ragflow 表示通过语义检索自动从知识库获取描述。',
    'ragflow_api_url': '对接的 RAGFlow 语义检索平台后端 API 服务地址。',
    'ragflow_api_key': '用于与 RAGFlow 进行安全 API 调用的身份验证令牌（API Key）。',
    'ragflow_dataset_ids': '与当前数据平台关联绑定的 RAGFlow 知识库 ID（可多选），用于通过语义搜索表/字段的匹配描述。',
    'ragflow_similarity_threshold': 'RAG 检索时的向量相似度阈值（0.0 至 1.0）。数值越高，检索出的元数据与提问的相关性要求越严苛，建议为 0.4。',
    'ragflow_vector_weight': '检索时的向量相似度权重占比（0.0 至 1.0），其余权重为全文关键词检索。值为 0.85 表示偏向于向量语义匹配。',
    'ragflow_metadata_top_k': '检索数据库表/字段描述时，最大召回的候选文档数量。值越大，召回的内容越多，但会增加 Token 消耗。',
    'sql_execution_mode': '控制生成的 SQL 查询的执行位置。remote 表示通过安全的远程微服务沙箱执行，local 表示直连本地配置好的数据源连接池执行。',
    'chatbi_sample_knowledge_base': 'ChatBI 经验库在 RAGFlow 中自动创建和同步对应的知识库 ID（由系统自动校验与测试连接生成，不可手动修改）。',
    'chatbi_sample_similarity_threshold': '匹配用户提问与 ChatBI 历史案例时的相似度过滤阈值，推荐配置为 0.65，过滤不相关的参考案例。',
    'chatbi_sample_vector_similarity_weight': '案例匹配时向量语义距离的权重（推荐 0.85），可与关键词全文检索进行混合比例平衡。',
    'embedchat_watermark_enabled': '开启后，将在嵌入式对话界面（EmbedChat）背景中平铺渲染防止信息截屏泄露的安全审计水印。',
    'embedchat_watermark_style': '水印的文字样式方案。可以选择【用户名 + 时间戳】或【自定义文字 + 时间戳】（两者均会自动附加当前时间戳）。',
    'embedchat_watermark_text': '当水印样式为【自定义文字 + 时间戳】时，在对话背景中平铺显示的自定义文本，末尾会自动追加时间戳。',
    'yovole_sso_enabled': '控制是否启用 Yovole SSO 统一登录。关闭后，登录页面的 SSO 登录将隐藏，且用户管理中的 SSO 同步按钮也将隐藏。'
  }
  return tips[key] || ''
}

const openDatasetSelector = (item: ConfigItem) => {
    workingConfigItem.value = item
    showRagSelector.value = true
}

const chatbiKbTesting = ref(false)
const testChatBiKb = async (item: ConfigItem) => {
  chatbiKbTesting.value = true
  try {
    const response = await axios.post(`/api/portal/system/test-connection/chatbi_kb`)
    const data = response.data
    if (data.status === 'success') {
      showToast('测试连接成功，已确保知识库 ID 正常', 'success')
      if (data.message && data.message.includes('ID:')) {
        const parts = data.message.split('ID:')
        const newId = parts[1].trim()
        if (newId) {
          item.value = newId
        }
      }
    } else {
      showToast(`测试连接失败: ${data.message}`, 'error')
    }
  } catch (error: any) {
    const msg = error.response?.data?.detail || error.message
    showToast(`测试请求失败: ${msg}`, 'error')
  } finally {
    chatbiKbTesting.value = false
  }
}

const handleDatasetSelect = (val: string | string[]) => {
    if (workingConfigItem.value) {
        workingConfigItem.value.value = Array.isArray(val) ? val.join(',') : val
    }
}

const getVisibleItems = (items: ConfigItem[], category: string) => {
  if (!items) return []
  let list = [...items]
  if (category === 'data_api') {
    const modeItemIndex = list.findIndex(x => x.key === 'sql_execution_mode')
    if (modeItemIndex !== -1) {
      const modeItem = list[modeItemIndex]
      if (!modeItem) return list
      list.splice(modeItemIndex, 1)
      list.unshift(modeItem)
      if (modeItem.value === 'local') {
        list = [modeItem]
      }
    }
  }
  if (category === 'other') {
    const enabledItem = list.find(x => x.key === 'embedchat_watermark_enabled')
    const enabled = enabledItem?.value === 'true'
    
    const styleItem = list.find(x => x.key === 'embedchat_watermark_style')
    const isCustomText = styleItem?.value === 'custom'
    
    list = list.filter(item => {
      if (item.key === 'embedchat_watermark_style') {
        return enabled
      }
      if (item.key === 'embedchat_watermark_text') {
        return enabled && isCustomText
      }
      return true
    })
  }
  return list
}

onMounted(() => {
  fetchConfigs()
  fetchModelsForConfigs()
})
</script>

<template>
  <div class="space-y-6 h-full flex flex-col">
    <div class="flex justify-between items-center flex-shrink-0">
      <h1 class="text-2xl font-semibold text-gray-900">系统配置与诊断</h1>
      <!-- Tabs -->
      <div class="bg-gray-100 p-1 rounded-lg flex space-x-1">
         <button
           @click="activeTab = 'models'"
           class="px-4 py-2 rounded-md text-sm font-medium transition-all duration-200 flex items-center"
           :class="activeTab === 'models' ? 'bg-white shadow text-primary' : 'text-gray-500 hover:text-gray-700'"
         >
           <SparklesIcon class="w-4 h-4 mr-2" />
           模型管理
         </button>
         <button
           @click="activeTab = 'tools'"
           class="px-4 py-2 rounded-md text-sm font-medium transition-all duration-200 flex items-center"
           :class="activeTab === 'tools' ? 'bg-white shadow text-primary' : 'text-gray-500 hover:text-gray-700'"
         >
           <WrenchScrewdriverIcon class="w-4 h-4 mr-2" />
           工具管理
         </button>
         <button
           @click="activeTab = 'configs'"
           class="px-4 py-2 rounded-md text-sm font-medium transition-all duration-200 flex items-center"
           :class="activeTab === 'configs' ? 'bg-white shadow text-primary' : 'text-gray-500 hover:text-gray-700'"
         >
           <Cog6ToothIcon class="w-4 h-4 mr-2" />
           参数配置
         </button>
         <button
           @click="activeTab = 'mcp'"
           class="px-4 py-2 rounded-md text-sm font-medium transition-all duration-200 flex items-center"
           :class="activeTab === 'mcp' ? 'bg-white shadow text-primary' : 'text-gray-500 hover:text-gray-700'"
         >
           <ServerStackIcon class="w-4 h-4 mr-2" />
           MCP管理
         </button>
         <button
           @click="activeTab = 'diagnostics'"
           class="px-4 py-2 rounded-md text-sm font-medium transition-all duration-200 flex items-center"
           :class="activeTab === 'diagnostics' ? 'bg-white shadow text-primary' : 'text-gray-500 hover:text-gray-700'"
         >
           <CpuChipIcon class="w-4 h-4 mr-2" />
           系统诊断
         </button>
      </div>
    </div>

    <!-- Content Area -->
    <div class="flex-1 overflow-hidden">

      <div v-if="activeTab === 'models'" class="h-full">
          <ModelRegistry />
      </div>

      <div v-else-if="activeTab === 'tools'" class="h-full">
          <ToolRegistry />
      </div>

      <div v-else-if="activeTab === 'mcp'" class="h-full">
          <McpServerRegistry />
      </div>

       <!-- DIAGNOSTICS TAB -->
       <div v-else-if="activeTab === 'diagnostics'" class="grid grid-cols-1 lg:grid-cols-2 gap-6 h-full overflow-y-auto pb-6">
        <!-- Left Column: Connection Checks -->
        <div class="space-y-6">
          <div class="bg-white shadow rounded-lg p-6">
            <div class="flex items-center justify-between mb-4">
              <div class="flex items-center space-x-3">
                <div class="p-2 bg-red-100 rounded-lg">
                  <CircleStackIcon class="h-6 w-6 text-red-600" />
                </div>
                <div>
                  <h3 class="text-lg font-medium text-gray-900">Redis</h3>
                  <p class="text-sm text-gray-500">缓存与会话管理</p>
                </div>
              </div>
              <div v-if="results.redis" class="flex items-center">
                <CheckCircleIcon v-if="results.redis === 'success'" class="h-6 w-6 text-green-500" />
                <XCircleIcon v-else class="h-6 w-6 text-red-500" />
              </div>
            </div>
            <div class="border-t border-gray-100 pt-4 mt-2 grid grid-cols-3 gap-3">
              <button @click="testConnection('redis')" :disabled="loading.redis || !canSave" class="flex justify-center items-center py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-red-600 hover:bg-red-700 focus:outline-none disabled:opacity-50">
                <PlayIcon v-if="!loading.redis" class="h-4 w-4 mr-2" />
                <span v-else class="animate-spin h-4 w-4 mr-2 border-2 border-white border-t-transparent rounded-full"></span>
                {{ loading.redis ? '测试中...' : '测试连接' }}
              </button>
               <button @click="scanRedisKeys" :disabled="loading.redis_scan || !canSave" class="flex justify-center items-center py-2 px-4 border border-gray-300 rounded-md shadow-sm text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50">
                <MagnifyingGlassIcon v-if="!loading.redis_scan" class="h-4 w-4 mr-2" />
                <span v-else class="animate-spin h-4 w-4 mr-2 border-2 border-gray-400 border-t-transparent rounded-full"></span>
                {{ loading.redis_scan ? '扫描中...' : '扫描 Keys' }}
              </button>
              <button @click="openClearConfirm" :disabled="loading.redis_flush || !canSave" class="flex justify-center items-center py-2 px-4 border border-red-300 rounded-md shadow-sm text-sm font-medium text-red-700 bg-red-50 hover:bg-red-100 disabled:opacity-50">
                <TrashIcon v-if="!loading.redis_flush" class="h-4 w-4 mr-2" />
                <span v-else class="animate-spin h-4 w-4 mr-2 border-2 border-red-400 border-t-transparent rounded-full"></span>
                {{ loading.redis_flush ? '清空中...' : '清空 Keys' }}
              </button>
            </div>
          </div>

          <div class="bg-white shadow rounded-lg p-6">
            <div class="flex items-start justify-between mb-4">
              <div class="flex items-center space-x-3">
                <div class="p-2 bg-emerald-100 rounded-lg">
                  <CpuChipIcon class="h-6 w-6 text-emerald-600" />
                </div>
                <div>
                  <h3 class="text-lg font-medium text-gray-900">Redis 向量搜索</h3>
                  <p class="text-sm text-gray-500">检测 RediSearch 与会话摘要向量索引能力</p>
                </div>
              </div>
              <div v-if="results.redis_vector" class="flex items-center">
                <CheckCircleIcon v-if="results.redis_vector === 'success'" class="h-6 w-6 text-green-500" />
                <XCircleIcon v-else class="h-6 w-6 text-red-500" />
              </div>
            </div>

            <div
              v-if="redisVectorHealth"
              class="rounded-md border p-3 text-sm mb-4"
              :class="redisVectorHealth.ok ? 'bg-green-50 border-green-200 text-green-800' : 'bg-amber-50 border-amber-200 text-amber-900'"
            >
              <div class="font-medium">{{ redisVectorHealth.message }}</div>
              <div v-if="redisVectorHealth.redis_host" class="mt-1 text-xs opacity-80">
                当前连接：{{ redisVectorHealth.redis_host }}:{{ redisVectorHealth.redis_port }} / db {{ redisVectorHealth.redis_db }}
              </div>
              <ul v-if="!redisVectorHealth.ok && redisVectorHealth.hints?.length" class="list-disc pl-5 mt-2 space-y-1 text-xs">
                <li v-for="(hint, i) in redisVectorHealth.hints" :key="i">{{ hint }}</li>
              </ul>
            </div>

            <div v-if="redisVectorHealth?.checks?.length" class="border border-gray-100 rounded-md overflow-hidden mb-4">
              <div
                v-for="check in redisVectorHealth.checks"
                :key="check.name"
                class="flex items-start justify-between gap-3 px-3 py-2 border-b border-gray-100 last:border-b-0 text-sm"
              >
                <div>
                  <div class="font-medium text-gray-800">{{ check.name }}</div>
                  <div class="text-xs text-gray-500 mt-0.5">{{ check.message }}</div>
                </div>
                <span
                  class="shrink-0 px-2 py-0.5 rounded-full text-xs font-medium"
                  :class="check.passed ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'"
                >
                  {{ check.passed ? '通过' : '失败' }}
                </span>
              </div>
            </div>

            <button
              @click="testRedisVectorSearch(true)"
              :disabled="loading.redis_vector || !canSave"
              class="inline-flex justify-center items-center py-2 px-4 border border-emerald-200 rounded-md shadow-sm text-sm font-medium text-emerald-700 bg-emerald-50 hover:bg-emerald-100 disabled:opacity-50"
            >
              <PlayIcon v-if="!loading.redis_vector" class="h-4 w-4 mr-2" />
              <span v-else class="animate-spin h-4 w-4 mr-2 border-2 border-emerald-400 border-t-transparent rounded-full"></span>
              {{ loading.redis_vector ? '检测中...' : '重新检测' }}
            </button>
          </div>
        </div>
        <!-- Right Column: Console Output -->
        <div class="bg-gray-900 rounded-lg shadow overflow-hidden flex flex-col h-[500px]">
          <div class="bg-gray-800 px-4 py-2 flex justify-between items-center border-b border-gray-700">
            <div class="flex items-center space-x-2 text-gray-300 font-mono text-xs">
              <CommandLineIcon class="h-4 w-4" />
              <span>诊断控制台</span>
            </div>
            <button @click="clearLogs" class="text-xs text-gray-400 hover:text-white">清空</button>
          </div>
          <div class="flex-1 p-4 overflow-y-auto font-mono text-sm space-y-1 custom-scrollbar">
            <div v-if="logs.length === 0" class="text-gray-500 italic">等待执行测试...</div>
            <div v-else v-for="(log, index) in logs" :key="index" class="text-green-400 break-all">
              <span class="text-gray-500 mr-2">></span>{{ log }}
            </div>
          </div>
        </div>
      </div>

      <!-- CONFIGS TAB -->
      <div v-else class="h-full overflow-y-auto pb-6 custom-scrollbar">
         <div v-if="configLoading" class="flex justify-center py-20">
             <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
         </div>
         <div v-else class="space-y-8 max-w-4xl">
             <div v-for="category in orderedCategories" :key="category" class="bg-white shadow rounded-lg overflow-hidden">
                <div class="bg-gray-50 px-6 py-3 border-b border-gray-200 flex items-center">
                   <div class="p-1.5 bg-white rounded-md shadow-sm border border-gray-100 mr-3">
                       <component :is="getCategoryIcon(String(category))" class="h-5 w-5 text-primary" />
                   </div>
                   <h3 class="text-md font-medium text-gray-800">{{ getCategoryLabel(String(category)) }}</h3>
                </div>
                <div class="p-6 space-y-5">
                   <div v-for="item in getVisibleItems(configGroups[category], String(category))" :key="item.key" class="grid grid-cols-1 md:grid-cols-3 gap-4">
                      <div class="md:col-span-1 pt-2">
                         <label class="block text-sm font-medium text-gray-700 flex items-center gap-1.5">
                            <span>{{ item.key }}</span>
                            <button
                              type="button"
                              @click="showExplanation(item)"
                              class="text-gray-400 hover:text-primary transition-colors focus:outline-none"
                              title="查看参数说明"
                            >
                              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" class="w-4 h-4 inline-block">
                                <path stroke-linecap="round" stroke-linejoin="round" d="M9.879 7.519c1.171-1.025 3.071-1.025 4.242 0 1.172 1.025 1.172 2.687 0 3.712-.203.179-.43.326-.67.442-.745.361-1.45.999-1.45 1.827v.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 5.25h.008v.008H12v-.008Z" />
                              </svg>
                            </button>
                         </label>
                         <p class="text-xs text-gray-500 mt-1">{{ item.description }}</p>
                      </div>
                       <div class="md:col-span-2 relative">
                          <div v-if="item.key === 'llm_model_name'">
                              <select v-model="item.value" :disabled="!canSave" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 p-2 disabled:opacity-70 disabled:cursor-not-allowed">
                                 <option value="" disabled>选择默认模型...</option>
                                 <option v-for="m in models.filter(x => x.type === 'llm' && x.is_active)" :key="m.id" :value="m.model_id">
                                    {{ m.name }} ({{ m.model_id }})
                                 </option>
                                 <option v-if="item.value && !models.find(m => m.model_id === item.value)" :value="item.value">
                                     {{ item.value }} (未知/环境变量)
                                 </option>
                              </select>
                          </div>
                          <div v-else-if="item.key === 'metadata_provider'">
                             <select v-model="item.value" :disabled="!canSave" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 p-2 disabled:opacity-70 disabled:cursor-not-allowed">
                                <option value="local">local (本地元数据)</option>
                                <option value="ragflow">ragflow (语义检索 RAG)</option>
                             </select>
                          </div>
                          <div v-else-if="item.key === 'sql_execution_mode'">
                             <select v-model="item.value" :disabled="!canSave" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 p-2 disabled:opacity-70 disabled:cursor-not-allowed">
                                <option value="remote">remote (走远程执行服务)</option>
                                <option value="local">local (本地数据源直连执行)</option>
                             </select>
                          </div>
                          <div v-else-if="item.is_secret" class="relative">
                             <input :type="showSecrets[item.key] ? 'text' : 'password'" v-model="item.value" :disabled="!canSave" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md pr-10 bg-gray-100 disabled:opacity-70 disabled:cursor-not-allowed" />
                             <div @click="toggleSecret(item.key)" class="absolute inset-y-0 right-0 pr-3 flex items-center cursor-pointer text-gray-400">
                                <EyeIcon v-if="!showSecrets[item.key]" class="h-5 w-5" />
                                <EyeSlashIcon v-else class="h-5 w-5" />
                             </div>
                          </div>
                          <div v-else-if="item.key === 'ragflow_dataset_ids'">
                               <div class="flex space-x-2">
                                   <input type="text" v-model="item.value" :disabled="!canSave" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 disabled:opacity-70 disabled:cursor-not-allowed" />
                                   <button
                                       v-if="canSave"
                                       @click="openDatasetSelector(item)"
                                       class="px-3 py-2 bg-white border border-gray-300 rounded-md text-gray-500 hover:text-primary hover:border-primary transition-colors"
                                       title="选择知识库"
                                    >
                                       <CircleStackIcon class="w-5 h-5" />
                                   </button>
                               </div>
                          </div>
                          <div v-else-if="item.key === 'chatbi_sample_knowledge_base'">
                               <div class="flex items-center space-x-2">
                                   <input type="text" v-model="item.value" :disabled="true" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 disabled:opacity-70 disabled:cursor-not-allowed" />
                                   <span class="inline-flex items-center px-3 py-1.5 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800 shrink-0 select-none border border-emerald-200">
                                       chatbi-example-meta
                                   </span>
                                   <button
                                       @click="testChatBiKb(item)"
                                       :disabled="chatbiKbTesting"
                                       class="inline-flex items-center px-3 py-2 border border-gray-300 shadow-sm text-sm leading-4 font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary disabled:opacity-50 shrink-0"
                                   >
                                       <PlayIcon v-if="!chatbiKbTesting" class="h-4 w-4 mr-1.5 text-gray-500" />
                                       <span v-else class="animate-spin h-4 w-4 mr-1.5 border-2 border-primary border-t-transparent rounded-full"></span>
                                       测试
                                   </button>
                               </div>
                          </div>
                          <div v-else-if="['ragflow_similarity_threshold', 'ragflow_vector_weight', 'chatbi_sample_similarity_threshold', 'chatbi_sample_vector_similarity_weight'].includes(item.key)">
                              <div class="flex items-center space-x-4">
                                  <div class="flex-1">
                                      <input
                                        type="range"
                                        min="0"
                                        max="1"
                                        step="0.05"
                                        :value="Number(item.value)"
                                        :disabled="!canSave"
                                        @input="(e) => item.value = (e.target as HTMLInputElement).value"
                                        class="w-full h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-primary disabled:opacity-50"
                                      />
                                      <div class="flex justify-between text-xs text-gray-400 mt-1 font-mono">
                                          <span>0.0</span>
                                          <span>0.5</span>
                                          <span>1.0</span>
                                      </div>
                                  </div>
                                  <div class="w-16">
                                      <input
                                        type="number"
                                        v-model="item.value"
                                        :disabled="!canSave"
                                        min="0"
                                        max="1"
                                        step="0.05"
                                        class="block w-full sm:text-sm border-gray-300 rounded-md bg-white text-center focus:ring-primary focus:border-primary disabled:opacity-70"
                                      />
                                  </div>
                              </div>
                          </div>
                          <div v-else-if="['embedchat_watermark_enabled', 'yovole_sso_enabled'].includes(item.key)" class="flex items-center">
                             <button
                               type="button"
                               :disabled="!canSave"
                               @click="item.value = item.value === 'true' ? 'false' : 'true'"
                               class="relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed shadow-inner"
                               :class="item.value === 'true' ? 'bg-primary' : 'bg-gray-200'"
                             >
                               <span
                                 aria-hidden="true"
                                 class="pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out"
                                 :class="item.value === 'true' ? 'translate-x-5' : 'translate-x-0'"
                               ></span>
                             </button>
                          </div>
                          <div v-else-if="item.key === 'embedchat_watermark_style'">
                             <select v-model="item.value" :disabled="!canSave" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 p-2 disabled:opacity-70 disabled:cursor-not-allowed">
                                <option value="user_time">用户名 + 时间戳</option>
                                <option value="custom">自定义文字</option>
                             </select>
                          </div>
                          <div v-else-if="isLongText(item)">
                             <textarea v-model="item.value" :disabled="!canSave" rows="10" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md font-mono text-xs bg-gray-100 p-3 disabled:opacity-70 disabled:cursor-not-allowed"></textarea>
                          </div>
                          <div v-else>
                             <input type="text" v-model="item.value" :disabled="!canSave" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 disabled:opacity-70 disabled:cursor-not-allowed" />
                          </div>
                       </div>
                   </div>
                </div>
             </div>
             <div v-if="canSave" class="flex justify-end pt-4 pb-12">
                <button @click="fetchConfigs" class="mr-4 bg-white py-2 px-4 border border-gray-300 rounded-md shadow-sm text-sm font-medium text-gray-700">重置</button>
                <button @click="saveConfigs" :disabled="saving" class="inline-flex justify-center py-2 px-4 border border-transparent shadow-sm text-sm font-medium rounded-md text-white bg-primary disabled:opacity-50">
                  {{ saving ? '保存中...' : '保存变更' }}
                </button>
             </div>
         </div>
      </div>
    </div>

    <RagFlowResourceSelector
        v-model="showRagSelector"
        type="dataset"
        :initial-selected="workingConfigItem?.value ? workingConfigItem.value.split(',').filter(Boolean) : []"
        @select="handleDatasetSelect"
    />

    <ConfirmModal
      v-if="showClearConfirm"
      title="清理系统缓存？"
      message="此操作将清理系统缓存（如权限、临时数据），但会**保留**所有用户的对话历史。确定执行吗？"
      confirm-text="确认清理"
      cancel-text="取消"
      type="warning"
      @confirm="executeClearKeys"
      @cancel="showClearConfirm = false"
    />

    <!-- LLM Model Name Explanation Modal -->
    <div v-if="showModelExplanation" class="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm animate-fade-in" @click.self="showModelExplanation = false">
      <div class="bg-white rounded-2xl shadow-2xl max-w-lg w-full overflow-hidden scale-100 transition-all duration-200 border border-gray-100 flex flex-col">
        <!-- Header -->
        <div class="px-6 py-4 border-b border-gray-100 flex justify-between items-center bg-gray-50/50">
          <div class="flex items-center space-x-2.5">
            <div class="p-2 bg-indigo-50 rounded-xl text-indigo-600">
              <SparklesIcon class="w-5 h-5" />
            </div>
            <div>
              <h3 class="text-md font-bold text-gray-900">默认大模型参数影响场景</h3>
              <p class="text-xs text-gray-400 mt-0.5">参数名：llm_model_name</p>
            </div>
          </div>
          <button @click="showModelExplanation = false" class="text-gray-400 hover:text-gray-600 focus:outline-none transition-colors">
            <svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <!-- Content -->
        <div class="p-6 space-y-4 text-sm text-gray-600 max-h-[400px] overflow-y-auto custom-scrollbar">
          <p class="text-gray-500 leading-relaxed">
            该参数配置了整个平台默认使用的大语言模型名称（例如 <code class="bg-gray-100 text-gray-800 px-1.5 py-0.5 rounded font-mono text-xs">deepseek-chat</code>）。它作为系统的基础底座模型，将主要影响以下核心业务场景：
          </p>
          
          <div class="space-y-3.5">
            <!-- Scenario 1 -->
            <div class="flex gap-3 p-3 rounded-xl bg-gray-50 hover:bg-gray-100/60 transition-colors">
              <div class="flex-shrink-0 flex items-center justify-center w-7 h-7 rounded-lg bg-indigo-100 text-indigo-700 font-bold text-xs">1</div>
              <div class="space-y-1">
                <h4 class="font-bold text-gray-900">智能意图路由与分发决策 (Intent Routing)</h4>
                <p class="text-xs text-gray-500 leading-relaxed">
                  在多智能体混合对话模式下，系统通过此模型对用户提问进行<strong>指代消解、上下文理解和意图识别</strong>，最终决定将任务分发给哪个特定的专家智能体（如 ChatBI、知识库、Jira 等）。
                </p>
              </div>
            </div>

            <!-- Scenario 2 -->
            <div class="flex gap-3 p-3 rounded-xl bg-gray-50 hover:bg-gray-100/60 transition-colors">
              <div class="flex-shrink-0 flex items-center justify-center w-7 h-7 rounded-lg bg-emerald-100 text-emerald-700 font-bold text-xs">2</div>
              <div class="space-y-1">
                <h4 class="font-bold text-gray-900">智能体兜底执行模型 (Fallback Execution)</h4>
                <p class="text-xs text-gray-500 leading-relaxed">
                  当用户调用<strong>未明确配置大模型</strong>的智能体（如使用默认模型设置），或者在执行某步 ReAct 逻辑链且模型参数为空时，系统将使用该参数配置的默认模型作为兜底进行回复生成。
                </p>
              </div>
            </div>

            <!-- Scenario 3 -->
            <div class="flex gap-3 p-3 rounded-xl bg-gray-50 hover:bg-gray-100/60 transition-colors">
              <div class="flex-shrink-0 flex items-center justify-center w-7 h-7 rounded-lg bg-amber-100 text-amber-700 font-bold text-xs">3</div>
              <div class="space-y-1">
                <h4 class="font-bold text-gray-900">系统内置辅助推理流 (System Internal Tasks)</h4>
                <p class="text-xs text-gray-500 leading-relaxed">
                  影响系统后台运行的一些自动化 AI 任务，包括但不限于：<strong>聊天会话每日摘要生成、分析推理过程（thought process）的二次修剪提取、AI 辅助生成元数据描述和标签</strong>等。
                </p>
              </div>
            </div>
            
            <!-- Scenario 4 -->
            <div class="flex gap-3 p-3 rounded-xl bg-gray-50 hover:bg-gray-100/60 transition-colors">
              <div class="flex-shrink-0 flex items-center justify-center w-7 h-7 rounded-lg bg-blue-100 text-blue-700 font-bold text-xs">4</div>
              <div class="space-y-1">
                <h4 class="font-bold text-gray-900">轻量级文本清洗与 RAG 决策 (Text Clean & RAG)</h4>
                <p class="text-xs text-gray-500 leading-relaxed">
                  在进行知识库关联检索、经验样本 Few-Shot 前置数据清洗以及查询结果数据过滤时，提供对文本片段的结构化分析与评判决策。
                </p>
              </div>
            </div>
          </div>
        </div>
        <!-- Footer -->
        <div class="bg-gray-50 px-6 py-4 flex justify-end border-t border-gray-100">
          <button 
            @click="showModelExplanation = false" 
            type="button" 
            class="px-5 py-2 rounded-xl text-sm font-bold text-white bg-primary hover:bg-primary-dark transition-all duration-200 active:scale-95 shadow-sm focus:outline-none"
          >
            我知道了
          </button>
        </div>
      </div>
    </div>

    <!-- Generic Config Explanation Modal -->
    <div v-if="activeExplanationItem" class="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm" @click.self="activeExplanationItem = null">
      <div class="bg-white rounded-2xl shadow-2xl max-w-md w-full overflow-hidden scale-100 transition-all duration-200 border border-gray-100 flex flex-col">
        <!-- Header -->
        <div class="px-6 py-4 border-b border-gray-100 flex justify-between items-center bg-gray-50/50">
          <div class="flex items-center space-x-2.5">
            <div class="p-2 bg-primary/10 rounded-xl text-primary">
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-5 h-5">
                <path stroke-linecap="round" stroke-linejoin="round" d="M9.879 7.519c1.171-1.025 3.071-1.025 4.242 0 1.172 1.025 1.172 2.687 0 3.712-.203.179-.43.326-.67.442-.745.361-1.45.999-1.45 1.827v.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 5.25h.008v.008H12v-.008Z" />
              </svg>
            </div>
            <div>
              <h3 class="text-md font-bold text-gray-900">配置参数说明</h3>
              <p class="text-xs text-gray-400 mt-0.5">{{ activeExplanationItem.key }}</p>
            </div>
          </div>
          <button @click="activeExplanationItem = null" class="text-gray-400 hover:text-gray-600 focus:outline-none transition-colors">
            <svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <!-- Content -->
        <div class="p-6 space-y-4 text-sm text-gray-600">
          <div class="space-y-2">
            <span class="text-xs font-bold text-gray-400 uppercase tracking-wider font-mono">功能描述</span>
            <p class="text-gray-700 leading-relaxed bg-gray-50 p-4 rounded-xl border border-gray-100">
              {{ activeExplanationItem.description || '暂无描述信息。' }}
            </p>
          </div>
          
          <!-- Category specific tips -->
          <div class="space-y-2" v-if="getCategoryTip(activeExplanationItem.key)">
            <span class="text-xs font-bold text-gray-400 uppercase tracking-wider font-mono">使用建议</span>
            <p class="text-xs text-gray-600 leading-relaxed bg-indigo-50/50 p-4 rounded-xl border border-indigo-100/50 text-indigo-950">
              {{ getCategoryTip(activeExplanationItem.key) }}
            </p>
          </div>
        </div>
        <!-- Footer -->
        <div class="bg-gray-50 px-6 py-4 flex justify-end border-t border-gray-100">
          <button 
            @click="activeExplanationItem = null" 
            type="button" 
            class="px-5 py-2 rounded-xl text-sm font-bold text-white bg-primary hover:bg-primary-dark transition-all duration-200 active:scale-95 shadow-sm focus:outline-none"
          >
            我知道了
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar { width: 6px; }
.custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
.custom-scrollbar::-webkit-scrollbar-thumb { background-color: rgba(156, 163, 175, 0.3); border-radius: 3px; }
</style>
