<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import axios from '../utils/axios'
import Modal from '../components/Modal.vue'
import ConfirmModal from '../components/ConfirmModal.vue'
import { useToast } from '../composables/useToast'
import { useUser } from '../composables/useUser'

type KnowledgeBase = {
  id: string
  ragflow_dataset_id?: string
  name?: string
  description?: string
  platform_name?: string
  platform_description?: string
  owner?: string
  visibility?: string
  tags?: string[]
  notes?: string
  doc_count?: number
  document_count?: number
  chunk_count?: number
  update_time?: string
  updated_at?: string
  status?: string
  is_missing_in_ragflow?: boolean
  created_by?: string
  can_write?: boolean
  can_view_chunks?: boolean
  is_read_only?: boolean
  local_metadata?: {
    extra_config?: Record<string, unknown>
  }
}

type KnowledgeDocument = {
  id: string
  name?: string
  status?: string
  run?: string | number
  chunk_count?: number
  size?: number
  update_time?: string
  created_at?: string
}

type RagFlowConfigSummary = {
  api_url: string
  api_key_configured: boolean
  configured: boolean
}

const { showToast } = useToast()
const { hasPermission } = useUser()

const loading = ref(false)
const syncing = ref(false)
const engineStatus = ref<'checking' | 'connected' | 'disconnected'>('checking')
const showErrorBanner = ref(true)
const datasets = ref<KnowledgeBase[]>([])
const errorMessage = ref('')
const ragflowConfig = ref<RagFlowConfigSummary | null>(null)

// 选中状态
const selectedType = ref<'dataset' | 'document' | null>(null)
const selectedId = ref<string>('')
const selectedDataset = ref<KnowledgeBase | null>(null)
const selectedDocument = ref<KnowledgeDocument | null>(null)

// 树状展开和数据加载状态
const expandedDatasetIds = ref<Record<string, boolean>>({})
const documentsMap = ref<Record<string, KnowledgeDocument[]>>({})
const documentsLoadingMap = ref<Record<string, boolean>>({})

// 检索过滤
const searchQuery = ref('')

const showCreateModal = ref(false)
const showEditModal = ref(false)
const deletingDataset = ref<KnowledgeBase | null>(null)
const deletingDocument = ref<KnowledgeDocument | null>(null)
const uploadFile = ref<File | null>(null)

const form = ref({
  name: '',
  description: '',
  chunk_method: 'naive',
  owner: '',
  visibility: 'private',
  tagsText: '',
  notes: '',
  extraConfigText: '{}'
})

const canCreate = computed(() => hasPermission('element:knowledge:create'))
const canEdit = computed(() => hasPermission('element:knowledge:edit'))
const canDelete = computed(() => hasPermission('element:knowledge:delete'))
const canUpload = computed(() => hasPermission('element:knowledge:upload_document'))
const canDeleteDocument = computed(() => hasPermission('element:knowledge:delete_document'))
const canParse = computed(() => hasPermission('element:knowledge:parse_document'))
const canSync = computed(() => canEdit.value)
const isEngineReady = computed(() => engineStatus.value === 'connected' && !loading.value)
const engineStatusText = computed(() => {
  if (engineStatus.value === 'checking') return '知识库引擎连接中 ...'
  if (engineStatus.value === 'connected') return '已连接'
  return '未连接'
})

const selectedDatasetId = computed(() => selectedDataset.value?.ragflow_dataset_id || selectedDataset.value?.id || '')

const chunkMethodDescription = computed(() => {
  const method = form.value.chunk_method
  switch (method) {
    case 'naive':
      return '💡 通用切分：适合绝大部分规则文档。基于段落与 Token 长度限制进行物理切分，简单高效。'
    case 'manual':
      return '💡 手动微调：适合对分块有严苛要求的文件。支持在解析后由人工手动调整切分边界。'
    case 'qa':
      return '💡 问答对模型：适合 FAQ 常见问答库、客服指引。自动调用大模型从文档中提取匹配的 Q&A 问答对。'
    case 'table':
      return '💡 表格切片：适合报表、带有密集表格的 PDF 或 Excel。专门优化并保留表格的行/列逻辑特征。'
    case 'paper':
      return '💡 学术论文：针对论文报告结构设计。自动识别标题和段落，智能排除参考文献等噪音。'
    case 'laws':
      return '💡 法律法规：适合条规文件。精准按“第X条、第X款”进行层级解析切片。'
    case 'resume':
      return '💡 简历解析：专门解析个人履历。智能识别并提取出教育背景、工作经历等核心属性。'
    case 'book':
      return '💡 图书解析：针对长篇连贯文学作品。按大篇幅连续小说段落逻辑进行切片。'
    case 'one':
      return '💡 单文件不切分：将整个文档作为一个 Chunk。适合短语列表、特定规则名称等无需分割的文件。'
    default:
      return ''
  }
})

const extractError = (err: unknown) => {
  const anyErr = err as any
  return anyErr?.response?.data?.detail || anyErr?.response?.data?.message || anyErr?.message || '操作失败'
}

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
}
)

const ragflowApiUrl = computed(() => ragflowConfig.value?.api_url || '未配置')
const apiData = (response: any) => response?.data?.data ?? response?.data ?? []
const normalizeTags = (text: string) => text.split(',').map(t => t.trim()).filter(Boolean)

const parseExtraConfig = () => {
  if (!form.value.extraConfigText.trim()) return {}
  try {
    return JSON.parse(form.value.extraConfigText)
  } catch {
    throw new Error('扩展配置必须是合法 JSON')
  }
}

const resetForm = () => {
  form.value = {
    name: '',
    description: '',
    chunk_method: 'naive',
    owner: '',
    visibility: 'private',
    tagsText: '',
    notes: '',
    extraConfigText: '{}'
  }
}

const openCreate = () => {
  if (!isEngineReady.value) {
    showToast('知识库引擎未连接，暂不能创建知识库', 'warning')
    return
  }
  resetForm()
  showCreateModal.value = true
}

const openEdit = (dataset: KnowledgeBase) => {
  if (!isEngineReady.value) {
    showToast('知识库引擎未连接，暂不能编辑知识库', 'warning')
    return
  }
  selectedDataset.value = dataset
  form.value = {
    name: dataset.platform_name || dataset.name || '',
    description: dataset.platform_description || dataset.description || '',
    chunk_method: 'naive',
    owner: dataset.owner || '',
    visibility: dataset.visibility || 'private',
    tagsText: (dataset.tags || []).join(', '),
    notes: dataset.notes || '',
    extraConfigText: JSON.stringify(dataset.local_metadata?.extra_config || {}, null, 2)
  }
  showEditModal.value = true
}

// 获取知识库列表
const fetchDatasets = async () => {
  loading.value = true
  engineStatus.value = 'checking'
  errorMessage.value = ''
  try {
    const response = await axios.get('/api/portal/ragflow/datasets', { params: { page_size: 200 } })
    datasets.value = apiData(response)
    engineStatus.value = 'connected'
    
    // 如果没有选中的节点，默认选中第一个
    if (!selectedId.value && datasets.value.length > 0) {
      const firstDataset = datasets.value[0]
      if (firstDataset) selectDatasetNode(firstDataset)
    } else if (selectedType.value === 'dataset' && selectedId.value) {
      // 保持之前的选中，更新数据引用
      const found = datasets.value.find(d => (d.ragflow_dataset_id || d.id) === selectedId.value)
      if (found) selectedDataset.value = found
    }
  } catch (err) {
    errorMessage.value = extractError(err)
    engineStatus.value = 'disconnected'
    showErrorBanner.value = true
  } finally {
    loading.value = false
  }
}

