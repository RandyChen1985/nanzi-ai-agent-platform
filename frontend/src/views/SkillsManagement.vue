<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SkillFlowGuideBanner from '../components/skill/SkillFlowGuideBanner.vue'
import SkillPersonalMenu from '../components/skill/SkillPersonalMenu.vue'
import SkillShareDialog from '../components/skill/SkillShareDialog.vue'
import ReceivedSharesPanel from '../components/skill/ReceivedSharesPanel.vue'
import SentSharesPanel from '../components/skill/SentSharesPanel.vue'
import axios from '../utils/axios'
import { useToast } from '../composables/useToast'
import SkillFileTree from '../components/SkillFileTree.vue'
import ConfirmModal from '../components/ConfirmModal.vue'
import MarkdownIt from 'markdown-it'
import { copyToClipboard } from '../utils/clipboard'
import { useUser } from '../composables/useUser'
import InputModal from '../components/InputModal.vue'
import { isSkillEnabled } from '@/utils/skillEnabled'

/** 发布状态：与后端 skill_publication_service 的取值一一对应（新增状态必须同步这里）。 */
type PublicationStatus = 'UNPUBLISHED' | 'PENDING' | 'PUBLISHED' | 'REJECTED' | 'WITHDRAWN' | 'REVOKED'

/** 「我的技能」内的二级菜单：我的技能 / 收到的分享 / 我的分享。 */
type PersonalMenuKey = 'mine' | 'received' | 'sent'

interface Skill {
  id: string
  name: string
  description: string
  path: string
  scope?: 'global' | 'personal'
  modified_at?: number
  enabled?: string
  publication_id?: string | null
  publication_status?: PublicationStatus
  current_public_version?: number | null
  pending_version?: number | null
  last_review_comment?: string | null
  platform_skill_id?: string | null
  /** 该技能来自谁的分享（仅在「收到的分享」接受过之后出现） */
  shared_from?: {
    share_id: string
    source_skill_id: string
    source_user_id: number
    source_user_name?: string | null
    source_real_name?: string | null
    shared_at?: string | null
  } | null
  /** 分享给过多少人（待接受 + 已接受） */
  share_count?: number
}

interface PublicationReviewItem {
  publication_id: string
  version_id: string
  name: string
  description: string
  skill_id: string
  platform_skill_id?: string | null
  version_number?: number | null
  version_status?: string
  publication_status?: string
  submitted_by?: number
  submitted_at?: string
  content_sha256?: string
  file_count?: number
  total_size?: number
  skill_md_content?: string
  file_tree?: FileNode[]
}

/**
 * 已发布（PUBLISHED）的发布记录，用于「已发布 / 下架管理」。
 *
 * 与审核队列的关键区别：审核队列是**待审版本**，这里是**已上线版本**；一条已发布记录
 * 对应技能库里真实对全员可见的那份快照，下架即回收它的可见性。
 */
interface PublishedPublication {
  publication_id: string
  version_id: string
  skill_id: string
  platform_skill_id?: string | null
  name: string
  description?: string
  publication_status?: string
  version_number?: number | null
  source_user_id?: number | null
  submitted_by?: number | null
  published_at?: string | null
  reviewed_by?: number | null
  file_count?: number
  total_size?: number
}

/** 技能工作台的视图分区：global=平台技能、personal=我的技能、review=待审核、publications=已发布（下架管理）。 */
type WorkspaceTab = 'global' | 'personal' | 'review' | 'publications'

interface BoundAgent {
  id: string
  name: string
  version_status: 'DRAFT' | 'PUBLISHED' | string
  version_number: number
}

interface SkillBindingEntry {
  count: number
  agents: BoundAgent[]
}

interface FileNode {
  name: string
  path: string
  is_dir: boolean
  size?: number
  modified_at?: number
  children?: FileNode[]
}

const props = withDefaults(defineProps<{
  /** 仅展示「我的技能」，用于个人中心（无需平台技能管理权限） */
  personalOnly?: boolean
  /** 打开时定位到指定个人技能详情 */
  initialSkillId?: string
  /** 「我的技能」内的落点：mine / received / sent（深链与通知共用） */
  initialPersonalMenu?: PersonalMenuKey
}>(), {
  personalOnly: false,
  initialSkillId: '',
  initialPersonalMenu: 'mine',
})

const emit = defineEmits<{
  (e: 'drawer-closed'): void
}>()

const { showToast } = useToast()
const router = useRouter()
const SKILL_FLOW_GUIDE_KEY = 'nanzi_skill_flow_guide_dismissed'
const showSkillFlowGuide = ref(localStorage.getItem(SKILL_FLOW_GUIDE_KEY) !== 'true')

const handleCloseSkillFlowGuide = () => {
  showSkillFlowGuide.value = false
}

const handleDismissSkillFlowGuide = () => {
  showSkillFlowGuide.value = false
  localStorage.setItem(SKILL_FLOW_GUIDE_KEY, 'true')
}

const restoreSkillFlowGuide = () => {
  localStorage.removeItem(SKILL_FLOW_GUIDE_KEY)
  showSkillFlowGuide.value = true
}

const activeHelpTab = ref<'flow' | 'schema' | 'audit'>('flow')

const { hasPermission } = useUser()
const canManagePlatformSkills = computed(() => hasPermission('element:skills:admin'))

/**
 * 是否可以使用「平台技能 / 待审核 / 已发布」这三个平台管理视图。
 *
 * 个人技能页（`personalOnly`，个人中心与嵌入式个人资源面板）下 tab bar 整体是
 * `v-if="!personalOnly"`，平台侧视图只靠 `canManagePlatformSkills` 渲染；越界切过去
 * 的结果是技能列表（`showSkillList`）与目标面板一起消失，页面看起来「坏了」。
 * 因此所有切到平台视图的入口都必须先过这道闸。
 */
const canAccessPlatformWorkspace = computed(() => !props.personalOnly && canManagePlatformSkills.value)

const handleSkillBannerAction = (type: 'create' | 'review' | 'global') => {
  if (type === 'create') {
    openCreateModal()
    return
  }
  // 守卫：个人技能页 / 无平台技能管理权限时不得切到不可见的平台视图，给出可见反馈而非静默失效
  if (!canAccessPlatformWorkspace.value) {
    showToast('「待审核 / 平台技能」仅平台技能管理员可用，个人技能页不提供该入口', 'warning')
    return
  }
  if (type === 'review') {
    activeWorkspaceTab.value = 'review'
  } else if (type === 'global') {
    activeWorkspaceTab.value = 'global'
    activeScope.value = 'global'
  }
}

const confirmState = ref({
  show: false,
  title: '',
  message: '',
  type: 'danger' as 'danger' | 'primary' | 'warning',
  onConfirm: () => {}
})

const skills = ref<Skill[]>([])
const personalSkills = ref<Skill[]>([])

// 「我的技能」内的二级菜单与分享状态
const normalizePersonalMenu = (value?: string): PersonalMenuKey =>
  value === 'received' || value === 'sent' ? value : 'mine'
const route = useRoute()
/** 二级菜单落点：query（深链/通知）优先，其次才是宿主传入的 prop。 */
const resolvePersonalMenu = (): PersonalMenuKey =>
  normalizePersonalMenu(String(route.query.skills_tab || '') || props.initialPersonalMenu)
const personalMenu = ref<PersonalMenuKey>(resolvePersonalMenu())
const shareDialogSkill = ref<Skill | null>(null)
const shareMenuCounts = ref({ received_pending: 0, sent_pending: 0, sent_total: 0 })

/** 首屏落点：只有能用平台管理视图的用户才落到「平台技能」，否则一律落到「我的技能」。 */
const resolveInitialWorkspaceTab = (): 'global' | 'personal' =>
  canAccessPlatformWorkspace.value ? 'global' : 'personal'
const initialWorkspaceTab = resolveInitialWorkspaceTab()
const activeScope = ref<'global' | 'personal'>(initialWorkspaceTab)
const activeWorkspaceTab = ref<WorkspaceTab>(initialWorkspaceTab)
const loading = ref(false)

/** 技能列表只在「平台技能 / 我的技能」两个视图渲染；待审核与已发布是并列的独立面板。 */
const showSkillList = computed(() => activeWorkspaceTab.value === 'global' || activeWorkspaceTab.value === 'personal')

/**
 * 兜底：权限或 `personalOnly` 变化后，若当前停在已不可见的平台视图上，立即退回「我的技能」。
 *
 * `hasPermission` 依赖用户信息异步注入，`canManagePlatformSkills` 可能在挂载后才确定；
 * 少了这道兜底，用户会停在一个既不渲染列表、也不渲染目标面板的空页面上。
 */
watch(canAccessPlatformWorkspace, (available) => {
  if (available) return
  if (activeWorkspaceTab.value !== 'personal') {
    activeWorkspaceTab.value = 'personal'
  }
  if (activeScope.value !== 'personal') {
    activeScope.value = 'personal'
  }
})

/** 列表类请求的并发计数：并发加载时避免先完成的一方提前结束 loading */
let listLoadingCount = 0
const beginListLoading = () => {
  listLoadingCount += 1
  loading.value = true
}
const endListLoading = () => {
  listLoadingCount = Math.max(0, listLoadingCount - 1)
  if (listLoadingCount === 0) loading.value = false
}
const searchQuery = ref('')
const viewMode = ref<'card' | 'list'>('card')
const skillSortBy = ref<'name' | 'time'>('time')
const skillSortOrder = ref<'asc' | 'desc'>('desc')
const fileTreeSortBy = ref<'name' | 'time'>('name')
const fileTreeSortOrder = ref<'asc' | 'desc'>('asc')
const showCreateMenu = ref(false)

// 新建技能相关
const showCreateModal = ref(false)
const newSkill = ref({
  id: '',
  name: '',
  description: ''
})
const creating = ref(false)

// 公共技能显式绑定智能体
const skillBindings = ref<Record<string, SkillBindingEntry>>({})
const showBindingsModal = ref(false)
const bindingsModalSkill = ref<Skill | null>(null)

const getBindingEntry = (skillId: string): SkillBindingEntry =>
  skillBindings.value[skillId] || { count: 0, agents: [] }

const openBindingsModal = (skill: Skill, e?: Event) => {
  e?.stopPropagation()
  if (skill.scope === 'personal') return
  bindingsModalSkill.value = skill
  showBindingsModal.value = true
}

const closeBindingsModal = () => {
  showBindingsModal.value = false
  bindingsModalSkill.value = null
}

const fetchSkillBindings = async () => {
  if (props.personalOnly) return
  try {
    const res = await axios.get('/api/portal/skills/bindings')
    if (res.data?.status === 'success') {
      skillBindings.value = res.data.bindings || {}
    }
  } catch (e) {
    console.warn('Failed to load skill bindings', e)
  }
}

// 统计相关
import * as echarts from 'echarts'
const showStatsModal = ref(false)
const activeTab = ref('distribution')
const loadingStats = ref(false)
const statsData = ref<any>(null)
const distributionChartRef = ref<HTMLElement | null>(null)
const trendChartRef = ref<HTMLElement | null>(null)
let distChartInstance: echarts.ECharts | null = null
let trendChartInstance: echarts.ECharts | null = null

const hasStatsData = computed(() => {
  if (!statsData.value) return false
  return Object.keys(statsData.value.total || {}).length > 0
})

// 抽屉内：当前技能近 30 天调用趋势
const skillDrawerChartRef = ref<HTMLElement | null>(null)
const loadingSkillDrawerStats = ref(false)
let skillDrawerChartInstance: echarts.ECharts | null = null

const activeSkillTrend = computed(() => {
  const trend = statsData.value?.trend || {}
  const skillId = activeSkillId.value
  const dates = Object.keys(trend).sort()
  return {
    dates,
    values: dates.map((d) => Number(trend[d]?.[skillId] || 0)),
  }
})

const activeSkill30DayTotal = computed(() => {
  return activeSkillTrend.value.values.reduce((sum, n) => sum + n, 0)
})

const activeSkillAllTimeTotal = computed(() => {
  const skillId = activeSkillId.value
  return Number(statsData.value?.total?.[skillId] || 0)
})

const disposeSkillDrawerChart = () => {
  if (skillDrawerChartInstance) {
    skillDrawerChartInstance.dispose()
    skillDrawerChartInstance = null
  }
}

const renderSkillDrawerChart = () => {
  if (!skillDrawerChartRef.value) return
  const { dates, values } = activeSkillTrend.value
  if (!skillDrawerChartInstance) {
    skillDrawerChartInstance = echarts.init(skillDrawerChartRef.value)
  }
  skillDrawerChartInstance.setOption({
    grid: { left: 8, right: 8, top: 12, bottom: 20, containLabel: true },
    tooltip: {
      trigger: 'axis',
      formatter: (params: any) => {
        const p = Array.isArray(params) ? params[0] : params
        return `${p?.axisValueLabel || ''}<br/>调用 ${p?.value ?? 0} 次`
      },
    },
    xAxis: {
      type: 'category',
      data: dates.map((d) => d.slice(5)),
      boundaryGap: false,
      axisLabel: {
        color: '#94a3b8',
        fontSize: 9,
        interval: Math.max(0, Math.floor(dates.length / 6) - 1),
      },
      axisLine: { lineStyle: { color: '#e2e8f0' } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      splitLine: { lineStyle: { color: '#f1f5f9' } },
      axisLabel: { color: '#94a3b8', fontSize: 9 },
    },
    series: [
      {
        type: 'line',
        smooth: true,
        symbol: 'circle',
        symbolSize: 4,
        showSymbol: false,
        data: values,
        lineStyle: { width: 2, color: '#3b82f6' },
        itemStyle: { color: '#3b82f6' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(59, 130, 246, 0.28)' },
            { offset: 1, color: 'rgba(59, 130, 246, 0.02)' },
          ]),
        },
      },
    ],
  })
}

const loadSkillDrawerStats = async () => {
  loadingSkillDrawerStats.value = true
  try {
    const res = await axios.get('/api/portal/skills/stats')
    statsData.value = res.data
  } catch (e) {
    console.warn('Failed to load skill drawer stats', e)
  } finally {
    loadingSkillDrawerStats.value = false
    await nextTick()
    renderSkillDrawerChart()
  }
}

// 帮助弹窗相关
const showHelpModal = ref(false)
const commandCopied = ref(false)

// 详情抽屉 (Drawer) 相关
const showDrawer = ref(false)
const activeSkillId = ref('')
const activeSkillScope = ref<'global' | 'personal'>('global')
const activeSkillName = ref('')
const activeSkillDesc = ref('')
const fileTree = ref<FileNode[]>([])
const selectedFilePath = ref('SKILL.md') // 当前选中的文件相对路径，默认 SKILL.md
const editingContent = ref('')
const originalContent = ref('')
const saving = ref(false)
const fetchingDetail = ref(false)

/**
 * 详情/文件读取令牌：技能切换或文件切换都会让在途请求失效。
 * 缺少它时，慢响应会覆盖当前正在查看的内容，进而造成「显示 A 的内容却保存到 B」。
 */
let detailToken = 0
const invalidateDetailRequests = () => ++detailToken

watch(showDrawer, (open) => {
  if (!open) {
    disposeSkillDrawerChart()
    if (props.personalOnly) {
      emit('drawer-closed')
    }
  }
})

// 新建技能资产相关
const selectedDirectoryPath = ref('')
const showCreateAssetModal = ref(false)
const createAssetType = ref<'file' | 'folder'>('file')
const createAssetName = ref('')
const creatingAsset = ref(false)

const createAssetTargetLabel = computed(() => {
  return selectedDirectoryPath.value || '技能根目录'
})

// 拖拽上传相关
const uploadFolder = ref('') // 上传到技能目录下的子文件夹路径 (可选)
const uploading = ref(false)
const uploadType = ref<'normal' | 'archive'>('normal')

// 右键菜单相关
const contextMenu = ref({
  show: false,
  x: 0,
  y: 0,
  node: null as any
})

// 导入技能相关
const showImportModal = ref(false)
const importOverwrite = ref(false)
const importingSkill = ref(false)
const importFile = ref<File | null>(null)
const importFileInput = ref<HTMLInputElement | null>(null)
const importDragActive = ref(false)

// 个人技能发布与管理员审核
const publicationQueue = ref<PublicationReviewItem[]>([])
const activePublicationReview = ref<PublicationReviewItem | null>(null)
const publicationReviewLoading = ref(false)
const publicationReviewAction = ref(false)
const publicationReviewComment = ref('')
// 审核面板的包内文件浏览：SKILL.md 只是技能包的一部分，scripts/ 等可执行文件同样会
// 随「通过并发布」进入公共技能库，必须让审核者能逐个打开看，而不是只展示 SKILL.md。
const publicationReviewFilePath = ref('SKILL.md')
const publicationReviewFileContent = ref('')
const publicationReviewFileLoading = ref(false)

// 已发布（上线中）的发布记录 + 下架管理
const publications = ref<PublishedPublication[]>([])
const publicationsLoading = ref(false)
/** 正在等待填写下架原因的记录：非空即表示「下架原因」弹窗打开。 */
const revokeTarget = ref<PublishedPublication | null>(null)
const revokeComment = ref('')
const revokeSubmitting = ref(false)
//: 下架原因长度上限，与弹窗 maxlength 保持一致
const REVOKE_COMMENT_MAX = 200

// 获取平台技能列表
const fetchSkills = async () => {
  beginListLoading()
  try {
    const response = await axios.get('/api/portal/skills')
    if (response.data && response.data.status === 'success') {
      skills.value = (response.data.data || []).map((s: Skill) => ({ ...s, scope: 'global' as const }))
    }
  } catch (e: any) {
    showToast(e.response?.data?.detail || '获取平台技能列表失败', 'error')
  } finally {
    endListLoading()
  }
}

// 获取个人技能列表
const fetchPersonalSkills = async () => {
  // 个人页首屏此前不参与 loading，会先闪一次「还没有个人技能」空状态
  beginListLoading()
  try {
    const response = await axios.get('/api/portal/skills/personal')
    if (response.data && response.data.status === 'success') {
      personalSkills.value = (response.data.data || []).map((s: Skill) => ({ ...s, scope: 'personal' as const }))
      await Promise.all(personalSkills.value.map(async (skill) => {
        try {
          const statusResponse = await axios.get(`/api/portal/skills/personal/${skill.id}/publication-status`)
          Object.assign(skill, statusResponse.data?.data || {})
        } catch (statusError) {
          console.warn(`Failed to load publication status for ${skill.id}`, statusError)
        }
      }))
    }
  } catch (e: any) {
    console.warn('获取个人技能失败', e)
    showToast(e.response?.data?.detail || '获取个人技能列表失败', 'error')
  } finally {
    endListLoading()
  }
}

// 分享：菜单计数、弹窗与跳转
const fetchShareMenuCounts = async () => {
  try {
    const response = await axios.get('/api/portal/skills/personal/shares/summary')
    const data = response.data?.data || {}
    shareMenuCounts.value = {
      received_pending: Number(data.received_pending || 0),
      sent_pending: Number(data.sent_pending || 0),
      sent_total: Number(data.sent_total || 0),
    }
  } catch (error) {
    console.warn('获取技能分享计数失败', error)
  }
}

/** 卡片「更多」操作菜单：fixed 定位，避免被卡片/网格裁切 */
const actionMenu = ref<{ skill: Skill; x: number; top: number | null; bottom: number | null } | null>(null)

const openActionMenu = (event: MouseEvent, skill: Skill) => {
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  const menuWidth = 176
  const x = Math.max(8, Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 8))
  // 下方放不下时向上展开；用 bottom 定位，菜单底部正好贴住按钮顶部
  const openUp = window.innerHeight - rect.bottom < 220
  actionMenu.value = {
    skill,
    x,
    top: openUp ? null : rect.bottom + 6,
    bottom: openUp ? window.innerHeight - rect.top + 6 : null,
  }
}

const closeActionMenu = () => {
  actionMenu.value = null
}

const runSkillAction = (action: 'share' | 'export' | 'rename' | 'delete') => {
  const skill = actionMenu.value?.skill
  closeActionMenu()
  if (!skill) return
  const scope = skill.scope === 'personal' ? 'personal' : 'global'
  if (action === 'share') openShareDialog(skill)
  else if (action === 'export') exportSkill(skill.id, scope)
  else if (action === 'rename') renameSkill(skill.id, scope)
  else deleteSkill(skill.id)
}

const openShareDialog = (skill: Skill) => {
  shareDialogSkill.value = skill
}

const handleShareChanged = async () => {
  await Promise.all([fetchShareMenuCounts(), fetchPersonalSkills()])
}

const openSharedSkill = async (skillId: string) => {
  personalMenu.value = 'mine'
  if (!personalSkills.value.some((skill) => skill.id === skillId)) {
    // 刚接受完分享时列表可能还在刷新，这里兜底重拉一次，避免「点了打不开」
    await fetchPersonalSkills()
  }
  await nextTick()
  if (personalSkills.value.some((skill) => skill.id === skillId)) {
    openSkillDetail(skillId)
  } else {
    showToast('该技能已不存在或已删除', 'error')
  }
}

