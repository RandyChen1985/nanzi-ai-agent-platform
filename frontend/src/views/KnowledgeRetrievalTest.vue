<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import axios from '../utils/axios'
import RagFlowResourceSelector from '../components/RagFlowResourceSelector.vue'
import { useToast } from '../composables/useToast'
import { useUser } from '../composables/useUser'
import { copyToClipboard } from '../utils/clipboard'

type RetrievalChunk = {
  id?: string
  chunk_id?: string
  // 后端归一化后提供的是 doc_id（不是 document_id）
  doc_id?: string
  document_name?: string
  doc_name?: string
  similarity?: number
  score?: number
  content?: string
  text?: string
  // 多知识库检索时用于标识命中来源
  dataset_id?: string
  page_no?: number
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
const datasetNameById = ref<Record<string, string>>({})
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
const executedQuery = ref('')
const executedTopK = ref(5)
const executedThreshold = ref(0.2)
const executedTopK_B = ref(8)
const executedThreshold_B = ref(0.2)
const hasSearched = ref(false)
const errorMessage = ref('')
const errorMessage_B = ref('')

const ragflowConfig = ref<RagFlowConfigSummary | null>(null)
const engineStatus = ref<'checking' | 'connected' | 'disconnected'>('checking')

const isKnowledgeEnabled = computed(() => ragflowConfig.value?.knowledge_base_enabled !== false)
const isEngineReady = computed(() => isKnowledgeEnabled.value && engineStatus.value === 'connected')
const canTest = computed(() => hasPermission('element:knowledge:test_retrieval') && isEngineReady.value)
const datasetDisplayItems = computed(() => {
  const limit = datasetIds.value.length > 3 ? 2 : 3
  return datasetIds.value.slice(0, limit).map((id) => ({
    id,
    name: datasetNameById.value[id] || id,
  }))
})
const hiddenDatasetCount = computed(() => Math.max(0, datasetIds.value.length - datasetDisplayItems.value.length))
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
  const data = anyErr?.response?.data
  // 后端的参数校验失败会把 Python repr 放进 detail、把可读文案放进 message，
  // 因此优先取 message；非字符串的 detail 一律忽略，避免把 repr 或 [object Object] 甩给用户
  const message = typeof data?.message === 'string' ? data.message : ''
  const detail = typeof data?.detail === 'string' ? data.detail : ''
  return message || detail || anyErr?.message || '检索失败'
}

// 错误可能来自权限不足、参数校验等，只有确实命中 RAGFlow 关键字时才归因于连通性
const errorTitle = computed(() => {
  const lower = (errorMessage.value || '').toLowerCase()
  if (
    lower.includes('ragflow') ||
    lower.includes('bad gateway') ||
    lower.includes('failed to connect') ||
    lower.includes('configuration missing')
  ) {
    return 'RAGFlow 服务连通性故障'
  }
  return '检索请求失败'
})

const handleDatasetSelect = (value: string | string[]) => {
  datasetIds.value = Array.isArray(value) ? value : [value].filter(Boolean)
}

const handleDatasetDetailsSelect = (details: Array<{ id: string; name: string }>) => {
  const next = { ...datasetNameById.value }
  for (const item of details) next[item.id] = item.name
  datasetNameById.value = next
}

const removeDataset = (id: string) => {
  datasetIds.value = datasetIds.value.filter((item) => item !== id)
}

// 与后端 Field(ge=0, le=1) 对齐；v-model.number 清空输入框会得到空串，Number.isFinite 可一并拦下
const isValidRatio = (value: number) => Number.isFinite(value) && value >= 0 && value <= 1

// 在途检索可被取消；组件卸载后也不再向已离开的页面弹全局 toast
let retrievalAbort: AbortController | null = null
let isUnmounted = false

const notify = (message: string, type: 'success' | 'error' | 'warning' = 'success') => {
  if (!isUnmounted) showToast(message, type)
}

const cancelRetrieval = () => {
  retrievalAbort?.abort()
  retrievalAbort = null
  loading.value = false
  loading_B.value = false
}

onUnmounted(() => {
  isUnmounted = true
  retrievalAbort?.abort()
})