// 展开/收起知识库，展开时懒加载文档
const toggleDatasetExpand = async (dataset: KnowledgeBase, forceExpand = false) => {
  const dsId = dataset.ragflow_dataset_id || dataset.id || ''
  if (!dsId) return

  if (forceExpand) {
    expandedDatasetIds.value[dsId] = true
  } else {
    expandedDatasetIds.value[dsId] = !expandedDatasetIds.value[dsId]
  }

  // 展开且未加载过文档时，懒加载
  if (expandedDatasetIds.value[dsId] && !documentsMap.value[dsId] && !dataset.is_missing_in_ragflow) {
    await fetchDocumentsForDataset(dsId)
  }
}

// 获取某个知识库下的文档
const fetchDocumentsForDataset = async (dsId: string) => {
  documentsLoadingMap.value[dsId] = true
  try {
    const response = await axios.get(`/api/portal/ragflow/datasets/${dsId}/documents`)
    documentsMap.value[dsId] = apiData(response)
  } catch (err) {
    showToast(extractError(err), 'error')
    documentsMap.value[dsId] = []
  } finally {
    documentsLoadingMap.value[dsId] = false
  }
}

// 选中知识库节点
const selectDatasetNode = (dataset: KnowledgeBase) => {
  selectedType.value = 'dataset'
  selectedId.value = dataset.ragflow_dataset_id || dataset.id || ''
  selectedDataset.value = dataset
  selectedDocument.value = null
  toggleDatasetExpand(dataset, true)
}

// 选中文档节点
const selectDocumentNode = (doc: KnowledgeDocument, dataset: KnowledgeBase) => {
  selectedType.value = 'document'
  selectedId.value = doc.id
  selectedDataset.value = dataset
  selectedDocument.value = doc
  const dsId = dataset.ragflow_dataset_id || dataset.id || ''
  const status = getDocStatus(doc)
    if (dsId && (status === 'parsing' || parsingDocIds.value[doc.id])) {
    if (!parsingDocIds.value[doc.id]) {
      parsingDocIds.value = { ...parsingDocIds.value, [doc.id]: true }
    }
    startParseStatusPolling(dsId, doc.id)
  }
}

// 计算过滤后的知识库树
const filteredDatasets = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  if (!query) return datasets.value

  return datasets.value.filter(ds => {
    const nameMatch = (ds.platform_name || ds.name || '').toLowerCase().includes(query)
    const descMatch = (ds.platform_description || ds.description || '').toLowerCase().includes(query)
    const ownerMatch = (ds.owner || '').toLowerCase().includes(query)
    const tagsMatch = (ds.tags || []).some(t => t.toLowerCase().includes(query))

    const dsId = ds.ragflow_dataset_id || ds.id || ''
    const subDocs = documentsMap.value[dsId] || []
    const docMatch = subDocs.some(doc => (doc.name || '').toLowerCase().includes(query))

    return nameMatch || descMatch || ownerMatch || tagsMatch || docMatch
  })
})

const syncFromRagFlow = async () => {
  if (!isEngineReady.value) {
    showToast('知识库引擎未连接，暂不能同步', 'warning')
    return
  }
  syncing.value = true
  errorMessage.value = ''
  try {
    const response = await axios.post('/api/portal/ragflow/datasets/sync')
    const result = response.data?.data || {}
    showToast(
      `同步完成：新增 ${result.created || 0} 个，更新 ${result.updated || 0} 个，跳过 ${result.skipped || 0} 个`,
      'success'
    )
    await fetchDatasets()
  } catch (err) {
    errorMessage.value = extractError(err)
    showToast('从 RAGFlow 同步失败', 'error')
  } finally {
    syncing.value = false
  }
}

const fetchRagFlowConfig = async () => {
  try {
    const response = await axios.get('/api/portal/ragflow/config')
    ragflowConfig.value = response.data?.data || null
  } catch {
    ragflowConfig.value = null
  }
}

const createDataset = async () => {
  if (!isEngineReady.value) {
    showToast('知识库引擎未连接，暂不能创建知识库', 'warning')
    return
  }
  if (!form.value.name.trim()) {
    showToast('请输入知识库名称', 'warning')
    return
  }
  try {
    const res = await axios.post('/api/portal/ragflow/datasets', {
      name: form.value.name.trim(),
      description: form.value.description,
      chunk_method: form.value.chunk_method,
      owner: form.value.owner,
      visibility: form.value.visibility,
      tags: normalizeTags(form.value.tagsText),
      notes: form.value.notes,
      extra_config: parseExtraConfig()
    })
    showToast('知识库创建成功', 'success')
    showCreateModal.value = false
    await fetchDatasets()
    
    // 自动选中并展开新建的知识库
    const newId = res.data?.data?.id
    if (newId) {
      const found = datasets.value.find(d => (d.ragflow_dataset_id || d.id) === newId)
      if (found) selectDatasetNode(found)
    }
  } catch (err) {
    showToast(extractError(err), 'error')
  }
}

const updateMetadata = async () => {
  if (!isEngineReady.value) {
    showToast('知识库引擎未连接，暂不能保存元数据', 'warning')
    return
  }
  if (!selectedDatasetId.value) return
  try {
    await axios.put(`/api/portal/ragflow/datasets/${selectedDatasetId.value}/metadata`, {
      name: form.value.name,
      description: form.value.description,
      owner: form.value.owner,
      visibility: form.value.visibility,
      tags: normalizeTags(form.value.tagsText),
      notes: form.value.notes,
      extra_config: parseExtraConfig()
    })
    showToast('知识库元数据已更新', 'success')
    showEditModal.value = false
    await fetchDatasets()
  } catch (err) {
    showToast(extractError(err), 'error')
  }
}

const confirmDeleteDataset = async () => {
  if (!isEngineReady.value) {
    showToast('知识库引擎未连接，暂不能删除知识库', 'warning')
    return
  }
  if (!deletingDataset.value) return
  try {
    const id = deletingDataset.value.ragflow_dataset_id || deletingDataset.value.id
    await axios.delete('/api/portal/ragflow/datasets', { data: { ids: [id] } })
    showToast('知识库已删除', 'success')
    
    const deletedId = id
    deletingDataset.value = null
    
    // 如果删除的是当前选中的知识库，切换选中状态
    if (selectedId.value === deletedId) {
      selectedType.value = null
      selectedId.value = ''
      selectedDataset.value = null
      selectedDocument.value = null
    }
    
    await fetchDatasets()
  } catch (err) {
    showToast(extractError(err), 'error')
  }
}

const onFileChange = (event: Event) => {
  const input = event.target as HTMLInputElement
  uploadFile.value = input.files?.[0] || null
}

