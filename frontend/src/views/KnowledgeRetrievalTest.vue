<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import axios from '../utils/axios'
import RagFlowResourceSelector from '../components/RagFlowResourceSelector.vue'
import { useToast } from '../composables/useToast'
import { useUser } from '../composables/useUser'

type RetrievalChunk = {
  id?: string
  chunk_id?: string
  document_id?: string
  document_name?: string
  doc_name?: string
  similarity?: number
  score?: number
  content?: string
  text?: string
}

type RagFlowConfigSummary = {
  api_url: string
  api_key_configured: boolean
  configured: boolean
  knowledge_base_enabled?: boolean
}

const { showToast } = useToast()
const { hasPermission } = useUser()

const showDatasetSelector = ref(false)
const datasetIds = ref<string[]>([])
const query = ref('')

// A/B 对照检索模式状态
const isCompareMode = ref(false)

// A 参数组使用原本的变量名
const topK = ref(5)
const similarityThreshold = ref(0.2)
const vectorSimilarityWeight = ref(0.3)

// B 参数组
const topK_B = ref(8)
const similarityThreshold_B = ref(0.2)
const vectorSimilarityWeight_B = ref(0.3)

const loading = ref(false)
const loading_B = ref(false)
const results = ref<RetrievalChunk[]>([])
const results_B = ref<RetrievalChunk[]>([])
const errorMessage = ref('')
const errorMessage_B = ref('')

const ragflowConfig = ref<RagFlowConfigSummary | null>(null)
const engineStatus = ref<'checking' | 'connected' | 'disconnected'>('checking')

const isKnowledgeEnabled = computed(() => ragflowConfig.value?.knowledge_base_enabled !== false)
const isEngineReady = computed(() => isKnowledgeEnabled.value && engineStatus.value === 'connected')
const canTest = computed(() => hasPermission('element:knowledge:test_retrieval') && isEngineReady.value)
const datasetIdsText = computed(() => datasetIds.value.join(','))
const ragflowApiUrl = computed(() => ragflowConfig.value?.api_url || '未配置')

const friendlyRagFlowError = computed(() => {
  if (!errorMessage.value) return ''
  const lower = errorMessage.value.toLowerCase()
  if (
    lower.includes('ragflow') ||
    lower.includes('bad gateway') ||
    lower.includes('failed to connect') ||
    lower.includes('configuration missing')
  ) {
    return '当前无法连接 RAGFlow 服务，请确认 RAGFlow 服务是否可访问、网关是否正常，以及系统配置中的 RAGFlow 地址/API Key 是否正确。'
  }
  return errorMessage.value
})

const friendlyRagFlowErrorB = computed(() => {
  if (!errorMessage_B.value) return ''
  const lower = errorMessage_B.value.toLowerCase()
  if (
    lower.includes('ragflow') ||
    lower.includes('bad gateway') ||
    lower.includes('failed to connect') ||
    lower.includes('configuration missing')
  ) {
    return '当前无法连接 RAGFlow 服务，请确认 RAGFlow 服务是否可访问、网关是否正常，以及系统配置中的 RAGFlow 地址/API Key 是否正确。'
  }
  return errorMessage_B.value
})

const extractError = (err: unknown) => {
  const anyErr = err as any
  return anyErr?.response?.data?.detail || anyErr?.response?.data?.message || anyErr?.message || '检索失败'
}

const handleDatasetSelect = (value: string | string[]) => {
  datasetIds.value = Array.isArray(value) ? value : [value].filter(Boolean)
}

const validate = () => {
  if (datasetIds.value.length === 0) {
    showToast('请至少选择一个知识库', 'warning')
    return false
  }
  if (!query.value.trim()) {
    showToast('请输入检索问题', 'warning')
    return false
  }
  if (topK.value < 1 || topK.value > 50) {
    showToast('A组 top_k 需在 1 到 50 之间', 'warning')
    return false
  }
  return true
}