const submitSkillPublication = async (skill: Skill) => {
  const isResubmission = skill.publication_status === 'PUBLISHED' || skill.publication_status === 'REJECTED'
  try {
    const response = await axios.post(`/api/portal/skills/personal/${skill.id}/publication-requests`)
    if (response.data?.status === 'success') {
      Object.assign(skill, response.data.data || {})
      skill.publication_status = response.data.data?.publication_status || 'PENDING'
      showToast(isResubmission ? '新版本已提交管理员审核' : '已提交管理员审核', 'success')
    }
  } catch (e: any) {
    showToast(e.response?.data?.detail || '提交平台技能审核失败', 'error')
  }
}

const withdrawSkillPublication = (skill: Skill) => {
  confirmState.value = {
    show: true,
    title: '撤销技能审核',
    message: `确定撤销个人技能 [${skill.id}] 的平台发布审核吗？个人源文件和已发布的平台版本不会受到影响。`,
    type: 'warning',
    onConfirm: async () => {
      try {
        const response = await axios.post(`/api/portal/skills/personal/${skill.id}/publication-requests/withdraw`)
        if (response.data?.status === 'success') {
          Object.assign(skill, response.data.data || {})
          showToast('已撤销技能发布审核', 'success')
        }
      } catch (e: any) {
        showToast(e.response?.data?.detail || '撤销技能发布审核失败', 'error')
      } finally {
        confirmState.value.show = false
      }
    },
  }
}

const fetchPublicationQueue = async (silent = false) => {
  publicationReviewLoading.value = true
  try {
    const response = await axios.get('/api/portal/skills/publication-requests')
    publicationQueue.value = response.data?.data || []
  } catch (e: any) {
    if (!silent) {
      showToast(e.response?.data?.detail || '获取技能发布审核队列失败', 'error')
    }
  } finally {
    publicationReviewLoading.value = false
  }
}

const openPublicationReview = async () => {
  if (!canAccessPlatformWorkspace.value) return
  activeWorkspaceTab.value = 'review'
  activeScope.value = 'global'
  await fetchPublicationQueue()
}

const closePublicationReview = () => {
  activeWorkspaceTab.value = 'global'
  activeScope.value = 'global'
}

// ---------------------------------------------------------------------------
// 已发布 / 下架管理
// ---------------------------------------------------------------------------

/**
 * 拉取「已发布」列表：一条记录就是一个已上线的版本，可被下架。
 *
 * `silent` 供首屏预取使用（tab 上的数量角标）：与审核队列不同，这里的失败不该在用户
 * 还没进入该分区时就弹错。
 */
const fetchPublications = async (silent = false) => {
  publicationsLoading.value = true
  try {
    const response = await axios.get('/api/portal/skills/publications')
    if (response.data?.status === 'success') {
      publications.value = response.data.data?.items || []
    } else if (!silent) {
      showToast(response.data?.message || '获取已发布技能列表失败', 'error')
    }
  } catch (e: any) {
    if (!silent) {
      showToast(e.response?.data?.detail || '获取已发布技能列表失败', 'error')
    }
  } finally {
    publicationsLoading.value = false
  }
}

/** 打开「已发布 / 下架管理」分区（仅平台技能管理员，个人技能页不可达）。 */
const openPublications = async () => {
  if (!canAccessPlatformWorkspace.value) {
    showToast('「已发布 / 下架管理」仅平台技能管理员可用', 'warning')
    return
  }
  activeWorkspaceTab.value = 'publications'
  activeScope.value = 'global'
  await fetchPublications()
}

const closePublications = () => {
  activeWorkspaceTab.value = 'global'
  activeScope.value = 'global'
}

/**
 * 第一步：确认弹窗。下架会立即收回该版本对全体用户的可见性，属于不可轻率的线上操作，
 * 因此先让用户确认影响面，再要求填写原因（原因会写入发布记录，供事后追溯）。
 */
const startRevokePublication = (item: PublishedPublication) => {
  confirmState.value = {
    show: true,
    title: '下架已发布技能',
    message: `确定下架「${item.name || item.skill_id}」的已发布版本 v${item.version_number ?? '—'} 吗？\n下架后该版本立即不再对用户可见；个人源技能与历史发布记录会保留，可重新提审发布。`,
    type: 'danger',
    onConfirm: () => {
      confirmState.value.show = false
      revokeTarget.value = item
      revokeComment.value = ''
    },
  }
}

const cancelRevokePublication = () => {
  if (revokeSubmitting.value) return
  revokeTarget.value = null
  revokeComment.value = ''
}

/**
 * 第二步：强制填写下架原因后再提交。
 *
 * 原因必填（后端 422 也会校验），空原因就地给出可见提示并**保持弹窗打开**，避免
 * 用户以为已经提交成功。
 */
const submitRevokePublication = async (commentFromModal?: string) => {
  const target = revokeTarget.value
  if (!target || revokeSubmitting.value) return
  // 以 confirm 载荷为准：InputModal 的输入值既经 @update:modelValue 同步到 revokeComment，
  // 也随 confirm 事件回传。只读本地 ref 曾经让这个流程 100% 走不进请求（永远是空原因）。
  const comment = (commentFromModal ?? revokeComment.value).trim()
  revokeComment.value = comment
  if (!comment) {
    showToast('请填写下架原因（必填）', 'warning')
    return
  }
  revokeSubmitting.value = true
  try {
    const response = await axios.post(`/api/portal/skills/publications/${target.version_id}/revoke`, { comment })
    if (response.data?.status !== 'success') {
      showToast(response.data?.message || '下架技能失败', 'error')
      return
    }
    // 下架会让公共技能目录真的消失，因此后端会同步清理智能体白名单与会话挂载引用，
    // 并把清理不了的项放进 warnings——与重命名/删除同一风格，必须如实展示。
    const { syncedCount, warnings } = summarizeReferenceSync(response.data?.data)
    showToast(
      warnings.length
        ? `已下架「${target.name || target.skill_id}」v${target.version_number ?? '—'}：已同步 ${syncedCount} 处引用；有 ${warnings.length} 条警告——${warnings.join('；')}`
        : `已下架「${target.name || target.skill_id}」v${target.version_number ?? '—'}：已同步 ${syncedCount} 处引用`,
      warnings.length ? 'error' : 'success',
    )
    revokeTarget.value = null
    revokeComment.value = ''
    // 下架后平台技能清单也会随之变化（版本下线），一并刷新避免「平台技能」页残留旧数据
    await Promise.all([fetchPublications(), fetchSkills()])
  } catch (e: any) {
    // 422（原因为空）/ 404（记录不存在）/ 409（已下架或不是当前版本）都要让用户看到后端原因
    showToast(e.response?.data?.detail || '下架技能失败', 'error')
  } finally {
    revokeSubmitting.value = false
  }
}

const openPublicationReviewDetail = async (versionId: string) => {
  try {
    const response = await axios.get(`/api/portal/skills/publication-requests/${versionId}`)
    activePublicationReview.value = response.data?.data || null
    publicationReviewComment.value = ''
    // 详情接口已下发 SKILL.md 正文，直接用作默认预览，避免多一次请求
    publicationReviewFilePath.value = 'SKILL.md'
    publicationReviewFileContent.value = activePublicationReview.value?.skill_md_content || ''
  } catch (e: any) {
    showToast(e.response?.data?.detail || '获取审核详情失败', 'error')
  }
}

/**
 * 读取审核快照里的指定文件（只读）。
 *
 * 走 `/publication-requests/{id}/file`，服务端从**提交快照**读取并做 realpath 越界校验，
 * 因此审核者看到的与即将发布的内容必然一致（不会因为源技能随后被改动而错审）。
 */
const openPublicationReviewFile = async (filePath: string) => {
  const review = activePublicationReview.value
  if (!review || !filePath) return
  publicationReviewFilePath.value = filePath
  // SKILL.md 用详情接口已带回的正文，省一次往返
  if (filePath === 'SKILL.md') {
    publicationReviewFileContent.value = review.skill_md_content || ''
    return
  }
  publicationReviewFileLoading.value = true
  try {
    const response = await axios.get(
      `/api/portal/skills/publication-requests/${review.version_id}/file`,
      { params: { path: filePath } },
    )
    if (activePublicationReview.value?.version_id !== review.version_id) return
    publicationReviewFileContent.value = response.data?.content || ''
  } catch (e: any) {
    publicationReviewFileContent.value = ''
    showToast(e.response?.data?.detail || `读取 ${filePath} 失败`, 'error')
  } finally {
    publicationReviewFileLoading.value = false
  }
}

const approvePublicationReview = async () => {
  if (!activePublicationReview.value) return
  publicationReviewAction.value = true
  try {
    const response = await axios.post(`/api/portal/skills/publication-requests/${activePublicationReview.value.version_id}/approve`)
    if (response.data?.status !== 'success') {
      showToast(response.data?.message || '发布平台技能失败', 'error')
      return
    }
    showToast('技能已审核通过并发布到平台技能库', 'success')
    activePublicationReview.value = null
    // 通过后平台技能目录与「已发布」清单都会变化，两个列表都要刷新，否则角标与表格残留旧数据
    await Promise.all([fetchPublicationQueue(), fetchSkills(), fetchPersonalSkills(), fetchPublications()])
  } catch (e: any) {
    showToast(e.response?.data?.detail || '发布平台技能失败', 'error')
  } finally {
    publicationReviewAction.value = false
  }
}

const rejectPublicationReview = async () => {
  if (!activePublicationReview.value || !publicationReviewComment.value.trim()) {
    showToast('请填写驳回原因', 'warning')
    return
  }
  publicationReviewAction.value = true
  try {
    await axios.post(
      `/api/portal/skills/publication-requests/${activePublicationReview.value.version_id}/reject`,
      { comment: publicationReviewComment.value.trim() },
    )
    showToast('已驳回该技能发布申请', 'success')
    activePublicationReview.value = null
    await Promise.all([fetchPublicationQueue(), fetchPersonalSkills()])
  } catch (e: any) {
    showToast(e.response?.data?.detail || '驳回技能发布申请失败', 'error')
  } finally {
    publicationReviewAction.value = false
  }
}

// 搜索过滤
const currentScopeSkills = computed(() => activeScope.value === 'global' ? skills.value : personalSkills.value)

const resolveSkillApiPrefix = (skillId?: string, scope?: 'global' | 'personal') => {
  if (scope === 'personal') return '/api/portal/skills/personal'
  if (scope === 'global') return '/api/portal/skills'
  const id = skillId || activeSkillId.value
  if (id && id === activeSkillId.value) {
    return activeSkillScope.value === 'personal'
      ? '/api/portal/skills/personal'
      : '/api/portal/skills'
  }
  const matched = [...skills.value, ...personalSkills.value].find((s) => s.id === id)
  if (matched?.scope === 'personal') return '/api/portal/skills/personal'
  return activeScope.value === 'personal' ? '/api/portal/skills/personal' : '/api/portal/skills'
}

const importSkillApiUrl = computed(() =>
  activeScope.value === 'personal' ? '/api/portal/skills/personal/import' : '/api/portal/skills/import'
)

const filteredSkills = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  if (!query) return currentScopeSkills.value
  return currentScopeSkills.value.filter(s => 
    s.id.toLowerCase().includes(query) ||
    s.name.toLowerCase().includes(query) ||
    s.description.toLowerCase().includes(query)
  )
})

const getSkillStatusRank = (skill: Skill) => !isSkillEnabled(skill.enabled) ? 1 : 0

const sortedSkills = computed(() => {
  const list = [...filteredSkills.value]
  const dir = skillSortOrder.value === 'asc' ? 1 : -1
  list.sort((a, b) => {
    const statusOrder = getSkillStatusRank(a) - getSkillStatusRank(b)
    if (statusOrder !== 0) return statusOrder

    if (skillSortBy.value === 'time') {
      return ((a.modified_at || 0) - (b.modified_at || 0)) * dir
    }
    const aKey = (a.name || a.id).toLowerCase()
    const bKey = (b.name || b.id).toLowerCase()
    return aKey.localeCompare(bKey, 'zh-CN') * dir
  })
  return list
})

const toggleSkillSortOrder = () => {
  skillSortOrder.value = skillSortOrder.value === 'asc' ? 'desc' : 'asc'
}

const toggleFileTreeSortOrder = () => {
  fileTreeSortOrder.value = fileTreeSortOrder.value === 'asc' ? 'desc' : 'asc'
}

/**
 * 发布状态徽章：所有取值都必须有文案与配色。
 *
 * 此前只用「PUBLISHED / PENDING / REJECTED / WITHDRAWN」四元三元表达式，落到 else 的是
 * 空字符串——新增 REVOKED（下架）后，作者看到的是一个**没有文字的琥珀色圆点**，看起来
 * 像"审核中"，完全不知道技能已被下架。这里改成显式映射 + 兜底，新增状态时不会再出现
 * 「有徽章没文字」。
 */
const publicationBadgeLabel = (skill: Skill): string => {
  const status = skill.publication_status
  if (status === 'PUBLISHED') {
    // 已发布 + 又提交了一版待审：线上仍是旧版，必须让作者看出来，否则他会以为自己刚提交
    // 的内容已经生效（后端此时保持 publication_status=PUBLISHED、pending_version 有值）。
    const online = `平台版 v${skill.current_public_version || 1}`
    return skill.pending_version ? `${online} · 新版审核中` : online
  }
  if (status === 'PENDING') return '审核中'
  if (status === 'REJECTED') return '已驳回'
  if (status === 'WITHDRAWN') return '已撤销'
  if (status === 'REVOKED') return '已下架'
  return status ? String(status) : ''
}

const publicationBadgeClass = (status?: PublicationStatus): string => {
  if (status === 'PUBLISHED') return 'bg-blue-100 text-blue-700'
  if (status === 'REJECTED') return 'bg-red-100 text-red-700'
  if (status === 'WITHDRAWN') return 'bg-gray-100 text-gray-600'
  if (status === 'REVOKED') return 'bg-orange-100 text-orange-700'
  return 'bg-amber-100 text-amber-700'
}

