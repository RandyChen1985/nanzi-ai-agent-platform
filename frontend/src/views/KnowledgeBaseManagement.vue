<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import HoverTip from '../components/HoverTip.vue'
import TagInput from '../components/TagInput.vue'
import { useRoute, useRouter } from 'vue-router'
import axios from '../utils/axios'
import Modal from '../components/Modal.vue'
import ConfirmModal from '../components/ConfirmModal.vue'
import KnowledgeFlowGuideBanner from '../components/knowledge/KnowledgeFlowGuideBanner.vue'
import { useToast } from '../composables/useToast'
import { useUser } from '../composables/useUser'
import { copyToClipboard as copyText } from '../utils/clipboard'
import DocumentViewer from '@/components/embed/DocumentViewer.vue'
import {
  canPreviewWithDocumentViewer,
  resolveDocumentViewerMime,
  PDF_EXTENSIONS,
  getWorkspaceFileExtension,
} from '@/utils/workspaceFilePreview'

const router = useRouter()
const route = useRoute()

type KnowledgeBase = {
  id: string
  ragflow_dataset_id?: string
  name?: string
  description?: string
  platform_name?: string
  platform_description?: string
  owner?: string
  // 业务归属部门：仅用于左侧树分组与过滤，不参与权限判定
  owner_dept_id?: number | null
  /**
   * 可见范围（真实权限语义，见后端 PermissionService）：
   * public = 授权可见（需授权）；team = TEAM 级（归属部门含下级自动只读）；private = 个人私有（仅创建人）
   */
  visibility?: string
  tags?: string[]
  notes?: string
  doc_count?: number
  document_count?: number
  chunk_count?: number
  chunk_method?: string
  embedding_model?: string
  update_time?: string
  updated_at?: string
  status?: string
  is_missing_in_ragflow?: boolean
  created_by?: string
  /** 归属人（管理权真源）。为空时后端回落 created_by 文本，兼容未回填的老库 */
  owner_user_id?: number
  owner_user_name?: string
  can_write?: boolean
  /** 管理级：改元数据/可见范围/删库/授权。部门成员只有内容级（can_write） */
  can_manage?: boolean
  can_view_chunks?: boolean
  is_read_only?: boolean
  /** 被多少个智能体引用（删除/收窄可见范围前的影响面） */
  agent_ref_count?: number
  agent_refs?: { id: string; name: string; display_name: string; is_enabled: boolean }[]
  local_metadata?: {
    extra_config?: Record<string, unknown> & {
      folder_structure?: Record<string, string[]>
    }
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
  /** RAGFlow 侧是否参与检索；停用后切片保留但不召回 */
  enabled?: boolean | number
}

type RagFlowConfigSummary = {
  api_url: string
  api_key_configured: boolean
  configured: boolean
  knowledge_base_enabled?: boolean
}

/** 只读部门目录节点（来自 /api/portal/departments/options，不含成员数） */
type DeptOptionNode = {
  id: number
  parent_id: number | null
  code: string
  name: string
  path: string
  status: number
  children?: DeptOptionNode[]
}

/** 左侧树的一级节点：部门分组（deptId=null 表示「未关联部门」或平铺模式） */
type DeptDatasetGroup = {
  key: string
  deptId: number | null
  label: string
  path: string
  level: number
  ancestors: number[]
  datasets: KnowledgeBase[]
  /** 含下级部门的库总数 */
  totalCount: number
  hasChildren: boolean
  isUnassigned: boolean
  /** 平铺模式（授权可见 / 个人私有 tab）：不渲染分组头，只列出库 */
  isFlat?: boolean
}

/** 可见范围三档（与后端 PermissionService 的常量一致） */
type VisibilityScope = 'public' | 'team' | 'private'

const VISIBILITY_TABS: { key: VisibilityScope; label: string; hint: string }[] = [
  { key: 'public', label: '授权可见', hint: '默认只有创建人可用，其他人需显式授权' },
  { key: 'team', label: 'TEAM 级', hint: '归属部门（含下级部门）成员自动只读' },
  { key: 'private', label: '个人私有', hint: '仅自己可见，不支持分配授权' },
]

/**
 * 可见范围下拉的选项：下拉里双行展示「名称 + 适用场景」。
 * 顺序按「默认项在前」排，`private` 是新建库的默认值，所以加了「默认」徽标，
 * 避免用户没注意就建出一个只有自己能看到的库。
 */
const VISIBILITY_SCOPE_OPTIONS: {
  key: VisibilityScope
  label: string
  hint: string
  scene: string
  /** 图标路径（heroicons 24 描边风格），用 currentColor 上色 */
  iconPath: string
  /** 图标颜色：与三个 tab / 徽标的配色保持一致，便于扫读时按颜色识别 */
  iconClass: string
  isDefault?: boolean
}[] = [
  {
    key: 'private',
    label: '个人私有',
    hint: '仅自己可见，不能分配授权',
    scene: '草稿、个人笔记、待整理资料',
    // 锁：仅自己能打开
    iconPath:
      'M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z',
    iconClass: 'text-amber-500',
    isDefault: true,
  },
  {
    key: 'team',
    label: 'TEAM 级',
    hint: '归属部门（含下级部门）成员自动只读',
    scene: '部门内部资料、团队对齐',
    // 楼宇：代表部门/组织
    iconPath:
      'M3.75 21h16.5M4.5 3h15M5.25 3v18m13.5-18v18M9 6.75h1.5m-1.5 3h1.5m-1.5 3h1.5m3-6H15m-1.5 3H15m-1.5 3H15M9 21v-3.375c0-.621.504-1.125 1.125-1.125h3.75c.621 0 1.125.504 1.125 1.125V21',
    iconClass: 'text-violet-500',
  },
  {
    key: 'public',
    label: '授权可见',
    hint: '默认只有创建人可用，其他人需显式授权',
    scene: '跨部门共享、需要精确控制名单',
    // 地球：代表跨部门/可对外共享
    iconPath:
      'M12 21a9 9 0 100-18 9 9 0 000 18zM3.6 9h16.8M3.6 15h16.8M12 3c2.4 2.4 3.6 5.4 3.6 9s-1.2 6.6-3.6 9c-2.4-2.4-3.6-5.4-3.6-9S9.6 5.4 12 3z',
    iconClass: 'text-blue-500',
  },
]

/** 可见范围徽标配色：与下拉图标色、权限配置页徽标保持一致 */
const VISIBILITY_BADGE_CLASS: Record<VisibilityScope, string> = {
  public: 'bg-blue-50 text-blue-600 border-blue-100',
  team: 'bg-violet-50 text-violet-600 border-violet-100',
  private: 'bg-amber-50 text-amber-700 border-amber-100',
}

/** 归一化可见范围：空值/未知值按 public 兜底，与后端判定口径保持一致 */
const visibilityOf = (dataset: KnowledgeBase): VisibilityScope => {
  const token = String(dataset.visibility || '').trim().toLowerCase()
  return token === 'team' || token === 'private' ? token : 'public'
}

/** 可见范围的中文标签（详情页/提示文案用） */
const visibilityLabel = (dataset: KnowledgeBase): string => {
  const labels: Record<VisibilityScope, string> = {
    public: '授权可见',
    team: 'TEAM 级',
    private: '个人私有',
  }
  return labels[visibilityOf(dataset)]
}

/** 详情页标题旁的可见范围徽标配色 */
const visibilityBadgeClass = (dataset: KnowledgeBase) =>
  VISIBILITY_BADGE_CLASS[visibilityOf(dataset)]

/** 徽标 hover 提示：取下拉选项的说明，与切换可见范围时看到的是同一句话 */
const visibilityHint = (dataset: KnowledgeBase) =>
  visibilityOptionOf(visibilityOf(dataset)).hint

type VisibilityScopeOption = (typeof VISIBILITY_SCOPE_OPTIONS)[number]

/** 兜底项：字段取值异常时也不让下拉显示空白（与默认值 private 保持一致） */
const FALLBACK_VISIBILITY_OPTION: VisibilityScopeOption = {
  key: 'private',
  label: '个人私有',
  hint: '仅自己可见，不能分配授权',
  scene: '草稿、个人笔记、待整理资料',
  iconPath:
    'M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z',
  iconClass: 'text-amber-500',
  isDefault: true,
}

/** 按档位取下拉选项（未知值回落到默认项） */
const visibilityOptionOf = (scope: string): VisibilityScopeOption =>
  VISIBILITY_SCOPE_OPTIONS.find(option => option.key === scope) ?? FALLBACK_VISIBILITY_OPTION

const { showToast } = useToast()
const { hasPermission, isAdmin } = useUser()

const loading = ref(false)
const syncing = ref(false)
const creatingDataset = ref(false)
const savingMetadata = ref(false)
const engineStatus = ref<'checking' | 'connected' | 'disconnected'>('checking')
const showErrorBanner = ref(true)
const datasets = ref<KnowledgeBase[]>([])
const errorMessage = ref('')
const ragflowConfig = ref<RagFlowConfigSummary | null>(null)

// 部门目录（只读）：用于「业务归属部门」选择器与左侧树部门分组。
// 与部门管理页的 /departments/tree 分开取：知识库编辑者通常没有部门管理权限。
const departmentOptions = ref<DeptOptionNode[]>([])
const departmentOptionsError = ref('')
// 部门分组折叠状态，key 为分组 key（dept:<id> 或 unassigned）
const collapsedDeptGroups = ref<Record<string, boolean>>({})
// 左栏可见范围 tab：授权可见 / TEAM 级 / 个人私有（对应 visibility 三档）
const activeVisibility = ref<VisibilityScope>('public')

// 自定义下拉的展开状态：可见范围（两个弹窗共用）+ 解析算法（创建弹窗）。
// 靠 data 属性判断"点了别处"，避免两个菜单同时展开时互相干扰。
const visibilityMenuOpen = ref(false)
const chunkMenuOpen = ref(false)
const closeFloatingMenusOnOutsideClick = (event: MouseEvent) => {
  const target = event.target as HTMLElement | null
  if (target?.closest('[data-visibility-select]')) return
  if (target?.closest('[data-chunk-select]')) return
  if (target?.closest('[data-dept-select]')) return
  visibilityMenuOpen.value = false
  chunkMenuOpen.value = false
  deptMenuOpen.value = false
}
onMounted(() => document.addEventListener('click', closeFloatingMenusOnOutsideClick))
onUnmounted(() => document.removeEventListener('click', closeFloatingMenusOnOutsideClick))

// 选中状态
const selectedType = ref<'dataset' | 'document' | null>(null)
const selectedId = ref<string>('')
const selectedDataset = ref<KnowledgeBase | null>(null)
const selectedDocument = ref<KnowledgeDocument | null>(null)

// 树状展开和数据加载状态
const expandedDatasetIds = ref<Record<string, boolean>>({})
const documentsMap = ref<Record<string, KnowledgeDocument[]>>({})
const documentsLoadingMap = ref<Record<string, boolean>>({})
const documentsErrorMap = ref<Record<string, boolean>>({})

// 检索过滤
const searchQuery = ref('')

const showCreateModal = ref(false)
const showEditModal = ref(false)
const aiAnalyzing = ref(false)
const customQuestions = ref<{ label: string, query: string }[]>([])
const deletingDataset = ref<KnowledgeBase | null>(null)
const deletingDocument = ref<KnowledgeDocument | null>(null)
const pendingPermissionRemoval = ref<{ type: string; id: number; name: string } | null>(null)
const pendingFolderRemoval = ref<{ dataset: KnowledgeBase; folderName: string } | null>(null)

// 转移归属：知识库的管理权从一个人交给另一个人（离职/调岗）
const showTransferOwnerModal = ref(false)
const transferTarget = ref<KnowledgeBase | null>(null)
const transferCandidates = ref<{ id: number; user_name: string; real_name?: string; can_manage?: boolean }[]>([])
const transferKeyword = ref('')
const transferUserId = ref<number | null>(null)
const transferring = ref(false)
const loadingTransferCandidates = ref(false)
const selectedTransferCandidate = computed(
  () => transferCandidates.value.find((c) => c.id === transferUserId.value) || null,
)

const form = ref({
  name: '',
  description: '',
  chunk_method: 'naive',
  // 业务归属部门：0 表示未关联部门；ownerText 是历史自由文本（只读兜底，不参与新写入）
  ownerDeptId: 0 as number,
  ownerText: '',
  visibility: 'private',
  tags: [] as string[],
  notes: '',
  extraConfigText: '{}'
})

const canCreate = computed(() => hasPermission('element:knowledge:create'))
const canEdit = computed(() => hasPermission('element:knowledge:edit'))
const canDelete = computed(() => hasPermission('element:knowledge:delete'))
const canUpload = computed(() => hasPermission('element:knowledge:upload_document'))
const canDeleteDocument = computed(() => hasPermission('element:knowledge:delete_document'))
const canParse = computed(() => hasPermission('element:knowledge:parse_document'))

// ---------------------------------------------------------------- 操作日志
// 后端已经把「谁上传/删除/解析/改设置/授权/转移」写进专用审计表
// （ai_agent_knowledge_audit_logs），这里只负责按库取回来并翻成人话。
// 「失联」到底是什么意思：平台元数据里有、RAGFlow 里找不到。
// 只写四个字没人能懂，必须讲清成因和处理办法。
const MISSING_DATASET_HINT =
  'RAGFlow 侧已找不到这个知识库：可能被直接在 RAGFlow 里删除了，或 RAGFlow 重建后 ID 变了。' +
  '这里只剩平台保存的名称、描述和标签，文档列表为空，不能上传或解析。' +
  '确认不再需要时，直接删除即可——不会去 RAGFlow 侧执行删除。'

const detailTab = ref<'documents' | 'audit' | 'references'>('documents')
// 「高级设置」默认收起：绝大多数人不需要碰 JSON，展开就先把代价讲清楚
const advancedConfigOpen = ref(false)
const createAdvancedConfigOpen = ref(false)
// 详情页底部那块只读 JSON 同理，默认收起
const detailConfigOpen = ref(false)
const auditLogs = ref<any[]>([])
const auditTotal = ref(0)
const auditPage = ref(1)
const auditLoading = ref(false)
const auditAction = ref('')
const auditCanManage = ref(false)

// ---------- 全平台审计（管理员） ----------
const showGlobalAudit = ref(false)
const globalAuditItems = ref<any[]>([])
const globalAuditTotal = ref(0)
const globalAuditPage = ref(1)
const globalAuditLoading = ref(false)
const globalAuditKeyword = ref('')
const globalAuditStart = ref('')
const globalAuditEnd = ref('')
const globalAuditAction = ref('')
const GLOBAL_AUDIT_PAGE_SIZE = 20

/** 审计表里落到具体库的记录显示库名，而不是一串 uuid */
const datasetNameById = (id: string | null | undefined) => {
  if (!id) return ''
  const found = datasets.value.find((item: any) => (item.ragflow_dataset_id || item.id) === id)
  return found?.platform_name || found?.name || ''
}

const globalAuditFilterParams = () => ({
  keyword: globalAuditKeyword.value.trim() || undefined,
  start: globalAuditStart.value || undefined,
  end: globalAuditEnd.value || undefined,
  action: globalAuditAction.value || undefined,
})

const openGlobalAudit = () => {
  showGlobalAudit.value = true
  globalAuditPage.value = 1
  void fetchGlobalAudit(1)
}

const fetchGlobalAudit = async (page = 1) => {
  globalAuditLoading.value = true
  try {
    const response = await axios.get('/api/portal/ragflow/audit-logs', {
      params: { page, size: GLOBAL_AUDIT_PAGE_SIZE, ...globalAuditFilterParams() },
    })
    const data = response.data?.data || {}
    globalAuditItems.value = data.items || []
    globalAuditTotal.value = data.total || 0
    globalAuditPage.value = page
  } catch (err) {
    showToast(extractError(err), 'error')
  } finally {
    globalAuditLoading.value = false
  }
}

const exportGlobalAudit = async () => {
  try {
    const response = await axios.get('/api/portal/ragflow/audit-logs', {
      params: { export: 'csv', ...globalAuditFilterParams() },
      responseType: 'blob',
    })
    const url = URL.createObjectURL(new Blob([response.data], { type: 'text/csv' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `knowledge-audit-${new Date().toISOString().slice(0, 10)}.csv`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  } catch (err) {
    showToast(extractError(err), 'error')
  }
}

const AUDIT_ACTION_LABELS: Record<string, string> = {
  'datasets:create': '创建知识库',
  'datasets:update_metadata': '修改设置',
  'datasets:delete': '删除知识库',
  'datasets:grant': '新增授权',
  'datasets:revoke': '取消授权',
  'datasets:transfer_owner': '转移归属人',
  'documents:upload': '上传文档',
  'documents:replace': '替换文档',
  'documents:update': '重命名 / 启停文档',
  'documents:delete': '删除文档',
  'documents:parse': '解析文档',
  'chunks:create': '新增切片',
  'chunks:update': '修改切片',
  'chunks:delete': '删除切片',
  'datasets:sync': '从 RAGFlow 同步',
  'datasets:update_layout': '调整文档目录结构',
  'retrieval-test': '检索测试',
}

/** 敏感操作只对管理级可见；部门成员/被授权成员只看文档内容类。 */
const ADMIN_ONLY_AUDIT_ACTIONS = [
  'datasets:create',
  'datasets:update_metadata',
  'datasets:delete',
  'datasets:grant',
  'datasets:revoke',
  'datasets:transfer_owner',
  'datasets:sync',
]
const auditActionOptions = computed(() => {
  const keys = Object.keys(AUDIT_ACTION_LABELS).filter(
    (key) => auditCanManage.value || !ADMIN_ONLY_AUDIT_ACTIONS.includes(key),
  )
  return keys.map((key) => ({ value: key, label: AUDIT_ACTION_LABELS[key] }))
})

const currentDatasetId = () => {
  const dataset = selectedDataset.value
  return dataset ? (dataset.ragflow_dataset_id || dataset.id || '') : ''
}

const loadAuditLogs = async (reset = false) => {
  const datasetId = currentDatasetId()
  if (!datasetId || auditLoading.value) return
  if (reset) {
    auditPage.value = 1
    // 刻意不清空旧列表：切换 tab 会走到这里，先清空会让面板闪一下空白。
    // 请求成功后再整体替换；失败则保留上一次的结果。
  }
  auditLoading.value = true
  try {
    const res = await axios.get(
      `/api/portal/ragflow/datasets/${encodeURIComponent(datasetId)}/audit-logs`,
      {
        params: {
          page: auditPage.value,
          size: 20,
          ...(auditAction.value ? { action: auditAction.value } : {}),
        },
      },
    )
    const data = res.data?.data || {}
    const items = data.items || []
    auditLogs.value = reset ? items : [...auditLogs.value, ...items]
    auditTotal.value = data.total ?? items.length
    auditCanManage.value = data.can_manage === true
    auditPage.value += 1
  } catch (err) {
    console.error('获取操作日志失败:', err)
    // 刷新失败保留旧数据：把用户正在看的内容清空，比不刷新更糟
  } finally {
    auditLoading.value = false
  }
}

/** 把审计 payload 翻成一句人话——原始 JSON 给使用者看没有意义。 */
const auditLogText = (log: any) => {
  const detail = log?.detail || {}
  const who = log?.user_name || '未知用户'
  const count = (list: any) => (Array.isArray(list) ? list.length : 0)
  switch (log?.action) {
    case 'documents:upload':
      return `${who} 上传了「${detail.file_name || '文档'}」`
    case 'documents:delete':
      return `${who} 删除了 ${count(detail.document_ids) || 1} 个文档`
    case 'documents:parse':
      return `${who} 触发了 ${count(detail.document_ids) || 1} 个文档的解析`
    case 'retrieval-test':
      return `${who} 做了检索测试（命中 ${detail.hit_count ?? 0} 段）`
    case 'datasets:create':
      return `${who} 创建了知识库「${detail.name || ''}」`
    case 'datasets:update_metadata':
      return `${who} 修改了知识库设置`
    case 'datasets:delete':
      return `${who} 删除了知识库`
    case 'datasets:grant':
      return `${who} 新增授权：${detail.target_type === 'role' ? '角色' : '用户'} ${count(detail.target_ids)} 项，影响 ${detail.affected_user_count ?? 0} 人`
    case 'datasets:revoke':
      return `${who} 取消了授权，影响 ${detail.affected_user_count ?? 0} 人`
    case 'datasets:transfer_owner':
      return `${who} 转移了知识库归属人`
    case 'datasets:sync':
      return `${who} 从 RAGFlow 同步了知识库（新增 ${detail.sync_result?.created ?? 0}、更新 ${detail.sync_result?.updated ?? 0}）`
    case 'datasets:update_layout':
      return `${who} 调整了文档目录结构`
    default:
      return `${who} 执行了 ${log?.action || '未知操作'}`
  }
}

// 切到日志 tab 就重新拉一次：
// 只在"列表为空"时加载会导致——在文档 tab 传完文件再切回来，看到的还是旧列表，
// 而"这条操作到底记没记上"恰恰是用户切回来的唯一目的。
watch(detailTab, (tab: 'documents' | 'audit' | 'references') => {
  if (tab === 'audit') loadAuditLogs(true)
  if (tab === 'references') void loadDatasetReferences()
})

// 换库要清空，否则会短暂看到上一个库的记录
watch(
  () => currentDatasetId(),
  () => {
    auditLogs.value = []
    auditTotal.value = 0
    auditPage.value = 1
    auditAction.value = ''
    detailConfigOpen.value = false
    agentRefs.value = []
    void loadDatasetQuota()
    if (detailTab.value === 'audit') loadAuditLogs(true)
    if (detailTab.value === 'references') void loadDatasetReferences()
  },
)

// ---------- 发现：看得见但还没权限的公共/部门库 ----------
const showDiscover = ref(false)
const discoverItems = ref<any[]>([])
const discoverTotal = ref(0)
const discoverPage = ref(1)
const discoverLoading = ref(false)
const discoverKeyword = ref('')
const applyingDataset = ref<any>(null)
const applyReason = ref('')
const applySubmitting = ref(false)
const DISCOVER_PAGE_SIZE = 10

const openDiscover = () => {
  showDiscover.value = true
  discoverPage.value = 1
  void fetchDiscover(1)
}

const fetchDiscover = async (page = 1) => {
  discoverLoading.value = true
  try {
    const response = await axios.get('/api/portal/ragflow/datasets/discover', {
      params: {
        page,
        size: DISCOVER_PAGE_SIZE,
        keyword: discoverKeyword.value.trim() || undefined,
      },
    })
    const data = response.data?.data || {}
    discoverItems.value = data.items || []
    discoverTotal.value = data.total || 0
    discoverPage.value = page
  } catch (err) {
    showToast(extractError(err), 'error')
  } finally {
    discoverLoading.value = false
  }
}

const startAccessRequest = (item: any) => {
  applyingDataset.value = item
  applyReason.value = ''
}

const submitAccessRequest = async () => {
  const target = applyingDataset.value
  if (!target || applySubmitting.value) return
  applySubmitting.value = true
  try {
    await axios.post(
      `/api/portal/ragflow/datasets/${encodeURIComponent(target.dataset_id)}/access-requests`,
      { reason: applyReason.value.trim() || null },
    )
    showToast('申请已提交，等待库归属人审批', 'success')
    applyingDataset.value = null
    await fetchDiscover(discoverPage.value)
  } catch (err) {
    showToast(extractError(err), 'error')
  } finally {
    applySubmitting.value = false
  }
}

const discoverUpdatedText = (item: any) => {
  const raw = item?.engine_update_time || item?.updated_at
  if (!raw) return ''
  const ts = typeof raw === 'number' ? (raw > 1e12 ? raw : raw * 1000) : Date.parse(String(raw).replace(' ', 'T'))
  if (!ts || Number.isNaN(ts)) return ''
  const days = Math.floor((Date.now() - ts) / 86400000)
  if (days <= 0) return '今天'
  if (days === 1) return '昨天'
  if (days < 30) return `${days} 天前`
  if (days < 365) return `${Math.floor(days / 30)} 个月前`
  return `${Math.floor(days / 365)} 年前`
}

const discoverStatusLabel = (status: string | null) => {
  if (status === 'pending') return '审批中'
  if (status === 'approved') return '已通过'
  if (status === 'rejected') return '已驳回'
  if (status === 'cancelled') return '已撤回'
  return ''
}

// ---------- 配额用量 ----------
const datasetQuota = ref<any>(null)
const docQuotaRatio = computed(() => {
  const docs = datasetQuota.value?.documents
  if (!docs || !docs.limit || docs.used == null) return 0
  return Math.min(1, Number(docs.used) / Number(docs.limit))
})
const docQuotaTone = computed(() => {
  if (docQuotaRatio.value >= 0.95) return 'bg-red-500 text-red-600'
  if (docQuotaRatio.value >= 0.8) return 'bg-amber-500 text-amber-600'
  return 'bg-emerald-500 text-gray-400'
})

const loadDatasetQuota = async () => {
  const dsId = currentDatasetId()
  datasetQuota.value = null
  if (!dsId) return
  try {
    const response = await axios.get(`/api/portal/ragflow/datasets/${encodeURIComponent(dsId)}/quota`)
    datasetQuota.value = response.data?.data || null
  } catch {
    // 配额只是辅助信息：拿不到就不显示，不影响文档列表
  }
}


const canManagePermissions = computed(() => {
  // 与后端 authorization 同码：element:user:edit（菜单可见不等于有权改授权）
  if (!(isAdmin.value || hasPermission('element:user:edit'))) return false
  // 后端的 GET/POST /permissions 都要求**管理级**写权限（部门成员也不行）。
  return canManageDataset(selectedDataset.value)
})
const canSync = computed(() => canEdit.value)
const isKnowledgeEnabled = computed(() => ragflowConfig.value?.knowledge_base_enabled !== false)
const isEngineReady = computed(() => isKnowledgeEnabled.value && engineStatus.value === 'connected' && !loading.value)
const engineStatusText = computed(() => {
  if (!isKnowledgeEnabled.value) return '功能已关闭'
  if (engineStatus.value === 'checking') return '知识库引擎连接中 ...'
  if (engineStatus.value === 'connected') return '已连接'
  return '未连接'
})

/**
 * 窄屏状态条只放圆点 + 两三个字，完整文案（「知识库引擎连接中 ...」）留给桌面。
 * 直接复用 engineStatusText 会把标题区撑宽，图标按钮那一行又回到换行老问题。
 */
const engineStatusShort = computed(() => {
  if (!isKnowledgeEnabled.value) return '未开启'
  if (engineStatus.value === 'checking') return '连接中'
  if (engineStatus.value === 'connected') return '已连接'
  return '未连接'
})

const selectedDatasetId = computed(() => selectedDataset.value?.ragflow_dataset_id || selectedDataset.value?.id || '')

/**
 * 解析算法选项：说明文案内联到下拉里（第一行名称 + 算法码，第二行说明），
 * 不再在下拉外面挂一条蓝色提示框——选完就得往下看半天才知道它是什么意思。
 */
const CHUNK_METHOD_OPTIONS: { value: string; label: string; code: string; desc: string }[] = [
  {
    value: 'naive',
    label: '通用切分',
    code: 'Naive',
    desc: '适合绝大部分规则文档，按段落与 Token 长度物理切分，简单高效。',
  },
  {
    value: 'qa',
    label: '问答对模型',
    code: 'QA',
    desc: '适合 FAQ 常见问答库、客服指引，自动调用大模型提取匹配的 Q&A 问答对。',
  },
  {
    value: 'table',
    label: '表格切片',
    code: 'Table',
    desc: '适合报表、带密集表格的 PDF 或 Excel，专门保留表格的行列逻辑特征。',
  },
  {
    value: 'laws',
    label: '法律法规',
    code: 'Laws',
    desc: '适合条规文件，按「第 X 条 / 第 X 款」进行层级解析切片。',
  },
  {
    value: 'paper',
    label: '学术论文',
    code: 'Paper',
    desc: '针对论文报告结构设计，自动识别标题和段落，智能排除参考文献等噪音。',
  },
  {
    value: 'resume',
    label: '简历解析',
    code: 'Resume',
    desc: '专门解析个人履历，智能识别并提取教育背景、工作经历等核心属性。',
  },
  {
    value: 'book',
    label: '图书解析',
    code: 'Book',
    desc: '针对长篇连贯文学作品，按大篇幅连续小说段落逻辑进行切片。',
  },
  {
    value: 'manual',
    label: '手动微调',
    code: 'Manual',
    desc: '适合对分块有严苛要求的文件，解析后由人工手动调整切分边界。',
  },
  {
    value: 'one',
    label: '单文件不切分',
    code: 'One',
    desc: '将整个文档作为一个 Chunk，适合短语列表、特定规则名称等无需分割的文件。',
  },
]

/** 兜底项：历史脏值也不让下拉显示空白（显式写字面量，避免下标访问被判为可能 undefined） */
const FALLBACK_CHUNK_OPTION = {
  value: 'naive',
  label: '通用切分',
  code: 'Naive',
  desc: '适合绝大部分规则文档，按段落与 Token 长度物理切分，简单高效。',
}

const chunkMethodOptionOf = (value: string) =>
  CHUNK_METHOD_OPTIONS.find(option => option.value === value) ?? FALLBACK_CHUNK_OPTION

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
// 中英文逗号都认：手动粘贴或 AI 回填经常给中文逗号，
// 只按半角拆会得到一个"财务，报税"这样的整标签。
const normalizeTags = (text: string) =>
  text.split(/[,，\n]/).map(t => t.trim()).filter(Boolean)

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
    ownerDeptId: 0,
    ownerText: '',
    visibility: 'private',
    tags: [] as string[],
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
  // 下拉展开态是弹窗级状态，重新打开时必须收起，避免"上次没关掉的菜单"
  visibilityMenuOpen.value = false
  chunkMenuOpen.value = false
  deptMenuOpen.value = false
  showCreateModal.value = true
}

const openEdit = (dataset: KnowledgeBase) => {
  if (!isEngineReady.value) {
    showToast('知识库引擎未连接，暂不能编辑知识库', 'warning')
    return
  }
  // 从左侧树直接点「编辑」时，详情页可能还停在上一个库：datasetPermissions
  // 会是别人的数据，弹窗里「将失去 N 个授权」的数字就错位了。走 selectDatasetNode
  // 顺带把权限列表刷成这个库的；已经选中时只更新引用，不重复请求。
  const editingDsId = dataset.ragflow_dataset_id || dataset.id || ''
  if (editingDsId && editingDsId !== selectedId.value) {
    selectDatasetNode(dataset)
  } else {
    selectedDataset.value = dataset
  }
  visibilityMenuOpen.value = false
  deptMenuOpen.value = false
  form.value = {
    name: dataset.platform_name || dataset.name || '',
    description: dataset.platform_description || dataset.description || '',
    chunk_method: 'naive',
    ownerDeptId: dataset.owner_dept_id || 0,
    ownerText: dataset.owner || '',
    // 与详情页展示口径一致：元数据缺失 / visibility 为空时按 public 兜底。
    // 原先写 `|| 'private'`，这类库在弹窗里会显示成「个人私有」，用户只改个
    // 名称保存就把它静默改成 private，其他被授权的人瞬间失去访问。
    visibility: visibilityOf(dataset),
    tags: [...(dataset.tags || [])],
    notes: dataset.notes || '',
    extraConfigText: JSON.stringify(dataset.local_metadata?.extra_config || {}, null, 2)
  }

  advancedConfigOpen.value = false

  customQuestions.value = []
  const localExtra = dataset.local_metadata?.extra_config || {}
  if (localExtra.custom_questions && Array.isArray(localExtra.custom_questions)) {
    customQuestions.value = localExtra.custom_questions.map(q => ({
      label: q.label || '',
      query: q.query || ''
    }))
  }
  
  showEditModal.value = true
}

const aiOverwritePending = ref(false)

const analyzeMetadataByAI = async () => {
  if (!selectedDataset.value) return
  const datasetId = selectedDataset.value.ragflow_dataset_id || selectedDataset.value.id
  if (!datasetId) return

  // AI 结果会直接写回表单，已填内容会被静默冲掉，先征得确认
  const hasContent = Boolean(
    form.value.description?.trim() || form.value.tags?.length || form.value.notes?.trim()
  )
  if (hasContent) {
    aiOverwritePending.value = true
    return
  }
  await runAiAnalyze()
}

const confirmAiOverwrite = async () => {
  aiOverwritePending.value = false
  await runAiAnalyze()
}

const runAiAnalyze = async () => {
  if (!selectedDataset.value) return
  const datasetId = selectedDataset.value.ragflow_dataset_id || selectedDataset.value.id
  if (!datasetId) return

  aiAnalyzing.value = true
  try {
    const response = await axios.post(`/api/portal/ragflow/datasets/${datasetId}/ai-analyze`)
    const resData = apiData(response)
    if (resData) {
      if (resData.description) form.value.description = resData.description
      if (resData.tags) form.value.tags = normalizeTags(String(resData.tags))
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
const retryAfterError = async () => {
  showErrorBanner.value = false
  errorMessage.value = ''
  await fetchDatasets()
}

/** 引擎挂了但还能看到旧数据时置为 true，页面顶部提示"这是缓存" */
const datasetsStale = ref(false)

const fetchDatasets = async (force = false) => {
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
    const response = await axios.get('/api/portal/ragflow/datasets', {
      params: { page_size: 5000, refresh: force ? 1 : 0 },
    })
    datasets.value = apiData(response)
    datasetsStale.value = response.data?.stale === true
    engineStatus.value = 'connected'
    
    // 如果没有选中的节点，默认选中第一个 —— 但要优先落在当前 Tab 的范围内，
    // 否则会出现「左侧授权可见 0 个、右侧却显示着一个 TEAM 级知识库」的错位。
    if (!selectedId.value && datasets.value.length > 0) {
      const firstDataset = visibilityScopeDatasets.value[0] ?? datasets.value[0]
      if (firstDataset) {
        const scope = visibilityOf(firstDataset)
        if (scope !== activeVisibility.value) activeVisibility.value = scope
        selectDatasetNode(firstDataset)
      }
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

// 只读部门目录：失败不阻塞知识库列表，仅在归属部门选择器里提示
const fetchDepartmentOptions = async () => {
  try {
    const response = await axios.get('/api/portal/departments/options')
    departmentOptions.value = response.data?.items || []
    departmentOptionsError.value = ''
  } catch (err) {
    departmentOptions.value = []
    departmentOptionsError.value = extractError(err) || '部门目录加载失败'
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
// 同一知识库可能被轮询、手动刷新、上传后刷新同时触发，用序号只认最后一次请求
const documentsReqSeq: Record<string, number> = {}

const fetchDocumentsForDataset = async (dsId: string, options: { silent?: boolean } = {}) => {
  const seq = (documentsReqSeq[dsId] = (documentsReqSeq[dsId] || 0) + 1)
  documentsLoadingMap.value[dsId] = true
  try {
    // 后端默认 page_size=100，且客户端仅在 page_size>100 时才自动翻页，
    // 不显式放大时文档超过 100 个会被静默截断，列表与文档数都会少算
    const response = await axios.get(`/api/portal/ragflow/datasets/${dsId}/documents`, {
      params: { page_size: 1000 }
    })
    if (seq !== documentsReqSeq[dsId]) return
    const docs = apiData(response)
    documentsMap.value[dsId] = docs
    documentsErrorMap.value[dsId] = false

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
    if (seq !== documentsReqSeq[dsId]) return
    // 显式区分「加载失败」与「确实没有文档」，否则树里会显示成「暂无文档」
    documentsErrorMap.value[dsId] = true
    // 搜索会一次性拉多个库，失败时逐个弹 toast 会把界面刷屏
    if (!options.silent) showToast(extractError(err), 'error')
    documentsMap.value[dsId] = []
  } finally {
    // 只有最后一次请求才有权关掉 loading，否则早返回的请求会让 spinner 提前消失
    if (seq === documentsReqSeq[dsId]) documentsLoadingMap.value[dsId] = false
  }
}

const datasetPermissions = ref<{ users: any[], roles: any[], ownerUserId: number | null }>({
  users: [],
  roles: [],
  ownerUserId: null,
})
const loadingPermissions = ref(false)
const permissionsError = ref(false)
/** 只读成员查不到授权名单（后端按写权限校验），这是既定权限而不是故障 */
const permissionsRestricted = ref(false)

/** 当前选中的库是否为个人私有：private 的语义是「仅创建人」，因此不允许分配授权 */
const isPrivateDataset = computed(
  () => !!selectedDataset.value && visibilityOf(selectedDataset.value) === 'private'
)

/** 当前库是否已有授权对象：把可见范围改成 private 前必须提示"授权会失效" */
const hasExistingPermissions = computed(
  () => datasetPermissions.value.users.length > 0 || datasetPermissions.value.roles.length > 0
)

const fetchDatasetPermissions = async (datasetId: string) => {
  // 只读成员对这个库没有写权限，后端 GET /permissions 必然 403。
  // 与其发一个注定失败的请求再显示"读取失败"，不如直接说明"看不到名单是权限所致"，
  // 否则用户会以为系统故障（红色报错 + 重试按钮）。
  const target = datasets.value.find(d => (d.ragflow_dataset_id || d.id) === datasetId)
  if (target && !canManageDataset(target)) {
    permissionsRestricted.value = true
    permissionsError.value = false
    datasetPermissions.value = { users: [], roles: [], ownerUserId: null }
    loadingPermissions.value = false
    return
  }
  permissionsRestricted.value = false
  loadingPermissions.value = true
  permissionsError.value = false
  datasetPermissions.value = { users: [], roles: [], ownerUserId: null }
  try {
    const response = await axios.get(`/api/portal/ragflow/datasets/${datasetId}/permissions`)
    const data = response.data?.data || {}
    datasetPermissions.value = {
      users: data.users || [],
      roles: data.roles || [],
      ownerUserId: data.owner_user_id ?? null,
    }
  } catch (err) {
    // 必须显式区分失败：静默时界面会显示「未分配权限」，让管理员误判为无人有权限
    permissionsError.value = true
    console.error('获取知识库权限分配失败:', err)
  } finally {
    loadingPermissions.value = false
  }
}

// 反向分配成员授权的状态与交互
const showAddPermissionModal = ref(false)
const assignType = ref<'role' | 'user'>('role')
const selectedCandidateIds = ref<number[]>([])
const candidates = ref<{ roles: any[], users: any[] }>({ roles: [], users: [] })
const savingPerms = ref(false)
const candidateSearchQuery = ref('')

const availableRoles = computed(() => {
  const grantedCodes = (datasetPermissions.value.roles || []).map(r => r.code)
  let filtered = (candidates.value.roles || []).filter(r => !grantedCodes.includes(r.code))
  if (candidateSearchQuery.value) {
    const q = candidateSearchQuery.value.toLowerCase()
    filtered = filtered.filter(r => 
      (r.name && r.name.toLowerCase().includes(q)) || 
      (r.code && r.code.toLowerCase().includes(q))
    )
  }
  return filtered
})

const availableUsers = computed(() => {
  const grantedNames = (datasetPermissions.value.users || []).map(u => u.user_name)
  let filtered = (candidates.value.users || []).filter(u => !grantedNames.includes(u.user_name))
  if (candidateSearchQuery.value) {
    const q = candidateSearchQuery.value.toLowerCase()
    filtered = filtered.filter(u =>
      (u.real_name && u.real_name.toLowerCase().includes(q)) ||
      (u.user_name && u.user_name.toLowerCase().includes(q)) ||
      // 按部门名搜：想找"开发部的人"时不用逐个点开看
      (u.departments || []).some((dept: any) =>
        String(dept?.name || '').toLowerCase().includes(q),
      )
    )
  }
  return filtered
})

const openAddPermissionModal = async () => {
  selectedCandidateIds.value = []
  showAddPermissionModal.value = true
  try {
    const res = await axios.get('/api/portal/metadata/candidates')
    candidates.value = res.data?.data || { roles: [], users: [] }
  } catch (err) {
    console.error('获取候选列表失败:', err)
  }
}

const closeAddPermissionModal = () => {
  showAddPermissionModal.value = false
  selectedCandidateIds.value = []
  candidateSearchQuery.value = ''
}

const toggleCandidateSelection = (id: number) => {
  const idx = selectedCandidateIds.value.indexOf(id)
  if (idx > -1) {
    selectedCandidateIds.value.splice(idx, 1)
  } else {
    selectedCandidateIds.value.push(id)
  }
}

const submitPermissions = async () => {
  if (!selectedCandidateIds.value.length || !selectedDatasetId.value) return
  savingPerms.value = true
  try {
    await axios.post(`/api/portal/ragflow/datasets/${selectedDatasetId.value}/permissions`, {
      target_type: assignType.value,
      target_ids: selectedCandidateIds.value
    })
    showToast('分配授权成功', 'success')
    closeAddPermissionModal()
    await fetchDatasetPermissions(selectedDatasetId.value)
  } catch (err) {
    showToast('分配授权失败', 'error')
  } finally {
    savingPerms.value = false
  }
}

const removePermission = async (type: string, id: number, name: string) => {
  if (!selectedDatasetId.value) return
  pendingPermissionRemoval.value = { type, id, name }
}

const removingPermission = ref(false)
const confirmRemovePermission = async () => {
  const pending = pendingPermissionRemoval.value
  if (!pending || !selectedDatasetId.value || removingPermission.value) return
  removingPermission.value = true
  try {
    await axios.delete(`/api/portal/ragflow/datasets/${selectedDatasetId.value}/permissions`, {
      data: {
        target_type: pending.type,
        target_id: pending.id
      }
    })
    showToast('取消授权成功', 'success')
    await fetchDatasetPermissions(selectedDatasetId.value)
  } catch (err) {
    showToast('取消授权失败', 'error')
  } finally {
    removingPermission.value = false
    pendingPermissionRemoval.value = null
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

// ==================== 业务归属部门：只读目录 + 左侧树部门分组 ====================
// 说明：owner_dept_id 只用于"按部门浏览/过滤"，不参与任何权限判定——
// 谁能看到、谁能修改知识库仍由后端的 get_knowledge_base_access 决定。

/** 部门 id → 节点（含停用部门：历史归属仍要能显示名称） */
const deptById = computed(() => {
  const map = new Map<number, DeptOptionNode>()
  const walk = (nodes: DeptOptionNode[]) => {
    for (const node of nodes) {
      map.set(node.id, node)
      if (node.children?.length) walk(node.children)
    }
  }
  walk(departmentOptions.value)
  return map
})

/** 先序索引：保证下拉与分组顺序与部门管理页（sort_order → id）一致 */
const deptOrderIndex = computed(() => {
  const index = new Map<number, number>()
  let cursor = 0
  const walk = (nodes: DeptOptionNode[]) => {
    for (const node of nodes) {
      index.set(node.id, cursor++)
      if (node.children?.length) walk(node.children)
    }
  }
  walk(departmentOptions.value)
  return index
})

const deptDepth = (id: number): number => {
  const path = deptById.value.get(id)?.path || ''
  return Math.max(path.split('/').filter(Boolean).length - 1, 0)
}

const deptName = (id?: number | null): string => {
  if (!id) return ''
  return deptById.value.get(Number(id))?.name || ''
}

/** 归属部门的展示文本：部门名优先（部门改名后自动跟随），历史自由文本兜底 */
const datasetOwnerLabel = (dataset: KnowledgeBase): string => {
  return deptName(dataset.owner_dept_id) || dataset.owner || ''
}

/** 部门名称链（根 → 自身）。物化 path 存的是部门 id，转成名字才能给人看 */
const deptNamePath = (id: number): string[] => {
  const path = deptById.value.get(id)?.path || ''
  return path
    .split('/')
    .filter(Boolean)
    .map((seg) => deptById.value.get(Number(seg))?.name || '')
    .filter(Boolean)
}

/** 选项 label 用全角空格表示层级缩进，回填到按钮上时要剥掉，否则文字会整体右移 */
const stripDeptIndent = (label: string) => label.replace(/^[　\s]+/, '').trim()

/** 原生 select 无法渲染树，用全角空格表达层级 */
const deptSelectOptions = computed(() => {
  const rows = [...deptById.value.values()]
    .sort((a, b) => (deptOrderIndex.value.get(a.id) ?? 0) - (deptOrderIndex.value.get(b.id) ?? 0))
    .map((node) => ({
      id: node.id,
      label: `${'　'.repeat(deptDepth(node.id))}${node.name}${node.status === 0 ? '（已停用）' : ''}`,
      path: deptNamePath(node.id),
    }))
  return rows
})

/** 部门下拉的展开态；与可见范围/解析算法共用「点空白处收起」的处理 */
const deptMenuOpen = ref(false)

/** 部门下拉的关键词；与开合态一起在弹窗打开、点空白处时重置 */
const deptSearchKeyword = ref('')

const deptSearching = computed(() => deptSearchKeyword.value.trim().length > 0)

/**
 * 下拉选项：0 号是「未关联部门」，其后是带层级缩进的部门树。
 * 部门多时靠缩进逐层找太慢，支持按名称搜索——匹配时忽略缩进，
 * 命中项在名称后补父级路径，层级信息不丢。
 */
const deptMenuOptions = computed(() => {
  const all = [{ id: 0, label: '未关联部门', path: ['未关联部门'] }, ...deptSelectOptions.value]
  const kw = deptSearchKeyword.value.trim().toLowerCase()
  if (!kw) return all
  return all.filter((option) => stripDeptIndent(option.label).toLowerCase().includes(kw))
})

const onDeptSearchInput = (event: Event) => {
  // 过滤后条目变少，原来的滚动位置可能落在空白处，看起来像「搜不到」
  const panel = (event.target as HTMLElement | null)
    ?.closest('[data-dept-select]')
    ?.querySelector('[data-dept-menu]')
  panel?.scrollTo({ top: 0 })
}

/** 每次打开都重置关键词：否则上次搜过的词还在，列表看起来像「部门没了」 */
const toggleDeptMenu = () => {
  deptMenuOpen.value = !deptMenuOpen.value
  if (deptMenuOpen.value) deptSearchKeyword.value = ''
}

/** 当前选中的部门名；0 / 已删除部门都回落到「未关联部门」 */
const selectedDeptLabel = computed(() => {
  const id = Number(form.value.ownerDeptId || 0)
  if (!id) return '未关联部门'
  const hit = deptSelectOptions.value.find((option) => option.id === id)
  return hit ? stripDeptIndent(hit.label) : '未关联部门'
})

/**
 * 下拉下方的说明行（双行结构的第二行）。
 * 右侧「解析算法」也是两行，高度对齐靠这行撑起来；文案按当前可见范围给出。
 */
const deptHintText = computed(() => {
  if (form.value.visibility !== 'team') {
    // 已选的部门不会被清空，切回 TEAM 级即生效——这一点必须说明，否则用户以为白选了
    return form.value.ownerDeptId
      ? '仅「TEAM 级」需要归属部门；已选值会保留'
      : '仅「TEAM 级」需要归属部门'
  }
  if (!form.value.ownerDeptId) {
    if (form.value.ownerText) return `历史归属：${form.value.ownerText}`
    return '未选部门会退化为授权可见'
  }
  return '含下级部门，成员自动只读'
})

/** 未选部门的告警色：切回 TEAM 级却忘了选，是要提醒的 */
const deptHintClass = computed(() =>
  form.value.visibility === 'team' && !form.value.ownerDeptId && !form.value.ownerText
    ? 'text-amber-600'
    : 'text-gray-400'
)

/** 搜索命中的部门及其所有下级部门（按部门名匹配，含下级是刻意的） */
const searchMatchedDeptIds = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  const ids = new Set<number>()
  if (!query) return ids
  const matchedRoots = [...deptById.value.values()].filter((node) =>
    node.name.toLowerCase().includes(query)
  )
  if (!matchedRoots.length) return ids
  for (const node of deptById.value.values()) {
    // path 末尾带 `/`，因此 `/1/7/` 不会误匹配 `/1/70/`
    if (matchedRoots.some((root) => node.path && node.path.startsWith(root.path))) {
      ids.add(node.id)
    }
  }
  return ids
})

// 计算过滤后的知识库树
const isSearchEmpty = computed(
  () => searchQuery.value.trim().length > 0 && filteredDatasets.value.length === 0
)

/** 当前 tab 下的库（可见范围维度）：与搜索正交，先按 tab 圈定范围再搜 */
const visibilityScopeDatasets = computed(() =>
  datasets.value.filter(dataset => visibilityOf(dataset) === activeVisibility.value)
)

/** 每个 tab 的库数量（用全量而非搜索结果，避免搜索时角标乱跳） */
const visibilityTabCount = (scope: VisibilityScope): number =>
  datasets.value.filter(dataset => visibilityOf(dataset) === scope).length

/** 空态文案随 tab 变化：三档的「为什么这里是空的」并不相同 */
const emptyVisibilityText = computed(() => {
  if (activeVisibility.value === 'team') return '还没有可访问的部门知识库'
  if (activeVisibility.value === 'private') return '你还没有个人私有知识库'
  return '还没有可访问的公共知识库'
})

const filteredDatasets = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  if (!query) return visibilityScopeDatasets.value

  const deptHit = searchMatchedDeptIds.value

  return visibilityScopeDatasets.value.filter(ds => {
    // 部门名命中（含下级部门）时，该部门下的库整体命中
    if (ds.owner_dept_id && deptHit.has(ds.owner_dept_id)) return true

    const nameMatch = (ds.platform_name || ds.name || '').toLowerCase().includes(query)
    const descMatch = (ds.platform_description || ds.description || '').toLowerCase().includes(query)
    // owner 文本可能是历史自由文本（未关联部门），仍允许被搜到
    const ownerMatch = (ds.owner || '').toLowerCase().includes(query)
    const tagsMatch = (ds.tags || []).some(t => t.toLowerCase().includes(query))

    const dsId = String(ds.ragflow_dataset_id || ds.id || '')
    // 支持按知识库 ID 搜索：详情页展示的就是这个 ID，复制后粘回搜索框要能定位到库。
    // 用 includes 而不是精确比较，方便只粘前缀（排障时拿到的往往是被截断的 ID）。
    const idMatch = !!dsId && dsId.toLowerCase().includes(query)

    const subDocs = documentsMap.value[dsId] || []
    const docMatch = subDocs.some(doc => (doc.name || '').toLowerCase().includes(query))

    return nameMatch || descMatch || ownerMatch || tagsMatch || idMatch || docMatch
  })
})

/**
 * 搜索文档名时按需加载文档列表。
 *
 * 文档是「展开才懒加载」的，但搜索框承诺能搜文档名：不补这一步，
 * 没展开过的库里的文档永远搜不到，用户会以为文档不存在。
 * 并发上限 4，避免一次打爆 RAGFlow；300ms 防抖避免连续输入触发请求风暴。
 */
const SEARCH_DOC_CONCURRENCY = 4
const SEARCH_DOC_DEBOUNCE_MS = 300
const searchDocLoading = ref(false)
let searchDocTimer: ReturnType<typeof setTimeout> | null = null

const ensureDocumentsForSearch = async () => {
  const targets = visibilityScopeDatasets.value
    .filter((dataset) => !dataset.is_missing_in_ragflow)
    .map((dataset) => dataset.ragflow_dataset_id || dataset.id)
    .filter((id) => id && !documentsMap.value[id] && !documentsLoadingMap.value[id])
  if (!targets.length) return
  searchDocLoading.value = true
  try {
    for (let i = 0; i < targets.length; i += SEARCH_DOC_CONCURRENCY) {
      await Promise.all(
        targets
          .slice(i, i + SEARCH_DOC_CONCURRENCY)
          .map((id) => fetchDocumentsForDataset(id, { silent: true })),
      )
    }
  } finally {
    searchDocLoading.value = false
  }
}

watch(searchQuery, (value) => {
  if (searchDocTimer) clearTimeout(searchDocTimer)
  const query = value.trim().toLowerCase()
  if (!query) return
  // 只在「搜到的是 ID」这件事上短路文档预加载：粘贴 32 位 Dataset ID 时用户显然在找库，
  // 没必要为此把所有库的文档列表都拉一遍（库一多就是一串串行 RAGFlow 请求）。
  // 加 8 位长度阈值，避免 "test" 这类短词恰好是某个 ID 的子串就把文档搜索整段跳过。
  if (
    query.length >= 8 &&
    visibilityScopeDatasets.value.some((ds) =>
      String(ds.ragflow_dataset_id || ds.id || '').toLowerCase().includes(query)
    )
  ) {
    return
  }
  searchDocTimer = setTimeout(() => {
    void ensureDocumentsForSearch()
  }, SEARCH_DOC_DEBOUNCE_MS)
})

/**
 * 左侧树的一级节点。
 *
 * 两个模式：
 * - **TEAM 级 tab**：按业务归属部门分组。只渲染「子树内有知识库」的部门，但父部门
 *   即使自身没有库也会出现以保留层级；`totalCount` 是含下级部门的库数，用于徽标；
 *   `owner_dept_id` 为空（含历史自由文本）统一归入「未关联部门」。
 * - **授权可见 / 个人私有 tab**：平铺（`isFlat`），不渲染分组头——这两档里部门归属
 *   只是附带信息，多套一层分组反而增加点击成本。
 */
const deptDatasetGroups = computed<DeptDatasetGroup[]>(() => {
  if (activeVisibility.value !== 'team') {
    const list = filteredDatasets.value
    if (!list.length) return []
    return [
      {
        key: `flat:${activeVisibility.value}`,
        deptId: null,
        label: '',
        path: '',
        level: 0,
        ancestors: [],
        datasets: list,
        totalCount: list.length,
        hasChildren: false,
        isUnassigned: false,
        isFlat: true,
      },
    ]
  }

  const direct = new Map<number, KnowledgeBase[]>()
  const unassigned: KnowledgeBase[] = []
  for (const dataset of filteredDatasets.value) {
    const deptId = dataset.owner_dept_id
    if (deptId && deptById.value.has(deptId)) {
      const list = direct.get(deptId)
      if (list) list.push(dataset)
      else direct.set(deptId, [dataset])
    } else {
      unassigned.push(dataset)
    }
  }

  if (!direct.size && !unassigned.length) return []

  // 有库的部门 + 其祖先链（父部门可能自身无库，仍需作为层级展示）
  const needed = new Set<number>()
  for (const deptId of direct.keys()) {
    let cursor: DeptOptionNode | undefined = deptById.value.get(deptId)
    while (cursor) {
      needed.add(cursor.id)
      cursor = cursor.parent_id ? deptById.value.get(cursor.parent_id) : undefined
    }
  }

  const totalInSubtree = (node: DeptOptionNode): number => {
    let total = direct.get(node.id)?.length || 0
    for (const [deptId, list] of direct) {
      if (deptId === node.id) continue
      const child = deptById.value.get(deptId)
      if (child && node.path && child.path.startsWith(node.path)) total += list.length
    }
    return total
  }

  const groups: DeptDatasetGroup[] = []
  const emitted = new Set<number>()
  const walk = (node: DeptOptionNode, ancestors: number[], level: number) => {
    if (!needed.has(node.id)) return
    const children = (node.children || []).filter(child => needed.has(child.id))
    groups.push({
      key: `dept:${node.id}`,
      deptId: node.id,
      label: node.name,
      path: node.path,
      level,
      ancestors,
      datasets: direct.get(node.id) || [],
      totalCount: totalInSubtree(node),
      hasChildren: children.length > 0,
      isUnassigned: false,
    })
    emitted.add(node.id)
    for (const child of children) walk(child, [...ancestors, node.id], level + 1)
  }
  for (const root of departmentOptions.value) walk(root, [], 0)

  // 兜底：owner_dept_id 指向部门树里已不存在的部门时，不能把库藏起来
  const orphanIds = [...direct.keys()].filter(id => !emitted.has(id))
  for (const orphanId of orphanIds) unassigned.push(...(direct.get(orphanId) || []))

  if (unassigned.length) {
    groups.push({
      key: 'unassigned',
      deptId: null,
      label: '未关联部门',
      path: '',
      level: 0,
      ancestors: [],
      datasets: unassigned,
      totalCount: unassigned.length,
      hasChildren: false,
      isUnassigned: true,
    })
  }

  return groups
})

const isDeptGroupCollapsed = (group: DeptDatasetGroup): boolean =>
  !!collapsedDeptGroups.value[group.key]

const toggleDeptGroup = (group: DeptDatasetGroup) => {
  collapsedDeptGroups.value = {
    ...collapsedDeptGroups.value,
    [group.key]: !collapsedDeptGroups.value[group.key],
  }
}

/** 祖先部门折叠时，子部门分组整体隐藏；搜索态强制展开，避免命中结果被折叠藏起来 */
const isDeptGroupHidden = (group: DeptDatasetGroup): boolean => {
  if (searchQuery.value.trim()) return false
  return group.ancestors.some(ancestorId => !!collapsedDeptGroups.value[`dept:${ancestorId}`])
}

/** 分组内容是否展开：搜索时一律展开，平铺模式没有分组头可点，恒展开 */
const isDeptGroupOpen = (group: DeptDatasetGroup): boolean => {
  if (group.isFlat) return true
  if (searchQuery.value.trim()) return true
  return !isDeptGroupHidden(group) && !isDeptGroupCollapsed(group)
}


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
    // 同步的语义就是「我要最新」，绕过 60s 列表缓存
    await fetchDatasets(true)
  } catch (err) {
    errorMessage.value = extractError(err)
    // 用户可能已关闭过横幅，失败时必须重新展开，否则只剩 3 秒 toast
    showErrorBanner.value = true
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
  if (creatingDataset.value) return
  creatingDataset.value = true
  try {
    const res = await axios.post('/api/portal/ragflow/datasets', {
      name: form.value.name.trim(),
      description: form.value.description,
      chunk_method: form.value.chunk_method,
      // 业务归属部门：只影响左侧树分组，不参与权限
      owner_dept_id: form.value.ownerDeptId || null,
      visibility: form.value.visibility,
      tags: form.value.tags,
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
      if (found) {
        // 新库按所选可见范围落在对应 tab，否则刚建完在左栏里"消失"
        activeVisibility.value = visibilityOf(found)
        selectDatasetNode(found)
      }
    }
  } catch (err) {
    showToast(extractError(err), 'error')
  } finally {
    creatingDataset.value = false
  }
}

const updateMetadata = async () => {
  if (!isEngineReady.value) {
    showToast('知识库引擎未连接，暂不能保存元数据', 'warning')
    return
  }
  if (!selectedDatasetId.value) return
  // 名称是必填展示字段，此前允许清空后保存
  if (!form.value.name.trim()) {
    showToast('请输入知识库名称', 'warning')
    return
  }
  if (savingMetadata.value) return
  savingMetadata.value = true
  try {
    const extraConfig = parseExtraConfig()
    extraConfig.custom_questions = customQuestions.value.filter(q => q.label.trim() && q.query.trim())

    const payload: Record<string, unknown> = {
      name: form.value.name,
      description: form.value.description,
      // 0 表示清空部门归属；后端据 owner_dept_id 回填 owner 文本快照
      owner_dept_id: form.value.ownerDeptId || 0,
      visibility: form.value.visibility,
      tags: form.value.tags,
      notes: form.value.notes,
      extra_config: extraConfig
    }
    // 未关联部门时，历史自由文本由前端决定保留还是清除；选了部门则由后端按部门名回填
    if (!form.value.ownerDeptId) {
      payload.owner = form.value.ownerText
    }

    await axios.put(`/api/portal/ragflow/datasets/${selectedDatasetId.value}/metadata`, payload)
    showToast('知识库元数据已更新', 'success')
    showEditModal.value = false
    await fetchDatasets()
    // 可见范围改到别的档后，库会落到另一个 tab；不跟随切换会让它看起来"消失了"
    const saved = datasets.value.find(
      d => (d.ragflow_dataset_id || d.id) === selectedDatasetId.value
    )
    if (saved) {
      activeVisibility.value = visibilityOf(saved)
      selectedDataset.value = saved
    }
  } catch (err) {
    showToast(extractError(err), 'error')
  } finally {
    savingMetadata.value = false
  }
}

// ==================== 虚拟文件夹层级管理开始 ====================
const editingDatasetIdForFolder = ref<string | null>(null)
const newFolderName = ref('')
const editingFolderKey = ref<string | null>(null) // 格式: datasetId + '_' + oldFolderName
const editingFolderNewName = ref('')
const collapsedFolders = ref<Record<string, boolean>>({}) // key: datasetId + '_' + folderName
const activeFileMoveMenuDocId = ref<string | null>(null)

// 判定文件夹折叠状态
const isFolderCollapsed = (datasetId: string, folderName: string) => {
  return !!collapsedFolders.value[`${datasetId}_${folderName}`]
}

// 切换文件夹折叠状态
const toggleFolderCollapse = (datasetId: string, folderName: string) => {
  const key = `${datasetId}_${folderName}`
  collapsedFolders.value[key] = !collapsedFolders.value[key]
}

// 打开新建文件夹输入框
const startCreateFolder = (dataset: KnowledgeBase) => {
  const datasetId = dataset.ragflow_dataset_id || dataset.id
  if (!datasetId) return
  // 自动展开该知识库节点
  expandedDatasetIds.value[datasetId] = true
  editingDatasetIdForFolder.value = datasetId
  newFolderName.value = ''
}

// 确认创建文件夹
const confirmCreateFolder = async (dataset: KnowledgeBase) => {
  const datasetId = dataset.ragflow_dataset_id || dataset.id
  if (!datasetId) return
  const name = newFolderName.value.trim()
  if (!name) {
    showToast('文件夹名称不能为空', 'warning')
    return
  }

  const structure = {
    ...(dataset.local_metadata?.extra_config?.folder_structure || {}),
  }

  if (structure[name]) {
    showToast('该文件夹已存在', 'warning')
    return
  }

  structure[name] = []

  try {
    await saveFolderStructure(dataset, structure)
    showToast('文件夹创建成功', 'success')
    editingDatasetIdForFolder.value = null
    newFolderName.value = ''
  } catch (err) {
    showToast('文件夹创建失败: ' + extractError(err), 'error')
  }
}

// 开始重命名文件夹
const startRenameFolder = (datasetId: string, folderName: string) => {
  editingFolderKey.value = `${datasetId}_${folderName}`
  editingFolderNewName.value = folderName
}

// 确认重命名文件夹
const confirmRenameFolder = async (dataset: KnowledgeBase, oldName: string) => {
  const datasetId = dataset.ragflow_dataset_id || dataset.id
  if (!datasetId) return
  const newName = editingFolderNewName.value.trim()
  if (!newName) {
    showToast('文件夹名称不能为空', 'warning')
    return
  }
  if (newName === oldName) {
    editingFolderKey.value = null
    return
  }

  const structure = {
    ...(dataset.local_metadata?.extra_config?.folder_structure || {}),
  }

  if (structure[newName]) {
    showToast('该名称的文件夹已存在', 'warning')
    return
  }

  // 替换键名并保持文档列表不变
  structure[newName] = structure[oldName] || []
  delete structure[oldName]

  try {
    await saveFolderStructure(dataset, structure)
    showToast('文件夹重命名成功', 'success')
    editingFolderKey.value = null
  } catch (err) {
    showToast('重命名失败: ' + extractError(err), 'error')
  }
}

// 删除文件夹 (解绑文件，不物理删除文件)
const removeFolder = async (dataset: KnowledgeBase, folderName: string) => {
  pendingFolderRemoval.value = { dataset, folderName }
}

const removingFolder = ref(false)
const confirmRemoveFolder = async () => {
  const pending = pendingFolderRemoval.value
  if (!pending || removingFolder.value) return
  removingFolder.value = true
  const { dataset, folderName } = pending
  const structure = {
    ...(dataset.local_metadata?.extra_config?.folder_structure || {}),
  }

  delete structure[folderName]

  try {
    await saveFolderStructure(dataset, structure)
    showToast('文件夹已删除', 'success')
  } catch (err) {
    showToast('删除文件夹失败: ' + extractError(err), 'error')
  } finally {
    removingFolder.value = false
    pendingFolderRemoval.value = null
  }
}

// 移动文档至文件夹
const moveDocumentToFolder = async (dataset: KnowledgeBase, docId: string, targetFolderName: string | null) => {
  const structure: Record<string, string[]> = {
    ...(dataset.local_metadata?.extra_config?.folder_structure || {}),
  }

  // 1. 从之前所在的文件夹中移除 docId
  for (const fName in structure) {
    if (Array.isArray(structure[fName])) {
      structure[fName] = structure[fName].filter((id: string) => id !== docId)
    }
  }

  // 2. 如果指定了目标文件夹，加入进去
  if (targetFolderName) {
    if (!Array.isArray(structure[targetFolderName])) {
      structure[targetFolderName] = []
    }
    if (!structure[targetFolderName].includes(docId)) {
      structure[targetFolderName].push(docId)
    }
  }

  try {
    await saveFolderStructure(dataset, structure)
    showToast('移动文档成功', 'success')
    activeFileMoveMenuDocId.value = null
  } catch (err) {
    showToast('移动文档失败: ' + extractError(err), 'error')
  }
}

// 内部方法：只保存虚拟文件夹映射
// 这里刻意不再走整份 PUT /metadata：移动一个文档会把前端缓存的 name/visibility
// 一起回写，并发操作就是 lost update（最坏情况是把公开库改成私有）。
const saveFolderStructure = async (
  dataset: KnowledgeBase,
  structure: Record<string, string[]>,
) => {
  const datasetId = dataset.ragflow_dataset_id || dataset.id
  const res = await axios.put(
    `/api/portal/ragflow/datasets/${datasetId}/folder-structure`,
    { folder_structure: structure },
  )
  const saved = res.data?.data?.extra_config?.folder_structure || {}
  if (!dataset.local_metadata) dataset.local_metadata = {}
  dataset.local_metadata.extra_config = {
    ...(dataset.local_metadata.extra_config || {}),
    folder_structure: saved,
  }
}

/** 文档被删除后清理目录里的悬空引用，避免树里出现点不开的条目。 */
const removeDocumentFromFolders = async (dataset: KnowledgeBase, docId: string) => {
  const structure = dataset.local_metadata?.extra_config?.folder_structure
  if (!structure) return
  const next: Record<string, string[]> = {}
  let changed = false
  for (const [folder, ids] of Object.entries(structure)) {
    const filtered = (ids || []).filter((id) => id !== docId)
    if (filtered.length !== (ids || []).length) changed = true
    next[folder] = filtered
  }
  if (!changed) return
  try {
    await saveFolderStructure(dataset, next)
  } catch (err) {
    // 清理失败不影响文档删除本身，但要让用户知道目录里可能有残留
    showToast('文档已删除，但目录引用清理失败：' + extractError(err), 'warning')
  }
}
// ==================== 虚拟文件夹层级管理结束 ====================

const triggerDeleteDataset = (dataset: KnowledgeBase) => {
  deletingDataset.value = dataset
  const dsId = dataset.ragflow_dataset_id || dataset.id
  if (dsId) {
    // 异步静默拉取该知识库的最新授权记录，以便在确认弹窗中即时呈现正确的同步警告
    fetchDatasetPermissions(dsId)
    // 引用关系同理：删除前必须让用户看见「会打挂哪些智能体」
    void loadDeleteAgentRefs(dataset)
  }
}

/** 删除确认弹窗里的智能体引用影响面。 */
const deleteAgentRefs = ref<{ id: string; name: string; display_name: string; is_enabled: boolean }[]>([])
const deleteAgentRefsUnknown = ref(false)

const loadDeleteAgentRefs = async (dataset: KnowledgeBase) => {
  const dsId = dataset.ragflow_dataset_id || dataset.id
  deleteAgentRefs.value = []
  deleteAgentRefsUnknown.value = false
  if (!dsId) return
  try {
    const res = await axios.get(`/api/portal/ragflow/datasets/${dsId}/agents`)
    deleteAgentRefs.value = res.data?.data?.items || []
  } catch {
    // 读不到影响面不阻断删除，但弹窗要说清「引用关系未知」，
    // 否则「没有警告」会被误解成「没有智能体在用」。
    deleteAgentRefsUnknown.value = true
  }
}

const deleteDatasetMessage = computed(() => {
  if (!deletingDataset.value) return ''
  const name = deletingDataset.value.platform_name || deletingDataset.value.name
  
  // 检查当前拉取出来的 datasetPermissions 中是否有授权角色或用户
  const hasPerms = datasetPermissions.value.users?.length > 0 || datasetPermissions.value.roles?.length > 0
  // 权限没读到时不能当作「没有授权」，否则会静默丢掉这条风险警告
  const permsUnknown = permissionsError.value
  const permWarning = permsUnknown
    ? '（⚠️ 授权信息读取失败，无法确认该知识库是否存在授权关系）'
    : '（⚠️ 该知识库当前已授权的角色/用户访问权限关系也将被同步清除）'

  const assetRefs = deleteAgentRefs.value
  const refWarning = assetRefs.length
    ? `（⚠️ 该库正被 ${assetRefs.length} 个智能体引用：${assetRefs
        .map((agent) => agent.display_name)
        .slice(0, 3)
        .join('、')}${assetRefs.length > 3 ? ' 等' : ''}；删除后这些智能体将检索不到本库内容）`
    : deleteAgentRefsUnknown.value
      ? '（⚠️ 引用关系读取失败，无法确认是否仍有智能体在使用本库）'
      : ''
  
  if (deletingDataset.value.is_missing_in_ragflow) {
    let baseMsg = '检测到该知识库已在 RAGFlow 端不存在或已被物理删除，确定清理平台本地残留的相关配置和元数据信息吗？'
    if (hasPerms || permsUnknown) {
      baseMsg += permWarning
    }
    return baseMsg + refWarning
  } else {
    let baseMsg = `确定真实删除知识库「${name}」吗？RAGFlow 侧相应集群数据也将同步卸载，此操作不可逆。`
    if (permsUnknown) {
      baseMsg += permWarning
    } else if (hasPerms) {
      baseMsg += '（⚠️ 注意：该知识库当前已授权给角色/用户，删除后系统关联的全部授权记录将被同步擦除清理）'
    }
    return baseMsg + refWarning
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

// 与后端 KNOWLEDGE_UPLOAD_MAX_BYTES / ALLOWED_EXTENSIONS 保持一致；
// 前端先拦一道，用户不用等整个文件传上去才看到 413。
const KNOWLEDGE_UPLOAD_MAX_MB = 256
const KNOWLEDGE_UPLOAD_EXTENSIONS = [
  'pdf', 'doc', 'docx', 'txt', 'md', 'csv', 'xls', 'xlsx',
  'ppt', 'pptx', 'html', 'htm', 'epub', 'png', 'jpg', 'jpeg', 'gif', 'bmp', 'tif', 'tiff',
]

const onFileChange = (event: Event) => {
  const input = event.target as HTMLInputElement
  if (!input.files) return
  const rejected: string[] = []
  const accepted = Array.from(input.files).filter((f) => {
    const ext = f.name.split('.').pop()?.toLowerCase() || ''
    if (!KNOWLEDGE_UPLOAD_EXTENSIONS.includes(ext)) {
      rejected.push(`${f.name}（类型不支持）`)
      return false
    }
    if (f.size > KNOWLEDGE_UPLOAD_MAX_MB * 1024 * 1024) {
      rejected.push(`${f.name}（超过 ${KNOWLEDGE_UPLOAD_MAX_MB}MB）`)
      return false
    }
    return true
  })
  if (rejected.length) {
    showToast(
      `已跳过 ${rejected.length} 个文件：${rejected.slice(0, 3).join('、')}${rejected.length > 3 ? ' 等' : ''}`,
      'warning',
    )
  }
  const newFiles = accepted.filter(
    (f) => !uploadFiles.value.some((existing) => existing.name === f.name && existing.size === f.size),
  )
  uploadFiles.value = [...uploadFiles.value, ...newFiles]
  // 允许用户重复选择同一个被拒文件（清空后二次选择仍能触发 change）
  input.value = ''
}

const uploadConflicts = ref<{ file: File; oldDocumentId: string; oldName: string }[]>([])
const pendingUploadClean = ref<File[]>([])

/** 查重用：优先用已加载的列表，避免每个文件都打一次接口 */
const fetchDatasetDocumentsForUpload = async (dsId: string): Promise<KnowledgeDocument[]> => {
  const cached = documentsMap.value[dsId]
  if (cached?.length) return cached
  const response = await axios.get(`/api/portal/ragflow/datasets/${dsId}/documents`, {
    params: { page_size: 1000 },
  })
  return (apiData(response) || []) as KnowledgeDocument[]
}

/**
 * 上传入口：先查重，有同名文档就弹「跳过 / 替换 / 保留两者」。
 *
 * 重名在 RAGFlow 侧不会报错，只会静默多存一份（或被加上 "(1)" 后缀），
 * 用户看到的却是「我明明替换了，怎么有两份」——所以必须在客户端先问清楚。
 */
const uploadDocument = async () => {
  if (!isEngineReady.value) {
    showToast('知识库引擎未连接，暂不能上传文档', 'warning')
    return
  }
  if (uploadFiles.value.length === 0 || !selectedDatasetId.value) {
    showToast('请先选择要上传的文件', 'warning')
    return
  }
  const targetDatasetId = selectedDatasetId.value

  let existing: KnowledgeDocument[] = []
  try {
    existing = await fetchDatasetDocumentsForUpload(targetDatasetId)
  } catch {
    // 查重失败不阻断上传：宁可让用户传，也不要因为列表接口抖动就传不了
    existing = []
  }

  const conflicts: { file: File; oldDocumentId: string; oldName: string }[] = []
  const clean: File[] = []
  for (const file of uploadFiles.value) {
    const hit = existing.find((doc) => (doc.name || '') === file.name)
    if (hit) conflicts.push({ file, oldDocumentId: hit.id, oldName: hit.name || file.name })
    else clean.push(file)
  }

  if (conflicts.length) {
    pendingUploadClean.value = clean
    uploadConflicts.value = conflicts
    return
  }
  await runUpload(clean, new Map())
}

const resolveUploadConflicts = async (strategy: 'skip' | 'replace' | 'keep') => {
  const conflicts = uploadConflicts.value
  const clean = pendingUploadClean.value
  uploadConflicts.value = []
  pendingUploadClean.value = []
  if (!conflicts.length) return

  const conflictFiles = conflicts.map((item) => item.file)
  if (strategy === 'skip') {
    await runUpload(clean, new Map())
    return
  }
  if (strategy === 'keep') {
    await runUpload([...clean, ...conflictFiles], new Map())
    return
  }
  const replaceMap = new Map<File, string>(
    conflicts.map((item) => [item.file, item.oldDocumentId] as const),
  )
  await runUpload([...clean, ...conflictFiles], replaceMap)
}

const runUpload = async (files: File[], replaceMap: Map<File, string>) => {
  const targetDatasetId = selectedDatasetId.value
  if (!targetDatasetId || !files.length) {
    showToast('没有需要上传的文件', 'warning')
    return
  }
  // 上传期间左侧知识库树仍可点击，必须固定目标库，
  // 否则中途切换会把剩余文件传到另一个知识库、刷新也会落到错误的库
  uploading.value = true

  const total = files.length
  let successCount = 0
  let failCount = 0
  let parseFailCount = 0

  for (let i = 0; i < total; i++) {
    const file = files[i]
    if (!file) continue
    uploadProgressText.value = `正在上传并解析: ${i + 1} / ${total} (${file.name})`

    try {
      if (replaceMap.has(file)) {
        // 替换由后端编排（上传→改名→删旧→解析），前端不再单独触发解析
        const payload = new FormData()
        payload.append('file', file)
        payload.append('old_document_id', replaceMap.get(file) as string)
        const response = await axios.post(
          `/api/portal/ragflow/datasets/${targetDatasetId}/documents/replace`,
          payload,
        )
        const newId = response.data?.data?.id
        if (newId) {
          parsingDocIds.value = { ...parsingDocIds.value, [newId]: true }
        }
        successCount++
        continue
      }

      const payload = new FormData()
      payload.append('file', file)
      const response = await axios.post(
        `/api/portal/ragflow/datasets/${targetDatasetId}/documents/upload`,
        payload,
      )
      const resData = response.data?.data || {}
      const docId = resData.id

      if (docId) {
        parsingDocIds.value = { ...parsingDocIds.value, [docId]: true }
        try {
          await axios.post(`/api/portal/ragflow/datasets/${targetDatasetId}/documents/parse`, {
            ids: [docId]
          })
        } catch (parseErr) {
          // 上传成功但解析未触发：单独计数，不再混入「上传成功」
          parseFailCount++
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

  if (failCount === total) {
    showToast('全部文档上传失败，请检查网络或知识库配置', 'error')
  } else if (parseFailCount > 0) {
    showToast(`上传成功 ${successCount} 个，其中 ${parseFailCount} 个自动解析未触发，请在列表中手动触发解析`, 'warning')
  } else if (failCount > 0) {
    showToast(`批量上传完成：成功 ${successCount} 个，失败 ${failCount} 个`, 'warning')
  } else {
    showToast(`成功批量上传并触发解析了 ${successCount} 个文档`, 'success')
  }

  clearAllUploadFiles()
  await fetchDocumentsForDataset(targetDatasetId)
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

    // 目录里还留着这个 docId 的话，树里会出现点不开的条目
    const targetDataset = datasets.value.find(
      (item) => (item.ragflow_dataset_id || item.id) === selectedDatasetId.value,
    )
    if (targetDataset) await removeDocumentFromFolders(targetDataset, docId)

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
let isUnmounted = false
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
      if (isUnmounted) return
      
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
      if (isUnmounted) return
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

const copyToClipboard = async (text: string) => {
  const ok = await copyText(text)
  if (ok) {
    showToast('已复制到剪贴板', 'success')
  } else {
    showToast('复制失败，请手动复制', 'error')
  }
}

const formatTime = (value?: string) => value ? new Date(value).toLocaleString() : '-'
const formatSize = (value?: number) => {
  if (!value) return '-'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

/**
 * 管理级写权限：改元数据、可见范围、归属部门、删库、分配授权、看授权名单。
 * 部门成员拿到的是内容级（能传/删/解析文档、看切片），到这里一律为 false。
 * `can_manage` 缺省（旧接口 / 未刷新）时沿用「能写就能管」的老口径，避免全站变只读。
 */
const canManageDataset = (dataset?: KnowledgeBase | null) => {
  if (!dataset) return false
  if (isAdmin.value) return true
  if (isReadOnlyDataset(dataset)) return false
  return dataset.can_manage ?? true
}

/** 归属人显示名：优先归属人，未回填的老库才回退创建人 */
const datasetOwnerUserLabel = (dataset?: KnowledgeBase | null) => {
  if (!dataset) return '未知'
  if (dataset.owner_user_id) {
    // 有 owner_user_id 却解析不出名字 = 账号已被删除。这里回退成创建人会让页面
    // 看起来「根本没转移过」，把问题藏起来；宁可显示成「用户#12（已删除）」，
    // 一眼就知道该重新指派人。
    return dataset.owner_user_name || `用户#${dataset.owner_user_id}（已删除）`
  }
  return dataset.created_by || '未知'
}

/**
 * 能否转移归属：编辑权限码 + 对该库的管理级写权限，与后端
 * `require_element_permission(element:knowledge:edit) + require_dataset_write_access` 同口径。
 * 部门成员只有内容级写权限，看不到这个入口。
 */
const canTransferOwner = (dataset?: KnowledgeBase | null) =>
  canEdit.value && canManageDataset(dataset)

const fetchTransferCandidates = async () => {
  loadingTransferCandidates.value = true
  try {
    const res = await axios.get('/api/portal/ragflow/owner-candidates', {
      params: { keyword: transferKeyword.value.trim() },
    })
    transferCandidates.value = apiData(res) || []
  } catch (err) {
    transferCandidates.value = []
    showToast('加载候选用户失败: ' + extractError(err), 'error')
  } finally {
    loadingTransferCandidates.value = false
  }
}

const openTransferOwner = async (dataset: KnowledgeBase) => {
  transferTarget.value = dataset
  transferUserId.value = null
  transferKeyword.value = ''
  showTransferOwnerModal.value = true
  await fetchTransferCandidates()
}

const closeTransferOwner = () => {
  showTransferOwnerModal.value = false
  transferTarget.value = null
  transferUserId.value = null
  transferCandidates.value = []
}

const submitTransferOwner = async () => {
  const target = transferTarget.value
  const userId = transferUserId.value
  if (!target || !userId || transferring.value) return
  transferring.value = true
  try {
    await axios.post(
      `/api/portal/ragflow/datasets/${target.ragflow_dataset_id || target.id}/owner`,
      { user_id: userId },
    )
    showToast('归属人已转移，原归属人的管理权已收回', 'success')
    closeTransferOwner()
    await fetchDatasets()
  } catch (err) {
    // 常见失败：目标账号在你打开弹窗后被停用、或你已被别人抢先转移走了管理权
    showToast(extractError(err), 'error')
    await fetchDatasets()
  } finally {
    transferring.value = false
  }
}

const isReadOnlyDataset = (dataset?: KnowledgeBase | null) => {
  if (!dataset) return false
  const name = dataset.platform_name || dataset.name
  if (name === 'chatbi-example-meta') return true
  if (dataset.is_read_only === true) return true
  if (dataset.can_write === false) return true
  return false
}

// ==================== 批量操作 ====================
// 后端早就支持 ids 数组，只是没暴露入口：库一多，逐个删/逐个解析是纯体力活。
// 默认关闭，避免破坏现有紧凑布局；开启后才在节点左侧出现勾选框。
const bulkSelectMode = ref(false)
const selectedDatasetIds = ref<string[]>([])
const selectedDocIds = ref<string[]>([])
/** 文档批量操作限定在单库内：跨库混选时「批量解析」的目标库会不明确 */
const bulkDocDatasetId = ref('')
const bulkWorking = ref(false)
const pendingBulkDelete = ref<string[] | null>(null)

const exitBulkMode = () => {
  bulkSelectMode.value = false
  selectedDatasetIds.value = []
  selectedDocIds.value = []
  bulkDocDatasetId.value = ''
}

const toggleDatasetSelection = (dataset: KnowledgeBase) => {
  const id = dataset.ragflow_dataset_id || dataset.id
  if (!id || !canManageDataset(dataset)) return
  selectedDatasetIds.value = selectedDatasetIds.value.includes(id)
    ? selectedDatasetIds.value.filter((item) => item !== id)
    : [...selectedDatasetIds.value, id]
}

const toggleDocSelection = (doc: KnowledgeDocument, datasetId: string) => {
  if (!doc?.id) return
  if (bulkDocDatasetId.value && bulkDocDatasetId.value !== datasetId) {
    // 换库了：清掉上一批，避免把 A 库的 docId 发到 B 库
    selectedDocIds.value = []
  }
  bulkDocDatasetId.value = datasetId
  selectedDocIds.value = selectedDocIds.value.includes(doc.id)
    ? selectedDocIds.value.filter((item) => item !== doc.id)
    : [...selectedDocIds.value, doc.id]
}

/**
 * 可批量选中的库：当前 tab + 当前搜索结果里、有管理权限的。
 * 刻意不跨 tab、不跨搜索：全选把看不见的库也选上，等于给自己埋雷。
 *
 * 失联库必须能选：单个勾选一直是允许的（toggleDatasetSelection 不限失联），
 * 只有全选把它排除了，两处口径打架。而失联库唯一的用途就是**批量清理**——
 * 元数据还在、RAGFlow 里已经没了，挨个点太折磨人。删除端点对这类 ID 会跳过
 * 远删、只标本地 deleted，选上批量删是安全的。
 */
const bulkSelectableDatasets = computed(() =>
  filteredDatasets.value.filter((dataset) => canManageDataset(dataset)),
)

const bulkAllSelected = computed(() => {
  if (selectedDocIds.value.length) {
    const docs = documentsMap.value[bulkDocDatasetId.value] || []
    return docs.length > 0 && docs.every((doc) => selectedDocIds.value.includes(doc.id))
  }
  return bulkSelectableDatasets.value.length > 0
    && bulkSelectableDatasets.value.every((dataset) =>
      selectedDatasetIds.value.includes(dataset.ragflow_dataset_id || dataset.id),
    )
})

const toggleSelectAll = () => {
  // 文档分支：全选当前库的文档
  if (selectedDocIds.value.length) {
    const docs = documentsMap.value[bulkDocDatasetId.value] || []
    selectedDocIds.value = bulkAllSelected.value ? [] : docs.map((doc) => doc.id)
    return
  }
  selectedDatasetIds.value = bulkAllSelected.value
    ? []
    : bulkSelectableDatasets.value.map((dataset) => dataset.ragflow_dataset_id || dataset.id)
}

const bulkDeleteDatasets = async () => {
  const ids = pendingBulkDelete.value
  if (!ids?.length || bulkWorking.value) return
  bulkWorking.value = true
  try {
    await axios.delete('/api/portal/ragflow/datasets', { data: { ids } })
    showToast(`已删除 ${ids.length} 个知识库`, 'success')
    pendingBulkDelete.value = null
    selectedDatasetIds.value = []
    await fetchDatasets()
    if (selectedType.value === 'dataset' && ids.includes(selectedId.value)) {
      if (datasets.value[0]) selectDatasetNode(datasets.value[0])
    }
  } catch (err) {
    showToast(extractError(err), 'error')
  } finally {
    bulkWorking.value = false
  }
}

const bulkParseDocuments = async () => {
  const datasetId = bulkDocDatasetId.value
  const ids = [...selectedDocIds.value]
  if (!datasetId || !ids.length || bulkWorking.value) return
  bulkWorking.value = true
  try {
    await axios.post(`/api/portal/ragflow/datasets/${datasetId}/documents/parse`, { ids })
    ids.forEach((id) => {
      parsingDocIds.value = { ...parsingDocIds.value, [id]: true }
    })
    showToast(`已触发 ${ids.length} 个文档解析`, 'success')
    selectedDocIds.value = []
    await fetchDocumentsForDataset(datasetId)
  } catch (err) {
    showToast(extractError(err), 'error')
  } finally {
    bulkWorking.value = false
  }
}

const bulkDeleteDocuments = async () => {
  const datasetId = bulkDocDatasetId.value
  const ids = [...selectedDocIds.value]
  if (!datasetId || !ids.length || bulkWorking.value) return
  bulkWorking.value = true
  try {
    await axios.delete(`/api/portal/ragflow/datasets/${datasetId}/documents`, {
      data: { ids },
    })
    showToast(`已删除 ${ids.length} 个文档`, 'success')
    const targetDataset = datasets.value.find(
      (item) => (item.ragflow_dataset_id || item.id) === datasetId,
    )
    if (targetDataset) {
      for (const docId of ids) await removeDocumentFromFolders(targetDataset, docId)
    }
    selectedDocIds.value = []
    await fetchDocumentsForDataset(datasetId)
  } catch (err) {
    showToast(extractError(err), 'error')
  } finally {
    bulkWorking.value = false
  }
}
// ==================== 批量操作结束 ====================

/** 当前详情页的库是否可写：只读成员只能看内容，不能改元数据/上传/授权 */
const selectedDatasetWritable = computed(() => canManageDataset(selectedDataset.value))

// ==================== 引用与授权（影响面） ====================
// 两件事必须分开看：
// - 智能体引用 = 智能体 engine_config.dataset_ids 里选中了本库，删库会让它检索不到内容；
// - 授权访问 = ResourcePermission 里给了角色/用户访问权，删库会让这些人失去入口。
// 给角色分配知识库**不会**产生智能体引用，两者不是同一件事，所以在同一个 tab 里并列说明。
const agentRefs = ref<{ id: string; name: string; display_name: string; is_enabled: boolean }[]>([])
const referencesLoading = ref(false)
const referencesError = ref('')

const loadDatasetReferences = async () => {
  const dsId = currentDatasetId()
  if (!dsId) return
  referencesLoading.value = true
  referencesError.value = ''
  try {
    const res = await axios.get(`/api/portal/ragflow/datasets/${dsId}/agents`)
    agentRefs.value = res.data?.data?.items || []
  } catch (err) {
    referencesError.value = extractError(err)
    agentRefs.value = []
  } finally {
    referencesLoading.value = false
  }
  // 授权名单是另一个接口，tab 里要一起展示；已加载过就不重复拉
  if (
    !permissionsRestricted.value
    && !datasetPermissions.value.users.length
    && !datasetPermissions.value.roles.length
  ) {
    void fetchDatasetPermissions(dsId)
  }
}

const openDatasetReferences = () => {
  detailTab.value = 'references'
  void loadDatasetReferences()
}

/** RAGFlow 用 enabled=0/1 表示文档是否参与检索；停用后切片仍在，只是不召回 */
const isDocDisabled = (doc?: KnowledgeDocument | null) => {
  if (!doc) return false
  // RAGFlow 可能回 0/1、也可能回 "0"/"1"，两种都要认，否则停用了却显示"参与检索"
  const enabled = (doc as { enabled?: unknown }).enabled
  return enabled === false || enabled === 0 || enabled === '0'

}

const renamingDoc = ref<KnowledgeDocument | null>(null)
const docRenameValue = ref('')
const docRenameBusy = ref(false)
const docToggleBusy = ref(false)

const currentDocDatasetId = () =>
  selectedDataset.value?.ragflow_dataset_id || selectedDataset.value?.id || ''

/** 本地就地更新，省一次列表请求：改名/启停只影响这一条 */
const patchDocumentLocally = (dsId: string, doc: KnowledgeDocument, patch: Partial<KnowledgeDocument>) => {
  const list = documentsMap.value[dsId]
  if (list) {
    const index = list.findIndex((item) => item.id === doc.id)
    const existing = index >= 0 ? list[index] : undefined
    if (existing) list[index] = { ...existing, ...patch }
  }
  if (selectedDocument.value?.id === doc.id) {
    selectedDocument.value = { ...selectedDocument.value, ...patch }
  }
}

const startRenameDocument = (doc: KnowledgeDocument) => {
  renamingDoc.value = doc
  docRenameValue.value = doc.name || ''
}

const confirmRenameDocument = async () => {
  const doc = renamingDoc.value
  const name = docRenameValue.value.trim()
  const dsId = currentDocDatasetId()
  if (!doc || !name || !dsId || docRenameBusy.value) return
  docRenameBusy.value = true
  try {
    await axios.put(`/api/portal/ragflow/datasets/${dsId}/documents/${doc.id}`, { name })
    patchDocumentLocally(dsId, doc, { name })
    renamingDoc.value = null
    showToast('已重命名。改名不会触发重新解析，已有切片保持不变', 'success')
  } catch (err) {
    showToast(extractError(err), 'error')
  } finally {
    docRenameBusy.value = false
  }
}

const toggleDocumentEnabled = async (doc: KnowledgeDocument) => {
  const dsId = currentDocDatasetId()
  if (!dsId || docToggleBusy.value) return
  const next = isDocDisabled(doc)
  docToggleBusy.value = true
  try {
    await axios.put(`/api/portal/ragflow/datasets/${dsId}/documents/${doc.id}`, { enabled: next })
    patchDocumentLocally(dsId, doc, { enabled: next })
    showToast(next ? '已启用，重新参与检索' : '已停用，不再参与检索召回（切片仍保留）', 'success')
  } catch (err) {
    showToast(extractError(err), 'error')
  } finally {
    docToggleBusy.value = false
  }
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
const chunksError = ref(false)

const openChunksModal = async (doc: any) => {
  viewingDocument.value = doc
  showChunksModal.value = true
  previewPage.value = null
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

// ---------- 切片增删改 ----------
const editingChunkId = ref<string | null>(null)
const editingChunkContent = ref('')
const newChunkContent = ref('')
const showNewChunkForm = ref(false)
const chunkSaving = ref(false)

/** RAGFlow 用 available / available_int 表示切片是否参与召回，两种都认 */
const isChunkDisabled = (chunk: any) => {
  const value = chunk?.available ?? chunk?.available_int
  return value === 0 || value === '0' || value === false
}

const currentChunkDatasetId = () =>
  selectedDataset.value?.ragflow_dataset_id || selectedDataset.value?.id || ''

const chunkTargetPath = (chunkId?: string) => {
  const dsId = currentChunkDatasetId()
  const docId = viewingDocument.value?.id
  if (!dsId || !docId) return ''
  const base = `/api/portal/ragflow/datasets/${dsId}/documents/${docId}/chunks`
  return chunkId ? `${base}/${chunkId}` : base
}

const startEditChunk = (chunk: any) => {
  editingChunkId.value = chunk.id
  editingChunkContent.value = chunk.content || ''
}

const cancelEditChunk = () => {
  editingChunkId.value = null
  editingChunkContent.value = ''
}

const saveChunk = async () => {
  const chunkId = editingChunkId.value
  const content = editingChunkContent.value.trim()
  const url = chunkTargetPath(chunkId || undefined)
  if (!chunkId || !content || !url || chunkSaving.value) return
  chunkSaving.value = true
  try {
    await axios.put(url, { content })
    const target = chunksList.value.find((item: any) => item.id === chunkId)
    if (target) {
      target.content = content
      target.content_with_weight = content
    }
    editingChunkId.value = null
    showToast('已保存，向量重建需数秒后生效', 'success')
  } catch (err) {
    showToast(extractError(err), 'error')
  } finally {
    chunkSaving.value = false
  }
}

const toggleChunk = async (chunk: any) => {
  const url = chunkTargetPath(chunk.id)
  if (!url || chunkSaving.value) return
  const next = isChunkDisabled(chunk)
  chunkSaving.value = true
  try {
    await axios.put(url, { available: next })
    chunk.available = next ? 1 : 0
    chunk.available_int = next ? 1 : 0
    showToast(next ? '已启用，重新参与召回' : '已停用，不再参与召回（内容仍保留）', 'success')
  } catch (err) {
    showToast(extractError(err), 'error')
  } finally {
    chunkSaving.value = false
  }
}

const removeChunk = async (chunk: any) => {
  const url = chunkTargetPath(chunk.id)
  if (!url || chunkSaving.value) return
  chunkSaving.value = true
  try {
    await axios.delete(url)
    chunksList.value = chunksList.value.filter((item: any) => item.id !== chunk.id)
    chunksTotal.value = Math.max(0, chunksTotal.value - 1)
    showToast('已删除切片', 'success')
  } catch (err) {
    showToast(extractError(err), 'error')
  } finally {
    chunkSaving.value = false
  }
}

const createChunk = async () => {
  const content = newChunkContent.value.trim()
  const url = chunkTargetPath()
  if (!content || !url || chunkSaving.value) return
  chunkSaving.value = true
  try {
    await axios.post(url, { content })
    newChunkContent.value = ''
    showNewChunkForm.value = false
    showToast('已新增切片，向量重建需数秒后生效', 'success')
    await fetchChunks()
  } catch (err) {
    showToast(extractError(err), 'error')
  } finally {
    chunkSaving.value = false
  }
}

const showDocPreview = ref(false)
/** 从切片跳原文时的目标页；null 表示普通预览（不带页锚） */
const previewPage = ref<number | null>(null)
const previewDocUrl = computed(() => {
  if (!selectedDatasetId.value || !selectedDocument.value) return ''
  const dsId = encodeURIComponent(selectedDatasetId.value)
  const docId = encodeURIComponent(selectedDocument.value.id)
  const base = `/api/portal/ragflow/datasets/${dsId}/documents/${docId}/file`
  return previewPage.value ? `${base}#page=${previewPage.value}` : base
})

const isPdfSelectedDocument = computed(() =>
  PDF_EXTENSIONS.has(getWorkspaceFileExtension(selectedDocument.value?.name || '')),
)
/**
 * PDF 要跳页时必须用 iframe：浏览器原生 PDF 阅读器认 `#page=N`，
 * 而 file-viewer 的 pdf 渲染器不认这个 fragment。
 */
const mustUseIframeForPage = computed(
  () => isPdfSelectedDocument.value && previewPage.value !== null,
)

/**
 * 交给 DocumentViewer 渲染的格式：Office + 文本（canPreviewWithDocumentViewer），
 * **外加 PDF**。
 *
 * PDF 在知识库这里并入，是有意的、而且**只在这一个调用点并入**：知识库「预览原文件」
 * 没有引用页跳转需求，用它换取统一工具栏（搜索/缩放/打印/目录），并摆脱对浏览器原生
 * PDF 阅读器的依赖（移动端与部分嵌入 WebView 会直接下载而不是预览）。
 *
 * RAG 引用抽屉必须保持 iframe —— 它靠 `#page=N` 跳到引用页，而 file-viewer 的 pdf
 * 渲染器不认这个 URL fragment。所以 `.pdf` 绝不能加进共享的
 * DOCUMENT_VIEWER_EXTENSIONS（详见 utils/documentPreviewFormats.ts 的 PDF_EXTENSIONS）。
 *
 * 其余 file-viewer 不支持的格式（.rst/.env/.ppt 等）继续走 iframe 兜底。
 */
const canPreviewSelectedDocument = computed(() => {
  // 需要跳页的 PDF 交给 iframe，DocumentViewer 不认 #page
  if (mustUseIframeForPage.value) return false
  const name = selectedDocument.value?.name || ''
  return (
    canPreviewWithDocumentViewer(name) ||
    PDF_EXTENSIONS.has(getWorkspaceFileExtension(name))
  )
})

/** 组件内部下载兜底的接收方；与画布版 downloadWorkspaceFile 一致的临时 a 标签写法 */
const downloadPreviewDocument = () => {
  if (!previewDocUrl.value) return
  const link = document.createElement('a')
  link.href = previewDocUrl.value
  link.download = selectedDocument.value?.name || ''
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}

const openDocPreview = () => {
  if (!previewDocUrl.value) return
  showDocPreview.value = true
}
const closeDocPreview = () => {
  showDocPreview.value = false
  previewPage.value = null
}

/** 切片里能拿到的页码：优先 page_no，其次 positions[].page_no（JSON 字符串） */
const resolveChunkPage = (chunk: any): number | null => {
  const direct = Number(chunk?.page_no ?? chunk?.page ?? chunk?.page_number)
  if (Number.isFinite(direct) && direct > 0) return Math.floor(direct)

  const raw = chunk?.positions ?? chunk?.position
  if (!raw) return null
  try {
    const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw
    const list = Array.isArray(parsed) ? parsed : [parsed]
    for (const item of list) {
      const candidate = Number(item?.page_no ?? item?.page ?? item?.page_number)
      if (Number.isFinite(candidate) && candidate > 0) return Math.floor(candidate)
    }
  } catch {
    return null
  }
  return null
}

const openChunkSource = (chunk: any) => {
  const page = resolveChunkPage(chunk)
  if (!selectedDocument.value || selectedDocument.value.id !== viewingDocument.value?.id) {
    // 抽屉里的文档必须就是详情页选中的那份，否则预览会打开另一份文件
    showToast('请从文档详情页打开切片后再查看原文', 'warning')
    return
  }
  previewPage.value = page
  closeChunksModal()
  showDocPreview.value = true
}

const CHUNKS_PAGE_SIZE = 30
const chunksTotalPages = computed(() => Math.max(1, Math.ceil(chunksTotal.value / CHUNKS_PAGE_SIZE)))
let chunksReqSeq = 0

const loadChunksPage = async (page: number) => {
  const prevPage = chunksPage.value
  chunksPage.value = page
  await fetchChunks()
  // 失败时回滚页码，避免「页码前进了但内容还是上一页」
  if (chunksError.value) chunksPage.value = prevPage
}

const fetchChunks = async () => {
  if (!viewingDocument.value) return
  const seq = ++chunksReqSeq
  loadingChunks.value = true
  chunksError.value = false
  try {
    const res = await axios.get(`/api/portal/ragflow/datasets/${selectedDatasetId.value}/documents/${viewingDocument.value.id}/chunks`, {
      params: {
        page: chunksPage.value,
        page_size: CHUNKS_PAGE_SIZE
      }
    })
    // 快速切文档时旧响应可能后到，丢弃非最新请求的结果
    if (seq !== chunksReqSeq) return
    const resData = res.data?.data || {}
    chunksList.value = resData.chunks || []
    chunksTotal.value = resData.total || resData.chunks?.length || 0
  } catch (err) {
    if (seq !== chunksReqSeq) return
    chunksError.value = true
    showToast(extractError(err), 'error')
  } finally {
    if (seq === chunksReqSeq) loadingChunks.value = false
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
  // 置位后，正在 await 中的 poll 不会再把自己重新挂回定时器
  isUnmounted = true
  for (const dsId of datasetPollTimers.keys()) {
    stopDatasetPolling(dsId)
  }
})

// 运营分析页下钻：?dataset=<id> 直接定位到对应知识库
const applyDeepLinkFromQuery = () => {
  const targetDatasetId = String(route.query.dataset || '').trim()
  if (!targetDatasetId) return
  const dataset = datasets.value.find(
    (item) => (item.ragflow_dataset_id || item.id) === targetDatasetId
  )
  if (!dataset) return
  // 目标库可能是 TEAM 级/私有：左侧 Tab 若停在授权可见就根本看不到这个节点，
  // 表现为「右侧凭空选中了一个左侧不存在的库」。
  activeVisibility.value = visibilityOf(dataset)
  selectDatasetNode(dataset)
}

onMounted(async () => {
  await fetchRagFlowConfig()
  // 部门目录与知识库列表互不依赖：并行取，部门加载失败不影响列表可用
  await Promise.all([fetchDepartmentOptions(), fetchDatasets()])
  applyDeepLinkFromQuery()
})

// 知识库 5 步全流程指引状态管理
const KNOWLEDGE_FLOW_GUIDE_DISMISSED_KEY = 'nanzi_knowledge_flow_guide_dismissed'
const showHelp = ref(false)
const activeHelpTab = ref<'flow' | 'chunking' | 'retrieval'>('flow')
const showKnowledgeFlowGuide = ref(localStorage.getItem(KNOWLEDGE_FLOW_GUIDE_DISMISSED_KEY) !== '1')

const closeKnowledgeFlowGuide = () => {
  showKnowledgeFlowGuide.value = false
}

const dismissKnowledgeFlowGuide = () => {
  showKnowledgeFlowGuide.value = false
  localStorage.setItem(KNOWLEDGE_FLOW_GUIDE_DISMISSED_KEY, '1')
  showToast('已设为不再提示流程指引，可在标题旁重新开启', 'info')
}

const restoreKnowledgeFlowGuide = () => {
  showKnowledgeFlowGuide.value = true
  localStorage.removeItem(KNOWLEDGE_FLOW_GUIDE_DISMISSED_KEY)
}

const handleFlowGuideAction = (type: 'create' | 'sync') => {
  if (type === 'create') {
    openCreate()
  } else if (type === 'sync') {
    syncFromRagFlow()
  }
}
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
    <div class="flex flex-col lg:flex-row lg:items-center justify-between gap-4 bg-white p-5 rounded-2xl border border-gray-200/80 shadow-sm">
      <div class="flex min-w-0 flex-col gap-1">
        <div class="flex items-center gap-3">
          <h1 class="text-2xl font-bold text-gray-900 tracking-tight">知识库管理</h1>
          <button
            type="button"
            class="hidden sm:flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-800 dark:hover:text-gray-300 cursor-pointer"
            title="知识库设计规范与全流程指引"
            @click="showHelp = true"
          >
            <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093M12 17h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          </button>
          <button
            v-if="!showKnowledgeFlowGuide"
            type="button"
            class="hidden sm:inline shrink-0 whitespace-nowrap text-xs font-medium text-gray-500 underline-offset-2 transition-colors hover:text-primary hover:underline cursor-pointer"
            title="重新展开知识库全流程指引"
            @click="restoreKnowledgeFlowGuide"
          >
            <span class="whitespace-nowrap">显示指引</span>
          </button>
        </div>
        <p class="text-sm text-gray-500 mt-0.5">管理 RAGFlow 知识库、平台扩展元数据及一体化文档解析。</p>
      </div>
      <!--
        操作区：窄屏一行图标，lg 起恢复文字按钮。
        四个文字按钮并排会把中文挤成两三行，两行又占竖向空间，所以窄屏只留图标；
        顺序仍是 引擎状态 → 同步 → 刷新 → 新建，图标按钮用 aria-label 保留语义。
      -->
      <div class="flex shrink-0 items-center gap-2 lg:gap-3">
        <!-- 引擎连接指示器 -->
        <div
          class="flex h-10 min-w-0 items-center gap-2 rounded-xl border px-2.5 text-xs transition-colors group relative cursor-pointer lg:h-auto lg:px-3 lg:py-1.5"
          :class="{
            'border-blue-200 bg-blue-50/50 text-blue-700': isKnowledgeEnabled && engineStatus === 'checking',
            'border-emerald-200 bg-emerald-50/50 text-emerald-700': isKnowledgeEnabled && engineStatus === 'connected',
            'border-amber-200 bg-amber-50/50 text-amber-700': !isKnowledgeEnabled || engineStatus === 'disconnected'
          }"
        >
          <span
            class="inline-block h-2 w-2 shrink-0 rounded-full"
            :class="{
              'bg-blue-500 animate-pulse': engineStatus === 'checking',
              'bg-emerald-500': engineStatus === 'connected',
              'bg-amber-500': engineStatus === 'disconnected'
            }"
          ></span>
          <span class="whitespace-nowrap font-medium lg:hidden">{{ engineStatusShort }}</span>
          <span class="hidden whitespace-nowrap font-medium lg:inline">引擎 {{ engineStatusText }}</span>

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
          type="button"
          class="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-emerald-200 bg-emerald-50 text-emerald-700 transition-all hover:bg-emerald-100 disabled:opacity-50 lg:h-auto lg:w-auto lg:px-4 lg:py-2"
          :aria-label="syncing ? '正在从 RAGFlow 同步' : '从 RAGFlow 同步'"
          :disabled="syncing || !isEngineReady"
          @click="syncFromRagFlow"
        >
          <svg class="h-5 w-5 lg:hidden" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M12 9.75v6.75m0 0l-3-3m3 3l3-3m-8.25 6a4.5 4.5 0 01-1.41-8.775 5.25 5.25 0 0110.233-2.33 3 3 0 013.758 3.848A3.752 3.752 0 0118 19.5H6.75z" />
          </svg>
          <span class="hidden whitespace-nowrap text-sm font-medium lg:inline">{{ syncing ? '同步中...' : '从 RAGFlow 同步' }}</span>
        </button>
        <button
          type="button"
          class="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-gray-200 bg-white text-gray-600 transition-all hover:bg-gray-50 lg:h-auto lg:w-auto lg:px-4 lg:py-2"
          aria-label="刷新知识库列表"
          @click="fetchDatasets(true)"
        >
          <svg class="h-5 w-5 lg:hidden" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99" />
          </svg>
          <span class="hidden whitespace-nowrap text-sm font-medium lg:inline">刷新</span>
        </button>
        <button
          v-if="isAdmin"
          type="button"
          class="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-gray-200 bg-white text-gray-600 transition-all hover:bg-gray-50"
          aria-label="全平台审计"
          title="全平台审计：按人 / 动作 / 时间查所有库的操作记录"
          @click="openGlobalAudit"
        >
          <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751A11.959 11.959 0 0112 2.714z" />
          </svg>
        </button>
        <button
          v-if="canCreate"
          type="button"
          class="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary text-white shadow-sm transition-all hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50 lg:h-auto lg:w-auto lg:px-4 lg:py-2"
          aria-label="新建知识库"
          :disabled="!isEngineReady"
          @click="openCreate"
        >
          <svg class="h-5 w-5 lg:hidden" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4.5v15m7.5-7.5h-15" />
          </svg>
          <span class="hidden whitespace-nowrap text-sm font-medium lg:inline">新建知识库</span>
        </button>
      </div>
    </div>

    <!-- 知识库 5 步全流程指引横幅 -->
    <KnowledgeFlowGuideBanner
      v-if="showKnowledgeFlowGuide"
      :engine-status="engineStatus"
      :is-engine-ready="isEngineReady"
      :ragflow-api-url="ragflowApiUrl"
      @action="handleFlowGuideAction"
      @close="closeKnowledgeFlowGuide"
      @dismiss="dismissKnowledgeFlowGuide"
    />

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
      <!-- 右上角操作区 -->
      <div class="absolute top-3.5 right-3.5 flex items-center gap-1">
        <button
          @click="retryAfterError"
          class="rounded-lg border border-amber-300 bg-white/70 px-2.5 py-1 text-xs font-semibold text-amber-700 hover:bg-amber-100 transition-colors"
          title="重新加载知识库列表"
        >
          重试
        </button>
        <button @click="showErrorBanner = false" class="p-1 text-amber-500 hover:text-amber-700 transition-colors" title="关闭">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </div>

    <!-- Main Workspace Layout -->
    <div class="flex flex-col lg:flex-row gap-6 min-h-[calc(100vh-16rem)] items-stretch">
      
      <!-- Left Side: Knowledge base & Document tree navigation -->
      <section class="w-full lg:w-96 flex-shrink-0 bg-white rounded-2xl border border-gray-200 shadow-sm flex flex-col overflow-hidden">
        <!-- 引擎挂了但本地有上次快照：明确告诉用户"这是旧数据"，别让他以为库真的被删了 -->
        <div
          v-if="datasetsStale"
          class="mx-3 mt-3 rounded-xl bg-amber-50 border border-amber-200 px-3 py-2 text-[11px] text-amber-700 flex items-start gap-2"
        >
          <span class="shrink-0">⚠️</span>
          <span class="flex-1 leading-relaxed">知识库引擎连接异常，当前展示的是上次成功获取的数据，可能不是最新。</span>
          <button type="button" class="shrink-0 font-semibold hover:underline" @click="fetchDatasets(true)">刷新</button>
        </div>
        <div class="p-4 border-b border-gray-100/80 bg-gray-50/50">
          <!-- 搜索框 + 批量入口同一行：开关常驻但不占额外一行，点开才在树顶展开操作条 -->
          <div class="flex items-center gap-2">
            <div class="relative flex-1 min-w-0">
              <input
                v-model="searchQuery"
                type="search"
                placeholder="搜索知识库名称、ID、文档或业务归属部门..."
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
            <button
              v-if="!bulkSelectMode && !isAdmin"
              type="button"
              class="shrink-0 px-2.5 py-2 rounded-xl border border-gray-200 bg-white text-[11px] font-semibold text-gray-500 hover:border-primary hover:text-primary transition-all flex items-center gap-1"
              title="发现：浏览你还没有权限的公共 / 部门知识库，直接申请访问"
              @click="openDiscover"
            >
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24" aria-hidden="true">
                <path stroke-linecap="round" stroke-linejoin="round" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                <path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              发现
            </button>
            <button
              v-if="!bulkSelectMode && datasets.length"
              type="button"
              class="shrink-0 px-2.5 py-2 rounded-xl border border-gray-200 bg-white text-[11px] font-semibold text-gray-500 hover:border-primary hover:text-primary transition-all"
              title="批量删除知识库、批量解析/删除文档"
              @click="bulkSelectMode = true"
            >批量</button>
          </div>

          <!-- 可见范围 tab：三档对应真实权限语义（授权可见需授权 / TEAM 级自动只读 / 个人私有仅自己） -->
          <div class="mt-2 flex items-center gap-1 p-1 bg-gray-100 rounded-xl">
            <button
              v-for="tab in VISIBILITY_TABS"
              :key="tab.key"
              type="button"
              class="flex-1 flex items-center justify-center gap-1 px-1.5 py-1.5 rounded-lg text-xs font-semibold transition-all"
              :class="activeVisibility === tab.key ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'"
              :title="tab.hint"
              @click="activeVisibility = tab.key"
            >
              <!-- 图标与切换弹窗共用同一份定义（visibilityOptionOf），不另写字面量 -->
              <svg
                class="w-3.5 h-3.5 shrink-0"
                :class="visibilityOptionOf(tab.key).iconClass"
                fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"
                aria-hidden="true"
              >
                <path :d="visibilityOptionOf(tab.key).iconPath" />
              </svg>
              <span class="truncate">{{ tab.label }}</span>
              <span
                class="text-[10px] px-1 rounded-full font-bold"
                :class="activeVisibility === tab.key ? 'bg-primary/10 text-primary' : 'bg-gray-200 text-gray-500'"
              >{{ visibilityTabCount(tab.key) }}</span>
            </button>
          </div>
          <div v-if="searchDocLoading" class="mt-1.5 text-[11px] text-gray-400">正在检索文档名…</div>
        </div>

        <!-- Scrollable tree region -->
        <div class="flex-1 overflow-y-auto p-4 space-y-1 select-none">
          <!-- 批量操作条：默认隐藏，点「批量管理」才出现，避免破坏现有紧凑布局 -->
          <div
            v-if="bulkSelectMode"
            class="flex items-center justify-between gap-2 rounded-xl bg-primary/5 border border-primary/20 px-2.5 py-2 mb-2 text-[11px]"
          >
            <span class="flex items-center gap-1.5 min-w-0 font-semibold text-primary">
              <span class="truncate">
                <template v-if="selectedDocIds.length">已选 {{ selectedDocIds.length }} 个文档</template>
                <template v-else>已选 {{ selectedDatasetIds.length }} 个知识库</template>
              </span>
              <button
                type="button"
                class="shrink-0 font-normal text-primary/70 hover:text-primary hover:underline"
                :disabled="(!selectedDocIds.length && !bulkSelectableDatasets.length) || bulkWorking"
                :title="selectedDocIds.length
                  ? '全选当前知识库的所有文档'
                  : '全选当前 Tab 与搜索结果里的知识库'"
                @click="toggleSelectAll"
              >{{ bulkAllSelected ? '取消全选' : '全选' }}</button>
            </span>
            <div class="flex items-center gap-1.5 shrink-0">
              <template v-if="selectedDocIds.length">
                <button
                  type="button"
                  class="px-2 py-1 rounded-lg bg-white border border-gray-200 text-gray-600 font-semibold hover:bg-gray-50 disabled:opacity-50"
                  :disabled="bulkWorking"
                  @click="bulkParseDocuments"
                >批量解析</button>
                <button
                  type="button"
                  class="px-2 py-1 rounded-lg bg-red-600 text-white font-semibold hover:bg-red-700 disabled:opacity-50"
                  :disabled="bulkWorking"
                  @click="bulkDeleteDocuments"
                >批量删除</button>
              </template>
              <button
                v-else
                type="button"
                class="px-2 py-1 rounded-lg bg-red-600 text-white font-semibold hover:bg-red-700 disabled:opacity-50"
                :disabled="!selectedDatasetIds.length || bulkWorking"
                @click="pendingBulkDelete = [...selectedDatasetIds]"
              >批量删除</button>
              <button type="button" class="text-gray-500 hover:text-gray-700" @click="exitBulkMode">退出</button>
            </div>
          </div>
          <div v-if="loading" class="text-center text-gray-400 py-12 text-sm flex flex-col items-center gap-3">
            <span class="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin"></span>
            <span>加载数据中...</span>
          </div>
          <div v-else-if="isSearchEmpty" class="text-center text-gray-400 py-12 text-sm italic flex flex-col items-center gap-3">
            <span>没有匹配「{{ searchQuery }}」的知识库</span>
            <!--
              搜索只在当前分类（授权可见/TEAM 级/个人私有）内进行：按 ID 搜索时，
              库若在别的分类里就会「查无此库」，不说清楚容易被误判成 ID 错了。
            -->
            <span class="text-[11px] text-gray-400 not-italic">
              当前只在「{{ visibilityOptionOf(activeVisibility).label }}」分类下搜索，可切换上方分类再试
            </span>
            <button type="button" class="text-xs font-semibold text-primary hover:underline" @click="searchQuery = ''">清除搜索条件</button>
          </div>
          <div v-else-if="deptDatasetGroups.length === 0" class="text-center text-gray-400 py-12 text-sm italic flex flex-col items-center gap-3">
            <span>{{ emptyVisibilityText }}</span>
            <div class="flex items-center gap-2">
              <button
                v-if="canCreate"
                type="button"
                class="px-3 py-1.5 rounded-lg bg-primary text-white text-xs font-semibold hover:bg-primary/90 transition-all"
                @click="openCreate"
              >
                新建知识库
              </button>
              <button
                v-if="!isAdmin"
                type="button"
                class="px-3 py-1.5 rounded-lg border border-gray-200 bg-white text-xs font-semibold text-gray-600 hover:border-primary hover:text-primary transition-all"
                @click="openDiscover"
              >
                去发现已有的库
              </button>
            </div>
          </div>
          <div v-else class="space-y-1">
            <div v-for="group in deptDatasetGroups" :key="group.key" class="space-y-1">
              <!-- 部门分组头：仅 TEAM 级 tab 渲染，授权可见/个人私有 tab 是平铺列表 -->
              <div
                v-if="!group.isFlat && !isDeptGroupHidden(group)"
                class="group/dept flex items-center justify-between gap-2 px-2 py-1.5 rounded-lg cursor-pointer select-none hover:bg-gray-50 transition-all"
                :class="group.isUnassigned ? 'text-gray-500' : 'text-gray-600'"
                :style="{ marginLeft: `${group.level * 12}px` }"
                :title="group.isUnassigned
                  ? '未关联部门的库（含历史自由文本归属）'
                  : `业务归属部门：${group.label}`"
                @click="toggleDeptGroup(group)"
              >
                <div class="flex items-center gap-1.5 min-w-0 flex-1">
                  <svg
                    class="w-3 h-3 shrink-0 text-gray-400 transition-transform duration-200"
                    :class="{ '-rotate-90': isDeptGroupCollapsed(group) }"
                    fill="none" stroke="currentColor" viewBox="0 0 24 24"
                  >
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7" />
                  </svg>
                  <svg class="w-3.5 h-3.5 shrink-0" :class="group.isUnassigned ? 'text-gray-400' : 'text-indigo-500'" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                  </svg>
                  <span class="truncate text-[11px] font-bold uppercase tracking-wider">{{ group.label }}</span>
                </div>
                <span
                  class="text-[10px] px-1.5 py-0.5 rounded-full font-semibold shrink-0"
                  :class="group.isUnassigned ? 'bg-gray-100 text-gray-500' : 'bg-indigo-50 text-indigo-600'"
                  :title="group.hasChildren ? `含下级部门共 ${group.totalCount} 个知识库` : `${group.totalCount} 个知识库`"
                >
                  {{ group.totalCount }}
                </span>
              </div>

              <div
                v-show="isDeptGroupOpen(group)"
                class="space-y-1"
                :class="group.isFlat ? '' : 'pl-3 ml-2 border-l border-gray-100'"
              >
                <div v-for="dataset in group.datasets" :key="dataset.ragflow_dataset_id || dataset.id" class="space-y-1">
              
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
                  <input
                    v-if="bulkSelectMode && canManageDataset(dataset)"
                    type="checkbox"
                    class="w-3.5 h-3.5 shrink-0 rounded border-gray-300 text-primary focus:ring-primary cursor-pointer"
                    :checked="selectedDatasetIds.includes(dataset.ragflow_dataset_id || dataset.id)"
                    :aria-label="`选择 ${dataset.platform_name || dataset.name}`"
                    @click.stop
                    @change="toggleDatasetSelection(dataset)"
                  />
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
                    <div class="flex items-center gap-1.5 min-w-0">
                      <span class="truncate text-sm font-medium">{{ dataset.platform_name || dataset.name }}</span>
                      <!--
                        失联标记常驻在名称旁：原先它挂在右侧、悬停即 hidden，
                        鼠标一放上去标签就消失、被操作按钮顶替，于是既读不到状态
                        也看不到解释——这正是"为什么放上去变成按钮"的由来。
                      -->
                      <HoverTip
                        v-if="dataset.is_missing_in_ragflow"
                        class="shrink-0"
                        :text="MISSING_DATASET_HINT"
                        :width="300"
                      >
                        <span class="text-[10px] px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 font-semibold select-none">
                          失联
                        </span>
                      </HoverTip>
                      <!--
                        引用数徽章也放名称旁：原先它在右侧指标区，而那片区域在悬停时
                        会被绝对定位的操作按钮覆盖 —— 徽章不会隐藏，于是和按钮叠在一起。
                        状态类信息统一靠左，右侧整块留给按钮。
                      -->
                      <HoverTip
                        v-if="(dataset.agent_ref_count || 0) > 0"
                        class="shrink-0"
                        :text="`被 ${dataset.agent_ref_count} 个智能体引用，删除后它们将检索不到本库内容`"
                        :width="240"
                      >
                        <span class="text-[10px] px-1.5 py-0.5 rounded-full bg-blue-50 text-blue-600 font-semibold select-none whitespace-nowrap">
                          🤖 {{ dataset.agent_ref_count }}
                        </span>
                      </HoverTip>
                    </div>
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
                  <!-- 失联时右侧不重复标注：状态已常驻在名称旁，避免悬停时被按钮顶替 -->

                  <!-- Hover Action Buttons -->
                  <div class="flex opacity-100 md:opacity-0 md:group-hover:opacity-100 md:focus-within:opacity-100 items-center gap-1 bg-inherit pl-2 absolute right-3 top-1/2 -translate-y-1/2">
                    <HoverTip
                      v-if="canEdit && canManageDataset(dataset)"
                      text="新建虚拟文件夹"
                      :width="160"
                    >
                      <button
                        class="p-1 rounded hover:bg-gray-200/60 text-gray-500 hover:text-primary transition-all bg-inherit"
                        @click.stop="startCreateFolder(dataset)"
                      >
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 13h6m-3-3v6m-9 1V7a2 2 0 012-2h6l2 2h6a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z" />
                        </svg>
                      </button>
                    </HoverTip>
                    <HoverTip
                      v-if="canEdit && canManageDataset(dataset)"
                      text="修改元数据"
                      :width="140"
                    >
                      <button
                        class="p-1 rounded hover:bg-gray-200/60 text-gray-500 hover:text-primary transition-all bg-inherit"
                        @click.stop="openEdit(dataset)"
                      >
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                        </svg>
                      </button>
                    </HoverTip>
                    <HoverTip
                      v-if="canDelete && canManageDataset(dataset)"
                      :text="dataset.is_missing_in_ragflow ? '清理失效残留配置（不会去 RAGFlow 删除）' : '删除知识库'"
                      :width="dataset.is_missing_in_ragflow ? 240 : 140"
                    >
                      <button
                        class="p-1 rounded hover:bg-red-50 text-gray-400 hover:text-red-600 transition-all bg-inherit"
                        @click.stop="triggerDeleteDataset(dataset)"
                      >
                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                        </svg>
                      </button>
                    </HoverTip>
                  </div>
                </div>
              </div>

              <!-- Document Nodes (Children) -->
              <transition name="slide-down">
                <div
                  v-show="expandedDatasetIds[dataset.ragflow_dataset_id || dataset.id]"
                  class="pl-4 ml-4 border-l border-gray-200 space-y-0.5 overflow-hidden"
                >
                  <!-- Loader for docs -->
                  <div v-if="documentsLoadingMap[dataset.ragflow_dataset_id || dataset.id]" class="py-2 pl-3 text-xs text-gray-400 flex items-center gap-1.5">
                    <span class="w-3 h-3 rounded-full border border-gray-300 border-t-transparent animate-spin"></span>
                    <span>加载文档...</span>
                  </div>
                  <!-- Empty hint -->
                  <div v-else-if="documentsErrorMap[dataset.ragflow_dataset_id || dataset.id]" class="py-2 pl-3 text-xs text-amber-600 flex items-center gap-2">
                    <span>文档列表加载失败</span>
                    <button
                      type="button"
                      class="font-semibold hover:underline"
                      @click.stop="fetchDocumentsForDataset(dataset.ragflow_dataset_id || dataset.id)"
                    >
                      重试
                    </button>
                  </div>
                  <div v-else-if="!(documentsMap[dataset.ragflow_dataset_id || dataset.id]?.length)" class="py-2 pl-3 text-xs text-gray-400 italic">
                    暂无文档
                  </div>
                  <!-- Documents loop with virtual folders support -->
                  <div v-else class="space-y-1 w-full">
                    <!-- 新建文件夹 Inline 输入框 -->
                    <div 
                      v-if="editingDatasetIdForFolder === (dataset.ragflow_dataset_id || dataset.id)"
                      class="flex items-center gap-1.5 pl-3 pr-2 py-1 bg-gray-50 rounded-lg border border-dashed border-gray-300"
                    >
                      <svg class="w-3.5 h-3.5 text-gray-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                      </svg>
                      <input 
                        v-model="newFolderName"
                        type="text" 
                        class="flex-1 min-w-0 bg-transparent text-xs outline-none border-b border-primary/50 text-gray-700 py-0.5" 
                        placeholder="文件夹名称..." 
                        @keyup.enter="confirmCreateFolder(dataset)"
                        @keyup.esc="editingDatasetIdForFolder = null"
                      />
                      <button 
                        class="text-[10px] text-primary font-bold hover:text-primary-hover px-1 cursor-pointer"
                        @click="confirmCreateFolder(dataset)"
                      >
                        确定
                      </button>
                      <button 
                        class="text-[10px] text-gray-400 hover:text-gray-600 px-1 cursor-pointer"
                        @click="editingDatasetIdForFolder = null"
                      >
                        取消
                      </button>
                    </div>

                    <!-- 文件夹层级渲染 -->
                    <template v-if="dataset.local_metadata?.extra_config?.folder_structure && Object.keys(dataset.local_metadata.extra_config.folder_structure).length">
                      <div 
                        v-for="fName in Object.keys(dataset.local_metadata.extra_config.folder_structure)"
                        :key="fName"
                        class="space-y-0.5"
                      >
                        <!-- 文件夹行 -->
                        <div 
                          class="group/folder relative flex items-center justify-between pl-2 pr-2 py-1.5 rounded-lg cursor-pointer hover:bg-gray-50/70 transition-all text-gray-700 select-none"
                          @click="toggleFolderCollapse(dataset.ragflow_dataset_id || dataset.id, fName)"
                        >
                          <div class="flex items-center gap-1.5 min-w-0 flex-1">
                            <!-- 折叠展开小尖角 -->
                            <svg 
                              class="w-3 h-3 text-gray-400 transition-transform shrink-0" 
                              :class="{ '-rotate-90': isFolderCollapsed(dataset.ragflow_dataset_id || dataset.id, fName) }"
                              fill="none" stroke="currentColor" viewBox="0 0 24 24"
                            >
                              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7" />
                            </svg>
                            <!-- 文件夹图标 -->
                            <svg class="w-3.5 h-3.5 text-amber-500 shrink-0" fill="currentColor" viewBox="0 0 20 20">
                              <path fill-rule="evenodd" d="M2 6a2 2 0 012-2h4l2 2h4a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" clip-rule="evenodd" />
                            </svg>
                            
                            <!-- 重命名输入框 -->
                            <div 
                              v-if="editingFolderKey === `${dataset.ragflow_dataset_id || dataset.id}_${fName}`"
                              class="flex items-center gap-1 min-w-0 flex-1"
                              @click.stop
                            >
                              <input 
                                v-model="editingFolderNewName"
                                type="text" 
                                class="w-full bg-white text-xs border border-primary px-1 rounded outline-none" 
                                @keyup.enter="confirmRenameFolder(dataset, fName)"
                                @keyup.esc="editingFolderKey = null"
                              />
                            </div>
                            <!-- 普通文件夹名称 -->
                            <span v-else class="truncate text-xs font-semibold text-gray-700">{{ fName }}</span>
                          </div>

                          <!-- 文件夹文件数量计数徽章 -->
                          <span 
                            class="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-500 font-mono scale-90 group-hover/folder:hidden shrink-0"
                          >
                            {{ dataset.local_metadata?.extra_config?.folder_structure?.[fName]?.length || 0 }}
                          </span>

                          <!-- 文件夹重命名/删除按钮 -->
                          <div 
                            v-if="canEdit && canManageDataset(dataset)"
                            class="flex opacity-100 md:opacity-0 md:group-hover/folder:opacity-100 md:focus-within:opacity-100 items-center gap-1 bg-inherit pl-2 z-30"
                            @click.stop
                          >
                            <button 
                              class="p-0.5 rounded hover:bg-gray-100 text-gray-400 hover:text-primary transition-all bg-inherit" 
                              title="重命名文件夹"
                              @click="startRenameFolder(dataset.ragflow_dataset_id || dataset.id, fName)"
                            >
                              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                              </svg>
                            </button>
                            <button 
                              class="p-0.5 rounded hover:bg-red-50 text-gray-400 hover:text-red-600 transition-all bg-inherit" 
                              title="删除文件夹"
                              @click="removeFolder(dataset, fName)"
                            >
                              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                              </svg>
                            </button>
                          </div>
                        </div>

                        <!-- 文件夹内部的文件子节点 -->
                        <transition name="slide-down">
                          <div 
                            v-show="!isFolderCollapsed(dataset.ragflow_dataset_id || dataset.id, fName)"
                            class="pl-4 ml-4 border-l border-gray-100/80 space-y-0.5"
                          >
                            <div 
                              v-for="doc in (documentsMap[dataset.ragflow_dataset_id || dataset.id] || []).filter(d => dataset.local_metadata?.extra_config?.folder_structure?.[fName]?.includes(d.id))"
                              :key="doc.id"
                              class="group/doc relative flex items-center justify-between pl-3 pr-2 py-1.5 rounded-lg cursor-pointer transition-all border border-transparent text-gray-500 hover:bg-gray-50"
                              :class="[
                                selectedType === 'document' && selectedId === doc.id
                                  ? 'bg-blue-50/70 border-blue-100 text-blue-700 font-semibold shadow-sm'
                                  : 'text-gray-500'
                              ]"
                              @click="selectDocumentNode(doc, dataset)"
                            >
                              <div class="flex items-center gap-1.5 min-w-0 flex-1">
                                <input
                                  v-if="bulkSelectMode && canEdit"
                                  type="checkbox"
                                  class="w-3 h-3 shrink-0 rounded border-gray-300 text-primary focus:ring-primary cursor-pointer"
                                  :checked="selectedDocIds.includes(doc.id)"
                                  :aria-label="`选择文档 ${doc.name || doc.id}`"
                                  @click.stop
                                  @change="toggleDocSelection(doc, dataset.ragflow_dataset_id || dataset.id)"
                                />
                                <svg class="w-3.5 h-3.5 shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
                                </svg>
                                <span
                                  class="truncate text-xs"
                                  :class="isDocDisabled(doc) ? 'text-gray-400 line-through' : ''"
                                >{{ doc.name || doc.id }}</span>
                                <span
                                  v-if="isDocDisabled(doc)"
                                  class="shrink-0 text-[9px] px-1 py-0.5 rounded bg-amber-100 text-amber-700 font-semibold"
                                  title="已停用：切片保留，但不参与检索召回"
                                >已停用</span>
                              </div>
                              
                              <!-- 状态指示灯与悬浮移动/删除操作 -->
                              <div class="flex items-center gap-1 shrink-0 justify-end relative" @click.stop>
                                <span
                                  class="w-1.5 h-1.5 rounded-full shrink-0 group-hover/doc:hidden"
                                  :class="{
                                    'bg-emerald-500 shadow-[0_0_4px_#10b981]': getDocStatus(doc) === 'parsed',
                                    'bg-amber-500 animate-pulse shadow-[0_0_4px_#f59e0b]': getDocStatus(doc) === 'parsing',
                                    'bg-red-500 shadow-[0_0_4px_#ef4444]': getDocStatus(doc) === 'failed',
                                    'bg-gray-300': !getDocStatus(doc) || getDocStatus(doc) === 'unparsed'
                                  }"
                                ></span>
                                
                                <div class="flex opacity-100 md:opacity-0 md:group-hover/doc:opacity-100 md:focus-within:opacity-100 items-center bg-inherit pl-2 gap-1 z-30">
                                  <!-- 移动到文件夹按钮 -->
                                  <div v-if="canEdit && canManageDataset(dataset)" class="relative">
                                    <button 
                                      class="p-0.5 rounded hover:bg-gray-100 text-gray-400 hover:text-primary transition-all bg-inherit cursor-pointer"
                                      title="移动到文件夹"
                                      @click="activeFileMoveMenuDocId = (activeFileMoveMenuDocId === doc.id ? null : doc.id)"
                                    >
                                      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4" />
                                      </svg>
                                    </button>
                                    
                                    <!-- 下拉定位菜单 -->
                                    <div 
                                      v-if="activeFileMoveMenuDocId === doc.id"
                                      class="absolute right-0 mt-1 w-36 bg-white border border-gray-200 rounded-lg shadow-lg z-50 py-1 divide-y divide-gray-50 text-xs"
                                    >
                                      <div class="px-2 py-1 text-[10px] text-gray-400 font-semibold select-none">移动至文件夹:</div>
                                      <button 
                                        v-for="targetFolder in Object.keys(dataset.local_metadata?.extra_config?.folder_structure || {}).filter(f => f !== fName)"
                                        :key="targetFolder"
                                        class="w-full text-left px-3 py-1.5 hover:bg-gray-50 text-gray-700 truncate cursor-pointer"
                                        @click="moveDocumentToFolder(dataset, doc.id, targetFolder)"
                                      >
                                        📁 {{ targetFolder }}
                                      </button>
                                      <button 
                                        class="w-full text-left px-3 py-1.5 hover:bg-gray-50 text-red-500 font-medium cursor-pointer"
                                        @click="moveDocumentToFolder(dataset, doc.id, null)"
                                      >
                                        移出该文件夹
                                      </button>
                                    </div>
                                  </div>

                                  <button
                                    v-if="canDeleteDocument && !isReadOnlyDataset(dataset)"
                                    class="p-0.5 rounded hover:bg-red-50 text-gray-400 hover:text-red-600 transition-all bg-inherit"
                                    title="删除文档"
                                    @click="deletingDocument = doc"
                                  >
                                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                    </svg>
                                  </button>
                                </div>
                              </div>
                            </div>
                            
                            <div 
                              v-if="!dataset.local_metadata?.extra_config?.folder_structure?.[fName]?.length"
                              class="py-1 pl-6 text-[10px] text-gray-400 italic select-none"
                            >
                              (空文件夹)
                            </div>
                          </div>
                        </transition>
                      </div>
                    </template>

                    <!-- 未分类文档列表渲染 (根目录下平铺) -->
                    <div 
                      v-for="doc in (documentsMap[dataset.ragflow_dataset_id || dataset.id] || []).filter(d => !dataset.local_metadata?.extra_config?.folder_structure || !Object.values(dataset.local_metadata.extra_config.folder_structure).some(ids => Array.isArray(ids) && ids.includes(d.id)))"
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
                        <input
                          v-if="bulkSelectMode && canEdit"
                          type="checkbox"
                          class="w-3 h-3 shrink-0 rounded border-gray-300 text-primary focus:ring-primary cursor-pointer"
                          :checked="selectedDocIds.includes(doc.id)"
                          :aria-label="`选择文档 ${doc.name || doc.id}`"
                          @click.stop
                          @change="toggleDocSelection(doc, dataset.ragflow_dataset_id || dataset.id)"
                        />
                        <svg class="w-3.5 h-3.5 shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
                        </svg>
                        <span
                          class="truncate text-xs"
                          :class="isDocDisabled(doc) ? 'text-gray-400 line-through' : ''"
                        >{{ doc.name || doc.id }}</span>
                        <span
                          v-if="isDocDisabled(doc)"
                          class="shrink-0 text-[9px] px-1 py-0.5 rounded bg-amber-100 text-amber-700 font-semibold"
                          title="已停用：切片保留，但不参与检索召回"
                        >已停用</span>
                      </div>

                      <div class="flex items-center gap-1 shrink-0 justify-end relative" @click.stop>
                        <span
                          class="w-1.5 h-1.5 rounded-full shrink-0 transition-all group-hover/doc:hidden"
                          :class="{
                            'bg-emerald-500 shadow-[0_0_4px_#10b981]': getDocStatus(doc) === 'parsed',
                            'bg-amber-500 animate-pulse shadow-[0_0_4px_#f59e0b]': getDocStatus(doc) === 'parsing',
                            'bg-red-500 shadow-[0_0_4px_#ef4444]': getDocStatus(doc) === 'failed',
                            'bg-gray-300': !getDocStatus(doc) || getDocStatus(doc) === 'unparsed'
                          }"
                        ></span>

                        <div class="flex opacity-100 md:opacity-0 md:group-hover/doc:opacity-100 md:focus-within:opacity-100 items-center bg-inherit pl-2 gap-1 z-30">
                          <!-- 移动至文件夹按钮 -->
                          <div 
                            v-if="canEdit && canManageDataset(dataset) && dataset.local_metadata?.extra_config?.folder_structure && Object.keys(dataset.local_metadata.extra_config.folder_structure).length"
                            class="relative"
                          >
                            <button 
                              class="p-0.5 rounded hover:bg-gray-100 text-gray-400 hover:text-primary transition-all bg-inherit cursor-pointer"
                              title="移动到文件夹"
                              @click="activeFileMoveMenuDocId = (activeFileMoveMenuDocId === doc.id ? null : doc.id)"
                            >
                              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4" />
                              </svg>
                            </button>
                            
                            <!-- 下拉定位菜单 -->
                            <div 
                              v-if="activeFileMoveMenuDocId === doc.id"
                              class="absolute right-0 mt-1 w-36 bg-white border border-gray-200 rounded-lg shadow-lg z-50 py-1 divide-y divide-gray-50 text-xs"
                            >
                              <div class="px-2 py-1 text-[10px] text-gray-400 font-semibold select-none">移动至文件夹:</div>
                              <button 
                                v-for="targetFolder in Object.keys(dataset.local_metadata.extra_config.folder_structure)"
                                :key="targetFolder"
                                class="w-full text-left px-3 py-1.5 hover:bg-gray-50 text-gray-700 truncate cursor-pointer"
                                @click="moveDocumentToFolder(dataset, doc.id, targetFolder)"
                              >
                                📁 {{ targetFolder }}
                              </button>
                            </div>
                          </div>

                          <button
                            v-if="canDeleteDocument && !isReadOnlyDataset(dataset)"
                            class="p-0.5 rounded hover:bg-red-50 text-gray-400 hover:text-red-600 transition-all bg-inherit"
                            title="删除文档"
                            @click="deletingDocument = doc"
                          >
                            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                            </svg>
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </transition>

                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- Right Side: Workbench / Details panel -->
      <section class="flex-1 bg-white rounded-2xl border border-gray-200 shadow-sm p-6 flex flex-col min-w-0">
        
        <!-- Case 1: Knowledge base metadata details selected -->
        <div v-if="selectedType === 'dataset' && selectedDataset" class="space-y-6 flex-1 flex flex-col">
          <!--
            头部信息：可见范围/更新时间这类"一眼确认状态"的字段收进标题与右上角，
            其余元数据压成一行小字。原先 7 张等宽卡片要占掉三行高度，
            在右侧详情区里把真正的内容（文档列表）挤到首屏之外。
          -->
          <div class="flex items-start justify-between gap-3 border-b border-gray-100 pb-4">
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2 flex-wrap">
                <h2 class="text-xl font-bold text-gray-900 truncate">{{ selectedDataset.platform_name || selectedDataset.name }}</h2>
                <span
                  class="shrink-0 px-2 py-0.5 rounded-full border text-[11px] font-semibold leading-normal cursor-default"
                  :class="visibilityBadgeClass(selectedDataset)"
                  :title="visibilityHint(selectedDataset)"
                >{{ visibilityLabel(selectedDataset) }}</span>
                <HoverTip
                  v-if="selectedDataset.is_missing_in_ragflow"
                  :text="MISSING_DATASET_HINT"
                  :width="320"
                >
                  <span class="shrink-0 px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 text-xs font-semibold cursor-help select-none">
                    RAGFlow 侧已失联
                  </span>
                </HoverTip>
              </div>

              <div v-if="selectedDataset.platform_description || selectedDataset.description" class="mt-2.5 pl-3.5 border-l-[3px] border-primary bg-gray-50/80 py-2 pr-4 rounded-r-xl max-w-3xl">
                <p class="text-sm text-gray-600 leading-relaxed italic">
                  "{{ selectedDataset.platform_description || selectedDataset.description }}"
                </p>
              </div>
              <p v-else class="text-xs text-gray-400 italic mt-2 select-none">暂无对该知识库的描述说明</p>

              <!-- 元信息条：创建人 · 切片 · 归属部门 · ID（可复制） -->
              <div class="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-gray-500">
                <span class="whitespace-nowrap" :title="`创建人：${selectedDataset.created_by || '未知'}`">
                  归属人
                  <span class="font-medium text-gray-700">{{ datasetOwnerUserLabel(selectedDataset) }}</span>
                  <button
                    v-if="canTransferOwner(selectedDataset)"
                    type="button"
                    class="ml-1 align-baseline text-[11px] font-semibold text-primary hover:underline"
                    title="把该知识库的管理权转交给其他同事（离职 / 调岗交接）"
                    @click="openTransferOwner(selectedDataset)"
                  >
                    转移
                  </button>
                </span>
                <span class="w-px h-3 bg-gray-200" aria-hidden="true"></span>
                <span class="whitespace-nowrap">
                  切片
                  <span class="font-medium text-gray-700">{{ selectedDataset.chunk_method || '未设置' }}</span>
                  <span class="text-gray-400">·</span>
                  <span class="font-medium text-gray-700">{{ selectedDataset.chunk_count ?? '—' }}</span> 段
                </span>
                <span class="w-px h-3 bg-gray-200" aria-hidden="true"></span>
                <!-- 一律显示：只在有引用时才出现的话，没人知道这里还有个「引用」维度 -->
                <span class="whitespace-nowrap">
                  <template v-if="(selectedDataset.agent_ref_count || 0) > 0">
                    被
                    <button
                      type="button"
                      class="font-semibold text-primary hover:underline"
                      title="查看引用该知识库的智能体，以及被授权访问的角色/用户"
                      @click="openDatasetReferences"
                    >
                      {{ selectedDataset.agent_ref_count }} 个智能体
                    </button>
                    引用
                  </template>
                  <span
                    v-else
                    class="text-gray-400"
                    title="智能体在「知识库绑定 / 默认 Dataset IDs」里选中本库后，这里会显示被引用数量"
                  >未被智能体引用</span>
                </span>
                <span class="w-px h-3 bg-gray-200" aria-hidden="true"></span>
                <span class="whitespace-nowrap max-w-[18rem] truncate" :title="datasetOwnerLabel(selectedDataset) || ''">
                  业务归属部门/负责人
                  <span class="font-medium text-gray-700">{{ datasetOwnerLabel(selectedDataset) || '未指派' }}</span>
                </span>
                <span class="w-px h-3 bg-gray-200" aria-hidden="true"></span>
                <!--
                  ID 完整展示而不是截成 "54af42d5…"：这个值就是 RAGFlow Dataset ID，
                  排查问题时要能直接对照 RAGFlow 后台、也要能整段复制出去。
                  用 break-all 允许在元信息条里折行，避免长 ID 撑破布局。
                -->
                <button
                  type="button"
                  class="inline-flex max-w-full items-start gap-1 text-left text-gray-400 hover:text-primary transition-colors"
                  :title="`点击复制知识库 ID：${selectedDataset.ragflow_dataset_id || selectedDataset.id || ''}`"
                  @click="copyToClipboard(selectedDataset.ragflow_dataset_id || selectedDataset.id || '')"
                >
                  <span class="shrink-0">ID</span>
                  <span class="font-mono break-all text-gray-500">{{ selectedDataset.ragflow_dataset_id || selectedDataset.id || '—' }}</span>
                  <svg class="mt-0.5 w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                  </svg>
                </button>
              </div>
            </div>

            <div class="flex items-center gap-2 shrink-0">
              <div class="text-right hidden sm:block mr-1">
                <span class="block text-[10px] text-gray-400 leading-none">更新时间</span>
                <span class="block text-[11px] font-medium text-gray-600 mt-1 whitespace-nowrap">{{ formatTime(selectedDataset.update_time || selectedDataset.updated_at) }}</span>
              </div>
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
                v-if="canEdit && canManageDataset(selectedDataset)"
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

          <!-- 视图切换：文档与设置 / 操作日志 -->
          <div class="flex items-center gap-1 -mt-2 border-b border-gray-100">
            <button
              v-for="tab in [{ key: 'documents', label: '文档与设置' }, { key: 'audit', label: '操作日志' }, { key: 'references', label: '引用与授权' }]"
              :key="tab.key"
              type="button"
              class="px-3 py-1.5 text-xs font-semibold rounded-t-lg transition-all -mb-px border-b-2"
              :class="detailTab === tab.key
                ? 'text-primary border-primary'
                : 'text-gray-400 border-transparent hover:text-gray-600'"
              @click="detailTab = (tab.key as 'documents' | 'audit' | 'references')"
            >
              {{ tab.label }}
            </button>
          </div>

          <template v-if="detailTab === 'documents'">

          <!-- 配额用量：接近上限时提前预警，别等传不进去才发现 -->
          <div
            v-if="datasetQuota?.documents?.limit"
            class="flex flex-wrap items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2"
          >
            <span class="text-[11px] font-medium text-gray-500 select-none">文档配额</span>
            <span class="text-xs font-semibold font-mono" :class="docQuotaTone.split(' ')[1]">
              {{ datasetQuota.documents.used ?? '-' }} / {{ datasetQuota.documents.limit }}
            </span>
            <div class="h-1.5 w-24 overflow-hidden rounded-full bg-gray-100">
              <div
                class="h-full rounded-full transition-all"
                :class="docQuotaTone.split(' ')[0]"
                :style="{ width: `${Math.round(docQuotaRatio * 100)}%` }"
              ></div>
            </div>
            <span v-if="docQuotaRatio >= 0.9" class="text-[11px] font-semibold text-red-600">
              快满了，建议先清理不再需要的文档
            </span>
            <span v-if="datasetQuota.owned_datasets" class="ml-auto text-[11px] text-gray-400 select-none">
              你已拥有 {{ datasetQuota.owned_datasets.used }} / {{ datasetQuota.owned_datasets.limit }} 个知识库
            </span>
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
                <p class="text-xs text-gray-400 mt-1.5">
                  支持多文件上传，类型包含 PDF, DOCX, TXT, MD, XLSX 等，单文件不超过 {{ KNOWLEDGE_UPLOAD_MAX_MB }}MB
                </p>
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
                    class="flex items-center gap-3 p-2.5 bg-white rounded-xl border border-gray-200 shadow-sm relative group/card"
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

          <!--
            Extended config JSON：与编辑弹窗的「高级设置」同一口径——默认收起。
            这坨 JSON 只有 folder_structure / custom_questions 两个键有消费者，
            平时挂在详情页最底部纯属视觉噪音，还把左右分栏的高度全吃掉了。
          -->
          <div class="flex flex-col shrink-0">
            <div class="flex items-center justify-between gap-2">
              <button
                type="button"
                class="flex items-center gap-1.5 text-left text-xs font-semibold uppercase tracking-wider text-gray-400 transition-colors hover:text-gray-600"
                :aria-expanded="detailConfigOpen"
                @click="detailConfigOpen = !detailConfigOpen"
              >
                <svg
                  class="h-3 w-3 shrink-0 transition-transform duration-200"
                  :class="{ 'rotate-90': detailConfigOpen }"
                  fill="none" stroke="currentColor" viewBox="0 0 24 24"
                >
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
                </svg>
                业务元数据扩展配置 (JSON)
              </button>
              <div class="flex shrink-0 items-center gap-2">
                <button
                  v-if="detailConfigOpen"
                  class="text-xs font-semibold text-primary hover:underline"
                  @click="copyToClipboard(JSON.stringify(selectedDataset.local_metadata?.extra_config || {}, null, 2))"
                >
                  复制配置
                </button>
                <span class="text-[10px] text-gray-300 select-none">{{ detailConfigOpen ? '收起' : '展开' }}</span>
              </div>
            </div>
            <pre
              v-show="detailConfigOpen"
              class="mt-2 max-h-[280px] overflow-auto rounded-xl bg-gray-900 p-4 font-mono text-xs leading-relaxed text-emerald-400 shadow-inner"
            >{{ JSON.stringify(selectedDataset.local_metadata?.extra_config || {}, null, 2) }}</pre>
          </div>
          </template>

          <!-- 操作日志：谁在什么时候对这个库做了什么 -->
          <div v-if="detailTab === 'audit'" class="flex-1 flex flex-col min-h-0">
            <div class="flex items-center justify-between gap-2 border-b border-gray-100 pb-2 shrink-0">
              <div class="flex items-center gap-2">
                <span class="text-xs text-gray-400 select-none">{{ auditTotal }} 条记录</span>
                <select
                  v-model="auditAction"
                  class="text-[11px] rounded-lg border border-gray-200 px-2 py-1 bg-white text-gray-600"
                  @change="loadAuditLogs(true)"
                >
                  <option value="">全部操作</option>
                  <option v-for="opt in auditActionOptions" :key="opt.value" :value="opt.value">
                    {{ opt.label }}
                  </option>
                </select>
              </div>
              <button
                type="button"
                class="text-[11px] text-gray-400 hover:text-primary transition-colors disabled:opacity-50"
                :disabled="auditLoading"
                @click="loadAuditLogs(true)"
              >
                刷新
              </button>
            </div>

            <div v-if="auditLoading && !auditLogs.length" class="text-xs text-gray-400 text-center py-10 select-none">
              正在读取操作日志...
            </div>
            <div v-else-if="!auditLogs.length" class="text-xs text-gray-400 text-center py-10 select-none">
              暂无操作记录
            </div>
            <template v-else>
              <ul class="divide-y divide-gray-100 overflow-y-auto flex-1 min-h-0">
                <li v-for="log in auditLogs" :key="log.id" class="py-2.5 flex items-start gap-2.5">
                  <span
                    class="mt-1.5 w-1.5 h-1.5 rounded-full shrink-0"
                    :class="log.status_code >= 400 ? 'bg-red-400' : 'bg-emerald-400'"
                  ></span>
                  <div class="min-w-0 flex-1">
                    <p class="text-xs text-gray-700 leading-relaxed">
                      {{ auditLogText(log) }}
                      <span v-if="log.status_code >= 400" class="ml-1 text-red-500 font-semibold">· 失败</span>
                    </p>
                    <p class="text-[10px] text-gray-400 mt-0.5">
                      {{ formatTime(log.created_at) }}
                      <span v-if="log.client_ip"> · {{ log.client_ip }}</span>
                    </p>
                  </div>
                </li>
              </ul>
              <button
                v-if="auditLogs.length < auditTotal"
                type="button"
                class="mt-2 shrink-0 w-full py-1.5 text-[11px] text-gray-500 border border-gray-200 rounded-lg hover:bg-gray-50 transition-all disabled:opacity-50"
                :disabled="auditLoading"
                @click="loadAuditLogs(false)"
              >
                {{ auditLoading ? '加载中...' : '加载更多' }}
              </button>
            </template>
          </div>

          <!-- 引用与授权：智能体绑定了本库 / 谁被授权访问本库 -->
          <div v-if="detailTab === 'references'" class="flex-1 space-y-4 pt-2 min-h-0 overflow-y-auto">
            <section class="rounded-xl border border-gray-200 bg-white p-4">
              <div class="flex items-center justify-between gap-2">
                <h3 class="text-sm font-semibold text-gray-800">
                  智能体引用
                  <span class="ml-1 text-xs font-normal text-gray-400">{{ agentRefs.length }} 个</span>
                </h3>
                <button
                  type="button"
                  class="text-[11px] text-gray-400 hover:text-primary transition-colors disabled:opacity-50"
                  :disabled="referencesLoading"
                  @click="loadDatasetReferences"
                >刷新</button>
              </div>
              <p class="mt-1 text-[11px] text-gray-400 leading-relaxed">
                这些智能体在「知识库绑定 / 默认 Dataset IDs」里选中了本库。删除本库或收窄可见范围后，它们将检索不到本库内容。
              </p>
              <div v-if="referencesLoading" class="py-6 text-center text-xs text-gray-400 select-none">正在读取…</div>
              <div v-else-if="referencesError" class="py-4 text-xs text-red-500">{{ referencesError }}</div>
              <div v-else-if="!agentRefs.length" class="py-6 text-center text-xs text-gray-400 italic select-none">
                暂无智能体引用本库
              </div>
              <ul v-else class="mt-3 divide-y divide-gray-100">
                <li v-for="agent in agentRefs" :key="agent.id" class="flex items-center justify-between gap-3 py-2">
                  <div class="min-w-0">
                    <p class="text-sm text-gray-800 truncate">{{ agent.display_name }}</p>
                    <p class="text-[11px] text-gray-400 font-mono truncate">{{ agent.name }}</p>
                  </div>
                  <span
                    class="shrink-0 text-[11px] px-2 py-0.5 rounded-full border"
                    :class="agent.is_enabled
                      ? 'bg-emerald-50 text-emerald-600 border-emerald-100'
                      : 'bg-gray-50 text-gray-400 border-gray-200'"
                  >
                    {{ agent.is_enabled ? '已启用' : '已停用' }}
                  </span>
                </li>
              </ul>
            </section>

            <section class="rounded-xl border border-gray-200 bg-white p-4">
              <div class="flex items-center justify-between gap-2">
                <h3 class="text-sm font-semibold text-gray-800">
                  授权访问
                  <span class="ml-1 text-xs font-normal text-gray-400">{{ datasetPermissions.roles.length + datasetPermissions.users.length }} 个</span>
                </h3>
                <div class="flex items-center gap-2">
                  <span v-if="loadingPermissions" class="text-[11px] text-gray-400 animate-pulse select-none">读取中…</span>
                  <button
                    v-if="canManagePermissions && !loadingPermissions && !isPrivateDataset"
                    type="button"
                    class="px-2 py-1 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-[11px] font-bold shadow-sm transition-all flex items-center gap-1 cursor-pointer"
                    @click="openAddPermissionModal"
                  >
                    <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z" />
                    </svg>
                    分配授权成员
                  </button>
                </div>
              </div>
              <p class="mt-1 text-[11px] text-gray-400 leading-relaxed">
                被授权的人能打开本库、检索里面的内容。这只是访问权限 —— 给角色分配知识库不等于智能体会用它。删除本库会一并清除这些授权。
              </p>

              <!-- 可见范围说明：三档语义真实生效，这里必须与后端判定一致 -->
              <p class="mt-2 rounded-lg bg-gray-50 px-2.5 py-2 text-[11px] leading-relaxed text-gray-500 select-none">
                可见范围为
                <span class="font-bold text-gray-800 uppercase px-1.5 py-0.5 rounded bg-white border border-gray-200 text-[10px]">{{ visibilityLabel(selectedDataset) }}</span>。
                <span v-if="visibilityOf(selectedDataset) === 'public'">授权可见：创建人自动可用，其他人<b>必须</b>在下方被显式授权才能访问（不会被自动放行）。</span>
                <span v-else-if="visibilityOf(selectedDataset) === 'team'">TEAM 级：{{ datasetOwnerLabel(selectedDataset) || '未设置归属部门' }} 的成员（<b>含下级部门</b>）自动获得只读权限<template v-if="selectedDatasetWritable">；也可在下方额外授权给其他用户/角色</template>。</span>
                <span v-else>
                  个人私有：仅你自己可见，<b>不支持</b>分配授权。如需共享，请先把可见范围改为授权可见或 TEAM 级。
                  <template v-if="datasetPermissions.users.length || datasetPermissions.roles.length">
                    下面的历史授权在当前范围内<b>不生效</b>，可逐条清除。
                  </template>
                </span>
              </p>

              <div v-if="permissionsRestricted" class="mt-3 py-3 text-xs text-gray-400 italic select-none">
                仅创建人与管理员可查看授权名单。
              </div>
              <div v-else-if="permissionsError" class="mt-3 flex items-center justify-between gap-3 text-xs">
                <span class="text-amber-600">授权信息读取失败，当前无法确认谁有访问权限</span>
                <button
                  type="button"
                  class="font-semibold text-primary hover:underline shrink-0"
                  @click="fetchDatasetPermissions(selectedDatasetId)"
                >重试</button>
              </div>
              <div v-else-if="!loadingPermissions && !datasetPermissions.roles.length && !datasetPermissions.users.length" class="mt-3 py-3 text-xs text-gray-400 italic select-none">
                {{ isPrivateDataset ? '个人私有知识库不支持分配授权。' : '尚未授权给任何角色或用户' }}
              </div>
              <div v-else class="mt-3 space-y-2.5">
                <div v-if="datasetPermissions.roles.length" class="flex items-start gap-2">
                  <span class="text-gray-400 font-medium shrink-0 mt-0.5 text-[11px] select-none">授权角色:</span>
                  <div class="flex flex-wrap gap-1.5">
                    <span
                      v-for="role in datasetPermissions.roles"
                      :key="`ref-role-${role.id}`"
                      class="px-2 py-0.5 rounded bg-indigo-50 text-indigo-700 text-[11px] font-semibold border border-indigo-100 flex items-center gap-1"
                    >
                      角色 · {{ role.name }}
                      <button
                        v-if="canManagePermissions"
                        type="button"
                        class="text-indigo-400 hover:text-red-500 font-bold focus:outline-none cursor-pointer"
                        title="取消授权"
                        @click.stop="removePermission('role', role.id, role.name)"
                      >×</button>
                    </span>
                  </div>
                </div>
                <div v-if="datasetPermissions.users.length" class="flex items-start gap-2">
                  <span class="text-gray-400 font-medium shrink-0 mt-0.5 text-[11px] select-none">授权用户:</span>
                  <div class="flex flex-wrap gap-1.5">
                    <span
                      v-for="user in datasetPermissions.users"
                      :key="`ref-user-${user.id}`"
                      class="px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 text-[11px] font-semibold border border-emerald-100 flex items-center gap-1"
                    >
                      {{ user.real_name || user.user_name }}
                      <span v-if="user.id === datasetPermissions.ownerUserId" class="text-[10px] text-primary">归属人</span>
                      <button
                        v-if="canManagePermissions && user.id !== datasetPermissions.ownerUserId"
                        type="button"
                        class="text-emerald-400 hover:text-red-500 font-bold focus:outline-none cursor-pointer"
                        title="取消授权"
                        @click.stop="removePermission('user', user.id, user.real_name || user.user_name)"
                      >×</button>
                    </span>
                  </div>
                </div>
              </div>
            </section>
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
                v-if="canEdit && !isReadOnlyDataset(selectedDataset)"
                class="px-3 py-2 border border-gray-200 text-gray-600 hover:bg-gray-50 text-sm font-semibold rounded-xl transition-all whitespace-nowrap shrink-0"
                :disabled="!isEngineReady"
                @click="startRenameDocument(selectedDocument)"
              >重命名</button>
              <label
                v-if="canEdit && !isReadOnlyDataset(selectedDataset)"
                class="px-3 py-2 border rounded-xl text-sm font-semibold flex items-center gap-2 select-none cursor-pointer whitespace-nowrap shrink-0 transition-all"
                :class="isDocDisabled(selectedDocument)
                  ? 'border-amber-200 bg-amber-50 text-amber-700'
                  : 'border-emerald-200 bg-emerald-50 text-emerald-700'"
                title="停用后切片仍保留，但不再参与检索召回；随时可以重新启用"
              >
                <input
                  type="checkbox"
                  class="w-3.5 h-3.5 rounded border-gray-300 text-primary focus:ring-primary cursor-pointer"
                  :checked="!isDocDisabled(selectedDocument)"
                  :disabled="docToggleBusy || !isEngineReady"
                  @change="toggleDocumentEnabled(selectedDocument)"
                />
                {{ isDocDisabled(selectedDocument) ? '已停用（不参与检索）' : '参与检索' }}
              </label>
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
            <div class="bg-gray-50/50 p-4 rounded-xl border border-gray-200">
              <span class="text-xs text-gray-400 font-semibold uppercase tracking-wider block">物理特征 / 文件大小</span>
              <span class="text-lg font-bold text-gray-800 mt-2 block">{{ formatSize(selectedDocument.size) }}</span>
            </div>
            <div class="bg-gray-50/50 p-4 rounded-xl border border-gray-200 flex flex-col justify-between">
              <div>
                <span class="text-xs text-gray-400 font-semibold uppercase tracking-wider block">已分切片段数 (Chunks)</span>
                <span class="text-lg font-bold text-gray-800 mt-2 block">{{ selectedDocument.chunk_count ?? 0 }} 个分块</span>
              </div>
              <div class="flex items-center gap-4 mt-2">
                <button
                  v-if="getDocStatus(selectedDocument) === 'parsed' && canViewChunks(selectedDataset)"
                  class="text-xs text-primary hover:underline flex items-center gap-1 font-semibold self-start"
                  @click="openChunksModal(selectedDocument)"
                >
                  <span>🔍 查看分块详情</span>
                </button>
                <!-- 对照原文核对分块，此前只能去对话页看引文 -->
                <button
                  v-if="canViewChunks(selectedDataset)"
                  class="text-xs text-primary hover:underline flex items-center gap-1 font-semibold self-start"
                  @click="openDocPreview"
                >
                  <span>📄 预览原文件</span>
                </button>
                <span
                  v-if="getDocStatus(selectedDocument) === 'parsed' && !canViewChunks(selectedDataset)"
                  class="text-xs text-gray-400"
                >
                  无权限查看分块明细（仅创建人或管理员可查看）
                </span>
              </div>
            </div>
            <div class="bg-gray-50/50 p-4 rounded-xl border border-gray-200">
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
              'bg-gray-50/30 border-gray-200': getDocStatus(selectedDocument) === 'unparsed'
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
              <div v-if="getDocStatus(selectedDocument) === 'failed' && canParse && !isReadOnlyDataset(selectedDataset)" class="shrink-0 self-center">
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
          <div class="space-y-3 bg-gray-50/30 border border-gray-200 rounded-2xl p-5 flex-1 flex flex-col justify-between">
            <div class="space-y-2">
              <h3 class="text-sm font-semibold text-gray-800">文档解析生命周期说明</h3>
              <p class="text-xs text-gray-500 leading-relaxed">
                平台上传的文档将被推送到 RAGFlow 的数据切片服务中。默认解析切分会根据知识库中配置的 `chunk_method` 算法进行文档提取。
                解析过程包括：格式化清洗、文本片段拆分、密集检索向量 Embedding 构建等。解析完成后，知识库即可直接提供语义检索服务。
              </p>
            </div>
            
            <div class="border-t border-gray-200 pt-4 flex items-center justify-between text-xs text-gray-400">
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
            <h3 class="text-base font-bold text-gray-900">请选择节点</h3>
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
        <div>
          <label class="block text-xs font-semibold text-gray-400 mb-1.5">可见范围</label>
          <div data-visibility-select class="relative">
            <button
              type="button"
              class="w-full flex items-center justify-between gap-2 border border-gray-200 rounded-xl px-3 py-2 text-left bg-white hover:border-gray-300 focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary"
              @click="visibilityMenuOpen = !visibilityMenuOpen"
            >
              <span class="flex items-center gap-2 min-w-0 flex-1">
                <svg
                  class="w-5 h-5 shrink-0"
                  :class="visibilityOptionOf(form.visibility).iconClass"
                  fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"
                  aria-hidden="true"
                >
                  <path :d="visibilityOptionOf(form.visibility).iconPath" />
                </svg>
                <span class="min-w-0 flex-1">
                  <span class="flex items-center gap-1.5">
                    <span class="text-sm font-semibold text-gray-800">{{ visibilityOptionOf(form.visibility).label }}</span>
                    <span v-if="visibilityOptionOf(form.visibility).isDefault" class="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 font-bold shrink-0">默认</span>
                  </span>
                  <span class="block text-[11px] text-gray-400 truncate">{{ visibilityOptionOf(form.visibility).hint }}</span>
                </span>
              </span>
              <svg class="w-4 h-4 shrink-0 text-gray-400 transition-transform" :class="{ 'rotate-180': visibilityMenuOpen }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
              </svg>
            </button>
            <div v-if="visibilityMenuOpen" class="absolute z-30 mt-1 w-full rounded-xl border border-gray-200 bg-white shadow-lg p-1 space-y-0.5">
              <button
                v-for="option in VISIBILITY_SCOPE_OPTIONS"
                :key="option.key"
                type="button"
                class="w-full text-left rounded-lg px-2.5 py-2 transition-colors"
                :class="form.visibility === option.key ? 'bg-primary/5' : 'hover:bg-gray-50'"
                @click="form.visibility = option.key; visibilityMenuOpen = false"
              >
                <span class="flex items-start gap-2">
                  <svg
                    class="w-5 h-5 shrink-0 mt-px"
                    :class="option.iconClass"
                    fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"
                    aria-hidden="true"
                  >
                    <path :d="option.iconPath" />
                  </svg>
                  <span class="min-w-0 flex-1">
                    <span class="flex items-center gap-1.5">
                      <span class="text-sm font-semibold text-gray-800">{{ option.label }}</span>
                      <span v-if="option.isDefault" class="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 font-bold shrink-0">默认</span>
                      <svg v-if="form.visibility === option.key" class="w-3.5 h-3.5 text-primary ml-auto shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7" />
                      </svg>
                    </span>
                    <span class="block text-[11px] text-gray-500 mt-0.5 leading-snug">{{ option.hint }}</span>
                    <span class="block text-[11px] text-gray-400 leading-snug">适合：{{ option.scene }}</span>
                  </span>
                </span>
              </button>
            </div>
          </div>
          <p v-if="form.visibility === 'private'" class="mt-1.5 flex items-start gap-1.5 text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-2 py-1.5 leading-snug">
            <svg class="w-3.5 h-3.5 shrink-0 mt-px" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
            </svg>
            <span>当前为<b>个人私有</b>：建好后只有你能看到，其他人无法访问、也不能被授权。要共享请先改成 TEAM 级或授权可见。</span>
          </p>
          <p v-else-if="form.visibility === 'team'" class="mt-1.5 text-[11px] text-gray-500 leading-snug">归属部门的成员（含下级部门）会自动获得只读权限；未选归属部门时会退化为授权可见。</p>
          <p v-else class="mt-1.5 text-[11px] text-gray-500 leading-snug">创建人之外的人需要你在详情页「引用与授权」里点「分配授权成员」后才能访问。</p>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="block text-xs font-semibold mb-1.5" :class="form.visibility === 'team' ? 'text-gray-400' : 'text-gray-300'">业务归属部门/负责人</label>
            <div data-dept-select class="relative">
              <button
                type="button"
                :disabled="form.visibility !== 'team'"
                class="w-full flex items-center justify-between gap-2 border border-gray-200 rounded-xl px-3 py-2 text-left bg-white hover:border-gray-300 focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary disabled:bg-gray-50 disabled:cursor-not-allowed disabled:hover:border-gray-200"
                @click="toggleDeptMenu"
              >
                <span class="min-w-0 flex-1">
                  <span class="block text-sm font-semibold truncate" :class="form.visibility === 'team' ? 'text-gray-800' : 'text-gray-400'">{{ selectedDeptLabel }}</span>
                  <span class="block text-[11px] truncate" :class="deptHintClass">{{ deptHintText }}</span>
                </span>
                <svg class="w-4 h-4 shrink-0 text-gray-400 transition-transform" :class="{ 'rotate-180': deptMenuOpen }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                </svg>
              </button>
              <div v-if="deptMenuOpen && form.visibility === 'team'" data-dept-menu class="absolute z-30 mt-1 w-full max-h-64 overflow-y-auto rounded-xl border border-gray-200 bg-white shadow-lg p-1 space-y-0.5">
                <div class="sticky top-0 z-10 -mx-1 -mt-1 rounded-t-xl bg-white px-1 pb-1.5 pt-1">
                  <input
                    v-model="deptSearchKeyword"
                    type="search"
                    placeholder="搜索部门"
                    class="w-full rounded-lg border border-gray-200 px-2.5 py-1.5 text-sm placeholder:text-gray-400 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                    @input="onDeptSearchInput"
                    @keydown.esc.stop="deptSearchKeyword = ''"
                  />
                </div>
                <div v-if="!deptMenuOptions.length" class="px-3 py-3 text-center text-xs text-gray-400">
                  没有匹配「{{ deptSearchKeyword.trim() }}」的部门
                </div>
                <button
                  v-for="option in deptMenuOptions"
                  :key="option.id"
                  type="button"
                  class="w-full text-left rounded-lg px-2.5 py-1.5 text-sm transition-colors"
                  :class="form.ownerDeptId === option.id ? 'bg-primary/5 text-primary font-semibold' : 'text-gray-700 hover:bg-gray-50'"
                  @click="form.ownerDeptId = option.id; deptMenuOpen = false"
                >
                  <span>{{ deptSearching ? stripDeptIndent(option.label) : option.label }}</span>
                  <span v-if="deptSearching && option.path.length > 1" class="ml-1.5 text-[11px] text-gray-400">
                    {{ option.path.slice(0, -1).join(' / ') }}
                  </span>
                </button>
              </div>
            </div>
            <p v-if="departmentOptionsError" class="mt-1 text-[11px] text-amber-600">{{ departmentOptionsError }}</p>
          </div>
          <div>
            <label class="block text-xs font-semibold text-gray-400 mb-1.5">解析算法 *</label>
            <div data-chunk-select class="relative">
              <button
                type="button"
                class="w-full flex items-center justify-between gap-2 border border-gray-200 rounded-xl px-3 py-2 text-left bg-white hover:border-gray-300 focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary"
                @click="chunkMenuOpen = !chunkMenuOpen"
              >
                <span class="min-w-0 flex-1">
                  <span class="flex items-center gap-1.5">
                    <span class="text-sm font-semibold text-gray-800">{{ chunkMethodOptionOf(form.chunk_method).label }}</span>
                    <span class="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 font-mono font-bold leading-none shrink-0">{{ chunkMethodOptionOf(form.chunk_method).code }}</span>
                  </span>
                  <span class="block text-[11px] text-gray-400 truncate">{{ chunkMethodOptionOf(form.chunk_method).desc }}</span>
                </span>
                <svg class="w-4 h-4 shrink-0 text-gray-400 transition-transform" :class="{ 'rotate-180': chunkMenuOpen }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                </svg>
              </button>
              <div v-if="chunkMenuOpen" class="absolute z-30 mt-1 w-full max-h-72 overflow-y-auto rounded-xl border border-gray-200 bg-white shadow-lg p-1 space-y-0.5">
                <button
                  v-for="option in CHUNK_METHOD_OPTIONS"
                  :key="option.value"
                  type="button"
                  class="w-full text-left rounded-lg px-2.5 py-2 transition-colors"
                  :class="form.chunk_method === option.value ? 'bg-primary/5' : 'hover:bg-gray-50'"
                  @click="form.chunk_method = option.value; chunkMenuOpen = false"
                >
                  <span class="flex items-center gap-1.5">
                    <span class="text-sm font-semibold text-gray-800">{{ option.label }}</span>
                    <span class="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 font-mono font-bold leading-none shrink-0">{{ option.code }}</span>
                    <svg v-if="form.chunk_method === option.value" class="w-3.5 h-3.5 text-primary ml-auto shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7" />
                    </svg>
                  </span>
                  <span class="block text-[11px] text-gray-500 mt-0.5 leading-snug">{{ option.desc }}</span>
                </button>
              </div>
            </div>
          </div>
        </div>
        <div>
          <label class="block text-xs font-semibold text-gray-400 mb-1.5">标签<span class="ml-1 font-normal text-gray-300">（回车或逗号分隔，可多个）</span></label>
          <TagInput v-model="form.tags" placeholder="输入后按回车添加，例如：财务" />
        </div>
        <div>
          <label class="block text-xs font-semibold text-gray-400 mb-1.5">平台备注</label>
          <textarea v-model="form.notes" class="w-full border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary" rows="2" placeholder="请输入备注"></textarea>
        </div>
        <div class="overflow-hidden rounded-xl border border-gray-200">
          <button
            type="button"
            class="flex w-full items-center justify-between bg-gray-50/60 px-3 py-2.5 text-left transition-colors hover:bg-gray-50"
            :aria-expanded="createAdvancedConfigOpen"
            @click="createAdvancedConfigOpen = !createAdvancedConfigOpen"
          >
            <span class="flex items-center gap-1.5">
              <svg
                class="h-3.5 w-3.5 shrink-0 text-gray-400 transition-transform duration-200"
                :class="{ 'rotate-90': createAdvancedConfigOpen }"
                fill="none" stroke="currentColor" viewBox="0 0 24 24"
              >
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
              </svg>
              <span class="text-xs font-semibold text-gray-500">高级设置</span>
              <span class="text-[11px] text-gray-400">· 业务扩展配置 JSON</span>
            </span>
            <span class="shrink-0 text-[10px] text-gray-400">{{ createAdvancedConfigOpen ? '收起' : '展开' }}</span>
          </button>

          <div v-show="createAdvancedConfigOpen" class="space-y-2 border-t border-gray-100 px-3 pb-3 pt-2.5">
            <div class="space-y-1.5 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2 text-[11px] leading-relaxed text-amber-800">
              <p><b>保存是整份覆盖。</b>删掉 <code class="font-mono">folder_structure</code> 会让所有文档掉回「未分类」，虚拟文件夹丢失且无法撤销。</p>
              <p>只有下面两个键会被读取，其余键写了也不会生效：</p>
              <ul class="list-disc space-y-0.5 pl-4">
                <li><code class="font-mono">folder_structure</code>：虚拟文件夹映射，由「新建文件夹 / 移动文档」自动维护，一般无需手改。</li>
                <li><code class="font-mono">custom_questions</code>：知识库门户的快捷提问，形如 <code class="font-mono">[{label:'总结核心内容', query:'请帮我梳理要点'}]</code>。</li>
              </ul>
            </div>
            <textarea
              v-model="form.extraConfigText"
              class="w-full rounded-xl border border-gray-200 bg-white px-3 py-2 font-mono text-xs focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary disabled:bg-gray-100"
              rows="4"
              placeholder="{}"
            ></textarea>
          </div>
        </div>
        <div class="flex justify-end gap-3 pt-2">
          <button class="px-4 py-2 rounded-xl border text-sm font-semibold hover:bg-gray-50 transition-all" @click="showCreateModal = false">取消</button>
          <button class="px-4 py-2 rounded-xl bg-primary text-white text-sm font-semibold disabled:opacity-50" :disabled="!isEngineReady || creatingDataset" @click="createDataset">{{ creatingDataset ? '创建中…' : '确定创建' }}</button>
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
            <label class="block text-xs font-semibold text-gray-400 mb-1.5">可见范围</label>
            <div data-visibility-select class="relative">
              <button
                type="button"
                class="w-full flex items-center justify-between gap-2 border border-gray-200 rounded-xl px-3 py-2 text-left bg-white hover:border-gray-300 focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary"
                @click="visibilityMenuOpen = !visibilityMenuOpen"
              >
                <span class="flex items-center gap-2 min-w-0 flex-1">
                  <svg
                    class="w-5 h-5 shrink-0"
                    :class="visibilityOptionOf(form.visibility).iconClass"
                    fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"
                    aria-hidden="true"
                  >
                    <path :d="visibilityOptionOf(form.visibility).iconPath" />
                  </svg>
                  <span class="min-w-0 flex-1">
                    <span class="flex items-center gap-1.5">
                      <span class="text-sm font-semibold text-gray-800">{{ visibilityOptionOf(form.visibility).label }}</span>
                      <span v-if="visibilityOptionOf(form.visibility).isDefault" class="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 font-bold shrink-0">默认</span>
                    </span>
                    <span class="block text-[11px] text-gray-400 truncate">{{ visibilityOptionOf(form.visibility).hint }}</span>
                  </span>
                </span>
                <svg class="w-4 h-4 shrink-0 text-gray-400 transition-transform" :class="{ 'rotate-180': visibilityMenuOpen }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                </svg>
              </button>
              <div v-if="visibilityMenuOpen" class="absolute z-30 mt-1 w-full rounded-xl border border-gray-200 bg-white shadow-lg p-1 space-y-0.5">
                <button
                  v-for="option in VISIBILITY_SCOPE_OPTIONS"
                  :key="option.key"
                  type="button"
                  class="w-full text-left rounded-lg px-2.5 py-2 transition-colors"
                  :class="form.visibility === option.key ? 'bg-primary/5' : 'hover:bg-gray-50'"
                  @click="form.visibility = option.key; visibilityMenuOpen = false"
                >
                  <span class="flex items-start gap-2">
                    <svg
                      class="w-5 h-5 shrink-0 mt-px"
                      :class="option.iconClass"
                      fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"
                      aria-hidden="true"
                    >
                      <path :d="option.iconPath" />
                    </svg>
                    <span class="min-w-0 flex-1">
                      <span class="flex items-center gap-1.5">
                        <span class="text-sm font-semibold text-gray-800">{{ option.label }}</span>
                        <span v-if="option.isDefault" class="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700 font-bold shrink-0">默认</span>
                        <svg v-if="form.visibility === option.key" class="w-3.5 h-3.5 text-primary ml-auto shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7" />
                        </svg>
                      </span>
                      <span class="block text-[11px] text-gray-500 mt-0.5 leading-snug">{{ option.hint }}</span>
                      <span class="block text-[11px] text-gray-400 leading-snug">适合：{{ option.scene }}</span>
                    </span>
                  </span>
                </button>
              </div>
            </div>
            <p v-if="form.visibility === 'private'" class="mt-1.5 text-[11px] text-gray-500 leading-snug">仅你自己可用；个人私有库不支持分配授权。</p>
            <p v-else-if="form.visibility === 'team'" class="mt-1.5 text-[11px] text-gray-500 leading-snug">归属部门的成员会自动获得只读权限，也可额外分配授权；未选归属部门时退化为授权可见，更换归属部门会立即改变可访问人群。</p>
            <p v-else class="mt-1.5 text-[11px] text-gray-500 leading-snug">创建人之外的人必须在这里「分配授权成员」后才能访问。</p>
            <!-- 改成个人私有会让已有授权失效，必须显式提示，避免静默撤权 -->
            <p
              v-if="form.visibility === 'private' && hasExistingPermissions"
              class="mt-1 text-[11px] text-rose-600 bg-rose-50 border border-rose-100 rounded-lg px-2 py-1.5"
            >
              <template v-if="isPrivateDataset">
                当前范围内这 {{ datasetPermissions.users.length + datasetPermissions.roles.length }} 个历史授权不生效，可逐条清除。
              </template>
              <template v-else>
                保存后，现有的 {{ datasetPermissions.users.length + datasetPermissions.roles.length }} 个授权对象将失去访问权。
              </template>
            </p>
          </div>
          <div>
            <label class="block text-xs font-semibold mb-1.5" :class="form.visibility === 'team' ? 'text-gray-400' : 'text-gray-300'">业务归属部门/负责人</label>
            <div data-dept-select class="relative">
              <button
                type="button"
                :disabled="form.visibility !== 'team'"
                class="w-full flex items-center justify-between gap-2 border border-gray-200 rounded-xl px-3 py-2 text-left bg-white hover:border-gray-300 focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary disabled:bg-gray-50 disabled:cursor-not-allowed disabled:hover:border-gray-200"
                @click="toggleDeptMenu"
              >
                <span class="min-w-0 flex-1">
                  <span class="block text-sm font-semibold truncate" :class="form.visibility === 'team' ? 'text-gray-800' : 'text-gray-400'">{{ selectedDeptLabel }}</span>
                  <span class="block text-[11px] truncate" :class="deptHintClass">{{ deptHintText }}</span>
                </span>
                <svg class="w-4 h-4 shrink-0 text-gray-400 transition-transform" :class="{ 'rotate-180': deptMenuOpen }" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                </svg>
              </button>
              <div v-if="deptMenuOpen && form.visibility === 'team'" data-dept-menu class="absolute z-30 mt-1 w-full max-h-64 overflow-y-auto rounded-xl border border-gray-200 bg-white shadow-lg p-1 space-y-0.5">
                <div class="sticky top-0 z-10 -mx-1 -mt-1 rounded-t-xl bg-white px-1 pb-1.5 pt-1">
                  <input
                    v-model="deptSearchKeyword"
                    type="search"
                    placeholder="搜索部门"
                    class="w-full rounded-lg border border-gray-200 px-2.5 py-1.5 text-sm placeholder:text-gray-400 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                    @input="onDeptSearchInput"
                    @keydown.esc.stop="deptSearchKeyword = ''"
                  />
                </div>
                <div v-if="!deptMenuOptions.length" class="px-3 py-3 text-center text-xs text-gray-400">
                  没有匹配「{{ deptSearchKeyword.trim() }}」的部门
                </div>
                <button
                  v-for="option in deptMenuOptions"
                  :key="option.id"
                  type="button"
                  class="w-full text-left rounded-lg px-2.5 py-1.5 text-sm transition-colors"
                  :class="form.ownerDeptId === option.id ? 'bg-primary/5 text-primary font-semibold' : 'text-gray-700 hover:bg-gray-50'"
                  @click="form.ownerDeptId = option.id; deptMenuOpen = false"
                >
                  <span>{{ deptSearching ? stripDeptIndent(option.label) : option.label }}</span>
                  <span v-if="deptSearching && option.path.length > 1" class="ml-1.5 text-[11px] text-gray-400">
                    {{ option.path.slice(0, -1).join(' / ') }}
                  </span>
                </button>
              </div>
            </div>
            <p v-if="departmentOptionsError" class="mt-1 text-[11px] text-amber-600">{{ departmentOptionsError }}</p>
            <p v-else-if="form.visibility === 'team' && !form.ownerDeptId && form.ownerText" class="mt-1 flex items-center gap-2 text-[11px] text-gray-400">
              <span class="truncate">历史归属「{{ form.ownerText }}」归入未关联部门</span>
              <button type="button" class="text-primary hover:underline shrink-0" @click="form.ownerText = ''">清除</button>
            </p>
          </div>
        </div>
        <div>
          <label class="block text-xs font-semibold text-gray-400 mb-1.5">标签<span class="ml-1 font-normal text-gray-300">（回车或逗号分隔，可多个）</span></label>
          <TagInput v-model="form.tags" placeholder="输入后按回车添加" />
        </div>
        <div>
          <label class="block text-xs font-semibold text-gray-400 mb-1.5">备注信息</label>
          <textarea v-model="form.notes" class="w-full border border-gray-200 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary" rows="2" placeholder="备注"></textarea>
        </div>
        
        <!-- 自定义提问置顶配置 -->
        <div class="border border-gray-100 bg-gray-50/30 p-3 rounded-xl space-y-2">
          <div class="flex justify-between items-center">
            <label class="block text-xs font-semibold text-gray-400 select-none">门户快捷提问置顶 (最多 3 个)</label>
            <button 
              v-if="customQuestions.length < 3"
              type="button"
              class="text-xs text-primary hover:text-primary-hover font-semibold transition-colors flex items-center gap-0.5 cursor-pointer"
              @click="customQuestions.push({ label: '', query: '' })"
            >
              ＋ 添加提问
            </button>
          </div>
          
          <div v-if="customQuestions.length === 0" class="text-xs text-gray-400 italic py-2 text-center select-none">
            暂无置顶提问（当前由大模型智能推荐）
          </div>
          
          <div v-else class="space-y-2">
            <div 
              v-for="(q, idx) in customQuestions" 
              :key="idx"
              class="bg-white p-2 rounded-lg border border-gray-200/60 shadow-sm relative group flex flex-col gap-2"
            >
              <button 
                type="button" 
                class="absolute top-1 right-2.5 text-gray-300 hover:text-red-500 font-bold focus:outline-none transition-colors cursor-pointer text-sm" 
                @click="customQuestions.splice(idx, 1)"
              >
                ×
              </button>
              <div class="grid grid-cols-3 gap-2 pr-4 pt-1">
                <div class="col-span-1">
                  <input 
                    v-model="q.label" 
                    class="w-full border border-gray-200 rounded-lg px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary" 
                    placeholder="按钮简称" 
                    maxlength="15" 
                  />
                </div>
                <div class="col-span-2">
                  <input 
                    v-model="q.query" 
                    class="w-full border border-gray-200 rounded-lg px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary" 
                    placeholder="给 AI 的提问问题..." 
                  />
                </div>
              </div>
            </div>
          </div>
        </div>

        <!--
          额外扩展配置：本质是开发者的逃生舱口，不该摆在一级表单里。
          只有 folder_structure 与 custom_questions 会被读取，其余键写了没人用；
          而保存是整份覆盖——手滑删掉 folder_structure，所有文档会掉回「未分类」且无法撤销。
          因此折叠起来 + 把代价写在脸上。
        -->
        <div class="overflow-hidden rounded-xl border border-gray-200">
          <button
            type="button"
            class="flex w-full items-center justify-between bg-gray-50/60 px-3 py-2.5 text-left transition-colors hover:bg-gray-50"
            :aria-expanded="advancedConfigOpen"
            @click="advancedConfigOpen = !advancedConfigOpen"
          >
            <span class="flex items-center gap-1.5">
              <svg
                class="h-3.5 w-3.5 shrink-0 text-gray-400 transition-transform duration-200"
                :class="{ 'rotate-90': advancedConfigOpen }"
                fill="none" stroke="currentColor" viewBox="0 0 24 24"
              >
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
              </svg>
              <span class="text-xs font-semibold text-gray-500">高级设置</span>
              <span class="text-[11px] text-gray-400">· 额外扩展配置 JSON</span>
            </span>
            <span class="shrink-0 text-[10px] text-gray-400">{{ advancedConfigOpen ? '收起' : '展开' }}</span>
          </button>

          <div v-show="advancedConfigOpen" class="space-y-2 border-t border-gray-100 px-3 pb-3 pt-2.5">
            <div class="space-y-1.5 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2 text-[11px] leading-relaxed text-amber-800">
              <p><b>保存是整份覆盖。</b>删掉 <code class="font-mono">folder_structure</code> 会让所有文档掉回「未分类」，虚拟文件夹丢失且无法撤销。</p>
              <p>只有下面两个键会被读取，其余键写了也不会生效：</p>
              <ul class="list-disc space-y-0.5 pl-4">
                <li><code class="font-mono">folder_structure</code>：虚拟文件夹映射，由「新建文件夹 / 移动文档」自动维护，一般无需手改。</li>
                <li><code class="font-mono">custom_questions</code>：知识库门户的快捷提问，形如 <code class="font-mono">[{label:'总结核心内容', query:'请帮我梳理要点'}]</code>。</li>
              </ul>
            </div>
            <textarea
              v-model="form.extraConfigText"
              class="w-full rounded-xl border border-gray-200 bg-white px-3 py-2 font-mono text-xs focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary disabled:bg-gray-100"
              rows="4"
              placeholder="{}"
            ></textarea>
          </div>
        </div>
        <div class="flex justify-end gap-3 pt-2">
          <button class="px-4 py-2 rounded-xl border text-sm font-semibold hover:bg-gray-50 transition-all" @click="showEditModal = false">取消</button>
          <button class="px-4 py-2 rounded-xl bg-primary text-white text-sm font-semibold disabled:opacity-50" :disabled="!isEngineReady || savingMetadata" @click="updateMetadata">{{ savingMetadata ? '保存中…' : '保存' }}</button>
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
    <ConfirmModal
      v-if="pendingPermissionRemoval"
      title="确认移除知识库授权"
      :loading="removingPermission"
      :message="`确定要移除${pendingPermissionRemoval.type === 'role' ? '角色' : '用户'}「${pendingPermissionRemoval.name}」对此知识库的访问授权吗？该对象将立即失去访问权限。`"
      confirm-text="确认移除"
      @confirm="confirmRemovePermission"
      @cancel="pendingPermissionRemoval = null"
    />
    <Modal
      :show="uploadConflicts.length > 0"
      title="同名文档已存在"
      size="max-w-lg"
      @close="resolveUploadConflicts('skip')"
    >
      <div class="space-y-4">
        <p class="text-sm text-gray-600">
          本次上传中有 <b class="text-primary">{{ uploadConflicts.length }}</b> 个文件名与库中已有文档重名：
        </p>
        <ul class="max-h-40 overflow-y-auto rounded-lg bg-gray-50 border border-gray-100 p-2 text-xs space-y-1">
          <li v-for="item in uploadConflicts" :key="item.oldDocumentId" class="truncate text-gray-500">
            · {{ item.oldName }}
          </li>
        </ul>
        <div class="rounded-lg bg-amber-50 border border-amber-100 p-3 text-[11px] text-amber-700 leading-relaxed">
          替换 = 删除旧文档后上传新文件并重新切片。<b>历史引用（对话引用、智能体检索）会指向已删除的旧文档</b>，需要重新提问才会命中新内容。
        </div>
        <div class="flex flex-col gap-2">
          <button
            type="button"
            class="w-full px-3 py-2 rounded-xl bg-primary text-white text-xs font-semibold hover:bg-primary/90 disabled:opacity-50"
            :disabled="uploading"
            @click="resolveUploadConflicts('replace')"
          >替换这 {{ uploadConflicts.length }} 个（重新切片）</button>
          <button
            type="button"
            class="w-full px-3 py-2 rounded-xl border border-gray-200 text-gray-600 text-xs font-semibold hover:bg-gray-50 disabled:opacity-50"
            :disabled="uploading"
            @click="resolveUploadConflicts('keep')"
          >保留两者（新文件另存一份）</button>
          <button
            type="button"
            class="w-full px-3 py-2 rounded-xl border border-gray-200 text-gray-400 text-xs font-semibold hover:bg-gray-50 disabled:opacity-50"
            :disabled="uploading"
            @click="resolveUploadConflicts('skip')"
          >跳过这 {{ uploadConflicts.length }} 个</button>
        </div>
      </div>
    </Modal>
    <Modal :show="!!renamingDoc" title="重命名文档" size="max-w-md" @close="renamingDoc = null">
      <div class="space-y-4">
        <input
          v-model="docRenameValue"
          type="text"
          maxlength="255"
          class="w-full rounded-xl border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
          placeholder="文档名称"
          @keyup.enter="confirmRenameDocument"
        />
        <p class="text-xs text-gray-400 leading-relaxed">
          改名不会触发重新解析，已有切片与引用保持不变；只影响列表里显示的名字。
        </p>
        <div class="flex justify-end gap-2">
          <button
            type="button"
            class="px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 text-xs font-semibold hover:bg-gray-50"
            @click="renamingDoc = null"
          >取消</button>
          <button
            type="button"
            class="px-3 py-1.5 rounded-lg bg-primary text-white text-xs font-semibold hover:bg-primary/90 disabled:opacity-50"
            :disabled="docRenameBusy || !docRenameValue.trim()"
            @click="confirmRenameDocument"
          >{{ docRenameBusy ? '保存中…' : '保存' }}</button>
        </div>
      </div>
    </Modal>
    <ConfirmModal
      v-if="pendingBulkDelete"
      title="确认批量删除知识库"
      :loading="bulkWorking"
      :message="`确定删除选中的 ${pendingBulkDelete.length} 个知识库吗？RAGFlow 侧集群数据将同步卸载，此操作不可逆。`"
      confirm-text="确认删除"
      @confirm="bulkDeleteDatasets"
      @cancel="pendingBulkDelete = null"
    />
    <ConfirmModal
      v-if="pendingFolderRemoval"
      title="确认删除文件夹"
      :loading="removingFolder"
      :message="`确定要删除文件夹「${pendingFolderRemoval.folderName}」吗？其中的所有文档将自动移入未分类根目录（不会删除物理文档）。`"
      confirm-text="确认删除"
      @confirm="confirmRemoveFolder"
      @cancel="pendingFolderRemoval = null"
    />
    <ConfirmModal
      v-if="aiOverwritePending"
      title="覆盖已填写的元数据"
      message="AI 智能分析的结果会直接覆盖当前已填写的「描述说明 / 标签 / 备注」，且无法撤销。确定继续吗？"
      confirm-text="继续覆盖"
      @confirm="confirmAiOverwrite"
      @cancel="aiOverwritePending = false"
    />

    <!-- Document original file preview -->
    <Modal
      :show="showDocPreview"
      :title="`原文件预览：${selectedDocument?.name || ''}`"
      size="max-w-5xl"
      maximizable
      @close="closeDocPreview"
    >
      <!-- maximized 由 Modal 内部状态透出：普通态仍是固定的 70vh（与原行为一致），
           最大化时改用 h-full 吃满内容区高度，DocumentViewer 与 iframe 两个分支共用 -->
      <template #default="{ maximized }">
      <div :class="maximized ? 'h-full w-full' : 'h-[70vh] w-full'">
        <DocumentViewer
          v-if="canPreviewSelectedDocument && showDocPreview && previewDocUrl"
          :url="previewDocUrl"
          :filename="selectedDocument?.name || ''"
          :meta="{ filename: selectedDocument?.name || '', mime: resolveDocumentViewerMime(selectedDocument?.name || '') }"
          theme="light"
          @fallback-download="downloadPreviewDocument"
        />
        <iframe
          v-else-if="showDocPreview && previewDocUrl"
          :src="previewDocUrl"
          class="w-full h-full rounded-xl border border-gray-200 bg-white"
          title="文档原文件预览"
        ></iframe>
      </div>
      </template>
    </Modal>

    <!-- 发现：看得见存在、但还没拿到权限的公共 / 部门库 -->
    <Modal :show="showDiscover" title="发现知识库" size="max-w-3xl" @close="showDiscover = false">
      <div class="space-y-3">
        <div class="flex items-center gap-2">
          <input
            v-model="discoverKeyword"
            type="search"
            placeholder="按名称 / 描述 / 归属部门搜索"
            class="min-w-0 flex-1 rounded-xl border border-gray-300 px-3 py-2 text-sm"
            @keyup.enter="fetchDiscover(1)"
          />
          <button type="button" class="px-3 py-2 rounded-xl bg-primary text-white text-sm font-semibold hover:bg-primary/90" @click="fetchDiscover(1)">搜索</button>
        </div>
        <p class="text-[11px] text-gray-400 leading-relaxed">
          这里只列授权可见与 TEAM 级知识库，且只列你还没有权限的库（申请中的也算没有）。个人私有库既不会出现在这里，也无法申请——它的可见范围由归属人自己决定。
        </p>
        <p class="text-[11px] text-gray-400 leading-relaxed">
          没有知识库管理菜单？可以在「我的工作台 › 资源广场」看到完整目录（含已授权的库）并提交申请。
        </p>

        <div v-if="discoverLoading" class="py-10 text-center text-sm text-gray-400">加载中…</div>
        <div v-else-if="!discoverItems.length" class="py-10 text-center text-sm text-gray-400 italic">
          没有可发现的知识库。你可能已经拥有全部可见库的权限，或者平台还没有公共库。
        </div>
        <ul v-else class="max-h-[55vh] space-y-2 overflow-y-auto pr-1">
          <li v-for="item in discoverItems" :key="item.dataset_id" class="rounded-xl border border-gray-200 p-3 hover:border-gray-300 transition-colors">
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-2">
                  <p class="truncate text-sm font-semibold text-gray-900">{{ item.name }}</p>
                  <span
                    class="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold"
                    :class="item.visibility === 'public' ? 'bg-blue-50 text-blue-600' : 'bg-indigo-50 text-indigo-600'"
                  >{{ item.visibility === 'public' ? '授权可见' : 'TEAM 级' }}</span>
                </div>
                <p class="mt-1 text-xs text-gray-500 line-clamp-2 leading-relaxed">{{ item.description || '暂无描述' }}</p>

                <!-- 规模与新鲜度：判断「值不值得申请」主要看这几个数 -->
                <div class="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-gray-400">
                  <span class="inline-flex items-center gap-1">
                    <svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24" aria-hidden="true">
                      <path stroke-linecap="round" stroke-linejoin="round" d="M7 21h10a2 2 0 002-2V9l-6-6H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
                    </svg>
                    {{ item.doc_count === null || item.doc_count === undefined ? '—' : item.doc_count }} 个文档
                  </span>
                  <span class="inline-flex items-center gap-1">
                    <svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24" aria-hidden="true">
                      <path stroke-linecap="round" stroke-linejoin="round" d="M4 6h16M4 12h16M4 18h10" />
                    </svg>
                    {{ item.chunk_count === null || item.chunk_count === undefined ? '—' : item.chunk_count }} 个切片
                  </span>
                  <span v-if="item.owner" class="truncate">归属 {{ item.owner }}</span>
                  <span v-if="discoverUpdatedText(item)">更新于 {{ discoverUpdatedText(item) }}</span>
                </div>

                <div v-if="item.tags && item.tags.length" class="mt-1.5 flex flex-wrap gap-1">
                  <span v-for="tag in item.tags" :key="tag" class="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-500">{{ tag }}</span>
                </div>
              </div>
              <div class="shrink-0 pt-0.5">
                <span
                  v-if="item.request_status"
                  class="text-[11px] font-semibold"
                  :class="{
                    'text-amber-600': item.request_status === 'pending',
                    'text-emerald-600': item.request_status === 'approved',
                    'text-red-500': item.request_status === 'rejected',
                    'text-gray-400': item.request_status === 'cancelled',
                  }"
                >{{ discoverStatusLabel(item.request_status) }}</span>
                <button
                  v-else
                  type="button"
                  class="rounded-lg bg-primary px-2.5 py-1.5 text-[11px] font-semibold text-white hover:bg-primary/90"
                  @click="startAccessRequest(item)"
                >申请访问</button>
              </div>
            </div>
          </li>
        </ul>

        <div class="flex items-center justify-between border-t pt-2">
          <span class="text-xs text-gray-500">共 {{ discoverTotal }} 个可申请</span>
          <div class="flex items-center gap-2">
            <button type="button" class="px-2.5 py-1.5 border rounded-lg text-xs font-semibold hover:bg-gray-50 disabled:opacity-40" :disabled="discoverPage <= 1 || discoverLoading" @click="fetchDiscover(discoverPage - 1)">上一页</button>
            <span class="text-xs text-gray-600 font-mono">{{ discoverPage }} / {{ Math.max(1, Math.ceil(discoverTotal / DISCOVER_PAGE_SIZE)) }}</span>
            <button type="button" class="px-2.5 py-1.5 border rounded-lg text-xs font-semibold hover:bg-gray-50 disabled:opacity-40" :disabled="discoverPage >= Math.ceil(discoverTotal / DISCOVER_PAGE_SIZE) || discoverLoading" @click="fetchDiscover(discoverPage + 1)">下一页</button>
          </div>
        </div>
      </div>
    </Modal>

    <!-- 申请理由 -->
    <Modal :show="!!applyingDataset" title="申请访问知识库" size="max-w-lg" @close="applyingDataset = null">
      <div class="space-y-3">
        <div class="rounded-xl bg-gray-50 px-3 py-2">
          <p class="text-sm font-semibold text-gray-900">{{ applyingDataset?.name }}</p>
          <p class="mt-0.5 text-xs text-gray-500">{{ applyingDataset?.description || '暂无描述' }}</p>
        </div>
        <label class="block">
          <span class="mb-1 block text-xs font-medium text-gray-700">申请理由（可选，写清楚更容易通过）</span>
          <textarea
            v-model="applyReason"
            rows="3"
            maxlength="2000"
            placeholder="例如：季度对账需要查 2024 年的报销制度"
            class="w-full rounded-lg border border-gray-300 p-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
          />
        </label>
        <p class="text-[11px] text-gray-400 leading-relaxed">
          提交后由库归属人审批，通过时会直接给你加上访问授权。审批进度可以在「个人中心 → 我的申请」里看。
        </p>
        <div class="flex justify-end gap-2 border-t pt-2">
          <button type="button" class="px-3 py-1.5 rounded-lg border border-gray-200 text-sm text-gray-600 font-semibold hover:bg-gray-50" @click="applyingDataset = null">取消</button>
          <button
            type="button"
            class="px-3 py-1.5 rounded-lg bg-primary text-white text-sm font-semibold hover:bg-primary/90 disabled:opacity-50"
            :disabled="applySubmitting"
            @click="submitAccessRequest"
          >{{ applySubmitting ? '提交中…' : '提交申请' }}</button>
        </div>
      </div>
    </Modal>

    <!-- 全平台审计（管理员）：逐库的操作日志只回答"这个库发生了什么"，
         这里回答"某个人今天动了哪些库" -->
    <Modal :show="showGlobalAudit" title="全平台审计日志" size="max-w-5xl" @close="showGlobalAudit = false">
      <div class="space-y-3">
        <div class="flex flex-wrap items-end gap-2 rounded-xl bg-gray-50 border border-gray-200 px-3 py-2.5">
          <label class="flex flex-col gap-1">
            <span class="text-[11px] font-medium text-gray-500">关键词（操作人 / 错误信息）</span>
            <input v-model="globalAuditKeyword" type="search" placeholder="如 alice" class="w-40 rounded-lg border border-gray-300 px-2 py-1.5 text-xs" @keyup.enter="fetchGlobalAudit(1)" />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-[11px] font-medium text-gray-500">开始日期</span>
            <input v-model="globalAuditStart" type="date" class="rounded-lg border border-gray-300 px-2 py-1.5 text-xs" />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-[11px] font-medium text-gray-500">结束日期</span>
            <input v-model="globalAuditEnd" type="date" class="rounded-lg border border-gray-300 px-2 py-1.5 text-xs" />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-[11px] font-medium text-gray-500">动作</span>
            <select v-model="globalAuditAction" class="rounded-lg border border-gray-300 px-2 py-1.5 text-xs">
              <option value="">全部动作</option>
              <option v-for="(label, key) in AUDIT_ACTION_LABELS" :key="key" :value="key">{{ label }}</option>
            </select>
          </label>
          <button type="button" class="px-3 py-1.5 rounded-lg bg-primary text-white text-xs font-semibold hover:bg-primary/90" @click="fetchGlobalAudit(1)">查询</button>
          <button type="button" class="px-3 py-1.5 rounded-lg border border-gray-200 bg-white text-xs font-semibold text-gray-600 hover:bg-gray-50" @click="exportGlobalAudit">导出 CSV</button>
        </div>

        <div class="max-h-[60vh] overflow-y-auto border border-gray-200 rounded-xl">
          <table class="min-w-full text-xs">
            <thead class="bg-gray-50 sticky top-0">
              <tr class="text-left text-gray-500">
                <th class="px-3 py-2 font-semibold whitespace-nowrap">时间</th>
                <th class="px-3 py-2 font-semibold whitespace-nowrap">动作</th>
                <th class="px-3 py-2 font-semibold">知识库</th>
                <th class="px-3 py-2 font-semibold whitespace-nowrap">操作人</th>
                <th class="px-3 py-2 font-semibold whitespace-nowrap">结果</th>
                <th class="px-3 py-2 font-semibold">详情</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100">
              <tr v-if="globalAuditLoading">
                <td colspan="6" class="px-3 py-8 text-center text-gray-400">加载中…</td>
              </tr>
              <tr v-else-if="globalAuditItems.length === 0">
                <td colspan="6" class="px-3 py-8 text-center text-gray-400 italic">没有匹配的记录</td>
              </tr>
              <template v-else>
              <tr v-for="item in globalAuditItems" :key="item.id" class="hover:bg-gray-50/60">
                <td class="px-3 py-2 font-mono text-gray-500 whitespace-nowrap">{{ String(item.created_at || '').replace('T', ' ').slice(0, 19) }}</td>
                <td class="px-3 py-2 whitespace-nowrap text-gray-700">{{ AUDIT_ACTION_LABELS[item.action] || item.action }}</td>
                <td class="px-3 py-2 font-mono text-gray-500">
                  <span v-if="item.dataset_id === '__global__'" class="text-gray-400">（全局）</span>
                  <span v-else>{{ datasetNameById(item.dataset_id) || item.dataset_id }}</span>
                </td>
                <td class="px-3 py-2 whitespace-nowrap text-gray-700">{{ item.user_name || item.user_id || '-' }}</td>
                <td class="px-3 py-2 whitespace-nowrap">
                  <span v-if="item.status_code === 200" class="text-emerald-600">成功</span>
                  <span v-else class="text-red-600" :title="item.error_message || ''">{{ item.status_code || '失败' }}</span>
                </td>
                <td class="px-3 py-2 text-gray-500 max-w-[22rem] truncate" :title="JSON.stringify(item.detail || {})">{{ item.error_message || JSON.stringify(item.detail || {}) }}</td>
              </tr>
              </template>
            </tbody>
          </table>
        </div>

        <div class="flex items-center justify-between">
          <span class="text-xs text-gray-500">共 {{ globalAuditTotal }} 条</span>
          <div class="flex items-center gap-2">
            <button type="button" class="px-2.5 py-1.5 border rounded-lg text-xs font-semibold hover:bg-gray-50 disabled:opacity-40" :disabled="globalAuditPage <= 1 || globalAuditLoading" @click="fetchGlobalAudit(globalAuditPage - 1)">上一页</button>
            <span class="text-xs text-gray-600 font-mono">{{ globalAuditPage }} / {{ Math.max(1, Math.ceil(globalAuditTotal / GLOBAL_AUDIT_PAGE_SIZE)) }}</span>
            <button type="button" class="px-2.5 py-1.5 border rounded-lg text-xs font-semibold hover:bg-gray-50 disabled:opacity-40" :disabled="globalAuditPage >= Math.ceil(globalAuditTotal / GLOBAL_AUDIT_PAGE_SIZE) || globalAuditLoading" @click="fetchGlobalAudit(globalAuditPage + 1)">下一页</button>
          </div>
          <button type="button" class="px-4 py-2 rounded-xl bg-gray-900 text-white text-sm font-semibold hover:bg-gray-800" @click="showGlobalAudit = false">关闭</button>
        </div>
      </div>
    </Modal>

    <!-- View chunks modal -->
    <Modal :show="showChunksModal" title="文档切片内容查看" size="max-w-4xl" @close="closeChunksModal">
      <div class="space-y-4">
        <div class="flex items-center justify-between border-b pb-3">
          <div>
            <h3 class="text-sm font-bold text-gray-900 truncate max-w-lg">{{ viewingDocument?.name }}</h3>
            <p class="text-xs text-gray-400 mt-1">
              共 {{ chunksTotal }} 个分块，当前展示第 {{ chunksPage }} 页
              <span class="ml-1 text-gray-300">·「查看原文」对 PDF 会跳到对应页，其余格式只打开文件</span>
            </p>
          </div>
          <div class="flex items-center gap-2">
            <button
              class="px-2.5 py-1.5 border rounded-lg text-xs font-semibold hover:bg-gray-50 disabled:opacity-40"
              :disabled="chunksPage <= 1 || loadingChunks"
              @click="loadChunksPage(chunksPage - 1)"
            >
              上一页
            </button>
            <span class="text-xs text-gray-600 font-medium font-mono">{{ chunksPage }} / {{ chunksTotalPages }}</span>
            <button
              class="px-2.5 py-1.5 border rounded-lg text-xs font-semibold hover:bg-gray-50 disabled:opacity-40"
              :disabled="chunksPage >= chunksTotalPages || loadingChunks"
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
        <div v-else-if="chunksError" class="py-12 text-center text-sm flex flex-col items-center gap-2">
          <span class="text-amber-600">切片数据加载失败</span>
          <button type="button" class="text-xs font-semibold text-primary hover:underline" @click="fetchChunks">重试</button>
        </div>
        <div v-else-if="chunksList.length === 0" class="py-12 text-center text-gray-400 text-sm italic">
          暂无切片数据，可能文档尚未解析成功
        </div>
        <div v-else class="space-y-3 max-h-[60vh] overflow-y-auto pr-1">
          <div
            v-for="(chunk, idx) in chunksList"
            :key="chunk.id || idx"
            class="bg-gray-50 p-4 rounded-xl border border-gray-200 hover:border-gray-200 transition-all flex flex-col gap-2"
          >
            <div class="flex items-center justify-between gap-2">
              <span class="text-xs font-bold text-gray-400 font-mono">#{{ (chunksPage - 1) * 30 + idx + 1 }} (ID: {{ chunk.id || '-' }})</span>
              <div v-if="canEdit" class="flex items-center gap-2 shrink-0">
                <label
                  class="flex items-center gap-1 text-[11px] cursor-pointer select-none"
                  :class="isChunkDisabled(chunk) ? 'text-amber-600' : 'text-gray-500'"
                  title="停用后这条切片仍保留，但不再参与召回"
                >
                  <input
                    type="checkbox"
                    class="w-3 h-3 rounded border-gray-300 text-primary focus:ring-primary cursor-pointer"
                    :checked="!isChunkDisabled(chunk)"
                    :disabled="chunkSaving"
                    @change="toggleChunk(chunk)"
                  />
                  {{ isChunkDisabled(chunk) ? '已停用' : '参与召回' }}
                </label>
                <button
                  type="button"
                  class="text-[11px] text-primary hover:underline disabled:text-gray-300 disabled:no-underline"
                  :disabled="!resolveChunkPage(chunk)"
                  :title="resolveChunkPage(chunk)
                    ? `在原文第 ${resolveChunkPage(chunk)} 页查看`
                    : '这条切片没有页码信息（纯文本类文档无法定位原页）'"
                  @click="openChunkSource(chunk)"
                >查看原文{{ resolveChunkPage(chunk) ? ` p.${resolveChunkPage(chunk)}` : '' }}</button>
                <button type="button" class="text-[11px] text-primary hover:underline" @click="startEditChunk(chunk)">编辑</button>
                <button type="button" class="text-[11px] text-red-500 hover:underline" @click="removeChunk(chunk)">删除</button>
              </div>
            </div>
            <template v-if="editingChunkId === chunk.id">
              <textarea
                v-model="editingChunkContent"
                rows="6"
                class="w-full text-xs font-mono leading-relaxed rounded-lg border border-primary/40 p-3 bg-white focus:outline-none focus:ring-2 focus:ring-primary/20"
              />
              <div class="flex items-center justify-between">
                <span class="text-[11px] text-gray-400">保存后需要数秒重建向量，期间召回可能仍是旧内容</span>
                <div class="flex gap-2">
                  <button type="button" class="px-2.5 py-1 rounded-lg border border-gray-200 text-[11px] text-gray-600 font-semibold hover:bg-white" @click="cancelEditChunk">取消</button>
                  <button
                    type="button"
                    class="px-2.5 py-1 rounded-lg bg-primary text-white text-[11px] font-semibold hover:bg-primary/90 disabled:opacity-50"
                    :disabled="chunkSaving || !editingChunkContent.trim()"
                    @click="saveChunk"
                  >{{ chunkSaving ? '保存中…' : '保存' }}</button>
                </div>
              </div>
            </template>
            <p
              v-else
              class="text-sm text-gray-700 leading-relaxed font-mono whitespace-pre-wrap select-text bg-white p-3 rounded-lg border border-gray-200/50 shadow-inner"
              :class="isChunkDisabled(chunk) ? 'opacity-60 line-through decoration-gray-300' : ''"
            >{{ chunk.content_with_weight || chunk.content || '-' }}</p>
          </div>
        </div>

        <!-- 手工补切片：把 QA / 口径 / 注意事项直接塞进知识库，不必先造个文档 -->
        <div v-if="canEdit" class="border-t pt-3">
          <div v-if="showNewChunkForm" class="space-y-2">
            <textarea
              v-model="newChunkContent"
              rows="4"
              placeholder="粘贴要补充的内容（QA、口径、注意事项均可）。新增后需要数秒重建向量。"
              class="w-full text-xs font-mono leading-relaxed rounded-lg border border-gray-300 p-3 bg-white focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
            <div class="flex justify-end gap-2">
              <button
                type="button"
                class="px-3 py-1.5 rounded-lg border border-gray-200 text-xs text-gray-600 font-semibold hover:bg-gray-50"
                @click="showNewChunkForm = false; newChunkContent = ''"
              >取消</button>
              <button
                type="button"
                class="px-3 py-1.5 rounded-lg bg-primary text-white text-xs font-semibold hover:bg-primary/90 disabled:opacity-50"
                :disabled="chunkSaving || !newChunkContent.trim()"
                @click="createChunk"
              >{{ chunkSaving ? '新增中…' : '新增切片' }}</button>
            </div>
          </div>
          <button
            v-else
            type="button"
            class="w-full py-2 rounded-xl border border-dashed border-gray-300 text-xs font-semibold text-gray-500 hover:border-primary hover:text-primary transition-all"
            @click="showNewChunkForm = true"
          >+ 手工新增一条切片</button>
        </div>

        <div class="flex justify-end pt-2 border-t">
          <button class="px-4 py-2 rounded-xl bg-gray-900 hover:bg-gray-800 text-white text-sm font-semibold transition-all" @click="closeChunksModal">关闭</button>
        </div>
      </div>
    </Modal>

    <!-- Transfer owner modal -->
    <Modal
      :show="showTransferOwnerModal"
      title="转移知识库归属人"
      size="max-w-lg"
      @close="closeTransferOwner"
    >
      <div class="space-y-4">
        <div class="rounded-xl bg-amber-50 border border-amber-100 px-4 py-3 text-xs text-amber-700 leading-relaxed">
          转移后，<b>你不再拥有该库的管理权</b>（不能改元数据、删库、分配授权）。<br />
          <template v-if="transferTarget && visibilityOf(transferTarget) === 'private'">
            这是<b>个人私有</b>库：原归属人会立刻失去访问，之后只有新归属人能看到。
          </template>
          <template v-else>
            授权可见 / TEAM 级的授权名单与部门可见范围不变，只是管理权换人。
          </template>
        </div>

        <div>
          <label class="mb-1 block text-xs font-medium text-gray-700">搜索用户</label>
          <div class="flex gap-2">
            <input
              v-model="transferKeyword"
              type="search"
              class="min-w-0 flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm"
              placeholder="按姓名或登录名搜索"
              @keyup.enter="fetchTransferCandidates"
            />
            <button
              type="button"
              class="rounded-lg border border-gray-300 px-3 text-xs text-gray-600 hover:bg-gray-50"
              :disabled="loadingTransferCandidates"
              @click="fetchTransferCandidates"
            >
              搜索
            </button>
          </div>
        </div>

        <div class="max-h-64 overflow-y-auto rounded-xl border border-gray-200 divide-y divide-gray-100">
          <div v-if="loadingTransferCandidates" class="py-8 text-center text-xs text-gray-400">加载中...</div>
          <div v-else-if="!transferCandidates.length" class="py-8 text-center text-xs text-gray-400">
            没有匹配的启用用户
          </div>
          <button
            v-for="candidate in transferCandidates"
            :key="candidate.id"
            type="button"
            class="w-full px-4 py-2.5 text-left text-sm flex items-center justify-between hover:bg-gray-50 transition-colors"
            :class="transferUserId === candidate.id ? 'bg-primary/5' : ''"
            @click="transferUserId = candidate.id"
          >
            <span class="flex items-center gap-2 min-w-0">
              <span class="font-medium text-gray-800 truncate">{{ candidate.real_name || candidate.user_name }}</span>
              <span
                v-if="candidate.can_manage === false"
                class="shrink-0 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700"
                title="缺少 element:knowledge:edit，转移后改元数据 / 再转移 / 分配授权会被拒"
              >
                无编辑权限
              </span>
            </span>
            <span class="text-xs text-gray-400 font-mono shrink-0">{{ candidate.user_name }}</span>
          </button>
        </div>

        <div
          v-if="selectedTransferCandidate && selectedTransferCandidate.can_manage === false"
          class="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-700 leading-relaxed"
        >
          该用户没有<b>知识库编辑</b>权限：转移后他能接手归属，但改元数据、再转移、分配授权仍会被拒。
          转移完成后需要管理员给他补上 <code class="font-mono">element:knowledge:edit</code>。
        </div>

        <div class="flex justify-end gap-2 pt-2 border-t">
          <button
            type="button"
            class="px-4 py-2 rounded-xl border border-gray-200 text-sm hover:bg-gray-50"
            @click="closeTransferOwner"
          >
            取消
          </button>
          <button
            type="button"
            class="px-4 py-2 rounded-xl bg-primary text-white text-sm font-semibold transition-all disabled:opacity-40"
            :disabled="!transferUserId || transferring"
            @click="submitTransferOwner"
          >
            {{ transferring ? '转移中...' : '确认转移' }}
          </button>
        </div>
      </div>
    </Modal>

    <!-- Add Permission Modal -->
    <Modal :show="showAddPermissionModal" title="分配知识库授权成员" size="max-w-md" @close="closeAddPermissionModal">
      <div class="space-y-4">
        <!-- 切换类型 -->
        <div>
          <label class="block text-xs font-semibold text-gray-400 mb-1.5 select-none">成员授权类型</label>
          <div class="grid grid-cols-2 gap-2 bg-gray-50 p-1 rounded-xl border border-gray-200">
            <button 
              type="button"
              @click="assignType = 'role'; selectedCandidateIds = []"
              class="py-2 text-xs font-semibold rounded-lg transition-all cursor-pointer font-bold"
              :class="assignType === 'role' ? 'bg-white shadow text-primary' : 'text-gray-500 hover:text-gray-800'"
            >
              按角色授权 (Role)
            </button>
            <button 
              type="button"
              @click="assignType = 'user'; selectedCandidateIds = []"
              class="py-2 text-xs font-semibold rounded-lg transition-all cursor-pointer font-bold"
              :class="assignType === 'user' ? 'bg-white shadow text-primary' : 'text-gray-500 hover:text-gray-800'"
            >
              按个人授权 (User)
            </button>
          </div>
        </div>

        <!-- 搜索输入框 -->
        <div class="relative">
          <span class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-gray-400">
             <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
               <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
             </svg>
          </span>
          <input 
            v-model="candidateSearchQuery"
            type="search"
            class="block w-full pl-9 pr-3 py-2 border border-gray-200 rounded-xl leading-5 bg-gray-50 placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary sm:text-xs transition-all focus:bg-white"
            :placeholder="assignType === 'role' ? '搜索角色名称或代码...' : '搜索用户姓名、账号或部门...'"
          />
        </div>

        <!-- 候选人列表勾选 -->
        <div class="space-y-2">
          <label class="block text-xs font-semibold text-gray-400 select-none">
            选择要添加的{{ assignType === 'role' ? '角色' : '用户' }} (可多选)
          </label>
          <div class="border border-gray-200 rounded-xl max-h-[30vh] overflow-y-auto divide-y divide-gray-100 bg-white">
            <!-- 候选角色 -->
            <template v-if="assignType === 'role'">
              <div 
                v-for="r in availableRoles" 
                :key="r.id"
                class="flex items-center gap-3 p-3 hover:bg-gray-50 transition-all select-none cursor-pointer"
                @click="toggleCandidateSelection(r.id)"
              >
                <input 
                  type="checkbox" 
                  :checked="selectedCandidateIds.includes(r.id)"
                  class="rounded text-indigo-600 border-gray-300 focus:ring-indigo-500" 
                  @click.stop="toggleCandidateSelection(r.id)"
                />
                <div class="flex flex-col">
                  <span class="text-sm font-semibold text-gray-800">{{ r.name }}</span>
                  <span class="text-[10px] text-gray-400 font-mono mt-0.5">{{ r.code }}</span>
                </div>
              </div>
              <div v-if="!availableRoles.length" class="text-xs text-gray-400 text-center py-8 select-none">
                所有角色已完成分配授权。
              </div>
            </template>

            <!-- 候选用户 -->
            <template v-if="assignType === 'user'">
              <div 
                v-for="u in availableUsers" 
                :key="u.id"
                class="flex items-center gap-3 p-3 hover:bg-gray-50 transition-all select-none cursor-pointer"
                @click="toggleCandidateSelection(u.id)"
              >
                <input 
                  type="checkbox" 
                  :checked="selectedCandidateIds.includes(u.id)"
                  class="rounded text-emerald-600 border-gray-300 focus:ring-emerald-500" 
                  @click.stop="toggleCandidateSelection(u.id)"
                />
                <div class="flex flex-col min-w-0">
                  <span class="text-sm font-semibold text-gray-800 truncate">{{ u.real_name || u.user_name }}</span>
                  <span class="text-[10px] text-gray-400 font-mono mt-0.5 truncate">{{ u.user_name }}</span>
                </div>
                <!-- 部门：授权时最难判断的就是「这个人到底在不在我要授权的部门」 -->
                <span v-if="u.departments?.length" class="ml-auto flex flex-wrap gap-1 justify-end shrink-0 max-w-[45%]">
                  <span
                    v-for="dept in u.departments"
                    :key="dept.id"
                    class="text-[10px] leading-none px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 border border-gray-200/70"
                  >{{ dept.name }}</span>
                </span>
                <span v-else class="ml-auto text-[10px] text-gray-300 shrink-0">未分配部门</span>
              </div>
              <div v-if="!availableUsers.length" class="text-xs text-gray-400 text-center py-8 select-none">
                所有活跃用户已完成分配授权。
              </div>
            </template>
          </div>
        </div>

        <div class="flex justify-end gap-3 pt-2">
          <button class="px-4 py-2 rounded-xl border text-sm font-semibold hover:bg-gray-50 transition-all" @click="closeAddPermissionModal">取消</button>
          <button 
            class="px-4 py-2 rounded-xl text-sm font-semibold text-white transition-all shadow-md disabled:opacity-50 bg-primary hover:bg-primary-hover"
            :disabled="!selectedCandidateIds.length || savingPerms"
            @click="submitPermissions"
          >
            确认分配
          </button>
        </div>
      </div>
    </Modal>
  </div>
    <!-- 知识库设计规范与全流程指引 Modal -->
    <Teleport to="body">
      <div v-if="showHelp" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="showHelp = false">
        <div class="bg-white rounded-2xl shadow-2xl w-full max-w-5xl h-[85vh] flex flex-col overflow-hidden border border-gray-100 animate-fade-in-up">
          <!-- Header -->
          <div class="p-6 border-b border-gray-100 flex justify-between items-center bg-emerald-50/30">
            <div class="flex items-center gap-3">
               <div class="w-10 h-10 rounded-xl bg-emerald-600 text-white flex items-center justify-center shadow-md shadow-emerald-500/20" style="background-color: #059669; color: #ffffff;">
                  <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"/></svg>
               </div>
               <div>
                 <h2 class="text-xl font-bold text-gray-900">知识库设计规范与全流程指引</h2>
                 <p class="text-xs text-gray-500 font-medium mt-0.5">从基础设施连通、切分策略定义，到多模态解析、权限下发与多智能体挂载。</p>
               </div>
            </div>
            <div class="flex items-center gap-3">
              <button
                v-if="!showKnowledgeFlowGuide"
                type="button"
                @click="restoreKnowledgeFlowGuide"
                class="inline-flex items-center gap-1 text-xs font-medium text-emerald-700 bg-emerald-100/70 hover:bg-emerald-200 px-3 py-1.5 rounded-lg transition-colors cursor-pointer"
              >
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                <span>恢复顶部流程提示</span>
              </button>
              <button @click="showHelp = false" class="text-gray-400 hover:text-gray-600 transition-colors cursor-pointer">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
              </button>
            </div>
          </div>

          <!-- Tabs -->
          <div class="flex border-b border-gray-200 bg-white px-6">
             <button 
               v-for="tab in ['flow', 'chunking', 'retrieval']" 
               :key="tab"
               @click="activeHelpTab = tab as any"
               class="px-4 py-3 text-sm font-medium border-b-2 transition-colors cursor-pointer"
               :class="activeHelpTab === tab ? 'border-emerald-600 text-emerald-700' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'"
             >
               {{ tab === 'flow' ? '全流程指引 (Workflow)' :
                  tab === 'chunking' ? '切分模板选型 (Chunk Methods)' : '检索调优与评测 (Retrieval & Rerank)' }}
             </button>
          </div>

          <!-- Content -->
          <div class="flex-1 overflow-y-auto p-6 sm:p-8 bg-gray-50/50">
             <!-- Tab 1: Workflow Flow -->
             <div v-if="activeHelpTab === 'flow'" class="space-y-6 max-w-4xl mx-auto">
                <div class="bg-gradient-to-r from-emerald-50 to-teal-50 border-l-4 border-emerald-600 p-4 rounded-r-xl shadow-sm">
                   <h3 class="font-bold text-emerald-900 mb-1">知识库 5 步全生命周期构建体系</h3>
                   <p class="text-xs text-emerald-700 leading-relaxed">
                      以 RAGFlow 为核心引擎。前置需确保系统配置连通；解析后建议先在召回测试台验证 Top-K 命中率；通过角色管理分配访问权限后挂载至智能体。
                   </p>
                </div>

                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                   <!-- Step 1 -->
                   <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                      <div>
                         <div class="flex items-center gap-2 mb-2">
                            <span class="w-6 h-6 rounded-full bg-emerald-600 text-white flex items-center justify-center text-xs font-bold">1</span>
                            <h4 class="font-bold text-gray-900 text-sm">环境连通与库创建</h4>
                         </div>
                         <p class="text-xs text-gray-500 leading-relaxed">
                            部署 RAGFlow 并在「系统配置」填入 API 地址与密钥确保引擎连通就绪。选择通用、QA 问答对、表格等模板创建知识库或一键同步。
                         </p>
                      </div>
                      <div class="mt-4 pt-3 border-t border-gray-100 flex items-center justify-end gap-2">
                         <button
                            type="button"
                            @click="showHelp = false; router.push('/dashboard/system?tab=configs')"
                            class="text-xs text-gray-600 hover:text-gray-900 font-medium px-2 py-1 rounded bg-gray-100 hover:bg-gray-200 cursor-pointer"
                         >
                            系统配置 &rarr;
                         </button>
                         <button
                            type="button"
                            @click="showHelp = false; openCreate()"
                            class="text-xs text-emerald-600 hover:text-emerald-800 font-bold cursor-pointer"
                         >
                            新建知识库 &rarr;
                         </button>
                      </div>
                   </div>

                   <!-- Step 2 -->
                   <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                      <div>
                         <div class="flex items-center gap-2 mb-2">
                            <span class="w-6 h-6 rounded-full bg-blue-600 text-white flex items-center justify-center text-xs font-bold">2</span>
                            <h4 class="font-bold text-gray-900 text-sm">文档上传与解析</h4>
                         </div>
                         <p class="text-xs text-gray-500 leading-relaxed">
                            批量上传 PDF/Word/Excel/Markdown/图片等；自动化 OCR 版面分析并生成 Chunks 切片，支持逐条审校与修正。
                         </p>
                      </div>
                      <div class="mt-4 pt-3 border-t border-gray-100 flex justify-end text-xs text-gray-400">
                         左侧树选择知识库上传并启动解析
                      </div>
                   </div>

                   <!-- Step 3 -->
                   <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                      <div>
                         <div class="flex items-center gap-2 mb-2">
                            <span class="w-6 h-6 rounded-full bg-indigo-600 text-white flex items-center justify-center text-xs font-bold">3</span>
                            <h4 class="font-bold text-gray-900 text-sm">召回测试与调优</h4>
                         </div>
                         <p class="text-xs text-gray-500 leading-relaxed">
                            输入真实业务提问验证 Top-K 向量相似度、全文检索与 Rerank 命中精度；在指标监控中心查看检索频次与命中趋势。
                         </p>
                      </div>
                      <div class="mt-4 pt-3 border-t border-gray-100 flex items-center justify-end gap-2">
                         <button
                            type="button"
                            @click="showHelp = false; router.push('/dashboard/knowledge-metrics')"
                            class="text-xs text-gray-600 hover:text-gray-900 font-medium px-2 py-1 rounded bg-gray-100 hover:bg-gray-200 cursor-pointer"
                         >
                            指标监控 &rarr;
                         </button>
                         <button
                            type="button"
                            @click="showHelp = false; router.push('/dashboard/knowledge-retrieval-test')"
                            class="text-xs text-indigo-600 hover:text-indigo-800 font-bold cursor-pointer"
                         >
                            召回测试 &rarr;
                         </button>
                      </div>
                   </div>

                   <!-- Step 4 -->
                   <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                      <div>
                         <div class="flex items-center gap-2 mb-2">
                            <span class="w-6 h-6 rounded-full bg-purple-600 text-white flex items-center justify-center text-xs font-bold">4</span>
                            <h4 class="font-bold text-gray-900 text-sm">权限授权与角色分配</h4>
                         </div>
                         <p class="text-xs text-gray-500 leading-relaxed">
                            配置知识库公开/私有范围与协作者读写权限；在「角色管理」中授予对应角色访问权限，确保业务人员可正常检索。
                         </p>
                      </div>
                      <div class="mt-4 pt-3 border-t border-gray-100 flex justify-end">
                         <button
                            type="button"
                            @click="showHelp = false; router.push('/dashboard/roles')"
                            class="text-xs text-purple-600 hover:text-purple-800 font-medium cursor-pointer"
                         >
                            前往角色管理 &rarr;
                         </button>
                      </div>
                   </div>

                   <!-- Step 5 -->
                   <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between md:col-span-2">
                      <div>
                         <div class="flex items-center gap-2 mb-2">
                            <span class="w-6 h-6 rounded-full bg-amber-600 text-white flex items-center justify-center text-xs font-bold">5</span>
                            <h4 class="font-bold text-gray-900 text-sm">智能体挂载与知识问答</h4>
                         </div>
                         <p class="text-xs text-gray-500 leading-relaxed">
                            在「智能体中心」为目标智能体显式绑定该知识库（`dataset_ids`）；智能体在对话中自主调用 RAG 工具，生成带切片溯源的高质量回答。
                         </p>
                      </div>
                      <div class="mt-4 pt-3 border-t border-gray-100 flex justify-end">
                         <button
                            type="button"
                            @click="showHelp = false; router.push('/dashboard/agent-management')"
                            class="text-xs text-amber-600 hover:text-amber-800 font-medium cursor-pointer"
                         >
                            前往智能体中心挂载 &rarr;
                         </button>
                      </div>
                   </div>
                </div>
             </div>

             <!-- Tab 2: Chunking Methods -->
             <div v-else-if="activeHelpTab === 'chunking'" class="space-y-4 max-w-4xl mx-auto">
                <div class="bg-white p-6 rounded-2xl border border-gray-200 shadow-sm space-y-4 text-sm text-gray-600 leading-relaxed">
                   <h4 class="font-bold text-gray-900 text-base">RAGFlow 切分策略选型推荐</h4>
                   <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
                      <div class="p-3.5 bg-blue-50/60 rounded-xl border border-blue-100 space-y-1">
                         <span class="font-bold text-blue-900 text-sm">naive（通用分块，推荐）</span>
                         <p class="text-gray-600 leading-relaxed">适用于大部分文章、产品说明书、Wiki 文档与 Markdown。按段落标点自适应合并切分。</p>
                      </div>
                      <div class="p-3.5 bg-emerald-50/60 rounded-xl border border-emerald-100 space-y-1">
                         <span class="font-bold text-emerald-900 text-sm">qa（问答对模式）</span>
                         <p class="text-gray-600 leading-relaxed">适用于 FAQ 客服手册、常见问题解答。自动提取 Q&A 问答对并向量化 Question。</p>
                      </div>
                      <div class="p-3.5 bg-purple-50/60 rounded-xl border border-purple-100 space-y-1">
                         <span class="font-bold text-purple-900 text-sm">table（表格模式）</span>
                         <p class="text-gray-600 leading-relaxed">适用于 Excel、CSV、财务数据表。保留行列结构与表头，避免跨行错位。</p>
                      </div>
                      <div class="p-3.5 bg-amber-50/60 rounded-xl border border-amber-100 space-y-1">
                         <span class="font-bold text-amber-900 text-sm">laws / book（法律/长篇专著）</span>
                         <p class="text-gray-600 leading-relaxed">按法律条文、合同章节、书籍目录层级结构进行树状切分，召回定位精准。</p>
                      </div>
                   </div>
                </div>
             </div>

             <!-- Tab 3: Retrieval & Rerank -->
             <div v-else-if="activeHelpTab === 'retrieval'" class="space-y-4 max-w-4xl mx-auto">
                <div class="bg-white p-6 rounded-2xl border border-gray-200 shadow-sm space-y-4 text-sm text-gray-600 leading-relaxed">
                   <h4 class="font-bold text-gray-900 text-base">RAG 混合检索与重排（Rerank）调优最佳实践</h4>
                   <div class="space-y-3 text-xs">
                      <div class="p-3.5 bg-gray-50 rounded-xl border border-gray-100 space-y-1">
                         <span class="font-bold text-gray-800">1. 混合检索（Hybrid Search）</span>
                         <p class="text-gray-600">结合 Dense 稠密向量（语义相近）与 Sparse 稀疏关键词（精准专有名词、错误码、型号），兼顾语义与精确字面匹配。</p>
                      </div>
                      <div class="p-3.5 bg-gray-50 rounded-xl border border-gray-100 space-y-1">
                         <span class="font-bold text-gray-800">2. 重排模型（Reranker）</span>
                         <p class="text-gray-600">初筛 Top-20 后通过 Cross-Encoder 语义重排输出最终 Top-5 最强相关切片，显著压低大模型幻觉。</p>
                      </div>
                      <div class="p-3.5 bg-gray-50 rounded-xl border border-gray-100 space-y-1">
                         <span class="font-bold text-gray-800">3. 召回测试验证</span>
                         <p class="text-gray-600">在上线前务必通过「知识库召回测试」验证关键高频提问的匹配分数（建议相似度 > 0.65）。</p>
                      </div>
                   </div>
                </div>
             </div>
          </div>
        </div>
      </div>
    </Teleport>
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