const uploadDocument = async () => {
  if (!isEngineReady.value) {
    showToast('知识库引擎未连接，暂不能上传文档', 'warning')
    return
  }
  if (!uploadFile.value || !selectedDatasetId.value) {
    showToast('请选择要上传的文件', 'warning')
    return
  }
  const payload = new FormData()
  payload.append('file', uploadFile.value)
  try {
    await axios.post(`/api/portal/ragflow/datasets/${selectedDatasetId.value}/documents/upload`, payload)
    showToast('文档上传成功，请在右侧或文档节点中触发解析', 'success')
    uploadFile.value = null
    // 重新获取该知识库下的文档
    await fetchDocumentsForDataset(selectedDatasetId.value)
  } catch (err) {
    showToast(extractError(err), 'error')
  }
}

const confirmDeleteDocument = async () => {
  if (!isEngineReady.value) {
    showToast('知识库引擎未连接，暂不能删除文档', 'warning')
    return
  }
  if (!deletingDocument.value || !selectedDatasetId.value) return
  try {
    const docId = deletingDocument.value.id
    await axios.delete(`/api/portal/ragflow/datasets/${selectedDatasetId.value}/documents`, {
      data: { ids: [docId] }
    })
    showToast('文档已删除', 'success')
    
    deletingDocument.value = null
    
    // 如果删除的是当前选中的文档，清空选中并回退到选中的知识库
    if (selectedType.value === 'document' && selectedId.value === docId) {
      selectedType.value = 'dataset'
      selectedId.value = selectedDatasetId.value
      selectedDocument.value = null
    }
    
    await fetchDocumentsForDataset(selectedDatasetId.value)
  } catch (err) {
    showToast(extractError(err), 'error')
  }
}

/** 已触发解析、等待 RAGFlow 返回终态（防重复点击） */
const parsingDocIds = ref<Record<string, boolean>>({})
const parsePollTimers = new Map<string, ReturnType<typeof setInterval>>()

const syncSelectedDocumentFromMap = (dsId: string, docId: string) => {
  const updatedDoc = (documentsMap.value[dsId] || []).find(d => d.id === docId)
  if (updatedDoc && selectedDocument.value?.id === docId) {
    selectedDocument.value = updatedDoc
  }
  return updatedDoc
}

const isDocParsing = (doc?: KnowledgeDocument | null) => {
  if (!doc) return false
  if (parsingDocIds.value[doc.id]) return true
  const status = getDocStatus(doc)
  return status === 'parsing'
}

const isParseButtonDisabled = (doc?: KnowledgeDocument | null) => {
  if (!isEngineReady.value || !doc) return true
  return isDocParsing(doc)
}

const stopParsePolling = (docId: string) => {
  const timer = parsePollTimers.get(docId)
  if (timer) {
    clearInterval(timer)
    parsePollTimers.delete(docId)
  }
}

const unmarkDocParsing = (docId: string) => {
  stopParsePolling(docId)
  if (!parsingDocIds.value[docId]) return
  const next = { ...parsingDocIds.value }
  delete next[docId]
  parsingDocIds.value = next
}

const startParseStatusPolling = (dsId: string, docId: string) => {
  stopParsePolling(docId)
  let seenParsing = false
  let pollCount = 0
  const maxPolls = 90 // ~3 分钟

  const timer = setInterval(async () => {
    pollCount += 1
    try {
      await fetchDocumentsForDataset(dsId)
      const doc = syncSelectedDocumentFromMap(dsId, docId)
      const status = getDocStatus(doc)
      if (status === 'parsing') {
        seenParsing = true
      }
      const terminal = status !== 'parsing'
      if (terminal && (seenParsing || pollCount >= 2)) {
        unmarkDocParsing(docId)
        return
      }
      if (pollCount >= maxPolls) {
        unmarkDocParsing(docId)
        showToast('解析状态轮询超时，请手动刷新查看', 'warning')
      }
    } catch {
      if (pollCount >= maxPolls) {
        unmarkDocParsing(docId)
      }
    }
  }, 2000)

  parsePollTimers.set(docId, timer)
}

// 解析单个文档
const parseSingleDocument = async (docId: string) => {
  const dsId = selectedDatasetId.value
  if (!isEngineReady.value) {
    showToast('知识库引擎未连接，暂不能解析文档', 'warning')
    return
  }
  if (!dsId) return

  const currentDoc = selectedDocument.value?.id === docId
    ? selectedDocument.value
    : (documentsMap.value[dsId] || []).find(d => d.id === docId)
  if (isParseButtonDisabled(currentDoc)) return

  parsingDocIds.value = { ...parsingDocIds.value, [docId]: true }
  try {
    await axios.post(`/api/portal/ragflow/datasets/${dsId}/documents/parse`, {
      ids: [docId]
    })
    showToast('解析任务已触发', 'success')
    await fetchDocumentsForDataset(dsId)
    syncSelectedDocumentFromMap(dsId, docId)

    // 轮询直到解析完成/失败（RAGFlow 状态切换可能有延迟）
    startParseStatusPolling(dsId, docId)
  } catch (err) {
    unmarkDocParsing(docId)
    showToast(extractError(err), 'error')
  }
}

const copyToClipboard = (text: string) => {
  navigator.clipboard.writeText(text)
  showToast('已复制到剪贴板', 'success')
}