const validateB = () => {
  if (topK_B.value < 1 || topK_B.value > 50) {
    showToast('B组 top_k 需在 1 到 50 之间', 'warning')
    return false
  }
  return true
}

const runRetrieval = async () => {
  if (!isKnowledgeEnabled.value) {
    showToast('知识库功能未开启', 'warning')
    return
  }
  if (!validate()) return
  if (isCompareMode.value && !validateB()) return

  errorMessage.value = ''
  errorMessage_B.value = ''

  if (!isCompareMode.value) {
    loading.value = true
    results.value = []
    results_B.value = []
    try {
      const response = await axios.post('/api/portal/ragflow/retrieval-test', {
        query: query.value.trim(),
        dataset_ids: datasetIds.value,
        top_k: topK.value,
        similarity_threshold: similarityThreshold.value,
        vector_similarity_weight: vectorSimilarityWeight.value
      })
      results.value = response.data?.data || []
      showToast(`检索完成，命中 ${results.value.length} 条`, 'success')
    } catch (err) {
      errorMessage.value = extractError(err)
      showToast(errorMessage.value, 'error')
    } finally {
      loading.value = false
    }
  } else {
    loading.value = true
    loading_B.value = true
    results.value = []
    results_B.value = []
    
    const promiseA = axios.post('/api/portal/ragflow/retrieval-test', {
      query: query.value.trim(),
      dataset_ids: datasetIds.value,
      top_k: topK.value,
      similarity_threshold: similarityThreshold.value,
      vector_similarity_weight: vectorSimilarityWeight.value
    }).then(response => {
      results.value = response.data?.data || []
    }).catch(err => {
      errorMessage.value = extractError(err)
    }).finally(() => {
      loading.value = false
    })

    const promiseB = axios.post('/api/portal/ragflow/retrieval-test', {
      query: query.value.trim(),
      dataset_ids: datasetIds.value,
      top_k: topK_B.value,
      similarity_threshold: similarityThreshold_B.value,
      vector_similarity_weight: vectorSimilarityWeight_B.value
    }).then(response => {
      results_B.value = response.data?.data || []
    }).catch(err => {
      errorMessage_B.value = extractError(err)
    }).finally(() => {
      loading_B.value = false
    })

    await Promise.all([promiseA, promiseB])
    if (errorMessage.value || errorMessage_B.value) {
      showToast('对照检索执行完毕，部分请求可能遇到错误', 'warning')
    } else {
      showToast(`对照检索完成，A组命中 ${results.value.length} 条，B组命中 ${results_B.value.length} 条`, 'success')
    }
  }
}

const copyText = async (text: string, message = '已复制') => {
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    showToast(message, 'success')
  } catch {
    showToast('复制失败', 'error')
  }
}

const copyResult = (chunk: RetrievalChunk) => {
  const payload = {
    dataset_ids: datasetIds.value,
    chunk_id: chunk.chunk_id || chunk.id,
    document_name: chunk.document_name || chunk.doc_name,
    score: chunk.similarity ?? chunk.score,
    content: chunk.content || chunk.text
  }
  copyText(JSON.stringify(payload, null, 2), '检索结果关键信息已复制')
}

const formatScore = (chunk: RetrievalChunk) => {
  const score = chunk.similarity ?? chunk.score
  return typeof score === 'number' ? score.toFixed(4) : '-'
}

const highlightContent = (content?: string, q?: string) => {
  const text = content || ''
  if (!text.trim()) return '无内容片段'
  const search = q || ''
  if (!search.trim()) return text

  const keywords = search.trim().split(/[\s,，.。;；!！?？|]+/).filter(x => x.length > 0)
  if (keywords.length === 0) return text

  let highlighted = text
  try {
    keywords.forEach(keyword => {
      if (keyword.length === 1 && !/[\u4e00-\u9fa5]/.test(keyword)) return
      const escaped = keyword.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&')
      const regex = new RegExp(`(?<!<[^>]*)((${escaped}))(?![^<]*>)`, 'gi')
      highlighted = highlighted.replace(regex, '<mark class="bg-amber-100 dark:bg-amber-900/40 text-amber-900 dark:text-amber-100 rounded px-0.5 font-semibold">$1</mark>')
    })
  } catch (e) {
    console.error('Highlight error:', e)
  }
  return highlighted
}

