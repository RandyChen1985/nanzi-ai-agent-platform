<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, nextTick, watch } from 'vue'
import axios from '@/utils/axios'
import { getTemperatureGuidance, temperatureScaleGuidance } from '../utils/temperatureGuidance'
import { useToast } from '../composables/useToast'
import { useUser } from '../composables/useUser'
import { modelApi, type AIModel } from '../api/model'
import ModelRegistry from '../components/system/ModelRegistry.vue'
import DeploymentChecklist from '../components/system/DeploymentChecklist.vue'
import ToolRegistry from '../components/system/ToolRegistry.vue'
import RagFlowResourceSelector from '../components/RagFlowResourceSelector.vue'
import ConfirmModal from '../components/ConfirmModal.vue'
import RedisKeyCleanupModal from '../components/system/RedisKeyCleanupModal.vue'
import Switch from '../components/Switch.vue'
import { useRouter, useRoute } from 'vue-router'
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
  ComputerDesktopIcon,
  CubeIcon,
  CloudIcon,
  ServerIcon,
  PlayIcon,
  ArrowPathIcon,
  PaintBrushIcon
} from '@heroicons/vue/24/outline'

const router = useRouter()
const route = useRoute()
const { hasPermission, userInfo } = useUser()
const canSave = hasPermission('element:system:config_save')

const activeTab = ref<'diagnostics' | 'configs' | 'models' | 'tools' | 'logs' | 'branding'>('configs')
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

// --- Docker 沙箱预构建 ---
const dockerPrebuilt = ref(false)
const dockerPrebuildChecking = ref(false)
const dockerPrebuilding = ref(false)
const dockerPrebuildReused = ref(false)
const dockerPrebuildTag = ref('')
const dockerPrebuildMessage = ref('')
const dockerPrebuildHelpUrl = ref('https://github.com/RandyChen1985/nanzi-ai-agent-platform/blob/main/FAQ.md')
type DockerPrebuildLogLevel = 'info' | 'error'
type DockerPrebuildLogEntry = { level: DockerPrebuildLogLevel; message: string }
const dockerPrebuildLogs = ref<DockerPrebuildLogEntry[]>([])
const dockerPrebuildStage = ref('idle')
const dockerPrebuildError = ref('')
const dockerPrebuildLogsCopied = ref(false)
const dockerPrebuildElapsedSeconds = ref(0)
const dockerPrebuildLogsExpanded = ref(true)
const dockerPrebuildLogContainer = ref<HTMLElement | null>(null)
const dockerPrebuildShouldAutoScroll = ref(true)
let dockerPrebuildTimer: number | null = null
const sandboxConnectionTesting = ref<'e2b' | 'ssh' | null>(null)

const dockerPrebuildLogsText = computed(() =>
  dockerPrebuildLogs.value.map((entry) => entry.message).join('\n'),
)
const dockerPrebuildStageLabel = computed(() => {
  const labels: Record<string, string> = {
    idle: '等待开始',
    environment: '检查 Docker 环境',
    prepare: '准备构建上下文',
    cache: '检查镜像缓存',
    build: '构建 Docker 镜像',
    finalize: '保存构建状态',
    completed: '构建完成',
  }
  return labels[dockerPrebuildStage.value] || dockerPrebuildStage.value
})
const appendDockerPrebuildLog = (message: string, level: DockerPrebuildLogLevel = 'info') => {
  const normalized = message.trimEnd()
  if (!normalized) return
  dockerPrebuildLogs.value.push({ level, message: normalized })
  void nextTick(() => {
    const container = dockerPrebuildLogContainer.value
    if (dockerPrebuildLogsExpanded.value && dockerPrebuildShouldAutoScroll.value && container) {
      container.scrollTop = container.scrollHeight
    }
  })
}
const scrollDockerPrebuildLogsToBottom = () => {
  void nextTick(() => {
    const container = dockerPrebuildLogContainer.value
    if (container) container.scrollTop = container.scrollHeight
  })
}
const handleDockerPrebuildLogScroll = (event: Event) => {
  const container = event.currentTarget as HTMLElement
  dockerPrebuildShouldAutoScroll.value =
    container.scrollHeight - container.scrollTop - container.clientHeight <= 24
}
const toggleDockerPrebuildLogs = () => {
  dockerPrebuildLogsExpanded.value = !dockerPrebuildLogsExpanded.value
  if (dockerPrebuildLogsExpanded.value) {
    dockerPrebuildShouldAutoScroll.value = true
    scrollDockerPrebuildLogsToBottom()
  }
}
const stopDockerPrebuildTimer = () => {
  if (dockerPrebuildTimer !== null) {
    window.clearInterval(dockerPrebuildTimer)
    dockerPrebuildTimer = null
  }
}
const startDockerPrebuildTimer = () => {
  stopDockerPrebuildTimer()
  const startedAt = Date.now()
  dockerPrebuildElapsedSeconds.value = 0
  dockerPrebuildTimer = window.setInterval(() => {
    dockerPrebuildElapsedSeconds.value = Math.floor((Date.now() - startedAt) / 1000)
  }, 1000)
}
const copyDockerPrebuildLogs = async () => {
  if (!dockerPrebuildLogsText.value) return
  try {
    if (!navigator.clipboard) throw new Error('当前浏览器不支持剪贴板')
    await navigator.clipboard.writeText(dockerPrebuildLogsText.value)
    dockerPrebuildLogsCopied.value = true
    showToast('构建日志已复制', 'success')
    window.setTimeout(() => { dockerPrebuildLogsCopied.value = false }, 1600)
  } catch (error: any) {
    showToast(`复制构建日志失败: ${error.message}`, 'error')
  }
}

const targetSandboxPolicy = () =>
  configGroups.value?.sandbox?.find(x => x.key === 'sandbox_policy')?.value ?? 'local'

/** 平台后端进程运行环境：docker=部署在容器内，host=运行在宿主机（由后端动态探测注入） */
const runtimeEnv = computed(() =>
  configGroups.value?.sandbox?.find(x => x.key === 'sandbox_runtime_env')?.value ?? 'host',
)

/** local 策略实际执行位置的动态描述（随平台部署环境变化） */
const sandboxLocalExecDesc = computed(() =>
  runtimeEnv.value === 'docker'
    ? '平台后端所在 Docker 容器内直接执行'
    : '宿主机扩展进程内直接执行（当前默认）',
)

/** sandbox_policy 短描述：local 部分随运行环境动态渲染 */
const sandboxPolicyShortDesc = computed(() =>
  `安全沙箱执行策略。local 表示${sandboxLocalExecDesc.value}；docker 表示在自动构建的 Docker 容器内执行${runtimeEnv.value === 'docker' ? '（通过宿主机 Docker Socket 隔离）' : ''}；e2b 表示在 E2B 云端沙箱内执行；ssh 表示在 SSH 远程主机上执行。`,
)

/** sandbox_policy 详细说明：local 部分随运行环境动态渲染（用于说明弹窗） */
const sandboxPolicyTip = computed(() => `安全沙箱执行策略：
* local（默认）：Bash / 文件工具在${sandboxLocalExecDesc.value}，性能最好，但代码运行在${runtimeEnv.value === 'docker' ? '平台容器内部' : '宿主机上'}。
* docker：在 Docker 容器内执行${runtimeEnv.value === 'docker' ? '（平台通过挂载的宿主机 Docker Socket 动态创建与管理沙箱容器）' : ''}。首次使用或基础镜像变更时，系统会自动构建并启动容器；每个用户的容器工作区固定挂载到该用户自己的平台工作区目录。
* e2b：在 E2B 云端沙箱内执行。需在下方填写 API Key 或配置 E2B_API_KEY 环境变量。
* ssh：在 SSH 远程主机上执行。平台所在主机通过 ssh 连接下方指定的远程主机，把远程目录作为沙箱工作区；支持密码（依赖 sshpass）与私钥两种认证。
注意：不同策略有各自的配置项，仅在切换到对应策略时生效。`)

/** sandbox_policy 自定义双行下拉选项：名称 + 备注说明 */
const sandboxPolicyOptions = computed(() => [
  {
    value: 'local',
    label: runtimeEnv.value === 'docker' ? 'local（平台后端容器内）' : 'local（宿主机）',
    disabled: false,
    desc: runtimeEnv.value === 'docker'
      ? '在平台后端所在 Docker 容器内直接执行（默认）'
      : '在宿主机扩展进程内直接执行（默认，性能最好）',
  },
  {
    value: 'docker',
    label: 'docker（Docker 容器）',
    disabled: false,
    desc: runtimeEnv.value === 'docker'
      ? '通过宿主机 Docker Socket 动态创建沙箱容器执行，工作区按用户隔离'
      : '在自动构建的 Docker 容器内执行，工作区按用户隔离',
  },
  {
    value: 'e2b',
    label: 'e2b（E2B 云端）',
    disabled: false,
    desc: '在 E2B 云端沙箱内执行，需配置 API Key 或 E2B_API_KEY',
  },
  {
    value: 'ssh',
    label: 'ssh（SSH 远程主机）',
    disabled: false,
    desc: '通过 SSH 连接远程主机执行，支持密码与私钥两种认证',
  },
])
const sandboxPolicyIcons = {
  local: ComputerDesktopIcon,
  docker: CubeIcon,
  e2b: CloudIcon,
  ssh: ServerIcon,
} as const
const getSandboxPolicyIcon = (value: string) =>
  sandboxPolicyIcons[value as keyof typeof sandboxPolicyIcons] ?? ComputerDesktopIcon
const sandboxPolicyOpen = ref(false)
const currentSandboxPolicy = computed(() =>
  sandboxPolicyOptions.value.find(o => o.value === (targetSandboxPolicy() || 'local'))
    ?? sandboxPolicyOptions.value[0]
    ?? { value: 'local', label: 'local（平台本地）', disabled: false, desc: '' },
)
const selectSandboxPolicy = (item: ConfigItem, value: string) => {
  if (isConfigItemDisabled(String('sandbox'), item)) return
  if (item.value === value) {
    sandboxPolicyOpen.value = false
    return
  }
  item.value = value
  sandboxPolicyOpen.value = false
  refreshDockerPrebuildStatus(true)
}

const DEFAULT_DOCKER_BASE_IMAGE = 'python:3.11-slim'

/** docker 基础镜像候选清单：官方标准镜像（作为 Dockerfile FROM 的 python 基座，烤入 agentscope MCP gateway）；
 * 另含「自定义…」手动输入（可填入私有仓库或镜像加速地址）。 */
const dockerBaseImagePresets: { label: string; value: string }[] = [
  { label: 'python:3.11-slim（官方标准，默认）', value: 'python:3.11-slim' },
  { label: 'python:3.11（官方完整版）', value: 'python:3.11' },
]
const dockerBaseImageOpen = ref(false)
const dockerBaseImageShowCustom = ref(false)

const isCustomDockerBaseImage = computed(() => {
  const cur = (configGroups.value?.sandbox?.find(x => x.key === 'sandbox_docker_base_image')?.value ?? '').trim()
  if (dockerBaseImageShowCustom.value) return true
  if (!cur) return false
  return !dockerBaseImagePresets.some(p => p.value === cur)
})

/** 当前值是否命中所选预设，用于展示按钮文案 */
const currentDockerBaseImageLabel = computed(() => {
  const cur = (configGroups.value?.sandbox?.find(x => x.key === 'sandbox_docker_base_image')?.value ?? '').trim()
  if (isCustomDockerBaseImage.value) {
    return cur ? `自定义镜像：${cur}` : '自定义镜像地址…'
  }
  const matched = dockerBaseImagePresets.find(p => p.value === cur)
  return matched ? matched.label : (cur ? `自定义镜像：${cur}` : (dockerBaseImagePresets[0]?.label || 'python:3.11-slim'))
})
/** 当前选中的 docker 基础镜像地址 */
const getTargetDockerBaseImage = () => {
  const cur = configGroups.value?.sandbox?.find(x => x.key === 'sandbox_docker_base_image')?.value
  return (cur || '').trim()
}

/** 点击预设后写入 item.value 并关闭面板，同时实时更新该镜像对应的预构建状态 */
const selectDockerBaseImage = (item: ConfigItem, preset: string) => {
  if (preset === '_custom') {
    dockerBaseImageShowCustom.value = true
    dockerBaseImageOpen.value = false
    return
  }
  item.value = preset
  dockerBaseImageShowCustom.value = false
  dockerBaseImageOpen.value = false
  refreshDockerPrebuildStatus(true, preset)
}
/** 拉起/收起下拉时联动重置 */
const toggleDockerBaseImage = (item: ConfigItem) => {
  if (isConfigItemDisabled(String('sandbox'), item)) return
  dockerBaseImageOpen.value = !dockerBaseImageOpen.value
}

const applyDockerPrebuildStatus = (data: any) => {
  // 状态查询返回 prebuilt，预构建接口返回 reused / built；统一归一为页面状态。
  dockerPrebuilt.value = !!(data?.prebuilt || data?.reused || data?.built)
  dockerPrebuildMessage.value = data?.message || ''
  dockerPrebuildTag.value = data?.tag || data?.required_image_tag || ''
  if (data?.help_url) {
    dockerPrebuildHelpUrl.value = data.help_url
  }
}