const formatModifiedAt = (ts?: number) => {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** 已发布记录的发布时间：后端下发 ISO 字符串（不是秒级时间戳，不能复用 formatModifiedAt）。 */
const formatPublishedAt = (value?: string | null) => {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const isSearchEmpty = computed(() => {
  return searchQuery.value.trim().length > 0 && sortedSkills.value.length === 0
})

const clearSearch = () => {
  searchQuery.value = ''
}

// 复制 CLI 命令
const copyCommand = async () => {
  const cmd = 'npx skills add https://github.com/vercel-labs/skills --skill find-skills'
  const ok = await copyToClipboard(cmd)
  if (ok) {
    commandCopied.value = true
    showToast('命令已成功复制到剪贴板！', 'success')
    setTimeout(() => {
      commandCopied.value = false
    }, 2000)
  } else {
    showToast('复制失败，请手动选择复制', 'error')
  }
}

// 复制当前编辑器内容
const copyEditorContent = async () => {
  const text = editingContent.value
  if (!text) {
    showToast('当前文件内容为空', 'warning')
    return
  }
  const ok = await copyToClipboard(text)
  if (ok) {
    showToast('已复制到剪贴板', 'success')
  } else {
    showToast('复制失败，请手动选择复制', 'error')
  }
}

// 打开创建弹窗
const openCreateModal = () => {
  newSkill.value = { id: '', name: '', description: '' }
  showCreateModal.value = true
}

// 提交创建
const createSkill = async () => {
  if (!newSkill.value.id.trim()) {
    showToast('技能唯一识别ID不能为空', 'warning')
    return
  }
  if (!/^[a-zA-Z0-9_-]+$/.test(newSkill.value.id)) {
    showToast('技能ID仅支持英文、数字、中划线和下划线', 'warning')
    return
  }
  if (!newSkill.value.name.trim()) {
    showToast('技能名称不能为空', 'warning')
    return
  }

  creating.value = true
  try {
    const apiPrefix = activeScope.value === 'personal' ? '/api/portal/skills/personal' : '/api/portal/skills'
    const response = await axios.post(apiPrefix, {
      id: newSkill.value.id.trim(),
      name: newSkill.value.name.trim(),
      description: newSkill.value.description.trim()
    })
    if (response.data && response.data.status === 'success') {
      showToast(`${activeScope.value === 'personal' ? '个人' : ''}技能创建成功！`, 'success')
      showCreateModal.value = false
      if (activeScope.value === 'personal') {
        fetchPersonalSkills()
      } else {
        fetchSkills()
      }
    }
  } catch (e: any) {
    showToast(e.response?.data?.detail || '新建技能失败', 'error')
  } finally {
    creating.value = false
  }
}

// 物理删除整个技能（注销）
/**
 * 导出技能为 zip 下载。
 *
 * 走统一的 axios 请求（而不是 ``window.open``）：接口需要平台管理权限，直接开新窗口
 * 会丢掉请求头里的凭据，拿到的只会是一段 401 JSON。
 */
const exportSkill = async (skillId: string, scope?: 'global' | 'personal') => {
  const apiPrefix = resolveSkillApiPrefix(skillId, scope)
  try {
    const response = await axios.get(`${apiPrefix}/${skillId}/export`, { responseType: 'blob' })
    const objectUrl = window.URL.createObjectURL(new Blob([response.data]))
    const link = document.createElement('a')
    link.href = objectUrl
    link.download = `${skillId}.zip`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(objectUrl)
    showToast(`已开始下载 ${skillId}.zip`, 'success')
  } catch (e: any) {
    // responseType=blob 时错误体也是二进制，必须读出来才能给出可读提示
    let detail = '导出失败'
    try {
      const raw = await e.response?.data?.text?.()
      if (raw) detail = JSON.parse(raw)?.detail || detail
    } catch {
      /* 解析失败就退化为通用提示 */
    }
    showToast(detail, 'error')
  }
}

/**
 * 重命名技能。
 *
 * 技能 ID 就是目录名，同时被智能体技能白名单、发布谱系来源 ID、以及各会话已挂载的
 * 技能 chip 引用；后端会一并同步，并把未能同步的项放进 warnings——这里必须把 warnings
 * 展示出来，否则用户会以为一切正常。
 */
const renameDialog = ref<{ skillId: string; scope: 'global' | 'personal'; draft: string } | null>(null)
const renameSubmitting = ref(false)

/**
 * 把后端的引用同步报告压成「同步了几处 + 几条警告」。
 *
 * `synced` 是后端按引用类别给出的成功条数（重命名：agents / publications / conversations /
 * permissions；删除：解除绑定与挂载清理），`warnings` 是未能同步的具体说明。两者都必须让
 * 用户看到，否则「技能还在但哪都挂不上」这种断链会悄无声息。
 *
 * `agents` 这一类后端给的是**智能体 ID 列表**而不是条数：`Number(['a','b'])` 是 NaN，
 * 此前会被当成 0，于是"已同步 N 处引用"在真的同步了智能体白名单时反而少算（甚至显示 0）。
 */
const summarizeReferenceSync = (data: any): { syncedCount: number; warnings: string[] } => {
  const synced = data?.synced || {}
  const syncedCount = Object.values(synced).reduce<number>((sum, value) => {
    if (Array.isArray(value)) return sum + value.length
    return sum + (Number(value) || 0)
  }, 0)
  const warnings = Array.isArray(data?.warnings) ? (data?.warnings as string[]) : []
  return { syncedCount, warnings }
}

/** 打开重命名弹窗（用 InputModal 而非浏览器原生弹窗：原生弹窗阻塞主线程且无法跟主题一致）。 */
const renameSkill = (skillId: string, scope?: 'global' | 'personal') => {
  renameDialog.value = { skillId, scope: scope === 'personal' ? 'personal' : 'global', draft: skillId }
}

/** 提交重命名。ID 即目录名，同时被智能体白名单、发布谱系与会话挂载引用。 */
const submitRename = async (nextIdFromModal?: string) => {
  const target = renameDialog.value
  if (!target || renameSubmitting.value) return
  const newSkillId = (nextIdFromModal ?? target.draft).trim()
  // 空 ID 与非法 ID 都在本地就地拦下并给出可见提示（弹窗保持打开），
  // 否则用户点完「确定」什么都没发生，只能自己猜哪里错了。
  if (!newSkillId) {
    showToast('技能 ID 不能为空', 'warning')
    return
  }
  if (newSkillId === target.skillId) {
    showToast('技能 ID 未变化，请修改后再提交', 'warning')
    return
  }
  if (!/^[a-zA-Z0-9_-]+$/.test(newSkillId)) {
    showToast('技能 ID 仅支持英文、数字、中划线和下划线', 'warning')
    return
  }
  renameSubmitting.value = true
  try {
    const apiPrefix = resolveSkillApiPrefix(target.skillId, target.scope)
    const response = await axios.put(`${apiPrefix}/${target.skillId}/rename`, { new_skill_id: newSkillId })
    if (response.data?.status !== 'success') {
      // 信封不是 success 时不能再报「已重命名」，否则用户会以为改成功了
      showToast(response.data?.message || '重命名失败', 'error')
      return
    }
    const { syncedCount, warnings } = summarizeReferenceSync(response.data?.data)
    showToast(
      warnings.length
        ? `已重命名为 ${newSkillId}：已同步 ${syncedCount} 处引用；有 ${warnings.length} 条警告——${warnings.join('；')}`
        : `已重命名为 ${newSkillId}：已同步 ${syncedCount} 处引用`,
      warnings.length ? 'error' : 'success',
    )
    if (activeSkillId.value === target.skillId) {
      showDrawer.value = false
    }
    renameDialog.value = null
    await Promise.all([fetchSkills(), fetchPersonalSkills()])
  } catch (e: any) {
    showToast(e.response?.data?.detail || '重命名失败', 'error')
  } finally {
    renameSubmitting.value = false
  }
}

const deleteSkill = (skillId: string) => {
  const isPersonal = activeScope.value === 'personal'
  confirmState.value = {
    show: true,
    title: isPersonal ? '删除个人技能' : '彻底删除技能',
    message: `确定要彻底删除${isPersonal ? '个人' : ''}技能 [${skillId}] 吗？\n该操作会物理删除对应的技能目录及其所有子资产脚本，且不可恢复！`,
    type: 'danger',
    onConfirm: async () => {
      try {
        const apiPrefix = isPersonal ? '/api/portal/skills/personal' : '/api/portal/skills'
        const response = await axios.delete(`${apiPrefix}/${skillId}`)
        if (response.data && response.data.status === 'success') {
          // 与重命名同理：删除也要解除智能体绑定 / 会话挂载 / 发布血缘引用，
          // 后端把未清理成功的项放进 warnings，必须如实展示而不是只报「已删除」。
          const { syncedCount, warnings } = summarizeReferenceSync(response.data?.data)
          showToast(
            warnings.length
              ? `技能 ${skillId} 已删除：已同步 ${syncedCount} 处引用；有 ${warnings.length} 条警告——${warnings.join('；')}`
              : `技能 ${skillId} 已成功物理删除，已同步 ${syncedCount} 处引用`,
            warnings.length ? 'error' : 'success',
          )
          if (isPersonal) {
            fetchPersonalSkills()
          } else {
            fetchSkills()
          }
          if (activeSkillId.value === skillId) {
            showDrawer.value = false
          }
        } else {
          showToast(response.data?.message || '注销技能失败', 'error')
        }
      } catch (e: any) {
        showToast(e.response?.data?.detail || '注销技能失败', 'error')
      } finally {
        confirmState.value.show = false
      }
    }
  }
}

// 切换启用/禁用状态
const toggleSkillStatus = async (skill: Skill) => {
  const newStatus = !isSkillEnabled(skill.enabled) ? 'true' : 'false'
  try {
    const isPersonal = skill.scope === 'personal'
    const apiPrefix = isPersonal ? '/api/portal/skills/personal' : '/api/portal/skills'
    const response = await axios.put(`${apiPrefix}/${skill.id}/toggle?enabled=${newStatus === 'true'}`)
    if (response.data && response.data.status === 'success') {
      skill.enabled = newStatus
      showToast(`技能已${newStatus === 'true' ? '启用' : '禁用'}`, 'success')
    } else {
      // 信封不是 success 时此前完全静默：开关弹回去了，用户却不知道为什么
      showToast(response.data?.message || '更新技能状态失败', 'error')
    }
  } catch (e: any) {
    showToast(e.response?.data?.detail || '更新技能状态失败', 'error')
  }
}

// 查看技能详情与抽屉挂载
const openSkillDetail = async (skillId: string) => {
  activeSkillId.value = skillId
  selectedFilePath.value = 'SKILL.md'
  selectedDirectoryPath.value = ''
  editorMode.value = 'preview'
  showDrawer.value = true
  disposeSkillDrawerChart()
  await Promise.all([fetchSkillDetail(skillId), loadSkillDrawerStats()])
}

// 获取详情 (包含加载选中的文件内容)
const fetchSkillDetail = async (skillId: string, options: { preserveEditing?: boolean } = {}) => {
  const token = invalidateDetailRequests()
  fetchingDetail.value = true
  try {
    // 根据当前技能的 scope 决定 API 路径
    const activeSkill = currentScopeSkills.value.find(s => s.id === skillId)
    const isPersonal = activeSkill?.scope === 'personal' || activeScope.value === 'personal'
    activeSkillScope.value = isPersonal ? 'personal' : 'global'
    const apiPrefix = resolveSkillApiPrefix(skillId, activeSkillScope.value)
    const response = await axios.get(`${apiPrefix}/${skillId}`)
    if (token !== detailToken) return // 已切换到其它技能/文件，丢弃过期响应
    if (response.data && response.data.status === 'success') {
      const data = response.data.data
      activeSkillName.value = data.name
      activeSkillDesc.value = data.description
      fileTree.value = data.file_tree || []

      if (options.preserveEditing) {
        // 仅刷新文件树与元信息：新建资产 / 上传后不应清掉用户正在编辑的内容
      } else if (selectedFilePath.value === 'SKILL.md') {
        // 默认加载 SKILL.md
        editingContent.value = data.skill_md_content || ''
        originalContent.value = data.skill_md_content || ''
      } else {
        // 如果选中了其他文件，递归查找读取它
        await loadFileContent(skillId, selectedFilePath.value)
      }
    }
  } catch (e: any) {
    if (token !== detailToken) return
    showToast(e.response?.data?.detail || '获取技能详情失败', 'error')
    showDrawer.value = false
  } finally {
    if (token === detailToken) fetchingDetail.value = false
  }
}

// 在线读取特定资产文件内容
const loadFileContent = async (skillId: string, filePath: string) => {
  const token = invalidateDetailRequests()
  fetchingDetail.value = true
  try {
    const apiPrefix = resolveSkillApiPrefix(skillId)
    const response = await axios.get(`${apiPrefix}/${skillId}/files`, {
      params: { path: filePath }
    })
    if (token !== detailToken) return
    if (response.data && response.data.status === 'success') {
      editingContent.value = response.data.content || ''
      originalContent.value = response.data.content || ''
    }
  } catch (e: any) {
    if (token !== detailToken) return
    showToast(e.response?.data?.detail || `读取文件 ${filePath} 失败`, 'error')
    // 降级回 SKILL.md
    selectedFilePath.value = 'SKILL.md'
    await fetchSkillDetail(skillId)
  } finally {
    if (token === detailToken) fetchingDetail.value = false
  }
}

// 选中某个文件进行编辑
const selectFileForEdit = async (path: string) => {
  // 已经是当前文件：无需切换，避免误弹「切换将丢失修改」并真的重载丢弃编辑
  if (path === selectedFilePath.value) return

  const doSelect = async () => {
    selectedFilePath.value = path
    editorMode.value = path.toLowerCase().endsWith('.md') ? 'preview' : 'edit'
    if (path === 'SKILL.md') {
      await fetchSkillDetail(activeSkillId.value)
    } else {
      await loadFileContent(activeSkillId.value, path)
    }
  }

  if (hasUnsavedChanges.value) {
    confirmState.value = {
      show: true,
      title: '未保存修改确认',
      message: '当前文件有未保存的修改，切换文件将丢失这些修改。确定要切换吗？',
      type: 'warning',
      onConfirm: async () => {
        confirmState.value.show = false
        await doSelect()
      }
    }
    return
  }
  await doSelect()
}

const selectDirectory = (path: string) => {
  selectedDirectoryPath.value = path
}

/** 抽屉内常显入口：右键菜单对键盘与触屏不可达，这里提供等价按钮 */
const createAssetFromDrawer = () => {
  openCreateAssetModal('file')
}

const uploadFromDrawer = () => {
  if (uploading.value) return
  uploadFolder.value = selectedDirectoryPath.value
  uploadType.value = 'normal'
  triggerFileInput()
}

const openCreateAssetModal = (type: 'file' | 'folder') => {
  // 新建成功后都会重载详情并覆盖编辑器内容，因此 file / folder 都要先拦未保存修改
  if (hasUnsavedChanges.value) {
    showToast(`请先保存当前文件的修改，再新建${type === 'file' ? '文件' : '文件夹'}`, 'warning')
    return
  }
  createAssetType.value = type
  createAssetName.value = ''
  showCreateAssetModal.value = true
}

const createSkillAsset = async () => {
  const name = createAssetName.value.trim()
  if (!name) {
    showToast('资产名称不能为空', 'warning')
    return
  }
  if (name.startsWith('.') || name.includes('/') || name.includes('\\')) {
    showToast('请输入非隐藏的单级文件或文件夹名称', 'warning')
    return
  }

  const path = selectedDirectoryPath.value
    ? `${selectedDirectoryPath.value}/${name}`
    : name

  creatingAsset.value = true
  try {
    const apiPrefix = resolveSkillApiPrefix(activeSkillId.value)
    const response = await axios.post(`${apiPrefix}/${activeSkillId.value}/files`, {
      path,
      type: createAssetType.value
    })
    if (response.data?.status === 'success') {
      showToast(createAssetType.value === 'file' ? '文件创建成功' : '文件夹创建成功', 'success')
      showCreateAssetModal.value = false
      if (createAssetType.value === 'file') {
        selectedFilePath.value = path
      } else {
        selectedDirectoryPath.value = path
      }
      await fetchSkillDetail(activeSkillId.value)
    }
  } catch (e: any) {
    showToast(e.response?.data?.detail || '创建资产失败', 'error')
  } finally {
    creatingAsset.value = false
  }
}

// 是否有未保存的修改
const hasUnsavedChanges = computed(() => {
  return editingContent.value !== originalContent.value
})

const requestCloseDrawer = () => {
  if (!hasUnsavedChanges.value) {
    showDrawer.value = false
    return
  }
  confirmState.value = {
    show: true,
    title: '未保存修改确认',
    message: '当前文件有未保存的修改，关闭将丢失这些修改。确定要关闭吗？',
    type: 'warning',
    onConfirm: () => {
      confirmState.value.show = false
      showDrawer.value = false
    }
  }
}

const handleDrawerKeydown = (e: KeyboardEvent) => {
  if (e.key !== 'Escape' || !showDrawer.value) return
  if (confirmState.value.show || showCreateModal.value || showImportModal.value || showCreateAssetModal.value || showStatsModal.value || showHelpModal.value || showBindingsModal.value) {
    return
  }
  e.preventDefault()
  requestCloseDrawer()
}

// 计算行数以显示在编辑器状态栏
const lineCount = computed(() => {
  return editingContent.value.split('\n').length || 1
})

const charCount = computed(() => editingContent.value.length)

const selectedFileExtension = computed(() => {
  const parts = selectedFilePath.value.split('.')
  return parts.length > 1 ? parts.pop()!.toLowerCase() : 'txt'
})

const editorTextareaRef = ref<HTMLTextAreaElement | null>(null)
const lineNumbersRef = ref<HTMLDivElement | null>(null)

const syncLineNumberScroll = () => {
  if (lineNumbersRef.value && editorTextareaRef.value) {
    lineNumbersRef.value.scrollTop = editorTextareaRef.value.scrollTop
  }
}

const handleEditorKeydown = (e: KeyboardEvent) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 's') {
    e.preventDefault()
    if (hasUnsavedChanges.value && !saving.value) {
      saveFileContent()
    }
  }
}

const mdParser = new MarkdownIt({
  html: true,
  linkify: true,
  breaks: true
})

const editorMode = ref<'edit' | 'preview'>('preview')

const isMarkdownFile = computed(() => {
  return selectedFilePath.value.toLowerCase().endsWith('.md')
})

const renderedMarkdown = computed(() => {
  if (!editingContent.value) return '<p class="preview-empty">暂无内容，请先在编辑视图中编写</p>'
  try {
    return mdParser.render(editingContent.value)
  } catch (e) {
    return `<p class="text-red-400 p-4">渲染解析 Markdown 时遇到错误: ${e}</p>`
  }
})

// 保存编辑的文件内容
const saveFileContent = async () => {
  saving.value = true
  try {
    const apiPrefix = resolveSkillApiPrefix(activeSkillId.value)
    const response = await axios.put(`${apiPrefix}/${activeSkillId.value}/files`, {
      path: selectedFilePath.value,
      content: editingContent.value
    })
    if (response.data && response.data.status === 'success') {
      showToast('文件保存成功', 'success')
      originalContent.value = editingContent.value
      // 重新获取详情以更新文件树大小
      await fetchSkillDetail(activeSkillId.value)
    }
  } catch (e: any) {
    showToast(e.response?.data?.detail || '保存失败', 'error')
  } finally {
    saving.value = false
  }
}

// 删除技能目录下的某个子文件或子目录
const deleteSkillFile = (filePath: string) => {
  confirmState.value = {
    show: true,
    title: '删除资产文件',
    message: `确定要删除物理资产 [${filePath}] 吗？\n该删除操作无法撤销！`,
    type: 'danger',
    onConfirm: async () => {
      try {
        const apiPrefix = resolveSkillApiPrefix(activeSkillId.value)
        const response = await axios.delete(`${apiPrefix}/${activeSkillId.value}/files`, {
          params: { path: filePath }
        })
        if (response.data && response.data.status === 'success') {
          showToast('资产已成功物理删除', 'success')
          const selectedWasRemoved =
            selectedFilePath.value === filePath ||
            selectedFilePath.value.startsWith(`${filePath}/`)
          if (selectedWasRemoved) {
            selectedFilePath.value = 'SKILL.md'
          }
          if (
            selectedDirectoryPath.value === filePath ||
            selectedDirectoryPath.value.startsWith(`${filePath}/`)
          ) {
            selectedDirectoryPath.value = ''
          }
          // 只有当前正在编辑的文件被删除时才重载内容，否则保留未保存的编辑
          await fetchSkillDetail(activeSkillId.value, { preserveEditing: !selectedWasRemoved })
        }
      } catch (e: any) {
        showToast(e.response?.data?.detail || '删除资产失败', 'error')
      } finally {
        confirmState.value.show = false
      }
    }
  }
}

// 上传资产文件
const handleFileUpload = async (event: Event) => {
  const target = event.target as HTMLInputElement
  const files = target.files
  if (!files || files.length === 0) return
  await uploadFiles(files)
}

// 技能包（zip / tar.gz / tgz）上限：与后端 app/core/upload_limits.py 对齐。
// 只在这里定义一次——弹窗文案、点选、拖拽、批量预检都引用它，改上限不会漏改。
const SKILL_PACKAGE_MAX_MB = 100
const SKILL_PACKAGE_MAX_BYTES = SKILL_PACKAGE_MAX_MB * 1024 * 1024
const ARCHIVE_EXTENSIONS = ['.zip', '.tar', '.gz', '.tgz', '.bz2']

/** 校验待导入技能包：格式与大小。点选与拖拽共用，避免两条路径规则漂移。 */
const validateArchiveFile = (file: File): boolean => {
  const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase()
  if (!ARCHIVE_EXTENSIONS.includes(ext)) {
    showToast('仅支持 .zip, .tar, .tar.gz, .tgz 等压缩包格式', 'warning')
    return false
  }
  if (file.size > SKILL_PACKAGE_MAX_BYTES) {
    showToast(`导入技能包不能超过 ${SKILL_PACKAGE_MAX_MB}MB`, 'warning')
    return false
  }
  return true
}

// 物理上传执行 (单文件限 10MB / 压缩包限 100MB)
const uploadFiles = async (files: FileList) => {
  uploading.value = true
  const failed: string[] = []
  let succeeded = 0
  try {
    for (const file of Array.from(files)) {
      const isArchive = uploadType.value === 'archive'
      const limit = isArchive ? SKILL_PACKAGE_MAX_BYTES : 10 * 1024 * 1024
      if (file.size > limit) {
        failed.push(`${file.name}（超过 ${isArchive ? `${SKILL_PACKAGE_MAX_MB}MB` : '10MB'} 限制）`)
        continue
      }

      try {
        const formData = new FormData()
        formData.append('file', file)
        if (uploadFolder.value.trim()) {
          formData.append('folder', uploadFolder.value.trim())
        }

        const apiPrefix = resolveSkillApiPrefix(activeSkillId.value)
        if (isArchive) {
          await axios.post(`${apiPrefix}/${activeSkillId.value}/upload-archive`, formData)
        } else {
          await axios.post(`${apiPrefix}/${activeSkillId.value}/upload`, formData)
        }
        succeeded += 1
      } catch (e: any) {
        // 单个文件失败不应中断整批，记录后继续处理剩余文件
        failed.push(`${file.name}（${e.response?.data?.detail || '上传失败'}）`)
      }
    }
  } finally {
    // 成功或失败都要重置上传上下文，避免下次上传误用上一次的目录
    uploadFolder.value = ''
    uploadType.value = 'normal'
    uploading.value = false
  }

  // 上传不应清空用户正在编辑的内容，只刷新文件树
  if (succeeded > 0) {
    await fetchSkillDetail(activeSkillId.value, { preserveEditing: true })
    showToast(`${succeeded} 个文件上传成功`, 'success')
  }
  if (failed.length > 0) {
    showToast(`以下文件上传失败：${failed.join('；')}`, 'error')
  }
}

const openStatsModal = async () => {
  showStatsModal.value = true
  loadingStats.value = true
  try {
    const res = await axios.get('/api/portal/skills/stats')
    statsData.value = res.data
  } catch (e: any) {
    showToast(e.response?.data?.detail || '获取统计数据失败', 'error')
  } finally {
    loadingStats.value = false
  }
  
  nextTick(() => {
    initCharts()
  })
}

const closeStatsModal = () => {
  showStatsModal.value = false
  activeTab.value = 'distribution'
  if (distChartInstance) {
    distChartInstance.dispose()
    distChartInstance = null
  }
  if (trendChartInstance) {
    trendChartInstance.dispose()
    trendChartInstance = null
  }
}

const switchTab = (tab: string) => {
  activeTab.value = tab
  nextTick(() => {
    initCharts()
  })
}

const initCharts = () => {
  if (!hasStatsData.value) return
  
  if (activeTab.value === 'distribution') {
    // 1. 分布饼图
    if (distributionChartRef.value) {
      if (!distChartInstance) {
        distChartInstance = echarts.init(distributionChartRef.value)
        const chartData = Object.entries(statsData.value.total).map(([name, value]) => ({
          name,
          value
        }))
        
        distChartInstance.setOption({
          tooltip: {
            trigger: 'item',
            formatter: '{b}: {c} 次 ({d}%)'
          },
          legend: {
            orient: 'horizontal',
            bottom: 0,
            type: 'scroll'
          },
          series: [
            {
              name: '激活次数',
              type: 'pie',
              radius: ['45%', '70%'],
              avoidLabelOverlap: true,
              itemStyle: {
                borderRadius: 8,
                borderColor: '#fff',
                borderWidth: 2
              },
              label: {
                show: false,
                position: 'center'
              },
              emphasis: {
                label: {
                  show: true,
                  fontSize: 14,
                  fontWeight: 'bold'
                }
              },
              data: chartData
            }
          ]
        })
      } else {
        distChartInstance.resize()
      }
    }
  } else if (activeTab.value === 'trend') {
    // 2. 趋势折线图
    if (trendChartRef.value) {
      if (!trendChartInstance) {
        trendChartInstance = echarts.init(trendChartRef.value)
        const dates = Object.keys(statsData.value.trend).sort()
        const skillIds = Object.keys(statsData.value.total)
        
        const series = skillIds.map(skillId => {
          const data = dates.map(date => statsData.value.trend[date][skillId] || 0)
          return {
            name: skillId,
            type: 'line',
            smooth: true,
            data
          }
        })
        
        trendChartInstance.setOption({
          tooltip: {
            trigger: 'axis'
          },
          legend: {
            data: skillIds,
            bottom: 0,
            type: 'scroll'
          },
          grid: {
            left: '3%',
            right: '4%',
            bottom: '12%',
            containLabel: true
          },
          xAxis: {
            type: 'category',
            boundaryGap: false,
            data: dates.map(d => d.substring(5))
          },
          yAxis: {
            type: 'value',
            minInterval: 1
          },
          series
        })
      } else {
        trendChartInstance.resize()
      }
    }
  }
}

const closeContextMenu = () => {
  contextMenu.value.show = false
  showCreateMenu.value = false
}

const handleContextMenu = (data: { event: MouseEvent, node: any }) => {
  contextMenu.value.show = true
  contextMenu.value.x = data.event.clientX
  contextMenu.value.y = data.event.clientY
  contextMenu.value.node = data.node
}

const handleEmptyAreaContextMenu = (e: MouseEvent) => {
  contextMenu.value.show = true
  contextMenu.value.x = e.clientX
  contextMenu.value.y = e.clientY
  contextMenu.value.node = {
    name: '技能根目录',
    path: '',
    is_dir: true
  }
}

const getParentDirectory = (path: string) => {
  if (!path || !path.includes('/')) return ''
  return path.substring(0, path.lastIndexOf('/'))
}