const engineStatusText = computed(() => {
  if (engineStatus.value === 'checking') return '连接中...'
  if (engineStatus.value === 'connected') return '已连接'
  return '未连接'
})

const checkConnectivity = async () => {
  if (!isKnowledgeEnabled.value) {
    engineStatus.value = 'disconnected'
    return
  }
  engineStatus.value = 'checking'
  try {
    await axios.get('/api/portal/ragflow/datasets', { params: { page_size: 1 } })
    engineStatus.value = 'connected'
  } catch {
    engineStatus.value = 'disconnected'
  }
}

const fetchRagFlowConfig = async () => {
  try {
    const response = await axios.get('/api/portal/ragflow/config')
    ragflowConfig.value = response.data?.data || null
    if (isKnowledgeEnabled.value) {
      await checkConnectivity()
    } else {
      engineStatus.value = 'disconnected'
    }
  } catch {
    ragflowConfig.value = null
    engineStatus.value = 'disconnected'
  }
}

onMounted(fetchRagFlowConfig)
</script>

<template>
  <div class="h-full flex flex-col overflow-hidden">
    <div
      v-if="!isKnowledgeEnabled"
      class="bg-amber-50 border border-amber-200 rounded-xl p-4 flex items-start gap-3 shadow-sm mb-4 shrink-0"
    >
      <div class="w-8 h-8 rounded-full bg-amber-100 flex items-center justify-center text-amber-600 border border-amber-200 shrink-0">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
      </div>
      <div>
        <h4 class="text-sm font-bold text-amber-900">知识库功能未开启</h4>
        <p class="text-xs text-amber-700 mt-1">请在系统配置 → 知识库设置中开启「knowledge_base_enabled」后，再进行检索测试。</p>
      </div>
    </div>

    <!-- Header -->
    <div class="flex items-center justify-between pb-4 shrink-0">
      <div>
        <h1 class="text-2xl font-bold text-gray-900">检索测试</h1>
        <p class="text-sm text-gray-500 mt-1">直接调用 RAGFlow Retrieval API，验证知识库 chunk 命中质量。</p>
      </div>
      <div class="flex items-center gap-3">
        <!-- 引擎连接指示器 -->
        <div
          class="flex items-center gap-2 px-3 py-1.5 rounded-xl text-xs border transition-colors group relative cursor-pointer"
          :class="{
            'border-blue-200 bg-blue-50/50 text-blue-700': isKnowledgeEnabled && engineStatus === 'checking',
            'border-emerald-200 bg-emerald-50/50 text-emerald-700': isKnowledgeEnabled && engineStatus === 'connected',
            'border-amber-200 bg-amber-50/50 text-amber-700': !isKnowledgeEnabled || engineStatus === 'disconnected'
          }"
        >
          <span
            class="inline-block w-2 h-2 rounded-full"
            :class="{
              'bg-blue-500 animate-pulse': engineStatus === 'checking',
              'bg-emerald-500': engineStatus === 'connected',
              'bg-amber-500': engineStatus === 'disconnected'
            }"
          ></span>
          <span class="font-medium">引擎 {{ engineStatusText }}</span>

          <!-- 悬浮 Tooltip 提示引擎详细配置 -->
          <span class="absolute top-full right-0 mt-2 hidden group-hover:block bg-slate-900 text-white text-xs p-2.5 rounded-lg shadow-xl z-50 text-left font-sans font-normal pointer-events-none">
            <div class="font-medium mb-1 border-b border-white/10 pb-1">知识库引擎信息</div>
            <div class="opacity-80">地址: {{ ragflowApiUrl }}</div>
            <div class="opacity-80 mt-1">
              API Key: 
              <span v-if="ragflowConfig?.api_key_configured" class="text-emerald-400">已配置</span>
              <span v-else class="text-amber-400">未配置</span>
            </div>
          </span>
        </div>
      </div>
    </div>

    <!-- Left-Right Split -->
    <div class="flex gap-5 flex-1 min-h-0">

      <!-- Left: Search Conditions -->
      <aside class="w-[380px] shrink-0 flex flex-col gap-4">
        <section class="bg-white rounded-2xl border border-gray-200 shadow-sm p-5 flex flex-col flex-1 overflow-y-auto">
        <fieldset
          :disabled="!isEngineReady"
          class="space-y-4 flex flex-col flex-1 min-w-0 border-0 p-0 m-0 disabled:opacity-60"
        >

          <!-- Dataset IDs -->
          <div>
            <label class="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">知识库</label>
            <div class="flex gap-2">
              <input
                :value="datasetIdsText"
                readonly
                :disabled="!isEngineReady"
                class="flex-1 border border-gray-200 rounded-lg px-3 py-2 bg-gray-50 text-xs font-mono truncate disabled:cursor-not-allowed"
                placeholder="请先选择知识库"
              />
              <button
                type="button"
                class="px-3 py-2 rounded-lg bg-primary text-white text-xs font-semibold hover:bg-primary/90 transition-all whitespace-nowrap shrink-0 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-primary"
                :disabled="!isEngineReady"
                @click="showDatasetSelector = true"
              >
                选择
              </button>
            </div>
          </div>

          <!-- Query -->
          <div>
            <label class="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">Query 检索问题</label>
            <textarea
              v-model="query"
              rows="5"
              :disabled="!isEngineReady"
              class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-primary/20 focus:border-primary resize-none disabled:cursor-not-allowed disabled:bg-gray-50"
              placeholder="输入要测试的检索问题..."
            ></textarea>
          </div>

          <!-- Compare Mode Switch -->
          <div class="flex items-center justify-between border-t border-b border-gray-100 py-3 shrink-0 select-none">
            <span class="text-xs font-bold text-gray-700">A/B 对照检索模式</span>
            <label class="relative inline-flex items-center select-none" :class="isEngineReady ? 'cursor-pointer' : 'cursor-not-allowed opacity-50'">
              <input type="checkbox" v-model="isCompareMode" :disabled="!isEngineReady" class="sr-only peer" />
              <div class="w-9 h-5 bg-gray-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-primary"></div>
            </label>
          </div>

          <!-- Parameters Block -->
          <div v-if="!isCompareMode" class="space-y-3">
            <label class="block text-xs font-semibold text-gray-500 uppercase tracking-wider">参数调节</label>
            <div class="grid grid-cols-3 gap-3">
              <div>
                <span class="block text-[10px] text-gray-500 mb-1">top_k</span>
                <input v-model.number="topK" type="number" min="1" max="50" :disabled="!isEngineReady" class="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs disabled:cursor-not-allowed disabled:bg-gray-50" />
              </div>
              <div>
                <span class="block text-[10px] text-gray-500 mb-1">相似度阈值</span>
                <input v-model.number="similarityThreshold" type="number" min="0" max="1" step="0.01" :disabled="!isEngineReady" class="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs disabled:cursor-not-allowed disabled:bg-gray-50" />
              </div>
              <div>
                <span class="block text-[10px] text-gray-500 mb-1">向量权重</span>
                <input v-model.number="vectorSimilarityWeight" type="number" min="0" max="1" step="0.01" :disabled="!isEngineReady" class="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs disabled:cursor-not-allowed disabled:bg-gray-50" />
              </div>
            </div>
          </div>

          <!-- Compare Mode Parameters Panel -->
          <div v-else class="space-y-4">
            <!-- Group A Config Card -->
            <div class="p-3 border border-gray-200 rounded-xl bg-gray-50/20 space-y-2">
              <span class="text-xs font-bold text-gray-800">对照组 A 参数</span>
              <div class="grid grid-cols-3 gap-2">
                <div>
                  <span class="block text-[10px] text-gray-400 mb-1">top_k</span>
                  <input v-model.number="topK" type="number" min="1" max="50" :disabled="!isEngineReady" class="w-full border border-gray-200 rounded-lg px-2 py-1 text-xs disabled:cursor-not-allowed disabled:bg-gray-50" />
                </div>
                <div>
                  <span class="block text-[10px] text-gray-400 mb-1">相似度阈值</span>
                  <input v-model.number="similarityThreshold" type="number" min="0" max="1" step="0.01" :disabled="!isEngineReady" class="w-full border border-gray-200 rounded-lg px-2 py-1 text-xs disabled:cursor-not-allowed disabled:bg-gray-50" />
                </div>
                <div>
                  <span class="block text-[10px] text-gray-400 mb-1">向量权重</span>
                  <input v-model.number="vectorSimilarityWeight" type="number" min="0" max="1" step="0.01" :disabled="!isEngineReady" class="w-full border border-gray-200 rounded-lg px-2 py-1 text-xs disabled:cursor-not-allowed disabled:bg-gray-50" />
                </div>
              </div>
            </div>

            <!-- Group B Config Card -->
            <div class="p-3 border border-gray-200 rounded-xl bg-gray-50/20 space-y-2">
              <span class="text-xs font-bold text-gray-800">对照组 B 参数</span>
              <div class="grid grid-cols-3 gap-2">
                <div>
                  <span class="block text-[10px] text-gray-400 mb-1">top_k</span>
                  <input v-model.number="topK_B" type="number" min="1" max="50" :disabled="!isEngineReady" class="w-full border border-gray-200 rounded-lg px-2 py-1 text-xs disabled:cursor-not-allowed disabled:bg-gray-50" />
                </div>
                <div>
                  <span class="block text-[10px] text-gray-400 mb-1">相似度阈值</span>
                  <input v-model.number="similarityThreshold_B" type="number" min="0" max="1" step="0.01" :disabled="!isEngineReady" class="w-full border border-gray-200 rounded-lg px-2 py-1 text-xs disabled:cursor-not-allowed disabled:bg-gray-50" />
                </div>
                <div>
                  <span class="block text-[10px] text-gray-400 mb-1">向量权重</span>
                  <input v-model.number="vectorSimilarityWeight_B" type="number" min="0" max="1" step="0.01" :disabled="!isEngineReady" class="w-full border border-gray-200 rounded-lg px-2 py-1 text-xs disabled:cursor-not-allowed disabled:bg-gray-50" />
                </div>
              </div>
            </div>
          </div>

          <!-- Execute -->
          <div class="pt-2 mt-auto shrink-0">
            <button
              type="button"
              class="w-full px-5 py-2.5 rounded-xl bg-gray-900 hover:bg-gray-800 text-white text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed transition-all flex items-center justify-center gap-2"
              :disabled="loading || loading_B || !canTest"
              @click="runRetrieval"
            >
              <svg v-if="loading || loading_B" class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
              </svg>
              <span>{{ (loading || loading_B) ? '检索中...' : isCompareMode ? '执行对照检索' : '执行检索' }}</span>
            </button>
            <p class="text-[10px] text-gray-400 mt-2 text-center">检索测试会写入审计日志</p>
          </div>
        </fieldset>
        </section>
      </aside>

      <!-- Right: Results Panel -->
      <section class="flex-1 bg-white rounded-2xl border border-gray-200 shadow-sm flex flex-col min-w-0 overflow-hidden">
        <!-- Results header -->
        <div class="px-5 py-3.5 border-b border-gray-100 flex items-center justify-between shrink-0 select-none">
          <div class="flex items-center gap-2">
            <h2 class="font-semibold text-gray-900">{{ isCompareMode ? '对照检索测试' : '命中结果' }}</h2>
            <span v-if="!isCompareMode && results.length" class="text-xs bg-primary/10 text-primary font-bold px-2 py-0.5 rounded-full">{{ results.length }}</span>
            <span v-else-if="isCompareMode" class="text-xs bg-primary/10 text-primary font-bold px-2 py-0.5 rounded-full">A/B 对照模式</span>
          </div>
          <span class="text-xs text-gray-400">
            {{ isCompareMode ? '双栏并发对比中' : results.length > 0 ? `共 ${results.length} 条命中` : '等待检索' }}
          </span>
        </div>

        <!-- 1. 常规模式列表 -->
        <div v-if="!isCompareMode" class="flex-1 flex flex-col min-h-0 overflow-hidden">
          <!-- Error alert -->
          <div v-if="errorMessage" class="mx-4 mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 shrink-0">
            <div class="font-semibold">RAGFlow 暂时不可用</div>
            <div class="mt-1">{{ friendlyRagFlowError }}</div>
            <div class="mt-2 text-xs text-amber-700">
              配置地址：<a :href="ragflowApiUrl" target="_blank" rel="noopener noreferrer" :title="ragflowApiUrl" class="font-mono hover:underline truncate max-w-[200px] sm:max-w-[300px] inline-block align-bottom">{{ ragflowApiUrl }}</a>
            </div>
            <div class="mt-1 text-xs text-amber-600">原始错误：{{ errorMessage }}</div>
          </div>

          <div class="flex-1 overflow-y-auto">
            <!-- Loading state -->
            <div v-if="loading" class="flex flex-col items-center justify-center h-full gap-3 text-gray-400 select-none">
              <span class="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin"></span>
              <span class="text-sm">正在检索...</span>
            </div>
            <!-- Empty state -->
            <div v-else-if="results.length === 0" class="flex flex-col items-center justify-center h-full gap-3 text-gray-400 select-none">
              <svg class="w-12 h-12 text-gray-200" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
              <span class="text-sm">{{ !isKnowledgeEnabled ? '知识库功能未开启' : engineStatus !== 'connected' ? '知识库引擎未连接，请检查配置' : '在左侧输入条件后执行检索' }}</span>
            </div>
            <!-- Results list -->
            <div v-else class="divide-y divide-gray-100">
              <article v-for="(chunk, idx) in results" :key="chunk.chunk_id || chunk.id" class="p-5 space-y-3 hover:bg-gray-50/50 transition-colors">
                <div class="flex items-start justify-between gap-4">
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-2">
                      <span class="text-xs font-bold text-gray-400 font-mono shrink-0">#{{ idx + 1 }}</span>
                      <h3 class="font-semibold text-gray-900 truncate" :title="chunk.document_name || chunk.doc_name">{{ chunk.document_name || chunk.doc_name || '未知文档' }}</h3>
                    </div>
                    <p class="text-xs text-gray-500 mt-1 font-mono">
                      Chunk: {{ chunk.chunk_id || chunk.id || '-' }} · 相似度: <span class="font-bold" :class="(chunk.similarity ?? chunk.score ?? 0) >= 0.5 ? 'text-emerald-600' : 'text-amber-600'">{{ formatScore(chunk) }}</span>
                    </p>
                  </div>
                  <button class="px-2.5 py-1.5 rounded-lg border border-gray-200 text-xs hover:bg-gray-100 transition-colors shrink-0" @click="copyResult(chunk)">复制</button>
                </div>
                <div
                  class="text-sm text-gray-700 whitespace-pre-wrap bg-gray-50 rounded-xl p-4 leading-relaxed border border-gray-100"
                  v-html="highlightContent(chunk.content || chunk.text, query)"
                ></div>
              </article>
            </div>
          </div>
        </div>

        <!-- 2. A/B 对照模式双栏并排列表 -->
        <div v-else class="flex-1 flex divide-x divide-gray-150 min-h-0 overflow-hidden">
          
          <!-- Column A (Left) -->
          <div class="flex-1 flex flex-col min-w-0 overflow-hidden">
            <!-- Header A -->
            <div class="px-4 py-2 border-b border-gray-100 bg-gray-50/50 flex items-center justify-between shrink-0 select-none">
              <span class="text-xs font-bold text-gray-700">对照组 A (K={{ topK }} · 阈值 {{ similarityThreshold }})</span>
              <span v-if="results.length" class="text-[10px] bg-primary/10 text-primary font-bold px-1.5 py-0.5 rounded-full">{{ results.length }}</span>
            </div>

            <!-- Error A -->
            <div v-if="errorMessage" class="mx-3 mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 shrink-0">
              <div class="font-semibold truncate">A组错误：{{ errorMessage }}</div>
            </div>

            <!-- Body A -->
            <div class="flex-1 overflow-y-auto custom-scrollbar">
              <div v-if="loading" class="flex flex-col items-center justify-center h-full gap-2 text-gray-400 select-none py-10">
                <span class="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin"></span>
                <span class="text-xs">A组正在检索...</span>
              </div>
              <div v-else-if="results.length === 0" class="flex flex-col items-center justify-center h-full gap-2 text-gray-400 select-none py-10">
                <span class="text-xs">A组暂无召回数据</span>
              </div>
              <div v-else class="divide-y divide-gray-100">
                <article v-for="(chunk, idx) in results" :key="'a-' + (chunk.chunk_id || chunk.id)" class="p-4 space-y-2 hover:bg-gray-50/50 transition-colors">
                  <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0 flex-1">
                      <div class="flex items-center gap-1.5">
                        <span class="text-[10px] font-bold text-gray-400 font-mono shrink-0">#{{ idx + 1 }}</span>
                        <h4 class="text-xs font-bold text-gray-800 truncate" :title="chunk.document_name || chunk.doc_name">{{ chunk.document_name || chunk.doc_name || '未知文档' }}</h4>
                      </div>
                      <p class="text-[10px] text-gray-400 font-mono mt-0.5 truncate">
                        相似度: <span class="font-bold text-emerald-600">{{ formatScore(chunk) }}</span>
                      </p>
                    </div>
                    <button class="px-2 py-1 rounded border border-gray-200 text-[10px] hover:bg-gray-100 transition-colors shrink-0" @click="copyResult(chunk)">复制</button>
                  </div>
                  <div
                    class="text-xs text-gray-700 whitespace-pre-wrap bg-gray-50 rounded-xl p-3 leading-relaxed border border-gray-100"
                    v-html="highlightContent(chunk.content || chunk.text, query)"
                  ></div>
                </article>
              </div>
            </div>
          </div>

          <!-- Column B (Right) -->
          <div class="flex-1 flex flex-col min-w-0 overflow-hidden">
            <!-- Header B -->
            <div class="px-4 py-2 border-b border-gray-100 bg-gray-50/50 flex items-center justify-between shrink-0 select-none">
              <span class="text-xs font-bold text-gray-700">对照组 B (K={{ topK_B }} · 阈值 {{ similarityThreshold_B }})</span>
              <span v-if="results_B.length" class="text-[10px] bg-primary/10 text-primary font-bold px-1.5 py-0.5 rounded-full">{{ results_B.length }}</span>
            </div>

            <!-- Error B -->
            <div v-if="friendlyRagFlowErrorB" class="mx-3 mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 shrink-0">
              <div class="font-semibold truncate">B组错误：{{ friendlyRagFlowErrorB }}</div>
            </div>

            <!-- Body B -->
            <div class="flex-1 overflow-y-auto custom-scrollbar">
              <div v-if="loading_B" class="flex flex-col items-center justify-center h-full gap-2 text-gray-400 select-none py-10">
                <span class="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin"></span>
                <span class="text-xs">B组正在检索...</span>
              </div>
              <div v-else-if="results_B.length === 0" class="flex flex-col items-center justify-center h-full gap-2 text-gray-400 select-none py-10">
                <span class="text-xs">B组暂无召回数据</span>
              </div>
              <div v-else class="divide-y divide-gray-100">
                <article v-for="(chunk, idx) in results_B" :key="'b-' + (chunk.chunk_id || chunk.id)" class="p-4 space-y-2 hover:bg-gray-50/50 transition-colors">
                  <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0 flex-1">
                      <div class="flex items-center gap-1.5">
                        <span class="text-[10px] font-bold text-gray-400 font-mono shrink-0">#{{ idx + 1 }}</span>
                        <h4 class="text-xs font-bold text-gray-800 truncate" :title="chunk.document_name || chunk.doc_name">{{ chunk.document_name || chunk.doc_name || '未知文档' }}</h4>
                      </div>
                      <p class="text-[10px] text-gray-400 font-mono mt-0.5 truncate">
                        相似度: <span class="font-bold text-emerald-600">{{ formatScore(chunk) }}</span>
                      </p>
                    </div>
                    <button class="px-2 py-1 rounded border border-gray-200 text-[10px] hover:bg-gray-100 transition-colors shrink-0" @click="copyResult(chunk)">复制</button>
                  </div>
                  <div
                    class="text-xs text-gray-700 whitespace-pre-wrap bg-gray-50 rounded-xl p-3 leading-relaxed border border-gray-100"
                    v-html="highlightContent(chunk.content || chunk.text, query)"
                  ></div>
                </article>
              </div>
            </div>
          </div>

        </div>
      </section>
    </div>

    <RagFlowResourceSelector
      v-if="isKnowledgeEnabled"
      v-model="showDatasetSelector"
      type="dataset"
      :initial-selected="datasetIds"
      @select="handleDatasetSelect"
    />
  </div>