const refreshDockerPrebuildStatus = async (silent = false, baseImageOverride?: string) => {
  if (targetSandboxPolicy() !== 'docker') return
  if (!silent) dockerPrebuildChecking.value = true
  try {
    const baseImage = (baseImageOverride ?? getTargetDockerBaseImage()) || DEFAULT_DOCKER_BASE_IMAGE
    const res = await axios.get('/api/v1/admin/sandbox/docker/prebuild-status', {
      params: baseImage ? { base_image: baseImage } : {}
    })
    const data = res.data?.data ?? res.data
    applyDockerPrebuildStatus(data)
    dockerPrebuildChecking.value = false
    if (!silent && data?.docker_available === false) {
      showToast(data?.message || '当前环境不支持自动构建', 'warning')
    }
  } catch (e: any) {
    dockerPrebuilt.value = false
    dockerPrebuildChecking.value = false
    if (!silent) {
      const msg = e.response?.data?.detail || e.message
      showToast(`预构建状态查询失败: ${msg}`, 'error')
    }
  }
}

type DockerPrebuildStreamEvent = {
  event: string
  data: Record<string, any>
}

const parseDockerPrebuildSseBlock = (block: string): DockerPrebuildStreamEvent | null => {
  let event = 'message'
  const dataLines: string[] = []
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
  }
  if (dataLines.length === 0) return null
  const payload = dataLines.join('\n')
  try {
    const data = JSON.parse(payload)
    return {
      event,
      data: data && typeof data === 'object' ? data : { message: String(data) },
    }
  } catch {
    return { event, data: { message: payload } }
  }
}

const handleDockerPrebuildStreamEvent = (streamEvent: DockerPrebuildStreamEvent) => {
  const data = streamEvent.data || {}
  if (streamEvent.event === 'phase') {
    dockerPrebuildStage.value = String(data.stage || dockerPrebuildStage.value)
    if (data.message) appendDockerPrebuildLog(String(data.message))
    return
  }
  if (streamEvent.event === 'log') {
    appendDockerPrebuildLog(String(data.message || ''), data.level === 'error' ? 'error' : 'info')
    return
  }
  if (streamEvent.event === 'result') {
    dockerPrebuildReused.value = !!data.reused
    applyDockerPrebuildStatus(data)
    if (data.docker_available === false) {
      dockerPrebuildError.value = String(data.message || '当前环境不支持自动构建')
      appendDockerPrebuildLog(dockerPrebuildError.value, 'error')
    }
    return
  }
  if (streamEvent.event === 'error') {
    dockerPrebuildError.value = String(data.message || 'Docker 镜像预构建失败')
    appendDockerPrebuildLog(dockerPrebuildError.value, 'error')
  }
}

