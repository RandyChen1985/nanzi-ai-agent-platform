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
import RedisKeyCleanupModal from '../components/system/RedisKeyCleanupModal.vue'
import Switch from '../components/Switch.vue'
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
  PlayIcon,
  ArrowPathIcon,
  PaintBrushIcon
} from '@heroicons/vue/24/outline'

const { hasPermission, userInfo } = useUser()
const canSave = hasPermission('element:system:config_save')

const activeTab = ref<'diagnostics' | 'configs' | 'models' | 'tools' | 'mcp' | 'logs' | 'branding'>('configs')
const diagSubTab = ref<'console' | 'redis'>('console')

// --- Diagnostics Logic ---
const logs = ref<string[]>([])
const loading = ref<{ [key: string]: boolean }> ({
  redis: false,
  redis_scan: false,
  redis_vector: false,
  rebuild_vector: false
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
const showRedisCleanupModal = ref(false)
const showRebuildConfirm = ref(false)
const showUserSyncDetail = ref(false)

const appendLog = (msg: string) => {
  const timestamp = new Date().toLocaleTimeString()
  logs.value.push(`[${timestamp}] ${msg}`)
}

const testConnection = async (component: string) => {
  diagSubTab.value = 'console'
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
  diagSubTab.value = 'console'
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
  diagSubTab.value = 'console'
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
  showRedisCleanupModal.value = true
}

const handleRedisKeysDeleted = async (payload: { deletedCount: number; message: string }) => {
  appendLog(`>>> ✅ ${payload.message}`)
  showToast(`已删除 ${payload.deletedCount} 个 Key`, 'success')
  if (diagSubTab.value === 'redis') {
    await fetchRedisKeys()
  }
}

const openRebuildConfirm = () => {
  showRebuildConfirm.value = true
}

const executeRebuildVectors = async () => {
  loading.value['rebuild_vector'] = true
  showRebuildConfirm.value = false
  appendLog('>>> 正在启动本地向量索引与数据重构任务...')

  try {
     const response = await axios.post('/api/portal/system/redis/rebuild-vectors')
     const { message, logs: serverLogs } = response.data
     if (serverLogs && Array.isArray(serverLogs)) {
       serverLogs.forEach((logStr: string) => {
         appendLog(`>>> ${logStr}`)
       })
     }
     appendLog(`>>> ✅ ${message}`)
     showToast('本地向量数据重构成功，后台同步中', 'success')
     
     // 自动重新检测
     testRedisVectorSearch(true)
  } catch (e: any) {
    const msg = e.response?.data?.detail || e.message
    appendLog(`>>> ❌ 重构失败: ${msg}`)
    showToast('重构失败', 'error')
  } finally {
    loading.value['rebuild_vector'] = false
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
  const order = ['agent', 'metadata', 'data_api', 'knowledge', 'general', 'other']
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

const metadataProvider = computed(() => {
  if (!configGroups.value) return 'local'
  for (const list of Object.values(configGroups.value)) {
    const item = list.find(x => x.key === 'metadata_provider')
    if (item) return item.value
  }
  return 'local'
})

const isKnowledgeFeatureEnabled = computed(() => {
  const list = configGroups.value.knowledge
  if (!list) return true
  const item = list.find(x => x.key === 'knowledge_base_enabled')
  return (item?.value ?? 'true') === 'true'
})

const isConfigItemDisabled = (_category: string, item: ConfigItem) => {
  if (item.key === 'third_party_user_sync_config') return true
  return !canSave
}
const parseJson = (val: string) => {
  try {
    return JSON.parse(val)
  } catch (e) {
    return null
  }
}
const originalConfigs = ref<{ [key: string]: string }>({})
const configLoading = ref(false)
const saving = ref(false)
const brandingSaving = ref(false)
const brandingIconUploading = ref(false)
const showSecrets = ref<{ [key: string]: boolean }>({})

const brandingConfig = ref({
  enabled: false,
  product_name: '南孜 · 智能体平台',
  login_subtitle: 'NanZi Intelligent Agent Platform',
  icon_url: '/favicon.png',
  hide_login_sso: false,
  hide_version_link: false,
  contact_markdown: '',
  copyright_text: '',
  default_agent_name: '南孜智能助手',
})
const brandingIconInput = ref<HTMLInputElement | null>(null)

const fetchBrandingConfig = async () => {
  try {
    const res = await axios.get('/api/portal/system/branding')
    const data = res.data || {}
    brandingConfig.value = {
      enabled: !!data.enabled,
      product_name: data.product_name || '南孜 · 智能体平台',
      login_subtitle: data.login_subtitle || 'NanZi Intelligent Agent Platform',
      icon_url: data.icon_url || '/favicon.png',
      hide_login_sso: !!data.hide_login_sso,
      hide_version_link: !!data.hide_version_link,
      contact_markdown: data.contact_markdown || '',
      copyright_text: data.copyright_text || '',
      default_agent_name: data.default_agent_name || '南孜智能助手',
    }
  } catch {
    showToast('品牌配置加载失败', 'error')
  }
}

const saveBrandingConfig = async () => {
  brandingSaving.value = true
  try {
    await axios.put('/api/portal/system/branding', { ...brandingConfig.value })
    const { loadBranding } = await import('../composables/useBranding')
    await loadBranding(true)
    showToast('品牌配置已保存', 'success')
  } catch (e: any) {
    showToast(e.response?.data?.detail || '保存失败', 'error')
  } finally {
    brandingSaving.value = false
  }
}

const showCropper = ref(false)
const cropperImageSrc = ref('')
const cropperZoom = ref(1)
const cropperOffset = ref({ x: 0, y: 0 })
const cropperImageFile = ref<File | null>(null)
const cropperImageType = ref('')
const cropperInitWidth = ref(0)
const cropperInitHeight = ref(0)

const cropperImageStyle = computed(() => {
  return {
    width: `${cropperInitWidth.value}px`,
    height: `${cropperInitHeight.value}px`,
    transform: `translate(${cropperOffset.value.x}px, ${cropperOffset.value.y}px) scale(${cropperZoom.value})`,
    transformOrigin: 'center center',
  }
})

const isDraggingCropper = ref(false)
const cropperDragStart = ref({ x: 0, y: 0 })

const onCropperMouseDown = (e: MouseEvent) => {
  isDraggingCropper.value = true
  cropperDragStart.value = { x: e.clientX - cropperOffset.value.x, y: e.clientY - cropperOffset.value.y }
}

const onCropperMouseMove = (e: MouseEvent) => {
  if (!isDraggingCropper.value) return
  cropperOffset.value = {
    x: e.clientX - cropperDragStart.value.x,
    y: e.clientY - cropperDragStart.value.y
  }
}

const onCropperMouseUp = () => {
  isDraggingCropper.value = false
}

const onCropperTouchStart = (e: TouchEvent) => {
  if (e.touches.length !== 1) return
  const touch = e.touches[0]
  if (!touch) return
  isDraggingCropper.value = true
  cropperDragStart.value = {
    x: touch.clientX - cropperOffset.value.x,
    y: touch.clientY - cropperOffset.value.y
  }
}

const onCropperTouchMove = (e: TouchEvent) => {
  if (!isDraggingCropper.value || e.touches.length !== 1) return
  const touch = e.touches[0]
  if (!touch) return
  cropperOffset.value = {
    x: touch.clientX - cropperDragStart.value.x,
    y: touch.clientY - cropperDragStart.value.y
  }
}

const triggerBrandingIconUpload = () => {
  brandingIconInput.value?.click()
}

const uploadBrandingIconDirectly = async (fileOrBlob: File | Blob, filename = 'icon.png') => {
  brandingIconUploading.value = true
  try {
    const form = new FormData()
    const uploadFile = fileOrBlob instanceof File ? fileOrBlob : new File([fileOrBlob], filename, { type: fileOrBlob.type })
    form.append('file', uploadFile)
    const res = await axios.post('/api/portal/system/branding/icon', form)
    brandingConfig.value.icon_url = res.data.icon_url
    showToast('图标上传成功', 'success')
  } catch (err: any) {
    showToast(err.response?.data?.detail || '上传失败', 'error')
  } finally {
    brandingIconUploading.value = false
  }
}

const onBrandingIconSelected = (e: Event) => {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return

  // 如果是 SVG，不需要裁剪，直接上传
  if (file.type === 'image/svg+xml') {
    uploadBrandingIconDirectly(file)
    return
  }

  // 验证是否是支持的图片类型
  const supported = ['image/png', 'image/jpeg', 'image/jpg', 'image/webp']
  if (!supported.includes(file.type)) {
    showToast('仅支持 PNG、JPEG、WebP、SVG 图片', 'error')
    return
  }

  cropperImageFile.value = file
  cropperImageType.value = file.type

  // 读取为 DataURL
  const reader = new FileReader()
  reader.onload = (event) => {
    cropperImageSrc.value = event.target?.result as string
    
    // 获取图片的自然宽高以计算自适应大小
    const img = new Image()
    img.src = cropperImageSrc.value
    img.onload = () => {
      const cropSize = 240
      const ratio = Math.max(cropSize / img.naturalWidth, cropSize / img.naturalHeight)
      cropperInitWidth.value = img.naturalWidth * ratio
      cropperInitHeight.value = img.naturalHeight * ratio
      
      cropperZoom.value = 1
      cropperOffset.value = { x: 0, y: 0 }
      showCropper.value = true
    }
  }
  reader.readAsDataURL(file)
}

const handleCropperConfirm = () => {
  const img = new Image()
  img.src = cropperImageSrc.value
  img.onload = () => {
    const canvas = document.createElement('canvas')
    canvas.width = 256
    canvas.height = 256
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const cropSize = 240
    const scaleFactor = 256 / cropSize

    // 如果是 jpeg，填充白色底；如果是 png/webp，保持透明
    if (cropperImageType.value === 'image/jpeg' || cropperImageType.value === 'image/jpg') {
      ctx.fillStyle = '#ffffff'
      ctx.fillRect(0, 0, 256, 256)
    }

    const initW = cropperInitWidth.value
    const initH = cropperInitHeight.value
    const drawW = initW * cropperZoom.value
    const drawH = initH * cropperZoom.value

    const x = (cropSize - initW) / 2 + cropperOffset.value.x
    const y = (cropSize - initH) / 2 + cropperOffset.value.y

    const drawX = x - (drawW - initW) / 2
    const drawY = y - (drawH - initH) / 2

    ctx.drawImage(
      img,
      drawX * scaleFactor,
      drawY * scaleFactor,
      drawW * scaleFactor,
      drawH * scaleFactor
    )

    canvas.toBlob((blob) => {
      if (blob) {
        const ext = cropperImageType.value === 'image/webp' ? 'webp' : 'png'
        uploadBrandingIconDirectly(blob, `icon.${ext}`)
      }
      showCropper.value = false
    }, cropperImageType.value, 0.9)
  }
}

const fetchConfigs = async () => {
  configLoading.value = true
  try {
    const res = await axios.get('/api/portal/system/configs')
    configGroups.value = res.data
    originalConfigs.value = {}
    for (const cat in res.data) {
        res.data[cat].forEach((item: ConfigItem) => {
            originalConfigs.value[item.key] = item.value
            if (item.key === 'third_party_user_sync_config' && !item.description) {
              item.description = '第三方用户同步配置（数据源、表、字段映射、定时周期）'
            }
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
    'knowledge': '知识库设置 (Knowledge Base)',
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
    'knowledge': ServerStackIcon,
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
const datasetSelectorUrl = ref('')
const datasetSelectorKey = ref('')
const showModelExplanation = ref(false)
const showMetadataExplanation = ref(false)
const activeExplanationItem = ref<ConfigItem | null>(null)

const showExplanation = (item: ConfigItem) => {
  if (item.key === 'llm_model_name') {
    showModelExplanation.value = true
  } else if (item.key === 'metadata_provider') {
    showMetadataExplanation.value = true
  } else {
    activeExplanationItem.value = item
  }
}

const getCategoryTip = (key: string) => {
  const tips: Record<string, string> = {
    'llm_temperature': '大模型温度系数，范围为 0.0 至 1.0。趋近于 0.0 表示回答更加确定、严谨和精准（适合数据查询与逻辑推理）；趋近于 1.0 表示回答更具创造力、发散性和随机性。',
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
    'ragflow_similarity_threshold': `【相似度阈值 (ragflow_similarity_threshold)】
这个参数是一个过滤器（门槛），用来决定什么样的表/字段元数据片段“足够相关”，可以被送给大模型。

在 RAGFlow（或者大多数先进的 RAG 检索系统）中，混合检索（Hybrid Search）通常结合了全文检索（Sparse Retrieval，如 BM25）和向量检索（Dense Retrieval，如 Vector Embeddings）。这个参数就是用来控制如何筛选这两种检索结果的核心阈值。

* 原理：系统计算出元数据和用户提问的相似度分数（如余弦相似度）后，只有分数大于或等于该阈值的片段才会被保留，低于该分数的全部被丢弃。
* 取值范围：0.0 到 1.0 之间。
* 通俗解释：
  - 0.0：没有任何门槛。系统会把检索出的相关表结构描述全部喂给大模型，不管它们是否相关。（容易引入大量不相关干扰信息，导致大模型混淆或胡说八道）。
  - 0.9：门槛极高。只有和问题中表述极其相似、几乎一模一样的元数据描述才能通过。（极易导致大模型找不到任何参考表结构，回答“无法获取相关信息”）。

💡 调优建议：
* 一般推荐设置在 0.4 到 0.6 之间作为起步（平台默认建议 0.40）。
* 如果发现大模型经常瞎编不存在的表或字段名，说明阈值太低了混入了杂音，需要调高。
* 如果发现大模型经常明明有对应的表，却回答“找不到相关表结构”，说明门槛太高了，需要调低。`,
    'ragflow_vector_weight': `【向量权重 (ragflow_vector_weight)】
该参数决定了在进行元数据混合检索时，向量语义检索结果所占的分数权重。

在 RAGFlow（或者大多数先进的 RAG 检索系统）中，混合检索（Hybrid Search）通常结合了全文检索（Sparse Retrieval，如 BM25）和向量检索（Dense Retrieval，如 Vector Embeddings）。这个参数就是用来平衡这两种检索结果的核心权重。

* 原理：混合检索的最终得分通常是通过公式计算的：
  最终得分 = (向量检索得分 * vector_weight) + (全文检索得分 * (1 - vector_weight))
* 取值范围：0.0 到 1.0 之间。
* 通俗解释：
  - 1.0：只看语义相关度（向量检索），完全忽略关键词的完全匹配。
  - 0.0：只看关键词匹配（全文检索），完全忽略同义词或相近语义的理解。
  - 0.70 / 0.85（默认值）：代表系统更倾向于语义理解，但同时保留 15%~30% 的权重给精确的关键词/表名匹配。

💡 调优建议：
* 如果你的数据集多为行业术语、字母缩写、特定型号、人名或股票代码等需要精准字面匹配的场景，调低该值（如 0.3 ~ 0.4）。
* 如果用户提问多为口语化日常表达、长句描述，或包含大量同义词（如“查找”与“搜索”），调高该值（如 0.7 ~ 0.85）。`,
    'ragflow_metadata_top_k': '检索数据库表/字段描述时，最大召回的候选文档数量。值越大，召回的内容越多，但会增加 Token 消耗。',
    'sql_execution_mode': '控制生成的 SQL 查询的执行位置。remote 表示通过安全的远程微服务沙箱执行，local 表示直连本地配置好的数据源连接池执行。',
    'chatbi_sample_knowledge_base': 'ChatBI 经验库在 RAGFlow 中自动创建和同步对应的知识库 ID（由系统自动校验与测试连接生成，不可手动修改）。',
    'chatbi_sample_top_k': '检索用户提问时召回的最相似问答案例（Few-shot）最大限制条数。值越大参考条数越多，但会占据更多的 Prompt 上下文。',
    'chatbi_sample_similarity_threshold': `【匹配相似度阈值 (chatbi_sample_similarity_threshold)】
这个参数是一个过滤器（门槛），用来决定什么样的历史查数案例（Few-shot）“足够相似”，可以用来参考。

在 RAGFlow（或者大多数先进的 RAG 检索系统）中，混合检索（Hybrid Search）通常结合了全文检索（Sparse Retrieval，如 BM25）和向量检索（Dense Retrieval，如 Vector Embeddings）。这个参数就是用来控制如何筛选这两种检索结果的核心阈值。

* 原理：计算当前提问与历史案例的相似度后，只有相似度大于或等于该阈值的案例才会被作为 Prompt 参考样本提供给大模型。
* 取值范围：0.0 到 1.0 之间。
* 通俗解释：
  - 0.0：无任何过滤。无论是否相关，最相似的几个案例都会全部作为 Few-shot 喂给大模型。（可能误导大模型套用错误模板，引入大量噪音导致大模型胡说八道）。
  - 0.9：门槛极高。只有和历史案例极其吻合的提问才能匹配上，参考难度极大。（容易导致大模型找不到任何参考案例）。

💡 调优建议：
* 一般推荐设置在 0.5 到 0.7 之间作为起步（平台默认建议 0.65，在本地模式下建议 0.40）。
* 若大模型经常乱套用历史案例的 SQL 模板，说明阈值太低了混入了杂音，需要调高。
* 若明明有相似案例大模型却无法参考，说明门槛太高了，需要调低。`,
    'chatbi_sample_vector_similarity_weight': `【案例向量权重 (chatbi_sample_vector_similarity_weight)】
此权重用于控制匹配 ChatBI 案例时，向量语义相似度分数的占比权重。

在 RAGFlow（或者大多数先进的 RAG 检索系统）中，混合检索（Hybrid Search）通常结合了全文检索（Sparse Retrieval，如 BM25）和向量检索（Dense Retrieval，如 Vector Embeddings）。这个参数就是用来平衡这两种检索结果的核心权重。

* 原理：混合检索的最终得分通常是通过公式计算的：
  最终得分 = (向量检索得分 * vector_weight) + (全文检索得分 * (1 - vector_weight))
* 取值范围：0.0 到 1.0 之间。
* 通俗解释：
  - 1.0：只以语义相似度进行案例搜索（向量检索），完全忽略关键词的精确字面匹配。
  - 0.0：只以字面关键词匹配进行案例搜索（全文检索），完全忽略同义词或相近语义的理解。
  - 0.85（默认值）：更倾向于语义匹配，保证在追问或同义表达时仍能稳定匹配到相应的查数案例（保留 15% 权重给关键词精确匹配）。

💡 调优建议：
* 如果案例中包含大量行业术语、人名、股票代码或特定型号等需要精准字面匹配的词，调低该值（如 0.3 ~ 0.5）。
* 如果用户提问多为日常用语、描述性问题或包含大量同义词，调高该值（如 0.7 ~ 0.85）。`,
    'embedchat_watermark_enabled': '开启后，将在嵌入式对话界面（EmbedChat）背景中平铺渲染防止信息截屏泄露的安全审计水印。',
    'embedchat_watermark_style': '水印的文字样式方案。可以选择【用户名 + 时间戳】或【自定义文字 + 时间戳】（两者均会自动附加当前时间戳）。',
    'embedchat_watermark_text': '当水印样式为【自定义文字 + 时间戳】时，在对话背景中平铺显示的自定义文本，末尾会自动追加时间戳。',
    'yovole_sso_enabled': '控制是否启用 Yovole SSO 统一登录。关闭后，登录页面的 SSO 登录将隐藏，且用户管理中的 SSO 同步按钮也将隐藏。',
    'audit_log_retention_days': '系统操作审计日志与智能体步骤级追踪 Trace 记录的物理保留天数。超出期限的整月历史分区会被自动 Drop 秒级清理以回收空间。',
    'embed_api_url': '全局 Embedding 服务的 API 接口网关地址。在本地模式（metadata_provider = local）下，此参数将在【本地元数据搜索】与【经验案例本地向量检索】场景中用于文本特征向量的在线生成计算。',
    'embed_api_key': '用于调用全局 Embedding 服务的身份验证 Key，请确保保密。',
    'embed_model_name': '全局 Embedding 服务的模型名称（例如 text-embedding-3-small 或 text-embedding-ada-002）。',
    'embed_dimensions': '全局 Embedding 模型输出的特征向量维度（例如 1024 或 1536）。需要与本地 Redis HNSW 向量索引创建时指定的维度完全一致。',
    'knowledge_ragflow_api_url': '对接的 RAGFlow 语义检索平台后端 API 服务地址，用于常规智能体的知识库问答检索。',
    'knowledge_ragflow_api_key': '用于与 RAGFlow 知识库服务进行安全 API 调用的身份验证令牌（API Key）。',
    'knowledge_ragflow_dataset_ids': '当前系统关联绑定的默认知识库 ID（可多选），用于为智能体问答检索背景文档和常识参考。',
    'knowledge_ragflow_similarity_threshold': `【相似度阈值 (knowledge_ragflow_similarity_threshold)】
这个参数是一个过滤器（门槛），用来决定什么样的知识库文档片段“足够相关”，可以被送给大模型。

在 RAGFlow（或者大多数先进的 RAG 检索系统）中，混合检索（Hybrid Search）通常结合了全文检索（Sparse Retrieval，如 BM25）和向量检索（Dense Retrieval，如 Vector Embeddings）。这个参数就是用来控制如何筛选这两种检索结果的核心阈值。

* 原理：系统计算出文档和用户问题的相似度分数（如余弦相似度）后，只有分数大于或等于该阈值的文档片段才会被保留，低于该分数的全部被丢弃。
* 取值范围：0.0 到 1.0 之间。
* 通俗解释：
  - 0.0：没有任何门槛。系统会把检索出的所有文档段落全部喂给大模型，不管它们是否真的相关。（容易引入大量噪音，导致大模型胡说八道）。
  - 0.9：门槛极高。只有和提问几乎一模一样的文档段落才能通过。（极易导致大模型找不到任何参考资料，回答“不知道”）。

💡 调优建议：
* 一般推荐设置在 0.2 到 0.4 之间作为起步（平台默认建议 0.20）。
* 如果发现大模型经常瞎编无关事实，说明阈值太低了混入了杂音，需要调高。
* 如果发现大模型经常明明有对应的知识，却回答“知识库中没有相关信息”，说明门槛太高了，需要调低。`,
    'knowledge_ragflow_vector_weight': `【向量权重 (knowledge_ragflow_vector_weight)】
该参数决定了在进行知识库混合检索时，向量语义检索结果所占的分数权重。

在 RAGFlow（或者大多数先进的 RAG 检索系统）中，混合检索（Hybrid Search）通常结合了全文检索（Sparse Retrieval，如 BM25）和向量检索（Dense Retrieval，如 Vector Embeddings）。这个参数就是用来平衡这两种检索结果的核心权重。

* 原理：混合检索的最终得分通常是通过公式计算的：
  最终得分 = (向量检索得分 * vector_weight) + (全文检索得分 * (1 - vector_weight))
* 取值范围：0.0 到 1.0 之间。
* 通俗解释：
  - 1.0：只看语义相关度（向量检索），完全忽略关键词的精确字面匹配。
  - 0.0：只看关键词匹配（全文检索），完全忽略同义词或相近语义的理解。
  - 0.30（默认值）：代表知识库检索更倾向于全文关键词匹配（占 70% 权重），对精准度要求较高。

💡 调优建议：
* 如果知识库多为技术文档、规格手册或包含大量专业代号，调低该值（如 0.2 ~ 0.3）。
* 如果问题比较多样化、偏口语表述，调高该值（如 0.6 ~ 0.7）以强化语义召回。`,
    'knowledge_ragflow_metadata_top_k': '知识库问答检索时，最大召回匹配的候选文档片段数。值越大参考条数越多，但会消耗更多的模型 Token。',
    'knowledge_base_enabled': '总开关：关闭后隐藏下方 RAGFlow 配置项，并禁用知识库管理、检索测试及智能体的 search_knowledge_base 工具。',
    'third_party_user_sync_config': '配置从外部数据源定时同步用户信息到本平台的参数。包含启用状态、连接源、表名、字段对应关系和同步周期。此配置已在【用户管理】页面统一维护，在此处仅提供只读展示。'
  }
  return tips[key] || ''
}

const openDatasetSelector = (item: ConfigItem) => {
    workingConfigItem.value = item

    // 根据 item.key 查找对应的 api_url 和 api_key
    let urlKey = 'knowledge_ragflow_api_url'
    let tokenKey = 'knowledge_ragflow_api_key'
    if (item.key === 'ragflow_dataset_ids') {
        urlKey = 'ragflow_api_url'
        tokenKey = 'ragflow_api_key'
    }

    let currentUrl = ''
    let currentKey = ''
    for (const list of Object.values(configGroups.value)) {
        const uItem = list.find(x => x.key === urlKey)
        if (uItem) currentUrl = uItem.value || ''
        const kItem = list.find(x => x.key === tokenKey)
        if (kItem) currentKey = kItem.value || ''
    }

    datasetSelectorUrl.value = currentUrl
    // 如果是掩码，传递空字符串，让后端自动读取数据库中的真实密钥
    datasetSelectorKey.value = currentKey.includes('****') ? '' : currentKey

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

const globalEmbedTesting = ref(false)
const testGlobalEmbed = async () => {
  globalEmbedTesting.value = true
  let url = ''
  let key = ''
  let model = ''
  for (const list of Object.values(configGroups.value)) {
    const uItem = list.find(x => x.key === 'embed_api_url')
    if (uItem) url = uItem.value
    const kItem = list.find(x => x.key === 'embed_api_key')
    if (kItem) key = kItem.value
    const mItem = list.find(x => x.key === 'embed_model_name')
    if (mItem) model = mItem.value
  }
  try {
    const response = await axios.post(`/api/portal/system/test-connection/global_embed`, {
      embed_api_url: url,
      embed_api_key: key,
      embed_model_name: model
    })
    const data = response.data
    if (data.status === 'success') {
      showToast('全局 Embedding 连通性测试成功，接口响应正常', 'success')
    } else {
      showToast(`测试连接失败: ${data.message}`, 'error')
    }
  } catch (error: any) {
    const msg = error.response?.data?.detail || error.message
    showToast(`测试请求失败: ${msg}`, 'error')
  } finally {
    globalEmbedTesting.value = false
  }
}

const handleDatasetSelect = (val: string | string[]) => {
    if (workingConfigItem.value) {
        workingConfigItem.value.value = Array.isArray(val) ? val.join(',') : val
    }
}

const getVisibleItems = (items: ConfigItem[] | undefined, category: string) => {
  if (!items) return []
  let list = [...items]
  if (category === 'agent') {
    const order = [
      'agent_max_context_messages',
      'agent_max_iterations',
      'llm_model_name',
      'llm_temperature',
      'embed_api_url',
      'embed_api_key',
      'embed_model_name',
      'embed_dimensions'
    ]
    list.sort((a, b) => {
      const idxA = order.indexOf(a.key)
      const idxB = order.indexOf(b.key)
      if (idxA !== -1 && idxB !== -1) return idxA - idxB
      if (idxA !== -1) return -1
      if (idxB !== -1) return 1
      return a.key.localeCompare(b.key)
    })
  }
  if (category === 'metadata') {
    if (metadataProvider.value === 'local') {
      list = list.filter(x => !['ragflow_api_url', 'ragflow_api_key'].includes(x.key))
    }
    const order = [
      'metadata_provider',
      'ragflow_api_url',
      'ragflow_api_key',
      'ragflow_similarity_threshold',
      'ragflow_vector_weight',
      'ragflow_metadata_top_k'
    ]
    list.sort((a, b) => {
      const idxA = order.indexOf(a.key)
      const idxB = order.indexOf(b.key)
      if (idxA !== -1 && idxB !== -1) return idxA - idxB
      if (idxA !== -1) return -1
      if (idxB !== -1) return 1
      return a.key.localeCompare(b.key)
    })
  }
  if (category === 'knowledge') {
    const enabledItem = list.find(x => x.key === 'knowledge_base_enabled')
    const enabled = (enabledItem?.value ?? 'true') === 'true'
    if (!enabled) {
      list = list.filter(x => x.key === 'knowledge_base_enabled')
    }
    const order = [
      'knowledge_base_enabled',
      'knowledge_ragflow_api_url',
      'knowledge_ragflow_api_key',
      'knowledge_ragflow_dataset_ids',
      'knowledge_ragflow_similarity_threshold',
      'knowledge_ragflow_vector_weight',
      'knowledge_ragflow_metadata_top_k'
    ]
    list.sort((a, b) => {
      const idxA = order.indexOf(a.key)
      const idxB = order.indexOf(b.key)
      if (idxA !== -1 && idxB !== -1) return idxA - idxB
      if (idxA !== -1) return -1
      if (idxB !== -1) return 1
      return a.key.localeCompare(b.key)
    })
  }
  if (category === 'data_api') {
    const chatbiKeys = [
      'chatbi_sample_knowledge_base',
      'chatbi_sample_top_k',
      'chatbi_sample_similarity_threshold',
      'chatbi_sample_vector_similarity_weight'
    ]
    const chatbiItems = list.filter(x => chatbiKeys.includes(x.key))
    chatbiItems.sort((a, b) => chatbiKeys.indexOf(a.key) - chatbiKeys.indexOf(b.key))
    const restItems = list.filter(x => !chatbiKeys.includes(x.key))

    const modeItemIndex = restItems.findIndex(x => x.key === 'sql_execution_mode')
    if (modeItemIndex !== -1) {
      const modeItem = restItems[modeItemIndex]
      if (modeItem) {
        let visibleRest = [...restItems]
        if (modeItem.value === 'local') {
          visibleRest = [modeItem]
        } else {
          visibleRest.splice(modeItemIndex, 1)
          visibleRest.unshift(modeItem)
        }
        list = [...chatbiItems, ...visibleRest]
      }
    } else {
      list = [...chatbiItems, ...restItems]
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

// --- Log Management Logic ---
const retentionDays = ref(90)
const partitions = ref<any[]>([])
const loadingPartitions = ref(false)
const savingLogConfig = ref(false)
const clearingLogs = ref(false)
const showCleanupConfirm = ref(false)

const fetchLogConfig = async () => {
  try {
    const res = await axios.get('/api/portal/system/logs/config')
    retentionDays.value = res.data.audit_log_retention_days
  } catch (e: any) {
    console.error('Failed to fetch log config:', e)
  }
}

const saveLogConfig = async () => {
  savingLogConfig.value = true
  try {
    await axios.post('/api/portal/system/logs/config', {
      audit_log_retention_days: Number(retentionDays.value)
    })
    showToast('日志保留配置保存成功', 'success')
  } catch (e: any) {
    showToast(`保存失败: ${e.response?.data?.detail || e.message}`, 'error')
  } finally {
    savingLogConfig.value = false
  }
}

const fetchPartitions = async () => {
  loadingPartitions.value = true
  try {
    const res = await axios.get('/api/portal/system/logs/partitions')
    partitions.value = res.data
  } catch (e: any) {
    showToast('获取日志分区信息失败', 'error')
  } finally {
    loadingPartitions.value = false
  }
}

const triggerCleanup = async () => {
  clearingLogs.value = true
  showCleanupConfirm.value = false
  try {
    const res = await axios.post('/api/portal/system/logs/cleanup')
    if (res.data.status === 'success') {
      showToast('日志手动清理成功', 'success')
      await fetchPartitions()
    } else {
      showToast(`清理跳过: ${res.data.message}`, 'info')
    }
  } catch (e: any) {
    showToast(`清理失败: ${e.response?.data?.detail || e.message}`, 'error')
  } finally {
    clearingLogs.value = false
  }
}

// --- Redis Browser Logic ---
const redisPattern = ref('*')
const redisKeys = ref<{ name: string; type: string }[]>([])
const redisKeysLoading = ref(false)
const selectedRedisKey = ref<string | null>(null)
const redisKeyDetail = ref<{ name: string; type: string; ttl: number; value: any } | null>(null)
const redisDetailLoading = ref(false)
const showDeleteKeyConfirm = ref(false)
const pendingDeleteKey = ref<string | null>(null)

const fetchRedisKeys = async () => {
  redisKeysLoading.value = true
  redisKeys.value = []
  redisKeyDetail.value = null
  selectedRedisKey.value = null
  try {
    const res = await axios.get('/api/portal/system/redis/keys-list', {
      params: { pattern: redisPattern.value || '*' }
    })
    redisKeys.value = res.data.keys || []
  } catch (e: any) {
    showToast(`获取 Redis Keys 失败: ${e.response?.data?.detail || e.message}`, 'error')
  } finally {
    redisKeysLoading.value = false
  }
}

const fetchRedisKeyDetail = async (key: string) => {
  selectedRedisKey.value = key
  redisDetailLoading.value = true
  redisKeyDetail.value = null
  try {
    const res = await axios.get('/api/portal/system/redis/key-detail', { params: { key } })
    redisKeyDetail.value = res.data
  } catch (e: any) {
    showToast(`获取键详情失败: ${e.response?.data?.detail || e.message}`, 'error')
  } finally {
    redisDetailLoading.value = false
  }
}

const formatRedisValue = (value: any): string => {
  if (value === null || value === undefined) return '(null)'
  if (typeof value === 'string') {
    try {
      return JSON.stringify(JSON.parse(value), null, 2)
    } catch {
      return value
    }
  }
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

const confirmDeleteKey = (key: string) => {
  pendingDeleteKey.value = key
  showDeleteKeyConfirm.value = true
}

const executeDeleteKey = async () => {
  if (!pendingDeleteKey.value) return
  showDeleteKeyConfirm.value = false
  try {
    await axios.delete('/api/portal/system/redis/key', { params: { key: pendingDeleteKey.value } })
    showToast(`已删除键: ${pendingDeleteKey.value}`, 'success')
    redisKeyDetail.value = null
    selectedRedisKey.value = null
    await fetchRedisKeys()
  } catch (e: any) {
    showToast(`删除失败: ${e.response?.data?.detail || e.message}`, 'error')
  } finally {
    pendingDeleteKey.value = null
  }
}

onMounted(() => {
  fetchConfigs()
  fetchBrandingConfig()
  fetchModelsForConfigs()
  if (userInfo.value?.role === 'admin') {
    fetchLogConfig()
    fetchPartitions()
  }
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
           @click="activeTab = 'branding'"
           class="px-4 py-2 rounded-md text-sm font-medium transition-all duration-200 flex items-center"
           :class="activeTab === 'branding' ? 'bg-white shadow text-primary' : 'text-gray-500 hover:text-gray-700'"
         >
           <PaintBrushIcon class="w-4 h-4 mr-2" />
           品牌个性化
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
         <button
            v-if="userInfo?.role === 'admin'"
            @click="activeTab = 'logs'"
            class="px-4 py-2 rounded-md text-sm font-medium transition-all duration-200 flex items-center"
            :class="activeTab === 'logs' ? 'bg-white shadow text-primary' : 'text-gray-500 hover:text-gray-700'"
          >
            <CircleStackIcon class="w-4 h-4 mr-2" />
            日志管理
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

        <!-- LOGS TAB -->
       <div v-else-if="activeTab === 'logs' && userInfo?.role === 'admin'" class="space-y-6 h-full overflow-y-auto pb-12 custom-scrollbar">
         <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
           <!-- Left: Config -->
           <div class="bg-white shadow rounded-lg p-6 space-y-6">
             <div>
               <h3 class="text-lg font-medium text-gray-900 flex items-center">
                 <Cog6ToothIcon class="w-5 h-5 mr-2 text-primary" />
                 日志保留策略
               </h3>
               <p class="text-sm text-gray-500 mt-1">控制系统操作审计日志与 Trace 步骤的物理留存时长</p>
             </div>
             
             <div class="space-y-4">
               <div>
                 <label class="block text-sm font-medium text-gray-700">日志保留天数</label>
                 <div class="mt-1 flex rounded-md shadow-sm">
                   <input
                     type="number"
                     v-model.number="retentionDays"
	                     @keypress="!/[0-9]/.test(($event as KeyboardEvent).key) && ($event as KeyboardEvent).preventDefault()"
	                     @input="retentionDays && typeof retentionDays === 'number' ? retentionDays = Math.floor(retentionDays) : undefined"
                     min="1"
                     max="3650"
                     :disabled="!canSave"
                     class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-l-md bg-gray-100 p-2 disabled:opacity-70 disabled:cursor-not-allowed"
                   />
                   <span class="inline-flex items-center px-3 rounded-r-md border border-l-0 border-gray-300 bg-gray-50 text-gray-500 text-sm">
                     天
                   </span>
                 </div>
                 <p class="text-xs text-gray-400 mt-1.5 leading-relaxed">
                   * 日志超出天数后，后台定时任务（Scheduler）会在每日凌晨 2:00 自动物理 Drop 过期的整月分区进行无损回收。
                 </p>
               </div>
               
               <button
                 @click="saveLogConfig"
                 :disabled="savingLogConfig || !canSave"
                 class="w-full flex justify-center py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-primary hover:bg-indigo-700 disabled:opacity-50"
               >
                 {{ savingLogConfig ? '保存中...' : '保存配置' }}
               </button>
             </div>

             <div class="border-t border-gray-100 pt-6">
               <h4 class="text-sm font-semibold text-gray-900">手动清理</h4>
               <p class="text-xs text-gray-500 mt-1">立即手动触发一次日志清理机制，系统会自动检查并释放超出配置天数的历史数据。</p>
               <button
                 @click="showCleanupConfirm = true"
                 :disabled="clearingLogs || !canSave"
                 class="mt-3 w-full flex justify-center py-2 px-4 border border-red-300 rounded-md shadow-sm text-sm font-medium text-red-700 bg-red-50 hover:bg-red-100 disabled:opacity-50"
               >
                 <TrashIcon class="h-4 w-4 mr-2" />
                 {{ clearingLogs ? '正在清理...' : '立即手动清理' }}
               </button>
             </div>
           </div>

           <!-- Right: Partitions -->
           <div class="bg-white shadow rounded-lg p-6 lg:col-span-2 flex flex-col justify-between">
             <div class="mb-4 flex items-center justify-between">
               <div>
                 <h3 class="text-lg font-medium text-gray-900 flex items-center">
                   <CircleStackIcon class="w-5 h-5 mr-2 text-primary" />
                   日志表分区状态
                 </h3>
                 <p class="text-sm text-gray-500 mt-1">显示目前已自动挂载的分区表（MySQL Range Partitions）</p>
               </div>
               <button
                 @click="fetchPartitions"
                 :disabled="loadingPartitions"
                 class="inline-flex items-center px-3 py-1.5 border border-gray-300 rounded-md text-xs font-medium text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50"
               >
                 <span v-if="loadingPartitions" class="animate-spin h-3.5 w-3.5 mr-1 border-2 border-gray-400 border-t-transparent rounded-full"></span>
                 刷新状态
               </button>
             </div>

             <div class="overflow-x-auto flex-1 min-h-[300px]">
               <table class="min-w-full divide-y divide-gray-100 text-sm">
                 <thead>
                   <tr class="text-left text-xs font-medium text-gray-500 whitespace-nowrap bg-gray-50">
                     <th class="py-2.5 px-4">物理数据表</th>
                     <th class="py-2.5 px-4">分区名称</th>
                     <th class="py-2.5 px-4">数据承载范围</th>
                     <th class="py-2.5 px-4 text-right">估算行数 (TABLE_ROWS)</th>
                   </tr>
                 </thead>
                 <tbody class="divide-y divide-gray-50 text-gray-700">
                   <tr v-if="partitions.length === 0 && !loadingPartitions">
                     <td colspan="4" class="py-12 text-center text-gray-400 italic">暂无分区数据（或系统运行在未分区单表模式下）</td>
                   </tr>
                   <tr v-for="(p, index) in partitions" :key="index" class="hover:bg-gray-50/50 transition-colors">
                     <td class="py-3 px-4 font-mono text-xs text-gray-900">{{ p.table_name }}</td>
                     <td class="py-3 px-4">
                       <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-indigo-50 text-indigo-700 border border-indigo-100 font-mono">
                         {{ p.partition_name }}
                       </span>
                     </td>
                     <td class="py-3 px-4 text-gray-500 text-xs">{{ p.data_range }}</td>
                     <td class="py-3 px-4 text-right font-mono text-gray-900 font-medium">{{ p.table_rows.toLocaleString() }}</td>
                   </tr>
                 </tbody>
               </table>
             </div>
           </div>
         </div>
       </div>

       <!-- DIAGNOSTICS TAB -->
       <div v-else-if="activeTab === 'diagnostics'" class="grid grid-cols-1 lg:grid-cols-3 gap-6 h-full overflow-y-auto pb-6">
        <!-- Left Column: Connection Checks -->
        <div class="space-y-6 lg:col-span-1">
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
            <div class="border-t border-gray-100 pt-4 mt-2 flex flex-col gap-2">
              <button @click="testConnection('redis')" :disabled="loading.redis || !canSave" class="w-full inline-flex justify-center items-center py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-red-600 hover:bg-red-700 focus:outline-none disabled:opacity-50 whitespace-nowrap">
                <PlayIcon v-if="!loading.redis" class="h-4 w-4 mr-2 shrink-0" />
                <span v-else class="animate-spin h-4 w-4 mr-2 border-2 border-white border-t-transparent rounded-full shrink-0"></span>
                {{ loading.redis ? '测试中...' : '测试连接' }}
              </button>
               <button @click="scanRedisKeys" :disabled="loading.redis_scan || !canSave" class="w-full inline-flex justify-center items-center py-2 px-4 border border-gray-300 rounded-md shadow-sm text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50 whitespace-nowrap">
                <MagnifyingGlassIcon v-if="!loading.redis_scan" class="h-4 w-4 mr-2 shrink-0" />
                <span v-else class="animate-spin h-4 w-4 mr-2 border-2 border-gray-400 border-t-transparent rounded-full shrink-0"></span>
                {{ loading.redis_scan ? '扫描中...' : '扫描 Keys' }}
              </button>
              <button @click="openClearConfirm" :disabled="!canSave" class="w-full inline-flex justify-center items-center py-2 px-4 border border-red-300 rounded-md shadow-sm text-sm font-medium text-red-700 bg-red-50 hover:bg-red-100 disabled:opacity-50 whitespace-nowrap">
                <TrashIcon class="h-4 w-4 mr-2 shrink-0" />
                清理 Keys
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
            <button
              @click="openRebuildConfirm"
              :disabled="loading.rebuild_vector || !canSave"
              class="inline-flex justify-center items-center py-2 px-4 border border-rose-200 rounded-md shadow-sm text-sm font-medium text-rose-700 bg-rose-50 hover:bg-rose-100 disabled:opacity-50 ml-3"
            >
              <ArrowPathIcon v-if="!loading.rebuild_vector" class="h-4 w-4 mr-2" />
              <span v-else class="animate-spin h-4 w-4 mr-2 border-2 border-rose-400 border-t-transparent rounded-full"></span>
              {{ loading.rebuild_vector ? '重构中...' : '重构本地向量数据' }}
            </button>
          </div>
        </div>
        <!-- Right Column: Console Output / Redis Browser -->
        <div class="lg:col-span-2 bg-white rounded-lg shadow flex flex-col h-[600px] border border-gray-100 overflow-hidden">
          <div class="bg-gray-50 px-4 py-2.5 flex justify-between items-center border-b border-gray-200 flex-shrink-0">
            <div class="flex space-x-2">
              <button 
                @click="diagSubTab = 'console'"
                class="px-3 py-1.5 rounded-md text-xs font-semibold transition-all flex items-center"
                :class="diagSubTab === 'console' ? 'bg-white shadow text-primary border border-gray-100' : 'text-gray-500 hover:text-gray-700'"
              >
                <CommandLineIcon class="w-3.5 h-3.5 mr-1.5" />
                诊断控制台
              </button>
              <button 
                @click="diagSubTab = 'redis'"
                class="px-3 py-1.5 rounded-md text-xs font-semibold transition-all flex items-center"
                :class="diagSubTab === 'redis' ? 'bg-white shadow text-primary border border-gray-100' : 'text-gray-500 hover:text-gray-700'"
              >
                <CircleStackIcon class="w-3.5 h-3.5 mr-1.5" />
                Redis浏览器
              </button>
            </div>
            <button v-if="diagSubTab === 'console'" @click="clearLogs" class="text-xs text-gray-400 hover:text-gray-600">清空</button>
          </div>
          
          <!-- Tab: Console -->
          <div v-if="diagSubTab === 'console'" class="flex-1 bg-gray-950 p-4 overflow-y-auto font-mono text-sm space-y-1 custom-scrollbar text-green-400">
            <div v-if="logs.length === 0" class="text-gray-500 italic">等待执行测试...</div>
            <div v-else v-for="(log, index) in logs" :key="index" class="text-green-400 break-all">
              <span class="text-gray-500 mr-2">></span>{{ log }}
            </div>
          </div>

          <!-- Tab: Redis Browser -->
          <div v-else-if="diagSubTab === 'redis'" class="flex-1 flex space-x-4 overflow-hidden p-4 bg-gray-50">
            <!-- Left Column: Keys list -->
            <div class="w-2/5 bg-white border border-gray-200 rounded-lg p-3 flex flex-col h-full overflow-hidden">
              <div class="mb-3 flex items-center space-x-2 flex-shrink-0">
                <input
                  type="text"
                  v-model="redisPattern"
                  placeholder="匹配模式 (例如 * 或 nanzi:*)"
                  @keyup.enter="fetchRedisKeys"
                  class="flex-1 min-w-0 shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-50 p-2 border"
                />
                <button
                  @click="fetchRedisKeys"
                  :disabled="redisKeysLoading"
                  class="inline-flex items-center px-3 py-2 border border-transparent text-sm leading-4 font-medium rounded-md text-white bg-primary hover:bg-indigo-700 disabled:opacity-50"
                >
                  <span v-if="redisKeysLoading" class="animate-spin h-4 w-4 border-2 border-white border-t-transparent rounded-full mr-1"></span>
                  搜索
                </button>
              </div>
              
              <div class="flex-1 overflow-y-auto min-h-0 custom-scrollbar border border-gray-100 rounded-md">
                <div v-if="redisKeys.length === 0 && !redisKeysLoading" class="p-6 text-center text-gray-400 italic text-sm">
                  无匹配的 Redis Keys
                </div>
                <div v-else-if="redisKeysLoading" class="p-12 text-center text-gray-400 flex flex-col items-center">
                  <span class="animate-spin h-8 w-8 border-2 border-primary border-t-transparent rounded-full mb-2"></span>
                  正在扫描键名...
                </div>
                <div v-else class="divide-y divide-gray-100">
                  <div
                    v-for="key in redisKeys"
                    :key="key.name"
                    @click="fetchRedisKeyDetail(key.name)"
                    class="px-2.5 py-2 hover:bg-gray-50 cursor-pointer flex items-center justify-between transition-colors duration-150"
                    :class="selectedRedisKey === key.name ? 'bg-indigo-50/70 hover:bg-indigo-50' : ''"
                  >
                    <span class="text-xs font-mono break-all text-gray-700 font-medium select-all" :class="selectedRedisKey === key.name ? 'text-primary font-bold' : ''">
                      {{ key.name }}
                    </span>
                    <span class="ml-2 shrink-0 px-2 py-0.5 rounded text-[10px] font-bold uppercase" :class="
                      key.type === 'string' ? 'bg-green-50 text-green-700 border border-green-100' :
                      key.type === 'hash' ? 'bg-blue-50 text-blue-700 border border-blue-100' :
                      key.type === 'list' ? 'bg-yellow-50 text-yellow-700 border border-yellow-100' :
                      'bg-gray-50 text-gray-600 border border-gray-100'
                    ">
                      {{ key.type }}
                    </span>
                  </div>
                </div>
              </div>
              <div class="mt-2 text-[10px] text-gray-400 font-mono text-right flex-shrink-0">
                显示最多 5000 条结果
              </div>
            </div>

            <!-- Right Column: Key detail -->
            <div class="flex-1 bg-white border border-gray-200 rounded-lg p-4 flex flex-col h-full overflow-hidden">
              <div v-if="redisDetailLoading" class="flex-1 flex flex-col items-center justify-center">
                <span class="animate-spin h-8 w-8 border-2 border-primary border-t-transparent rounded-full mb-2"></span>
                <p class="text-gray-400 text-xs">正在加载详情...</p>
              </div>
              <div v-else-if="redisKeyDetail" class="flex flex-col h-full min-h-0">
                <!-- Header detail info -->
                <div class="border-b border-gray-100 pb-3 mb-3 flex items-start justify-between flex-shrink-0">
                  <div class="space-y-1 min-w-0 pr-2">
                    <div class="flex items-center space-x-2">
                      <h3 class="text-sm font-bold text-gray-900 break-all font-mono select-all">
                        {{ redisKeyDetail.name }}
                      </h3>
                    </div>
                    <div class="flex items-center space-x-2 text-[10px]">
                      <span class="px-1.5 py-0.5 rounded-full font-bold uppercase bg-indigo-50 text-indigo-700 border border-indigo-100">
                        {{ redisKeyDetail.type }}
                      </span>
                      <span class="font-mono text-gray-500">
                        TTL: {{ redisKeyDetail.ttl === -1 ? '永不过期 (-1)' : redisKeyDetail.ttl === -2 ? '已过期 (-2)' : `${redisKeyDetail.ttl} 秒` }}
                      </span>
                    </div>
                  </div>
                  
                  <button
                    @click="confirmDeleteKey(redisKeyDetail.name)"
                    title="删除此键"
                    class="inline-flex items-center p-1.5 border border-red-200 rounded-md text-red-700 bg-red-50 hover:bg-red-100 transition-colors shadow-sm"
                  >
                    <TrashIcon class="h-3.5 w-3.5" />
                  </button>
                </div>

                <!-- Value area -->
                <div class="flex-1 min-h-0 overflow-y-auto bg-gray-950 rounded-lg p-3 font-mono text-[11px] text-green-400 custom-scrollbar border border-gray-950">
                  <pre class="whitespace-pre-wrap break-all select-text selection:bg-indigo-500/30">{{ formatRedisValue(redisKeyDetail.value) }}</pre>
                </div>
              </div>
              <div v-else class="flex-1 flex flex-col items-center justify-center text-gray-400">
                <CircleStackIcon class="h-10 w-10 text-gray-200 mb-2" />
                <p class="text-xs">请从左侧列表选择一个 Key 查看详细内容</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- BRANDING TAB -->
      <div v-else-if="activeTab === 'branding'" class="h-full overflow-y-auto pb-6 custom-scrollbar">
        <div class="max-w-3xl space-y-6">
          <div class="flex items-center justify-between bg-white shadow rounded-lg p-6">
            <div>
              <h3 class="text-lg font-bold text-gray-900">品牌个性化</h3>
              <p class="text-sm text-gray-500 mt-1">开启后可自定义产品名称、登录页、图标与联系信息</p>
            </div>
            <Switch v-model="brandingConfig.enabled" :disabled="!canSave" />
          </div>

          <fieldset :disabled="!brandingConfig.enabled || !canSave" class="bg-white shadow rounded-lg p-6 space-y-5 disabled:opacity-60">
            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">产品名称</label>
              <input
                v-model="brandingConfig.product_name"
                type="text"
                class="block w-full rounded-lg border-gray-300 shadow-sm focus:border-primary focus:ring-primary text-sm"
                placeholder="南孜 · 智能体平台"
              />
              <p class="text-xs text-gray-400 mt-1">影响浏览器标题、左侧菜单栏名称、登录页</p>
            </div>

            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">登录页副标题</label>
              <input
                v-model="brandingConfig.login_subtitle"
                type="text"
                class="block w-full rounded-lg border-gray-300 shadow-sm focus:border-primary focus:ring-primary text-sm"
                placeholder="NanZi Intelligent Agent Platform"
              />
            </div>

            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">默认智能助手名称</label>
              <input
                v-model="brandingConfig.default_agent_name"
                type="text"
                class="block w-full rounded-lg border-gray-300 shadow-sm focus:border-primary focus:ring-primary text-sm"
                placeholder="南孜智能助手"
              />
              <p class="text-xs text-gray-400 mt-1">影响未开启品牌个性化时或未指定时的智能助手默认名称（例如：Nexus AI）</p>
            </div>

            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">Logo / Favicon</label>
              <div class="flex items-center gap-4">
                <img
                  :src="brandingConfig.icon_url || '/favicon.png'"
                  alt="Logo 预览"
                  class="w-12 h-12 rounded-lg border border-gray-200 object-cover bg-white"
                />
                <div class="flex-1 space-y-2">
                  <input
                    v-model="brandingConfig.icon_url"
                    type="text"
                    class="block w-full rounded-lg border-gray-300 shadow-sm focus:border-primary focus:ring-primary text-sm font-mono"
                    placeholder="/favicon.png 或 /branding/icon.png"
                  />
                  <button
                    type="button"
                    class="text-sm text-primary hover:text-primary/80 disabled:opacity-50"
                    :disabled="brandingIconUploading || !brandingConfig.enabled"
                    @click="triggerBrandingIconUpload"
                  >
                    {{ brandingIconUploading ? '上传中...' : '上传图片' }}
                  </button>
                  <input
                    ref="brandingIconInput"
                    type="file"
                    accept="image/png,image/jpeg,image/webp,image/svg+xml"
                    class="hidden"
                    @change="onBrandingIconSelected"
                  />
                </div>
              </div>
              <p class="text-xs text-gray-400 mt-1">用于登录页、侧栏左上角与浏览器标签图标（PNG/JPEG/WebP/SVG，最大 512KB）</p>
            </div>

            <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 py-2 border-t border-gray-100">
              <div>
                <p class="text-sm font-medium text-gray-700">隐藏登录页 SSO</p>
                <p class="text-xs text-gray-400">开启后登录页不再显示 SSO 登录 Tab</p>
              </div>
              <Switch v-model="brandingConfig.hide_login_sso" />
            </div>

            <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 py-2 border-t border-gray-100">
              <div>
                <p class="text-sm font-medium text-gray-700">隐藏版本号外链</p>
                <p class="text-xs text-gray-400">开启后侧栏版本号不再链接到 GitHub，并隐藏 GitHub 图标</p>
              </div>
              <Switch v-model="brandingConfig.hide_version_link" />
            </div>

            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">联系信息（Markdown）</label>
              <textarea
                v-model="brandingConfig.contact_markdown"
                rows="8"
                class="block w-full rounded-lg border-gray-300 shadow-sm focus:border-primary focus:ring-primary text-sm font-mono"
                placeholder="支持 Markdown，将在「个人中心 → 我的权限 → 关于」中展示"
              />
            </div>

            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">版权信息</label>
              <input
                v-model="brandingConfig.copyright_text"
                type="text"
                class="block w-full rounded-lg border-gray-300 shadow-sm focus:border-primary focus:ring-primary text-sm"
                placeholder="© 2026 公司名称 · All Rights Reserved"
              />
              <p class="text-xs text-gray-400 mt-1">启用品牌个性化后，显示在登录页底部（小字展示，支持换行）</p>
            </div>
          </fieldset>

          <div class="flex justify-end">
            <button
              type="button"
              :disabled="brandingSaving || !canSave"
              class="inline-flex items-center px-6 py-2 text-sm font-medium rounded-md text-white bg-primary hover:bg-primary/90 disabled:opacity-50"
              @click="saveBrandingConfig"
            >
              {{ brandingSaving ? '保存中...' : '保存品牌配置' }}
            </button>
          </div>
        </div>
      </div>

      <!-- CONFIGS TAB -->
      <div v-else-if="activeTab === 'configs'" class="h-full overflow-y-auto pb-6 custom-scrollbar">
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
                    <div v-if="category === 'agent'" class="bg-amber-50 border-l-4 border-amber-400 p-4 rounded-md text-sm text-amber-900 flex items-start space-x-2 mb-4">
                       <span class="text-amber-500 font-bold shrink-0">⚠️ 提示：</span>
                       <div>
                          如果在此处变更了全局 <strong>Embedding 模型名</strong> 或 <strong>向量维度</strong>，已有的向量数据（包括本地元数据和经验案例集）必须进行重新向量化重建，否则无法正常进行相似度检索。保存变更后，请前往 <strong>【系统诊断】</strong> 标签页执行 <strong>【重构本地向量数据】</strong> 即可。
                       </div>
                    </div>
                    <div v-if="category === 'knowledge' && !isKnowledgeFeatureEnabled" class="bg-gray-50 border-l-4 border-gray-300 p-4 rounded-md text-sm text-gray-600 flex items-start space-x-2 mb-4">
                       <span class="text-gray-400 font-bold shrink-0">ℹ️</span>
                       <div>
                          知识库功能已<strong>关闭</strong>。开启「knowledge_base_enabled」后将显示 RAGFlow 连接与检索参数，并启用知识库管理、检索测试与智能体知识库检索工具。
                       </div>
                    </div>
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
                              <select v-model="item.value" :disabled="isConfigItemDisabled(String(category), item)" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 p-2 disabled:opacity-70 disabled:cursor-not-allowed">
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
                              <div class="flex items-center gap-2">
                                <select v-model="item.value" :disabled="isConfigItemDisabled(String(category), item)" class="shadow-sm focus:ring-primary focus:border-primary block flex-1 min-w-0 sm:text-sm border-gray-300 rounded-md bg-gray-100 p-2 disabled:opacity-70 disabled:cursor-not-allowed">
                                   <option value="local">local (本地元数据)</option>
                                   <option value="ragflow">ragflow (语义检索 RAG)</option>
                                </select>
                                <button
                                  v-if="item.value === 'local'"
                                  type="button"
                                  @click="openRebuildConfirm"
                                  :disabled="loading.rebuild_vector || !canSave"
                                  class="inline-flex shrink-0 items-center justify-center py-2 px-3 border border-rose-200 rounded-md shadow-sm text-sm font-medium text-rose-700 bg-rose-50 hover:bg-rose-100 disabled:opacity-50 whitespace-nowrap"
                                  title="重建本地 Redis 元数据与案例向量索引并全量同步"
                                >
                                  <ArrowPathIcon v-if="!loading.rebuild_vector" class="h-4 w-4 mr-1.5" />
                                  <span v-else class="animate-spin h-4 w-4 mr-1.5 border-2 border-rose-400 border-t-transparent rounded-full"></span>
                                  {{ loading.rebuild_vector ? '重构中...' : '一键重构' }}
                                </button>
                              </div>
                              <div v-if="item.value === 'local'" class="mt-2 text-xs text-blue-700 bg-blue-50/50 p-3 rounded-xl border border-blue-100/50 leading-relaxed select-none">
                                  💡 <strong>本地元数据模式：</strong>直接在本地查询由元数据字典维护的表和字段，并使用全局 Embedding 算法计算向量，通过<strong>本地 Redis (HNSW) 向量索引</strong>进行高速检索，<strong>无需配置下方的 RAGFlow 地址与密钥</strong>。首次启用、变更 Embedding 模型/维度或索引异常时，请点击右侧<strong>「一键重构」</strong>手动触发全量向量化。
                              </div>
                              <div v-else-if="item.value === 'ragflow'" class="mt-2 text-xs text-amber-700 bg-amber-50/50 p-3 rounded-xl border border-amber-100/50 leading-relaxed select-none">
                                  💡 <strong>RAGFlow 语义检索模式：</strong>需要将本地元数据字典一键同步至 RAGFlow 系统，系统在检索表和字段的描述信息时会调用下方配置的 RAGFlow 网关地址与 API 密钥进行全文 + 向量的混合检索。
                              </div>
                          </div>
                          <div v-else-if="item.key === 'sql_execution_mode'">
                             <select v-model="item.value" :disabled="isConfigItemDisabled(String(category), item)" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 p-2 disabled:opacity-70 disabled:cursor-not-allowed">
                                <option value="remote">remote (走远程执行服务)</option>
                                <option value="local">local (本地数据源直连执行)</option>
                             </select>
                          </div>
                          <div v-else-if="item.is_secret" class="relative">
                             <input :type="showSecrets[item.key] ? 'text' : 'password'" v-model="item.value" :disabled="isConfigItemDisabled(String(category), item)" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md pr-10 bg-gray-100 disabled:opacity-70 disabled:cursor-not-allowed" />
                             <div @click="toggleSecret(item.key)" class="absolute inset-y-0 right-0 pr-3 flex items-center cursor-pointer text-gray-400">
                                <EyeIcon v-if="!showSecrets[item.key]" class="h-5 w-5" />
                                <EyeSlashIcon v-else class="h-5 w-5" />
                             </div>
                          </div>
                          <div v-else-if="item.key === 'third_party_user_sync_config'">
                              <div class="border border-gray-200/80 rounded-xl p-4 bg-gray-50/50 space-y-4 shadow-inner">
                                 <!-- 状态与定时自动同步 -->
                                 <div class="flex flex-wrap items-center justify-between gap-3" :class="{ 'pb-3 border-b border-gray-200/60': showUserSyncDetail }">
                                    <div class="flex flex-wrap items-center gap-x-6 gap-y-2">
                                       <div class="flex items-center gap-2">
                                          <span class="text-xs font-semibold text-gray-500">同步状态:</span>
                                          <span v-if="parseJson(item.value)?.enabled" class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-100 text-emerald-800 border border-emerald-200">
                                             <span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
                                             已启用
                                          </span>
                                          <span v-else class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-800 border border-gray-200">
                                             <span class="w-1.5 h-1.5 rounded-full bg-gray-400"></span>
                                             已禁用
                                          </span>
                                       </div>
                                       <div class="flex items-center gap-2">
                                          <span class="text-xs font-semibold text-gray-500">定时周期:</span>
                                          <span class="text-xs font-medium text-gray-600 bg-gray-100/80 px-2.5 py-0.5 rounded border border-gray-200">
                                             {{ 
                                                parseJson(item.value)?.schedule === 'off' ? '未开启定时自动同步' :
                                                parseJson(item.value)?.schedule === 'hourly' ? '每小时自动同步' :
                                                parseJson(item.value)?.schedule === 'daily' ? '每日凌晨 2:00 同步' :
                                                parseJson(item.value)?.schedule === 'weekly' ? '每周一凌晨 2:00 同步' : '未开启'
                                             }}
                                          </span>
                                       </div>
                                    </div>

                                    <!-- Toggle Details Button -->
                                    <button 
                                       @click="showUserSyncDetail = !showUserSyncDetail"
                                       class="inline-flex items-center gap-1 text-[11px] font-medium text-gray-500 hover:text-indigo-600 hover:bg-white active:bg-gray-100 border border-gray-200 rounded-md px-2 py-1 transition-all focus:outline-none select-none shadow-sm cursor-pointer"
                                    >
                                       <span>{{ showUserSyncDetail ? '收起配置详情' : '查看配置详情' }}</span>
                                       <svg 
                                          class="w-3.5 h-3.5 transform transition-transform duration-200" 
                                          :class="{ 'rotate-180': showUserSyncDetail }"
                                          fill="none" stroke="currentColor" viewBox="0 0 24 24"
                                       >
                                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                                       </svg>
                                    </button>
                                 </div>
                                 
                                 <!-- 可折叠详细参数信息 -->
                                 <div v-show="showUserSyncDetail" class="space-y-4 pt-1 transition-all duration-300">
                                    <!-- 数据源与对应表 -->
                                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3.5 text-xs">
                                       <div class="flex items-center justify-between bg-white px-3 py-2 rounded-lg border border-gray-150">
                                          <span class="font-medium text-gray-500">外部数据源 ID:</span>
                                          <span class="font-mono text-gray-800 font-bold bg-slate-50 px-2 py-0.5 rounded border border-slate-200">{{ parseJson(item.value)?.connection_config_id || '未配置' }}</span>
                                       </div>
                                       <div class="flex items-center justify-between bg-white px-3 py-2 rounded-lg border border-gray-150">
                                          <span class="font-medium text-gray-500">外部用户表名:</span>
                                          <span class="font-mono text-indigo-700 font-semibold bg-indigo-50/30 px-2 py-0.5 rounded border border-indigo-100/60">{{ parseJson(item.value)?.table_name || '未配置' }}</span>
                                       </div>
                                    </div>

                                    <!-- 核心字段映射 -->
                                    <div class="space-y-2">
                                       <span class="text-xs font-semibold text-gray-700 block">核心字段映射:</span>
                                       <div class="bg-white rounded-lg border border-gray-150 divide-y divide-gray-100 overflow-hidden shadow-sm">
                                          <div class="grid grid-cols-[110px_1fr] px-3.5 py-2.5 text-xs items-center">
                                             <span class="text-gray-500 font-medium">用户名 (user_name):</span>
                                             <span class="font-mono text-gray-800 bg-gray-100 px-2 py-0.5 rounded w-max border border-gray-200/60">{{ parseJson(item.value)?.field_map?.user_name || '未配置' }}</span>
                                          </div>
                                          <div class="grid grid-cols-[110px_1fr] px-3.5 py-2.5 text-xs items-center">
                                             <span class="text-gray-500 font-medium">真实姓名 (real_name):</span>
                                             <span class="font-mono text-gray-800 bg-gray-100 px-2 py-0.5 rounded w-max border border-gray-200/60">{{ parseJson(item.value)?.field_map?.real_name || '未配置' }}</span>
                                          </div>
                                          <div class="grid grid-cols-[110px_1fr] px-3.5 py-2.5 text-xs items-center">
                                             <span class="text-gray-500 font-medium">备注说明 (remark):</span>
                                             <span class="font-mono text-gray-800 bg-gray-100 px-2 py-0.5 rounded w-max border border-gray-200/60">{{ parseJson(item.value)?.field_map?.remark || '未配置' }}</span>
                                          </div>
                                       </div>
                                    </div>

                                    <!-- 额外字段扩展 -->
                                    <div v-if="parseJson(item.value)?.extra_data_mappings?.length" class="space-y-2 pt-1">
                                       <span class="text-xs font-semibold text-gray-700 block">扩展字段同步:</span>
                                       <div class="flex flex-wrap gap-2">
                                          <span v-for="map in parseJson(item.value).extra_data_mappings" :key="map.json_key" class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-slate-100/80 text-slate-700 border border-slate-200/60 text-[11px] font-mono shadow-sm">
                                             {{ map.json_key }} 
                                             <span class="text-slate-400">←</span>
                                             {{ map.source_column }}
                                          </span>
                                       </div>
                                    </div>
                                 </div>
                              </div>
                              <p class="mt-2 text-xs text-blue-600 bg-blue-50/50 p-2.5 rounded-lg border border-blue-100 leading-normal select-none">
                                  💡 <strong>提示：</strong>该配置项为只读模式。如需配置或测试同步规则，请前往 <strong>【用户管理】</strong> 页面进行设置。
                              </p>
                          </div>
                          <div v-else-if="['ragflow_dataset_ids', 'knowledge_ragflow_dataset_ids'].includes(item.key)">
                               <div class="flex space-x-2">
                                   <input type="text" v-model="item.value" :disabled="isConfigItemDisabled(String(category), item)" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 disabled:opacity-70 disabled:cursor-not-allowed" />
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
                               <div v-if="metadataProvider === 'local'" class="text-sm text-gray-500 py-2 bg-gray-50 border border-gray-200 rounded-md px-3 font-medium flex items-center">
                                   <span class="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium bg-blue-100 text-blue-800 mr-2 border border-blue-200 whitespace-nowrap shrink-0">local-redis</span>
                                   使用本地 Redis 向量存储 (HNSW)
                               </div>
                               <div v-else class="flex items-center space-x-2">
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
                          <div v-else-if="item.key === 'embed_api_url'">
                              <div class="flex items-center space-x-2">
                                  <input type="text" v-model="item.value" :disabled="isConfigItemDisabled(String(category), item)" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 disabled:opacity-70 disabled:cursor-not-allowed p-2" />
                                  <button
                                      @click="testGlobalEmbed"
                                      :disabled="globalEmbedTesting"
                                      class="inline-flex items-center px-3 py-2 border border-gray-300 shadow-sm text-sm leading-4 font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary disabled:opacity-50 shrink-0"
                                  >
                                      <PlayIcon v-if="!globalEmbedTesting" class="h-4 w-4 mr-1.5 text-gray-500" />
                                      <span v-else class="animate-spin h-4 w-4 mr-1.5 border-2 border-primary border-t-transparent rounded-full"></span>
                                      测试
                                  </button>
                              </div>
                          </div>
                          <div v-else-if="['ragflow_similarity_threshold', 'ragflow_vector_weight', 'chatbi_sample_similarity_threshold', 'chatbi_sample_vector_similarity_weight', 'knowledge_ragflow_similarity_threshold', 'knowledge_ragflow_vector_weight', 'llm_temperature'].includes(item.key)">
                              <div class="flex items-center space-x-4">
                                  <div class="flex-1">
                                      <input
                                        type="range"
                                        min="0"
                                        max="1"
                                        step="0.05"
                                        :value="Number(item.value)"
                                        :disabled="isConfigItemDisabled(String(category), item)"
                                        @input="(e) => item.value = (e.target as HTMLInputElement).value"
                                        class="w-full h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-primary disabled:opacity-50"
                                      />
                                      <div class="flex justify-between text-xs text-gray-400 mt-1 font-mono">
                                          <span class="flex items-center">
                                            0.0
                                            <span v-if="item.key === 'llm_temperature'" class="text-[10px] text-gray-500 font-sans ml-1 select-none">(更严谨/精准)</span>
                                            <span v-else-if="['ragflow_similarity_threshold', 'chatbi_sample_similarity_threshold', 'knowledge_ragflow_similarity_threshold'].includes(item.key)" class="text-[10px] text-gray-500 font-sans ml-1 select-none">(无门槛)</span>
                                            <span v-else-if="['ragflow_vector_weight', 'chatbi_sample_vector_similarity_weight', 'knowledge_ragflow_vector_weight'].includes(item.key)" class="text-[10px] text-gray-500 font-sans ml-1 select-none">(只看关键词)</span>
                                          </span>
                                          <span>0.5</span>
                                          <span class="flex items-center">
                                            1.0
                                            <span v-if="item.key === 'llm_temperature'" class="text-[10px] text-gray-500 font-sans ml-1 select-none">(更随机/发散)</span>
                                            <span v-else-if="['ragflow_similarity_threshold', 'chatbi_sample_similarity_threshold', 'knowledge_ragflow_similarity_threshold'].includes(item.key)" class="text-[10px] text-gray-500 font-sans ml-1 select-none">(极高门槛)</span>
                                            <span v-else-if="['ragflow_vector_weight', 'chatbi_sample_vector_similarity_weight', 'knowledge_ragflow_vector_weight'].includes(item.key)" class="text-[10px] text-gray-500 font-sans ml-1 select-none">(只看语义)</span>
                                          </span>
                                      </div>
                                  </div>
                                  <div class="w-16">
                                      <input
                                        type="number"
                                        v-model="item.value"
                                        :disabled="isConfigItemDisabled(String(category), item)"
                                        min="0"
                                        max="1"
                                        step="0.05"
                                        class="block w-full sm:text-sm border-gray-300 rounded-md bg-white text-center focus:ring-primary focus:border-primary disabled:opacity-70"
                                      />
                                  </div>
                              </div>
                          </div>
                          <div v-else-if="['embedchat_watermark_enabled', 'yovole_sso_enabled', 'knowledge_base_enabled'].includes(item.key)" class="flex items-center">
                             <button
                               type="button"
                               :disabled="isConfigItemDisabled(String(category), item)"
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
                             <select v-model="item.value" :disabled="isConfigItemDisabled(String(category), item)" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 p-2 disabled:opacity-70 disabled:cursor-not-allowed">
                                <option value="user_time">用户名 + 时间戳</option>
                                <option value="custom">自定义文字</option>
                             </select>
                          </div>
                          <div v-else-if="isLongText(item)">
                             <textarea v-model="item.value" :disabled="isConfigItemDisabled(String(category), item)" rows="10" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md font-mono text-xs bg-gray-100 p-3 disabled:opacity-70 disabled:cursor-not-allowed"></textarea>
                             <p v-if="item.key === 'third_party_user_sync_config'" class="mt-2 text-xs text-blue-600 bg-blue-50/50 p-2.5 rounded-lg border border-blue-100 leading-normal select-none">
                                 💡 <strong>提示：</strong>该配置项为只读模式。如需配置或测试同步规则，请前往 <strong>【用户管理】</strong> 页面进行设置。
                             </p>
                          </div>
                          <div v-else-if="['audit_log_retention_days', 'agent_max_iterations', 'agent_max_context_turns', 'data_api_timeout_seconds', 'schema_api_timeout_seconds', 'ragflow_metadata_top_k', 'knowledge_ragflow_metadata_top_k', 'embed_dimensions', 'chatbi_sample_top_k'].includes(item.key)">
	                             <input type="text" v-model="item.value" @keypress="!/[0-9]/.test(($event as KeyboardEvent).key) && ($event as KeyboardEvent).preventDefault()" @input="item.value = item.value.replace(/\D/g, '')" :disabled="isConfigItemDisabled(String(category), item)" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 disabled:opacity-70 disabled:cursor-not-allowed p-2" />
                          </div>
                          <div v-else>
                             <input type="text" v-model="item.value" :disabled="isConfigItemDisabled(String(category), item)" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 disabled:opacity-70 disabled:cursor-not-allowed" />
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
        :override-url="datasetSelectorUrl"
        :override-key="datasetSelectorKey"
        :include-missing="false"
        @select="handleDatasetSelect"
    />

    <RedisKeyCleanupModal
      :show="showRedisCleanupModal"
      @close="showRedisCleanupModal = false"
      @deleted="handleRedisKeysDeleted"
    />

    <ConfirmModal
      v-if="showRebuildConfirm"
      title="重构本地向量索引与数据？"
      message="此操作将删除本地 Redis 中的元数据和经验案例的向量索引定义并清理其已存向量，随后重新创建索引并触发全量数据的重新向量化后台同步。如果变更了 Embedding 模型或维度，必须执行此操作。确定执行吗？"
      confirm-text="确认重构"
      cancel-text="取消"
      type="danger"
      @confirm="executeRebuildVectors"
      @cancel="showRebuildConfirm = false"
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

    <!-- Metadata Provider Explanation Modal -->
    <div v-if="showMetadataExplanation" class="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm animate-fade-in" @click.self="showMetadataExplanation = false">
      <div class="bg-white rounded-2xl shadow-2xl max-w-xl w-full overflow-hidden scale-100 transition-all duration-200 border border-gray-100 flex flex-col">
        <!-- Header -->
        <div class="px-6 py-4 border-b border-gray-100 flex justify-between items-center bg-gray-50/50">
          <div class="flex items-center space-x-2.5">
            <div class="p-2 bg-indigo-50 rounded-xl text-indigo-600">
              <CircleStackIcon class="w-5 h-5" />
            </div>
            <div>
              <h3 class="text-md font-bold text-gray-900">元数据提供方参数说明</h3>
              <p class="text-xs text-gray-400 mt-0.5">参数名：metadata_provider</p>
            </div>
          </div>
          <button @click="showMetadataExplanation = false" class="text-gray-400 hover:text-gray-600 focus:outline-none transition-colors">
            <svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <!-- Content -->
        <div class="p-6 space-y-4 text-sm text-gray-600 max-h-[500px] overflow-y-auto custom-scrollbar">
          <p class="text-gray-500 leading-relaxed">
            该参数决定了系统在“元数据检索（获取表/字段描述来生成 SQL）”场景下通过何种途径来获取数据：
          </p>
          
          <div class="space-y-3">
            <!-- Mode 1: Local -->
            <div class="flex gap-3 p-3 rounded-xl bg-gray-50 hover:bg-gray-100/60 transition-colors">
              <div class="flex-shrink-0 flex items-center justify-center w-7 h-7 rounded-lg bg-indigo-100 text-indigo-700 font-bold text-xs">local</div>
              <div class="space-y-1">
                <h4 class="font-bold text-gray-900">本地元数据模式 (Local Metadata)</h4>
                <p class="text-xs text-gray-500 leading-relaxed">
                  直接检索系统内手工填写维护的本地元数据字典。在包含检索词的场景下，系统调用本地 Embedding 服务生成向量，并使用<strong>本地 Redis 向量数据库 (HNSW 索引)</strong> 进行高效的相似度检索过滤。
                </p>
              </div>
            </div>

            <!-- Mode 2: RAGFlow -->
            <div class="flex gap-3 p-3 rounded-xl bg-gray-50 hover:bg-gray-100/60 transition-colors">
              <div class="flex-shrink-0 flex items-center justify-center w-7 h-7 rounded-lg bg-emerald-100 text-emerald-700 font-bold text-xs">ragflow</div>
              <div class="space-y-1">
                <h4 class="font-bold text-gray-900">知识库检索模式 (RAGFlow Retrieval)</h4>
                <p class="text-xs text-gray-500 leading-relaxed">
                  系统将元数据字典同步至 RAGFlow，并通过调用 RAGFlow 后端 API，自动在绑定的元数据知识库中进行全文 + 向量的混合语义匹配检索。
                </p>
              </div>
            </div>

            <!-- Shared parameters section -->
            <div class="p-3 rounded-xl bg-amber-50/50 border border-amber-100 space-y-2">
              <h4 class="font-bold text-amber-900 flex items-center text-xs">
                <span class="mr-1">💡</span> 元数据检索参数说明（仅适用于元数据检索场景）
              </h4>
              <p class="text-xs text-amber-800 leading-relaxed">
                以下三个配置参数仅控制了<strong>元数据检索</strong>的召回和过滤评分（无论是<strong>本地元数据检索</strong>还是 <strong>RAGFlow 元数据检索</strong>模式，均会使用这组参数）：
              </p>
              <ul class="text-xs text-amber-900 space-y-1.5 list-disc pl-4">
                <li>
                  <strong class="font-mono">ragflow_metadata_top_k</strong>: 元数据检索时最大召回的候选文档/描述条数上限。值越大召回越丰富，但大模型上下文占用（Token）也会越高。
                </li>
                <li>
                  <strong class="font-mono">ragflow_similarity_threshold</strong>: 元数据相似度匹配过滤阈值（0.0 至 1.0）。低于此设定值的检索结果将被过滤，以防混入不相关的上下文。推荐配置为 <code class="bg-amber-100/80 px-1 py-0.5 rounded font-mono text-amber-900">0.40</code>。
                </li>
                <li>
                  <strong class="font-mono">ragflow_vector_weight</strong>: 元数据混合检索中向量相似度匹配的分数占比（其余比例为全文关键词匹配）。注：此权重目前主要在 RAGFlow 的混合检索中生效，本地 Redis 模式下固定使用纯向量检索。
                </li>
              </ul>
            </div>
          </div>
        </div>
        <!-- Footer -->
        <div class="bg-gray-50 px-6 py-4 flex justify-end border-t border-gray-100">
          <button 
            @click="showMetadataExplanation = false" 
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
      <div class="bg-white rounded-2xl shadow-2xl max-w-md w-full max-h-[85vh] overflow-hidden scale-100 transition-all duration-200 border border-gray-100 flex flex-col">
        <!-- Header -->
        <div class="px-6 py-4 border-b border-gray-100 flex justify-between items-center bg-gray-50/50 shrink-0">
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
        <div class="p-6 space-y-4 text-sm text-gray-600 overflow-y-auto custom-scrollbar flex-1">
          <div class="space-y-2">
            <span class="text-xs font-bold text-gray-400 uppercase tracking-wider font-mono">功能描述</span>
            <p class="text-gray-700 leading-relaxed bg-gray-50 p-4 rounded-xl border border-gray-100 whitespace-pre-wrap">
              {{ activeExplanationItem.description || '暂无描述信息。' }}
            </p>
          </div>
          
          <!-- Category specific tips -->
          <div class="space-y-2" v-if="getCategoryTip(activeExplanationItem.key)">
            <span class="text-xs font-bold text-gray-400 uppercase tracking-wider font-mono">使用建议</span>
            <p class="text-xs text-gray-600 leading-relaxed bg-indigo-50/50 p-4 rounded-xl border border-indigo-100/50 text-indigo-950 whitespace-pre-wrap">
              {{ getCategoryTip(activeExplanationItem.key) }}
            </p>
          </div>
        </div>
        <!-- Footer -->
        <div class="bg-gray-50 px-6 py-4 flex justify-end border-t border-gray-100 shrink-0">
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
    <ConfirmModal
      v-if="showCleanupConfirm"
      title="手动清理历史日志？"
      message="此操作将秒级 DROP 所有满足过期条件的整月日志分区。未分区环境将使用微批量 DELETE 进行平滑删除，本操作不可逆，是否确定清理？"
      confirm-text="确认清理"
      cancel-text="取消"
      type="danger"
      @confirm="triggerCleanup"
      @cancel="showCleanupConfirm = false"
    />
    <ConfirmModal
      v-if="showDeleteKeyConfirm"
      title="确认删除此 Redis Key？"
      :message="`即将物理删除键 「${pendingDeleteKey}」，此操作不可恢复，是否继续？`"
      confirm-text="确认删除"
      cancel-text="取消"
      type="danger"
      @confirm="executeDeleteKey"
      @cancel="showDeleteKeyConfirm = false"
    />

    <!-- Image Cropper Modal -->
    <div v-if="showCropper" class="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm" @click.self="showCropper = false">
      <div class="bg-white rounded-2xl shadow-2xl max-w-md w-full overflow-hidden scale-100 transition-all duration-200 border border-gray-100 flex flex-col">
        <!-- Header -->
        <div class="px-6 py-4 border-b border-gray-100 flex justify-between items-center bg-gray-50/50 shrink-0">
          <div class="flex items-center space-x-2.5">
            <div class="p-2 bg-primary/10 rounded-xl text-primary">
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-5 h-5">
                <path stroke-linecap="round" stroke-linejoin="round" d="M10.5 6h9.75M10.5 6a1.5 1.5 0 1 1-3 0m3 0a1.5 1.5 0 1 0-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 0 1-3 0m3 0a1.5 1.5 0 0 0-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 0 1-3 0m3 0a1.5 1.5 0 0 0-3 0m-9.75 0h9.75" />
              </svg>
            </div>
            <h3 class="text-md font-bold text-gray-900">裁剪个性化图标</h3>
          </div>
          <button @click="showCropper = false" class="text-gray-400 hover:text-gray-600 focus:outline-none transition-colors">
            <svg class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <!-- Content -->
        <div class="p-6 flex flex-col items-center justify-center space-y-6 select-none">
          <!-- Cropping Area container -->
          <div 
            class="relative w-[240px] h-[240px] border border-gray-200 rounded-2xl bg-gray-900 overflow-hidden cursor-move shadow-inner"
            @mousedown="onCropperMouseDown"
            @mousemove="onCropperMouseMove"
            @mouseup="onCropperMouseUp"
            @mouseleave="onCropperMouseUp"
            @touchstart="onCropperTouchStart"
            @touchmove="onCropperTouchMove"
            @touchend="onCropperMouseUp"
          >
            <!-- Image to crop -->
            <img 
              :src="cropperImageSrc" 
              alt="裁剪预览" 
              class="absolute pointer-events-none max-w-none"
              :style="cropperImageStyle"
            />
            <!-- Highlighting central viewport (200x200) with circle border -->
            <div class="absolute inset-0 pointer-events-none flex items-center justify-center">
              <div class="w-[200px] h-[200px] border-2 border-dashed border-primary rounded-xl shadow-[0_0_0_9999px_rgba(0,0,0,0.6)] z-10"></div>
            </div>
          </div>
          <!-- Zoom Slider Control -->
          <div class="w-full flex flex-col space-y-2">
            <div class="flex justify-between items-center px-1">
              <span class="text-xs font-bold text-gray-400">缩放比例</span>
              <span class="text-xs font-mono font-bold text-primary">{{ Math.round(cropperZoom * 100) }}%</span>
            </div>
            <div class="flex items-center space-x-3">
              <span class="text-xs text-gray-400">缩小</span>
              <input 
                v-model.number="cropperZoom" 
                type="range" 
                min="0.5" 
                max="3" 
                step="0.05" 
                class="flex-1 h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-primary focus:outline-none"
              />
              <span class="text-xs text-gray-400">放大</span>
            </div>
          </div>
          <p class="text-xs text-gray-400 text-center leading-relaxed">
            💡 按住鼠标左键并拖拽可移动图片位置，使用下方滑块进行缩放。<br/>
            系统会自动把图片压缩并裁剪到标准清晰尺寸，避免大图上传失败。
          </p>
        </div>
        <!-- Footer -->
        <div class="bg-gray-50 px-6 py-4 flex justify-end space-x-3 border-t border-gray-100 shrink-0">
          <button 
            @click="showCropper = false" 
            type="button" 
            class="px-4 py-2 rounded-xl text-sm font-bold text-gray-600 bg-white border border-gray-200 hover:bg-gray-50 active:scale-95 transition-all duration-200"
          >
            取消
          </button>
          <button 
            @click="handleCropperConfirm" 
            type="button" 
            class="px-5 py-2 rounded-xl text-sm font-bold text-white bg-primary hover:bg-primary-dark transition-all duration-200 active:scale-95 shadow-sm focus:outline-none"
          >
            确认裁剪并上传
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
