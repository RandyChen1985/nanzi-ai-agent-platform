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
  progress?: number
  progress_msg?: string
}

type RagFlowConfigSummary = {
  api_url: string
  api_key_configured: boolean
  configured: boolean
  knowledge_base_enabled?: boolean
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
const aiAnalyzing = ref(false)
const deletingDataset = ref<KnowledgeBase | null>(null)
const deletingDocument = ref<KnowledgeDocument | null>(null)

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
const isKnowledgeEnabled = computed(() => ragflowConfig.value?.knowledge_base_enabled !== false)
const isEngineReady = computed(() => isKnowledgeEnabled.value && engineStatus.value === 'connected' && !loading.value)
const engineStatusText = computed(() => {
  if (!isKnowledgeEnabled.value) return '功能已关闭'
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

const analyzeMetadataByAI = async () => {
  if (!selectedDataset.value) return
  const datasetId = selectedDataset.value.ragflow_dataset_id || selectedDataset.value.id
  if (!datasetId) return

  aiAnalyzing.value = true
  try {
    const response = await axios.post(`/api/portal/ragflow/datasets/${datasetId}/ai-analyze`)
    const resData = apiData(response)
    if (resData) {
      if (resData.description) form.value.description = resData.description
      if (resData.tags) form.value.tagsText = resData.tags
      if (resData.notes) form.value.notes = resData.notes
      showToast('AI 智能分析完成，已自动填充元数据', 'success')
    }
  } catch (err) {
    showToast(extractError(err) || 'AI 分析失败，请重试', 'error')
  } finally {
    aiAnalyzing.value = false
  }
}

// 获取知识库列表
const fetchDatasets = async () => {
  if (!isKnowledgeEnabled.value) {
    datasets.value = []
    engineStatus.value = 'disconnected'
    loading.value = false
    return
  }
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
    const docs = apiData(response)
    documentsMap.value[dsId] = docs

    // 更新对应知识库的文档数量计数
    const dataset = datasets.value.find(d => (d.ragflow_dataset_id || d.id) === dsId)
    if (dataset) {
      dataset.doc_count = docs.length
      dataset.document_count = docs.length
    }

    // 自动更新并同步清理 parsingDocIds
    const nextParsing = { ...parsingDocIds.value }
    let updated = false
    for (const d of docs) {
      if (nextParsing[d.id] && getDocStatus(d) !== 'parsing') {
        delete nextParsing[d.id]
        updated = true
      }
    }
    if (updated) {
      parsingDocIds.value = nextParsing
    }

    // 自动触发或检查是否需要轮询
    checkAndStartDatasetPolling(dsId)
  } catch (err) {
    showToast(extractError(err), 'error')
    documentsMap.value[dsId] = []
  } finally {
    documentsLoadingMap.value[dsId] = false
  }
}

const datasetPermissions = ref<{ users: any[], roles: any[] }>({ users: [], roles: [] })
const loadingPermissions = ref(false)

const fetchDatasetPermissions = async (datasetId: string) => {
  loadingPermissions.value = true
  datasetPermissions.value = { users: [], roles: [] }
  try {
    const response = await axios.get(`/api/portal/ragflow/datasets/${datasetId}/permissions`)
    datasetPermissions.value = response.data?.data || { users: [], roles: [] }
  } catch (err) {
    console.error('获取知识库权限分配失败:', err)
  } finally {
    loadingPermissions.value = false
  }
}

// 选中知识库节点
const selectDatasetNode = (dataset: KnowledgeBase) => {
  selectedType.value = 'dataset'
  const dsId = dataset.ragflow_dataset_id || dataset.id || ''
  selectedId.value = dsId
  selectedDataset.value = dataset
  selectedDocument.value = null
  toggleDatasetExpand(dataset, true)
  if (dsId) {
    fetchDatasetPermissions(dsId)
  }
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
    checkAndStartDatasetPolling(dsId)
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

const triggerDeleteDataset = (dataset: KnowledgeBase) => {
  deletingDataset.value = dataset
  const dsId = dataset.ragflow_dataset_id || dataset.id
  if (dsId) {
    // 异步静默拉取该知识库的最新授权记录，以便在确认弹窗中即时呈现正确的同步警告
    fetchDatasetPermissions(dsId)
  }
}

const deleteDatasetMessage = computed(() => {
  if (!deletingDataset.value) return ''
  const name = deletingDataset.value.platform_name || deletingDataset.value.name
  
  // 检查当前拉取出来的 datasetPermissions 中是否有授权角色或用户
  const hasPerms = datasetPermissions.value.users?.length > 0 || datasetPermissions.value.roles?.length > 0
  
  if (deletingDataset.value.is_missing_in_ragflow) {
    let baseMsg = '检测到该知识库已在 RAGFlow 端不存在或已被物理删除，确定清理云枢平台本地残留的相关配置和元数据信息吗？'
    if (hasPerms) {
      baseMsg += '（⚠️ 该知识库当前已授权的角色/用户访问权限关系也将被同步清除）'
    }
    return baseMsg
  } else {
    let baseMsg = `确定真实删除知识库「${name}」吗？RAGFlow 侧相应集群数据也将同步卸载，此操作不可逆。`
    if (hasPerms) {
      baseMsg += '（⚠️ 注意：该知识库当前已授权给角色/用户，删除后系统关联的全部授权记录将被同步擦除清理）'
    }
    return baseMsg
  }
})

const confirmDeleteDataset = async () => {
  // 如果当前删除的是在 RAGFlow 中已失效的配置，允许跳过引擎连接校验直接清理
  if (!isEngineReady.value && !deletingDataset.value?.is_missing_in_ragflow) {
    showToast('知识库引擎未连接，暂不能删除知识库', 'warning')
    return
  }
  if (!deletingDataset.value) return
  deleting.value = true
  try {
    const id = deletingDataset.value.ragflow_dataset_id || deletingDataset.value.id
    const isMissing = deletingDataset.value.is_missing_in_ragflow
    await axios.delete('/api/portal/ragflow/datasets', { data: { ids: [id] } })
    showToast(isMissing ? '本地失效配置已清理' : '知识库已删除', 'success')
    
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
  } finally {
    deleting.value = false
  }
}

const onFileChange = (event: Event) => {
  const input = event.target as HTMLInputElement
  if (input.files) {
    const files = Array.from(input.files)
    const newFiles = files.filter(f => !uploadFiles.value.some(existing => existing.name === f.name && existing.size === f.size))
    uploadFiles.value = [...uploadFiles.value, ...newFiles]
  }
}

const uploadDocument = async () => {
  if (!isEngineReady.value) {
    showToast('知识库引擎未连接，暂不能上传文档', 'warning')
    return
  }
  if (uploadFiles.value.length === 0 || !selectedDatasetId.value) {
    showToast('请先选择要上传的文件', 'warning')
    return
  }
  uploading.value = true
  
  const total = uploadFiles.value.length
  let successCount = 0
  let failCount = 0
  
  for (let i = 0; i < total; i++) {
    const file = uploadFiles.value[i]
    if (!file) continue
    uploadProgressText.value = `正在上传并解析: ${i + 1} / ${total} (${file.name})`
    
    const payload = new FormData()
    payload.append('file', file)
    
    try {
      const response = await axios.post(`/api/portal/ragflow/datasets/${selectedDatasetId.value}/documents/upload`, payload)
      const resData = response.data?.data || {}
      const docId = resData.id
      
      if (docId) {
        parsingDocIds.value = { ...parsingDocIds.value, [docId]: true }
        try {
          await axios.post(`/api/portal/ragflow/datasets/${selectedDatasetId.value}/documents/parse`, {
            ids: [docId]
          })
        } catch (parseErr) {
          const nextParsing = { ...parsingDocIds.value }
          delete nextParsing[docId]
          parsingDocIds.value = nextParsing
          console.error(`文档 ${file.name} 自动解析失败:`, parseErr)
        }
      }
      successCount++
    } catch (err) {
      failCount++
      console.error(`文档 ${file.name} 上传失败:`, err)
    }
  }
  
  if (failCount === 0) {
    showToast(`成功批量上传并触发解析了 ${successCount} 个文档`, 'success')
  } else if (successCount > 0) {
    showToast(`批量上传完成：成功 ${successCount} 个，失败 ${failCount} 个`, 'warning')
  } else {
    showToast('全部文档上传失败，请检查网络或知识库配置', 'error')
  }
  
  clearAllUploadFiles()
  await fetchDocumentsForDataset(selectedDatasetId.value)
  uploading.value = false
  uploadProgressText.value = ''
}

const confirmDeleteDocument = async () => {
  if (!isEngineReady.value) {
    showToast('知识库引擎未连接，暂不能删除文档', 'warning')
    return
  }
  if (!deletingDocument.value || !selectedDatasetId.value) return
  deleting.value = true
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
  } finally {
    deleting.value = false
  }
}

/** 已触发解析、等待 RAGFlow 返回终态（防重复点击） */
const parsingDocIds = ref<Record<string, boolean>>({})

// 基于知识库维度的自适应渐进退避轮询机制
const datasetPollTimers = new Map<string, ReturnType<typeof setTimeout>>()
const datasetPollCounts = new Map<string, number>()

const stopDatasetPolling = (dsId: string) => {
  const timer = datasetPollTimers.get(dsId)
  if (timer) {
    clearTimeout(timer)
    datasetPollTimers.delete(dsId)
  }
  datasetPollCounts.delete(dsId)
}

const checkAndStartDatasetPolling = (dsId: string) => {
  const docs = documentsMap.value[dsId] || []
  const hasParsing = docs.some(d => getDocStatus(d) === 'parsing')
  
  if (hasParsing) {
    startDatasetStatusPolling(dsId)
  } else {
    stopDatasetPolling(dsId)
  }
}

const startDatasetStatusPolling = (dsId: string) => {
  if (datasetPollTimers.has(dsId)) return
  datasetPollCounts.set(dsId, 0)
  
  const poll = async () => {
    let count = datasetPollCounts.get(dsId) || 0
    count += 1
    datasetPollCounts.set(dsId, count)
    
    try {
      await fetchDocumentsForDataset(dsId)
      
      // 同步当前选中的文档详情信息
      const currentDocument = selectedDocument.value
      if (currentDocument && selectedDatasetId.value === dsId) {
        const updatedDoc = (documentsMap.value[dsId] || []).find(d => d.id === currentDocument.id)
        if (updatedDoc) {
          selectedDocument.value = updatedDoc
        }
      }
      
      const docs = documentsMap.value[dsId] || []
      const stillHasParsing = docs.some(d => getDocStatus(d) === 'parsing')
      
      if (!stillHasParsing) {
        stopDatasetPolling(dsId)
        return
      }
      
      const maxPolls = 60 // 约 5-6 分钟
      if (count >= maxPolls) {
        stopDatasetPolling(dsId)
        showToast('文档解析状态轮询超时，请手动刷新查看', 'warning')
        return
      }
      
      // 自适应渐进退避间隔：1~5次3秒，6~20次6秒，21次以上15秒
      let interval = 3000
      if (count > 20) {
        interval = 15000
      } else if (count > 5) {
        interval = 6000
      }
      
      const timer = setTimeout(poll, interval)
      datasetPollTimers.set(dsId, timer)
    } catch {
      const maxPolls = 60
      if (count >= maxPolls) {
        stopDatasetPolling(dsId)
      } else {
        const timer = setTimeout(poll, 10000)
        datasetPollTimers.set(dsId, timer)
      }
    }
  }
  
  const timer = setTimeout(poll, 3000)
  datasetPollTimers.set(dsId, timer)
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
  } catch (err) {
    const nextParsing = { ...parsingDocIds.value }
    delete nextParsing[docId]
    parsingDocIds.value = nextParsing
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
  // "0" / "UNSTART": unparsed, "1" / "DONE": completed/parsed, "2" / "RUNNING": parsing, "3" / "FAIL": failed
  const run = String(doc.run ?? '').toUpperCase()
  if (run === 'RUNNING' || run === '2') return 'parsing'
  if (run === 'DONE' || run === '1') return 'parsed'
  if (run === 'FAIL' || run === '3') return 'failed'
  if (run === 'UNSTART' || run === '0') return 'unparsed'
  
  // Fallback to doc.status only if run is completely unavailable
  if (doc.run === undefined || doc.run === null) {
    const status = String(doc.status ?? '').toUpperCase()
    // Note: status '1' in RAGFlow means file is active/enabled. DO NOT map '1' or '0' to parsing status here!
    if (status === 'COMPLETED' || status === 'PARSED') return 'parsed'
    if (status === 'RUNNING' || status === 'PARSING') return 'parsing'
    if (status === 'FAILED') return 'failed'
  }
  
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
const uploading = ref(false)
const uploadProgressText = ref('')
const isDragOver = ref(false)
const fileInputRef = ref<HTMLInputElement | null>(null)
const uploadFiles = ref<File[]>([])

const triggerFileSelect = () => {
  if (uploading.value) return
  fileInputRef.value?.click()
}

const clearAllUploadFiles = () => {
  uploadFiles.value = []
  if (fileInputRef.value) {
    fileInputRef.value.value = ''
  }
}

const removeUploadFile = (index: number) => {
  uploadFiles.value.splice(index, 1)
  if (uploadFiles.value.length === 0 && fileInputRef.value) {
    fileInputRef.value.value = ''
  }
}

const onFileDrop = (e: DragEvent) => {
  isDragOver.value = false
  if (uploading.value) return
  const files = e.dataTransfer?.files
  if (files && files.length > 0) {
    const fileList = Array.from(files)
    const newFiles = fileList.filter(f => !uploadFiles.value.some(existing => existing.name === f.name && existing.size === f.size))
    uploadFiles.value = [...uploadFiles.value, ...newFiles]
  }
}

const getFileExtension = (filename: string) => {
  if (!filename) return 'FILE'
  const idx = filename.lastIndexOf('.')
  return idx !== -1 ? filename.slice(idx + 1).toLowerCase() : 'FILE'
}

const getFileIconClass = (filename: string) => {
  const ext = getFileExtension(filename)
  switch (ext) {
    case 'pdf': return 'bg-red-500 shadow-[0_0_4px_#ef4444]'
    case 'doc':
    case 'docx': return 'bg-blue-500 shadow-[0_0_4px_#3b82f6]'
    case 'xls':
    case 'xlsx': return 'bg-emerald-500 shadow-[0_0_4px_#10b981]'
    case 'ppt':
    case 'pptx': return 'bg-orange-500 shadow-[0_0_4px_#f97316]'
    case 'txt':
    case 'md': return 'bg-gray-500 shadow-[0_0_4px_#6b7280]'
    default: return 'bg-primary shadow-[0_0_4px_#1e40af]'
  }
}

const formatFileSize = (bytes: number) => {
  if (bytes === 0) return '0 Bytes'
  const k = 1024
  const sizes = ['Bytes', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

const deleting = ref(false)
const refreshingDocs = ref(false)
const handleManualRefreshDocs = async () => {
  const dsId = selectedDataset.value?.ragflow_dataset_id || selectedDataset.value?.id
  if (!dsId) return
  refreshingDocs.value = true
  try {
    await fetchDocumentsForDataset(dsId)
    showToast('已刷新当前文档列表及状态', 'success')
  } catch (err) {
    showToast(extractError(err), 'error')
  } finally {
    refreshingDocs.value = false
  }
}

const reparsingDocId = ref('')
const handleReparseDocument = async (doc: any) => {
  if (!isEngineReady.value) {
    showToast('知识库引擎未连接，暂不能重新解析', 'warning')
    return
  }
  reparsingDocId.value = doc.id
  try {
    await axios.post(`/api/portal/ragflow/datasets/${selectedDatasetId.value}/documents/parse`, {
      ids: [doc.id]
    })
    showToast('已重新触发该文档的解析任务', 'success')
    parsingDocIds.value = { ...parsingDocIds.value, [doc.id]: true }
    await fetchDocumentsForDataset(selectedDatasetId.value)
  } catch (err) {
    showToast(extractError(err), 'error')
  } finally {
    reparsingDocId.value = ''
  }
}

onUnmounted(() => {
  for (const dsId of datasetPollTimers.keys()) {
    stopDatasetPolling(dsId)
  }
})

onMounted(async () => {
  await fetchRagFlowConfig()
  await fetchDatasets()
})
</script>

<template>
  <div class="space-y-6">
    <div
      v-if="!isKnowledgeEnabled"
      class="bg-amber-50 border border-amber-200 rounded-xl p-4 flex items-start gap-3 shadow-sm"
    >
      <div class="w-8 h-8 rounded-full bg-amber-100 flex items-center justify-center text-amber-600 border border-amber-200 shrink-0">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
      </div>
      <div>
        <h4 class="text-sm font-bold text-amber-900">知识库功能未开启</h4>
        <p class="text-xs text-amber-700 mt-1">请在系统配置 → 知识库设置中开启「knowledge_base_enabled」开关后，再使用知识库管理与文档解析能力。</p>
      </div>
    </div>

    <!-- Header Section -->
    <div class="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-white p-5 rounded-2xl border border-gray-200/80 shadow-sm">
      <div>
        <h1 class="text-2xl font-bold text-gray-900 tracking-tight">知识库管理</h1>
        <p class="text-sm text-gray-500 mt-1">管理 RAGFlow 知识库、平台扩展元数据及一体化文档解析。</p>
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
                    class="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 font-semibold group-hover:hidden transition-all duration-150"
                  >
                    {{ dataset.doc_count ?? dataset.document_count ?? 0 }}
                  </span>
                  <span
                    v-else
                    class="text-[10px] px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 group-hover:hidden transition-all duration-150"
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
                      @click.stop="triggerDeleteDataset(dataset)"
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
                        class="w-1.5 h-1.5 rounded-full shrink-0 transition-all group-hover/doc:hidden"
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
              <div v-if="selectedDataset.platform_description || selectedDataset.description" class="mt-3 pl-3.5 border-l-3 border-primary bg-gray-50/80 py-2.5 pr-4 rounded-r-xl max-w-3xl">
                <p class="text-sm text-gray-600 leading-relaxed italic">
                  "{{ selectedDataset.platform_description || selectedDataset.description }}"
                </p>
              </div>
              <p v-else class="text-xs text-gray-400 italic mt-2.5 select-none">暂无对该知识库的描述说明</p>
            </div>
            <div class="flex items-center gap-2 shrink-0">
              <button
                v-if="!selectedDataset.is_missing_in_ragflow"
                class="p-2 border border-gray-200 rounded-xl hover:bg-gray-50 transition-all flex items-center justify-center text-gray-500 hover:text-primary hover:border-primary/30 shrink-0"
                title="刷新文档及状态"
                :disabled="refreshingDocs"
                @click="handleManualRefreshDocs"
              >
                <svg 
                  class="w-4 h-4" 
                  :class="{ 'animate-spin': refreshingDocs }" 
                  fill="none" 
                  stroke="currentColor" 
                  stroke-width="2"
                  viewBox="0 0 24 24"
                >
                  <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99" />
                </svg>
              </button>
              <button
                v-if="canEdit && !isReadOnlyDataset(selectedDataset)"
                class="px-4 py-2 text-sm font-medium border border-gray-200 rounded-xl hover:bg-gray-50 transition-all flex items-center gap-1.5"
                @click="openEdit(selectedDataset)"
              >
                <svg class="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                </svg>
                编辑
              </button>
            </div>
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
          <div class="grid grid-cols-1 md:grid-cols-2 gap-6 pt-2">
            <div class="space-y-2.5">
              <div class="flex items-center gap-1.5 text-gray-400">
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 7h.01M6 20h12a2 2 0 002-2V8a2 2 0 00-2-2H6a2 2 0 00-2 2v10a2 2 0 002 2z" />
                </svg>
                <h3 class="text-xs font-semibold uppercase tracking-wider">分类标签</h3>
              </div>
              <div v-if="selectedDataset.tags?.length" class="flex flex-wrap gap-1.5">
                <span
                  v-for="tag in selectedDataset.tags"
                  :key="tag"
                  class="px-2.5 py-1 text-xs rounded-lg bg-blue-50/60 text-blue-700 border border-blue-100/60 font-medium transition-all hover:bg-blue-100/50 select-none cursor-default"
                >
                  {{ tag }}
                </span>
              </div>
              <span v-else class="text-sm text-gray-400 italic select-none block pt-1">暂无分类标签</span>
            </div>

            <div class="space-y-2.5">
              <div class="flex items-center gap-1.5 text-gray-400">
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                </svg>
                <h3 class="text-xs font-semibold uppercase tracking-wider">平台备注说明</h3>
              </div>
              <p class="text-sm text-gray-600 bg-amber-50/20 p-3.5 rounded-xl border border-amber-100/50 leading-relaxed">
                {{ selectedDataset.notes || '暂无备注说明。' }}
              </p>
            </div>
          </div>

          <!-- 权限分配说明卡片 -->
          <div class="space-y-2 bg-gray-50/20 p-4 rounded-xl border border-gray-150">
            <div class="flex items-center justify-between">
              <h3 class="text-xs font-semibold text-gray-400 uppercase tracking-wider select-none">系统授权分配详情</h3>
              <span v-if="loadingPermissions" class="text-xs text-gray-400 animate-pulse flex items-center gap-1 select-none">
                <span class="w-2.5 h-2.5 rounded-full border border-gray-300 border-t-transparent animate-spin"></span>
                正在读取权限分配...
              </span>
            </div>
            
            <div class="text-xs space-y-3 mt-1.5">
              <!-- 可见性基本说明 -->
              <p class="text-gray-500 leading-normal select-none">
                公开级别为 
                <span class="font-bold text-gray-800 uppercase px-1.5 py-0.5 rounded bg-gray-100 text-[10px]">{{ selectedDataset.visibility || 'Private' }}</span>。
                <span v-if="selectedDataset.visibility === 'public'">此知识库全平台公开，所有配置该权限的角色或平台用户均有权进行检索。</span>
                <span v-else-if="selectedDataset.visibility === 'team'">此知识库部门公开，创建者、系统管理员以及业务归属部门（{{ selectedDataset.owner || '未指派' }}）的用户有权访问。</span>
                <span v-else>此知识库为私有，仅创建者、系统管理员以及下方被额外明确指派了权限的角色/用户有权进行访问。</span>
              </p>

              <!-- 被指派的用户/角色列表 -->
              <div v-if="!loadingPermissions && (datasetPermissions.users.length || datasetPermissions.roles.length)" class="flex flex-col gap-2.5 pt-2 border-t border-gray-100">
                <div v-if="datasetPermissions.roles.length" class="flex items-start gap-2">
                  <span class="text-gray-400 font-medium shrink-0 mt-0.5 select-none">授权角色:</span>
                  <div class="flex flex-wrap gap-1.5">
                    <span 
                      v-for="r in datasetPermissions.roles" 
                      :key="r.code"
                      class="px-2 py-0.5 rounded bg-blue-50 text-blue-700 font-semibold border border-blue-100/50 flex items-center gap-1 select-none"
                    >
                      <svg class="w-3 h-3 text-blue-500 shrink-0" fill="currentColor" viewBox="0 0 20 20">
                        <path d="M10.394 2.08a1 1 0 00-.788 0l-7 3a1 1 0 000 1.84L5.25 8.051a.999.999 0 01.356-.257l4-1.714a1 1 0 11.788 1.838L7.667 9.088l1.94.831a1 1 0 00.787 0l7-3a1 1 0 000-1.838l-7-3zM3.31 9.397L5 10.12v4.102a8.969 8.969 0 00-1.05-.174 1 1 0 01-.89-.89 11.115 11.115 0 01.25-3.762zM9.3 16.573A9.026 9.026 0 007 14.935v-3.957l1.818.78a3 3 0 002.364 0l5.508-2.361a11.026 11.026 0 01.25 3.762 1 1 0 01-.89.89 8.968 8.968 0 00-5.35 2.524 1 1 0 01-1.4 0z" />
                      </svg>
                      {{ r.name }} ({{ r.code }})
                    </span>
                  </div>
                </div>
                
                <div v-if="datasetPermissions.users.length" class="flex items-start gap-2">
                  <span class="text-gray-400 font-medium shrink-0 mt-0.5 select-none">授权用户:</span>
                  <div class="flex flex-wrap gap-1.5">
                    <span 
                      v-for="u in datasetPermissions.users" 
                      :key="u.user_name"
                      class="px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 font-semibold border border-emerald-100/50 flex items-center gap-1 select-none"
                    >
                      <svg class="w-3 h-3 text-emerald-500 shrink-0" fill="currentColor" viewBox="0 0 20 20">
                        <path fill-rule="evenodd" d="M10 9a3 3 0 100-6 3 3 0 000 6zm-7 9a7 7 0 1114 0H3z" clip-rule="evenodd" />
                      </svg>
                      {{ u.real_name || u.user_name }} ({{ u.user_name }})
                    </span>
                  </div>
                </div>
              </div>
              
              <div v-else-if="!loadingPermissions" class="text-gray-400 italic pt-2 border-t border-gray-100 select-none">
                未分配额外的用户或角色角色访问权限。
              </div>
            </div>
          </div>

          <!-- Document Upload Region -->
          <div class="space-y-3">
            <h3 class="text-sm font-semibold text-gray-800">上传新文档到此知识库</h3>
            <div
              v-if="canUpload && !selectedDataset.is_missing_in_ragflow && !isReadOnlyDataset(selectedDataset)"
              class="border-2 border-dashed rounded-2xl p-6 transition-all duration-300 flex flex-col items-center justify-center bg-gray-50/30 group relative overflow-hidden"
              :class="[
                isDragOver ? 'border-primary bg-primary/5 scale-[1.01]' : 'border-gray-300 hover:border-primary',
                uploading ? 'pointer-events-none' : ''
              ]"
              @dragover.prevent="isDragOver = true"
              @dragleave.prevent="isDragOver = false"
              @drop.prevent="onFileDrop"
            >
              <!-- 隐藏的真实多文件输入框 -->
              <input
                ref="fileInputRef"
                type="file"
                multiple
                class="hidden"
                accept=".pdf,.docx,.doc,.txt,.csv,.xlsx,.xls,.pptx,.ppt,.md"
                :disabled="uploading || !isEngineReady"
                @change="onFileChange"
              />

              <!-- 上传中全局模糊遮罩 -->
              <div
                v-if="uploading"
                class="absolute inset-0 bg-white/95 backdrop-blur-sm flex flex-col items-center justify-center rounded-2xl transition-all duration-300 z-10 p-4"
              >
                <span class="w-9 h-9 rounded-full border-2 border-primary border-t-transparent animate-spin"></span>
                <p class="text-xs font-bold text-gray-800 mt-4 text-center animate-pulse leading-relaxed">
                  {{ uploadProgressText || '正在上传文件，请稍候...' }}
                </p>
              </div>

              <!-- 1. 未选择文件时的引导界面 -->
              <div 
                v-if="uploadFiles.length === 0"
                class="w-full flex flex-col items-center justify-center py-5 cursor-pointer select-none"
                @click="triggerFileSelect"
              >
                <div class="w-12 h-12 rounded-full bg-primary/5 flex items-center justify-center text-primary group-hover:scale-110 transition-transform duration-300">
                  <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                  </svg>
                </div>
                <p class="text-sm font-medium text-gray-700 mt-3">
                  将一个或多个文件拖拽至此处，或 <span class="text-primary hover:underline font-semibold">点击上传</span>
                </p>
                <p class="text-xs text-gray-400 mt-1.5">支持多文件上传，类型包含 PDF, DOCX, TXT, MD, XLSX 等</p>
              </div>

              <!-- 2. 已选择文件时的批量卡片队列展示 -->
              <div v-else class="w-full space-y-4">
                <div class="flex items-center justify-between text-xs text-gray-400 border-b pb-2 select-none">
                  <span>待上传队列 ({{ uploadFiles.length }} 个文件)</span>
                  <button class="text-red-500 hover:underline font-semibold" @click="clearAllUploadFiles">清空全部</button>
                </div>
                
                <!-- 待上传文件滚动卡片容器 -->
                <div class="max-h-[160px] overflow-y-auto pr-1 space-y-2 custom-scrollbar">
                  <div 
                    v-for="(f, fIdx) in uploadFiles" 
                    :key="fIdx"
                    class="flex items-center gap-3 p-2.5 bg-white rounded-xl border border-gray-150 shadow-sm relative group/card"
                  >
                    <!-- 文件后缀高亮块 -->
                    <div 
                      class="w-8 h-8 rounded-lg flex items-center justify-center text-white shrink-0 font-bold text-[9px] uppercase select-none"
                      :class="getFileIconClass(f.name)"
                    >
                      {{ getFileExtension(f.name) }}
                    </div>
                    <!-- 文件名与大小 -->
                    <div class="flex-1 min-w-0">
                      <p class="text-xs font-semibold text-gray-800 truncate" :title="f.name">
                        {{ f.name }}
                      </p>
                      <p class="text-[10px] text-gray-400 mt-0.5 select-none">
                        {{ formatFileSize(f.size) }}
                      </p>
                    </div>
                    <!-- 移出队列按钮 -->
                    <button 
                      class="p-1 rounded-full text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors"
                      title="移出队列"
                      @click="removeUploadFile(fIdx)"
                    >
                      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  </div>
                </div>

                <!-- 动作按钮区 -->
                <div class="flex items-center justify-center gap-3 pt-2">
                  <button
                    class="px-4 py-2 border border-gray-200 rounded-xl hover:bg-gray-50 text-gray-600 text-xs font-semibold transition-all"
                    @click="triggerFileSelect"
                  >
                    继续添加文件
                  </button>
                  <button
                    class="px-5 py-2 rounded-xl bg-primary hover:bg-primary/90 text-white text-xs font-semibold shadow-md shadow-primary/10 hover:shadow-lg transition-all flex items-center justify-center gap-1.5 disabled:opacity-50"
                    :disabled="uploading || !isEngineReady"
                    @click="uploadDocument"
                  >
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                    </svg>
                    <span>确认批量上传解析</span>
                  </button>
                </div>
              </div>
            </div>
            <div v-else class="rounded-xl bg-gray-50/50 p-4 border text-center text-sm text-gray-400 select-none">
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

          <!-- RAG 解析状态诊断看板 -->
          <div 
            v-if="selectedDocument"
            class="p-4 rounded-xl border select-none"
            :class="{
              'bg-blue-50/20 border-blue-100': getDocStatus(selectedDocument) === 'parsing',
              'bg-red-50/20 border-red-100': getDocStatus(selectedDocument) === 'failed',
              'bg-emerald-50/20 border-emerald-100': getDocStatus(selectedDocument) === 'parsed',
              'bg-gray-50/30 border-gray-150': getDocStatus(selectedDocument) === 'unparsed'
            }"
          >
            <div class="flex items-start justify-between gap-4">
              <div class="space-y-1.5 flex-1 min-w-0">
                <span class="text-xs font-bold uppercase tracking-wider block" :class="{
                  'text-blue-600': getDocStatus(selectedDocument) === 'parsing',
                  'text-red-600': getDocStatus(selectedDocument) === 'failed',
                  'text-emerald-600': getDocStatus(selectedDocument) === 'parsed',
                  'text-gray-400': getDocStatus(selectedDocument) === 'unparsed'
                }">
                  RAG 解析状态诊断
                </span>
                
                <!-- 解析中：显示进度条 -->
                <div v-if="getDocStatus(selectedDocument) === 'parsing'" class="space-y-2 mt-2">
                  <div class="flex items-center justify-between text-xs">
                    <span class="text-gray-500 font-medium">切片构建进度：</span>
                    <span class="font-bold text-blue-600">{{ ((selectedDocument.progress ?? 0) * 100).toFixed(1) }}%</span>
                  </div>
                  <div class="w-full bg-blue-100/50 rounded-full h-2 overflow-hidden">
                    <div 
                      class="bg-blue-500 h-2 rounded-full transition-all duration-500 ease-out"
                      :style="{ width: `${Math.min(100, (selectedDocument.progress ?? 0) * 100)}%` }"
                    ></div>
                  </div>
                  <p class="text-[11px] text-blue-500/80 font-mono italic mt-1.5 truncate" :title="selectedDocument.progress_msg">
                    实时状态: {{ selectedDocument.progress_msg || '正在启动解析器...' }}
                  </p>
                </div>

                <!-- 解析失败：显示红字报错 -->
                <div v-else-if="getDocStatus(selectedDocument) === 'failed'" class="space-y-1.5 mt-2">
                  <div class="flex items-start gap-1.5 text-xs text-red-600 bg-red-50/50 p-2.5 rounded-lg border border-red-100/30">
                    <svg class="w-4 h-4 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                    </svg>
                    <div class="break-all font-mono leading-normal">
                      <strong>解析失败原因：</strong>
                      {{ selectedDocument.progress_msg || '未知外部切片解析故障。请确保文件可读且非加密 PDF。' }}
                    </div>
                  </div>
                </div>

                <!-- 解析完成 -->
                <div v-else-if="getDocStatus(selectedDocument) === 'parsed'" class="text-xs text-emerald-600/80 mt-2 flex items-center gap-1.5">
                  <svg class="w-4 h-4 text-emerald-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7" />
                  </svg>
                  <span>该文档已顺利完成切片构建与向量化，当前可提供高质量的语义召回服务。</span>
                </div>

                <!-- 未解析 -->
                <div v-else class="text-xs text-gray-500 mt-2 flex items-center gap-1.5">
                  <svg class="w-4 h-4 text-gray-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <span>文档处于就绪态，尚未执行分块解析。请点击上方按钮触发解析。</span>
                </div>
              </div>

              <!-- 右侧独立控制：失败时的一键重新解析 -->
              <div v-if="getDocStatus(selectedDocument) === 'failed' && !isReadOnlyDataset(selectedDataset)" class="shrink-0 self-center">
                <button
                  class="px-3 py-1.5 rounded-lg border border-red-200 hover:border-red-300 bg-white hover:bg-red-50 text-red-600 hover:text-red-700 text-xs font-semibold shadow-sm transition-all flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed"
                  :disabled="reparsingDocId === selectedDocument.id || !isEngineReady"
                  @click="handleReparseDocument(selectedDocument)"
                >
                  <svg 
                    class="w-3.5 h-3.5" 
                    :class="{ 'animate-spin': reparsingDocId === selectedDocument.id }"
                    fill="none" 
                    stroke="currentColor" 
                    viewBox="0 0 24 24"
                  >
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 1121.21 15H18" />
                  </svg>
                  <span>重新解析</span>
                </button>
              </div>
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
          <div class="flex justify-between items-center mb-1.5">
            <label class="block text-xs font-semibold text-gray-400">描述说明</label>
            <button
              v-if="selectedDataset && (selectedDataset.doc_count ?? selectedDataset.document_count ?? 0) > 0"
              class="flex items-center gap-1 text-xs text-primary hover:text-primary-hover font-semibold transition-colors disabled:opacity-50"
              :disabled="aiAnalyzing"
              @click="analyzeMetadataByAI"
            >
              <svg v-if="aiAnalyzing" class="animate-spin h-3.5 w-3.5 text-primary" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
              </svg>
              <span v-else>✨</span>
              <span>{{ aiAnalyzing ? 'AI 分析中...' : 'AI 智能分析元数据' }}</span>
            </button>
          </div>
          <textarea v-model="form.description" class="w-full border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary" rows="2" placeholder="由 AI 自动分析生成或手动输入"></textarea>
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
      :title="deletingDataset.is_missing_in_ragflow ? '清理失效残留配置' : '删除知识库'"
      :message="deleteDatasetMessage"
      :confirm-text="deletingDataset.is_missing_in_ragflow ? '确认清理残留配置' : '确认物理删除'"
      :loading="deleting"
      @confirm="confirmDeleteDataset"
      @cancel="deletingDataset = null"
    />
    <ConfirmModal
      v-if="deletingDocument"
      title="删除文档"
      :message="`确定从当前知识库中删除文档「${deletingDocument.name || deletingDocument.id}」吗？已解析的 Vector Chunks 数据将同步清空，此操作不可逆。`"
      confirm-text="确认删除"
      :loading="deleting"
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