const handleMenuAction = async (action: 'new-file' | 'new-folder' | 'upload-normal' | 'upload-archive' | 'delete' | 'edit') => {
  const node = contextMenu.value.node
  if (!node) return
  
  closeContextMenu()

  if (action === 'new-file') {
    selectedDirectoryPath.value = node.is_dir ? node.path : getParentDirectory(node.path)
    openCreateAssetModal('file')
  } else if (action === 'new-folder') {
    selectedDirectoryPath.value = node.is_dir ? node.path : getParentDirectory(node.path)
    openCreateAssetModal('folder')
  } else if (action === 'upload-normal') {
    uploadFolder.value = node.is_dir ? node.path : getParentDirectory(node.path)
    uploadType.value = 'normal'
    triggerFileInput()
  } else if (action === 'upload-archive') {
    uploadFolder.value = node.is_dir ? node.path : getParentDirectory(node.path)
    uploadType.value = 'archive'
    triggerFileInput()
  } else if (action === 'delete') {
    await deleteSkillFile(node.path)
  } else if (action === 'edit') {
    await selectFileForEdit(node.path)
  }
}

const triggerFileInput = () => {
  nextTick(() => {
    const input = document.getElementById('file-upload-input') as HTMLInputElement
    if (input) {
      input.click()
    }
  })
}

const openImportModal = () => {
  importFile.value = null
  importOverwrite.value = false
  showImportModal.value = true
}

const openImportFilePicker = () => {
  importFileInput.value?.click()
}

const handleImportFileChange = (e: Event) => {
  const target = e.target as HTMLInputElement
  const files = target.files
  if (files && files.length > 0) {
    const file = files[0]
    if (!file) return
    if (!validateArchiveFile(file)) return
    importFile.value = file
  }
}

const handleImportDragOver = (e: DragEvent) => {
  e.preventDefault()
  importDragActive.value = true
}

const handleImportDragLeave = (e: DragEvent) => {
  e.preventDefault()
  importDragActive.value = false
}

const handleImportDrop = (e: DragEvent) => {
  e.preventDefault()
  importDragActive.value = false
  const files = e.dataTransfer?.files
  if (files && files.length > 0) {
    const file = files[0]
    if (!file) return
    if (!validateArchiveFile(file)) return
    importFile.value = file
  }
}

const submitImportSkill = async () => {
  if (!importFile.value) {
    showToast('请选择或拖入要导入的压缩技能包', 'warning')
    return
  }

  importingSkill.value = true
  try {
    const formData = new FormData()
    formData.append('file', importFile.value)
    formData.append('overwrite', importOverwrite.value ? 'true' : 'false')

    const isPersonal = activeScope.value === 'personal'
    const response = await axios.post(importSkillApiUrl.value, formData)
    if (response.data?.status === 'success') {
      showToast(response.data?.message || `${isPersonal ? '个人' : ''}技能导入成功！`, 'success')
      showImportModal.value = false
      importFile.value = null
      importOverwrite.value = false
      if (isPersonal) {
        await fetchPersonalSkills()
      } else {
        await fetchSkills()
      }
    }
  } catch (e: any) {
    showToast(e.response?.data?.detail || '技能导入失败', 'error')
  } finally {
    importingSkill.value = false
  }
}

onMounted(() => {
  if (!props.personalOnly && canManagePlatformSkills.value) {
    fetchSkills()
    fetchSkillBindings()
    fetchPublicationQueue(true)
    // 静默预取已发布列表：只为了让「已发布」tab 的数量角标在进入前就是准确的
    fetchPublications(true)
  }
  fetchPersonalSkills().then(() => {
    const targetId = String(props.initialSkillId || '').trim()
    if (targetId && personalSkills.value.some((s) => s.id === targetId)) {
      activeWorkspaceTab.value = 'personal'
      activeScope.value = 'personal'
      personalMenu.value = 'mine'
      openSkillDetail(targetId)
    }
  })
  fetchShareMenuCounts()
  document.addEventListener('click', closeContextMenu)
  document.addEventListener('keydown', handleDrawerKeydown)
})

watch(
  () => props.initialSkillId,
  async (skillId) => {
    const targetId = String(skillId || '').trim()
    if (!targetId) return
    if (!personalSkills.value.some((s) => s.id === targetId)) {
      await fetchPersonalSkills()
    }
    if (personalSkills.value.some((s) => s.id === targetId)) {
      activeWorkspaceTab.value = 'personal'
      activeScope.value = 'personal'
      personalMenu.value = 'mine'
      openSkillDetail(targetId)
    }
  },
)

watch(
  () => [String(route.query.skills_tab || ''), props.initialPersonalMenu],
  () => {
    personalMenu.value = resolvePersonalMenu()
  },
)

onUnmounted(() => {
  document.removeEventListener('click', closeContextMenu)
  document.removeEventListener('keydown', handleDrawerKeydown)
  disposeSkillDrawerChart()
  if (distChartInstance) {
    distChartInstance.dispose()
    distChartInstance = null
  }
  if (trendChartInstance) {
    trendChartInstance.dispose()
    trendChartInstance = null
  }
})
</script>