const formatTime = (value?: string) => value ? new Date(value).toLocaleString() : '-'
const formatSize = (value?: number) => {
  if (!value) return '-'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

const isReadOnlyDataset = (dataset?: KnowledgeBase | null) => {
  if (!dataset) return false
  const name = dataset.platform_name || dataset.name
  if (name === 'chatbi-example-meta') return true
  if (dataset.is_read_only === true) return true
  if (dataset.can_write === false) return true
  return false
}

const canViewChunks = (dataset?: KnowledgeBase | null) => {
  if (!dataset) return false
  if (dataset.can_view_chunks === true) return true
  if (dataset.can_view_chunks === false) return false
  return !isReadOnlyDataset(dataset)
}

const getDocStatus = (doc: any) => {
  if (!doc) return 'unparsed'
  // RAGFlow returns parsing status in the 'run' field:
  // "0": unparsed, "1": completed/parsed, "2": parsing, "3": failed
  const run = String(doc.run ?? '')
  if (run === '1') return 'parsed'
  if (run === '2') return 'parsing'
  if (run === '3') return 'failed'
  if (run === '0') return 'unparsed'
  
  // Fallback to doc.status if run is not available
  const status = String(doc.status ?? '')
  if (status === 'completed' || status === 'parsed' || status === '1') return 'parsed'
  if (status === 'running' || status === 'parsing' || status === '2') return 'parsing'
  if (status === 'failed' || status === '3') return 'failed'
  return 'unparsed'
}

const getDocStatusText = (doc: any) => {
  const status = getDocStatus(doc)
  switch (status) {
    case 'parsed': return '解析完成'
    case 'parsing': return '解析中'
    case 'failed': return '解析失败'
    default: return '未解析'
  }
}

const showChunksModal = ref(false)
const viewingDocument = ref<any>(null)
const chunksList = ref<any[]>([])
const chunksTotal = ref(0)
const chunksPage = ref(1)
const loadingChunks = ref(false)

const openChunksModal = async (doc: any) => {
  viewingDocument.value = doc
  showChunksModal.value = true
  chunksPage.value = 1
  chunksList.value = []
  chunksTotal.value = 0
  await fetchChunks()
}

const closeChunksModal = () => {
  showChunksModal.value = false
  viewingDocument.value = null
  chunksList.value = []
  chunksTotal.value = 0
}

const loadChunksPage = async (page: number) => {
  chunksPage.value = page
  await fetchChunks()
}

const fetchChunks = async () => {
  if (!viewingDocument.value) return
  loadingChunks.value = true
  try {
    const res = await axios.get(`/api/portal/ragflow/datasets/${selectedDatasetId.value}/documents/${viewingDocument.value.id}/chunks`, {
      params: {
        page: chunksPage.value,
        page_size: 30
      }
    })
    const resData = res.data?.data || {}
    chunksList.value = resData.chunks || []
    chunksTotal.value = resData.total || resData.chunks?.length || 0
  } catch (err) {
    showToast(extractError(err), 'error')
  } finally {
    loadingChunks.value = false
  }
}

onUnmounted(() => {
  for (const docId of parsePollTimers.keys()) {
    unmarkDocParsing(docId)
  }
})

onMounted(async () => {
  await fetchRagFlowConfig()
  await fetchDatasets()
})
</script>

<template>
  <div class="space-y-6">
    <!-- Header Section -->
    <div class="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-white p-5 rounded-2xl border border-gray-200/80 shadow-sm">
      <div>
        <h1 class="text-2xl font-bold text-gray-900 tracking-tight">知识库管理</h1>
        <p class="text-sm text-gray-500 mt-1">管理 RAGFlow 知识库、平台扩展元数据及一体化文档解析。</p>
        <p class="text-xs text-gray-400 mt-2 flex items-center gap-1">
          <span>当前 知识库引擎(RAGFlow)：</span>
          <a :href="ragflowApiUrl" target="_blank" rel="noopener noreferrer" :title="ragflowApiUrl" class="font-mono text-primary bg-gray-100 px-1.5 py-0.5 rounded hover:underline truncate max-w-[200px] sm:max-w-[300px] inline-block align-bottom">{{ ragflowApiUrl }}</a>
          <span v-if="ragflowConfig && !ragflowConfig.api_key_configured" class="ml-2 text-amber-600 font-medium">⚠️ API Key 未配置</span>
        </p>
      </div>
      <div class="flex items-center gap-3">
        <!-- 引擎连接指示器 -->
        <div
          class="flex items-center gap-2 px-3 py-1.5 rounded-xl text-xs border transition-colors"
          :class="{
            'border-blue-200 bg-blue-50/50 text-blue-700': engineStatus === 'checking',
            'border-emerald-200 bg-emerald-50/50 text-emerald-700': engineStatus === 'connected',
            'border-amber-200 bg-amber-50/50 text-amber-700': engineStatus === 'disconnected'
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
        </div>
        <button
          v-if="canSync"
          class="px-4 py-2 rounded-xl border border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 text-sm font-medium transition-all disabled:opacity-50"
          :disabled="syncing || !isEngineReady"
          @click="syncFromRagFlow"
        >
          {{ syncing ? '同步中...' : '从 RAGFlow 同步' }}
        </button>
        <button class="px-4 py-2 rounded-xl border border-gray-200 bg-white hover:bg-gray-50 text-sm font-medium transition-all" @click="fetchDatasets">刷新</button>
        <button
          v-if="canCreate"
          class="px-4 py-2 rounded-xl bg-primary text-white hover:bg-primary/90 text-sm font-medium shadow-sm transition-all disabled:opacity-50 disabled:cursor-not-allowed"
          :disabled="!isEngineReady"
          @click="openCreate"
        >
          新建知识库
        </button>
      </div>
    </div>

    <!-- Error Banner -->
    <div v-if="errorMessage && showErrorBanner" class="relative rounded-2xl border border-amber-200 bg-amber-50 p-4 pr-10 text-sm text-amber-800 shadow-sm flex items-start gap-3">
      <svg class="w-5 h-5 text-amber-500 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
      </svg>
      <div>
        <div class="font-semibold text-amber-900">RAGFlow 服务连通性故障</div>
        <div class="mt-1 text-amber-800/90">{{ friendlyRagFlowError }}</div>
        <div class="mt-2 text-xs font-mono text-amber-700 bg-amber-100/50 p-2 rounded-lg">
          <div>连接地址: <a :href="ragflowApiUrl" target="_blank" rel="noopener noreferrer" :title="ragflowApiUrl" class="hover:underline truncate max-w-[200px] sm:max-w-[300px] inline-block align-bottom">{{ ragflowApiUrl }}</a></div>
          <div class="mt-0.5">错误日志: {{ errorMessage }}</div>
        </div>
      </div>
      <!-- 右上角关闭按钮 -->
      <button @click="showErrorBanner = false" class="absolute top-4 right-4 text-amber-500 hover:text-amber-700 transition-colors" title="关闭">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>

    <!-- Main Workspace Layout -->
    <div class="flex flex-col lg:flex-row gap-6 min-h-[calc(100vh-16rem)] items-stretch">
      
      <!-- Left Side: Knowledge base & Document tree navigation -->
      <section class="w-full lg:w-96 flex-shrink-0 bg-white rounded-2xl border border-gray-200 shadow-sm flex flex-col overflow-hidden">
        <div class="p-4 border-b border-gray-100/80 bg-gray-50/50">
          <div class="relative">
            <input
              v-model="searchQuery"
              type="text"
              placeholder="搜索知识库、文档或归属部门..."
              class="w-full pl-9 pr-4 py-2 bg-white border border-gray-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all placeholder-gray-400"
            />
            <svg class="w-4 h-4 text-gray-400 absolute left-3 top-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            <button v-if="searchQuery" class="absolute right-3 top-3 text-gray-400 hover:text-gray-600" @click="searchQuery = ''">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        <!-- Scrollable tree region -->
        <div class="flex-1 overflow-y-auto p-4 space-y-1 select-none">
          <div v-if="loading" class="text-center text-gray-400 py-12 text-sm flex flex-col items-center gap-3">
            <span class="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin"></span>
            <span>加载数据中...</span>
          </div>
          <div v-else-if="filteredDatasets.length === 0" class="text-center text-gray-400 py-12 text-sm italic">
            暂无匹配的知识库
          </div>
          <div v-else class="space-y-1">
            <div v-for="dataset in filteredDatasets" :key="dataset.ragflow_dataset_id || dataset.id" class="space-y-1">
              
              <!-- Dataset Node (Parent) -->
              <div
                class="group relative flex items-center justify-between px-3 py-2.5 rounded-xl cursor-pointer transition-all border border-transparent"
                :class="[
                  selectedType === 'dataset' && selectedId === (dataset.ragflow_dataset_id || dataset.id)
                    ? 'bg-primary/5 border-primary/20 text-primary font-medium shadow-sm'
                    : 'hover:bg-gray-50 text-gray-700'
                ]"
                @click="selectDatasetNode(dataset)"
              >
                <div class="flex items-center gap-2.5 min-w-0 flex-1">
                  <!-- Toggle arrow -->
                  <button
                    class="p-0.5 rounded hover:bg-gray-200/50 text-gray-400 hover:text-gray-600 transition-transform duration-200 shrink-0"
                    :class="{ 'rotate-90': expandedDatasetIds[dataset.ragflow_dataset_id || dataset.id] }"
                    @click.stop="toggleDatasetExpand(dataset)"
                  >
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                    </svg>
                  </button>
                  <!-- Dataset folder icon -->
                  <svg class="w-5 h-5 shrink-0" :class="dataset.is_missing_in_ragflow ? 'text-amber-500' : 'text-primary'" fill="currentColor" viewBox="0 0 20 20">
                    <path fill-rule="evenodd" d="M2 6a2 2 0 012-2h4l2 2h4a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" clip-rule="evenodd" />
                  </svg>
                  <!-- Display name + creator -->
                  <div class="min-w-0 flex flex-col">
                    <span class="truncate text-sm font-medium">{{ dataset.platform_name || dataset.name }}</span>
                    <span v-if="dataset.created_by" class="truncate text-[10px] text-gray-400">创建人: {{ dataset.created_by }}</span>
                  </div>
                </div>
                
                <!-- Indicators & Actions -->
                <div class="flex items-center gap-2 shrink-0 min-w-[24px] justify-end">
                  <!-- Display doc count -->
                  <span
                    v-if="!dataset.is_missing_in_ragflow"
                    class="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 font-semibold"
                  >
                    {{ dataset.doc_count ?? dataset.document_count ?? 0 }}
                  </span>
                  <span
                    v-else
                    class="text-[10px] px-2 py-0.5 rounded-full bg-amber-100 text-amber-700"
                  >
                    失联
                  </span>

                  <!-- Hover Action Buttons -->
                  <div class="hidden group-hover:flex items-center gap-1 bg-inherit pl-2 absolute right-3 top-1/2 -translate-y-1/2">
                    <button
                      v-if="canEdit && !isReadOnlyDataset(dataset)"
                      class="p-1 rounded hover:bg-gray-200/60 text-gray-500 hover:text-primary transition-all bg-inherit"
                      title="修改元数据"
                      @click.stop="openEdit(dataset)"
                    >
                      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                      </svg>
                    </button>
                    <button
                      v-if="canDelete && !isReadOnlyDataset(dataset)"
                      class="p-1 rounded hover:bg-red-50 text-gray-400 hover:text-red-600 transition-all bg-inherit"
                      title="删除知识库"
                      @click.stop="deletingDataset = dataset"
                    >
                      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                    </button>
                  </div>
                </div>
              </div>

              <!-- Document Nodes (Children) -->
              <transition name="slide-down">
                <div
                  v-show="expandedDatasetIds[dataset.ragflow_dataset_id || dataset.id]"
                  class="pl-4 ml-4.5 border-l border-gray-150 space-y-0.5 overflow-hidden"
                >
                  <!-- Loader for docs -->
                  <div v-if="documentsLoadingMap[dataset.ragflow_dataset_id || dataset.id]" class="py-2 pl-3 text-xs text-gray-400 flex items-center gap-1.5">
                    <span class="w-3 h-3 rounded-full border border-gray-300 border-t-transparent animate-spin"></span>
                    <span>加载文档...</span>
                  </div>
                  <!-- Empty hint -->
                  <div v-else-if="!(documentsMap[dataset.ragflow_dataset_id || dataset.id]?.length)" class="py-2 pl-3 text-xs text-gray-400 italic">
                    暂无文档
                  </div>
                  <!-- Documents loop -->
                  <div
                    v-else
                    v-for="doc in documentsMap[dataset.ragflow_dataset_id || dataset.id]"
                    :key="doc.id"
                    class="group/doc relative flex items-center justify-between pl-3 pr-2 py-1.5 rounded-lg cursor-pointer transition-all border border-transparent"
                    :class="[
                      selectedType === 'document' && selectedId === doc.id
                        ? 'bg-blue-50/70 border-blue-100 text-blue-700 font-semibold shadow-sm'
                        : 'hover:bg-gray-50 text-gray-600'
                    ]"
                    @click="selectDocumentNode(doc, dataset)"
                  >
                    <div class="flex items-center gap-2 min-w-0 flex-1">
                      <!-- File format icon -->
                      <svg class="w-3.5 h-3.5 shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
                      </svg>
                      <!-- File name -->
                      <span class="truncate text-xs">{{ doc.name || doc.id }}</span>
                    </div>

                    <!-- File status dot or delete action -->
                    <div class="flex items-center gap-2 shrink-0 min-w-[12px] justify-end">
                      <!-- Status indicators -->
                      <span
                        class="w-1.5 h-1.5 rounded-full shrink-0 transition-all"
                        :class="{
                          'bg-emerald-500 shadow-[0_0_4px_#10b981]': getDocStatus(doc) === 'parsed',
                          'bg-amber-500 animate-pulse shadow-[0_0_4px_#f59e0b]': getDocStatus(doc) === 'parsing',
                          'bg-red-500 shadow-[0_0_4px_#ef4444]': getDocStatus(doc) === 'failed',
                          'bg-gray-300': !getDocStatus(doc) || getDocStatus(doc) === 'unparsed'
                        }"
                      ></span>

                      <!-- Hover Action Buttons -->
                      <div class="hidden group-hover/doc:flex items-center bg-inherit pl-2 absolute right-2 top-1/2 -translate-y-1/2">
                        <button
                          v-if="canDeleteDocument && !isReadOnlyDataset(dataset)"
                          class="p-0.5 rounded hover:bg-red-50 text-gray-400 hover:text-red-600 transition-all bg-inherit"
                          title="删除文档"
                          @click.stop="deletingDocument = doc"
                        >
                          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                          </svg>
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </transition>

            </div>
          </div>
        </div>
      </section>

      <!-- Right Side: Workbench / Details panel -->
      <section class="flex-1 bg-white rounded-2xl border border-gray-200 shadow-sm p-6 flex flex-col min-w-0">
        
        <!-- Case 1: Knowledge base metadata details selected -->
        <div v-if="selectedType === 'dataset' && selectedDataset" class="space-y-6 flex-1 flex flex-col">
          <div class="flex items-start justify-between border-b border-gray-100 pb-5">
            <div>
              <div class="flex items-center gap-3">
                <h2 class="text-xl font-bold text-gray-900">{{ selectedDataset.platform_name || selectedDataset.name }}</h2>
                <span v-if="selectedDataset.is_missing_in_ragflow" class="px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 text-xs font-semibold">RAGFlow 侧已失联</span>
              </div>
              <p class="text-sm text-gray-500 mt-1.5">{{ selectedDataset.platform_description || selectedDataset.description || '暂无对该知识库的描述' }}</p>
            </div>
            <button
              v-if="canEdit && !isReadOnlyDataset(selectedDataset)"
              class="px-4 py-2 text-sm font-medium border border-gray-200 rounded-xl hover:bg-gray-50 transition-all flex items-center gap-1.5 shrink-0"
              @click="openEdit(selectedDataset)"
            >
              <svg class="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
              </svg>
              编辑元数据
            </button>
          </div>

          <!-- RAG Parameters cards grid -->
          <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-4">
            <div class="bg-gray-50/50 p-4 rounded-xl border border-gray-200/50 flex flex-col">
              <span class="text-xs text-gray-400 font-medium">知识库 ID</span>
              <div class="flex items-center justify-between mt-1">
                <span class="text-sm font-mono text-gray-700 truncate mr-2">{{ selectedDataset.ragflow_dataset_id || selectedDataset.id }}</span>
                <button class="text-primary hover:text-primary-dark shrink-0" @click="copyToClipboard(selectedDataset.ragflow_dataset_id || selectedDataset.id || '')">
                  <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3" />
                  </svg>
                </button>
              </div>
            </div>
            <div class="bg-gray-50/50 p-4 rounded-xl border border-gray-200/50 flex flex-col">
              <span class="text-xs text-gray-400 font-medium">创建人</span>
              <span class="text-sm font-medium text-gray-800 mt-1">{{ selectedDataset.created_by || '未知' }}</span>
            </div>
            <div class="bg-gray-50/50 p-4 rounded-xl border border-gray-200/50 flex flex-col">
              <span class="text-xs text-gray-400 font-medium">业务归属部门 (Owner)</span>
              <span class="text-sm font-medium text-gray-800 mt-1">{{ selectedDataset.owner || '未指派' }}</span>
            </div>
            <div class="bg-gray-50/50 p-4 rounded-xl border border-gray-200/50 flex flex-col">
              <span class="text-xs text-gray-400 font-medium">公开属性 (Visibility)</span>
              <span class="text-sm font-medium text-gray-800 mt-1 capitalize">{{ selectedDataset.visibility || 'Private' }}</span>
            </div>
            <div class="bg-gray-50/50 p-4 rounded-xl border border-gray-200/50 flex flex-col">
              <span class="text-xs text-gray-400 font-medium">最后同步/更新时间</span>
              <span class="text-sm font-medium text-gray-800 mt-1 truncate">{{ formatTime(selectedDataset.update_time || selectedDataset.updated_at) }}</span>
            </div>
          </div>

          <!-- Tags & Notes -->
          <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div class="space-y-2">
              <h3 class="text-xs font-semibold text-gray-400 uppercase tracking-wider">分类标签</h3>
              <div v-if="selectedDataset.tags?.length" class="flex flex-wrap gap-1.5">
                <span
                  v-for="tag in selectedDataset.tags"
                  :key="tag"
                  class="px-2.5 py-1 text-xs rounded-full bg-blue-50 text-blue-700 border border-blue-100 font-medium"
                >
                  {{ tag }}
                </span>
              </div>
              <span v-else class="text-sm text-gray-400 italic">暂无标签</span>
            </div>

            <div class="space-y-2">
              <h3 class="text-xs font-semibold text-gray-400 uppercase tracking-wider">平台备注</h3>
              <p class="text-sm text-gray-600 bg-gray-50/50 p-3 rounded-xl border border-gray-150 leading-relaxed">
                {{ selectedDataset.notes || '暂无备注说明。' }}
              </p>
            </div>
          </div>

          <!-- Document Upload Region -->
          <div class="space-y-3">
            <h3 class="text-sm font-semibold text-gray-800">上传新文档到此知识库</h3>
            <div
              v-if="canUpload && !selectedDataset.is_missing_in_ragflow && !isReadOnlyDataset(selectedDataset)"
              class="border-2 border-dashed border-gray-300 hover:border-primary rounded-2xl p-6 transition-all flex flex-col items-center justify-center bg-gray-50/30 group"
            >
              <svg class="w-10 h-10 text-gray-400 group-hover:text-primary transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
              </svg>
              <input
                type="file"
                class="block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-xl file:border-0 file:text-sm file:font-semibold file:bg-primary/10 file:text-primary hover:file:bg-primary/20 file:cursor-pointer mt-4"
                :disabled="!isEngineReady"
                @change="onFileChange"
              />
              <div class="flex items-center gap-3 mt-4 w-full max-w-xs">
                <button
                  class="w-full px-4 py-2.5 rounded-xl bg-gray-900 text-white text-sm font-semibold shadow hover:bg-gray-800 transition-all disabled:opacity-50"
                  :disabled="!uploadFile || !isEngineReady"
                  @click="uploadDocument"
                >
                  上传至服务器
                </button>
              </div>
              <p class="text-xs text-gray-400 mt-2">支持 PDF, DOCX, TXT 等任意结构化文档，上传后可在左侧树节点展开管理和手动解析。</p>
            </div>
            <div v-else class="rounded-xl bg-gray-50/50 p-4 border text-center text-sm text-gray-400">
              <span v-if="isReadOnlyDataset(selectedDataset)" class="text-amber-600 font-medium">⚠️ 只读模式：您不是该知识库的创建人，仅可查看，不可修改或上传文档</span>
              <span v-else>当前状态无法向知识库上传文档（可能由于 RAGFlow 失联或权限受限）</span>
            </div>
          </div>

          <!-- Extended config JSON -->
          <div class="space-y-2 flex-1 flex flex-col min-h-[150px]">
            <div class="flex items-center justify-between">
              <h3 class="text-xs font-semibold text-gray-400 uppercase tracking-wider">业务元数据扩展配置 (JSON)</h3>
              <button
                class="text-xs text-primary hover:underline font-semibold"
                @click="copyToClipboard(JSON.stringify(selectedDataset.local_metadata?.extra_config || {}, null, 2))"
              >
                复制配置
              </button>
            </div>
            <pre class="flex-1 overflow-auto bg-gray-900 text-emerald-400 p-4 rounded-xl font-mono text-xs shadow-inner leading-relaxed">{{ JSON.stringify(selectedDataset.local_metadata?.extra_config || {}, null, 2) }}</pre>
          </div>
        </div>

        <!-- Case 2: Document details and parsing node selected -->
        <div v-else-if="selectedType === 'document' && selectedDocument" class="space-y-6 flex-1 flex flex-col">
          <div class="flex items-start justify-between border-b border-gray-100 pb-5 gap-4">
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-3">
                <h2 class="text-xl font-bold text-gray-900 truncate" :title="selectedDocument.name || selectedDocument.id">{{ selectedDocument.name || selectedDocument.id }}</h2>
                <div
                  class="flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium border shrink-0"
                  :class="{
                    'bg-emerald-50 text-emerald-700 border-emerald-200': getDocStatus(selectedDocument) === 'parsed',
                    'bg-amber-50 text-amber-700 border-amber-200': getDocStatus(selectedDocument) === 'parsing',
                    'bg-red-50 text-red-700 border-red-200': getDocStatus(selectedDocument) === 'failed',
                    'bg-gray-50 text-gray-600 border-gray-200': !getDocStatus(selectedDocument) || getDocStatus(selectedDocument) === 'unparsed'
                  }"
                >
                  <span
                    class="w-1.5 h-1.5 rounded-full"
                    :class="{
                      'bg-emerald-500': getDocStatus(selectedDocument) === 'parsed',
                      'bg-amber-500 animate-ping': getDocStatus(selectedDocument) === 'parsing',
                      'bg-red-500': getDocStatus(selectedDocument) === 'failed',
                      'bg-gray-400': !getDocStatus(selectedDocument) || getDocStatus(selectedDocument) === 'unparsed'
                    }"
                  ></span>
                  <span>{{ getDocStatusText(selectedDocument) }}</span>
                </div>
              </div>
              <p class="text-xs text-gray-400 mt-2 font-mono truncate">所属知识库: {{ selectedDataset?.platform_name || selectedDataset?.name }} (ID: {{ selectedDatasetId }})</p>
            </div>
            
            <div class="flex gap-2 shrink-0 ml-4">
              <button
                v-if="canParse && !isReadOnlyDataset(selectedDataset)"
                class="px-4 py-2 bg-primary text-white text-sm font-semibold rounded-xl hover:bg-primary/95 transition-all shadow-sm flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap shrink-0"
                :disabled="isParseButtonDisabled(selectedDocument)"
                @click="parseSingleDocument(selectedDocument.id)"
              >
                <span
                  v-if="isDocParsing(selectedDocument)"
                  class="w-4 h-4 shrink-0 rounded-full border-2 border-white/40 border-t-white animate-spin"
                />
                <svg v-else class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                {{ isDocParsing(selectedDocument) ? '解析中…' : '触发解析' }}
              </button>
              <button
                v-if="canDeleteDocument && !isReadOnlyDataset(selectedDataset)"
                class="px-4 py-2 border border-red-200 text-red-600 hover:bg-red-50 text-sm font-semibold rounded-xl transition-all whitespace-nowrap shrink-0"
                :disabled="!isEngineReady"
                @click="deletingDocument = selectedDocument"
              >
                删除文档
              </button>
              <span v-if="isReadOnlyDataset(selectedDataset)" class="text-xs text-amber-600 bg-amber-50 border border-amber-100 rounded-xl px-3 py-2 flex items-center select-none font-medium">⚠️ 只读：非创建人不可解析/删除/查看分块</span>
            </div>
          </div>

          <!-- Document specifications grid -->
          <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div class="bg-gray-50/50 p-4 rounded-xl border border-gray-150">
              <span class="text-xs text-gray-400 font-semibold uppercase tracking-wider block">物理特征 / 文件大小</span>
              <span class="text-lg font-bold text-gray-800 mt-2 block">{{ formatSize(selectedDocument.size) }}</span>
            </div>
            <div class="bg-gray-50/50 p-4 rounded-xl border border-gray-150 flex flex-col justify-between">
              <div>
                <span class="text-xs text-gray-400 font-semibold uppercase tracking-wider block">已分切片段数 (Chunks)</span>
                <span class="text-lg font-bold text-gray-800 mt-2 block">{{ selectedDocument.chunk_count ?? 0 }} 个分块</span>
              </div>
              <button
                v-if="getDocStatus(selectedDocument) === 'parsed' && canViewChunks(selectedDataset)"
                class="text-xs text-primary hover:underline mt-2 flex items-center gap-1 font-semibold self-start"
                @click="openChunksModal(selectedDocument)"
              >
                <span>🔍 查看分块详情</span>
              </button>
              <span
                v-else-if="getDocStatus(selectedDocument) === 'parsed' && !canViewChunks(selectedDataset)"
                class="text-xs text-gray-400 mt-2 block"
              >
                无权限查看分块明细（仅创建人或管理员可查看）
              </span>
            </div>
            <div class="bg-gray-50/50 p-4 rounded-xl border border-gray-150">
              <span class="text-xs text-gray-400 font-semibold uppercase tracking-wider block">上传及最后更新时间</span>
              <span class="text-sm font-bold text-gray-800 mt-2.5 block truncate">{{ formatTime(selectedDocument.update_time || selectedDocument.created_at) }}</span>
            </div>
          </div>

          <!-- Parsing logs/info block -->
          <div class="space-y-3 bg-gray-50/30 border border-gray-150 rounded-2xl p-5 flex-1 flex flex-col justify-between">
            <div class="space-y-2">
              <h3 class="text-sm font-semibold text-gray-800">文档解析生命周期说明</h3>
              <p class="text-xs text-gray-500 leading-relaxed">
                平台上传的文档将被推送到 RAGFlow 的数据切片服务中。默认解析切分会根据知识库中配置的 `chunk_method` 算法进行文档提取。
                解析过程包括：格式化清洗、文本片段拆分、密集检索向量 Embedding 构建等。解析完成后，知识库即可直接提供语义检索服务。
              </p>
            </div>
            
            <div class="border-t border-gray-150 pt-4 flex items-center justify-between text-xs text-gray-400">
              <span>文档 ID: <span class="font-mono">{{ selectedDocument.id }}</span></span>
              <button class="text-primary hover:underline" @click="copyToClipboard(selectedDocument.id)">复制 ID</button>
            </div>
          </div>
        </div>

        <!-- Case 3: Empty State (Default) -->
        <div v-else class="flex-1 flex flex-col items-center justify-center text-center p-8 space-y-4">
          <div class="w-16 h-16 rounded-full bg-gray-100 flex items-center justify-center text-gray-400">
            <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 114 0v2m-4 0h4m-2 0h-2" />
            </svg>
          </div>
          <div>
            <h3 class="text-md font-bold text-gray-900">请选择节点</h3>
            <p class="text-sm text-gray-500 mt-1 max-w-xs">在左侧树形导航中选中知识库或者具体文档，可在右侧显示详情并进行业务操作。</p>
          </div>
        </div>

      </section>
    </div>

    <!-- Create dataset modal -->
    <Modal :show="showCreateModal" title="创建知识库" size="max-w-xl" @close="showCreateModal = false">
      <div class="space-y-4">
        <div>
          <label class="block text-xs font-semibold text-gray-400 mb-1.5">知识库名称 *</label>
          <input v-model="form.name" class="w-full border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary" placeholder="请输入知识库名称" />
        </div>
        <div>
          <label class="block text-xs font-semibold text-gray-400 mb-1.5">业务描述</label>
          <textarea v-model="form.description" class="w-full border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary" rows="2" placeholder="请输入知识库描述"></textarea>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="block text-xs font-semibold text-gray-400 mb-1.5">归属部门/人员</label>
            <input v-model="form.owner" class="w-full border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary" placeholder="归属" />
          </div>
          <div>
            <label class="block text-xs font-semibold text-gray-400 mb-1.5">解析算法 *</label>
            <select v-model="form.chunk_method" class="w-full border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary bg-white">
              <option value="naive">Naive (通用切片)</option>
              <option value="qa">QA (大模型问答对)</option>
              <option value="table">Table (表格切片)</option>
              <option value="laws">Laws (法律法规)</option>
              <option value="paper">Paper (学术论文)</option>
              <option value="resume">Resume (简历解析)</option>
              <option value="book">Book (图书解析)</option>
              <option value="manual">Manual (手动分块)</option>
              <option value="one">One (单文件整体)</option>
            </select>
          </div>
        </div>
        <!-- 动态解析指引说明 -->
        <div v-if="chunkMethodDescription" class="rounded-xl border border-blue-100 bg-blue-50/40 p-3 text-xs text-blue-700 leading-relaxed shadow-sm">
          {{ chunkMethodDescription }}
        </div>
        <div>
          <label class="block text-xs font-semibold text-gray-400 mb-1.5">标签 (英文逗号分隔)</label>
          <input v-model="form.tagsText" class="w-full border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary" placeholder="例如: 财务, 报税, 2026" />
        </div>
        <div>
          <label class="block text-xs font-semibold text-gray-400 mb-1.5">平台备注</label>
          <textarea v-model="form.notes" class="w-full border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary" rows="2" placeholder="请输入备注"></textarea>
        </div>
        <div>
          <label class="block text-xs font-semibold text-gray-400 mb-1.5">业务扩展配置 JSON</label>
          <textarea v-model="form.extraConfigText" class="w-full border border-gray-200 rounded-xl px-3 py-2 font-mono text-xs focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary bg-gray-50/50" rows="3" placeholder="{}"></textarea>
        </div>
        <div class="flex justify-end gap-3 pt-2">
          <button class="px-4 py-2 rounded-xl border text-sm font-semibold hover:bg-gray-50 transition-all" @click="showCreateModal = false">取消</button>
          <button class="px-4 py-2 rounded-xl bg-primary text-white text-sm font-semibold disabled:opacity-50" :disabled="!isEngineReady" @click="createDataset">确定创建</button>
        </div>
      </div>
    </Modal>

    <!-- Edit metadata modal -->
    <Modal :show="showEditModal" title="编辑知识库元数据" size="max-w-xl" @close="showEditModal = false">
      <div class="space-y-4">
        <div>
          <label class="block text-xs font-semibold text-gray-400 mb-1.5">平台展示名称 *</label>
          <input v-model="form.name" class="w-full border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary" placeholder="展示名称" />
        </div>
        <div>
          <label class="block text-xs font-semibold text-gray-400 mb-1.5">描述说明</label>
          <textarea v-model="form.description" class="w-full border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary" rows="2" placeholder="描述"></textarea>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="block text-xs font-semibold text-gray-400 mb-1.5">业务归属人员</label>
            <input v-model="form.owner" class="w-full border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary" placeholder="归属" />
          </div>
          <div>
            <label class="block text-xs font-semibold text-gray-400 mb-1.5">可见范围</label>
            <select v-model="form.visibility" class="w-full border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary bg-white">
              <option value="private">Private (私有)</option>
              <option value="team">Team (团队可见)</option>
              <option value="public">Public (公开)</option>
            </select>
          </div>
        </div>
        <div>
          <label class="block text-xs font-semibold text-gray-400 mb-1.5">标签 (英文逗号分隔)</label>
          <input v-model="form.tagsText" class="w-full border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary" placeholder="标签" />
        </div>
        <div>
          <label class="block text-xs font-semibold text-gray-400 mb-1.5">备注信息</label>
          <textarea v-model="form.notes" class="w-full border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary" rows="2" placeholder="备注"></textarea>
        </div>
        <div>
          <label class="block text-xs font-semibold text-gray-400 mb-1.5">额外扩展配置 JSON</label>
          <textarea v-model="form.extraConfigText" class="w-full border border-gray-200 rounded-xl px-3 py-2 font-mono text-xs focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary bg-gray-50/50" rows="3" placeholder="扩展配置 JSON"></textarea>
        </div>
        <div class="flex justify-end gap-3 pt-2">
          <button class="px-4 py-2 rounded-xl border text-sm font-semibold hover:bg-gray-50 transition-all" @click="showEditModal = false">取消</button>
          <button class="px-4 py-2 rounded-xl bg-primary text-white text-sm font-semibold disabled:opacity-50" :disabled="!isEngineReady" @click="updateMetadata">保存保存</button>
        </div>
      </div>
    </Modal>

    <!-- Confirm delete modals -->
    <ConfirmModal
      v-if="deletingDataset"
      title="删除知识库"
      :message="`确定真实删除知识库「${deletingDataset.platform_name || deletingDataset.name}」吗？RAGFlow 侧相应集群数据也将同步卸载，此操作不可逆。`"
      confirm-text="确认物理删除"
      @confirm="confirmDeleteDataset"
      @cancel="deletingDataset = null"
    />
    <ConfirmModal
      v-if="deletingDocument"
      title="删除文档"
      :message="`确定从当前知识库中删除文档「${deletingDocument.name || deletingDocument.id}」吗？已解析的 Vector Chunks 数据将同步清空，此操作不可逆。`"
      confirm-text="确认删除"
      @confirm="confirmDeleteDocument"
      @cancel="deletingDocument = null"
    />

    <!-- View chunks modal -->
    <Modal :show="showChunksModal" title="文档切片内容查看" size="max-w-4xl" @close="closeChunksModal">
      <div class="space-y-4">
        <div class="flex items-center justify-between border-b pb-3">
          <div>
            <h3 class="text-sm font-bold text-gray-900 truncate max-w-lg">{{ viewingDocument?.name }}</h3>
            <p class="text-xs text-gray-400 mt-1">共 {{ chunksTotal }} 个分块，当前展示第 {{ chunksPage }} 页</p>
          </div>
          <div class="flex items-center gap-2">
            <button
              class="px-2.5 py-1.5 border rounded-lg text-xs font-semibold hover:bg-gray-50 disabled:opacity-40"
              :disabled="chunksPage <= 1 || loadingChunks"
              @click="loadChunksPage(chunksPage - 1)"
            >
              上一页
            </button>
            <span class="text-xs text-gray-600 font-medium font-mono">{{ chunksPage }} / {{ Math.ceil(chunksTotal / 30) || 1 }}</span>
            <button
              class="px-2.5 py-1.5 border rounded-lg text-xs font-semibold hover:bg-gray-50 disabled:opacity-40"
              :disabled="chunksPage >= Math.ceil(chunksTotal / 30) || loadingChunks"
              @click="loadChunksPage(chunksPage + 1)"
            >
              下一页
            </button>
          </div>
        </div>

        <div v-if="loadingChunks" class="py-12 text-center text-gray-400 text-sm flex flex-col items-center gap-3">
          <span class="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin"></span>
          <span>加载切片数据中...</span>
        </div>
        <div v-else-if="chunksList.length === 0" class="py-12 text-center text-gray-400 text-sm italic">
          暂无切片数据，可能文档尚未解析成功
        </div>
        <div v-else class="space-y-3 max-h-[60vh] overflow-y-auto pr-1">
          <div
            v-for="(chunk, idx) in chunksList"
            :key="chunk.id || idx"
            class="bg-gray-50 p-4 rounded-xl border border-gray-150 hover:border-gray-200 transition-all flex flex-col gap-2"
          >
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-gray-400 font-mono">#{{ (chunksPage - 1) * 30 + idx + 1 }} (ID: {{ chunk.id || '-' }})</span>
            </div>
            <p class="text-sm text-gray-700 leading-relaxed font-mono whitespace-pre-wrap select-text bg-white p-3 rounded-lg border border-gray-150/50 shadow-inner">{{ chunk.content_with_weight || chunk.content || '-' }}</p>
          </div>
        </div>

        <div class="flex justify-end pt-2 border-t">
          <button class="px-4 py-2 rounded-xl bg-gray-900 hover:bg-gray-800 text-white text-sm font-semibold transition-all" @click="closeChunksModal">关闭</button>
        </div>
      </div>
    </Modal>
  </div>
</template>

<style scoped>
/* slide-down vertical transitions */
.slide-down-enter-active,
.slide-down-leave-active {
  transition: max-height 0.25s ease-in-out, opacity 0.2s ease-in-out;
  max-height: 1000px;
}

.slide-down-enter-from,
.slide-down-leave-to {
  max-height: 0;
  opacity: 0;
}
</style>