const executeDockerPrebuild = async () => {
  dockerPrebuilding.value = true
  startDockerPrebuildTimer()
  dockerPrebuildReused.value = false
  dockerPrebuildTag.value = ''
  dockerPrebuildLogs.value = []
  dockerPrebuildLogsExpanded.value = true
  dockerPrebuildShouldAutoScroll.value = true
  dockerPrebuildStage.value = 'environment'
  dockerPrebuildError.value = ''
  dockerPrebuildLogsCopied.value = false
  let receivedDoneEvent = false
  try {
    const baseImage = getTargetDockerBaseImage() || DEFAULT_DOCKER_BASE_IMAGE
    const params = new URLSearchParams()
    if (baseImage) params.set('base_image', baseImage)
    const apiKey = localStorage.getItem('api_key')
    const token = localStorage.getItem('yovole_token') || localStorage.getItem('admin_token')
    const headers: Record<string, string> = { Accept: 'text/event-stream' }
    if (apiKey) headers['X-API-Key'] = apiKey
    if (token) headers.Authorization = `Bearer ${token}`
    const response = await fetch(
      `/api/v1/admin/sandbox/docker/prebuild/stream?${params.toString()}`,
      {
        method: 'POST',
        headers,
        credentials: 'include',
      },
    )
    if (!response.ok) {
      const body = await response.json().catch(() => ({}))
      throw new Error(body.detail || body.message || `请求失败（${response.status}）`)
    }
    const reader = response.body?.getReader()
    if (!reader) throw new Error('浏览器不支持实时构建日志流')

    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
      const blocks = buffer.split(/\r?\n\r?\n/)
      buffer = blocks.pop() || ''
      for (const block of blocks) {
        const streamEvent = parseDockerPrebuildSseBlock(block)
        if (!streamEvent) continue
        handleDockerPrebuildStreamEvent(streamEvent)
        if (streamEvent.event === 'done') receivedDoneEvent = true
      }
      if (receivedDoneEvent || done) break
    }
    if (buffer.trim()) {
      const streamEvent = parseDockerPrebuildSseBlock(buffer)
      if (streamEvent) {
        handleDockerPrebuildStreamEvent(streamEvent)
        if (streamEvent.event === 'done') receivedDoneEvent = true
      }
    }
    if (!receivedDoneEvent) throw new Error('构建日志流意外中断')
    if (dockerPrebuildError.value) {
      showToast(`Docker 镜像预构建失败: ${dockerPrebuildError.value}`, 'error')
      return
    }
    showToast(
      dockerPrebuildReused.value ? '已复用既有镜像缓存，无需重新构建' : 'Docker 沙箱镜像预构建完成',
      'success',
    )
  } catch (e: any) {
    const msg = e.response?.data?.detail || e.message
    dockerPrebuildError.value = msg
    appendDockerPrebuildLog(msg, 'error')
    showToast(`Docker 镜像预构建失败: ${msg}`, 'error')
  } finally {
    stopDockerPrebuildTimer()
    dockerPrebuilding.value = false
  }
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
const collapsedConfigGroups = ref<Set<string>>(new Set())
const sandboxSshAuthType = computed(() => {
  const configured = configGroups.value?.sandbox?.find(item => item.key === 'sandbox_ssh_auth_type')?.value
  return configured === 'key' || configured === 'private_key' ? 'key' : 'password'
})
const orderedCategories = computed(() => {
  if (!configGroups.value) return []
  const order = ['general', 'agent_context', 'agent', 'metadata', 'data_api', 'knowledge', 'sandbox', 'other']
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

const isConfigGroupCollapsed = (category: string) =>
  collapsedConfigGroups.value.has(category)

const toggleConfigGroup = (category: string) => {
  const next = new Set(collapsedConfigGroups.value)
  if (next.has(category)) {
    next.delete(category)
  } else {
    next.add(category)
  }
  collapsedConfigGroups.value = next
}

const expandAllConfigGroups = () => {
  collapsedConfigGroups.value = new Set()
}

const collapseAllConfigGroups = () => {
  collapsedConfigGroups.value = new Set(orderedCategories.value)
}

const metadataProvider = computed(() => {
  if (!configGroups.value) return 'local'
  for (const list of Object.values(configGroups.value)) {
    const item = list.find(x => x.key === 'metadata_provider')
    if (item) return item.value
  }
  return 'local'
})

const sqlExecutionMode = computed(() => {
  for (const list of Object.values(configGroups.value)) {
    const item = list.find(x => x.key === 'sql_execution_mode')
    if (item) return item.value.trim().toLowerCase() === 'local' ? 'local' : 'remote'
  }
  return 'remote'
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
const getAgentToolcallTimeoutValue = (item: ConfigItem) => {
  const value = Number(item.value)
  if (Number.isInteger(value) && value >= 1 && value <= 3600) return value
  return 120
}
const getAgentToolLoopGlobalLimitValue = (item: ConfigItem) => {
  const value = Number(item.value)
  if (Number.isInteger(value) && value >= 1 && value <= 3600) return value
  return 50
}
const adjustAgentToolLoopGlobalLimit = (item: ConfigItem, delta: number) => {
  if (isConfigItemDisabled('agent', item)) return
  const current = getAgentToolLoopGlobalLimitValue(item)
  item.value = String(Math.min(3600, Math.max(1, current + delta)))
}
const handleAgentToolLoopGlobalLimitKeydown = (event: KeyboardEvent) => {
  if (event.ctrlKey || event.metaKey || event.altKey) return
  if (['Backspace', 'Delete', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'Tab', 'Enter', 'Escape'].includes(event.key)) return
  if (!/^\d$/.test(event.key)) event.preventDefault()
}
const handleAgentToolLoopGlobalLimitInput = (item: ConfigItem, event: Event) => {
  if (isConfigItemDisabled('agent', item)) return
  const target = event.target as HTMLInputElement
  const digits = target.value.replace(/\D/g, '')
  item.value = digits
  target.value = digits
}
const normalizeAgentToolLoopGlobalLimitInput = (item: ConfigItem) => {
  const digits = String(item.value ?? '').replace(/\D/g, '')
  const value = Number(digits)
  item.value = Number.isFinite(value) && Number.isInteger(value) && value >= 1
    ? String(Math.min(3600, value))
    : '50'
}
const adjustAgentToolcallTimeout = (item: ConfigItem, delta: number) => {
  if (isConfigItemDisabled('agent', item)) return
  const current = getAgentToolcallTimeoutValue(item)
  item.value = String(Math.min(3600, Math.max(1, current + delta)))
}
const handleAgentToolcallTimeoutKeydown = (event: KeyboardEvent) => {
  if (event.ctrlKey || event.metaKey || event.altKey) return
  if (['Backspace', 'Delete', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'Tab', 'Enter', 'Escape'].includes(event.key)) return
  if (!/^\d$/.test(event.key)) event.preventDefault()
}
const handleAgentToolcallTimeoutInput = (item: ConfigItem, event: Event) => {
  if (isConfigItemDisabled('agent', item)) return
  const target = event.target as HTMLInputElement
  const digits = target.value.replace(/\D/g, '')
  item.value = digits
  target.value = digits
}
const normalizeAgentToolcallTimeoutInput = (item: ConfigItem) => {
  const digits = String(item.value ?? '').replace(/\D/g, '')
  const value = Number(digits)
  item.value = Number.isFinite(value) && Number.isInteger(value) && value >= 1
    ? String(Math.min(3600, value))
    : '120'
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
  product_name: 'NanZi·智能体平台',
  login_subtitle: 'Your Intelligent Agent Platform',
  icon_url: '/favicon.svg',
  hide_login_sso: false,
  hide_version_link: false,
  contact_markdown: '',
  copyright_text: '',
  default_agent_name: 'NanZi · AI',
})
const brandingIconInput = ref<HTMLInputElement | null>(null)

const fetchBrandingConfig = async () => {
  try {
    const res = await axios.get('/api/portal/system/branding')
    const data = res.data || {}
    brandingConfig.value = {
      enabled: !!data.enabled,
      product_name: data.product_name || 'NanZi·智能体平台',
      login_subtitle: data.login_subtitle || 'Your Intelligent Agent Platform',
      icon_url: data.icon_url || '/favicon.svg',
      hide_login_sso: !!data.hide_login_sso,
      hide_version_link: !!data.hide_version_link,
      contact_markdown: data.contact_markdown || '',
      copyright_text: data.copyright_text || '',
      default_agent_name: data.default_agent_name || 'NanZi · AI',
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
            if (item.key === 'agent_max_toolcall_timeout') {
              item.value = String(getAgentToolcallTimeoutValue(item))
            }
            if (item.key === 'agent_tool_loop_global_limit') {
              item.value = String(getAgentToolLoopGlobalLimitValue(item))
            }
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

// 检测未保存的配置修改
const changedConfigsList = computed(() => {
  const list: { key: string; label?: string; oldValue: any; newValue: any }[] = []
  for (const cat in configGroups.value) {
    const items = configGroups.value[cat]
    if (items) {
      items.forEach(item => {
        if (originalConfigs.value && item.value !== originalConfigs.value[item.key]) {
          list.push({
            key: item.key,
            label: item.description || item.key,
            oldValue: originalConfigs.value[item.key],
            newValue: item.value
          })
        }
      })
    }
  }
  return list
})

const hasUnsavedConfigChanges = computed(() => changedConfigsList.value.length > 0)
const unsavedConfigCount = computed(() => changedConfigsList.value.length)

const resetUnsavedConfigs = () => {
  for (const cat in configGroups.value) {
    const items = configGroups.value[cat]
    if (items) {
      items.forEach(item => {
        if (originalConfigs.value && item.key in originalConfigs.value) {
          item.value = originalConfigs.value[item.key] ?? ''
        }
      })
    }
  }
  showToast('已放弃未保存的配置修改', 'info')
}

const handleGlobalKeydown = (e: KeyboardEvent) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
    if (activeTab.value === 'configs' && canSave) {
      e.preventDefault()
      saveConfigs()
    }
  }
}

const toggleSecret = (key: string) => {
  showSecrets.value[key] = !showSecrets.value[key]
}

const getCategoryLabel = (cat: string) => {
  const map: Record<string, string> = {
    'agent_context': '上下文管理 (Context Management)',
    'data_api': '智能报表 (ChatBI)',
    'metadata': '元数据与 RAG 设置 (Metadata & RAG)',
    'knowledge': '知识库设置 (Knowledge Base)',
    'agent': '智能体设置 (AI Agent)',
    'general': '常规设置 (General Settings)',
    'sandbox': '安全沙箱 (Sandbox)',
    'other': '其他参数 (Other Parameters)'
  }
  return map[cat] || cat.toUpperCase()
}

const getCategoryIcon = (cat: string) => {
  const map: Record<string, any> = {
    'data_api': CircleStackIcon,
    'agent_context': ArrowPathIcon,
    'agent': CpuChipIcon,
    'metadata': SparklesIcon,
    'knowledge': ServerStackIcon,
    'sandbox': CommandLineIcon,
    'general': AdjustmentsHorizontalIcon
  }
  return map[cat] || AdjustmentsHorizontalIcon
}

const isLongText = (item: ConfigItem) => {
  if (item.key === 'sandbox_docker_base_image') return false
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
    'multimodal_model_name': '会话当前模型不支持识图时，用该默认多模态模型解析本轮图片为文字，再交给原模型继续回答。留空则直接提示用户当前模型不支持图片理解。',
    'agent_max_iterations': 'ReAct 智能体单次对话的最大思考与工具调用轮数限制。建议设定在 10-20 之间，过小可能导致任务未完成便终止，过大可能因死循环消耗过多 Token。',
    'agent_max_toolcall_timeout': '单次 Agent 工具调用的全局超时时间（秒），默认 180 秒，范围 1-3600；版本级配置优先于全局配置。',
    'agent_tool_loop_global_limit': '单次对话所有工具调用的总次数上限，默认 50 次，范围 1-3600，用于防止工具循环空转。',
    'agent_max_context_turns': '智能体能够保留的最大历史上下文轮数。设置合理的值能防止发送给大模型的消息体过长，从而节约 Token 并加速模型响应。',
    'external_sql_api_url': '用于远程安全沙箱中执行生成 SQL 查询的 API 服务网关地址。直连物理执行模式（local）下此配置项将被忽略。',
    'external_sql_api_key': '用于调用远程安全 SQL 执行服务的身份验证 Token，请确保保密。',
    'external_sql_data_source': `远程 SQL 服务使用的默认数据源 ID。

如果 AI 调用工具时传入了实际的 data_source，会优先使用 AI 传入的 data_source；只有工具没有传入数据源时，才使用这里配置的默认值。请填写远程 NanZi 数据服务平台中已配置的数据源标识，例如 default_clickhouse、mysql_oa。`,
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
    'sql_execution_mode': `当前支持两种执行模式：

remote（推荐用于独立部署）：平台不直接连接业务数据库，而是把 SQL、数据源 ID 和 API Key 发送给远程 NanZi 数据服务平台执行。请先部署开源项目并配置数据源，再填写下方远程服务地址、API Key 和数据源 ID，最后点击“测试连接”。

local（适用于同一平台可直连数据库）：平台使用本地已配置的数据源连接池直接执行 SQL，不调用远程 SQL 服务。请确保数据库连接已在本平台的数据源管理中配置完成；remote 专用的 URL、API Key 和数据源 ID 在此模式下不会生效。

切换模式并保存后，新的 SQL 查询才会按对应路径执行。`,
    'platform_timezone': '平台业务时区（IANA）。用于定时任务、当前时间锚点与前端时间展示。默认 Asia/Shanghai。修改后会刷新缓存并尝试重载调度器。外部数据库服务器时区不受此项控制。',
    'agentscope_inject_runtime_state': '向 Agent 上下文注入运行时状态（当前时间、任务态、上下文占用等）。时区跟随「平台业务时区」。关闭后不注入 hint，不影响工具权限与 HITL。',
    'agentscope_inject_time_interval_hours': '时间字段重复注入的最小间隔（小时）。仅在开启运行时状态注入时生效。默认 0.5（约 30 分钟）。',
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
    'embed_api_url': '全局 Embedding 服务的 API 接口网关地址。可从上方「从模型管理加载」快捷填入；在本地模式（metadata_provider = local）下用于本地元数据与经验案例向量计算，记忆摘要向量也使用此配置。',
    'embed_api_key': '用于调用全局 Embedding 服务的身份验证 Key。从模型管理加载时无法自动填入（脱敏）；若所选模型已在模型管理配置密钥，此处可留空由运行时解析。',
    'embed_model_name': '全局 Embedding 服务的模型名称。可从「从模型管理加载」填入 model_id（例如 bge-m3）。',
    'embed_dimensions': '全局 Embedding 模型输出的特征向量维度（例如 1024 或 1536）。需与 Redis HNSW 索引维度一致；变更后请重建本地向量与记忆索引。',
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
    'third_party_user_sync_config': '配置从外部数据源定时同步用户信息到本平台的参数。包含启用状态、连接源、表名、字段对应关系和同步周期。此配置已在【用户管理】页面统一维护，在此处仅提供只读展示。',
    'agent_context_max_tokens': '发送给 LLM 的上下文 Token 预算上限（默认 65536 即 64k）。当历史会话上下文超过此预算时，系统优先触发早期对话压缩摘录或截断，避免超出大模型的上下文窗口限制。',
    'agent_max_context_messages': '发送给 LLM 的最大历史消息条目数（Token 预算优先，此处作为绝对兜底上限，默认 60 条）。',
    'agent_context_compaction_enabled': '上下文超预算时，是否把早期历史对话压缩为摘录注入上下文，保留关键信息而非直接丢弃。',
    'agent_context_compaction_max_chars': '溢出压缩摘录中正文部分的最大字符数（默认 1200），用于控制历史摘录的体积，防止摘录过大挤占新对话空间。',
    'agent_context_llm_summary_enabled': '是否用当前会话模型对超长历史做语义摘要（LLM 智能摘要）；若模型摘要失败或超时，系统将自动降级为确定性首末尾摘录，保证对话稳定性。',
    'sandbox_policy': `安全沙箱执行策略：
* local（默认）：Bash / 文件工具在宿主机扩展进程内直接执行，性能最好，但代码运行在宿主机上。
* docker：在 Docker 容器内执行。首次使用或基础镜像变更时，系统会自动构建并启动容器；每个用户的容器工作区固定挂载到该用户自己的平台工作区目录。
* e2b：在 E2B 云端沙箱内执行。需在下方填写 API Key 或配置 E2B_API_KEY 环境变量。
* ssh：在 SSH 远程主机上执行。平台所在主机通过 ssh 连接下方指定的远程主机，把远程目录作为沙箱工作区；支持密码（依赖 sshpass）与私钥两种认证。
注意：不同策略有各自的配置项，仅在切换到对应策略时生效。`
  }
  if (key === 'sandbox_policy') return sandboxPolicyTip.value
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
const selectedEmbedModelId = ref('')
const embeddingModelsForConfig = computed(() =>
  models.value.filter((m) => m.type === 'embedding' && m.is_active)
)
const multimodalModelsForConfig = computed(() =>
  models.value.filter(
    (m) => ['multimodal', 'vision', 'image2text'].includes(String(m.type || '').toLowerCase()) && m.is_active
  )
)

const findConfigItemByKey = (key: string): ConfigItem | null => {
  for (const list of Object.values(configGroups.value)) {
    const item = list.find((x) => x.key === key)
    if (item) return item
  }
  return null
}

type RagflowConnectionResult = {
  status: 'success' | 'error'
  message: string
  datasetCount?: number
}

const ragflowConnectionTesting = ref(false)
const ragflowConnectionResult = ref<RagflowConnectionResult | null>(null)
const ragflowApiUrl = computed(() => findConfigItemByKey('ragflow_api_url')?.value ?? '')
const ragflowApiKey = computed(() => findConfigItemByKey('ragflow_api_key')?.value ?? '')
const ragflowTestDisabled = computed(() => {
  const hasApiKey = ragflowApiKey.value.trim().length > 0
  const hasSavedApiKey = ragflowApiKey.value.includes('****')
  return !canSave ||
    metadataProvider.value !== 'ragflow' ||
    !ragflowApiUrl.value.trim() ||
    (!hasApiKey && !hasSavedApiKey)
})

const clearRagflowConnectionResult = () => {
  ragflowConnectionResult.value = null
}

watch(
  [metadataProvider, ragflowApiUrl, ragflowApiKey],
  clearRagflowConnectionResult
)

const testRagflowMetadataConnection = async () => {
  if (ragflowConnectionTesting.value || ragflowTestDisabled.value) return

  const apiUrl = ragflowApiUrl.value.trim()
  const apiKey = ragflowApiKey.value.trim()
  const useSavedApiKey = apiKey.includes('****')

  ragflowConnectionTesting.value = true
  ragflowConnectionResult.value = null
  try {
    const response = await axios.post('/api/portal/system/test-connection/ragflow_metadata', {
      ragflow_api_url: apiUrl,
      ragflow_api_key: useSavedApiKey ? '' : apiKey,
      use_saved_api_key: useSavedApiKey,
    })
    const data = response.data || {}
    const success = data.status === 'success'
    ragflowConnectionResult.value = {
      status: success ? 'success' : 'error',
      message: data.message || (success ? 'RAGFlow 连接成功' : '连接失败'),
      datasetCount: data.dataset_count,
    }
    showToast(
      success ? 'RAGFlow 连接测试成功' : `RAGFlow 连接测试失败：${data.message || '未知错误'}`,
      success ? 'success' : 'error'
    )
  } catch (error: any) {
    const message = error.response?.data?.detail || error.message || '未知错误'
    ragflowConnectionResult.value = {
      status: 'error',
      message: `连接失败：${message}`,
    }
    showToast(`RAGFlow 连接测试失败：${message}`, 'error')
  } finally {
    ragflowConnectionTesting.value = false
  }
}

type RemoteSqlConnectionResult = {
  status: 'success' | 'error'
  message: string
}

const remoteSqlConnectionTesting = ref(false)
const remoteSqlConnectionResult = ref<RemoteSqlConnectionResult | null>(null)
const externalSqlApiUrl = computed(() => findConfigItemByKey('external_sql_api_url')?.value ?? '')
const externalSqlApiKey = computed(() => findConfigItemByKey('external_sql_api_key')?.value ?? '')
const externalSqlDataSource = computed(() => findConfigItemByKey('external_sql_data_source')?.value ?? '')
const remoteSqlTestDisabled = computed(() => {
  const apiKey = externalSqlApiKey.value.trim()
  return !canSave ||
    sqlExecutionMode.value !== 'remote' ||
    !externalSqlApiUrl.value.trim() ||
    (!apiKey && !apiKey.includes('****')) ||
    !externalSqlDataSource.value.trim()
})

watch(
  [sqlExecutionMode, externalSqlApiUrl, externalSqlApiKey, externalSqlDataSource],
  () => { remoteSqlConnectionResult.value = null }
)

const testRemoteSqlConnection = async () => {
  if (remoteSqlConnectionTesting.value || remoteSqlTestDisabled.value) return

  const apiUrl = externalSqlApiUrl.value.trim()
  const apiKey = externalSqlApiKey.value.trim()
  const useSavedApiKey = apiKey.includes('****')

  remoteSqlConnectionTesting.value = true
  remoteSqlConnectionResult.value = null
  try {
    const response = await axios.post('/api/portal/system/test-connection/remote_sql', {
      external_sql_api_url: apiUrl,
      external_sql_api_key: useSavedApiKey ? '' : apiKey,
      external_sql_data_source: externalSqlDataSource.value.trim(),
      use_saved_external_sql_api_key: useSavedApiKey,
    })
    const data = response.data || {}
    const success = data.status === 'success'
    remoteSqlConnectionResult.value = {
      status: success ? 'success' : 'error',
      message: data.message || (success ? '远程 SQL 连接成功' : '连接失败'),
    }
    showToast(
      success ? '远程 SQL 连接测试成功' : `远程 SQL 连接测试失败：${data.message || '未知错误'}`,
      success ? 'success' : 'error'
    )
  } catch (error: any) {
    const message = error.response?.data?.detail || error.message || '未知错误'
    remoteSqlConnectionResult.value = { status: 'error', message: `连接失败：${message}` }
    showToast(`远程 SQL 连接测试失败：${message}`, 'error')
  } finally {
    remoteSqlConnectionTesting.value = false
  }
}

const sandboxConnectionConfigKeys: Record<'e2b' | 'ssh', string[]> = {
  e2b: [
    'sandbox_e2b_api_key',
    'sandbox_e2b_template',
    'sandbox_e2b_timeout_seconds',
  ],
  ssh: [
    'sandbox_ssh_host',
    'sandbox_ssh_port',
    'sandbox_ssh_user',
    'sandbox_ssh_auth_type',
    'sandbox_ssh_password',
    'sandbox_ssh_private_key',
    'sandbox_ssh_remote_workdir',
  ],
}

const testSandboxConnection = async (policy: 'e2b' | 'ssh') => {
  if (sandboxConnectionTesting.value) return

  const values = Object.fromEntries(
    sandboxConnectionConfigKeys[policy].map((key) => [
      key,
      findConfigItemByKey(key)?.value ?? '',
    ])
  )
  sandboxConnectionTesting.value = policy
  try {
    await axios.post(`/api/v1/admin/sandbox/${policy}/test-connection`, values)
    showToast(
      policy === 'e2b' ? 'E2B 沙箱连接测试成功' : 'SSH 沙箱连接测试成功',
      'success'
    )
  } catch (e: any) {
    const detail = e.response?.data?.detail || e.message || '未知错误'
    showToast(`连接测试失败：${detail}`, 'error')
  } finally {
    sandboxConnectionTesting.value = null
  }
}

const loadEmbedConfigFromModel = () => {
  if (!canSave) return
  const model = embeddingModelsForConfig.value.find((m) => m.id === selectedEmbedModelId.value)
  if (!model) {
    showToast('请先选择模型管理中的 Embedding 模型', 'info')
    return
  }
  const urlItem = findConfigItemByKey('embed_api_url')
  const nameItem = findConfigItemByKey('embed_model_name')
  if (urlItem) {
    urlItem.value = (model.api_base_url || '').trim()
  }
  if (nameItem) {
    nameItem.value = model.model_id
  }
  if (model.has_api_key) {
    showToast(
      '已填入 API 地址与模型名。Key 因脱敏无法自动填入；模型管理已有 Key 时可留空，运行时会自动解析。请核对向量维度后保存。',
      'success'
    )
  } else {
    showToast('已填入 API 地址与模型名。请手动填写 API Key 与向量维度后保存。', 'success')
  }
}

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

/** 左侧简短说明；右侧控件下方仍用 item.description 展示详细备注 */
const configShortDescriptions: Record<string, string> = {
  agentscope_inject_runtime_state: '是否向 Agent 上下文注入运行时状态（当前时间、任务态、上下文占用）。',
  agentscope_inject_time_interval_hours: '运行时时间字段重复注入的最小间隔（小时）。',
  download_url_prefix: '生成文件下载链接时使用的公网地址前缀。',
  multimodal_model_name: '当前对话模型不支持识图时，用此模型解析图片为文字。',
  agent_max_toolcall_timeout: '单次 Agent 工具调用的全局超时时间（秒），默认 180 秒，范围 1-3600；版本级配置优先于全局配置。',
  sql_execution_mode: 'remote 调用独立数据服务，local 由平台直连数据源。',
  external_sql_data_source: '远程 SQL 的默认数据源 ID；AI 调用工具时传入的实际 data_source 优先于此配置。',
  agent_context_max_tokens: '上下文 Token 预算上限 (默认 64k，超过则从最早历史开始截断)。',
  agent_max_context_messages: '发送给 LLM 的最大历史消息条目数 (Token 预算优先，此处作为绝对兜底上限，默认 60)。',
  agent_context_compaction_enabled: '上下文超预算时，是否把早期对话压缩为摘录注入，避免丢失关键信息。',
  agent_context_compaction_max_chars: '溢出压缩摘录中正文部分的最大字符数，过大会挤占新对话空间。',
  agent_context_llm_summary_enabled: '是否用当前会话模型对历史做语义摘要，失败或超时会自动降级为确定性摘录。',
  sandbox_policy: '安全沙箱执行策略。local 表示在宿主机扩展进程内直接执行（当前默认）；docker 表示在自动构建的 Docker 容器内执行；e2b 表示在 E2B 云端沙箱内执行；ssh 表示在 SSH 远程主机上执行。',
  sandbox_docker_base_image: 'docker 策略使用的容器基础镜像（留空默认使用官方标准镜像 python:3.11-slim）。',
  sandbox_e2b_api_key: 'e2b 策略使用的 E2B API Key，留空则读取 E2B_API_KEY 环境变量。',
  sandbox_e2b_template: 'e2b 策略使用的沙箱模板名，留空使用默认模板 base。',
  sandbox_e2b_timeout_seconds: 'e2b 策略沙箱超时时间（秒），默认 300。',
  sandbox_ssh_host: 'ssh 策略连接的远程主机地址（IP 或域名），必填。',
  sandbox_ssh_port: 'ssh 策略连接的远程主机端口，默认 22。',
  sandbox_ssh_user: 'ssh 策略登录的远程用户名，可留空（SSH 默认使用当前用户）。',
  sandbox_ssh_auth_type: 'ssh 策略认证方式：password 表示密码认证（依赖 sshpass），key 表示私钥认证。',
  sandbox_ssh_password: 'ssh 策略密码认证的登录密码（敏感信息），仅在认证方式为 password 时使用。',
  sandbox_ssh_private_key: 'ssh 策略私钥认证的私钥内容（敏感信息），仅在认证方式为 key 时使用。',
  sandbox_ssh_remote_workdir: 'ssh 策略远程沙箱的工作目录（默认 /workspace），由平台自动创建 data/skills/sessions 等子目录。',
}

const getVisibleItems = (items: ConfigItem[] | undefined, category: string) => {
  if (!items) return []
  let list = [...items]
  if (category === 'agent') {
    const order = [
      'agent_max_iterations',
      'agent_tool_loop_global_limit',
      'agent_max_toolcall_timeout',
      'llm_model_name',
      'multimodal_model_name',
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
  if (category === 'agent_context') {
    const order = [
      'agent_context_max_tokens',
      'agent_max_context_messages',
      'agent_context_compaction_enabled',
      'agent_context_compaction_max_chars',
      'agent_context_llm_summary_enabled'
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
  if (category === 'general') {
    const order = [
      'platform_timezone',
      'agentscope_inject_runtime_state',
      'agentscope_inject_time_interval_hours',
      'download_url_prefix',
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
  if (category === 'sandbox') {
    // 内部键不对用户展示：预构建标记 + 平台运行环境（后者仅用于 local 文案动态显示）
    list = list.filter(x => x.key !== 'sandbox_docker_prebuild_done' && x.key !== 'sandbox_runtime_env')
    // 按当前 sandbox 策略动态过滤：仅展示与该策略相关的配置项
    const policy = targetSandboxPolicy()
    const policyKeySets: Record<string, string[]> = {
      docker: ['sandbox_docker_base_image'],
      e2b: ['sandbox_e2b_api_key', 'sandbox_e2b_template', 'sandbox_e2b_timeout_seconds'],
      ssh: [
        'sandbox_ssh_host', 'sandbox_ssh_port', 'sandbox_ssh_user', 'sandbox_ssh_auth_type',
        'sandbox_ssh_password', 'sandbox_ssh_private_key', 'sandbox_ssh_remote_workdir'
      ],
      local: []
    }
    const visibleForPolicy = policyKeySets[policy] || []
    const order = ['sandbox_policy', ...visibleForPolicy]
    list = list.filter(x => x.key === 'sandbox_policy' || visibleForPolicy.includes(x.key))
    if (policy === 'ssh') {
      list = list.filter(item => {
        if (item.key === 'sandbox_ssh_password') return sandboxSshAuthType.value !== 'key'
        if (item.key === 'sandbox_ssh_private_key') return sandboxSshAuthType.value === 'key'
        return true
      })
    }
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
        const commonDataApiKeys = ['data_api_timeout_seconds', 'schema_api_timeout_seconds']
        const remoteDataApiOrder = [
          'sql_execution_mode',
          'external_sql_api_url',
          'external_sql_api_key',
          'external_sql_data_source',
          ...commonDataApiKeys,
        ]
        let visibleRest = modeItem.value === 'local'
          ? [modeItem, ...restItems.filter(item => commonDataApiKeys.includes(item.key))]
          : [...restItems]
        visibleRest.sort((a, b) => {
          const idxA = remoteDataApiOrder.indexOf(a.key)
          const idxB = remoteDataApiOrder.indexOf(b.key)
          return (idxA === -1 ? remoteDataApiOrder.length : idxA) - (idxB === -1 ? remoteDataApiOrder.length : idxB)
        })
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

onMounted(async () => {
  window.addEventListener('keydown', handleGlobalKeydown)
  if (route.query.tab === 'mcp') {
    router.replace('/dashboard/mcp')
    return
  }
  const requestedTab = route.query.tab
  if (requestedTab === 'models' || requestedTab === 'tools' || requestedTab === 'configs' || requestedTab === 'branding' || requestedTab === 'diagnostics' || requestedTab === 'logs') {
    activeTab.value = requestedTab
  }
  await fetchConfigs()
  fetchBrandingConfig()
  fetchModelsForConfigs()
  if (userInfo.value?.role === 'admin') {
    fetchLogConfig()
    fetchPartitions()
    refreshDockerPrebuildStatus(true)
  }
})

onUnmounted(() => {
  window.removeEventListener('keydown', handleGlobalKeydown)
  stopDockerPrebuildTimer()
})
</script>

<template>
  <div class="flex h-full min-h-0 flex-col gap-4 sm:gap-6">
    <div class="flex flex-shrink-0 flex-col gap-3">
      <h1 class="text-xl font-semibold text-gray-900 sm:text-2xl">系统配置与诊断</h1>
      <DeploymentChecklist v-if="userInfo?.role === 'admin'" compact />
      <!-- Tabs：窄屏横向滚动，避免文字被挤成竖排 -->
      <div
        class="-mx-1 overflow-x-auto px-1"
        style="-webkit-overflow-scrolling: touch;"
      >
        <div class="inline-flex min-w-full gap-1 rounded-lg bg-gray-100 p-1 sm:min-w-0">
          <button
            type="button"
            @click="activeTab = 'models'"
            class="inline-flex shrink-0 items-center whitespace-nowrap rounded-md px-3 py-2 text-xs font-medium transition-all duration-200 sm:px-4 sm:text-sm"
            :class="activeTab === 'models' ? 'bg-white text-primary shadow' : 'text-gray-500 hover:text-gray-700'"
          >
            <SparklesIcon class="mr-1.5 h-4 w-4 shrink-0" />
            模型管理
          </button>
          <button
            type="button"
            @click="activeTab = 'tools'"
            class="inline-flex shrink-0 items-center whitespace-nowrap rounded-md px-3 py-2 text-xs font-medium transition-all duration-200 sm:px-4 sm:text-sm"
            :class="activeTab === 'tools' ? 'bg-white text-primary shadow' : 'text-gray-500 hover:text-gray-700'"
          >
            <WrenchScrewdriverIcon class="mr-1.5 h-4 w-4 shrink-0" />
            工具管理
          </button>
          <button
            type="button"
            @click="activeTab = 'configs'"
            class="inline-flex shrink-0 items-center whitespace-nowrap rounded-md px-3 py-2 text-xs font-medium transition-all duration-200 sm:px-4 sm:text-sm"
            :class="activeTab === 'configs' ? 'bg-white text-primary shadow' : 'text-gray-500 hover:text-gray-700'"
          >
            <Cog6ToothIcon class="mr-1.5 h-4 w-4 shrink-0" />
            参数配置
          </button>
          <button
            type="button"
            @click="activeTab = 'branding'"
            class="inline-flex shrink-0 items-center whitespace-nowrap rounded-md px-3 py-2 text-xs font-medium transition-all duration-200 sm:px-4 sm:text-sm"
            :class="activeTab === 'branding' ? 'bg-white text-primary shadow' : 'text-gray-500 hover:text-gray-700'"
          >
            <PaintBrushIcon class="mr-1.5 h-4 w-4 shrink-0" />
            品牌个性化
          </button>
          <button
            type="button"
            @click="activeTab = 'diagnostics'"
            class="inline-flex shrink-0 items-center whitespace-nowrap rounded-md px-3 py-2 text-xs font-medium transition-all duration-200 sm:px-4 sm:text-sm"
            :class="activeTab === 'diagnostics' ? 'bg-white text-primary shadow' : 'text-gray-500 hover:text-gray-700'"
          >
            <CpuChipIcon class="mr-1.5 h-4 w-4 shrink-0" />
            系统诊断
          </button>
          <button
            v-if="userInfo?.role === 'admin'"
            type="button"
            @click="activeTab = 'logs'"
            class="inline-flex shrink-0 items-center whitespace-nowrap rounded-md px-3 py-2 text-xs font-medium transition-all duration-200 sm:px-4 sm:text-sm"
            :class="activeTab === 'logs' ? 'bg-white text-primary shadow' : 'text-gray-500 hover:text-gray-700'"
          >
            <CircleStackIcon class="mr-1.5 h-4 w-4 shrink-0" />
            日志管理
          </button>
        </div>
      </div>
    </div>

    <!-- Content Area -->
    <div class="min-h-0 flex-1 overflow-hidden">

      <div v-if="activeTab === 'models'" class="h-full min-h-0">
          <ModelRegistry />
      </div>

      <div v-else-if="activeTab === 'tools'" class="h-full min-h-0">
          <ToolRegistry />
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
                placeholder="NanZi·智能体平台"
              />
              <p class="text-xs text-gray-400 mt-1">影响浏览器标题、左侧菜单栏名称、登录页</p>
            </div>

            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">登录页副标题</label>
              <input
                v-model="brandingConfig.login_subtitle"
                type="text"
                class="block w-full rounded-lg border-gray-300 shadow-sm focus:border-primary focus:ring-primary text-sm"
                placeholder="Your Intelligent Agent Platform"
              />
            </div>

            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">默认智能助手名称</label>
              <input
                v-model="brandingConfig.default_agent_name"
                type="text"
                class="block w-full rounded-lg border-gray-300 shadow-sm focus:border-primary focus:ring-primary text-sm"
                placeholder="NanZi · AI"
              />
              <p class="text-xs text-gray-400 mt-1">影响未开启品牌个性化时或未指定时的智能助手默认名称（例如：Nexus AI）</p>
            </div>

            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1">Logo / Favicon</label>
              <div class="flex items-center gap-4">
                <img
                  :src="brandingConfig.icon_url || '/favicon.svg'"
                  alt="Logo 预览"
                  class="w-12 h-12 rounded-lg border border-gray-200 object-cover bg-white"
                />
                <div class="flex-1 space-y-2">
                  <input
                    v-model="brandingConfig.icon_url"
                    type="text"
                    class="block w-full rounded-lg border-gray-300 shadow-sm focus:border-primary focus:ring-primary text-sm font-mono"
                    placeholder="/favicon.svg 或 /branding/icon.png"
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
             <div v-if="orderedCategories.length" class="flex flex-wrap items-center justify-between gap-3 -mb-4">
               <!-- 左侧：未保存状态指示 -->
               <div class="flex items-center gap-2">
                 <div
                   v-if="hasUnsavedConfigChanges"
                   class="inline-flex items-center gap-1.5 rounded-full bg-amber-50 border border-amber-200 px-2.5 py-1 text-xs font-semibold text-amber-700 shadow-2xs animate-pulse"
                 >
                   <span class="h-2 w-2 rounded-full bg-amber-500"></span>
                   <span>已修改 {{ unsavedConfigCount }} 项参数（未保存）</span>
                 </div>
                 <div v-else class="text-xs text-gray-400">
                   参数修改后请及时保存生效
                 </div>
               </div>

               <!-- 右侧：展开/折叠与顶部常驻操作 -->
               <div class="flex items-center gap-2">
                 <button
                   type="button"
                   @click="expandAllConfigGroups"
                   class="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-600 shadow-sm transition-colors hover:border-primary/30 hover:bg-primary/5 hover:text-primary cursor-pointer"
                 >
                   <span aria-hidden="true">▾</span>
                   全部展开
                 </button>
                 <button
                   type="button"
                   @click="collapseAllConfigGroups"
                   class="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-600 shadow-sm transition-colors hover:border-primary/30 hover:bg-primary/5 hover:text-primary cursor-pointer"
                 >
                   <span aria-hidden="true">▸</span>
                   全部折叠
                 </button>

                 <template v-if="canSave">
                   <button
                     v-if="hasUnsavedConfigChanges"
                     type="button"
                     @click="resetUnsavedConfigs"
                     class="inline-flex items-center rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-600 shadow-sm hover:bg-gray-50 transition-colors cursor-pointer"
                     title="放弃未保存修改并恢复原值"
                   >
                     重置
                   </button>
                   <button
                     type="button"
                     :disabled="saving"
                     @click="saveConfigs"
                     class="inline-flex items-center gap-1.5 rounded-md px-3.5 py-1.5 text-xs font-bold shadow-sm transition-all cursor-pointer"
                     :class="hasUnsavedConfigChanges
                       ? 'bg-primary text-white hover:bg-primary/90 shadow-primary/20 ring-2 ring-primary/20'
                       : 'bg-gray-100 text-gray-500 hover:bg-gray-200 hover:text-gray-800'"
                     title="保存所有已修改的参数 (快捷键: ⌘S 或 Ctrl+S)"
                   >
                     <svg v-if="saving" class="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                       <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                       <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                     </svg>
                     <span>{{ saving ? '保存中...' : '保存变更 (⌘S)' }}</span>
                   </button>
                 </template>
               </div>
             </div>
             <div v-for="category in orderedCategories" :key="category" class="bg-white shadow rounded-lg">
                <button
                  type="button"
                  class="w-full bg-gray-50 px-6 py-3 border-b border-gray-200 rounded-t-lg flex items-center text-left transition-colors hover:bg-gray-100"
                  :aria-expanded="!isConfigGroupCollapsed(String(category))"
                  :aria-controls="`config-group-${category}`"
                  @click="toggleConfigGroup(String(category))"
                >
                   <div class="p-1.5 bg-white rounded-md shadow-sm border border-gray-100 mr-3">
                       <component :is="getCategoryIcon(String(category))" class="h-5 w-5 text-primary" />
                   </div>
                   <h3 class="text-md font-medium text-gray-800">{{ getCategoryLabel(String(category)) }}</h3>
                   <span class="ml-auto flex items-center gap-1.5 text-xs font-medium text-gray-500">
                     {{ isConfigGroupCollapsed(String(category)) ? '展开' : '收起' }}
                     <span aria-hidden="true" class="text-base leading-none">
                       {{ isConfigGroupCollapsed(String(category)) ? '▸' : '▾' }}
                     </span>
                   </span>
                </button>
                 <div
                   v-if="!isConfigGroupCollapsed(String(category))"
                   :id="`config-group-${category}`"
                   class="p-6 space-y-5"
                 >
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
                    <div v-for="item in getVisibleItems(configGroups[category], String(category))" :key="item.key" class="grid grid-cols-1 md:grid-cols-3 gap-4" :class="[
                      item.key === 'embed_api_url' ? 'embed-config-group rounded-t-xl border-x border-t border-indigo-100/70 bg-indigo-50/30 px-4 pt-4 pb-3 -mx-4' : '',
                      ['embed_api_key', 'embed_model_name'].includes(item.key) ? 'embed-config-group border-x border-indigo-100/70 bg-indigo-50/30 px-4 py-3 -mx-4 !mt-0' : '',
                      ['embed_dimensions'].includes(item.key) ? 'embed-config-group rounded-b-xl border-x border-b border-indigo-100/70 bg-indigo-50/30 px-4 pt-3 pb-4 -mx-4 !mt-0' : '',
                      item.key === 'ragflow_api_url' ? 'ragflow-config-group rounded-t-xl border-x border-t border-sky-200/80 bg-sky-50/70 px-4 pt-4 -mx-4' : '',
                      item.key === 'ragflow_api_key' ? 'ragflow-config-group rounded-b-xl border-x border-b border-sky-200/80 bg-sky-50/70 px-4 pb-4 -mx-4 !mt-0' : '',
                      item.key === 'external_sql_api_url' && sqlExecutionMode === 'remote' ? 'remote-sql-config-group rounded-t-xl border-x border-t border-emerald-200/80 bg-emerald-50/60 px-4 pt-4 -mx-4' : '',
                      item.key === 'external_sql_api_key' && sqlExecutionMode === 'remote' ? 'remote-sql-config-group border-x border-emerald-200/80 bg-emerald-50/60 px-4 -mx-4 !mt-0' : '',
                      item.key === 'external_sql_data_source' && sqlExecutionMode === 'remote' ? 'remote-sql-config-group rounded-b-xl border-x border-b border-emerald-200/80 bg-emerald-50/60 px-4 pb-4 -mx-4 !mt-0' : ''
                    ]">
                      <div v-if="item.key === 'ragflow_api_url'" class="md:col-span-3 -mb-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm font-semibold text-sky-800">
                        <ServerIcon class="h-4 w-4 shrink-0 text-sky-600" />
                        <span>RAGFlow 连接配置</span>
                        <span class="text-xs font-normal text-sky-700/80">配置地址与密钥后，可立即测试数据集列表接口</span>
                      </div>
                      <div v-if="item.key === 'external_sql_api_url' && sqlExecutionMode === 'remote'" class="md:col-span-3 -mb-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm font-semibold text-emerald-800">
                        <ServerIcon class="h-4 w-4 shrink-0 text-emerald-600" />
                        <span>远程 SQL 执行配置</span>
                        <span class="text-xs font-normal text-emerald-700/80">配置远程服务后，可立即测试 SELECT 1</span>
                      </div>
                      <div v-if="item.key === 'external_sql_api_url' && sqlExecutionMode === 'remote'" class="md:col-span-3 rounded-lg border border-emerald-200/80 bg-white/70 px-4 py-3 text-sm leading-relaxed text-emerald-950">
                        <div class="flex flex-wrap items-center justify-between gap-2">
                          <span class="font-semibold">远程 SQL 模式怎么使用？</span>
                          <span class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs font-medium">
                            <a href="https://github.com/RandyChen1985/nanzi-api-data-platform/blob/main/HOW_TO_INSTALL.md" target="_blank" rel="noopener noreferrer" class="text-emerald-700 underline decoration-emerald-300 underline-offset-2 hover:text-emerald-900">查看部署教程 ↗</a>
                            <a href="https://github.com/RandyChen1985/nanzi-api-data-platform/blob/main/architech/api-schema/sql_execution_api_usage.md" target="_blank" rel="noopener noreferrer" class="text-emerald-700 underline decoration-emerald-300 underline-offset-2 hover:text-emerald-900">查看 API 接入说明 ↗</a>
                          </span>
                        </div>
                        <ol class="mt-2 grid gap-1 text-xs text-emerald-900/80 md:grid-cols-2">
                          <li><span class="font-semibold">1.</span> 部署 NanZi 数据服务平台（推荐 Docker）。</li>
                          <li><span class="font-semibold">2.</span> 在数据服务平台中配置数据源。</li>
                          <li><span class="font-semibold">3.</span> 创建或获取 API Key。</li>
                          <li><span class="font-semibold">4.</span> 填好下方 3 项后，点击「测试连接」。</li>
                        </ol>
                        <div class="mt-2 rounded-md bg-emerald-50/80 px-2.5 py-1.5 text-[11px] text-emerald-800">
                          服务地址示例：<code class="font-mono">http://your-server:8000/api/v1/chatbi/sql/execute</code>；测试会执行安全的 <code class="font-mono">SELECT 1</code>。
                        </div>
                      </div>
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
                         <p class="text-xs text-gray-500 mt-1">
                           {{ item.key === 'sandbox_policy' ? sandboxPolicyShortDesc : (configShortDescriptions[item.key] || item.description) }}
                         </p>
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
                          <div v-else-if="item.key === 'multimodal_model_name'">
                              <select v-model="item.value" :disabled="isConfigItemDisabled(String(category), item)" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 p-2 disabled:opacity-70 disabled:cursor-not-allowed">
                                 <option value="">未配置（不支持识图时提示用户）</option>
                                 <option v-for="m in multimodalModelsForConfig" :key="m.id" :value="m.model_id">
                                    {{ m.name }} ({{ m.model_id }})
                                 </option>
                                 <option v-if="item.value && !multimodalModelsForConfig.find(m => m.model_id === item.value)" :value="item.value">
                                     {{ item.value }} (未知/未启用)
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
                                  💡 <strong>本地元数据模式：</strong>直接在本地查询由元数据字典维护的表和字段，并使用全局 Embedding 算法计算向量，通过<strong>本地 Redis (HNSW) 向量索引</strong>进行高速检索，<strong>无需配置下方的 RAGFlow 地址与密钥</strong>。服务启动时会自动 ensure 索引并全量同步（不 DROP）；变更 Embedding 模型/维度或索引异常时，请点击右侧<strong>「一键重构」</strong>手动 DROP 后重建。
                              </div>
                              <div v-else-if="item.value === 'ragflow'" class="mt-2 text-xs text-amber-700 bg-amber-50/50 p-3 rounded-xl border border-amber-100/50 leading-relaxed select-none">
                                  💡 <strong>RAGFlow 语义检索模式：</strong>需要将本地元数据字典一键同步至 RAGFlow 系统，系统在检索表和字段的描述信息时会调用下方配置的 RAGFlow 网关地址与 API 密钥进行全文 + 向量的混合检索。
                              </div>
                          </div>
                          <div v-else-if="item.key === 'sql_execution_mode'">
                             <select v-model="item.value" :disabled="isConfigItemDisabled(String(category), item)" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 p-2 disabled:opacity-70 disabled:cursor-not-allowed">
                                <option value="remote">remote（调用远程数据服务）</option>
                                <option value="local">local（平台直连数据源）</option>
                             </select>
                          </div>
                          <div v-else-if="item.key === 'sandbox_policy'" class="relative">
                             <button
                               type="button"
                               @click="!isConfigItemDisabled(String(category), item) && (sandboxPolicyOpen = !sandboxPolicyOpen)"
                               :disabled="isConfigItemDisabled(String(category), item)"
                               aria-haspopup="listbox"
                               :aria-expanded="sandboxPolicyOpen"
                               class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 p-2 text-left disabled:opacity-70 disabled:cursor-not-allowed"
                             >
                               <span class="flex min-w-0 items-center gap-2 pr-5">
                                 <component
                                   :is="getSandboxPolicyIcon(currentSandboxPolicy.value)"
                                   class="h-5 w-5 shrink-0 text-indigo-500"
                                   aria-hidden="true"
                                 />
                                 <span class="min-w-0">
                                   <span class="block font-medium text-gray-700">{{ currentSandboxPolicy.label }}</span>
                                   <span class="block text-[11px] text-gray-500 leading-snug">{{ currentSandboxPolicy.desc }}</span>
                                 </span>
                               </span>
                             </button>
                             <ChevronDownIcon class="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
                             <div
                               v-if="sandboxPolicyOpen"
                               class="fixed inset-0 z-40"
                               @click="sandboxPolicyOpen = false"
                               @contextmenu.prevent="sandboxPolicyOpen = false"
                             ></div>
                             <div
                               v-if="sandboxPolicyOpen"
                               class="absolute z-50 mt-1 w-full rounded-lg border border-gray-200 bg-white shadow-xl overflow-hidden divide-y divide-gray-100 ring-1 ring-black/5"
                               role="listbox"
                             >
                               <button
                                 v-for="opt in sandboxPolicyOptions"
                                 :key="opt.value"
                                 type="button"
                                 role="option"
                                 :aria-selected="opt.value === item.value"
                                 :aria-disabled="opt.disabled ? 'true' : 'false'"
                                 @click="selectSandboxPolicy(item, opt.value)"
                                 :disabled="isConfigItemDisabled(String(category), item) || opt.disabled"
                                 class="flex w-full items-start gap-2.5 px-3 py-2.5 text-left hover:bg-indigo-50/80 focus:bg-indigo-50/80 transition-colors disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-white"
                                 :class="opt.value === item.value ? 'bg-indigo-50/70 font-medium' : ''"
                               >
                                 <component
                                   :is="getSandboxPolicyIcon(opt.value)"
                                   class="mt-0.5 h-5 w-5 shrink-0 text-indigo-500"
                                   aria-hidden="true"
                                 />
                                 <span class="min-w-0">
                                   <span class="block text-sm font-medium text-gray-800">{{ opt.label }}</span>
                                   <span class="mt-0.5 block text-[11px] text-gray-500 leading-snug">{{ opt.desc }}</span>
                                 </span>
                               </button>
                             </div>
                             <p class="mt-1.5 text-[11px] text-gray-500 leading-relaxed">
                                切换策略后，沙箱 Bash / 文件工具在对应环境内执行。local 表示{{ sandboxLocalExecDesc }}；docker、e2b 与 ssh 策略的配置项在下方按需填写，仅在对应策略被选中时生效。
                             </p>

                             <div v-if="item.value === 'e2b'" class="mt-3 text-xs text-violet-700 bg-violet-50/60 p-3 rounded-xl border border-violet-100/60 leading-relaxed select-none space-y-1.5">
                                 <div>🌐 <strong>E2B 云端沙箱服务</strong>：E2B（<a href="https://e2b.dev" target="_blank" rel="noopener noreferrer" class="font-medium text-violet-800 underline decoration-violet-300 hover:decoration-violet-600">e2b.dev</a>）是第三方 AI 云端沙箱平台。选择本策略后，Bash 命令在 E2B 云端沙箱内执行；文件读写 / 搜索仍走平台上配置的本地工作目录，不随沙箱上传。</div>
                                 <div>🔑 <strong>API Key 来源</strong>：优先读取下方 <code class="font-mono text-violet-800">sandbox_e2b_api_key</code>；留空则读取进程环境变量 <code class="font-mono text-violet-800">E2B_API_KEY</code>。两者均无时，初始化 E2B 沙箱会失败。请先在 e2b.dev 注册登录并生成 <code class="font-mono text-violet-800">e2b_...</code> 形式的 Key。</div>
                                 <div>💰 <strong>云端付费</strong>：E2B 沙箱运行在第三方云端，按需创建、消耗配额并产生计费；会话结束后暂停（pause）保存磁盘供下次复用，请留意用量与费用。</div>
                                 <div>🛠 <strong>相关配置</strong>：沙箱模板（<code class="font-mono text-violet-800">sandbox_e2b_template</code>）为空时使用 E2B 默认模板；超时时间（<code class="font-mono text-violet-800">sandbox_e2b_timeout_seconds</code>）默认 300 秒。</div>
                             </div>
                          </div>
                          <div v-else-if="item.key === 'platform_timezone'">
                             <select
                               v-model="item.value"
                               :disabled="isConfigItemDisabled(String(category), item)"
                               class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 p-2 disabled:opacity-70 disabled:cursor-not-allowed"
                             >
                                <option value="Asia/Shanghai">Asia/Shanghai（中国标准时间，推荐）</option>
                                <option value="Asia/Hong_Kong">Asia/Hong_Kong</option>
                                <option value="Asia/Tokyo">Asia/Tokyo</option>
                                <option value="Asia/Singapore">Asia/Singapore</option>
                                <option value="UTC">UTC</option>
                                <option value="America/Los_Angeles">America/Los_Angeles</option>
                                <option value="America/New_York">America/New_York</option>
                                <option value="Europe/London">Europe/London</option>
                             </select>
                             <p class="mt-1.5 text-[11px] text-gray-500 leading-relaxed">
                               影响定时任务 Cron「每天 08:00」的解释与下次运行时间，以及平台时间展示。无法修改外部 MySQL 服务器时区时，仍以本项 + 应用容器 TZ 为准。
                             </p>
                          </div>
                          <div v-else-if="item.key === 'download_url_prefix'">
                             <input
                               type="url"
                               v-model="item.value"
                               :disabled="isConfigItemDisabled(String(category), item)"
                               placeholder="https://your-domain.example.com"
                               class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 p-2 disabled:opacity-70 disabled:cursor-not-allowed"
                             />
                             <div class="mt-2 text-xs text-blue-700 bg-blue-50/60 p-3 rounded-xl border border-blue-100/70 leading-relaxed">
                               <div>💡 <strong>设置示例：</strong>填写 <code class="font-mono text-blue-800">https://your-domain.example.com</code>，生成的下载地址会是 <code class="font-mono text-blue-800">https://your-domain.example.com/api/v1/chat/generated-files/...</code>。</div>
                               <div class="mt-1">只填写协议、域名和必要的反向代理前缀，<strong>不要填写</strong> API 路径、文件名或 token。留空时回退到环境变量 <code class="font-mono text-blue-800">APP_PUBLIC_URL</code> 或相对地址。</div>
                             </div>
                          </div>
                          <div v-else-if="item.key === 'sandbox_ssh_auth_type'">
                             <select
                               v-model="item.value"
                               :disabled="isConfigItemDisabled(String(category), item)"
                               class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 p-2 disabled:opacity-70 disabled:cursor-not-allowed"
                             >
                               <option value="password">密码认证（需要 sshpass）</option>
                               <option value="key">私钥认证（推荐）</option>
                               <option v-if="item.value === 'private_key'" value="private_key">私钥认证（历史配置值）</option>
                             </select>
                             <p class="mt-1.5 text-[11px] text-gray-500 leading-relaxed">
                               密码认证使用 sshpass 连接；私钥认证使用下方私钥内容，切换方式只隐藏另一字段，不会自动清空已保存内容。
                             </p>
                          </div>
                          <div v-else-if="item.is_secret && item.key !== 'embed_api_key'" class="relative">
                             <input :type="showSecrets[item.key] ? 'text' : 'password'" v-model="item.value" :disabled="isConfigItemDisabled(String(category), item)" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md pr-10 bg-gray-100 disabled:opacity-70 disabled:cursor-not-allowed" />
                             <div @click="toggleSecret(item.key)" class="absolute inset-y-0 right-0 pr-3 flex items-center cursor-pointer text-gray-400">
                                <EyeIcon v-if="!showSecrets[item.key]" class="h-5 w-5" />
                                <EyeSlashIcon v-else class="h-5 w-5" />
                             </div>
                             <div v-if="item.key === 'ragflow_api_key'" class="mt-3 flex flex-wrap items-center gap-3">
                               <button
                                 type="button"
                                 @click="testRagflowMetadataConnection"
                                 :disabled="ragflowTestDisabled || ragflowConnectionTesting"
                                 class="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-md border border-sky-200 bg-sky-50 px-3 py-2 text-sm font-medium text-sky-700 shadow-sm transition-colors hover:bg-sky-100 disabled:cursor-not-allowed disabled:opacity-50"
                               >
                                 <PlayIcon v-if="!ragflowConnectionTesting" class="h-4 w-4" />
                                 <span v-else class="h-4 w-4 animate-spin rounded-full border-2 border-sky-400 border-t-transparent"></span>
                                 {{ ragflowConnectionTesting ? '测试中...' : '测试连接' }}
                               </button>
                               <span
                                 v-if="ragflowConnectionResult"
                                 class="inline-flex min-w-0 items-center gap-1 text-xs leading-relaxed"
                                 :class="ragflowConnectionResult.status === 'success' ? 'text-emerald-700' : 'text-rose-700'"
                               >
                                 <CheckCircleIcon v-if="ragflowConnectionResult.status === 'success'" class="h-4 w-4 shrink-0" />
                                 <XCircleIcon v-else class="h-4 w-4 shrink-0" />
                                 <span>{{ ragflowConnectionResult.message }}</span>
                               </span>
                               <span v-else class="text-xs text-gray-500">仅测试连接，不会自动保存配置</span>
                             </div>
                          </div>
                          <div v-else-if="item.key === 'external_sql_data_source'" class="space-y-2.5">
                            <input
                              type="text"
                              v-model="item.value"
                              :disabled="isConfigItemDisabled(String(category), item)"
                              class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 disabled:opacity-70 disabled:cursor-not-allowed p-2"
                              placeholder="如 default_clickhouse"
                            />
                            <div class="flex flex-wrap items-center gap-3">
                              <button
                                type="button"
                                @click="testRemoteSqlConnection"
                                :disabled="remoteSqlTestDisabled || remoteSqlConnectionTesting"
                                class="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-700 shadow-sm transition-colors hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-50"
                              >
                                <PlayIcon v-if="!remoteSqlConnectionTesting" class="h-4 w-4" />
                                <span v-else class="h-4 w-4 animate-spin rounded-full border-2 border-emerald-400 border-t-transparent"></span>
                                {{ remoteSqlConnectionTesting ? '测试中...' : '测试连接' }}
                              </button>
                              <span
                                v-if="remoteSqlConnectionResult"
                                class="inline-flex min-w-0 items-center gap-1 text-xs leading-relaxed"
                                :class="remoteSqlConnectionResult.status === 'success' ? 'text-emerald-700' : 'text-rose-700'"
                              >
                                <CheckCircleIcon v-if="remoteSqlConnectionResult.status === 'success'" class="h-4 w-4 shrink-0" />
                                <XCircleIcon v-else class="h-4 w-4 shrink-0" />
                                <span>{{ remoteSqlConnectionResult.message }}</span>
                              </span>
                              <span v-else class="text-xs text-gray-500">仅测试连接，不会自动保存配置</span>
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
                                       class="inline-flex shrink-0 items-center gap-1.5 px-3 py-2 bg-white border border-gray-300 rounded-md text-gray-500 hover:text-primary hover:border-primary transition-colors"
                                       :title="item.key === 'knowledge_ragflow_dataset_ids' ? '测试连接并配置默认知识库' : '选择知识库'"
                                    >
                                       <CircleStackIcon class="w-5 h-5" />
                                       <span v-if="item.key === 'knowledge_ragflow_dataset_ids'" class="whitespace-nowrap text-xs font-medium">测试 &amp; 配置默认知识库</span>
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
                              <div class="mb-3 rounded-md border border-indigo-100 bg-white/80 px-3 py-2 text-xs text-indigo-900">
                                <div class="font-semibold">向量模式配置</div>
                                <div class="mt-0.5 text-indigo-700/80">用于会话记忆向量化以及 Redis 模式下元数据向量化</div>
                              </div>
                              <div class="mb-2 flex flex-col gap-2 sm:flex-row sm:items-center">
                                  <select
                                    v-model="selectedEmbedModelId"
                                    :disabled="isConfigItemDisabled(String(category), item) || embeddingModelsForConfig.length === 0"
                                    class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:flex-1 sm:min-w-0 sm:text-sm border-gray-300 rounded-md bg-white p-2 disabled:opacity-70 disabled:cursor-not-allowed"
                                    title="从模型管理选择已配置的 Embedding 模型"
                                  >
                                    <option value="" disabled>
                                      {{ embeddingModelsForConfig.length === 0 ? '暂无可用 Embedding 模型（请先在模型管理添加）' : '从模型管理选择 Embedding…' }}
                                    </option>
                                    <option
                                      v-for="m in embeddingModelsForConfig"
                                      :key="m.id"
                                      :value="m.id"
                                    >
                                      {{ m.name }} ({{ m.model_id }})
                                    </option>
                                  </select>
                                  <button
                                    type="button"
                                    @click="loadEmbedConfigFromModel"
                                    :disabled="isConfigItemDisabled(String(category), item) || !selectedEmbedModelId"
                                    class="inline-flex shrink-0 items-center justify-center px-3 py-2 border border-indigo-200 shadow-sm text-sm leading-4 font-medium rounded-md text-indigo-700 bg-indigo-50 hover:bg-indigo-100 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary disabled:opacity-50"
                                    title="将所选模型的 API 地址与模型名填入下方配置"
                                  >
                                    加载配置
                                  </button>
                              </div>
                              <p class="mb-2 text-[11px] text-gray-500 leading-relaxed">
                                快捷加载会填入 <strong>API 地址</strong>与<strong>模型名</strong>；Key 因脱敏无法自动填入（模型管理已有 Key 时可留空）；<strong>向量维度</strong>请自行核对后保存。
                              </p>
                              <div class="flex items-center space-x-2">
                                  <input type="text" v-model="item.value" :disabled="isConfigItemDisabled(String(category), item)" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 disabled:opacity-70 disabled:cursor-not-allowed p-2" />
                              </div>
                          </div>
                          <div v-else-if="item.key === 'embed_api_key'">
                              <div class="relative">
                                  <input :type="showSecrets[item.key] ? 'text' : 'password'" v-model="item.value" :disabled="isConfigItemDisabled(String(category), item)" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md pr-10 bg-gray-100 disabled:opacity-70 disabled:cursor-not-allowed" />
                                  <div @click="toggleSecret(item.key)" class="absolute inset-y-0 right-0 pr-3 flex items-center cursor-pointer text-gray-400">
                                      <EyeIcon v-if="!showSecrets[item.key]" class="h-5 w-5" />
                                      <EyeSlashIcon v-else class="h-5 w-5" />
                                  </div>
                              </div>
                              <p class="mt-1.5 text-[11px] text-gray-500">API Key 可留空；若供应商需要鉴权，测试时会返回对应错误。</p>
                          </div>
                          <div v-else-if="item.key === 'embed_model_name'">
                              <input type="text" v-model="item.value" :disabled="isConfigItemDisabled(String(category), item)" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 disabled:opacity-70 disabled:cursor-not-allowed p-2" />
                          </div>
                          <div v-else-if="item.key === 'embed_dimensions'">
                              <input type="text" v-model="item.value" @keypress="!/[0-9]/.test(($event as KeyboardEvent).key) && ($event as KeyboardEvent).preventDefault()" @input="item.value = item.value.replace(/\D/g, '')" :disabled="isConfigItemDisabled(String(category), item)" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 disabled:opacity-70 disabled:cursor-not-allowed p-2" />
                              <button
                                  type="button"
                                  @click="testGlobalEmbed"
                                  :disabled="globalEmbedTesting"
                                  class="mt-3 inline-flex items-center px-3 py-2 border border-gray-300 shadow-sm text-sm leading-4 font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary disabled:opacity-50"
                              >
                                  <PlayIcon v-if="!globalEmbedTesting" class="h-4 w-4 mr-1.5 text-gray-500" />
                                  <span v-else class="animate-spin h-4 w-4 mr-1.5 border-2 border-primary border-t-transparent rounded-full"></span>
                                  测试 Embedding 连接
                              </button>
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
                                      <div v-if="item.key === 'llm_temperature'" class="mt-2 grid grid-cols-1 gap-1 text-[11px] leading-4 text-gray-500 sm:grid-cols-3 sm:gap-2">
                                          <p v-for="guidance in temperatureScaleGuidance" :key="guidance.range">
                                              <span class="font-medium text-gray-600">{{ guidance.range }}</span>：{{ guidance.description }}
                                          </p>
                                          <p class="sm:col-span-3 text-blue-600">
                                              当前 {{ Number(item.value).toFixed(2) }}：{{ getTemperatureGuidance(item.value) }}
                                          </p>
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
                          <div v-else-if="['embedchat_watermark_enabled', 'yovole_sso_enabled', 'knowledge_base_enabled', 'agentscope_inject_runtime_state'].includes(item.key)">
                             <div class="flex items-center">
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
                             <p
                               v-if="item.key === 'agentscope_inject_runtime_state' && item.description"
                               class="mt-1.5 text-[11px] text-gray-500 leading-relaxed"
                             >{{ item.description }}</p>
                          </div>
                          <div v-else-if="item.key === 'agentscope_inject_time_interval_hours'">
                             <div class="flex items-center gap-3 max-w-xs">
                               <input
                                 type="number"
                                 inputmode="decimal"
                                 min="0"
                                 max="24"
                                 step="0.1"
                                 :value="item.value"
                                 :disabled="isConfigItemDisabled(String(category), item)"
                                 @keypress="!/[0-9.]/.test(($event as KeyboardEvent).key) && ($event as KeyboardEvent).preventDefault()"
                                 @input="(e) => {
                                   const raw = (e.target as HTMLInputElement).value.replace(/[^\d.]/g, '')
                                   const parts = raw.split('.')
                                   const normalized = parts.length <= 1 ? raw : `${parts[0]}.${parts.slice(1).join('').slice(0, 2)}`
                                   item.value = normalized
                                   ;(e.target as HTMLInputElement).value = normalized
                                 }"
                                 @blur="() => {
                                   const n = Number(item.value)
                                   if (!Number.isFinite(n) || item.value === '' || item.value === '.') {
                                     item.value = '0.5'
                                     return
                                   }
                                   item.value = String(Math.min(24, Math.max(0, n)))
                                 }"
                                 class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-white disabled:opacity-70 disabled:cursor-not-allowed p-2"
                               />
                               <span class="text-sm text-gray-500 shrink-0">小时</span>
                             </div>
                             <p
                               v-if="item.description"
                               class="mt-1.5 text-[11px] text-gray-500 leading-relaxed"
                             >{{ item.description }}</p>
                          </div>
                          <div v-else-if="['agent_context_compaction_enabled', 'agent_context_llm_summary_enabled'].includes(item.key)">
                             <select v-model="item.value" :disabled="isConfigItemDisabled(String(category), item)" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 p-2 disabled:opacity-70 disabled:cursor-not-allowed">
                                <option value="true">true (开启)</option>
                                <option value="false">false (关闭)</option>
                             </select>
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
                          <div v-else-if="item.key === 'agent_context_max_tokens'">
                             <input
                               type="text"
                               v-model="item.value"
                               @keypress="!/[0-9]/.test(($event as KeyboardEvent).key) && ($event as KeyboardEvent).preventDefault()"
                               @input="item.value = item.value.replace(/\D/g, '')"
                               :disabled="isConfigItemDisabled(String(category), item)"
                               class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 disabled:opacity-70 disabled:cursor-not-allowed p-2 font-mono"
                               placeholder="如 65536"
                             />
                             <div class="flex flex-wrap items-center gap-1.5 mt-2">
                                <span class="text-[11px] text-gray-400 select-none mr-0.5">常用预设:</span>
                                <button
                                  v-for="preset in [
                                    { label: '16k', value: '16384' },
                                    { label: '32k', value: '32768' },
                                    { label: '64k (推荐)', value: '65536' },
                                    { label: '128k', value: '131072' },
                                    { label: '256k', value: '262144' },
                                  ]"
                                  :key="preset.value"
                                  type="button"
                                  :disabled="isConfigItemDisabled(String(category), item)"
                                  @click="item.value = preset.value"
                                  class="px-2 py-0.5 text-xs rounded-md border transition-all duration-150 select-none disabled:opacity-50 disabled:cursor-not-allowed"
                                  :class="item.value === preset.value
                                    ? 'bg-primary/10 text-primary border-primary/30 font-medium shadow-xs ring-1 ring-primary/20'
                                    : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50 hover:border-gray-300 hover:text-gray-900'"
                                >
                                  {{ preset.label }}
                                </button>
                             </div>
                             <p class="mt-2 text-[11px] text-gray-500 leading-relaxed">
                                系统优先采用当前模型配置的上下文窗口；本配置仅在模型未配置有效上下文大小时作为兜底预算。
                             </p>
                          </div>
                          <div v-else-if="item.key === 'agent_max_context_messages'">
                             <input
                               type="text"
                               v-model="item.value"
                               @keypress="!/[0-9]/.test(($event as KeyboardEvent).key) && ($event as KeyboardEvent).preventDefault()"
                               @input="item.value = item.value.replace(/\D/g, '')"
                               :disabled="isConfigItemDisabled(String(category), item)"
                               class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 disabled:opacity-70 disabled:cursor-not-allowed p-2 font-mono"
                               placeholder="如 60"
                             />
                             <div class="flex flex-wrap items-center gap-1.5 mt-2">
                                <span class="text-[11px] text-gray-400 select-none mr-0.5">常用预设:</span>
                                <button
                                  v-for="preset in [
                                    { label: '30 条', value: '30' },
                                    { label: '60 条 (推荐)', value: '60' },
                                    { label: '100 条', value: '100' },
                                    { label: '200 条', value: '200' },
                                  ]"
                                  :key="preset.value"
                                  type="button"
                                  :disabled="isConfigItemDisabled(String(category), item)"
                                  @click="item.value = preset.value"
                                  class="px-2 py-0.5 text-xs rounded-md border transition-all duration-150 select-none disabled:opacity-50 disabled:cursor-not-allowed"
                                  :class="item.value === preset.value
                                    ? 'bg-primary/10 text-primary border-primary/30 font-medium shadow-xs ring-1 ring-primary/20'
                                    : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50 hover:border-gray-300 hover:text-gray-900'"
                                >
                                  {{ preset.label }}
                                </button>
                             </div>
                          </div>
                          <div v-else-if="item.key === 'agent_context_compaction_max_chars'">
                             <input
                               type="text"
                               v-model="item.value"
                               @keypress="!/[0-9]/.test(($event as KeyboardEvent).key) && ($event as KeyboardEvent).preventDefault()"
                               @input="item.value = item.value.replace(/\D/g, '')"
                               :disabled="isConfigItemDisabled(String(category), item)"
                               class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 disabled:opacity-70 disabled:cursor-not-allowed p-2 font-mono"
                               placeholder="如 1200"
                             />
                             <div class="flex flex-wrap items-center gap-1.5 mt-2">
                                <span class="text-[11px] text-gray-400 select-none mr-0.5">常用预设:</span>
                                <button
                                  v-for="preset in [
                                    { label: '600 字', value: '600' },
                                    { label: '1200 字 (推荐)', value: '1200' },
                                    { label: '2000 字', value: '2000' },
                                    { label: '3000 字', value: '3000' },
                                  ]"
                                  :key="preset.value"
                                  type="button"
                                  :disabled="isConfigItemDisabled(String(category), item)"
                                  @click="item.value = preset.value"
                                  class="px-2 py-0.5 text-xs rounded-md border transition-all duration-150 select-none disabled:opacity-50 disabled:cursor-not-allowed"
                                  :class="item.value === preset.value
                                    ? 'bg-primary/10 text-primary border-primary/30 font-medium shadow-xs ring-1 ring-primary/20'
                                    : 'bg-white text-gray-600 border-gray-200 hover:bg-gray-50 hover:border-gray-300 hover:text-gray-900'"
                                >
                                  {{ preset.label }}
                                </button>
                             </div>
                          </div>
                          <div v-else-if="item.key === 'agent_max_toolcall_timeout'" class="flex items-center gap-2 max-w-xs">
                             <button
                               type="button"
                               aria-label="减少工具调用超时时间"
                               :disabled="isConfigItemDisabled(String(category), item) || getAgentToolcallTimeoutValue(item) <= 1"
                               @click="adjustAgentToolcallTimeout(item, -1)"
                               class="inline-flex h-9 w-9 items-center justify-center rounded-md border border-gray-300 bg-white text-lg leading-none text-gray-600 shadow-sm hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
                             >−</button>
                             <input
                               type="number"
                               inputmode="numeric"
                               min="1"
                               max="3600"
                               step="1"
                               :value="item.value"
                               :disabled="isConfigItemDisabled(String(category), item)"
                               @keydown="handleAgentToolcallTimeoutKeydown"
                               @input="handleAgentToolcallTimeoutInput(item, $event)"
                               @blur="normalizeAgentToolcallTimeoutInput(item)"
                               aria-label="Agent 工具调用最大超时时间（秒）"
                               class="min-w-[5rem] rounded-md border border-gray-300 bg-gray-100 px-3 py-2 text-center font-mono text-sm text-gray-700 shadow-sm focus:border-primary focus:ring-primary disabled:cursor-not-allowed disabled:opacity-70"
                             />
                             <button
                               type="button"
                               aria-label="增加工具调用超时时间"
                               :disabled="isConfigItemDisabled(String(category), item) || getAgentToolcallTimeoutValue(item) >= 3600"
                               @click="adjustAgentToolcallTimeout(item, 1)"
                               class="inline-flex h-9 w-9 items-center justify-center rounded-md border border-gray-300 bg-white text-lg leading-none text-gray-600 shadow-sm hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
                             >+</button>
                             <span class="text-xs text-gray-500">秒</span>
                          </div>
                          <div v-else-if="item.key === 'agent_tool_loop_global_limit'" class="flex items-center gap-2 max-w-xs">
                             <button
                               type="button"
                               aria-label="减少工具调用总次数上限"
                               :disabled="isConfigItemDisabled(String(category), item) || getAgentToolLoopGlobalLimitValue(item) <= 1"
                               @click="adjustAgentToolLoopGlobalLimit(item, -1)"
                               class="inline-flex h-9 w-9 items-center justify-center rounded-md border border-gray-300 bg-white text-lg leading-none text-gray-600 shadow-sm hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
                             >−</button>
                             <input
                               type="number"
                               inputmode="numeric"
                               min="1"
                               max="3600"
                               step="1"
                               :value="item.value"
                               :disabled="isConfigItemDisabled(String(category), item)"
                               @keydown="handleAgentToolLoopGlobalLimitKeydown"
                               @input="handleAgentToolLoopGlobalLimitInput(item, $event)"
                               @blur="normalizeAgentToolLoopGlobalLimitInput(item)"
                               aria-label="Agent 工具调用总次数上限"
                               class="min-w-[5rem] rounded-md border border-gray-300 bg-gray-100 px-3 py-2 text-center font-mono text-sm text-gray-700 shadow-sm focus:border-primary focus:ring-primary disabled:cursor-not-allowed disabled:opacity-70"
                             />
                             <button
                               type="button"
                               aria-label="增加工具调用总次数上限"
                               :disabled="isConfigItemDisabled(String(category), item) || getAgentToolLoopGlobalLimitValue(item) >= 3600"
                               @click="adjustAgentToolLoopGlobalLimit(item, 1)"
                               class="inline-flex h-9 w-9 items-center justify-center rounded-md border border-gray-300 bg-white text-lg leading-none text-gray-600 shadow-sm hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
                             >+</button>
                             <span class="text-xs text-gray-500">次</span>
                          </div>
                          <div v-else-if="['audit_log_retention_days', 'agent_max_iterations', 'agent_max_context_turns', 'data_api_timeout_seconds', 'schema_api_timeout_seconds', 'ragflow_metadata_top_k', 'knowledge_ragflow_metadata_top_k', 'chatbi_sample_top_k'].includes(item.key)">
	                             <input type="text" v-model="item.value" @keypress="!/[0-9]/.test(($event as KeyboardEvent).key) && ($event as KeyboardEvent).preventDefault()" @input="item.value = item.value.replace(/\D/g, '')" :disabled="isConfigItemDisabled(String(category), item)" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 disabled:opacity-70 disabled:cursor-not-allowed p-2" />
                          </div>
                          <div v-else-if="item.key === 'sandbox_docker_base_image'" class="space-y-2.5">
                            <div class="relative">
                              <button
                                type="button"
                                @click="toggleDockerBaseImage(item)"
                                :disabled="isConfigItemDisabled(String(category), item)"
                                aria-haspopup="listbox"
                                :aria-expanded="dockerBaseImageOpen"
                                class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 p-2 text-left disabled:opacity-70 disabled:cursor-not-allowed"
                                title="选择内置镜像或自定义镜像地址"
                              >
                                <span class="block font-medium text-gray-700 truncate">{{ currentDockerBaseImageLabel }}</span>
                              </button>
                              <ChevronDownIcon class="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
                              <div
                                v-if="dockerBaseImageOpen"
                                class="fixed inset-0 z-30"
                                @click="dockerBaseImageOpen = false"
                                @contextmenu.prevent="dockerBaseImageOpen = false"
                              ></div>
                              <div
                                v-if="dockerBaseImageOpen"
                                class="absolute left-0 top-full mt-1 w-full z-40 bg-white rounded-lg border border-gray-200 shadow-lg py-1 max-h-72 overflow-y-auto"
                                role="listbox"
                              >
                                <button
                                  v-for="preset in dockerBaseImagePresets"
                                  :key="preset.value"
                                  type="button"
                                  @click="selectDockerBaseImage(item, preset.value)"
                                  role="option"
                                  :aria-selected="item.value === preset.value && !isCustomDockerBaseImage"
                                  class="block w-full px-3 py-2 text-left text-sm transition-colors hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                                  :class="item.value === preset.value && !isCustomDockerBaseImage ? 'bg-indigo-50 font-medium text-primary' : 'text-gray-700'"
                                >
                                  <span class="block">{{ preset.label }}</span>
                                </button>
                                <button
                                  type="button"
                                  @click="selectDockerBaseImage(item, '_custom')"
                                  role="option"
                                  :aria-selected="isCustomDockerBaseImage"
                                  class="block w-full px-3 py-2 text-left text-sm transition-colors hover:bg-gray-50"
                                  :class="isCustomDockerBaseImage ? 'bg-indigo-50 font-medium text-primary' : 'text-gray-700'"
                                >
                                  自定义镜像地址…
                                </button>
                              </div>
                            </div>

                            <div v-if="isCustomDockerBaseImage" class="pt-0.5">
                              <input
                                type="text"
                                @change="refreshDockerPrebuildStatus(true)"
                                @input="refreshDockerPrebuildStatus(true)"
                                v-model="item.value"
                                :disabled="isConfigItemDisabled(String(category), item)"
                                class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-white p-2 font-mono disabled:opacity-70 disabled:cursor-not-allowed"
                                placeholder="如 registry.example.com/ai/python:3.11-slim"
                              />
                              <p class="mt-1 text-[11px] text-gray-500">
                                请填写可拉取的 Docker 镜像完整路径（需内置 Python 3.10+ 与 Debian/Ubuntu apt 环境）。
                              </p>
                            </div>

                            <div v-if="!isConfigItemDisabled(String(category), item)" class="mt-3 flex flex-wrap items-center gap-3">
                               <button
                                 type="button"
                                 @click="refreshDockerPrebuildStatus()"
                                 :disabled="dockerPrebuilding || dockerPrebuildChecking"
                                 class="inline-flex shrink-0 items-center justify-center py-2 px-3 border border-gray-200 rounded-md shadow-sm text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50 whitespace-nowrap"
                                 title="只查询当前 Docker 沙箱镜像是否已预构建，不会触发构建"
                               >
                                 <ArrowPathIcon :class="['h-4 w-4 mr-1.5', dockerPrebuildChecking ? 'animate-spin' : '']" />
                                 {{ dockerPrebuildChecking ? '检查中...' : '刷新状态' }}
                               </button>
                               <button
                                 type="button"
                                 @click="executeDockerPrebuild"
                                 :disabled="dockerPrebuilding || dockerPrebuildChecking"
                                 class="inline-flex shrink-0 items-center justify-center py-2 px-3 border border-indigo-200 rounded-md shadow-sm text-sm font-medium text-indigo-700 bg-indigo-50 hover:bg-indigo-100 disabled:opacity-50 whitespace-nowrap"
                                 title="按当前 sandbox_docker_base_image 预构建/复用沙箱镜像，避免首个会话等待构建"
                               >
                                 <ArrowPathIcon v-if="!dockerPrebuilding" class="h-4 w-4 mr-1.5" />
                                 <span v-else class="animate-spin h-4 w-4 mr-1.5 border-2 border-indigo-400 border-t-transparent rounded-full"></span>
                                 <template v-if="dockerPrebuilding">预构建中 {{ dockerPrebuildElapsedSeconds }}s</template>
                                 <template v-else>{{ dockerPrebuilt ? '重新预构建' : '预构建镜像' }}</template>
                               </button>
                               <span v-if="dockerPrebuildChecking" class="text-xs text-gray-500">正在检查预构建状态...</span>
                               <span v-else-if="dockerPrebuilt" class="text-xs text-green-700 bg-green-50 border border-green-100 rounded-md px-2.5 py-1">
                                 ✅ 镜像已预构建
                                 <template v-if="dockerPrebuildReused">（复用了既有缓存）</template>
                               </span>
                               <span v-else class="text-xs text-amber-700 bg-amber-50 border border-amber-100 rounded-md px-2.5 py-1">
                                 ⚠ 镜像尚未预构建，首个 docker 沙箱会话需等待构建
                               </span>
                             </div>
                             <div
                               v-if="dockerPrebuilding || dockerPrebuildLogs.length || dockerPrebuildError"
                               class="mt-3 overflow-hidden rounded-md border border-slate-200 bg-slate-950 shadow-inner"
                             >
                               <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-700 px-3 py-2 text-xs text-slate-200">
                                 <div class="flex items-center gap-2">
                                   <span class="h-2 w-2 rounded-full" :class="dockerPrebuildError ? 'bg-red-400' : (dockerPrebuilding ? 'animate-pulse bg-amber-400' : 'bg-emerald-400')"></span>
                                   <button
                                     type="button"
                                     @click="toggleDockerPrebuildLogs"
                                     class="font-medium text-left text-slate-200 hover:text-white"
                                   >
                                     {{ dockerPrebuildLogsExpanded ? '收起构建日志' : '展开构建日志' }}
                                   </button>
                                   <span class="text-slate-400">{{ dockerPrebuildError ? '构建失败' : dockerPrebuildStageLabel }}</span>
                                   <span class="text-slate-400">{{ dockerPrebuildLogs.length }} 条日志</span>
                                 </div>
                                 <button
                                   type="button"
                                   @click="copyDockerPrebuildLogs"
                                   :disabled="!dockerPrebuildLogsText"
                                   class="rounded border border-slate-600 px-2 py-1 text-[11px] text-slate-200 hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
                                 >
                                   {{ dockerPrebuildLogsCopied ? '已复制' : '复制构建日志' }}
                                 </button>
                               </div>
                               <pre
                                 v-if="dockerPrebuildLogsExpanded"
                                 ref="dockerPrebuildLogContainer"
                                 @scroll="handleDockerPrebuildLogScroll"
                                 class="max-h-72 overflow-y-auto whitespace-pre-wrap break-words px-3 py-2 font-mono text-[11px] leading-relaxed text-slate-200"
                               >{{ dockerPrebuildLogsText || '正在等待 Docker 构建输出…' }}</pre>
                               <div v-if="dockerPrebuildError" class="border-t border-red-900/80 bg-red-950/60 px-3 py-2 text-xs leading-relaxed text-red-200">
                                 <span class="font-semibold">失败原因：</span>{{ dockerPrebuildError }}
                               </div>
                             </div>
                             <div v-if="dockerPrebuildTag" class="mt-1.5 text-[11px] text-gray-500 leading-relaxed select-none">
                               当前预构建镜像 Tag：<code class="font-mono text-indigo-700">{{ dockerPrebuildTag }}</code>
                             </div>
                             <div v-if="dockerPrebuildMessage && !dockerPrebuilt" class="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
                               <div class="font-medium">{{ dockerPrebuildMessage }}</div>
                               <a
                                 :href="dockerPrebuildHelpUrl"
                                 target="_blank"
                                 rel="noopener noreferrer"
                                 class="mt-1.5 inline-flex items-center gap-1 text-indigo-700 underline hover:text-indigo-900 font-medium"
                               >
                                 📖 查看 FAQ 离线镜像与沙箱环境排查指南 ↗
                               </a>
                             </div>
                          </div>
                          <div v-else>
                             <input type="text" v-model="item.value" :disabled="isConfigItemDisabled(String(category), item)" class="shadow-sm focus:ring-primary focus:border-primary block w-full sm:text-sm border-gray-300 rounded-md bg-gray-100 disabled:opacity-70 disabled:cursor-not-allowed" />
                             <div v-if="canSave && item.key === 'sandbox_e2b_timeout_seconds'" class="mt-3">
                               <button
                                 type="button"
                                 @click="testSandboxConnection('e2b')"
                                 :disabled="sandboxConnectionTesting !== null"
                                 class="inline-flex items-center justify-center py-2 px-3 border border-violet-200 rounded-md shadow-sm text-sm font-medium text-violet-700 bg-violet-50 hover:bg-violet-100 disabled:opacity-50 whitespace-nowrap"
                                 title="按当前页面填写的 E2B 配置创建一次临时沙箱并立即释放"
                               >
                                 <ArrowPathIcon v-if="sandboxConnectionTesting !== 'e2b'" class="h-4 w-4 mr-1.5" />
                                 <span v-else class="animate-spin h-4 w-4 mr-1.5 border-2 border-violet-400 border-t-transparent rounded-full"></span>
                                 {{ sandboxConnectionTesting === 'e2b' ? '测试中...' : '测试连接' }}
                               </button>
                               <p class="mt-1.5 text-[11px] text-gray-500 leading-relaxed">
                                 使用当前填写值测试，不会保存配置；E2B 会创建临时云沙箱并消耗配额。
                               </p>
                             </div>
                             <div v-else-if="canSave && item.key === 'sandbox_ssh_remote_workdir'" class="mt-3">
                               <button
                                 type="button"
                                 @click="testSandboxConnection('ssh')"
                                 :disabled="sandboxConnectionTesting !== null"
                                 class="inline-flex items-center justify-center py-2 px-3 border border-sky-200 rounded-md shadow-sm text-sm font-medium text-sky-700 bg-sky-50 hover:bg-sky-100 disabled:opacity-50 whitespace-nowrap"
                                 title="按当前页面填写的 SSH 配置执行一次真实连接与远程工作目录检查"
                               >
                                 <ArrowPathIcon v-if="sandboxConnectionTesting !== 'ssh'" class="h-4 w-4 mr-1.5" />
                                 <span v-else class="animate-spin h-4 w-4 mr-1.5 border-2 border-sky-400 border-t-transparent rounded-full"></span>
                                 {{ sandboxConnectionTesting === 'ssh' ? '测试中...' : '测试连接' }}
                               </button>
                               <p class="mt-1.5 text-[11px] text-gray-500 leading-relaxed">
                                 使用当前填写值测试，不会保存配置；会检查 SSH 认证和远程工作目录。
                               </p>
                             </div>
                          </div>
                       </div>
                   </div>
                </div>
             </div>
              <div v-if="canSave" class="flex justify-end pt-4 pb-12">
                 <button @click="resetUnsavedConfigs" class="mr-4 bg-white py-2 px-4 border border-gray-300 rounded-md shadow-sm text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors cursor-pointer">重置修改</button>
                 <button @click="saveConfigs" :disabled="saving" class="inline-flex items-center gap-1.5 justify-center py-2 px-5 border border-transparent shadow-sm text-sm font-bold rounded-md text-white bg-primary hover:bg-primary/90 disabled:opacity-50 transition-all cursor-pointer">
                   <svg v-if="saving" class="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
                     <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                     <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                   </svg>
                   <span>{{ saving ? '保存中...' : '保存变更 (⌘S)' }}</span>
                 </button>
              </div>
          </div>
       </div>

       <!-- 底部浮动吸底保存提示条 (Sticky Save Bar) -->
       <transition
         enter-active-class="transition duration-300 ease-out"
         enter-from-class="transform translate-y-12 opacity-0"
         enter-to-class="transform translate-y-0 opacity-100"
         leave-active-class="transition duration-200 ease-in"
         leave-from-class="transform translate-y-0 opacity-100"
         leave-to-class="transform translate-y-12 opacity-0"
       >
         <div
           v-if="activeTab === 'configs' && hasUnsavedConfigChanges && canSave"
           class="fixed bottom-6 left-1/2 z-40 flex -translate-x-1/2 items-center gap-4 rounded-2xl border border-amber-200/90 bg-white/95 px-5 py-3 shadow-2xl backdrop-blur-md"
         >
           <div class="flex items-center gap-2.5">
             <span class="relative flex h-3 w-3">
               <span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-75"></span>
               <span class="relative inline-flex h-3 w-3 rounded-full bg-amber-500"></span>
             </span>
             <div class="text-xs font-semibold text-gray-800">
               您有 <span class="font-bold text-amber-600">{{ unsavedConfigCount }}</span> 项未保存的配置修改
             </div>
           </div>

           <div class="flex items-center gap-2 border-l border-gray-200 pl-4">
             <button
               type="button"
               class="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 shadow-2xs hover:bg-gray-50 hover:text-gray-900 transition-colors cursor-pointer"
               @click="resetUnsavedConfigs"
             >
               放弃修改
             </button>
             <button
               type="button"
               :disabled="saving"
               class="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-1.5 text-xs font-bold text-white shadow-md shadow-primary/20 hover:bg-primary/90 transition-all disabled:opacity-50 cursor-pointer"
               @click="saveConfigs"
             >
               <svg v-if="saving" class="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                 <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                 <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
               </svg>
               <span>{{ saving ? '保存中...' : '保存变更 (⌘S)' }}</span>
             </button>
           </div>
         </div>
       </transition>
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
              {{ activeExplanationItem.key === 'sandbox_policy' ? sandboxPolicyTip : (activeExplanationItem.key === 'sql_execution_mode' ? 'SQL 查询执行位置。当前仅支持 remote 和 local 两种模式。' : (activeExplanationItem.description || '暂无描述信息。')) }}
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