const validate = () => {
  if (datasetIds.value.length === 0) {
    showToast('请至少选择一个知识库', 'warning')
    return false
  }
  if (!query.value.trim()) {
    showToast('请输入检索问题', 'warning')
    return false
  }
  if (!Number.isInteger(topK.value) || topK.value < 1 || topK.value > 50) {
    showToast('A组 top_k 需为 1 到 50 之间的整数', 'warning')
    return false
  }
  if (!isValidRatio(similarityThreshold.value)) {
    showToast('A组相似度阈值需在 0 到 1 之间', 'warning')
    return false
  }
  if (!isValidRatio(vectorSimilarityWeight.value)) {
    showToast('A组向量相似度权重需在 0 到 1 之间', 'warning')
    return false
  }
  return true
}

const validateB = () => {
  if (!Number.isInteger(topK_B.value) || topK_B.value < 1 || topK_B.value > 50) {
    showToast('B组 top_k 需为 1 到 50 之间的整数', 'warning')
    return false
  }
  if (!isValidRatio(similarityThreshold_B.value)) {
    showToast('B组相似度阈值需在 0 到 1 之间', 'warning')
    return false
  }
  if (!isValidRatio(vectorSimilarityWeight_B.value)) {
    showToast('B组向量相似度权重需在 0 到 1 之间', 'warning')
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

  // 中止上一次仍在途的检索，避免慢响应落地覆盖本次结果
  retrievalAbort?.abort()
  const controller = new AbortController()
  retrievalAbort = controller

  // 记录本次真正提交的参数：否则用户改动输入框后，已有结果的表头与关键词高亮会跟着变
  executedQuery.value = query.value.trim()
  executedTopK.value = topK.value
  executedThreshold.value = similarityThreshold.value
  executedTopK_B.value = topK_B.value
  executedThreshold_B.value = similarityThreshold_B.value
  hasSearched.value = true

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
      }, { signal: controller.signal })
      results.value = response.data?.data || []
      notify(`检索完成，命中 ${results.value.length} 条`, 'success')
    } catch (err) {
      errorMessage.value = extractError(err)
      notify(errorMessage.value, 'error')
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
    }, { signal: controller.signal }).then(response => {
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
    }, { signal: controller.signal }).then(response => {
      results_B.value = response.data?.data || []
    }).catch(err => {
      errorMessage_B.value = extractError(err)
    }).finally(() => {
      loading_B.value = false
    })

    await Promise.all([promiseA, promiseB])
    if (errorMessage.value || errorMessage_B.value) {
      notify('对照检索执行完毕，部分请求可能遇到错误', 'warning')
    } else {
      notify(`对照检索完成，A组命中 ${results.value.length} 条，B组命中 ${results_B.value.length} 条`, 'success')
    }
  }
}