<template>
  <div :class="personalOnly ? 'space-y-6' : 'space-y-5'">
    <!-- Header：标题单独一行；搜索 + 排序/视图 + 新建同一工具行 -->
    <div class="flex flex-col gap-4">
      <div class="flex min-w-0 items-center gap-2.5">
        <h1
          class="font-bold tracking-normal text-gray-900"
          :class="personalOnly ? 'text-xl' : 'text-2xl'"
        >
          {{ personalOnly ? '我的技能' : '技能工作台' }}
        </h1>
        <button
          type="button"
          class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-gray-200 bg-white text-emerald-600 shadow-sm transition-colors hover:border-emerald-300 hover:bg-emerald-50 cursor-pointer"
          title="技能开发规范与全流程指引"
          @click="showHelpModal = true"
        >
          <span class="text-sm font-bold">?</span>
        </button>
        <button
          v-if="!showSkillFlowGuide"
          type="button"
          class="hidden sm:inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50/80 px-2.5 py-1 text-xs font-medium text-emerald-700 shadow-sm transition-colors hover:bg-emerald-100 cursor-pointer"
          title="重新在页面顶部显示流程引导"
          @click="restoreSkillFlowGuide"
        >
          <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <span class="whitespace-nowrap">显示指引</span>
        </button>
      </div>

    </div>

    <!-- 技能工作台全生命周期指引横幅 -->
    <div v-if="showSkillFlowGuide" class="flex-shrink-0">
      <SkillFlowGuideBanner
        :platform-workspace-available="canAccessPlatformWorkspace"
        @action="handleSkillBannerAction"
        @close="handleCloseSkillFlowGuide"
        @dismiss="handleDismissSkillFlowGuide"
      />
    </div>

    <!-- Scope Tab 切换 -->
    <div
      v-if="!personalOnly"
      class="flex items-center overflow-x-auto border-b border-gray-200 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
    >
      <button
        v-if="canManagePlatformSkills"
        id="tab-global-skills"
        @click="activeWorkspaceTab = 'global'; activeScope = 'global'"
        class="flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2.5 text-xs font-medium transition-colors sm:gap-2 sm:px-4 sm:text-sm"
        :class="activeWorkspaceTab === 'global' ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'"
      >
        <svg class="h-3.5 w-3.5 shrink-0 sm:h-4 sm:w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064" />
        </svg>
        平台技能
        <span class="ml-1 px-1.5 py-0.5 text-[10px] rounded-full font-semibold" :class="activeWorkspaceTab === 'global' ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500'">{{ skills.length }}</span>
      </button>
      <button
        id="tab-personal-skills"
        @click="activeWorkspaceTab = 'personal'; activeScope = 'personal'"
        class="flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2.5 text-xs font-medium transition-colors sm:gap-2 sm:px-4 sm:text-sm"
        :class="activeWorkspaceTab === 'personal' ? 'border-emerald-600 text-emerald-600' : 'border-transparent text-gray-500 hover:text-gray-700'"
      >
        <svg class="h-3.5 w-3.5 shrink-0 sm:h-4 sm:w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
        </svg>
        我的技能
        <span class="ml-1 px-1.5 py-0.5 text-[10px] rounded-full font-semibold" :class="activeWorkspaceTab === 'personal' ? 'bg-emerald-100 text-emerald-700' : 'bg-gray-100 text-gray-500'">{{ personalSkills.length }}</span>
      </button>
      <button
        v-if="canManagePlatformSkills"
        id="tab-skill-publication-review"
        type="button"
        @click="openPublicationReview"
        class="flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2.5 text-xs font-medium transition-colors sm:gap-2 sm:px-4 sm:text-sm"
        :class="activeWorkspaceTab === 'review' ? 'border-indigo-600 text-indigo-600' : 'border-transparent text-gray-500 hover:text-gray-700'"
      >
        <svg class="h-3.5 w-3.5 shrink-0 sm:h-4 sm:w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.5-2.5A8.5 8.5 0 1112 3.5a8.5 8.5 0 018.5 8.5z" />
        </svg>
        待审核
        <span class="ml-1 px-1.5 py-0.5 text-[10px] rounded-full font-semibold" :class="activeWorkspaceTab === 'review' ? 'bg-indigo-100 text-indigo-700' : publicationQueue.length ? 'bg-amber-100 text-amber-700' : 'bg-gray-100 text-gray-500'">{{ publicationQueue.length }}</span>
      </button>
      <button
        v-if="canManagePlatformSkills"
        id="tab-skill-publications"
        type="button"
        @click="openPublications"
        class="flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2.5 text-xs font-medium transition-colors sm:gap-2 sm:px-4 sm:text-sm"
        :class="activeWorkspaceTab === 'publications' ? 'border-purple-600 text-purple-600' : 'border-transparent text-gray-500 hover:text-gray-700'"
      >
        <svg class="h-3.5 w-3.5 shrink-0 sm:h-4 sm:w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" />
        </svg>
        已发布
        <span class="ml-1 px-1.5 py-0.5 text-[10px] rounded-full font-semibold" :class="activeWorkspaceTab === 'publications' ? 'bg-purple-100 text-purple-700' : 'bg-gray-100 text-gray-500'">{{ publications.length }}</span>
      </button>
    </div>

    <!-- 已发布 / 下架管理面板：一条记录 = 一个已上线的版本，下架即回收其可见性 -->
    <div v-if="activeWorkspaceTab === 'publications' && canAccessPlatformWorkspace" class="rounded-2xl border border-purple-200 bg-white p-5 shadow-sm">
      <div class="mb-4 flex items-start justify-between gap-3 border-b border-gray-100 pb-3">
        <div>
          <h2 class="text-lg font-bold text-gray-900">已发布 / 下架管理</h2>
          <p class="mt-1 text-xs text-gray-500">这里列出已上线到平台技能库的版本；下架会收回该版本对全体用户的可见性，个人源技能与历史发布记录保留。</p>
        </div>
        <div class="flex shrink-0 items-center gap-2">
          <button
            type="button"
            class="rounded-lg border border-gray-200 px-2.5 py-1 text-xs text-gray-600 transition-colors hover:bg-gray-50"
            :disabled="publicationsLoading"
            @click="fetchPublications()"
          >
            刷新
          </button>
          <button type="button" class="rounded-lg px-2 py-1 text-xs text-gray-500 hover:bg-gray-100" @click="closePublications">
            返回平台技能
          </button>
        </div>
      </div>

      <div v-if="publicationsLoading" class="py-8 text-center text-sm text-gray-400">加载已发布技能...</div>
      <div v-else-if="publications.length === 0" class="rounded-xl border border-dashed border-gray-200 py-8 text-center text-sm text-gray-400">
        当前没有已发布的平台技能版本
      </div>
      <div v-else class="overflow-x-auto">
        <table class="min-w-full divide-y divide-gray-200 text-left">
          <thead class="bg-gray-50/70 text-xs font-bold text-gray-500 uppercase tracking-wider select-none">
            <tr>
              <th class="px-4 py-3">技能名称与描述</th>
              <th class="px-4 py-3 text-center">版本</th>
              <th class="px-4 py-3 text-center">平台技能 ID</th>
              <th class="px-4 py-3 text-center">文件</th>
              <th class="px-4 py-3 text-center">发布时间</th>
              <th class="px-4 py-3 text-center">操作</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-200 text-sm">
            <tr v-for="item in publications" :key="item.version_id" class="transition-colors hover:bg-purple-50/20">
              <td class="px-4 py-3">
                <div class="font-bold text-gray-900">{{ item.name || item.skill_id }}</div>
                <div class="mt-0.5 max-w-2xl text-xs text-gray-500 line-clamp-2" :title="item.description">{{ item.description || '暂无描述' }}</div>
                <div class="mt-0.5 font-mono text-[10px] text-gray-400">来源个人技能 {{ item.skill_id }}</div>
              </td>
              <td class="px-4 py-3 text-center whitespace-nowrap">
                <span class="rounded-full bg-blue-100 px-2 py-0.5 text-[10px] font-semibold text-blue-700">v{{ item.version_number ?? '—' }}</span>
                <div class="mt-1 text-[10px] font-semibold text-emerald-600">已发布</div>
              </td>
              <td class="px-4 py-3 text-center font-mono text-xs text-gray-500">{{ item.platform_skill_id || '—' }}</td>
              <td class="px-4 py-3 text-center text-xs text-gray-500 whitespace-nowrap">{{ item.file_count ?? 0 }} 个</td>
              <td class="px-4 py-3 text-center text-xs text-gray-500 whitespace-nowrap">{{ formatPublishedAt(item.published_at) }}</td>
              <td class="px-4 py-3 text-center whitespace-nowrap">
                <button
                  type="button"
                  class="rounded-lg border border-red-200 px-3 py-1.5 text-xs font-semibold text-red-600 transition-colors hover:bg-red-50"
                  @click="startRevokePublication(item)"
                >
                  下架
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 管理员发布审核面板 -->
    <div v-if="activeWorkspaceTab === 'review' && canAccessPlatformWorkspace" class="rounded-2xl border border-indigo-200 bg-white p-5 shadow-sm">
      <div class="mb-4 flex items-start justify-between gap-3 border-b border-gray-100 pb-3">
        <div>
          <h2 class="text-lg font-bold text-gray-900">发布审核</h2>
          <p class="mt-1 text-xs text-gray-500">通过后会复制为平台技能，个人技能不会被删除或自动替换。</p>
        </div>
        <button type="button" class="rounded-lg px-2 py-1 text-xs text-gray-500 hover:bg-gray-100" @click="closePublicationReview">
          返回平台技能
        </button>
      </div>

      <div v-if="publicationReviewLoading" class="py-8 text-center text-sm text-gray-400">加载审核队列...</div>
      <div v-else-if="publicationQueue.length === 0" class="rounded-xl border border-dashed border-gray-200 py-8 text-center text-sm text-gray-400">
        当前没有待审核的个人技能发布申请
      </div>
      <div v-else class="grid gap-4 lg:grid-cols-[300px_minmax(0,1fr)] xl:grid-cols-[340px_minmax(0,1fr)]">
        <div class="space-y-2">
          <button
            v-for="item in publicationQueue"
            :key="item.version_id"
            type="button"
            class="flex w-full items-start justify-between rounded-xl border px-3 py-3 text-left transition-colors"
            :class="activePublicationReview?.version_id === item.version_id ? 'border-indigo-400 bg-indigo-50' : 'border-gray-200 hover:border-indigo-200 hover:bg-gray-50'"
            @click="openPublicationReviewDetail(item.version_id)"
          >
            <span class="min-w-0">
              <span class="block truncate text-sm font-semibold text-gray-800">{{ item.name }}</span>
              <span class="mt-1 block text-[11px] text-gray-500">个人技能 {{ item.skill_id }} · v{{ item.version_number }}</span>
            </span>
            <span class="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-700">待审核</span>
          </button>
        </div>

        <div v-if="activePublicationReview" class="rounded-xl border border-gray-200 bg-gray-50/60 p-4">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <h3 class="truncate text-base font-bold text-gray-900">{{ activePublicationReview.name }}</h3>
              <p class="mt-1 text-xs text-gray-500">{{ activePublicationReview.description || '暂无描述' }}</p>
            </div>
            <span class="shrink-0 rounded-full bg-blue-100 px-2 py-0.5 text-[10px] font-semibold text-blue-700">平台候选 v{{ activePublicationReview.version_number }}</span>
          </div>
          <div class="mt-3 flex flex-wrap gap-2 text-[10px] text-gray-500">
            <span class="rounded bg-white px-2 py-1">文件 {{ activePublicationReview.file_count || 0 }}</span>
            <span class="rounded bg-white px-2 py-1">SHA256 {{ activePublicationReview.content_sha256?.slice(0, 12) }}</span>
          </div>
          <!-- 包内文件浏览：此前只渲染 SKILL.md，scripts/ 里的可执行代码在无人查看的
               情况下就能被批准发布进公共技能库，与「审查文件 Diff 与代码合规性」的文案不符。 -->
          <div class="mt-3 grid gap-3 md:grid-cols-[minmax(0,0.85fr)_minmax(0,1.3fr)]">
            <div class="max-h-72 overflow-auto rounded-lg border border-gray-200 bg-white p-2">
              <div
                v-if="!activePublicationReview.file_tree?.length"
                class="p-3 text-center text-[11px] text-gray-400"
              >
                快照内没有可列出的文件
              </div>
              <SkillFileTree
                v-else
                :tree-data="activePublicationReview.file_tree"
                :selected-path="publicationReviewFilePath"
                @select-file="openPublicationReviewFile"
              />
            </div>
            <div class="min-w-0">
              <div class="mb-1 flex items-center justify-between gap-2">
                <span class="truncate font-mono text-[11px] text-gray-500">{{ publicationReviewFilePath || '未选择文件' }}</span>
                <span v-if="publicationReviewFileLoading" class="shrink-0 text-[10px] text-gray-400">读取中…</span>
              </div>
              <pre class="max-h-72 overflow-auto rounded-lg bg-slate-900 p-3 text-[11px] leading-relaxed text-slate-100">{{ publicationReviewFileContent || '（该文件为空或不可预览）' }}</pre>
            </div>
          </div>
          <textarea
            v-model="publicationReviewComment"
            rows="3"
            class="mt-3 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-xs outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
            placeholder="驳回时填写原因；通过时可留空"
          />
          <div class="mt-3 flex justify-end gap-2">
            <button type="button" class="rounded-lg border border-red-200 px-3 py-2 text-xs font-semibold text-red-600 hover:bg-red-50" :disabled="publicationReviewAction" @click="rejectPublicationReview">驳回</button>
            <button type="button" class="rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50" :disabled="publicationReviewAction" @click="approvePublicationReview">通过并发布</button>
          </div>
        </div>
        <div v-else class="flex min-h-32 items-center justify-center rounded-xl border border-dashed border-gray-200 text-sm text-gray-400">
          请选择左侧申请查看详情
        </div>
      </div>
    </div>

    <!-- 我的技能：左侧二级菜单 + 右侧内容（窄屏折叠为顶部胶囊 Tab） -->
    <div v-if="activeWorkspaceTab === 'personal'" class="mb-3 md:hidden">
      <SkillPersonalMenu
        variant="chips"
        :active="personalMenu"
        :received-count="shareMenuCounts.received_pending"
        :sent-pending-count="shareMenuCounts.sent_pending"
        @select="personalMenu = $event"
      />
    </div>

    <div class="flex flex-col gap-4 md:flex-row md:items-start">
      <SkillPersonalMenu
        v-if="activeWorkspaceTab === 'personal'"
        variant="aside"
        :active="personalMenu"
        :received-count="shareMenuCounts.received_pending"
        :sent-pending-count="shareMenuCounts.sent_pending"
        @select="personalMenu = $event"
      />
      <div class="min-w-0 flex-1 space-y-4">
        <ReceivedSharesPanel
          v-if="activeWorkspaceTab === 'personal' && personalMenu === 'received'"
          @updated="handleShareChanged"
          @go-mine="openSharedSkill"
        />
        <SentSharesPanel
          v-else-if="activeWorkspaceTab === 'personal' && personalMenu === 'sent'"
          @updated="handleShareChanged"
          @open-skill="openSharedSkill"
        />
        <template v-else>
          <div v-if="showSkillList" class="flex flex-col gap-3 sm:flex-row sm:items-center">
            <div class="relative min-w-0 flex-1">
              <span class="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3">
                <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
              </span>
              <input
                v-model="searchQuery"
                type="search"
                placeholder="搜索技能名称或描述..."
                class="w-full rounded-lg border border-gray-300 bg-white py-2.5 pl-9 pr-4 text-sm shadow-sm transition-all focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>

            <div class="flex shrink-0 flex-wrap items-center gap-2">
              <div class="flex select-none items-center gap-0.5 rounded-lg border border-gray-300 bg-gray-200/60 p-0.5">
                <button
                  type="button"
                  class="rounded-md px-2.5 py-1.5 text-xs font-medium transition-all"
                  :class="skillSortBy === 'name' ? 'border border-gray-200 bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:bg-gray-200 hover:text-gray-800'"
                  title="按名称排序"
                  @click="skillSortBy = 'name'"
                >
                  名称
                </button>
                <button
                  type="button"
                  class="rounded-md px-2.5 py-1.5 text-xs font-medium transition-all"
                  :class="skillSortBy === 'time' ? 'border border-gray-200 bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:bg-gray-200 hover:text-gray-800'"
                  title="按更新时间排序"
                  @click="skillSortBy = 'time'"
                >
                  时间
                </button>
                <button
                  type="button"
                  class="rounded-md p-1.5 text-gray-500 transition-all hover:bg-gray-200 hover:text-gray-800"
                  :title="skillSortOrder === 'asc' ? '当前升序，点击切换降序' : '当前降序，点击切换升序'"
                  @click="toggleSkillSortOrder"
                >
                  <svg class="h-4 w-4 transition-transform" :class="skillSortOrder === 'desc' ? 'rotate-180' : ''" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4h13M3 8h9m-9 4h6m4 0v4m0 0l-3-3m3 3l3-3" />
                  </svg>
                </button>
              </div>

              <div class="flex select-none items-center gap-0.5 rounded-lg border border-gray-300 bg-gray-200/60 p-0.5">
                <button
                  type="button"
                  class="flex items-center justify-center rounded-md p-1.5 transition-all duration-200"
                  :class="viewMode === 'card' ? 'border border-gray-200 bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:bg-gray-200 hover:text-gray-800'"
                  title="卡片视图"
                  @click="viewMode = 'card'"
                >
                  <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
                  </svg>
                </button>
                <button
                  type="button"
                  class="flex items-center justify-center rounded-md p-1.5 transition-all duration-200"
                  :class="viewMode === 'list' ? 'border border-gray-200 bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:bg-gray-200 hover:text-gray-800'"
                  title="列表视图"
                  @click="viewMode = 'list'"
                >
                  <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
                  </svg>
                </button>
              </div>

              <button
                v-if="!personalOnly"
                type="button"
                class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-gray-300 bg-white text-gray-700 shadow-sm transition-all hover:bg-gray-50 active:scale-95"
                title="统计查看"
                @click="openStatsModal"
              >
                <svg class="h-4 w-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                </svg>
              </button>

              <div class="relative shrink-0" @click.stop>
                <div class="flex items-stretch overflow-hidden rounded-lg shadow-sm">
                  <button
                    type="button"
                    class="flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium text-white transition-all active:scale-[0.98]"
                    :class="activeScope === 'personal' ? 'bg-emerald-600 hover:bg-emerald-700' : 'bg-blue-600 hover:bg-blue-700'"
                    @click="openCreateModal"
                  >
                    <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
                    </svg>
                    新建技能
                  </button>
                  <button
                    type="button"
                    class="border-l px-2 text-white transition-colors"
                    :class="activeScope === 'personal' ? 'border-emerald-500 bg-emerald-600 hover:bg-emerald-700' : 'border-blue-500 bg-blue-600 hover:bg-blue-700'"
                    title="更多创建方式"
                    @click="showCreateMenu = !showCreateMenu"
                  >
                    <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                    </svg>
                  </button>
                </div>
                <div
                  v-if="showCreateMenu"
                  class="absolute right-0 z-20 mt-1.5 w-48 rounded-xl border border-gray-200 bg-white py-1 shadow-lg"
                >
                  <button
                    type="button"
                    class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50"
                    @click="showCreateMenu = false; openCreateModal()"
                  >
                    <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
                    </svg>
                    空白新建
                  </button>
                  <button
                    type="button"
                    class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50"
                    @click="showCreateMenu = false; openImportModal()"
                  >
                    <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                    </svg>
                    导入 Zip/Tar
                  </button>
                </div>
              </div>
            </div>
          </div>
    <template v-if="showSkillList">
    <!-- 技能列表网格 -->
    <div v-if="loading" class="flex flex-col items-center justify-center py-16">
      <div class="w-10 h-10 border-4 border-blue-600 border-t-transparent rounded-full animate-spin"></div>
      <p class="text-sm text-gray-500 mt-4 font-medium">智能检索系统文件夹中...</p>
    </div>

    <div v-else-if="sortedSkills.length === 0" class="flex flex-col items-center justify-center min-h-[360px] bg-white border border-gray-200 rounded-lg shadow-sm px-6">
      <svg class="w-14 h-14 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5S19.832 5.477 21 6.253v13C19.832 18.477 18.246 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
      </svg>
      <template v-if="isSearchEmpty">
        <p class="text-sm text-gray-500 mt-4 font-semibold">无匹配「{{ searchQuery.trim() }}」的技能</p>
        <p class="text-xs text-gray-400 mt-1">试试其他关键词，或清除搜索后浏览全部</p>
        <button
          @click="clearSearch"
          class="mt-5 px-4 py-2 text-sm font-medium text-blue-600 border border-blue-200 rounded-lg hover:bg-blue-50 transition-colors"
        >
          清除搜索
        </button>
      </template>
      <template v-else>
        <p class="text-sm text-gray-500 mt-4 font-semibold">
          {{ activeScope === 'personal' ? '还没有个人技能' : '暂无平台技能' }}
        </p>
        <p class="text-xs text-gray-400 mt-1 text-center max-w-sm">
          <template v-if="activeScope === 'personal'">
            个人技能仅对自己可见，可新建或导入到「我的技能」目录
          </template>
          <template v-else>
            您可以新建一个技能，或导入 Zip/Tar 压缩包
          </template>
        </p>
        <div class="mt-5 flex items-center gap-3">
          <button
            @click="openCreateModal"
            class="px-4 py-2 text-sm font-medium text-white rounded-lg transition-colors"
            :class="activeScope === 'personal' ? 'bg-emerald-600 hover:bg-emerald-700' : 'bg-blue-600 hover:bg-blue-700'"
          >
            新建技能
          </button>
          <button
            @click="openImportModal"
            class="px-4 py-2 text-sm font-medium border rounded-lg transition-colors"
            :class="activeScope === 'personal' ? 'text-emerald-600 border-emerald-200 hover:bg-emerald-50' : 'text-blue-600 border-blue-200 hover:bg-blue-50'"
          >
            导入技能
          </button>
        </div>
      </template>
    </div>

    <!-- 卡片视图 -->
    <div
      v-else-if="viewMode === 'card'"
      class="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3"
      :class="personalOnly ? 'gap-6' : ''"
    >
      <!-- 磨砂玻璃质感卡片 -->
      <div 
        v-for="skill in sortedSkills" 
        :key="skill.id"
        role="button"
        tabindex="0"
        :aria-label="`配置技能 ${skill.name || skill.id}`"
        @keydown.enter.prevent="openSkillDetail(skill.id)"
        @keydown.space.prevent="openSkillDetail(skill.id)"
        @click="openSkillDetail(skill.id)"
        class="group relative flex flex-col cursor-pointer rounded-xl border border-gray-200 bg-white transition-all duration-200 hover:-translate-y-0.5 hover:border-blue-400 hover:shadow-lg"
        :class="[
          personalOnly ? 'p-6' : 'rounded-lg p-5',
          !isSkillEnabled(skill.enabled) ? 'opacity-65 saturate-50 bg-gray-50/50' : '',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-2',
        ]"
      >
        <!-- 卡片顶部 -->
        <div class="flex items-start justify-between gap-3">
          <div class="flex min-w-0 items-center space-x-3">
            <div
              class="flex h-10 w-10 items-center justify-center rounded-xl transition-transform group-hover:scale-110"
              :class="skill.scope === 'personal' ? 'bg-gradient-to-br from-emerald-500/10 to-teal-500/10 text-emerald-600' : 'bg-gradient-to-br from-blue-500/10 to-indigo-500/10 text-blue-600'"
            >
              <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5S19.832 5.477 21 6.253v13C19.832 18.477 18.246 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
              </svg>
            </div>
            <div class="min-w-0">
              <div class="flex items-center gap-1.5">
                <h3 class="truncate font-bold text-gray-800 transition-colors group-hover:text-blue-600">
                  {{ skill.name }}
                </h3>
                <!-- Scope 徽章：个人中心全是个人技能，不再重复标「个人」 -->
                <span
                  v-if="skill.scope === 'personal' && !personalOnly"
                  class="shrink-0 rounded-full bg-emerald-100 px-1.5 py-0.5 text-[9px] font-semibold text-emerald-700"
                >个人</span>
                <span
                  v-else-if="skill.scope !== 'personal'"
                  class="shrink-0 rounded-full bg-blue-100 px-1.5 py-0.5 text-[9px] font-semibold text-blue-700"
                >平台</span>
                <span
                  v-if="!isSkillEnabled(skill.enabled)"
                  class="shrink-0 rounded-full bg-gray-100 px-1.5 py-0.5 text-[9px] font-semibold text-gray-500"
                >已禁用</span>
                <span
                  v-if="skill.scope === 'personal' && skill.publication_status && skill.publication_status !== 'UNPUBLISHED'"
                  class="shrink-0 rounded-full px-1.5 py-0.5 text-[9px] font-semibold"
                  :class="publicationBadgeClass(skill.publication_status)"
                >
                  {{ publicationBadgeLabel(skill) }}
                </span>
              </div>
              <p class="mt-0.5 font-mono text-[10px] uppercase tracking-wider text-gray-400">
                ID: {{ skill.id }}
              </p>
              <p
                v-if="skill.shared_from"
                class="mt-0.5 inline-flex items-center gap-1 text-[10px] text-blue-600"
                :title="`来源技能：${skill.shared_from.source_skill_id}`"
              >
                <svg class="h-3 w-3 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 12v7a2 2 0 002 2h12a2 2 0 002-2v-7M16 6l-4-4-4 4M12 2v13" />
                </svg>
                来自 {{ skill.shared_from.source_real_name || skill.shared_from.source_user_name }} 的分享
              </p>
            </div>
          </div>
          
          <div class="flex shrink-0 items-center space-x-2" @click.stop>
            <!-- Switch 开关 -->
            <label class="relative inline-flex cursor-pointer items-center" title="启用/禁用此技能（禁用后对话不再自动匹配）">
              <input 
                type="checkbox" 
                :checked="isSkillEnabled(skill.enabled)"
                @change="toggleSkillStatus(skill)"
                class="peer sr-only"
              >
              <div class="peer h-4 w-7 rounded-full bg-gray-200 after:absolute after:left-[2px] after:top-[2px] after:h-3 after:w-3 after:rounded-full after:border after:border-gray-300 after:bg-white after:transition-all after:content-[''] peer-checked:bg-blue-600 peer-checked:after:translate-x-full peer-checked:after:border-white peer-focus:outline-none dark:bg-gray-700"></div>
            </label>

            <!-- 操作收进「⋯」：桌面 hover 显示，移动端常驻；菜单 fixed 定位避免被卡片裁切 -->
            <button
              type="button"
              @click.stop="openActionMenu($event, skill)"
              class="rounded-lg p-1.5 text-gray-400 opacity-100 transition-all hover:bg-gray-100 hover:text-gray-600 md:opacity-0 md:group-hover:opacity-100 focus-visible:opacity-100"
              :class="actionMenu?.skill?.id === skill.id ? 'bg-gray-100 text-gray-600' : ''"
              :aria-label="`${skill.name} 的更多操作`"
              :aria-expanded="actionMenu?.skill?.id === skill.id"
            >
              <svg class="h-4 w-4" fill="currentColor" viewBox="0 0 24 24">
                <circle cx="5" cy="12" r="1.6" />
                <circle cx="12" cy="12" r="1.6" />
                <circle cx="19" cy="12" r="1.6" />
              </svg>
            </button>
          </div>
        </div>

        <!-- 技能描述 -->
        <p class="mt-4 flex-1 text-sm leading-relaxed text-gray-500 line-clamp-3">
          {{ skill.description || '暂无描述' }}
        </p>

        <div v-if="skill.scope === 'personal'" class="mt-3 flex items-center justify-between gap-2">
          <span v-if="skill.last_review_comment" class="min-w-0 truncate text-[10px] text-red-500" :title="skill.last_review_comment">{{ skill.publication_status === 'REVOKED' ? '下架原因' : '驳回' }}：{{ skill.last_review_comment }}</span>
          <span v-else class="text-[10px] text-gray-400">个人源文件始终保留</span>
          <div class="flex shrink-0 items-center gap-1.5">
            <button
              v-if="skill.publication_status !== 'PENDING'"
              type="button"
              class="rounded-lg border border-indigo-200 px-2 py-1 text-[10px] font-semibold text-indigo-600 transition-colors hover:bg-indigo-50"
              @click.stop="submitSkillPublication(skill)"
            >
              {{ skill.publication_status === 'PUBLISHED' || skill.publication_status === 'REJECTED' ? '提交新版本' : '提交为平台技能' }}
            </button>
            <button
              v-else
              type="button"
              class="rounded-lg border border-amber-200 px-2 py-1 text-[10px] font-semibold text-amber-700 transition-colors hover:bg-amber-50"
              @click.stop="withdrawSkillPublication(skill)"
            >
              撤销审核
            </button>
          </div>
        </div>

        <!-- 卡片底部路径信息 -->
        <div
          class="mt-5 flex items-center justify-between gap-2 border-t border-gray-100 pt-3 font-mono text-[11px] text-gray-400"
        >
          <div v-if="!personalOnly" class="flex min-w-0 items-center gap-1.5">
            <svg class="h-3.5 w-3.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
            </svg>
            <span class="truncate">{{ skill.path }}</span>
          </div>
          <span v-else class="text-[11px] text-gray-400">更新</span>
          <div class="flex shrink-0 items-center gap-2">
            <button
              v-if="!personalOnly && skill.scope !== 'personal'"
              type="button"
              @click.stop="openBindingsModal(skill)"
              class="rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums transition-colors"
              :class="getBindingEntry(skill.id).count > 0
                ? 'bg-violet-50 text-violet-700 hover:bg-violet-100'
                : 'bg-gray-50 text-gray-400 hover:bg-gray-100'"
              :title="`显式绑定 ${getBindingEntry(skill.id).count} 个智能体`"
            >
              绑定 {{ getBindingEntry(skill.id).count }}
            </button>
            <span class="tabular-nums">{{ formatModifiedAt(skill.modified_at) }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 列表视图 -->
    <div v-else class="bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
      <div class="overflow-x-auto">
        <table class="min-w-full divide-y divide-gray-200 text-left">
          <thead class="bg-gray-50/70 text-xs font-bold text-gray-500 uppercase tracking-wider select-none">
            <tr>
              <th class="px-6 py-4">技能名称与描述</th>
              <th class="px-6 py-4 text-center">物理映射路径</th>
              <th v-if="!personalOnly && activeScope === 'global'" class="px-6 py-4 text-center">绑定</th>
              <th class="px-6 py-4 text-center">更新时间</th>
              <th class="px-6 py-4 text-center">状态</th>
              <th class="px-6 py-4 text-center">操作</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-200 text-sm">
            <tr 
              v-for="skill in sortedSkills" 
              :key="skill.id"
              @click="openSkillDetail(skill.id)"
              class="hover:bg-blue-50/20 cursor-pointer transition-colors group"
              :class="{ 'opacity-65 saturate-50 bg-gray-50/30': !isSkillEnabled(skill.enabled) }"
            >
              <td class="px-6 py-4">
                <div class="flex items-start space-x-3">
                  <div class="flex items-center justify-center w-8 h-8 rounded-lg bg-blue-50 text-blue-600 group-hover:scale-105 transition-transform flex-shrink-0 mt-0.5">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5S19.832 5.477 21 6.253v13C19.832 18.477 18.246 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                    </svg>
                  </div>
                  <div class="space-y-0.5">
                    <div class="font-bold text-gray-900 group-hover:text-blue-600 transition-colors flex items-center gap-2 flex-wrap">
                      <span>{{ skill.name }}</span>
                      <span
                        v-if="skill.scope === 'personal' && !personalOnly"
                        class="shrink-0 px-1.5 py-0.5 text-[9px] font-semibold rounded-full bg-emerald-100 text-emerald-700"
                      >个人</span>
                      <span
                        v-else-if="skill.scope !== 'personal'"
                        class="shrink-0 px-1.5 py-0.5 text-[9px] font-semibold rounded-full bg-blue-100 text-blue-700"
                      >平台</span>
                      <span
                        v-if="!isSkillEnabled(skill.enabled)"
                        class="shrink-0 px-1.5 py-0.5 text-[9px] font-semibold rounded-full bg-gray-100 text-gray-500"
                      >已禁用</span>
                      <span class="text-[10px] text-gray-400 font-mono bg-gray-100 px-1.5 py-0.5 rounded border border-gray-200 font-normal">ID: {{ skill.id }}</span>
                    </div>
                    <div class="text-xs text-gray-500 max-w-2xl line-clamp-2" :title="skill.description">
                      {{ skill.description || '暂无描述' }}
                    </div>
                  </div>
                </div>
              </td>
              <td class="px-6 py-4 text-center font-mono text-xs text-gray-500 max-w-xs truncate" :title="skill.path">
                {{ skill.path }}
              </td>
              <td
                v-if="!personalOnly && activeScope === 'global'"
                class="px-6 py-4 text-center whitespace-nowrap"
                @click.stop
              >
                <button
                  type="button"
                  @click="openBindingsModal(skill)"
                  class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold tabular-nums transition-colors"
                  :class="getBindingEntry(skill.id).count > 0
                    ? 'bg-violet-50 text-violet-700 hover:bg-violet-100'
                    : 'bg-gray-50 text-gray-400 hover:bg-gray-100'"
                  :title="`显式绑定 ${getBindingEntry(skill.id).count} 个智能体`"
                >
                  {{ getBindingEntry(skill.id).count }}
                </button>
              </td>
              <td class="px-6 py-4 text-center text-xs text-gray-500 whitespace-nowrap">
                {{ formatModifiedAt(skill.modified_at) }}
              </td>
              <td class="px-6 py-4 text-center whitespace-nowrap" @click.stop>
                <div class="flex flex-col items-center gap-1">
                  <label class="relative inline-flex items-center cursor-pointer" title="启用/禁用此技能（禁用后对话不再自动匹配）">
                    <input 
                      type="checkbox" 
                      :checked="isSkillEnabled(skill.enabled)"
                      @change="toggleSkillStatus(skill)"
                      class="sr-only peer"
                    >
                    <div class="w-7 h-4 bg-gray-200 dark:bg-gray-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-blue-600"></div>
                  </label>
                  <span
                    class="text-[10px] font-medium"
                    :class="!isSkillEnabled(skill.enabled) ? 'text-gray-400' : 'text-emerald-600'"
                  >{{ !isSkillEnabled(skill.enabled) ? '已禁用' : '已启用' }}</span>
                </div>
              </td>
              <td class="px-6 py-4 text-center whitespace-nowrap" @click.stop>
                <div class="flex items-center justify-center space-x-2 whitespace-nowrap">
                  <button
                    v-if="skill.scope === 'personal' && skill.publication_status === 'PENDING'"
                    type="button"
                    @click="withdrawSkillPublication(skill)"
                    class="rounded-lg border border-amber-200 px-2 py-1.5 text-xs font-semibold text-amber-700 transition-colors hover:bg-amber-50 whitespace-nowrap shrink-0"
                  >
                    撤销审核
                  </button>
                  <button
                    v-if="skill.scope === 'personal'"
                    type="button"
                    @click="openShareDialog(skill)"
                    class="rounded-lg border border-blue-200 px-2 py-1.5 text-xs font-semibold text-blue-600 transition-colors hover:bg-blue-50 whitespace-nowrap shrink-0"
                    title="分享给同事（对方接受后会复制一份到他的个人技能）"
                  >
                    分享
                  </button>
                  <button 
                    @click="openSkillDetail(skill.id)"
                    class="px-3 py-1.5 text-xs font-semibold border border-blue-200 text-blue-600 hover:bg-blue-50 hover:border-blue-300 rounded-lg transition-colors whitespace-nowrap shrink-0"
                  >
                    配置
                  </button>
                  <button 
                    @click="deleteSkill(skill.id)"
                    class="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors whitespace-nowrap shrink-0"
                    title="注销并彻底删除技能目录"
                  >
                    <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                    </svg>
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    </template>
        </template>
      </div>
    </div>

    <!-- 技能工作台设计规范与全流程指引 Modal -->
    <Teleport to="body">
      <div v-if="showHelpModal" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4" @click.self="showHelpModal = false">
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="skill-help-title"
          class="bg-white rounded-2xl shadow-2xl w-full max-w-5xl h-[85vh] flex flex-col overflow-hidden border border-gray-100 animate-fade-in-up"
        >
          <!-- Header -->
          <div class="p-6 border-b border-gray-100 flex justify-between items-center bg-emerald-50/30">
            <div class="flex items-center gap-3">
               <div class="w-10 h-10 rounded-xl bg-emerald-600 text-white flex items-center justify-center shadow-md shadow-emerald-500/20" style="background-color: #059669; color: #ffffff;">
                  <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"/></svg>
               </div>
               <div>
                 <h2 id="skill-help-title" class="text-xl font-bold text-gray-900">技能工作台研发规范与全流程指引</h2>
                 <p class="text-xs text-gray-500 font-medium mt-0.5">从 SKILL.md 规范定义、代码脚本沉淀，到发布审批、平台多版本隔离与智能体动态装配激活。</p>
               </div>
            </div>
            <div class="flex items-center gap-3">
              <button
                v-if="!showSkillFlowGuide"
                type="button"
                @click="restoreSkillFlowGuide"
                class="inline-flex items-center gap-1 text-xs font-medium text-emerald-700 bg-emerald-100/70 hover:bg-emerald-200 px-3 py-1.5 rounded-lg transition-colors cursor-pointer"
              >
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                <span>恢复顶部流程提示</span>
              </button>
              <button @click="showHelpModal = false" class="text-gray-400 hover:text-gray-600 transition-colors cursor-pointer">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
              </button>
            </div>
          </div>

          <!-- Tabs -->
          <div class="flex border-b border-gray-200 bg-white px-6">
             <button 
               v-for="tab in ['flow', 'schema', 'audit']" 
               :key="tab"
               @click="activeHelpTab = tab as any"
               class="px-4 py-3 text-sm font-medium border-b-2 transition-colors cursor-pointer"
               :class="activeHelpTab === tab ? 'border-emerald-600 text-emerald-700' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'"
             >
               {{ tab === 'flow' ? '全流程指引 (Workflow)' :
                  tab === 'schema' ? 'SKILL.md 规范与生态安装 (Schema & Install)' : '审批发布与多版本隔离 (Audit & Versioning)' }}
             </button>
          </div>

          <!-- Content -->
          <div class="flex-1 overflow-y-auto p-6 sm:p-8 bg-gray-50/50">
             <!-- Tab 1: Workflow Flow -->
             <div v-if="activeHelpTab === 'flow'" class="space-y-6 max-w-4xl mx-auto">
                <div class="bg-gradient-to-r from-emerald-50 to-teal-50 border-l-4 border-emerald-600 p-4 rounded-r-xl shadow-sm">
                   <h3 class="font-bold text-emerald-900 mb-1">技能工作台 5 步全生命周期标准体系</h3>
                   <p class="text-xs text-emerald-700 leading-relaxed">
                      Skills 是面向 AI 智能体的一套高阶执行规范与指令约束。通过 SKILL.md 定义与脚本编写，使智能体具备复杂的工具链执行能力。
                   </p>
                </div>

                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                   <!-- Step 1 -->
                   <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                      <div>
                         <div class="flex items-center gap-2 mb-2">
                            <span class="w-6 h-6 rounded-full bg-emerald-600 text-white flex items-center justify-center text-xs font-bold">1</span>
                            <h4 class="font-bold text-gray-900 text-sm">技能创建与目录规划</h4>
                         </div>
                         <p class="text-xs text-gray-500 leading-relaxed">
                            设定英文 ID、中文名称与业务描述（Description 是大模型识别意图的关键）；自动生成标准化入口 SKILL.md。
                         </p>
                      </div>
                      <div class="mt-4 pt-3 border-t border-gray-100 flex items-center justify-end gap-2">
                         <button
                            type="button"
                            @click="showHelpModal = false; openCreateModal()"
                            class="text-xs text-emerald-600 hover:text-emerald-800 font-bold cursor-pointer"
                         >
                            新建技能 &rarr;
                         </button>
                      </div>
                   </div>

                   <!-- Step 2 -->
                   <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                      <div>
                         <div class="flex items-center gap-2 mb-2">
                            <span class="w-6 h-6 rounded-full bg-blue-600 text-white flex items-center justify-center text-xs font-bold">2</span>
                            <h4 class="font-bold text-gray-900 text-sm">脚本编写与沙箱调试</h4>
                         </div>
                         <p class="text-xs text-gray-500 leading-relaxed">
                            在内置工作台编写多级文件树（scripts/ 脚本、resources/ 模板、references/ 规范）；支持 Markdown 在线渲染。
                         </p>
                      </div>
                      <div class="mt-4 pt-3 border-t border-gray-100 flex justify-end text-xs text-gray-400">
                         点击技能卡片进入文件树编辑
                      </div>
                   </div>

                   <!-- Step 3 -->
                   <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                      <div>
                         <div class="flex items-center gap-2 mb-2">
                            <span class="w-6 h-6 rounded-full bg-amber-600 text-white flex items-center justify-center text-xs font-bold">3</span>
                            <h4 class="font-bold text-gray-900 text-sm">提审申请与平台合规</h4>
                         </div>
                         <p class="text-xs text-gray-500 leading-relaxed">
                            个人技能成熟后在抽屉中点击「申请发布至平台」；管理员在「待审核」Tab 下审查文件 Diff 与代码合规性。
                         </p>
                      </div>
                      <div class="mt-4 pt-3 border-t border-gray-100 flex items-center justify-end gap-2">
                         <button
                            v-if="canAccessPlatformWorkspace"
                            type="button"
                            @click="showHelpModal = false; handleSkillBannerAction('review')"
                            class="text-xs text-amber-600 hover:text-amber-800 font-bold cursor-pointer"
                         >
                            前往待审核 &rarr;
                         </button>
                         <span v-else class="text-[11px] text-gray-400">仅平台技能管理员可访问「待审核」</span>
                      </div>
                   </div>

                   <!-- Step 4 -->
                   <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between">
                      <div>
                         <div class="flex items-center gap-2 mb-2">
                            <span class="w-6 h-6 rounded-full bg-purple-600 text-white flex items-center justify-center text-xs font-bold">4</span>
                            <h4 class="font-bold text-gray-900 text-sm">平台发布与版本隔离</h4>
                         </div>
                         <p class="text-xs text-gray-500 leading-relaxed">
                            审批通过后晋升为平台公共技能，对全体业务角色公开；严格实现生产发布版本与开发草稿隔离。
                         </p>
                      </div>
                      <div class="mt-4 pt-3 border-t border-gray-100 flex justify-end">
                         <button
                            v-if="canAccessPlatformWorkspace"
                            type="button"
                            @click="showHelpModal = false; handleSkillBannerAction('global')"
                            class="text-xs text-purple-600 hover:text-purple-800 font-medium cursor-pointer"
                         >
                            平台技能 &rarr;
                         </button>
                         <span v-else class="text-[11px] text-gray-400">仅平台技能管理员可访问「平台技能」</span>
                      </div>
                   </div>

                   <!-- Step 5 -->
                   <div class="bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between md:col-span-2">
                      <div>
                         <div class="flex items-center gap-2 mb-2">
                            <span class="w-6 h-6 rounded-full bg-teal-600 text-white flex items-center justify-center text-xs font-bold">5</span>
                            <h4 class="font-bold text-gray-900 text-sm">智能体绑定与动态激活</h4>
                         </div>
                         <p class="text-xs text-gray-500 leading-relaxed">
                            在智能体中心为目标助手显式勾选挂载该技能，或由大模型意图感知动态加载；支持在工作台中查看智能体绑定分布。
                         </p>
                      </div>
                      <div class="mt-4 pt-3 border-t border-gray-100 flex justify-end">
                         <button
                            v-if="canAccessPlatformWorkspace"
                            type="button"
                            @click="showHelpModal = false; router.push('/dashboard/agent-management')"
                            class="text-xs text-teal-600 hover:text-teal-800 font-medium cursor-pointer"
                         >
                            前往智能体中心 &rarr;
                         </button>
                         <span v-else class="text-[11px] text-gray-400">智能体中心需要平台管理权限</span>
                      </div>
                   </div>
                </div>
             </div>

             <!-- Tab 2: Schema & Install -->
             <div v-else-if="activeHelpTab === 'schema'" class="space-y-4 max-w-4xl mx-auto">
                <div class="bg-white p-6 rounded-2xl border border-gray-200 shadow-sm space-y-4 text-sm text-gray-600 leading-relaxed">
                   <h4 class="font-bold text-gray-900 text-base">SKILL.md 规范与三种安装方式</h4>
                 
                   <!-- SKILL.md 结构 -->
                   <div class="p-4 bg-emerald-50/50 rounded-xl border border-emerald-100 space-y-2 text-xs">
                      <span class="font-bold text-emerald-900 text-sm">📄 SKILL.md YAML Frontmatter 规范</span>
                      <pre class="bg-slate-900 text-slate-100 p-3 rounded-lg font-mono text-[11px] overflow-x-auto leading-relaxed">---
  name: doc-reviewer
  description: 专门审查 Markdown 与技术文档的格式与结构守则。当用户需要校对文档或生成规范报告时调用。
  ---
  # 执行步骤与指示
  1. 检查文档各级标题层级...
  2. 运行 scripts/lint.py 校验规范...</pre>
                   </div>

                   <!-- 安装方式详细说明：保留旧版 CLI、Git、压缩包安装信息，同时沿用当前 schema Tab。 -->
                   <div class="space-y-4 text-xs">
                      <section class="rounded-xl border border-blue-100 bg-blue-50/50 p-4 dark:border-blue-900/30 dark:bg-blue-950/10">
                         <h5 class="flex items-center gap-2 font-bold text-blue-900 dark:text-blue-200">
                            <span class="h-3 w-1.5 rounded-full bg-blue-500"></span>
                            <span>什么是 Skills 技能</span>
                         </h5>
                         <p class="mt-2 pl-3.5 leading-relaxed text-gray-600 dark:text-gray-400">
                            Skills 是面向 AI 智能体的一套高阶执行规范与指令约束。装载后，大模型可以感知技能内部的脚本、API 动作与业务守则，完成网页渲染、子进程管理、沙箱 SQL 分析等任务。
                         </p>
                      </section>

                      <section class="rounded-xl border border-gray-200 bg-gray-50/80 p-4 dark:border-gray-700 dark:bg-gray-800/40">
                         <div class="flex items-start justify-between gap-3">
                            <h5 class="flex items-center gap-2 font-bold text-gray-900 dark:text-gray-100">
                               <span class="h-3 w-1.5 rounded-full bg-blue-500"></span>
                               <span>平台全局技能安装 (npx CLI)</span>
                            </h5>
                            <button
                              type="button"
                              @click="copyCommand"
                              class="shrink-0 rounded-md px-2 py-1 text-[11px] font-medium text-emerald-700 transition-colors hover:bg-emerald-100 hover:text-emerald-900 dark:text-emerald-300 dark:hover:bg-emerald-950/40"
                            >
                              {{ commandCopied ? '✓ 已复制' : '复制命令' }}
                            </button>
                         </div>
                         <p class="mt-2 pl-3.5 leading-relaxed text-gray-600 dark:text-gray-400">
                            平台会扫描运行环境中的全局技能目录：本地默认是 <code class="rounded bg-white px-1 py-0.5 font-mono text-blue-600 dark:bg-gray-900 dark:text-blue-300">~/.agents/skills</code>，Docker 环境默认是 <code class="rounded bg-white px-1 py-0.5 font-mono text-blue-600 dark:bg-gray-900 dark:text-blue-300">/app/data/skills</code>。请在与平台共享该目录的宿主机环境中执行安装命令。
                         </p>
                         <div class="mt-3 rounded-xl border border-slate-800 bg-slate-950 p-3 font-mono text-[11px] text-slate-200 shadow-inner sm:text-xs">
                            <div class="overflow-x-auto whitespace-nowrap select-all leading-relaxed">
                               <span class="text-slate-500">$</span> npx skills add &lt;仓库地址&gt; --skill &lt;skill-id&gt;
                            </div>
                            <div class="mt-2 border-t border-slate-800 pt-2 text-[10px] leading-relaxed text-slate-400">
                               示例：npx skills add https://github.com/vercel-labs/skills --skill find-skills
                            </div>
                         </div>
                         <p class="mt-2 pl-3.5 leading-relaxed text-gray-500 dark:text-gray-400">
                            也可以直接复制上面的示例命令。安装完成后，回到工作台刷新列表；技能目录必须包含有效的 <code>SKILL.md</code> 才会被平台识别。
                         </p>
                      </section>

                      <section class="rounded-xl border border-gray-200 bg-gray-50/80 p-4 dark:border-gray-700 dark:bg-gray-800/40">
                         <h5 class="flex items-center gap-2 font-bold text-gray-900 dark:text-gray-100">
                            <span class="h-3 w-1.5 rounded-full bg-indigo-500"></span>
                            <span>个人专属技能克隆 (Git clone)</span>
                         </h5>
                         <p class="mt-2 pl-3.5 leading-relaxed text-gray-600 dark:text-gray-400">
                            如果要为某个用户安装个人技能，可以将仓库克隆到该用户隔离的技能目录。当前个人技能目录结构为：
                         </p>
                         <code class="mt-2 block overflow-x-auto rounded-lg bg-white p-2.5 font-mono text-[10px] text-gray-700 dark:bg-gray-900 dark:text-gray-300">
                            git clone &lt;仓库地址&gt; data/agent_workspaces/{user_key}/skills/{skill_id}
                         </code>
                         <p class="mt-2 pl-3.5 leading-relaxed text-gray-500 dark:text-gray-400">
                            Docker 环境对应 <code>/app/data/agent_workspaces/{user_key}/skills/{skill_id}</code>。其中 <code>{user_key}</code> 和 <code>{skill_id}</code> 需要替换为实际值，且技能目录中应包含入口文件 <code>SKILL.md</code>。
                         </p>
                      </section>

                      <section class="rounded-xl border border-gray-200 bg-gray-50/80 p-4 dark:border-gray-700 dark:bg-gray-800/40">
                         <h5 class="flex items-center gap-2 font-bold text-gray-900 dark:text-gray-100">
                            <span class="h-3 w-1.5 rounded-full bg-amber-500"></span>
                            <span>压缩包导入规范</span>
                         </h5>
                         <p class="mt-2 pl-3.5 leading-relaxed text-gray-600 dark:text-gray-400">
                            在技能工作台选择对应的「平台技能」或「我的技能」作用域，点击「导入技能 (Zip/Tar)」上传压缩文件，系统会自动解压并读取技能元数据。
                         </p>
                         <p class="mt-2 pl-3.5 leading-relaxed text-gray-600 dark:text-gray-400">
                            <strong class="text-amber-700 dark:text-amber-300">导入限制：</strong>压缩包根目录，或仅包裹一层的子目录中，必须包含核心指令定义文件 <code class="rounded bg-white px-1 py-0.5 font-mono text-blue-600 dark:bg-gray-900 dark:text-blue-300">SKILL.md</code>；缺少该文件时，系统会拒绝导入并清理已解压内容。
                         </p>
                         <h6 class="mt-3 font-bold text-gray-800 dark:text-gray-200">覆盖模式与安全策略</h6>
                         <p class="mt-1 pl-3.5 leading-relaxed text-gray-600 dark:text-gray-400">
                            如果技能 ID 已存在，只有开启「允许覆盖原有技能」后才会覆盖导入；覆盖时系统会先清理同名物理目录，再重新解压写入。个人技能仅对当前用户可见，平台技能需遵循平台权限和审核流程。
                         </p>
                      </section>

                      <section class="rounded-xl border border-red-100 bg-red-50/60 p-4 dark:border-red-900/30 dark:bg-red-950/10">
                         <h5 class="flex items-center gap-2 font-bold text-red-900 dark:text-red-200">
                            <span class="h-3 w-1.5 rounded-full bg-red-500"></span>
                            <span>安装前安全检查</span>
                         </h5>
                         <p class="mt-2 pl-3.5 leading-relaxed text-gray-600 dark:text-gray-400">
                            安装前确认第三方来源和脚本内容，重点检查 <code>SKILL.md</code> 中的执行指令以及 <code>scripts/</code>、<code>resources/</code> 等目录。第三方 Skill 可能包含可执行脚本，请确认来源可信后再安装到平台技能目录。
                         </p>
                      </section>
                   </div>

                   <div class="flex flex-col gap-3 border-t border-gray-100 pt-4 dark:border-gray-800 sm:flex-row sm:items-center sm:justify-between">
                      <span class="text-[11px] text-gray-400">想寻找更多好用的 AI 技能？前往官方市场发现并下载</span>
                      <a
                        href="https://www.skills.sh/"
                        target="_blank"
                        rel="noopener noreferrer"
                        class="market-link inline-flex items-center justify-center gap-1.5 rounded-xl px-4 py-2 text-center text-xs font-bold shadow-md shadow-blue-500/20 transition-all active:scale-95"
                      >
                         <span>💡 前往官方 Skills 开放市场</span>
                         <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4M14 4h6m0 0v6m0-6L10 14" />
                         </svg>
                      </a>
                   </div>
                </div>
             </div>

             <!-- Tab 3: Audit & Versioning -->
             <div v-else-if="activeHelpTab === 'audit'" class="space-y-4 max-w-4xl mx-auto">
                <div class="bg-white p-6 rounded-2xl border border-gray-200 shadow-sm space-y-4 text-sm text-gray-600 leading-relaxed">
                   <h4 class="font-bold text-gray-900 text-base">发布审批流程与生产版本隔离机制</h4>
                   <div class="space-y-3 text-xs">
                      <div class="p-3.5 bg-gray-50 rounded-xl border border-gray-200 space-y-1">
                         <span class="font-bold text-gray-900">1. 个人开发沙箱（我的技能）</span>
                         <p class="text-gray-600 leading-relaxed">每个用户的技能相互隔离在独立存储空间中，开发与调试过程完全不影响平台公共技能和其他用户。</p>
                      </div>
                      <div class="p-3.5 bg-gray-50 rounded-xl border border-gray-200 space-y-1">
                         <span class="font-bold text-gray-900">2. 提审发布与 Diff 对比</span>
                         <p class="text-gray-600 leading-relaxed">点击「申请发布至平台」生成发布工单；平台管理员在「待审核」中对文件树变动、代码执行风险与 YAML 描述进行严格核验。</p>
                      </div>
                      <div class="p-3.5 bg-gray-50 rounded-xl border border-gray-200 space-y-1">
                         <span class="font-bold text-gray-900">3. 生产发布与热更新</span>
                         <p class="text-gray-600 leading-relaxed">审核通过后，技能自动复制并归档至平台全局目录，版本号自增；绑定的智能体即刻生效，无需重启服务。</p>
                      </div>
                   </div>
                </div>
             </div>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 新建技能 Modal -->
    <Teleport to="body">
      <div 
        v-if="showCreateModal" 
        class="fixed inset-0 bg-black/50 backdrop-blur-sm z-[9990] flex items-center justify-center p-4 animate-fade-in"
        @click.self="showCreateModal = false"
      >
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="skill-create-title"
          class="bg-white rounded-2xl shadow-2xl max-w-md w-full p-6 border border-gray-200"
        >
          <h2 id="skill-create-title" class="text-xl font-bold text-gray-800 mb-1 flex items-center gap-2">
            <svg class="w-5 h-5" :class="activeScope === 'personal' ? 'text-emerald-500' : 'text-blue-500'" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5S19.832 5.477 21 6.253v13C19.832 18.477 18.246 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
            </svg>
            初始化智能体技能
          </h2>
          <p class="text-xs text-gray-500 mb-4">
            将创建到<strong>{{ activeScope === 'personal' ? '我的技能（个人）' : '平台技能（全局）' }}</strong>目录
          </p>
          <div class="space-y-4">
            <div>
              <label class="block text-xs font-bold text-gray-500 uppercase mb-1">
                唯一识别 ID (英文标识) <span class="text-red-500">*</span>
              </label>
              <input 
                v-model="newSkill.id"
                type="text" 
                placeholder="例如: search-helper" 
                class="w-full px-3 py-2 border border-gray-300 rounded-xl focus:ring-2 focus:outline-none text-sm transition-all"
                :class="activeScope === 'personal' ? 'focus:ring-emerald-500' : 'focus:ring-blue-500'"
              />
              <span class="text-[10px] text-gray-400 mt-1 block">物理目录名，创建后不可修改</span>
            </div>

            <div>
              <label class="block text-xs font-bold text-gray-500 uppercase mb-1">
                技能名称 <span class="text-red-500">*</span>
              </label>
              <input 
                v-model="newSkill.name"
                type="text" 
                placeholder="例如: 全局网页搜索辅助" 
                class="w-full px-3 py-2 border border-gray-300 rounded-xl focus:ring-2 focus:outline-none text-sm transition-all"
                :class="activeScope === 'personal' ? 'focus:ring-emerald-500' : 'focus:ring-blue-500'"
              />
            </div>

            <div>
              <label class="block text-xs font-bold text-gray-500 uppercase mb-1">
                描述说明
              </label>
              <textarea 
                v-model="newSkill.description"
                rows="3"
                placeholder="描述此技能的核心能力或针对的典型任务约束..." 
                class="w-full px-3 py-2 border border-gray-300 rounded-xl focus:ring-2 focus:outline-none text-sm transition-all"
                :class="activeScope === 'personal' ? 'focus:ring-emerald-500' : 'focus:ring-blue-500'"
              ></textarea>
            </div>
          </div>

          <div class="flex justify-end gap-3 mt-6 border-t border-gray-100 pt-4">
            <button 
              @click="showCreateModal = false"
              class="px-4 py-2 border border-gray-300 text-gray-600 rounded-xl hover:bg-gray-50 transition-colors text-sm font-medium"
            >
              取消
            </button>
            <button 
              @click="createSkill"
              :disabled="creating"
              class="px-4 py-2 disabled:opacity-50 text-white rounded-xl transition-colors text-sm font-medium"
              :class="activeScope === 'personal' ? 'bg-emerald-600 hover:bg-emerald-700' : 'bg-blue-600 hover:bg-blue-700'"
            >
              {{ creating ? '创建中...' : (activeScope === 'personal' ? '创建到我的技能' : '创建到平台技能') }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
    <!-- 详情大抽屉 Drawer (从右侧滑出) -->
    <div 
      v-if="showDrawer"
      class="fixed inset-0 z-[9950] overflow-hidden"
    >
      <!-- 蒙层 -->
      <div 
        class="absolute inset-0 bg-black/30 backdrop-blur-[2px] transition-opacity"
        @click="requestCloseDrawer"
      ></div>

      <!-- 抽屉主体 -->
      <div class="absolute inset-y-0 right-0 pl-10 max-w-full flex">
        <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="skill-drawer-title"
        class="w-screen max-w-6xl bg-white shadow-2xl flex flex-col h-full border-l border-gray-200 animate-slide-in"
      >
          <!-- 抽屉头部 -->
          <div class="px-6 py-4 bg-gray-50 border-b border-gray-200 flex items-center justify-between">
            <div class="flex items-center space-x-3 min-w-0">
              <div class="w-8 h-8 rounded-lg bg-blue-100 text-blue-600 flex items-center justify-center flex-shrink-0">
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5S19.832 5.477 21 6.253v13C19.832 18.477 18.246 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                </svg>
              </div>
              <div class="min-w-0">
                <h2 id="skill-drawer-title" class="text-lg font-bold text-gray-800 truncate">{{ activeSkillName }}</h2>
                <p class="text-xs text-gray-500 font-mono truncate">ID: {{ activeSkillId }}</p>
              </div>
            </div>
            
            <button 
              @click="requestCloseDrawer"
              class="p-2 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100 transition-colors"
            >
              <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          <!-- 抽屉主要内容，左右分栏 -->
          <div class="flex-1 flex flex-col lg:flex-row overflow-y-auto lg:overflow-hidden min-h-0 bg-slate-50 dark:bg-gray-900">
            <!-- 左侧：在线编辑器 -->
            <div class="flex-[1.8] h-[60vh] lg:h-auto flex flex-col border-b lg:border-b-0 lg:border-r border-gray-200 p-3 sm:p-5 bg-white dark:bg-gray-800 overflow-hidden min-h-0">
              <div v-if="fetchingDetail" class="flex-1 flex flex-col items-center justify-center py-20">
                <div class="w-8 h-8 border-4 border-blue-600 border-t-transparent rounded-full animate-spin"></div>
                <p class="text-xs text-gray-400 mt-3 font-medium">载入文件内容...</p>
              </div>

              <div v-else class="skill-editor flex-1 flex flex-col min-h-0">
                <!-- 工具栏 -->
                <div class="skill-editor-toolbar">
                  <div class="skill-editor-file-info">
                    <span class="skill-editor-file-icon" :class="`ext-${selectedFileExtension}`">
                      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                      </svg>
                    </span>
                    <span class="skill-editor-filename" :title="selectedFilePath">{{ selectedFilePath }}</span>
                    <span v-if="hasUnsavedChanges" class="skill-editor-unsaved" title="有未保存的修改">未保存</span>
                  </div>

                  <div class="skill-editor-actions">
                    <div v-if="isMarkdownFile" class="skill-editor-mode-toggle">
                      <button
                        type="button"
                        @click="editorMode = 'edit'"
                        :class="{ active: editorMode === 'edit' }"
                      >
                        <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                        </svg>
                        编辑
                      </button>
                      <button
                        type="button"
                        @click="editorMode = 'preview'"
                        :class="{ active: editorMode === 'preview' }"
                      >
                        <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                        </svg>
                        预览
                      </button>
                    </div>

                    <button
                      type="button"
                      @click="saveFileContent"
                      :disabled="saving || !hasUnsavedChanges"
                      class="skill-editor-save-btn"
                      :class="{ 'is-dirty': hasUnsavedChanges, 'is-saving': saving }"
                      title="保存 (⌘S / Ctrl+S)"
                    >
                      <span v-if="saving" class="skill-editor-save-spinner"></span>
                      <svg v-else class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-3m-1 4l-3 3m0 0l-3-3m3 3V4" />
                      </svg>
                      {{ saving ? '保存中' : '保存' }}
                    </button>
                  </div>
                </div>

                <!-- 编辑区 -->
                <div class="skill-editor-body group/editor flex-1 overflow-hidden min-h-0 flex flex-col relative">
                  <button
                    type="button"
                    @click="copyEditorContent"
                    class="absolute top-1.5 right-2 p-1.5 text-gray-400 bg-white/90 dark:bg-gray-700/90 hover:bg-gray-100 dark:hover:bg-gray-600 hover:text-primary rounded-md opacity-0 group-hover/editor:opacity-100 transition-all z-10 shadow-sm border border-gray-100 dark:border-gray-600"
                    title="复制内容"
                    aria-label="复制内容"
                  >
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path></svg>
                  </button>

                  <div v-if="editorMode === 'edit'" class="skill-editor-code-pane flex-1 flex overflow-hidden min-h-0">
                    <div ref="lineNumbersRef" class="skill-editor-gutter overflow-hidden select-none">
                      <div v-for="n in lineCount" :key="n" class="skill-editor-line-num">{{ n }}</div>
                    </div>
                    <textarea
                      ref="editorTextareaRef"
                      v-model="editingContent"
                      @scroll="syncLineNumberScroll"
                      @keydown="handleEditorKeydown"
                      class="skill-editor-textarea flex-1 min-w-0"
                      spellcheck="false"
                      placeholder="在此输入文件内容..."
                    ></textarea>
                  </div>

                  <div
                    v-else
                    class="skill-editor-preview markdown-preview flex-1 overflow-y-auto"
                    v-html="renderedMarkdown"
                  ></div>
                </div>

                <!-- 状态栏 -->
                <div class="skill-editor-statusbar shrink-0">
                  <div class="skill-editor-status-left">
                    <span>{{ isMarkdownFile ? 'Markdown' : selectedFileExtension.toUpperCase() }}</span>
                    <span class="skill-editor-status-sep">·</span>
                    <span>UTF-8</span>
                    <span v-if="hasUnsavedChanges" class="skill-editor-status-dirty">· 已修改</span>
                  </div>
                  <div class="skill-editor-status-right">
                    <span>{{ lineCount }} 行</span>
                    <span class="skill-editor-status-sep">·</span>
                    <span>{{ charCount }} 字符</span>
                  </div>
                </div>
              </div>
            </div>

            <!-- 右侧：文件资产与脚本上传 (占据 35%) -->
            <div class="flex-1 h-[40vh] lg:h-auto flex flex-col p-4 sm:p-6 overflow-y-auto bg-white dark:bg-gray-800">
              <!-- 隐藏的物理资产上传接收器 -->
              <input 
                type="file" 
                id="file-upload-input" 
                multiple 
                @change="handleFileUpload" 
                :accept="uploadType === 'archive' ? '.zip,.tar,.gz,.tgz,.bz2' : '*'"
                class="hidden"
              />

              <div class="mb-3 flex flex-wrap items-center justify-between gap-x-3 gap-y-2 select-none shrink-0">
                <h3 class="shrink-0 whitespace-nowrap text-xs font-bold text-gray-400 tracking-wider" title="技能资产物理树：文件与空白区域均支持右键菜单操作">技能资产物理树</h3>
                <div class="flex shrink-0 items-center gap-1.5">
                  <div class="flex shrink-0 items-center rounded-lg border border-slate-200 bg-white p-0.5">
                    <button
                      type="button"
                      @click="fileTreeSortBy = 'name'"
                      class="whitespace-nowrap rounded-md px-1.5 py-1 text-[10px] font-medium transition-all"
                      :class="fileTreeSortBy === 'name' ? 'bg-slate-100 text-slate-700' : 'text-slate-400 hover:text-slate-600'"
                    >
                      名称
                    </button>
                    <button
                      type="button"
                      @click="fileTreeSortBy = 'time'"
                      class="whitespace-nowrap rounded-md px-1.5 py-1 text-[10px] font-medium transition-all"
                      :class="fileTreeSortBy === 'time' ? 'bg-slate-100 text-slate-700' : 'text-slate-400 hover:text-slate-600'"
                    >
                      时间
                    </button>
                    <button
                      type="button"
                      @click="toggleFileTreeSortOrder"
                      class="shrink-0 rounded-md p-1 text-slate-400 transition-all hover:text-slate-600"
                      :title="fileTreeSortOrder === 'asc' ? '升序' : '降序'"
                    >
                      <svg class="h-3.5 w-3.5 transition-transform" :class="fileTreeSortOrder === 'desc' ? 'rotate-180' : ''" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4h13M3 8h9m-9 4h6m4 0v4m0 0l-3-3m3 3l3-3" />
                      </svg>
                    </button>
                  </div>
                  <button
                    type="button"
                    @click="createAssetFromDrawer"
                    class="shrink-0 whitespace-nowrap rounded-lg border border-slate-200 bg-white px-2 py-1 text-[10px] font-semibold text-slate-600 shadow-sm transition-colors hover:border-slate-300 hover:text-slate-800"
                    title="在当前定位目录下新建文件"
                  >新建文件</button>
                  <button
                    type="button"
                    :disabled="uploading"
                    @click="uploadFromDrawer"
                    class="shrink-0 whitespace-nowrap rounded-lg border border-blue-200 bg-white px-2 py-1 text-[10px] font-semibold text-blue-600 shadow-sm transition-colors hover:border-blue-300 hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-60"
                    :title="uploading ? '正在上传…' : '上传脚本文件到当前定位目录'"
                  >{{ uploading ? '上传中…' : '上传脚本' }}</button>
                </div>
              </div>

              <!-- 精致路径定位面包屑 -->
              <div class="mb-3 flex items-center justify-between bg-slate-50 border border-slate-200/80 rounded-xl px-3 py-2 text-xs font-medium text-slate-700 shadow-sm shrink-0">
                <div class="flex items-center gap-1.5 truncate">
                  <svg class="w-3.5 h-3.5 text-slate-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" />
                  </svg>
                  <span class="text-slate-400 text-[10px] uppercase font-bold tracking-wider">定位目录:</span>
                  <span class="font-mono text-[11px] text-slate-600 truncate">
                    {{ selectedDirectoryPath || '技能根目录' }}
                  </span>
                </div>
                
                <button
                  v-if="selectedDirectoryPath !== ''"
                  @click="selectedDirectoryPath = ''"
                  class="text-slate-400 hover:text-slate-600 p-0.5 rounded-md hover:bg-slate-200/60 transition-colors"
                  title="清除定位并定位回根目录"
                >
                  <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>

              <!-- 文件树渲染 -->
              <div @contextmenu.self.prevent="handleEmptyAreaContextMenu" class="flex-1 flex flex-col border border-gray-200 rounded-2xl p-4 bg-gray-50/50 overflow-y-auto min-h-[180px] custom-scrollbar mb-3">
                <div v-if="fileTree.length === 0" class="text-center py-10 text-xs text-gray-400 italic flex-1 flex items-center justify-center">
                  暂无任何技能物理资产文件
                </div>
                <SkillFileTree 
                  v-else
                  :tree-data="fileTree" 
                  :sort-by="fileTreeSortBy"
                  :sort-order="fileTreeSortOrder"
                  :selected-path="selectedFilePath"
                  :selected-directory-path="selectedDirectoryPath"
                  @select-file="selectFileForEdit"
                  @select-directory="selectDirectory"
                  @delete-file="deleteSkillFile"
                  @context-menu="handleContextMenu"
                />
              </div>

              <!-- 当前技能近 30 天调用趋势 -->
              <div class="shrink-0 rounded-2xl border border-gray-200 bg-white p-3 shadow-sm">
                <div class="flex items-center justify-between mb-2">
                  <div class="flex items-center gap-1.5 min-w-0">
                    <h4 class="text-xs font-bold text-gray-500 uppercase tracking-wider">近 30 天调用</h4>
                    <span class="text-[10px] text-gray-400 truncate">{{ activeSkillId }}</span>
                  </div>
                  <div class="flex items-center gap-2 text-[10px] font-mono text-slate-500">
                    <span class="px-1.5 py-0.5 rounded bg-blue-50 text-blue-600 font-bold">30天 {{ activeSkill30DayTotal }}</span>
                    <span class="px-1.5 py-0.5 rounded bg-slate-50 text-slate-500">累计 {{ activeSkillAllTimeTotal }}</span>
                  </div>
                </div>
                <div v-if="loadingSkillDrawerStats" class="h-[140px] flex items-center justify-center text-[11px] text-gray-400">
                  加载调用统计...
                </div>
                <div
                  v-else-if="activeSkill30DayTotal === 0 && activeSkillAllTimeTotal === 0"
                  class="h-[140px] flex flex-col items-center justify-center text-[11px] text-gray-400 gap-1"
                >
                  <svg class="w-5 h-5 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3 3v18h18M7 14l4-4 3 3 5-6" />
                  </svg>
                  暂无调用记录
                </div>
                <div v-else ref="skillDrawerChartRef" class="w-full h-[140px]"></div>
              </div>

            </div>
          </div>
        </div>
      </div>
    </div>
    </Teleport>
  </div>

  <!-- 新建技能资产 Modal -->
  <Teleport to="body">
    <div
      v-if="showCreateAssetModal"
      class="fixed inset-0 bg-black/45 backdrop-blur-sm z-[9995] flex items-center justify-center p-4 animate-fade-in"
      @click.self="showCreateAssetModal = false"
    >
      <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="skill-asset-title"
          class="bg-white rounded-2xl shadow-2xl w-full max-w-sm p-5 border border-gray-200"
        >
        <div class="flex items-center justify-between mb-4">
          <h2 id="skill-asset-title" class="text-base font-bold text-gray-800">
            新建{{ createAssetType === 'file' ? '文件' : '文件夹' }}
          </h2>
          <button
            @click="showCreateAssetModal = false"
            class="p-1 text-gray-400 hover:text-gray-600 rounded-md hover:bg-gray-100"
            aria-label="关闭"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div class="mb-4 px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg">
          <p class="text-[10px] text-gray-400 font-bold mb-1">创建位置</p>
          <p class="text-xs text-slate-600 font-mono break-all">{{ createAssetTargetLabel }}</p>
        </div>

        <label class="block text-xs font-bold text-gray-600 mb-1.5">
          {{ createAssetType === 'file' ? '文件名（需包含文本扩展名）' : '文件夹名称' }}
        </label>
        <input
          v-model="createAssetName"
          type="text"
          :placeholder="createAssetType === 'file' ? '例如: guide.md' : '例如: references'"
          class="w-full px-3 py-2 border border-gray-300 rounded-xl focus:ring-2 focus:ring-blue-500 focus:outline-none text-sm font-mono"
          @keyup.enter="createSkillAsset"
        />
        <p class="mt-1.5 text-[10px] text-gray-400">请输入单级名称，不支持 /、\ 或隐藏名称。</p>

        <div class="flex justify-end gap-2 mt-5 pt-4 border-t border-gray-100">
          <button
            @click="showCreateAssetModal = false"
            class="px-3 py-1.5 text-xs font-medium text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50"
          >
            取消
          </button>
          <button
            @click="createSkillAsset"
            :disabled="creatingAsset"
            class="px-3 py-1.5 text-xs font-bold text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded-lg"
          >
            {{ creatingAsset ? '创建中...' : '确认创建' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- 导入技能包 Modal -->
  <Teleport to="body">
    <div
      v-if="showImportModal"
      class="fixed inset-0 bg-black/45 backdrop-blur-sm z-[9995] flex items-center justify-center p-4 animate-fade-in"
      @click.self="showImportModal = false"
    >
      <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="skill-import-title"
          class="bg-white rounded-2xl shadow-2xl w-full max-w-md p-6 border border-gray-200 relative"
        >
        <div class="flex items-center justify-between mb-2">
          <h2 id="skill-import-title" class="text-base font-bold text-gray-800 flex items-center gap-1.5">
            <svg class="w-5 h-5 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
            </svg>
            导入智能体技能压缩包
          </h2>
          <button
            @click="showImportModal = false"
            class="p-1 text-gray-400 hover:text-gray-600 rounded-md hover:bg-gray-100"
            aria-label="关闭"
          >
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <p class="text-xs text-gray-500 mb-4">
          将导入至<strong>{{ activeScope === 'personal' ? '我的技能（个人）' : '平台技能（全局）' }}</strong>目录
        </p>

        <!-- 拖拽上传区域 -->
        <div
          @dragover="handleImportDragOver"
          @dragleave="handleImportDragLeave"
          @drop="handleImportDrop"
          @click="openImportFilePicker"
          class="border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-all flex flex-col items-center justify-center"
          :class="[
            importDragActive 
              ? 'border-blue-500 bg-blue-50/50' 
              : 'border-gray-300 hover:border-blue-400 hover:bg-gray-50'
          ]"
        >
          <input
            type="file"
            ref="importFileInput"
            accept=".zip,.tar,.gz,.tgz,.bz2"
            @change="handleImportFileChange"
            class="hidden"
          />
        
          <template v-if="!importFile">
            <svg class="w-10 h-10 text-gray-400 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
            </svg>
            <span class="text-xs text-gray-600 font-bold">拖拽压缩包至此，或点击选择文件</span>
            <span class="text-[10px] text-gray-400 mt-1.5">仅支持 .zip, .tar, .tar.gz, .tgz 等压缩文件，最大 {{ SKILL_PACKAGE_MAX_MB }}MB</span>
          </template>
        
          <template v-else>
            <svg class="w-10 h-10 text-blue-500 mb-3 animate-bounce" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            <span class="text-xs text-gray-800 font-bold truncate max-w-full px-4">{{ importFile.name }}</span>
            <span class="text-[10px] text-gray-500 mt-1">{{ (importFile.size / 1024 / 1024).toFixed(2) }} MB</span>
            <span class="text-[10px] text-blue-600 mt-2 hover:underline">重新选择文件</span>
          </template>
        </div>

        <!-- 选项配置 -->
        <div class="mt-4 flex items-center space-x-2 bg-slate-50 border border-slate-200 rounded-xl p-3 select-none">
          <input
            type="checkbox"
            id="import-overwrite-checkbox"
            v-model="importOverwrite"
            class="w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500 cursor-pointer"
          />
          <label for="import-overwrite-checkbox" class="text-xs font-semibold text-gray-700 cursor-pointer">
            覆盖已存在的同名技能 (Overwrite Existing)
          </label>
        </div>
        <p class="mt-1.5 text-[10px] text-gray-400">若技能包解压后根目录不含 <b>SKILL.md</b> 规范文档，系统将自动拒绝并回滚清理。</p>

        <!-- 操作按钮 -->
        <div class="flex justify-end gap-2 mt-5 pt-4 border-t border-gray-100">
          <button
            @click="showImportModal = false"
            class="px-4 py-2 text-xs font-semibold text-gray-600 border border-gray-300 rounded-xl hover:bg-gray-50"
          >
            取消
          </button>
          <button
            @click="submitImportSkill"
            :disabled="importingSkill || !importFile"
            class="px-4 py-2 text-xs font-bold text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:hover:bg-blue-600 rounded-xl flex items-center gap-1.5 transition-all shadow-sm"
          >
            <span v-if="importingSkill" class="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin"></span>
            {{ importingSkill ? '正在导入解压...' : '确认导入' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>

  <ConfirmModal
    v-if="confirmState.show"
    :title="confirmState.title"
    :message="confirmState.message"
    :type="confirmState.type"
    @confirm="confirmState.onConfirm"
    @cancel="confirmState.show = false"
  />

  <!-- 显式绑定智能体 Modal -->
  <Teleport to="body">
    <div
      v-if="showBindingsModal && bindingsModalSkill"
      class="fixed inset-0 z-[9990] flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm animate-fade-in"
      @click.self="closeBindingsModal"
    >
      <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="skill-bindings-title"
          class="flex max-h-[80vh] w-full max-w-md flex-col rounded-2xl border border-gray-200 bg-white p-5 shadow-2xl"
        >
        <div class="mb-4 flex shrink-0 items-start justify-between gap-3 border-b border-gray-100 pb-3">
          <div class="min-w-0">
            <h2 id="skill-bindings-title" class="truncate text-lg font-bold text-gray-800">显式绑定智能体</h2>
            <p class="mt-0.5 truncate text-xs text-gray-500">
              {{ bindingsModalSkill.name }}
              <span class="font-mono text-gray-400">· {{ bindingsModalSkill.id }}</span>
            </p>
          </div>
          <button type="button" class="shrink-0 text-gray-400 transition-colors hover:text-gray-600" @click="closeBindingsModal">
            <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div
          v-if="getBindingEntry(bindingsModalSkill.id).count === 0"
          class="flex flex-1 flex-col items-center justify-center py-12 text-center text-gray-400"
        >
          <p class="text-sm font-medium text-gray-500">暂无智能体显式绑定此技能</p>
          <p class="mt-1 text-xs text-gray-400">仅统计开启自定义 Skills 白名单的智能体</p>
        </div>
        <ul v-else class="custom-scrollbar flex-1 space-y-2 overflow-y-auto pr-1">
          <li
            v-for="agent in getBindingEntry(bindingsModalSkill.id).agents"
            :key="agent.id"
            class="flex items-center justify-between gap-3 rounded-xl border border-gray-100 bg-gray-50/60 px-3 py-2.5"
          >
            <div class="min-w-0">
              <p class="truncate text-sm font-semibold text-gray-800">{{ agent.name }}</p>
              <p class="mt-0.5 font-mono text-[10px] text-gray-400">v{{ agent.version_number }}</p>
            </div>
            <span
              class="shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold"
              :class="agent.version_status === 'DRAFT'
                ? 'bg-amber-50 text-amber-700'
                : 'bg-emerald-50 text-emerald-700'"
            >
              {{ agent.version_status === 'DRAFT' ? '草稿' : '已发布' }}
            </span>
          </li>
        </ul>

        <div class="mt-4 flex shrink-0 justify-end border-t border-gray-100 pt-3">
          <button
            type="button"
            class="rounded-xl bg-gray-100 px-4 py-2 text-sm font-semibold text-gray-700 transition-colors hover:bg-gray-200"
            @click="closeBindingsModal"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- 统计查看 Modal -->
  <Teleport to="body">
    <div 
      v-if="showStatsModal" 
      class="fixed inset-0 bg-black/50 backdrop-blur-sm z-[9990] flex items-center justify-center p-4 animate-fade-in"
      @click.self="closeStatsModal"
    >
      <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="skill-stats-title"
          class="bg-white rounded-2xl shadow-2xl max-w-4xl w-full p-6 border border-gray-200 flex flex-col max-h-[90vh]"
        >
        <div class="flex items-center justify-between border-b border-gray-100 pb-4 mb-4 select-none shrink-0">
          <h2 id="skill-stats-title" class="text-xl font-bold text-gray-800 flex items-center gap-2">
            <svg class="w-5 h-5 text-indigo-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
            </svg>
            技能激活与调用统计
          </h2>
          <button @click="closeStatsModal" class="text-gray-400 hover:text-gray-600 transition-colors">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div v-if="loadingStats" class="flex flex-col items-center justify-center py-20 flex-1">
          <div class="w-10 h-10 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin"></div>
          <p class="text-sm text-gray-500 mt-4 font-medium">正在拉取统计数据...</p>
        </div>
        <div v-else-if="!hasStatsData" class="flex flex-col items-center justify-center py-20 flex-1 text-gray-400 select-none">
          <svg class="w-16 h-16 text-gray-300 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0a2 2 0 01-2 2H6a2 2 0 01-2-2m16 0V9a2 2 0 00-2-2H6a2 2 0 00-2 2v4m16 0a2 2 0 01-2 2H6a2 2 0 01-2-2" />
          </svg>
          <p class="text-sm font-medium">暂无任何技能的激活和调用数据</p>
          <p class="text-xs text-gray-400 mt-1">在智能体对话中触发技能后，统计系统会自动开始记录</p>
        </div>
        <div v-else class="flex-1 flex flex-col min-h-[400px] overflow-hidden">
          <!-- Tab 切换菜单 -->
          <div class="flex border-b border-gray-200 mb-4 shrink-0 select-none gap-2">
            <button 
              @click="switchTab('distribution')"
              class="px-5 py-2.5 font-bold text-sm transition-all border-b-2 -mb-[2px]"
              :class="activeTab === 'distribution' ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-400 hover:text-gray-600'"
            >
              总量占比与分布
            </button>
            <button 
              @click="switchTab('trend')"
              class="px-5 py-2.5 font-bold text-sm transition-all border-b-2 -mb-[2px]"
              :class="activeTab === 'trend' ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-400 hover:text-gray-600'"
            >
              近30天激活趋势
            </button>
          </div>

          <div class="flex-1 overflow-y-auto pr-1 py-1 custom-scrollbar">
            <!-- 分布图 -->
            <div v-show="activeTab === 'distribution'" class="border border-gray-200 rounded-xl p-4 bg-gray-50/50 flex flex-col min-h-[380px]">
              <h3 class="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5 shrink-0 select-none">
                <span class="w-2 h-2 bg-blue-500 rounded-full"></span>
                技能总调用占比与分布
              </h3>
              <div ref="distributionChartRef" class="w-full flex-1 min-h-[320px]"></div>
            </div>
            <!-- 趋势图 -->
            <div v-show="activeTab === 'trend'" class="border border-gray-200 rounded-xl p-4 bg-gray-50/50 flex flex-col min-h-[380px]">
              <h3 class="text-sm font-bold text-gray-700 mb-3 flex items-center gap-1.5 shrink-0 select-none">
                <span class="w-2 h-2 bg-indigo-500 rounded-full"></span>
                近30天激活趋势变化
              </h3>
              <div ref="trendChartRef" class="w-full flex-1 min-h-[320px]"></div>
            </div>
          </div>
        </div>
      
        <div class="flex justify-end gap-3 mt-6 border-t border-gray-100 pt-4 shrink-0">
          <button 
            @click="closeStatsModal"
            class="px-5 py-2.5 bg-gray-100 text-gray-700 hover:bg-gray-200 transition-colors text-sm font-semibold rounded-xl"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- 重命名弹窗 -->
  <InputModal
    v-if="renameDialog"
    :title="`重命名技能「${renameDialog.skillId}」`"
    :message="'技能 ID 就是目录名，会一并同步智能体白名单、发布记录与会话挂载。'"
    :model-value="renameDialog.draft"
    :loading="renameSubmitting"
    placeholder="仅允许字母、数字、下划线、连字符，长度 1-128"
    hint="重命名后原 ID 立即失效；未能同步的引用会在完成后提示。"
    @update:model-value="renameDialog.draft = $event"
    @confirm="submitRename"
    @cancel="renameDialog = null"
  />

  <!-- 下架原因（必填）：先经 ConfirmModal 确认影响面，再强制填写原因后提交 -->
  <InputModal
    v-if="revokeTarget"
    :title="`下架「${revokeTarget.name || revokeTarget.skill_id}」v${revokeTarget.version_number ?? '—'}`"
    message="下架后该版本立即不再对用户可见，原因会记录在发布记录中供追溯。"
    :model-value="revokeComment"
    :loading="revokeSubmitting"
    :maxlength="REVOKE_COMMENT_MAX"
    placeholder="请填写下架原因（必填）"
    hint="原因必填，例如：内容有误 / 存在安全风险 / 已被新版本取代。"
    confirm-text="确认下架"
    @update:model-value="revokeComment = $event"
    @confirm="submitRevokePublication"
    @cancel="cancelRevokePublication"
  />

  <!-- 右键菜单 -->
  <div
    v-if="contextMenu.show"
    :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }"
    class="fixed z-[9999] bg-white border border-gray-200 rounded-lg shadow-lg py-1 w-48 text-xs text-gray-700 select-none animate-fade-in"
  >
    <!-- 文件夹专属操作 -->
    <template v-if="contextMenu.node?.is_dir">
      <button
        @click="handleMenuAction('new-file')"
        class="w-full text-left px-3.5 py-2 hover:bg-gray-50 flex items-center gap-2 transition-colors text-blue-600 font-medium"
      >
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 13h6m-3-3v6m5 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
        新建文件
      </button>
      <button
        @click="handleMenuAction('new-folder')"
        class="w-full text-left px-3.5 py-2 hover:bg-gray-50 flex items-center gap-2 transition-colors text-amber-700 font-medium"
      >
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 13h6m-3-3v6m-9 1V7a2 2 0 012-2h6l2 2h6a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z" />
        </svg>
        新建文件夹
      </button>
      <div class="border-t border-gray-100 my-1"></div>
      <button
        @click="handleMenuAction('upload-normal')"
        class="w-full text-left px-3.5 py-2 hover:bg-gray-50 flex items-center gap-2 transition-colors"
      >
        <svg class="w-3.5 h-3.5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
        </svg>
        上传普通文件
      </button>
      <button
        @click="handleMenuAction('upload-archive')"
        class="w-full text-left px-3.5 py-2 hover:bg-gray-50 flex items-center gap-2 transition-colors"
      >
        <svg class="w-3.5 h-3.5 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" />
        </svg>
        上传压缩包 (Zip/Tar)
      </button>
      <div v-if="contextMenu.node?.path !== ''" class="border-t border-gray-100 my-1"></div>
      <button
        v-if="contextMenu.node?.path !== ''"
        @click="handleMenuAction('delete')"
        class="w-full text-left px-3.5 py-2 hover:bg-gray-50 flex items-center gap-2 text-red-500 hover:bg-red-50 transition-colors"
      >
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
        </svg>
        删除此文件夹
      </button>
    </template>

    <!-- 普通文件专属操作 -->
    <template v-else>
      <button
        @click="handleMenuAction('edit')"
        class="w-full text-left px-3.5 py-2 hover:bg-gray-50 flex items-center gap-2 transition-colors font-medium text-slate-800"
      >
        <svg class="w-3.5 h-3.5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
        </svg>
        编辑此文件
      </button>
      <template v-if="contextMenu.node?.name.toLowerCase() !== 'skill.md'">
        <div class="border-t border-gray-100 my-1"></div>
        <button
          @click="handleMenuAction('delete')"
          class="w-full text-left px-3.5 py-2 hover:bg-gray-50 flex items-center gap-2 text-red-500 hover:bg-red-50 transition-colors"
        >
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
          </svg>
          删除此文件
        </button>
      </template>
    </template>
  </div>

  <!-- 卡片「⋯」操作菜单 -->
  <div v-if="actionMenu" class="fixed inset-0 z-[60]" @click="closeActionMenu"></div>
  <div
    v-if="actionMenu"
    role="menu"
    class="fixed z-[61] w-44 overflow-hidden rounded-xl border border-gray-100 bg-white py-1 shadow-xl"
    :style="actionMenu.top !== null
      ? { left: `${actionMenu.x}px`, top: `${actionMenu.top}px` }
      : { left: `${actionMenu.x}px`, bottom: `${actionMenu.bottom}px` }"
  >
    <button
      v-if="actionMenu.skill.scope === 'personal'"
      type="button"
      role="menuitem"
      class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs text-gray-700 transition-colors hover:bg-gray-50"
      @click="runSkillAction('share')"
    >
      <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 12v7a2 2 0 002 2h12a2 2 0 002-2v-7M16 6l-4-4-4 4M12 2v13" />
      </svg>
      分享给同事
    </button>
    <button
      type="button"
      role="menuitem"
      class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs text-gray-700 transition-colors hover:bg-gray-50"
      @click="runSkillAction('export')"
    >
      <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M12 4v12m0 0l-4-4m4 4l4-4" />
      </svg>
      导出 zip
    </button>
    <button
      type="button"
      role="menuitem"
      class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs text-gray-700 transition-colors hover:bg-gray-50"
      @click="runSkillAction('rename')"
    >
      <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
      </svg>
      重命名
    </button>
    <div class="my-1 border-t border-gray-100"></div>
    <button
      type="button"
      role="menuitem"
      class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-xs text-red-600 transition-colors hover:bg-red-50"
      @click="runSkillAction('delete')"
    >
      <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
      </svg>
      删除技能
    </button>
  </div>

  <SkillShareDialog
    :visible="!!shareDialogSkill"
    :skill="shareDialogSkill"
    @close="shareDialogSkill = null"
    @sent="handleShareChanged"
    @view-sent="shareDialogSkill = null; personalMenu = 'sent'"
  />
</template>

<style scoped>
/* 渐变流光与动画 */
@keyframes slideIn {
  from {
    transform: translateX(100%);
  }
  to {
    transform: translateX(0);
  }
}

.animate-slide-in {
  animation: slideIn 0.3s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}

@keyframes fadeIn {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

.animate-fade-in {
  animation: fadeIn 0.2s ease-out forwards;
}

/* 自定义滚动条 */
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

/* 仅编辑器内部隐藏焦点环；此前是全局 textarea:focus，会压掉其它输入框的 focus:ring */
.skill-editor-textarea:focus {
  outline: none;
  box-shadow: none;
}

/* Skill Editor (light theme) */
.skill-editor {
  border-radius: 12px;
  border: 1px solid #e2e8f0;
  background: #ffffff;
  overflow: hidden;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04), 0 4px 16px rgba(15, 23, 42, 0.04);
}

.skill-editor-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
}

.skill-editor-file-info {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

.skill-editor-file-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 7px;
  flex-shrink: 0;
  background: #eff6ff;
  color: #2563eb;
}

.skill-editor-file-icon.ext-md {
  background: #eff6ff;
  color: #2563eb;
}
.skill-editor-file-icon.ext-sh,
.skill-editor-file-icon.ext-bash {
  background: #f0fdf4;
  color: #16a34a;
}
.skill-editor-file-icon.ext-py {
  background: #fefce8;
  color: #ca8a04;
}
.skill-editor-file-icon.ext-json {
  background: #fff7ed;
  color: #ea580c;
}

.skill-editor-filename {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  font-weight: 500;
  color: #334155;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.skill-editor-unsaved {
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 600;
  color: #b45309;
  background: #fffbeb;
  border: 1px solid #fde68a;
  padding: 1px 7px;
  border-radius: 999px;
}

.skill-editor-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.skill-editor-mode-toggle {
  display: flex;
  align-items: center;
  padding: 2px;
  border-radius: 8px;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
}

.skill-editor-mode-toggle button {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 5px 10px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 500;
  color: #64748b;
  transition: all 0.15s ease;
}

.skill-editor-mode-toggle button:hover {
  color: #334155;
}

.skill-editor-mode-toggle button.active {
  background: #ffffff;
  color: #1e293b;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
}

.skill-editor-save-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 6px 14px;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 600;
  color: #94a3b8;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  transition: all 0.15s ease;
  cursor: not-allowed;
}

.skill-editor-save-btn.is-dirty {
  color: #fff;
  background: #2563eb;
  border-color: #3b82f6;
  cursor: pointer;
  box-shadow: 0 1px 2px rgba(37, 99, 235, 0.25);
}

.skill-editor-save-btn.is-dirty:hover {
  background: #1d4ed8;
}

.skill-editor-save-btn.is-dirty:active {
  transform: scale(0.97);
}

.skill-editor-save-btn.is-saving {
  cursor: wait;
  opacity: 0.8;
}

.skill-editor-save-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.skill-editor-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: #ffffff;
  position: relative;
}

.skill-editor-code-pane {
  flex: 1;
  display: flex;
  min-height: 0;
  overflow: hidden;
}

.skill-editor-gutter {
  width: 44px;
  flex-shrink: 0;
  padding: 12px 8px 12px 0;
  text-align: right;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.625rem;
  color: #94a3b8;
  background: #f8fafc;
  border-right: 1px solid #e2e8f0;
  overflow: hidden;
  user-select: none;
}

.skill-editor-line-num {
  height: 1.625rem;
}

.skill-editor-textarea {
  flex: 1;
  padding: 12px 44px 12px 16px;
  background: #ffffff;
  color: #1e293b;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
  line-height: 1.625rem;
  border: none;
  resize: none;
  overflow-y: auto;
  caret-color: #2563eb;
}

.skill-editor-textarea::placeholder {
  color: #cbd5e1;
}

.skill-editor-textarea::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

.skill-editor-textarea::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 4px;
}