</template>

<style scoped>
.whitespace-pre-wrap :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 10px 0;
  font-size: 11px;
  line-height: 1.5;
  background-color: #ffffff;
}

.dark .whitespace-pre-wrap :deep(table) {
  background-color: #1f2937;
}

.whitespace-pre-wrap :deep(th),
.whitespace-pre-wrap :deep(td) {
  border: 1px solid #e5e7eb;
  padding: 6px 8px;
  text-align: left;
  word-break: break-all;
}

.dark .whitespace-pre-wrap :deep(th),
.dark .whitespace-pre-wrap :deep(td) {
  border-color: #374151;
}

.whitespace-pre-wrap :deep(th) {
  background-color: #f3f4f6;
  font-weight: 700;
  color: #1f2937;
}

.dark .whitespace-pre-wrap :deep(th) {
  background-color: #374151;
  color: #f9fafb;
}

.whitespace-pre-wrap :deep(tr:nth-child(even)) {
  background-color: #f9fafb;
}

.dark .whitespace-pre-wrap :deep(tr:nth-child(even)) {
  background-color: rgba(31, 41, 55, 0.4);
}

.whitespace-pre-wrap :deep(caption) {
  font-size: 10px;
  color: #6b7280;
  padding: 6px 4px;
  font-weight: 700;
  text-align: left;
  background-color: rgba(243, 244, 246, 0.5);
  border-bottom: 2px solid #e5e7eb;
}

.dark .whitespace-pre-wrap :deep(caption) {
  color: #9ca3af;
  background-color: rgba(55, 65, 81, 0.5);
  border-color: #4b5563;
}
</style>