const copyText = async (text: string, message = '已复制') => {
  if (!text) return
  const ok = await copyToClipboard(text)
  if (ok) {
    showToast(message, 'success')
  } else {
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

// 相似度是召回测试的首要判读信号，A/B 两栏必须同一套配色
const scoreClass = (chunk: RetrievalChunk) =>
  (chunk.similarity ?? chunk.score ?? 0) >= 0.5 ? 'text-emerald-600' : 'text-amber-600'

const formatScore = (chunk: RetrievalChunk) => {
  const score = chunk.similarity ?? chunk.score
  return typeof score === 'number' ? score.toFixed(4) : '-'
}

// RAGFlow 返回的分块正文可能带 HTML（表格等），本页又用 v-html 渲染，
// 因此按白名单清洗：只保留排版类标签，移除脚本标签与所有事件属性
const ALLOWED_HTML_TAGS = new Set([
  'P', 'BR', 'DIV', 'SPAN', 'STRONG', 'B', 'EM', 'I', 'U', 'S', 'SUB', 'SUP',
  'CODE', 'PRE', 'BLOCKQUOTE', 'MARK', 'HR', 'UL', 'OL', 'LI', 'DL', 'DT', 'DD',
  'H1', 'H2', 'H3', 'H4', 'H5', 'H6',
  'TABLE', 'THEAD', 'TBODY', 'TFOOT', 'TR', 'TH', 'TD', 'CAPTION', 'COLGROUP', 'COL'
])

const sanitizeHtml = (html: string) => {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  const clean = (parent: Element) => {
    for (const child of Array.from(parent.children)) {
      if (!ALLOWED_HTML_TAGS.has(child.tagName)) {
        // 不在白名单：拆掉标签本身、保留其内容，并继续清洗被提升上来的节点
        const promoted = Array.from(child.childNodes)
        child.replaceWith(...promoted)
        for (const node of promoted) {
          if (node.nodeType === 1) clean(node as Element)
        }
        continue
      }
      for (const attr of Array.from(child.attributes)) {
        const name = attr.name.toLowerCase()
        const value = attr.value.trim().toLowerCase()
        const isDangerousUrl = (name === 'href' || name === 'src' || name === 'xlink:href') &&
          (value.startsWith('javascript:') || value.startsWith('data:'))
        if (name.startsWith('on') || name === 'style' || name === 'srcdoc' || isDangerousUrl) {
          child.removeAttribute(attr.name)
        }
      }
      clean(child)
    }
  }
  clean(doc.body)
  return doc.body.innerHTML
}

const highlightContent = (content?: string, q?: string) => {
  const text = content || ''
  if (!text.trim()) return '无内容片段'
  const search = q || ''
  if (!search.trim()) return sanitizeHtml(text)

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
  return sanitizeHtml(highlighted)
}

const connectivityError = ref('')

// 后端把缺失的文档名兜底成英文 "Unknown Document"，直接展示会中英混排
const displayDocName = (chunk: any) => {
  const name = chunk?.document_name || chunk?.doc_name
  if (!name || name === 'Unknown Document') return '未知文档'
  return name
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
  connectivityError.value = ''
  try {
    await axios.get('/api/portal/ragflow/datasets', { params: { page_size: 1 } })
    engineStatus.value = 'connected'
  } catch (err) {
    // 此前静默吞掉原因，用户只看到「未连接」，无法判断是超时、网关还是 API Key 未配置
    engineStatus.value = 'disconnected'
    connectivityError.value = extractError(err)
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
          :title="connectivityError ? `点击重新检测（上次失败：${connectivityError}）` : '点击重新检测连通性'"
          @click="checkConnectivity"
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
    <div class="flex flex-col lg:flex-row gap-5 flex-1 min-h-0">

      <!-- Left: Search Conditions -->
      <aside class="w-full lg:w-[380px] shrink-0 flex flex-col gap-4">
        <section class="bg-white rounded-2xl border border-gray-200 shadow-sm p-5 flex flex-col flex-1 overflow-y-auto">
        <fieldset
          :disabled="!isEngineReady || loading || loading_B"
          class="space-y-4 flex flex-col flex-1 min-w-0 border-0 p-0 m-0 disabled:opacity-60"
        >

          <!-- Dataset names, while requests continue using Dataset IDs -->
          <div>
            <label class="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">知识库</label>
            <div class="flex gap-2">
              <div
                class="flex-1 min-h-[42px] max-h-[88px] overflow-y-auto border border-gray-200 rounded-lg px-2.5 py-2 bg-gray-50 flex flex-wrap content-start gap-1.5 disabled:cursor-not-allowed"
                :class="!isEngineReady ? 'opacity-60' : ''"
                :title="datasetIds.length ? datasetIds.map((id) => datasetNameById[id] || id).join('、') : '请先选择知识库'"
              >
                <template v-if="datasetDisplayItems.length">
                  <span
                    v-for="item in datasetDisplayItems"
                    :key="item.id"
                    class="inline-flex max-w-full items-center gap-1 rounded-md border border-indigo-200 bg-indigo-50 px-2 py-1 text-xs text-indigo-700"
                  >
                    <span class="max-w-[170px] truncate">{{ item.name }}</span>
                    <button type="button" class="shrink-0 text-indigo-400 hover:text-indigo-700" :aria-label="`移除${item.name}`" :disabled="!isEngineReady" @click="removeDataset(item.id)">×</button>
                  </span>
                  <button
                    v-if="hiddenDatasetCount > 0"
                    type="button"
                    class="rounded-md border border-gray-200 bg-white px-2 py-1 text-xs text-gray-500 hover:text-gray-800"
                    @click="showDatasetSelector = true"
                  >
                    还有 {{ hiddenDatasetCount }} 个
                  </button>
                </template>
                <span v-else class="self-center px-1 text-xs text-gray-400">请先选择知识库</span>
              </div>
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
            <label for="krt-query" class="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5">Query 检索问题</label>
            <textarea
              id="krt-query"
              v-model="query"
              @keyup.enter.ctrl="runRetrieval"
              rows="5"
              :disabled="!isEngineReady"
              class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-primary/20 focus:border-primary resize-none disabled:cursor-not-allowed disabled:bg-gray-50"
              placeholder="输入要测试的检索问题（Ctrl + Enter 直接执行）..."
            ></textarea>
          </div>

          <!-- Compare Mode Switch -->
          <div class="flex items-center justify-between border-t border-b border-gray-100 py-3 shrink-0 select-none">
            <span class="text-xs font-bold text-gray-700">A/B 对照检索模式</span>
            <label class="relative inline-flex items-center select-none" :class="isEngineReady ? 'cursor-pointer' : 'cursor-not-allowed opacity-50'">
              <input type="checkbox" v-model="isCompareMode" :disabled="!isEngineReady" class="sr-only peer" aria-label="A/B 对照检索模式" />
              <div class="w-9 h-5 bg-gray-200 peer-focus-visible:ring-2 peer-focus-visible:ring-primary/40 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-primary"></div>
            </label>
          </div>

          <!-- Parameters Block -->
          <div v-if="!isCompareMode" class="space-y-3">
            <label class="block text-xs font-semibold text-gray-500 uppercase tracking-wider">参数调节</label>
            <div class="grid grid-cols-3 gap-3">
              <div>
                <span class="block text-[10px] text-gray-500 mb-1">top_k</span>
                <input v-model.number="topK" type="number" min="1" max="50" :disabled="!isEngineReady" aria-label="A组 top_k（1-50 整数）" class="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs focus:ring-2 focus:ring-primary/20 focus:border-primary disabled:cursor-not-allowed disabled:bg-gray-50" />
              </div>
              <div>
                <span class="block text-[10px] text-gray-500 mb-1">相似度阈值</span>
                <input v-model.number="similarityThreshold" type="number" min="0" max="1" step="0.01" :disabled="!isEngineReady" aria-label="A组相似度阈值（0-1）" class="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs focus:ring-2 focus:ring-primary/20 focus:border-primary disabled:cursor-not-allowed disabled:bg-gray-50" />
              </div>
              <div>
                <span class="block text-[10px] text-gray-500 mb-1">向量权重</span>
                <input v-model.number="vectorSimilarityWeight" type="number" min="0" max="1" step="0.01" :disabled="!isEngineReady" aria-label="A组向量相似度权重（0-1）" class="w-full border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs focus:ring-2 focus:ring-primary/20 focus:border-primary disabled:cursor-not-allowed disabled:bg-gray-50" />
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
            <p v-if="!hasPermission('element:knowledge:test_retrieval')" class="text-[11px] text-amber-600 mb-2 text-center">
              当前账号无「召回测试」权限，无法执行检索，请联系管理员开通
            </p>
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
            <button
              v-if="loading || loading_B"
              type="button"
              class="w-full mt-2 px-5 py-2 rounded-xl border border-gray-200 text-gray-600 text-sm font-semibold hover:bg-gray-50 transition-all"
              @click="cancelRetrieval"
            >
              取消检索
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
          <div v-if="errorMessage" role="alert" class="mx-4 mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 shrink-0">
            <div class="font-semibold">{{ errorTitle }}</div>
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
            <!-- Failure state：与上方错误横幅互斥，避免同时出现「检索失败」和「暂无数据」 -->
            <div v-else-if="errorMessage" class="flex flex-col items-center justify-center h-full gap-3 text-amber-600 select-none">
              <span class="text-sm">本次检索未返回结果，请查看上方错误详情后重试</span>
            </div>
            <!-- Empty state -->
            <div v-else-if="results.length === 0" class="flex flex-col items-center justify-center h-full gap-3 text-gray-400 select-none">
              <svg class="w-12 h-12 text-gray-200" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
              <span class="text-sm">{{ !isKnowledgeEnabled ? '知识库功能未开启' : engineStatus !== 'connected' ? '知识库引擎未连接，请检查配置' : hasSearched ? '本次检索未命中任何分块' : '在左侧输入条件后执行检索' }}</span>
              <span v-if="isEngineReady && hasSearched" class="text-xs text-gray-400">可尝试提高 TopK 或降低相似度阈值</span>
            </div>
            <!-- Results list -->
            <div v-else class="divide-y divide-gray-100" aria-live="polite">
              <article v-for="(chunk, idx) in results" :key="chunk.chunk_id || chunk.id" class="p-5 space-y-3 hover:bg-gray-50/50 transition-colors">
                <div class="flex items-start justify-between gap-4">
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-2">
                      <span class="text-xs font-bold text-gray-400 font-mono shrink-0">#{{ idx + 1 }}</span>
                      <h3 class="font-semibold text-gray-900 truncate" :title="displayDocName(chunk)">{{ displayDocName(chunk) }}</h3>
                    </div>
                    <p class="text-xs text-gray-500 mt-1 font-mono">
                      Chunk: {{ chunk.chunk_id || chunk.id || '-' }} · 相似度: <span class="font-bold" :class="scoreClass(chunk)">{{ formatScore(chunk) }}</span>
                      <span v-if="chunk.dataset_id && datasetNameById[chunk.dataset_id]" class="ml-1.5 text-[10px] px-1.5 py-0.5 rounded bg-blue-50 text-blue-700">{{ datasetNameById[chunk.dataset_id] }}</span>
                      <span v-if="chunk.page_no" class="ml-1 text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-600">第 {{ chunk.page_no }} 页</span>
                    </p>
                  </div>
                  <button class="px-2.5 py-1.5 rounded-lg border border-gray-200 text-xs hover:bg-gray-100 transition-colors shrink-0" @click="copyResult(chunk)">复制</button>
                </div>
                <div
                  class="text-sm text-gray-700 whitespace-pre-wrap bg-gray-50 rounded-xl p-4 leading-relaxed border border-gray-100"
                  v-html="highlightContent(chunk.content || chunk.text, executedQuery)"
                ></div>
              </article>
            </div>
          </div>
        </div>

        <!-- 2. A/B 对照模式双栏并排列表 -->
        <div v-else class="flex-1 flex divide-x divide-gray-200 min-h-0 overflow-hidden">
          
          <!-- Column A (Left) -->
          <div class="flex-1 flex flex-col min-w-0 overflow-hidden">
            <!-- Header A -->
            <div class="px-4 py-2 border-b border-gray-100 bg-gray-50/50 flex items-center justify-between shrink-0 select-none">
              <span class="text-xs font-bold text-gray-700">对照组 A (K={{ executedTopK }} · 阈值 {{ executedThreshold }})</span>
              <span v-if="results.length" class="text-[10px] bg-primary/10 text-primary font-bold px-1.5 py-0.5 rounded-full">{{ results.length }}</span>
            </div>

            <!-- Error A -->
            <div v-if="errorMessage" class="mx-3 mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 shrink-0">
              <div class="font-semibold truncate">A组错误：{{ friendlyRagFlowError }}</div>
            </div>

            <!-- Body A -->
            <div class="flex-1 overflow-y-auto custom-scrollbar">
              <div v-if="loading" class="flex flex-col items-center justify-center h-full gap-2 text-gray-400 select-none py-10">
                <span class="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin"></span>
                <span class="text-xs">A组正在检索...</span>
              </div>
              <div v-else-if="errorMessage" class="flex flex-col items-center justify-center h-full gap-2 text-amber-600 select-none py-10">
                <span class="text-xs">A组检索失败，请查看上方错误详情后重试</span>
              </div>
              <div v-else-if="results.length === 0" class="flex flex-col items-center justify-center h-full gap-2 text-gray-400 select-none py-10">
                <span class="text-xs">A组未召回任何分块</span>
                <span class="text-[10px] text-gray-400">可尝试提高 TopK 或降低相似度阈值</span>
              </div>
              <div v-else class="divide-y divide-gray-100">
                <article v-for="(chunk, idx) in results" :key="'a-' + (chunk.chunk_id || chunk.id)" class="p-4 space-y-2 hover:bg-gray-50/50 transition-colors">
                  <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0 flex-1">
                      <div class="flex items-center gap-1.5">
                        <span class="text-[10px] font-bold text-gray-400 font-mono shrink-0">#{{ idx + 1 }}</span>
                        <h4 class="text-xs font-bold text-gray-800 truncate" :title="displayDocName(chunk)">{{ displayDocName(chunk) }}</h4>
                      </div>
                      <p class="text-[10px] text-gray-400 font-mono mt-0.5 truncate">
                        相似度: <span class="font-bold" :class="scoreClass(chunk)">{{ formatScore(chunk) }}</span>
                      </p>
                    </div>
                    <button class="px-2 py-1 rounded border border-gray-200 text-[10px] hover:bg-gray-100 transition-colors shrink-0" @click="copyResult(chunk)">复制</button>
                  </div>
                  <div
                    class="text-xs text-gray-700 whitespace-pre-wrap bg-gray-50 rounded-xl p-3 leading-relaxed border border-gray-100"
                    v-html="highlightContent(chunk.content || chunk.text, executedQuery)"
                  ></div>
                </article>
              </div>
            </div>
          </div>

          <!-- Column B (Right) -->
          <div class="flex-1 flex flex-col min-w-0 overflow-hidden">
            <!-- Header B -->
            <div class="px-4 py-2 border-b border-gray-100 bg-gray-50/50 flex items-center justify-between shrink-0 select-none">
              <span class="text-xs font-bold text-gray-700">对照组 B (K={{ executedTopK_B }} · 阈值 {{ executedThreshold_B }})</span>
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
              <div v-else-if="errorMessage_B" class="flex flex-col items-center justify-center h-full gap-2 text-amber-600 select-none py-10">
                <span class="text-xs">B组检索失败，请查看上方错误详情后重试</span>
              </div>
              <div v-else-if="results_B.length === 0" class="flex flex-col items-center justify-center h-full gap-2 text-gray-400 select-none py-10">
                <span class="text-xs">B组未召回任何分块</span>
                <span class="text-[10px] text-gray-400">可尝试提高 TopK 或降低相似度阈值</span>
              </div>
              <div v-else class="divide-y divide-gray-100">
                <article v-for="(chunk, idx) in results_B" :key="'b-' + (chunk.chunk_id || chunk.id)" class="p-4 space-y-2 hover:bg-gray-50/50 transition-colors">
                  <div class="flex items-start justify-between gap-3">
                    <div class="min-w-0 flex-1">
                      <div class="flex items-center gap-1.5">
                        <span class="text-[10px] font-bold text-gray-400 font-mono shrink-0">#{{ idx + 1 }}</span>
                        <h4 class="text-xs font-bold text-gray-800 truncate" :title="displayDocName(chunk)">{{ displayDocName(chunk) }}</h4>
                      </div>
                      <p class="text-[10px] text-gray-400 font-mono mt-0.5 truncate">
                        相似度: <span class="font-bold" :class="scoreClass(chunk)">{{ formatScore(chunk) }}</span>
                      </p>
                    </div>
                    <button class="px-2 py-1 rounded border border-gray-200 text-[10px] hover:bg-gray-100 transition-colors shrink-0" @click="copyResult(chunk)">复制</button>
                  </div>
                  <div
                    class="text-xs text-gray-700 whitespace-pre-wrap bg-gray-50 rounded-xl p-3 leading-relaxed border border-gray-100"
                    v-html="highlightContent(chunk.content || chunk.text, executedQuery)"
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
      @select-details="handleDatasetDetailsSelect"
    />
  </div>
</template>

<style scoped>
/* 结果区滚动条样式；此前引用了其它组件 scoped 里的同名类，实际不生效 */
.custom-scrollbar::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 3px;
}
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}

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