.skill-editor-textarea::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}

.skill-editor-preview {
  flex: 1;
  overflow-y: auto;
  padding: 24px 52px 24px 32px;
  color: #1e293b;
  background: #ffffff;
}

.skill-editor-preview::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}

.skill-editor-preview::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 4px;
}

.skill-editor-preview::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}

.skill-editor-statusbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 5px 14px;
  background: #f8fafc;
  border-top: 1px solid #e2e8f0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 10px;
  color: #94a3b8;
  flex-shrink: 0;
  user-select: none;
}

.skill-editor-status-left,
.skill-editor-status-right {
  display: flex;
  align-items: center;
  gap: 6px;
}

.skill-editor-status-sep {
  opacity: 0.6;
}

.skill-editor-status-dirty {
  color: #d97706;
}

/* 生态市场外链按钮特殊强制渐变背景以防止被第三方库覆盖 */
.market-link {
  background: linear-gradient(135deg, #2563eb, #4f46e5) !important;
  color: #ffffff !important;
}
.market-link:hover {
  background: linear-gradient(135deg, #1d4ed8, #4338ca) !important;
  color: #ffffff !important;
}

/* Markdown Preview Styling (light theme) */
.markdown-preview :deep(h1) {
  font-size: 1.625rem;
  font-weight: 700;
  border-bottom: 1px solid #e2e8f0;
  padding-bottom: 0.5rem;
  margin-top: 0;
  margin-bottom: 1rem;
  color: #0f172a;
  letter-spacing: -0.01em;
}
.markdown-preview :deep(h2) {
  font-size: 1.25rem;
  font-weight: 600;
  margin-top: 1.5rem;
  margin-bottom: 0.75rem;
  color: #1e293b;
}
.markdown-preview :deep(h3) {
  font-size: 1.05rem;
  font-weight: 600;
  margin-top: 1.25rem;
  margin-bottom: 0.5rem;
  color: #334155;
}
.markdown-preview :deep(p) {
  margin-bottom: 0.85rem;
  line-height: 1.75;
  color: #475569;
}
.markdown-preview :deep(ul), .markdown-preview :deep(ol) {
  margin-left: 1.5rem;
  margin-bottom: 1rem;
  list-style-type: disc;
}
.markdown-preview :deep(ol) {
  list-style-type: decimal;
}
.markdown-preview :deep(li) {
  margin-bottom: 0.35rem;
  line-height: 1.65;
  color: #475569;
}
.markdown-preview :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.875em;
  background-color: #f1f5f9;
  padding: 0.15rem 0.4rem;
  border-radius: 0.25rem;
  color: #be185d;
  border: 1px solid #e2e8f0;
}
.markdown-preview :deep(pre) {
  background-color: #f8fafc;
  padding: 1rem 1.25rem;
  border-radius: 0.5rem;
  overflow-x: auto;
  margin-bottom: 1rem;
  border: 1px solid #e2e8f0;
}
.markdown-preview :deep(pre code) {
  background-color: transparent;
  padding: 0;
  border: none;
  color: #334155;
  font-size: 0.8125rem;
  line-height: 1.6;
}
.markdown-preview :deep(blockquote) {
  border-left: 3px solid #3b82f6;
  padding: 0.25rem 0 0.25rem 1rem;
  margin-left: 0;
  margin-right: 0;
  margin-bottom: 1rem;
  color: #64748b;
  background: #f8fafc;
  border-radius: 0 0.375rem 0.375rem 0;
}
.markdown-preview :deep(a) {
  color: #2563eb;
  text-decoration: none;
}
.markdown-preview :deep(a:hover) {
  text-decoration: underline;
}
.markdown-preview :deep(hr) {
  border: none;
  border-top: 1px solid #e2e8f0;
  margin: 1.5rem 0;
}
.markdown-preview :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 1rem;
  font-size: 0.875rem;
}
.markdown-preview :deep(th),
.markdown-preview :deep(td) {
  border: 1px solid #e2e8f0;
  padding: 0.5rem 0.75rem;
  text-align: left;
}
.markdown-preview :deep(th) {
  background: #f8fafc;
  font-weight: 600;
  color: #334155;
}
.markdown-preview :deep(.preview-empty) {
  color: #94a3b8;
  font-style: italic;
  padding: 1rem 0;
}

/* 移动端/小屏幕下技能编辑器的优化样式 */
@media (max-width: 1024px) {
  /* 隐藏抽屉最左侧的大内边距，使内容宽度最大化 */
  .absolute.inset-y-0.right-0.pl-10.max-w-full.flex {
    padding-left: 0 !important;
  }
  
  /* 给编辑器主体加上底边框与最小高度 */
  .skill-editor {
    border-radius: 8px;
  }
}

@media (max-width: 640px) {
  .skill-editor-toolbar {
    flex-direction: column;
    align-items: stretch;
    gap: 8px;
    padding: 8px 10px;
  }
  
  .skill-editor-actions {
    justify-content: space-between;
    width: 100%;
  }
  
  .skill-editor-file-info {
    width: 100%;
  }

  .skill-editor-filename {
    max-width: 150px;
  }
  
  .skill-editor-body {
    padding: 8px !important;
  }
  
  .skill-editor-textarea {
    font-size: 11px !important;
    padding: 10px 40px 10px 10px !important;
  }
  
  .skill-editor-gutter {
    font-size: 11px !important;
    padding: 10px 4px 10px 8px !important;
  }
  
  .skill-editor-statusbar {
    padding: 6px 10px !important;
    font-size: 9px !important;
  }
}
</style>
