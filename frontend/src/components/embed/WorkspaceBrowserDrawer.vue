<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick, markRaw, type Component } from 'vue'
import axios from '@/utils/axios'
import { useToast } from '@/composables/useToast'
import {
  resolveFileTypeVisual,
  type FileTypeCategory,
} from '@/utils/fileTypeVisual'
import { canPreviewWorkspaceFile, downloadWorkspaceFile, createWorkspaceEntry, renameWorkspaceEntry, deleteWorkspaceEntry, copyTextToClipboard, restoreWorkspaceEntry, purgeWorkspaceEntry, emptyWorkspaceTrash, extractWorkspaceArchive } from '@/utils/workspaceFilePreview'
import WorkspaceActionMenu from '@/components/embed/WorkspaceActionMenu.vue'
import WorkspaceDirectorySaveDialog from '@/components/embed/WorkspaceDirectorySaveDialog.vue'
import ConfirmModal from '@/components/ConfirmModal.vue'
import { planBatchDelete, selectAllState, toggleSelectAllPaths } from '@/utils/workspaceBatchSelection'
import { buildWorkspaceActionEntries } from '@/utils/workspaceActionEntries'
import type { WorkspaceActionKey, WorkspaceActionTarget, WorkspaceMenuContext } from '@/utils/workspaceActionEntries'
import { useWorkspaceUploadQueue } from '@/composables/useWorkspaceUploadQueue'
import { shouldAutoDismissUploads } from '@/utils/workspaceUploadQueue'
import {
  ArrowPathIcon,
  ArrowUpTrayIcon,
  ChatBubbleLeftEllipsisIcon,
  CheckCircleIcon,
  ClockIcon,
  ComputerDesktopIcon,
  DocumentTextIcon,
  EllipsisVerticalIcon,
  HomeIcon,
  ListBulletIcon,
  Squares2X2Icon,
  TrashIcon,
  XMarkIcon,
} from '@heroicons/vue/24/outline'

const LEGACY_RECENT_FILES_KEY = 'workspace_recent_files_v1'
const LEGACY_BROWSER_PREFS_KEY = 'workspace_browser_prefs_v1'
const SHOW_TYPE_FILTER_KEY = 'workspace_show_type_filter_v1'
const MAX_RECENT = 20
const LIST_PAGE_SIZE = 200

const modelValue = defineModel<boolean>({ default: false })
const keepOpenOnSelect = defineModel<boolean>('keepOpenOnSelect', { default: false })
const pinned = defineModel<boolean>('pinned', { default: false })
const drawerWidth = defineModel<number>('drawerWidth', { default: 448 })

const props = withDefaults(
  defineProps<{
    /**
     * 与其它钉住面板并排时距视口右边的像素偏移。
     *
     * 必须收 number 走内联 style，不能收 Tailwind 类名：父级曾用
     * `right-[${rem}rem]` 拼字符串传进来，而 Tailwind 只做静态扫描，构建产物里
     * 根本没有这些规则，多个面板并排时偏移失效、直接叠在同一位置。
     */
    pinnedDockRight?: number
    conversationId?: string | null
    /** 当前会话是否已开始（有消息往来）；未开始时「本会话目录」不可用 */
    sessionStarted?: boolean
  }>(),
  { pinnedDockRight: 0, conversationId: null, sessionStarted: false },
)

const emit = defineEmits<{
  (e: 'select', payload: { type: 'local_file' | 'local_dir'; path: string; name: string; size: number; ext: string }): void
  (e: 'preview', payload: { path: string; name: string }): void
}>()

const currentPath = ref<string>('')
const parentPath = ref<string | null>(null)
const isRoot = ref<boolean>(true)
const isVirtualRoot = ref(false)
const scope = ref<'admin_all' | 'user_scoped'>('user_scoped')
const isAdminScope = computed(() => scope.value === 'admin_all')
const items = ref<any[]>([])
const loading = ref<boolean>(false)
const baseDir = ref<string>('')
const currentPathWritable = ref(false)
const userWorkspaceRoot = ref<string>('')

const createDialog = ref<{ kind: 'file' | 'dir'; parentPath: string; name: string } | null>(null)
const createSubmitting = ref(false)
const renameDialog = ref<{ path: string; name: string; isDir: boolean } | null>(null)
const extractDialog = ref<{ item: WorkspaceItemLike; parentPath: string; defaultName: string } | null>(null)
const extractSubmitting = ref(false)
const deleteTarget = ref<{ path: string; name: string; isDir: boolean; isPublic?: boolean } | null>(null)
const purgeTarget = ref<{ path: string; name: string; isDir: boolean } | null>(null)
const emptyTrashConfirm = ref(false)
const emptyTrashSubmitting = ref(false)
const uploadInputRef = ref<HTMLInputElement | null>(null)
const uploadTargetPath = ref('')
const highlightedPath = ref('')
const selectedPaths = ref<Set<string>>(new Set())
const multiSelectMode = ref(false)
const batchDeleteConfirm = ref(false)
const batchDeleteSubmitting = ref(false)
const batchDeleteProgress = ref({ done: 0, total: 0 })

/**
 * 清空当前勾选。
 *
 * 勾选按 path 记录，切换目录/搜索条件后旧路径已不在列表内；若不清空，
 * 计数与批量动作就会出现「看不见却被操作」的条目。
 */
const resetBatchSelection = () => {
  if (selectedPaths.value.size > 0) selectedPaths.value = new Set()
}
type ViewMode = 'list' | 'grid'
const viewMode = ref<ViewMode>('list')
const listPage = ref(1)
const recentFiles = ref<Array<{ path: string; name: string; mtime: number }>>([])
const recentFilesOpen = ref(false)
const recentFilesMenuRef = ref<HTMLElement | null>(null)
const recentFilesMenuPos = ref({ top: 0, left: 0 })
const RECENT_FILES_MENU_WIDTH = 288

const quickNavOpen = ref(false)
const quickNavMenuRef = ref<HTMLElement | null>(null)
const quickNavTriggerRef = ref<HTMLElement | null>(null)
const quickNavMenuPos = ref({ top: 0, left: 0 })
const QUICK_NAV_MENU_WIDTH = 220
const longPressTimer = ref<ReturnType<typeof setTimeout> | null>(null)

const { showToast } = useToast()

// --------------------------------------------------------------------------
// 上传队列：进度、取消、重试统一由零依赖引擎编排（见 utils/workspaceUploadQueue.ts）。
// 之所以不再是「串行 await + 逐个 toast」：浏览器把请求体写入 socket 缓冲区即算发送完成，
// 5MB 文件只会触发一次进度事件，若不显式建模「服务端写入中」阶段，界面就会长时间静默。
// --------------------------------------------------------------------------
const {
  snapshot: uploadSnapshot,
  enqueue: enqueueWorkspaceUpload,
  cancel: cancelWorkspaceUpload,
  retry: retryWorkspaceUpload,
  clearFinished: clearFinishedWorkspaceUpload,
} = useWorkspaceUploadQueue()
const uploadPanelCollapsed = ref(false)

// 全部成功后自动清除队列面板：抽屉是常驻挂载的（父组件用 v-model 而非 v-if），
// 若只靠手动「清除已完成」，成功的浮层会一直跟着用户跨目录、跨会话占位。
// 留 3 秒是为了让「已完成」有机会被看见；有失败或取消时不清除，见 shouldAutoDismissUploads。
const UPLOAD_PANEL_AUTO_DISMISS_MS = 3000
let uploadAutoDismissTimer: ReturnType<typeof setTimeout> | null = null

const cancelUploadAutoDismiss = () => {
  if (uploadAutoDismissTimer === null) return
  clearTimeout(uploadAutoDismissTimer)
  uploadAutoDismissTimer = null
}

// 手动清除也要撤销待执行的自动清除，否则面板已清空后还会再触发一次空清除
const clearFinishedUploads = () => {
  cancelUploadAutoDismiss()
  clearFinishedWorkspaceUpload()
}

const formatBytes = (bytes: number) => {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value >= 100 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`
}

const formatUploadSpeed = (bps: number | null) => (bps && bps > 0 ? `${formatBytes(bps)}/s` : '')

const formatUploadEta = (seconds: number | null) => {
  if (!seconds || seconds <= 0) return ''
  if (seconds < 60) return `约 ${seconds} 秒`
  return `约 ${Math.ceil(seconds / 60)} 分钟`
}

const UPLOAD_STATUS_TEXT: Record<string, string> = {
  queued: '等待中',
  transferring: '传输中',
  committing: '服务端写入中…',
  done: '已完成',
  failed: '失败',
  canceled: '已取消',
}

const uploadTaskStatusText = (status: string) => UPLOAD_STATUS_TEXT[status] ?? status

const uploadTaskHint = (task: { status: string; error: string | null; speedBps: number | null; remainingSeconds: number | null }) => {
  if (task.status === 'failed') return task.error || '上传失败'
  // 取消只能中止传输：请求体可能已完整送达并被服务端落盘，因此不承诺「未保存」
  if (task.status === 'canceled') return '已取消，可能已保存'
  if (task.status === 'done') return '已保存'
  if (task.status === 'committing') return '文件已送达，正在写入工作空间…'
  return [formatUploadSpeed(task.speedBps), formatUploadEta(task.remainingSeconds)].filter(Boolean).join(' · ')
}

const uploadTaskActive = (status: string) =>
  status === 'queued' || status === 'transferring' || status === 'committing'

const selectedItem = ref<any | null>(null)
const searchQuery = ref('')
const includeSubdirs = ref(true)
const searchLoading = ref(false)
const recursiveSearchResults = ref<any[]>([])
const searchTruncated = ref(false)
type TypeFilterKey = 'all' | FileTypeCategory | 'markdown' | 'html'
const typeFilter = ref<TypeFilterKey>('all')
/** 默认收起类型筛选，节省纵向空间；有活跃筛选时自动展开 */
const showTypeFilter = ref(
  typeof window !== 'undefined' && localStorage.getItem(SHOW_TYPE_FILTER_KEY) === '1',
)

const TYPE_FILTER_OPTIONS: Array<{ key: TypeFilterKey; label: string; icon: string }> = [
  { key: 'all', label: '全部', icon: '✨' },
  { key: 'folder', label: '文件夹', icon: '📁' },
  { key: 'image', label: '图片', icon: '🖼️' },
  { key: 'markdown', label: 'Markdown', icon: '📝' },
  { key: 'document', label: '文档', icon: '📄' },
  { key: 'html', label: 'HTML', icon: '🌐' },
  { key: 'code', label: '代码', icon: '💻' },
  { key: 'spreadsheet', label: '表格', icon: '📊' },
  { key: 'presentation', label: '演示', icon: '📽️' },
  { key: 'archive', label: '压缩包', icon: '🗜️' },
  { key: 'video', label: '视频', icon: '🎬' },
  { key: 'audio', label: '音频', icon: '🎵' },
  { key: 'data', label: '数据', icon: '🗃️' },
]

/** 仅选类型、无搜索词时，按扩展名通配符递归扫描子目录 */
const TYPE_SCAN_PATTERNS: Partial<Record<TypeFilterKey, string[]>> = {
  markdown: ['*.md'],
  html: ['*.html', '*.htm'],
  image: ['*.png', '*.jpg', '*.jpeg', '*.webp', '*.gif', '*.svg', '*.bmp'],
  document: ['*.pdf', '*.doc', '*.docx', '*.txt', '*.rtf'],
  code: ['*.py', '*.js', '*.ts', '*.tsx', '*.jsx', '*.json', '*.yaml', '*.yml', '*.css', '*.sh', '*.sql', '*.xml'],
  spreadsheet: ['*.xls', '*.xlsx', '*.csv'],
  presentation: ['*.ppt', '*.pptx'],
  archive: ['*.zip', '*.rar', '*.tar', '*.gz', '*.7z'],
  video: ['*.mp4', '*.mov', '*.avi', '*.mkv', '*.webm'],
  audio: ['*.mp3', '*.wav', '*.flac', '*.aac'],
  data: ['*.db', '*.sqlite', '*.parquet'],
}

const loadBrowserPrefs = async () => {
  try {
    const res = await axios.get('/api/v1/chat/fs/browser-prefs')
    const prefs = res.data?.data
    if (prefs && typeof prefs.include_subdirs === 'boolean') {
      includeSubdirs.value = prefs.include_subdirs
    }
    if (prefs?.type_filter) typeFilter.value = prefs.type_filter as TypeFilterKey
    if (typeFilter.value !== 'all') showTypeFilter.value = true
    await migrateLegacyBrowserPrefsIfNeeded()
  } catch { /* ignore */ }
}

let browserPrefsPersistTimer: ReturnType<typeof setTimeout> | null = null

const persistBrowserPrefsNow = async () => {
  try {
    await axios.put('/api/v1/chat/fs/browser-prefs', {
      include_subdirs: includeSubdirs.value,
      type_filter: typeFilter.value,
    })
  } catch {
    /* ignore */
  }
}

const saveBrowserPrefs = () => {
  if (browserPrefsPersistTimer) clearTimeout(browserPrefsPersistTimer)
  browserPrefsPersistTimer = setTimeout(() => {
    browserPrefsPersistTimer = null
    void persistBrowserPrefsNow()
  }, 300)
}

const migrateLegacyBrowserPrefsIfNeeded = async () => {
  try {
    const raw = localStorage.getItem(LEGACY_BROWSER_PREFS_KEY)
    if (!raw) return
    const legacy = JSON.parse(raw) as { includeSubdirs?: boolean; typeFilter?: string }
    localStorage.removeItem(LEGACY_BROWSER_PREFS_KEY)
    if (typeof legacy.includeSubdirs === 'boolean') {
      includeSubdirs.value = legacy.includeSubdirs
    }
    if (legacy.typeFilter) {
      typeFilter.value = legacy.typeFilter as TypeFilterKey
    }
    await persistBrowserPrefsNow()
  } catch {
    localStorage.removeItem(LEGACY_BROWSER_PREFS_KEY)
  }
}

const loadRecentFiles = async () => {
  try {
    const res = await axios.get('/api/v1/chat/fs/recent-files')
    recentFiles.value = res.data?.data?.items || []
    await migrateLegacyRecentFilesIfNeeded()
  } catch {
    recentFiles.value = []
  }
}

let recentFilesPersistTimer: ReturnType<typeof setTimeout> | null = null

const persistRecentFilesNow = async () => {
  try {
    await axios.put('/api/v1/chat/fs/recent-files', { items: recentFiles.value })
  } catch {
    /* ignore */
  }
}

const persistRecentFiles = () => {
  if (recentFilesPersistTimer) clearTimeout(recentFilesPersistTimer)
  recentFilesPersistTimer = setTimeout(() => {
    recentFilesPersistTimer = null
    void persistRecentFilesNow()
  }, 300)
}

const migrateLegacyRecentFilesIfNeeded = async () => {
  try {
    const raw = localStorage.getItem(LEGACY_RECENT_FILES_KEY)
    if (!raw) return
    const legacy = JSON.parse(raw) as Array<{ path: string; name: string; mtime?: number }>
    localStorage.removeItem(LEGACY_RECENT_FILES_KEY)
    if (!Array.isArray(legacy) || legacy.length === 0) return
    if (recentFiles.value.length > 0) return
    recentFiles.value = legacy
      .map((item) => ({ path: item.path, name: item.name, mtime: item.mtime || Date.now() / 1000 }))
      .slice(0, MAX_RECENT)
    await persistRecentFilesNow()
  } catch {
    localStorage.removeItem(LEGACY_RECENT_FILES_KEY)
  }
}

const trackRecentFile = (item: { path: string; name: string; mtime?: number }) => {
  if (item.path.includes('/.trash/')) return
  const next = [{ path: item.path, name: item.name, mtime: item.mtime || Date.now() / 1000 }, ...recentFiles.value.filter((r) => r.path !== item.path)]
  recentFiles.value = next.slice(0, MAX_RECENT)
  persistRecentFiles()
}

const removeRecentFile = (path: string) => {
  recentFiles.value = recentFiles.value.filter((r) => r.path !== path)
  persistRecentFiles()
}

const clearRecentFiles = () => {
  recentFiles.value = []
  void persistRecentFilesNow()
  showToast('已清空最近记录', 'success')
}

const updateRecentFilesMenuPosition = () => {
  const trigger = quickNavTriggerRef.value
  if (!trigger) return
  const rect = trigger.getBoundingClientRect()
  const width = Math.min(RECENT_FILES_MENU_WIDTH, window.innerWidth - 16)
  let left = rect.left
  left = Math.max(8, Math.min(left, window.innerWidth - width - 8))
  recentFilesMenuPos.value = { top: rect.bottom + 4, left }
}

const syncRecentFilesMenuPosition = () => {
  if (recentFilesOpen.value) updateRecentFilesMenuPosition()
  if (quickNavOpen.value) updateQuickNavMenuPosition()
}

const updateQuickNavMenuPosition = () => {
  const trigger = quickNavTriggerRef.value
  if (!trigger) return
  const rect = trigger.getBoundingClientRect()
  const width = Math.min(QUICK_NAV_MENU_WIDTH, window.innerWidth - 16)
  let left = rect.left
  left = Math.max(8, Math.min(left, window.innerWidth - width - 8))
  quickNavMenuPos.value = { top: rect.bottom + 4, left }
}

const closeQuickNavMenu = () => {
  quickNavOpen.value = false
}

const toggleQuickNavMenu = () => {
  if (quickNavOpen.value) {
    closeQuickNavMenu()
    return
  }
  closeRecentFilesMenu()
  quickNavOpen.value = true
  nextTick(updateQuickNavMenuPosition)
}

const openRecentFilesFromQuickNav = () => {
  closeQuickNavMenu()
  void loadRecentFiles()
  recentFilesOpen.value = true
  nextTick(updateRecentFilesMenuPosition)
}

const closeRecentFilesMenu = () => {
  recentFilesOpen.value = false
}

const formatRecentFileLocation = (filePath: string) => {
  const parent = filePath.split('/').slice(0, -1).join('/')
  const root = userWorkspaceRoot.value
  if (root && parent.startsWith(root)) {
    const rel = parent.slice(root.length).replace(/^\//, '')
    return rel ? `📂 ${rel}` : '📂 我的目录'
  }
  const parts = parent.split('/').filter(Boolean)
  if (parts.length >= 2) return `📂 …/${parts.slice(-2).join('/')}`
  return parts.length ? `📂 ${parts[parts.length - 1]}` : '📂 —'
}

const openRecentFile = async (recent: { path: string; name: string; mtime: number }) => {
  closeRecentFilesMenu()
  const parentDir = recent.path.split('/').slice(0, -1).join('/')
  highlightedPath.value = recent.path
  await fetchDirectory(parentDir, { preserveSearch: true })
  await nextTick()
  const index = sortedDisplayItems.value.findIndex((item) => item.path === recent.path)
  if (index >= 0) {
    listPage.value = Math.floor(index / LIST_PAGE_SIZE) + 1
  }
  const matched = items.value.find((item) => item.path === recent.path)
  selectedItem.value = matched || {
    path: recent.path,
    name: recent.name,
    is_dir: false,
    mtime: recent.mtime,
  }
  setTimeout(() => { highlightedPath.value = '' }, 3000)
}

void loadBrowserPrefs()
void loadRecentFiles()

let searchDebounceTimer: ReturnType<typeof setTimeout> | null = null

const isMobile = ref(
  typeof window !== 'undefined' && window.matchMedia('(max-width: 639px)').matches,
)
let mobileMq: MediaQueryList | null = null

const syncMobile = () => {
  const wasMobile = isMobile.value
  isMobile.value = !!mobileMq?.matches
  if (!wasMobile && isMobile.value && pinned.value) {
    pinned.value = false
  }
}

const setMobileBodyScrollLock = (locked: boolean) => {
  if (!isMobile.value) return
  const value = locked ? 'hidden' : ''
  document.documentElement.style.overflow = value
  document.body.style.overflow = value
}

const clearSearch = () => {
  searchQuery.value = ''
  recursiveSearchResults.value = []
  searchTruncated.value = false
}

const resetSearchState = () => {
  clearSearch()
  typeFilter.value = 'all'
  if (searchDebounceTimer) {
    clearTimeout(searchDebounceTimer)
    searchDebounceTimer = null
  }
}

/** 非管理员虚拟根目录的 currentPath 为 data 根，不可作为搜索起点 */
const resolveSearchPathParam = (): string | undefined => {
  if (isRoot.value && scope.value === 'user_scoped') return undefined
  return currentPath.value || undefined
}

const nameMatchesSearchQuery = (name: string, query: string): boolean => {
  const lowerName = name.toLowerCase()
  const lowerQuery = query.toLowerCase()
  if (/[*?]/.test(lowerQuery)) {
    const escaped = lowerQuery.replace(/[.+^${}()|[\]\\]/g, '\\$&')
    const pattern = `^${escaped.replace(/\*/g, '.*').replace(/\?/g, '.')}$`
    return new RegExp(pattern).test(lowerName)
  }
  return lowerName.includes(lowerQuery)
}

const filteredCurrentItems = computed(() => {
  const q = searchQuery.value.trim()
  if (!q) return items.value
  return items.value.filter((item) => nameMatchesSearchQuery(item.name, q))
})

const isSearchActive = computed(() => searchQuery.value.trim().length > 0)
const isTypeFilterActive = computed(() => typeFilter.value !== 'all')
const isTypeScanActive = computed(() => {
  const key = typeFilter.value
  return key !== 'all' && key !== 'folder' && !searchQuery.value.trim()
})
const isRecursiveListingActive = computed(
  () => isTypeScanActive.value || (isSearchActive.value && includeSubdirs.value),
)

const resolveRecursiveSearchPatterns = (): string[] => {
  const userQ = searchQuery.value.trim()
  if (userQ) return [userQ]
  if (isTypeScanActive.value) {
    return TYPE_SCAN_PATTERNS[typeFilter.value] || []
  }
  return []
}

const fetchRecursiveSearch = async () => {
  const patterns = resolveRecursiveSearchPatterns()
  if (!patterns.length || !isRecursiveListingActive.value) {
    recursiveSearchResults.value = []
    searchTruncated.value = false
    return
  }

  searchLoading.value = true
  try {
    const merged = new Map<string, any>()
    let truncated = false
    for (const q of patterns) {
      const res = await axios.get('/api/v1/chat/fs/search', {
        params: {
          q,
          path: resolveSearchPathParam(),
          max_results: 80,
        },
      })
      if (res.data?.data) {
        for (const item of res.data.data.items || []) {
          merged.set(item.path, item)
        }
        if (res.data.data.truncated) truncated = true
      }
    }
    recursiveSearchResults.value = Array.from(merged.values())
    searchTruncated.value = truncated
  } catch (error) {
    console.error('Failed to search files:', error)
    recursiveSearchResults.value = []
    showToast('搜索失败，请稍后重试', 'error')
  } finally {
    searchLoading.value = false
  }
}

const scheduleRecursiveSearch = (delay = 200) => {
  if (searchDebounceTimer) clearTimeout(searchDebounceTimer)
  searchDebounceTimer = setTimeout(fetchRecursiveSearch, delay)
}

watch(searchQuery, () => {
  selectedItem.value = null
  resetBatchSelection()
  if (searchDebounceTimer) clearTimeout(searchDebounceTimer)

  if (!searchQuery.value.trim() && !isTypeScanActive.value) {
    recursiveSearchResults.value = []
    searchTruncated.value = false
    return
  }

  if (isRecursiveListingActive.value) {
    scheduleRecursiveSearch(300)
  } else {
    recursiveSearchResults.value = []
    searchTruncated.value = false
  }
})

watch(includeSubdirs, () => {
  selectedItem.value = null
  if (isTypeScanActive.value) return
  if (searchDebounceTimer) clearTimeout(searchDebounceTimer)
  if (searchQuery.value.trim() && includeSubdirs.value) {
    scheduleRecursiveSearch(200)
  } else {
    recursiveSearchResults.value = []
    searchTruncated.value = false
  }
})

watch(typeFilter, () => {
  selectedItem.value = null
  resetBatchSelection()
  if (searchDebounceTimer) clearTimeout(searchDebounceTimer)
  if (isTypeScanActive.value || (searchQuery.value.trim() && includeSubdirs.value)) {
    scheduleRecursiveSearch(150)
  } else if (!searchQuery.value.trim()) {
    recursiveSearchResults.value = []
    searchTruncated.value = false
  }
})

const itemMatchesTypeFilter = (item: { name: string; is_dir: boolean }) => {
  if (typeFilter.value === 'all') return true
  const visual = getItemVisual(item)
  if (typeFilter.value === 'folder') return item.is_dir
  if (item.is_dir) {
    if (isRecursiveListingActive.value) return false
    return false
  }
  if (typeFilter.value === 'markdown') return visual.ext === 'md'
  if (typeFilter.value === 'html') return visual.ext === 'html' || visual.ext === 'htm'
  if (typeFilter.value === 'document') {
    return visual.category === 'document' && visual.ext !== 'md'
  }
  return visual.category === typeFilter.value
}

const baseDisplayItems = computed(() => {
  if (isRecursiveListingActive.value) return recursiveSearchResults.value
  if (isSearchActive.value) return filteredCurrentItems.value
  return items.value
})

const displayItems = computed(() => baseDisplayItems.value.filter(itemMatchesTypeFilter))

type SortKey = 'name' | 'type' | 'size' | 'mtime'
const sortKey = ref<SortKey>('type')
const sortDir = ref<'asc' | 'desc'>('asc')

const toggleSort = (key: SortKey) => {
  if (sortKey.value === key) {
    sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortKey.value = key
    sortDir.value = key === 'size' || key === 'mtime' ? 'desc' : 'asc'
  }
}

/** 仅当前排序列显示 ↑/↓，未激活列不显示静态 ↕ */
const activeSortArrow = (key: SortKey) => {
  if (sortKey.value !== key) return ''
  return sortDir.value === 'asc' ? '↑' : '↓'
}

const sortedDisplayItems = computed(() => {
  const list = [...displayItems.value]
  const dir = sortDir.value === 'asc' ? 1 : -1

  list.sort((a, b) => {
    let cmp = 0
    switch (sortKey.value) {
      case 'name':
        if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1
        cmp = a.name.localeCompare(b.name, 'zh-CN', { sensitivity: 'base', numeric: true })
        break
      case 'type': {
        if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1
        const va = getItemVisual(a)
        const vb = getItemVisual(b)
        cmp = va.label.localeCompare(vb.label, 'zh-CN', { sensitivity: 'base' })
          || va.ext.localeCompare(vb.ext)
        break
      }
      case 'size':
        if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1
        cmp = (a.size || 0) - (b.size || 0)
        break
      case 'mtime':
        cmp = (a.mtime || 0) - (b.mtime || 0)
        break
    }
    return cmp * dir
  })
  return list
})

/**
 * 全选的作用范围＝当前列表（搜索/类型筛选 + 排序后）的全部条目。
 *
 * 与「批量挂载」的取数保持一致：两个批量动作看到的集合必须是同一个，
 * 否则用户会以为「全选后挂载」和「全选后删除」操作的是同一批文件。
 */
const batchSelectablePaths = computed(() => sortedDisplayItems.value.map((item) => item.path))
const batchSelectAllState = computed(() => selectAllState(batchSelectablePaths.value, selectedPaths.value))
/** 只统计仍在当前列表内的勾选，避免跨目录残留路径虚增计数 */
const batchSelectedItems = computed(() =>
  sortedDisplayItems.value.filter((item) => selectedPaths.value.has(item.path)),
)

const toggleSelectAllItems = () => {
  selectedPaths.value = toggleSelectAllPaths(batchSelectablePaths.value, selectedPaths.value)
}

const displayEmptyHint = computed(() => {
  if (isRecursiveListingActive.value && searchLoading.value) return ''
  if (isTypeScanActive.value) return '未找到符合类型筛选的文件'
  if (isTypeFilterActive.value && !isSearchActive.value) return '当前目录下没有符合类型筛选的项'
  if (!isSearchActive.value && !isTypeFilterActive.value) return '暂无子文件或子目录'
  if (isTypeFilterActive.value) return '未找到符合搜索与类型筛选的文件'
  return '未找到匹配的文件或目录'
})

const paginatedDisplayItems = computed(() => {
  const end = listPage.value * LIST_PAGE_SIZE
  return sortedDisplayItems.value.slice(0, end)
})

const hasMoreListItems = computed(() => paginatedDisplayItems.value.length < sortedDisplayItems.value.length)

const loadMoreListItems = () => {
  if (hasMoreListItems.value) listPage.value += 1
}

watch([displayItems, sortKey, sortDir], () => {
  listPage.value = 1
})

watch([includeSubdirs, typeFilter], saveBrowserPrefs)

watch(showTypeFilter, (show) => {
  try {
    localStorage.setItem(SHOW_TYPE_FILTER_KEY, show ? '1' : '0')
  } catch { /* ignore */ }
  if (!show && typeFilter.value !== 'all') {
    typeFilter.value = 'all'
  }
})

const setTypeFilter = (key: TypeFilterKey) => {
  typeFilter.value = key
  saveBrowserPrefs()
}

const activeTypeFilterLabel = computed(() => {
  const opt = TYPE_FILTER_OPTIONS.find((o) => o.key === typeFilter.value)
  return opt?.label || '全部'
})

const getItemLocationHint = (itemPath: string) => {
  const parent = itemPath.split('/').slice(0, -1).join('/')
  if (!parent || parent === currentPath.value) return ''
  const base = baseDir.value || currentPath.value
  const rel = parent.startsWith(base) ? parent.slice(base.length).replace(/^\//, '') : parent
  return rel || parent.split('/').pop() || ''
}

const normalizeFsPathForCompare = (path: string) => path.replace(/\\/g, '/').replace(/\/+$/, '')

const trashDirPath = computed(() => (userWorkspaceRoot.value ? `${userWorkspaceRoot.value}/.trash` : ''))

const isTrashPath = (path: string) => {
  const trash = trashDirPath.value
  if (!trash || !path) return false
  const norm = normalizeFsPathForCompare(path)
  const t = normalizeFsPathForCompare(trash)
  return norm === t || norm.startsWith(`${t}/`)
}

const isTrashView = computed(() => isTrashPath(currentPath.value))

const displayItemName = (item: { name: string; path: string; is_dir: boolean }) => {
  if (item.name === '.trash' || (item.is_dir && isTrashPath(item.path) && normalizeFsPathForCompare(item.path) === normalizeFsPathForCompare(trashDirPath.value))) {
    return '回收站'
  }
  return item.name
}

const formatTrashItemName = (name: string) => {
  const match = name.match(/^\d+_[a-f0-9]{8}_(.+)$/)
  return match?.[1] || name
}

const resolveItemDisplayName = (item: { name: string; path: string; is_dir: boolean }) => {
  if (isUserSessionsContainerItem(item)) return '会话目录'
  return isTrashView.value ? formatTrashItemName(item.name) : displayItemName(item)
}

const isTrashRootItem = (item: { path: string; name: string; is_dir: boolean }) => {
  if (!item.is_dir || !trashDirPath.value) return false
  return normalizeFsPathForCompare(item.path) === normalizeFsPathForCompare(trashDirPath.value)
}

const isTrashListItem = (item: { path: string; name: string; is_dir: boolean }) => (
  isTrashPath(item.path) && !isTrashRootItem(item)
)

const USER_WORKSPACE_RESERVED_DIRS = new Set(['docs', 'uploads', 'sandbox', '.trash', 'skills', 'sessions'])
const SESSION_DIR_NAME_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

const isSessionDirName = (name: string) => (
  SESSION_DIR_NAME_RE.test(name) || name.startsWith('conv_')
)

const isUserSessionsContainerItem = (item: { name: string; path: string; is_dir: boolean }) => {
  if (!item.is_dir || item.name !== 'sessions') return false
  const root = userWorkspaceRoot.value
  if (!root || !item.path) return false
  const norm = normalizeFsPathForCompare(item.path)
  const rootNorm = normalizeFsPathForCompare(root)
  if (!norm.startsWith(`${rootNorm}/`)) return false
  const relative = norm.slice(rootNorm.length + 1)
  return relative === 'sessions'
}

const isSessionDirItem = (item: { name: string; path: string; is_dir: boolean }) => {
  if (!item.is_dir) return false
  if (isUserSessionsContainerItem(item)) return true
  const root = userWorkspaceRoot.value
  if (!root || !item.path) return false
  const norm = normalizeFsPathForCompare(item.path)
  const rootNorm = normalizeFsPathForCompare(root)
  if (!norm.startsWith(`${rootNorm}/`)) return false
  const relative = norm.slice(rootNorm.length + 1)
  const parts = relative.split('/')
  const sessionName = parts[1]
  if (parts.length === 2 && parts[0] === 'sessions' && sessionName && isSessionDirName(sessionName)) {
    return true
  }
  if (parts.length === 1 && !USER_WORKSPACE_RESERVED_DIRS.has(item.name)) {
    return isSessionDirName(item.name)
  }
  return false
}

const isPathInUserWorkspace = (path: string) => {
  const root = userWorkspaceRoot.value
  if (!root || !path) return false
  const norm = normalizeFsPathForCompare(path)
  const rootNorm = normalizeFsPathForCompare(root)
  return norm === rootNorm || norm.startsWith(`${rootNorm}/`)
}

const isPublicItem = (item?: { path?: string; is_public?: boolean; is_user_workspace?: boolean; is_dir?: boolean } | null) => {
  if (!item) return false
  if (item.is_user_workspace) return false
  if (typeof item.is_public === 'boolean') return item.is_public
  if (!item.path) return false
  return !isPathInUserWorkspace(item.path)
}

const canCreateInPath = (parentPath: string) => {
  if (isAdminScope.value && currentPathWritable.value && !isTrashPath(parentPath)) return true
  return isPathInUserWorkspace(parentPath) && !isTrashPath(parentPath)
}

const canUseCreateMenu = computed(
  () => !isRecursiveListingActive.value && !loading.value && !searchLoading.value,
)

type WorkspaceItemLike = {
  path: string
  name: string
  is_dir: boolean
  is_public?: boolean
  is_user_workspace?: boolean
  size?: number
  mtime?: number
}

type WorkspaceActionMenuState = {
  ctx: WorkspaceMenuContext
  anchor: { x: number; y: number }
  parentPath: string
  item?: WorkspaceItemLike
  source: 'row' | 'context'
}

const actionMenu = ref<WorkspaceActionMenuState | null>(null)
const actionMenuEntries = computed(() =>
  actionMenu.value ? buildWorkspaceActionEntries(actionMenu.value.ctx) : [],
)

const closeActionMenu = () => {
  actionMenu.value = null
}

/** 把一条文件记录翻译成纯函数需要的场景描述（⋮ 与右键共用同一套判定）。 */
const buildItemMenuContext = (item: WorkspaceItemLike): WorkspaceMenuContext => {
  const target: WorkspaceActionTarget = {
    name: resolveItemDisplayName(item),
    path: item.path,
    isDir: !!item.is_dir,
    isPreviewable: item.is_dir ? undefined : canPreviewWorkspaceFile(item.name),
  }
  if (isTrashRootItem(item)) return { scene: 'trash-root', target }
  if (isTrashListItem(item)) return { scene: 'trash-item', target }
  if (isPublicItem(item)) {
    return {
      scene: 'public-item',
      target,
      isAdminScope: isAdminScope.value,
      isSystemProtected: isSystemProtectedDir(item),
    }
  }
  return { scene: 'regular-item', target, canManage: canManageItem(item) }
}

const openContextMenu = (
  event: MouseEvent,
  parentPath: string,
  item?: WorkspaceItemLike,
) => {
  if (!canUseCreateMenu.value && !item) return
  const ctx: WorkspaceMenuContext = item
    ? buildItemMenuContext(item)
    : { scene: 'empty-area', canCreateHere: canCreateInPath(parentPath) }
  // 「能不能开菜单」与「菜单里有几项」用同一个判定，行内 ⋮ 和右键才不会一个能开一个不能
  if (!buildWorkspaceActionEntries(ctx).length) return
  event.preventDefault()
  event.stopPropagation()
  actionMenu.value = {
    ctx,
    anchor: { x: event.clientX, y: event.clientY },
    parentPath,
    item,
    source: 'context',
  }
}

const handleListContextMenu = (event: MouseEvent) => {
  if (!currentPath.value || !currentPathWritable.value) return
  openContextMenu(event, currentPath.value)
}

const handleItemContextMenu = (event: MouseEvent, item: WorkspaceItemLike) => {
  const parentPath = item.is_dir ? item.path : currentPath.value
  if (!parentPath) return
  openContextMenu(event, parentPath, item)
}

const isRowActionMenuOpen = (item: { path: string }) =>
  actionMenu.value?.source === 'row' && actionMenu.value.item?.path === item.path

/** 行尾 ⋮：锚在按钮左下角，再由菜单组件按视口夹取/翻转。 */
const toggleRowActionMenu = (event: MouseEvent, item: WorkspaceItemLike) => {
  event.stopPropagation()
  if (actionMenu.value?.source === 'row' && actionMenu.value.item?.path === item.path) {
    closeActionMenu()
    return
  }
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  actionMenu.value = {
    ctx: buildItemMenuContext(item),
    anchor: { x: rect.left, y: rect.bottom + 4 },
    parentPath: item.is_dir ? item.path : currentPath.value,
    item,
    source: 'row',
  }
}

/** 菜单项 key → 既有动作函数。菜单项由纯函数生成，这里只做派发。 */
const runWorkspaceAction = (key: WorkspaceActionKey) => {
  const state = actionMenu.value
  if (!state) return
  const item = state.item
  const parentPath = state.parentPath
  closeActionMenu()

  switch (key) {
    case 'preview':
      if (item) previewItem(item)
      break
    case 'download':
      if (item) void downloadItem(item)
      break
    case 'extract':
      if (item) openExtractDialog(item)
      break
    case 'copy-path':
      if (item) void copyItemPath(item.path)
      break
    case 'mount':
      if (item) mountItemToSession(item)
      break
    case 'open-dir':
      if (item) void fetchDirectory(item.path)
      break
    case 'rename':
      if (item) startRenameEntry(item)
      break
    case 'delete':
      if (item) confirmDeleteEntry(item)
      break
    case 'purge':
      if (item) confirmPurgeEntry(item)
      break
    case 'restore':
      if (item) void restoreTrashItem(item)
      break
    case 'empty-trash':
      confirmEmptyTrash()
      break
    case 'create-file':
      startCreateEntry('file', parentPath)
      break
    case 'create-dir':
      startCreateEntry('dir', parentPath)
      break
    case 'upload':
      openUploadPicker(parentPath)
      break
    case 'header':
    default:
      break
  }
}

const startCreateEntry = (kind: 'file' | 'dir', parentPath: string) => {
  createDialog.value = {
    kind,
    parentPath,
    name: kind === 'file' ? '未命名.md' : '新建文件夹',
  }
  closeActionMenu()
}

/** 压缩包名去后缀：与后端 archive_stem 保持同一套后缀，避免默认文件夹名不一致。 */
const ARCHIVE_SUFFIXES = ['.tar.gz', '.tgz', '.tar', '.zip']
const archiveStem = (name: string) => {
  const lower = name.toLowerCase()
  const suffix = ARCHIVE_SUFFIXES.find((item) => lower.endsWith(item))
  return suffix ? name.slice(0, name.length - suffix.length) : name.replace(/\.[^.]+$/, '')
}

const openExtractDialog = (item: WorkspaceItemLike) => {
  extractDialog.value = {
    item,
    parentPath: currentPath.value,
    defaultName: archiveStem(resolveItemDisplayName(item)),
  }
  closeActionMenu()
}

const closeExtractDialog = () => {
  if (extractSubmitting.value) return
  extractDialog.value = null
}

const submitExtract = async (payload: { parentPath: string; name: string }) => {
  const state = extractDialog.value
  if (!state || extractSubmitting.value) return
  extractSubmitting.value = true
  try {
    const res = await extractWorkspaceArchive({
      archivePath: state.item.path,
      targetDir: payload.parentPath,
      subdir: payload.name,
    })
    const data = res.data?.data
    const count = Number(data?.file_count ?? 0)
    showToast(`已解压 ${count} 个文件到 ${data?.path || payload.parentPath}`, 'success')
    extractDialog.value = null
    await fetchDirectory(currentPath.value, { preserveSearch: true })
  } catch (error: any) {
    showToast(error?.response?.data?.detail || '解压失败，请稍后重试', 'error')
  } finally {
    extractSubmitting.value = false
  }
}

const cancelCreateEntry = () => {
  createDialog.value = null
}

const submitCreateEntry = async () => {
  if (!createDialog.value || createSubmitting.value) return
  const { kind, parentPath, name } = createDialog.value
  const trimmed = name.trim()
  if (!trimmed) {
    showToast('请输入名称', 'error')
    return
  }

  createSubmitting.value = true
  try {
    const res = await createWorkspaceEntry({
      parentPath,
      name: trimmed,
      kind,
      content: kind === 'file' ? '' : undefined,
    })
    const created = res.data?.data
    showToast(kind === 'file' ? '文件已创建' : '文件夹已创建', 'success')
    createDialog.value = null

    const refreshPath = normalizeFsPathForCompare(currentPath.value) === normalizeFsPathForCompare(parentPath)
      ? parentPath
      : currentPath.value
    await fetchDirectory(refreshPath, { preserveSearch: true })

    if (created && kind === 'file') {
      selectedItem.value = {
        path: created.path,
        name: created.name,
        is_dir: false,
        size: created.size ?? 0,
      }
    }
  } catch (error: any) {
    const detail = error?.response?.data?.detail || error?.response?.data?.message
    showToast(detail || '创建失败，请稍后重试', 'error')
  } finally {
    createSubmitting.value = false
  }
}

const fetchDirectory = async (pathUrl: string = '', options?: { preserveSearch?: boolean }) => {
  // 目标目录变了：旧的勾选已不在列表内，留着会让批量删除误伤其它目录的条目
  if (normalizeFsPathForCompare(pathUrl || '') !== normalizeFsPathForCompare(currentPath.value)) {
    resetBatchSelection()
  }
  loading.value = true
  selectedItem.value = null
  closeActionMenu()
  closeRecentFilesMenu()
  if (!options?.preserveSearch) {
    resetSearchState()
  }
  try {
    const res = await axios.get('/api/v1/chat/fs/list', {
      params: { path: pathUrl },
    })
    if (res.data?.data) {
      const data = res.data.data
      currentPath.value = data.current_path
      parentPath.value = data.parent_path
      isRoot.value = data.is_root
      isVirtualRoot.value = !!data.is_virtual_root
      scope.value = data.scope === 'admin_all' ? 'admin_all' : 'user_scoped'
      items.value = data.items
      currentPathWritable.value = !!data.writable
      if (data.user_workspace_root) {
        userWorkspaceRoot.value = data.user_workspace_root
      }
      if (data.is_root && !baseDir.value) {
        baseDir.value = data.current_path
      }
    }
  } catch (error) {
    console.error('Failed to fetch file system:', error)
    showToast('安全越权拦截或读取目录失败，请重试', 'error')
  } finally {
    loading.value = false
  }
}

watch(
  modelValue,
  (open) => {
    setMobileBodyScrollLock(!!open)
    if (open) {
      baseDir.value = ''
      // 抽屉关闭期间列表可能已被外部改动，重开时旧勾选没有意义
      resetBatchSelection()
      loadRecentFiles()
      fetchDirectory()
    } else {
      resetBatchSelection()
      closeQuickNavMenu()
      closeRecentFilesMenu()
    }
  },
  { immediate: true },
)

const onGlobalKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Escape') {
    closeQuickNavMenu()
    closeRecentFilesMenu()
    closeActionMenu()
    if (createDialog.value && !createSubmitting.value) {
      createDialog.value = null
    }
  }
  if (event.key === 'Enter' && createDialog.value && !createSubmitting.value) {
    event.preventDefault()
    submitCreateEntry()
  }
}

const onDocumentClick = (event: MouseEvent) => {
  closeActionMenu()
  const target = event.target as Node
  if (quickNavOpen.value) {
    if (!quickNavMenuRef.value?.contains(target) && !quickNavTriggerRef.value?.contains(target)) {
      closeQuickNavMenu()
    }
  }
  if (recentFilesOpen.value) {
    if (!recentFilesMenuRef.value?.contains(target) && !quickNavTriggerRef.value?.contains(target)) {
      closeRecentFilesMenu()
    }
  }
}

onMounted(() => {
  mobileMq = window.matchMedia('(max-width: 639px)')
  syncMobile()
  if (isMobile.value && pinned.value) {
    pinned.value = false
  }
  mobileMq.addEventListener('change', syncMobile)
  document.addEventListener('click', onDocumentClick)
  document.addEventListener('keydown', onGlobalKeydown)
  window.addEventListener('resize', syncRecentFilesMenuPosition)
  window.addEventListener('scroll', syncRecentFilesMenuPosition, true)
})

onUnmounted(() => {
  cancelUploadAutoDismiss()
  if (recentFilesPersistTimer) {
    clearTimeout(recentFilesPersistTimer)
    recentFilesPersistTimer = null
    void persistRecentFilesNow()
  }
  if (browserPrefsPersistTimer) {
    clearTimeout(browserPrefsPersistTimer)
    browserPrefsPersistTimer = null
    void persistBrowserPrefsNow()
  }
  mobileMq?.removeEventListener('change', syncMobile)
  document.removeEventListener('click', onDocumentClick)
  document.removeEventListener('keydown', onGlobalKeydown)
  window.removeEventListener('resize', syncRecentFilesMenuPosition)
  window.removeEventListener('scroll', syncRecentFilesMenuPosition, true)
  setMobileBodyScrollLock(false)
  resetSearchState()
})

const formatSize = (bytes: number) => {
  if (bytes === 0) return '-'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`
}

const formatTime = (timestamp: number) => {
  const date = new Date(timestamp * 1000)
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const getFileExt = (filename: string) => {
  const parts = filename.split('.')
  return parts.length > 1 ? `.${parts.pop()?.toLowerCase()}` : ''
}

const getItemVisual = (item: { name: string; is_dir: boolean }) =>
  resolveFileTypeVisual(item.name, item.is_dir)

const getRowVisual = (item: { name: string; is_dir: boolean; path: string }) => {
  if (isTrashRootItem(item)) {
    return {
      icon: '🗑️',
      label: '回收站',
      iconBg: 'bg-amber-50 dark:bg-amber-500/10',
      iconRing: 'ring-amber-200/60 dark:ring-amber-500/20',
      badgeBg: 'bg-amber-50 dark:bg-amber-500/10',
      badgeText: 'text-amber-700 dark:text-amber-300',
      category: 'folder' as FileTypeCategory,
      ext: '',
    }
  }
  if (isSessionDirItem(item)) {
    return {
      icon: '💬',
      label: '会话目录',
      iconBg: 'bg-sky-50 dark:bg-sky-500/10',
      iconRing: 'ring-sky-200/60 dark:ring-sky-500/20',
      badgeBg: 'bg-sky-50 dark:bg-sky-500/10',
      badgeText: 'text-sky-700 dark:text-sky-300',
      category: 'folder' as FileTypeCategory,
      ext: '',
    }
  }
  return getItemVisual(item)
}

const scopedHomeCrumbName = () => '工作空间'

const userHomeCrumbLabel = () => {
  const root = userWorkspaceRoot.value
  if (!root) return '我的目录'
  const base = root.split('/').filter(Boolean).pop()
  return base ? `我的目录 (${base})` : '我的目录'
}

const breadcrumbs = computed(() => {
  if (isVirtualRoot.value && scope.value === 'user_scoped') {
    return [{ name: scopedHomeCrumbName(), path: '', isBase: true, navigable: true }]
  }

  const crumbs: Array<{ name: string; path: string; isBase: boolean; navigable: boolean }> = [
    { name: scopedHomeCrumbName(), path: '', isBase: true, navigable: true },
  ]

  const root = userWorkspaceRoot.value
  const current = currentPath.value
  if (!current) return crumbs

  if (root && normalizeFsPathForCompare(current).startsWith(normalizeFsPathForCompare(root))) {
    crumbs.push({ name: userHomeCrumbLabel(), path: root, isBase: false, navigable: true })
    const rel = normalizeFsPathForCompare(current) === normalizeFsPathForCompare(root)
      ? ''
      : current.slice(root.length).replace(/^[/\\]+/, '')
    if (rel) {
      let acc = root
      for (const seg of rel.split(/[/\\]/).filter(Boolean)) {
        acc = `${acc}/${seg}`
        crumbs.push({
          name: seg === '.trash' ? '回收站' : seg,
          path: acc,
          isBase: false,
          navigable: true,
        })
      }
    }
    return crumbs
  }

  const base = baseDir.value
  if (base && current.startsWith(base)) {
    const relParts = current.slice(base.length).replace(/^[/\\]+/, '').split(/[/\\]/).filter(Boolean)
    let acc = base
    for (const seg of relParts) {
      acc = `${acc}/${seg}`
      const navigable = !(scope.value === 'user_scoped' && seg === 'agent_workspaces')
      crumbs.push({ name: seg, path: acc, isBase: false, navigable })
    }
  }

  return crumbs
})

const goParent = () => {
  if (isRoot.value || loading.value) return
  fetchDirectory(parentPath.value || '')
}

const clickBreadcrumb = (path: string) => {
  fetchDirectory(path)
}

const handleDoubleClick = (item: any) => {
  if (item.is_dir) {
    fetchDirectory(item.path)
    return
  }
  if (canPreviewWorkspaceFile(item.name)) {
    previewItem(item)
  }
}

const previewItem = (item: { path: string; name: string }) => {
  const displayName = isTrashView.value ? formatTrashItemName(item.name) : item.name
  if (!canPreviewWorkspaceFile(displayName)) {
    showToast('不支持预览该类型的文件', 'error')
    return
  }
  trackRecentFile({ path: item.path, name: displayName })
  emit('preview', { path: item.path, name: displayName })
}

const downloadItem = async (item: { path: string; name: string }) => {
  await downloadWorkspaceFile({
    path: item.path,
    name: item.name,
    showToast,
  })
}

const handleSelectRow = (item: any) => {
  closeActionMenu()
  selectedItem.value = item
}

const toggleMultiSelect = (item: { path: string }) => {
  const next = new Set(selectedPaths.value)
  if (next.has(item.path)) next.delete(item.path)
  else next.add(item.path)
  selectedPaths.value = next
}

const clearMultiSelect = () => {
  resetBatchSelection()
  batchDeleteConfirm.value = false
  multiSelectMode.value = false
}

const toggleMultiSelectMode = () => {
  multiSelectMode.value = !multiSelectMode.value
  if (!multiSelectMode.value) clearMultiSelect()
}

const handleRowClick = (item: any) => {
  closeActionMenu()
  if (isMobile.value && item.is_dir) {
    fetchDirectory(item.path)
    selectedItem.value = null
    return
  }
  handleSelectRow(item)
}

const finishSelect = (payload: {
  type: 'local_file' | 'local_dir'
  path: string
  name: string
  size: number
  ext: string
}) => {
  emit('select', payload)
  selectedItem.value = null
  if (!keepOpenOnSelect.value) {
    modelValue.value = false
  }
}

const mountItemToSession = (item: { path: string; name: string; is_dir: boolean; size?: number }) => {
  trackRecentFile({ path: item.path, name: item.name })
  if (!item.is_dir) {
    finishSelect({
      type: 'local_file',
      path: item.path,
      name: item.name,
      size: item.size ?? 0,
      ext: getFileExt(item.name),
    })
    return
  }
  finishSelect({
    type: 'local_dir',
    path: item.path,
    name: item.name,
    size: 0,
    ext: '',
  })
}

const mountSelectedBatch = () => {
  const selected = batchSelectedItems.value
  if (!selected.length) return
  for (const item of selected) {
    mountItemToSession(item)
  }
  clearMultiSelect()
}

const shouldShowRowActions = (item: { path: string }) => selectedItem.value?.path === item.path

const trashNavLink = computed(() => {
  if (!userWorkspaceRoot.value) return null
  return {
    key: 'trash',
    label: '回收站',
    path: `${userWorkspaceRoot.value}/.trash`,
    icon: markRaw(TrashIcon),
  }
})

const quickNavLinks = computed(() => {
  const links: Array<{
    key: string
    label: string
    path: string
    icon: Component
    disabled?: boolean
    disabledTitle?: string
  }> = []
  if (userWorkspaceRoot.value) {
    links.push({ key: 'home', label: '我的目录', path: userWorkspaceRoot.value, icon: markRaw(HomeIcon) })
    links.push({ key: 'docs', label: '我的文档', path: `${userWorkspaceRoot.value}/docs`, icon: markRaw(DocumentTextIcon) })
    if (props.conversationId) {
      links.push({
        key: 'session',
        label: '本会话目录',
        path: `${userWorkspaceRoot.value}/sessions/${props.conversationId}`,
        icon: markRaw(ChatBubbleLeftEllipsisIcon),
        disabled: !props.sessionStarted,
        disabledTitle: '发送首条消息后会话目录才会创建',
      })
    }
    links.push({ key: 'uploads', label: '上传目录', path: `${userWorkspaceRoot.value}/uploads`, icon: markRaw(ArrowUpTrayIcon) })
  }
  return links
})

const isQuickLinkActive = (path: string) => Boolean(path) && currentPath.value === path

const handleQuickNavClick = (link: { path: string; disabled?: boolean; disabledTitle?: string }) => {
  if (link.disabled) {
    showToast(link.disabledTitle || '当前不可用', 'info')
    return
  }
  closeQuickNavMenu()
  navigateQuick(link.path)
}

const navigateQuick = (path: string) => {
  if (!path) return fetchDirectory('')
  fetchDirectory(path)
}

const openUploadPicker = (parentPath?: string) => {
  const target = parentPath || currentPath.value
  if (!target || !canCreateInPath(target)) {
    showToast('仅可在本人工作目录内上传', 'error')
    return
  }
  uploadTargetPath.value = target
  uploadInputRef.value?.click()
}

const handleUploadFiles = async (event: Event) => {
  const input = event.target as HTMLInputElement
  const files = input.files
  const target = uploadTargetPath.value
  if (!files?.length || !target) return
  // 入队即返回：并发、进度、取消、重试全部交给队列引擎，此处不再串行阻塞
  // 同步撤销待执行的自动清除：若新任务在同一 tick 内就进入终态（例如超限被立即判失败），
  // active 会 true→false 一闪而过，下面那个 watcher 只看到最终值 false 就直接返回，
  // 上一轮的定时器不会被撤销，3 秒后会把这一批失败条目一起清掉。
  cancelUploadAutoDismiss()
  enqueueWorkspaceUpload(Array.from(files), target)
  input.value = ''
}

// 目录刷新收敛为「队列全部进入终态后刷一次」：
// 逐个文件刷新会让批量上传十几次互相打断滚动位置与选中项。
watch(
  () => uploadSnapshot.value.aggregate.active,
  async (active, wasActive) => {
    // 新一轮上传开始：撤销尚未到期的自动清除，否则刚入队的新任务会被一起清掉
    if (active) {
      cancelUploadAutoDismiss()
      return
    }
    if (!wasActive) return
    const { done, failed, canceled } = uploadSnapshot.value.aggregate
    if (done > 0) {
      await fetchDirectory(currentPath.value, { preserveSearch: true })
    }
    if (failed > 0) {
      showToast(`${done} 个文件上传成功，${failed} 个失败`, 'error')
    } else if (done > 0) {
      const tail = canceled > 0 ? `，${canceled} 个已取消` : ''
      showToast(`${done} 个文件上传完成${tail}`, 'success')
    }
    // 全部成功才自动清除；有失败/取消时保留条目，供重试和确认「可能已保存」
    if (shouldAutoDismissUploads(uploadSnapshot.value.aggregate)) {
      cancelUploadAutoDismiss()
      uploadAutoDismissTimer = setTimeout(() => {
        uploadAutoDismissTimer = null
        clearFinishedWorkspaceUpload()
      }, UPLOAD_PANEL_AUTO_DISMISS_MS)
    }
  },
)

const copyItemPath = async (path: string) => {
  try {
    await copyTextToClipboard(path)
    showToast('路径已复制', 'success')
  } catch {
    showToast('复制失败', 'error')
  }
  closeActionMenu()
}

const startRenameEntry = (item: { path: string; name: string; is_dir: boolean }) => {
  renameDialog.value = { path: item.path, name: item.name, isDir: item.is_dir }
  closeActionMenu()
}

const submitRename = async () => {
  if (!renameDialog.value) return
  try {
    await renameWorkspaceEntry(renameDialog.value.path, renameDialog.value.name.trim())
    showToast('重命名成功', 'success')
    renameDialog.value = null
    await fetchDirectory(currentPath.value, { preserveSearch: true })
  } catch (error: any) {
    showToast(error?.response?.data?.detail || '重命名失败', 'error')
  }
}

const confirmDeleteEntry = (item: { path: string; name: string; is_dir: boolean; is_public?: boolean }) => {
  deleteTarget.value = { path: item.path, name: item.name, isDir: item.is_dir, isPublic: isAdminScope.value && !!item.is_public }
  closeActionMenu()
}

const submitDelete = async () => {
  if (!deleteTarget.value) return
  try {
    await deleteWorkspaceEntry(deleteTarget.value.path)
    showToast(deleteTarget.value.isPublic ? '已永久删除' : '已移入回收站', 'success')
    deleteTarget.value = null
    await fetchDirectory(currentPath.value, { preserveSearch: true })
  } catch (error: any) {
    showToast(error?.response?.data?.detail || '删除失败', 'error')
  }
}

const restoreTrashItem = async (item: { path: string; name: string }) => {
  try {
    const res = await restoreWorkspaceEntry(item.path)
    const restored = res.data?.data
    showToast(`已恢复至我的目录：${restored?.name || formatTrashItemName(item.name)}`, 'success')
    selectedItem.value = null
    await fetchDirectory(currentPath.value, { preserveSearch: true })
  } catch (error: any) {
    showToast(error?.response?.data?.detail || '恢复失败', 'error')
  }
}

const confirmPurgeEntry = (item: { path: string; name: string; is_dir: boolean }) => {
  purgeTarget.value = { path: item.path, name: item.name, isDir: item.is_dir }
  closeActionMenu()
}

const submitPurge = async () => {
  if (!purgeTarget.value) return
  try {
    await purgeWorkspaceEntry(purgeTarget.value.path)
    showToast('已永久删除', 'success')
    purgeTarget.value = null
    selectedItem.value = null
    await fetchDirectory(currentPath.value, { preserveSearch: true })
  } catch (error: any) {
    showToast(error?.response?.data?.detail || '永久删除失败', 'error')
  }
}

const confirmEmptyTrash = () => {
  emptyTrashConfirm.value = true
  closeActionMenu()
}

const submitEmptyTrash = async () => {
  if (emptyTrashSubmitting.value) return
  emptyTrashSubmitting.value = true
  try {
    const res = await emptyWorkspaceTrash()
    const count = res.data?.data?.deleted_count ?? 0
    showToast(count > 0 ? `已清空回收站（${count} 项）` : '回收站已是空的', 'success')
    emptyTrashConfirm.value = false
    selectedItem.value = null
    await fetchDirectory(currentPath.value || trashDirPath.value, { preserveSearch: true })
  } catch (error: any) {
    showToast(error?.response?.data?.detail || '清空回收站失败', 'error')
  } finally {
    emptyTrashSubmitting.value = false
  }
}

/**
 * 系统级保护目录：data 根下的顶层固定目录，任何人（含管理员）不可删除。
 * 包括三个公共只读目录（docs/skills/branding）和用户工作区容器（agent_workspaces）。
 */
const isSystemProtectedDir = (item: { path: string; is_dir?: boolean }) => {
  if (!item.is_dir && !item.path) return false
  const base = baseDir.value
  if (!base) return false
  const PROTECTED_NAMES = new Set(['docs', 'skills', 'branding', 'agent_workspaces'])
  const normBase = normalizeFsPathForCompare(base)
  const normPath = normalizeFsPathForCompare(item.path)
  // 必须是 data 根的直接子目录
  if (!normPath.startsWith(`${normBase}/`)) return false
  const relative = normPath.slice(normBase.length + 1)
  // 没有再深一层斜杠，说明是直接子目录
  if (relative.includes('/')) return false
  return PROTECTED_NAMES.has(relative)
}

const canManageItem = (item: { path: string; is_dir?: boolean }) => {
  if (isSystemProtectedDir(item)) return false
  // 用户主目录本身不允许重命名或删除
  if (
    userWorkspaceRoot.value &&
    normalizeFsPathForCompare(item.path) === normalizeFsPathForCompare(userWorkspaceRoot.value)
  ) return false
  if (isAdminScope.value && !isTrashPath(item.path)) return true
  return isPathInUserWorkspace(item.path) && !isTrashPath(item.path)
}

// --------------------------------------------------------------------------
// 批量删除：先按「可管理」拆分选中项，再用通用 ConfirmModal 二次确认。
// 不许绕过确认直接删：一次误操作会同时移走多个文件，回收站虽然可恢复，
// 但用户很难回忆起自己刚才勾了哪些。
// --------------------------------------------------------------------------
const batchDeletePlan = computed(() =>
  planBatchDelete(batchSelectedItems.value, (item) => canManageItem(item) && !isTrashPath(item.path)),
)

/** 管理员删公共目录内容是永久删除（后端直接 rmtree/remove），确认框必须说清楚 */
const batchDeletePermanentCount = computed(
  () => batchDeletePlan.value.deletable.filter((item) => isAdminScope.value && isPublicItem(item)).length,
)

const batchDeleteDetails = computed(() =>
  batchDeletePlan.value.deletable.map((item) => resolveItemDisplayName(item)),
)

const batchDeleteMessage = computed(() => {
  if (batchDeleteSubmitting.value) {
    return `正在删除 ${batchDeleteProgress.value.done}/${batchDeleteProgress.value.total} 项…`
  }
  const plan = batchDeletePlan.value
  const permanentCount = batchDeletePermanentCount.value
  const trashedCount = plan.deletable.length - permanentCount
  const lines: string[] = []
  if (trashedCount > 0) lines.push(`${trashedCount} 项将移入回收站，可随时恢复。`)
  if (permanentCount > 0) lines.push(`${permanentCount} 项属于公共目录，将被永久删除且不可恢复。`)
  if (plan.skipped.length > 0) lines.push(`另有 ${plan.skipped.length} 项只读或受保护，将自动跳过。`)
  return lines.join('\n')
})

const requestBatchDelete = () => {
  if (isTrashView.value || batchDeleteSubmitting.value) return
  const plan = batchDeletePlan.value
  if (plan.deletable.length === 0) {
    showToast(plan.skipped.length > 0 ? '所选项目为只读或受保护，无法删除' : '请先选择要删除的项目', 'info')
    return
  }
  batchDeleteConfirm.value = true
  closeActionMenu()
}

const submitBatchDelete = async () => {
  if (batchDeleteSubmitting.value) return
  const plan = batchDeletePlan.value
  if (plan.deletable.length === 0) {
    batchDeleteConfirm.value = false
    return
  }
  const permanentPaths = new Set(
    plan.deletable.filter((item) => isAdminScope.value && isPublicItem(item)).map((item) => item.path),
  )
  batchDeleteSubmitting.value = true
  batchDeleteProgress.value = { done: 0, total: plan.deletable.length }
  const succeededPaths: string[] = []
  const failed: Array<{ name: string; error: string }> = []
  // 逐项调用既有单条删除接口：不新增后端，单项失败也不影响其余条目
  for (const item of plan.deletable) {
    try {
      await deleteWorkspaceEntry(item.path)
      succeededPaths.push(item.path)
    } catch (error: any) {
      failed.push({
        name: resolveItemDisplayName(item),
        error: error?.response?.data?.detail || '删除失败',
      })
    }
    batchDeleteProgress.value = {
      done: batchDeleteProgress.value.done + 1,
      total: plan.deletable.length,
    }
  }
  batchDeleteSubmitting.value = false
  batchDeleteConfirm.value = false

  // 成功项移出勾选；失败项保留，用户可以直接重试
  const next = new Set(selectedPaths.value)
  for (const path of succeededPaths) next.delete(path)
  selectedPaths.value = next

  const permanentDeleted = succeededPaths.filter((path) => permanentPaths.has(path)).length
  const trashed = succeededPaths.length - permanentDeleted
  const summary: string[] = []
  if (trashed > 0) summary.push(`${trashed} 项已移入回收站`)
  if (permanentDeleted > 0) summary.push(`${permanentDeleted} 项已永久删除`)
  if (plan.skipped.length > 0) summary.push(`${plan.skipped.length} 项只读已跳过`)
  if (summary.length > 0) showToast(summary.join('，'), 'success')
  if (failed.length > 0) {
    showToast(`${failed.length} 项删除失败：${failed[0].name} — ${failed[0].error}`, 'error')
  }

  await fetchDirectory(currentPath.value, { preserveSearch: true })
}

const handleTouchStart = (event: TouchEvent, item: { path: string; name: string; is_dir: boolean }) => {
  if (!isMobile.value) return
  const parentPath = item.is_dir ? item.path : currentPath.value
  const itemAllowed = canManageItem(item) || isTrashListItem(item) || isTrashRootItem(item)
  if (!canCreateInPath(parentPath) && !itemAllowed) return
  longPressTimer.value = setTimeout(() => {
    const touch = event.touches[0]
    if (touch) openContextMenu({ clientX: touch.clientX, clientY: touch.clientY } as MouseEvent, parentPath, item)
  }, 550)
}

const handleTouchEnd = () => {
  if (longPressTimer.value) clearTimeout(longPressTimer.value)
}

const refreshDirectory = async (highlightPathArg?: string) => {
  if (highlightPathArg) highlightedPath.value = highlightPathArg
  await fetchDirectory(currentPath.value || '', { preserveSearch: true })
  if (highlightPathArg) {
    setTimeout(() => { highlightedPath.value = '' }, 3000)
  }
}

defineExpose({ refreshDirectory })

const sheetEnterFrom = computed(() => (isMobile.value ? 'translate-y-full' : 'translate-x-full'))
const sheetLeaveTo = computed(() => (isMobile.value ? 'translate-y-full' : 'translate-x-full'))

const pinButtonTitle = computed(() => {
  if (pinned.value) {
    return isMobile.value ? '取消钉住（恢复全屏抽屉）' : '取消钉住（恢复遮罩模式）'
  }
  return isMobile.value
    ? '钉住底部抽屉（去掉遮罩，可继续聊天）'
    : '钉住侧栏（去掉遮罩，可继续浏览聊天）'
})

const pinnedContainerClass = computed(() => {
  if (!pinned.value) return ''
  return isMobile.value
    ? 'fixed inset-x-0 bottom-0 max-w-full flex flex-col justify-end pointer-events-none'
    : 'fixed inset-y-0 right-0 max-w-full flex pointer-events-none'
})

const pinnedContainerStyle = computed(() => {
  if (!pinned.value || isMobile.value) return undefined
  return { right: `${Math.max(0, props.pinnedDockRight || 0)}px` }
})

const closeDrawer = () => {
  modelValue.value = false
}

const CANVAS_WIDTH_STORAGE_KEY = 'nanzi_workspace_browser_drawer_width'
const customWidth = ref<number | null>(null)
const isResizing = ref(false)

const loadCustomWidth = () => {
  if (typeof window === 'undefined') return
  const saved = localStorage.getItem(CANVAS_WIDTH_STORAGE_KEY)
  if (saved) {
    const parsed = parseInt(saved, 10)
    if (!isNaN(parsed) && parsed >= 320) {
      customWidth.value = parsed
      drawerWidth.value = parsed
    }
  }
}

const startResize = (e: MouseEvent) => {
  if (isMobile.value) return
  e.preventDefault()
  isResizing.value = true
  document.body.classList.add('select-none')
  window.addEventListener('mousemove', handleResizing)
  window.addEventListener('mouseup', stopResize)
}

const handleResizing = (e: MouseEvent) => {
  if (!isResizing.value) return
  const viewportWidth = window.innerWidth
  const newWidth = viewportWidth - e.clientX
  const minWidth = 320
  const maxWidth = Math.max(minWidth, viewportWidth - 300)
  const clampedWidth = Math.min(maxWidth, Math.max(minWidth, newWidth))
  customWidth.value = clampedWidth
  drawerWidth.value = clampedWidth
}

const stopResize = () => {
  if (!isResizing.value) return
  isResizing.value = false
  document.body.classList.remove('select-none')
  window.removeEventListener('mousemove', handleResizing)
  window.removeEventListener('mouseup', stopResize)
  if (customWidth.value) {
    localStorage.setItem(CANVAS_WIDTH_STORAGE_KEY, String(customWidth.value))
  }
}

const resetWidth = () => {
  customWidth.value = null
  drawerWidth.value = 448
  localStorage.removeItem(CANVAS_WIDTH_STORAGE_KEY)
}

const drawerPanelStyle = computed(() => {
  if (isMobile.value) return {}
  if (customWidth.value) {
    return {
      width: `${customWidth.value}px`,
      maxWidth: `calc(100vw - 300px)`,
    }
  }
  return {}
})

onMounted(() => {
  loadCustomWidth()
})

onUnmounted(() => {
  stopResize()
})
</script>

<template>
  <teleport to="body">
    <div
      v-show="modelValue"
      :style="pinnedContainerStyle"
      :class="[
        'z-[125]',
        pinned
          ? pinnedContainerClass
          : isMobile
            ? 'fixed inset-0 flex flex-col overflow-hidden'
            : 'fixed inset-0 overflow-hidden',
      ]"
    >
      <transition
        v-if="!pinned"
        enter-active-class="ease-out duration-300"
        enter-from-class="opacity-0"
        enter-to-class="opacity-100"
        leave-active-class="ease-in duration-200"
        leave-from-class="opacity-100"
        leave-to-class="opacity-0"
      >
        <div
          v-show="modelValue"
          :class="[
            'bg-gray-500/30 backdrop-blur-xs transition-opacity',
            isMobile ? 'flex-1 min-h-0 w-full' : 'absolute inset-0',
          ]"
          @click="closeDrawer"
        />
      </transition>

      <div
        :class="[
          pinned
            ? isMobile
              ? 'w-full flex pointer-events-auto min-h-0 max-h-[58%]'
              : 'h-full flex pointer-events-auto'
            : isMobile
              ? 'w-full flex justify-center min-h-0 max-h-[92%] shrink-0'
              : 'absolute inset-y-0 right-0 pl-0 sm:pl-10 max-w-full flex',
        ]"
      >
        <transition
          enter-active-class="transform transition ease-in-out duration-300"
          :enter-from-class="sheetEnterFrom"
          enter-to-class="translate-x-0 translate-y-0"
          leave-active-class="transform transition ease-in-out duration-300"
          leave-from-class="translate-x-0 translate-y-0"
          :leave-to-class="sheetLeaveTo"
        >
          <div
            v-show="modelValue"
            :class="[
              'bg-white dark:bg-gray-900 shadow-2xl flex flex-col relative z-10 min-h-0 pb-[env(safe-area-inset-bottom,0px)]',
              isResizing ? 'transition-none select-none' : 'transition-all duration-300',
              isMobile
                ? 'w-full max-w-none rounded-t-2xl border-t border-gray-200 dark:border-gray-800 h-full max-h-full'
                : 'w-screen max-w-[min(100vw,28rem)] h-full border-l border-gray-200 dark:border-gray-800',
            ]"
            :style="drawerPanelStyle"
          >
            <!-- Drag Overlay Guard during resizing -->
            <div v-if="isResizing" class="fixed inset-0 z-[300] cursor-col-resize select-none" />

            <!-- Resizer Handle Bar -->
            <div
              v-if="!isMobile"
              class="absolute top-0 bottom-0 -left-1.5 w-3 z-50 flex items-center justify-center cursor-col-resize group select-none touch-none transition-colors"
              :class="isResizing ? 'bg-primary/30' : 'hover:bg-primary/20'"
              title="按住左右拖拽调整工作空间宽度（双击重置）"
              @mousedown="startResize"
              @dblclick="resetWidth"
            >
              <div
                class="w-1 h-8 rounded-full transition-all flex flex-col items-center justify-center gap-0.5"
                :class="isResizing ? 'bg-primary scale-110 shadow-sm' : 'bg-gray-300 dark:bg-gray-600 group-hover:bg-primary group-hover:scale-105'"
              >
                <div class="w-0.5 h-0.5 rounded-full bg-white dark:bg-gray-900" />
                <div class="w-0.5 h-0.5 rounded-full bg-white dark:bg-gray-900" />
                <div class="w-0.5 h-0.5 rounded-full bg-white dark:bg-gray-900" />
              </div>
            </div>
            <div
              v-if="isMobile"
              class="shrink-0 flex justify-center pt-2 pb-1"
              aria-hidden="true"
            >
              <div class="w-10 h-1 rounded-full bg-gray-300 dark:bg-gray-600" />
            </div>

            <div
              class="shrink-0 px-4 py-3 sm:py-4 border-b border-gray-150 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-800/20 flex items-center justify-between gap-2"
            >
              <span class="text-sm font-bold text-gray-900 dark:text-gray-100 flex items-center gap-1.5 select-none min-w-0">
                <ComputerDesktopIcon class="h-5 w-5 flex-shrink-0 text-blue-500" aria-hidden="true" />
                <span class="truncate">浏览工作空间</span>
                <span
                  v-if="pinned"
                  class="hidden sm:inline-flex shrink-0 items-center px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wide whitespace-nowrap text-blue-600 bg-blue-50 border border-blue-100 dark:text-blue-300 dark:bg-blue-500/10 dark:border-blue-500/20"
                >
                  已钉住
                </span>
              </span>
              <div class="flex items-center gap-2 flex-shrink-0">
                <label
                  class="hidden sm:flex items-center gap-1.5 text-[10px] text-gray-500 dark:text-gray-400 cursor-pointer select-none whitespace-nowrap"
                  title="开启后确认添加不会关闭侧栏，可连续选择"
                >
                  <input
                    v-model="keepOpenOnSelect"
                    type="checkbox"
                    class="rounded border-gray-300 text-primary focus:ring-primary/30"
                  />
                  添加后保持
                </label>
                <button
                  type="button"
                  class="hidden sm:inline-flex text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 p-1 rounded-md hover:bg-gray-150 dark:hover:bg-gray-800 transition-colors"
                  :class="{ 'text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-500/10': pinned }"
                  :title="pinButtonTitle"
                  :aria-label="pinned ? '取消钉住' : '钉住侧栏'"
                  @click="pinned = !pinned"
                >
                  <svg
                    v-if="pinned"
                    class="h-5 w-5 -rotate-45"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    aria-hidden="true"
                  >
                    <path d="M12 17v5" />
                    <path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v3.76" />
                  </svg>
                  <svg
                    v-else
                    class="h-5 w-5"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    aria-hidden="true"
                  >
                    <path d="M12 17v5" />
                    <path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v3.76" />
                  </svg>
                </button>
                <button
                  type="button"
                  class="text-gray-400 hover:text-gray-500 dark:hover:text-gray-300 p-1.5 rounded-md hover:bg-gray-150 dark:hover:bg-gray-800 transition-colors"
                  title="关闭 (Esc)"
                  aria-label="关闭工作空间"
                  @click="closeDrawer"
                >
                  <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            </div>

            <div class="workspace-drawer-scroll flex-1 overflow-y-auto overscroll-y-contain p-3 sm:p-4 bg-white dark:bg-gray-900/60 min-h-0 touch-pan-y flex flex-col">
              <div class="flex flex-col flex-1 min-h-0">
                <div
                  v-if="isTrashView"
                  class="mb-2 px-2.5 py-2 rounded-lg text-[10px] text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-500/10 border border-amber-200/60 dark:border-amber-500/20 shrink-0 flex items-center justify-between gap-2"
                >
                  <span>已删文件暂存于此，可恢复至目录根，或永久删除。</span>
                  <button
                    v-if="displayItems.length > 0"
                    type="button"
                    class="shrink-0 px-2 py-1 rounded-md text-[10px] font-bold text-red-600 dark:text-red-400 bg-white/80 dark:bg-gray-900/60 border border-red-200/60 dark:border-red-500/30 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors focus:outline-none"
                    @click="confirmEmptyTrash"
                  >
                    清空回收站
                  </button>
                </div>

                <div class="flex items-center gap-2 mb-2 shrink-0">
                  <div class="flex items-center space-x-2 px-2 py-1.5 bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-gray-100 dark:border-gray-800 flex-1 min-w-0 overflow-x-auto no-scrollbar">
                    <button
                      ref="quickNavTriggerRef"
                      type="button"
                      class="inline-flex items-center gap-0.5 px-1.5 py-1 rounded-md text-[10px] font-bold shrink-0 transition-colors focus:outline-none"
                      :class="quickNavOpen || recentFilesOpen
                        ? 'text-primary bg-primary/10'
                        : 'text-gray-500 hover:text-primary hover:bg-gray-200/70 dark:hover:bg-gray-700'"
                      title="快捷入口"
                      aria-label="快捷入口"
                      aria-haspopup="menu"
                      :aria-expanded="quickNavOpen ? 'true' : 'false'"
                      @click.stop="toggleQuickNavMenu"
                    >
                      <span>快捷</span>
                      <svg
                        class="w-3 h-3 transition-transform"
                        :class="quickNavOpen ? 'rotate-180' : ''"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                        aria-hidden="true"
                      >
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7" />
                      </svg>
                    </button>
                    <span class="text-gray-300 dark:text-gray-700 shrink-0">|</span>
                    <button
                      class="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-500 disabled:opacity-30 disabled:hover:bg-transparent transition-colors shrink-0"
                      :disabled="isRoot || loading"
                      title="返回上一级"
                      @click="goParent"
                    >
                      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15 19l-7-7 7-7" />
                      </svg>
                    </button>
                    <span class="text-gray-300 dark:text-gray-700 shrink-0">|</span>
                    <div class="flex items-center space-x-1 whitespace-nowrap text-xs min-w-0 flex-1 overflow-x-auto no-scrollbar">
                      <template v-for="(crumb, idx) in breadcrumbs" :key="idx">
                        <span v-if="idx > 0" class="text-gray-400 dark:text-gray-600">/</span>
                        <button
                          v-if="crumb.navigable"
                          class="font-medium hover:text-primary transition-colors focus:outline-none"
                          :class="idx === breadcrumbs.length - 1 ? 'text-gray-800 dark:text-gray-200 font-bold' : 'text-gray-400 dark:text-gray-500'"
                          :disabled="loading"
                          @click="clickBreadcrumb(crumb.path)"
                        >
                          {{ crumb.name }}
                        </button>
                        <span
                          v-else
                          class="font-medium text-gray-400/70 dark:text-gray-500/70 cursor-default select-none"
                          :class="idx === breadcrumbs.length - 1 ? 'text-gray-600 dark:text-gray-400 font-bold' : ''"
                          title="无权访问该目录"
                        >
                          {{ crumb.name }}
                        </span>
                      </template>
                      <span
                        v-if="!currentPathWritable && !isVirtualRoot"
                        class="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-50 dark:bg-amber-500/10 text-amber-700 dark:text-amber-300 ring-1 ring-amber-200/60 dark:ring-amber-500/20 shrink-0 ml-auto"
                        title="当前处于公共只读目录，禁止新建、修改或删除"
                      >
                        🔒 只读
                      </span>
                    </div>
                  </div>
                </div>

                <div class="flex items-center gap-2 mb-2 shrink-0">
                  <div class="relative flex-1 min-w-0">
                    <span class="absolute inset-y-0 left-0 pl-2.5 flex items-center pointer-events-none text-gray-400">
                      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                      </svg>
                    </span>
                    <input
                      v-model="searchQuery"
                      type="search"
                      placeholder="搜索文件名..."
                      class="w-full pl-8 pr-8 py-2 text-xs bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg focus:ring-2 focus:ring-primary/30 focus:border-primary focus:outline-none transition-all"
                    />
                    <button
                      v-if="searchQuery"
                      type="button"
                      class="absolute inset-y-0 right-0 pr-2.5 flex items-center text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
                      title="清除搜索"
                      @click="clearSearch"
                    >
                      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  </div>
                  <button
                    type="button"
                    class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-gray-200 bg-white text-gray-500 transition-colors hover:border-primary/30 hover:text-primary disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300"
                    :disabled="loading || searchLoading"
                    title="刷新当前目录"
                    aria-label="刷新当前目录"
                    @click="refreshDirectory()"
                  >
                    <ArrowPathIcon class="h-4 w-4" :class="{ 'animate-spin': loading }" aria-hidden="true" />
                  </button>
                  <label
                    class="inline-flex items-center gap-1 shrink-0 text-[10px] font-bold text-gray-500 dark:text-gray-400 cursor-pointer select-none"
                    title="搜索时是否包含子目录"
                  >
                    <input
                      v-model="includeSubdirs"
                      type="checkbox"
                      class="rounded border-gray-300 text-primary focus:ring-primary/30"
                    />
                    含子目录
                  </label>
                  <label
                    class="inline-flex items-center gap-1 shrink-0 text-[10px] font-bold cursor-pointer select-none"
                    :class="showTypeFilter || isTypeFilterActive ? 'text-primary' : 'text-gray-500 dark:text-gray-400'"
                    title="显示文件类型筛选"
                  >
                    <input
                      v-model="showTypeFilter"
                      type="checkbox"
                      class="rounded border-gray-300 text-primary focus:ring-primary/30"
                    />
                    类型
                  </label>
                </div>

                <div
                  v-if="showTypeFilter"
                  class="flex items-center gap-1.5 mb-2 shrink-0 min-w-0"
                >
                  <div class="flex-1 min-w-0 overflow-x-auto no-scrollbar">
                    <div class="flex items-center gap-1.5 pr-1">
                      <button
                        v-for="opt in TYPE_FILTER_OPTIONS"
                        :key="opt.key"
                        type="button"
                        class="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[9px] font-bold border transition-all whitespace-nowrap focus:outline-none"
                        :class="typeFilter === opt.key
                          ? 'bg-primary/10 border-primary/30 text-primary shadow-sm'
                          : 'bg-white dark:bg-gray-900 border-gray-200 dark:border-gray-700 text-gray-500 hover:border-primary/20 hover:text-primary'"
                        @click="setTypeFilter(opt.key)"
                      >
                        <span class="text-[10px] leading-none">{{ opt.icon }}</span>
                        {{ opt.label }}
                      </button>
                    </div>
                  </div>
                </div>

                <div v-if="isSearchActive || isTypeFilterActive" class="flex items-center justify-between px-1 mb-2 text-[10px] text-gray-400 shrink-0">
                  <span>
                    <template v-if="isRecursiveListingActive && searchLoading">正在搜索...</template>
                    <template v-else>
                      找到 {{ displayItems.length }} 项
                      <span v-if="isTypeFilterActive" class="text-primary/80">（{{ activeTypeFilterLabel }}）</span>
                    </template>
                    <span v-if="searchTruncated" class="text-amber-600 dark:text-amber-400 ml-1">（结果已截断，请缩小范围）</span>
                  </span>
                  <span v-if="isRecursiveListingActive" class="font-mono truncate max-w-[50%]">{{ isRoot && scope === 'user_scoped' ? '全部授权目录' : currentPath }}</span>
                  <span v-else-if="isSearchActive">仅当前目录</span>
                </div>

                <div class="flex-1 min-h-[240px] border border-gray-100 dark:border-gray-800 rounded-xl overflow-hidden flex flex-col relative bg-gray-50/10">
                  <div class="flex items-center justify-start border-b border-gray-100 px-3 py-2 dark:border-gray-800">
                    <div class="inline-flex rounded-lg border border-gray-200 bg-white p-0.5 dark:border-gray-700 dark:bg-gray-900" role="group" aria-label="文件视图">
                      <button
                        type="button"
                        class="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[10px] font-bold transition-colors"
                        :class="viewMode === 'list' ? 'bg-primary/10 text-primary' : 'text-gray-500 hover:text-primary'"
                        title="切换到列表视图"
                        aria-label="切换到列表视图"
                        @click="viewMode = 'list'"
                      >
                        <ListBulletIcon class="h-3.5 w-3.5" aria-hidden="true" />
                        列表
                      </button>
                      <button
                        type="button"
                        class="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[10px] font-bold transition-colors"
                        :class="viewMode === 'grid' ? 'bg-primary/10 text-primary' : 'text-gray-500 hover:text-primary'"
                        title="切换到文件夹视图"
                        aria-label="切换到文件夹视图"
                        @click="viewMode = 'grid'"
                      >
                        <Squares2X2Icon class="h-3.5 w-3.5" aria-hidden="true" />
                        文件夹
                      </button>
                    </div>
                  </div>
                  <div v-if="viewMode === 'list'" class="grid grid-cols-12 gap-2 px-4 py-2 border-b border-gray-100 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-900/20 text-[10px] font-black text-gray-400 tracking-wider shrink-0">
                    <div v-if="multiSelectMode" class="col-span-1 flex items-center">
                      <input
                        type="checkbox"
                        class="rounded border-gray-300 text-primary"
                        :checked="batchSelectAllState === 'all'"
                        :indeterminate="batchSelectAllState === 'partial'"
                        :disabled="batchSelectablePaths.length === 0"
                        :title="batchSelectAllState === 'all' ? '取消全选' : '全选当前列表'"
                        :aria-label="batchSelectAllState === 'all' ? '取消全选' : '全选当前列表'"
                        @click.stop="toggleSelectAllItems"
                      >
                    </div>
                    <button
                      type="button"
                      class="inline-flex items-center gap-1 text-left hover:text-primary transition-colors focus:outline-none"
                      :class="[
                        multiSelectMode ? 'col-span-6 sm:col-span-5' : 'col-span-7 sm:col-span-6',
                        sortKey === 'name' ? 'text-primary' : '',
                      ]"
                      @click="toggleSort('name')"
                    >
                      <span>名称</span>
                      <span v-if="activeSortArrow('name')" class="text-[9px] opacity-80 ml-0.5">{{ activeSortArrow('name') }}</span>
                    </button>
                    <button
                      type="button"
                      class="col-span-2 hidden sm:inline-flex items-center hover:text-primary transition-colors focus:outline-none"
                      :class="sortKey === 'type' ? 'text-primary' : ''"
                      @click="toggleSort('type')"
                    >
                      <span>类型</span>
                      <span v-if="activeSortArrow('type')" class="text-[9px] opacity-80 ml-0.5">{{ activeSortArrow('type') }}</span>
                    </button>
                    <div class="col-span-5 sm:col-span-4 text-right leading-tight">
                      <button
                        type="button"
                        class="inline-flex items-center justify-end w-full hover:text-primary transition-colors focus:outline-none"
                        :class="sortKey === 'size' ? 'text-primary' : ''"
                        @click="toggleSort('size')"
                      >
                        <span>大小</span>
                        <span v-if="activeSortArrow('size')" class="text-[9px] opacity-80 ml-0.5">{{ activeSortArrow('size') }}</span>
                      </button>
                      <button
                        type="button"
                        class="inline-flex items-center justify-end w-full text-[9px] font-normal mt-0.5 hover:text-primary transition-colors focus:outline-none"
                        :class="sortKey === 'mtime' ? 'text-primary font-bold' : 'text-gray-300 dark:text-gray-600'"
                        @click="toggleSort('mtime')"
                      >
                        <span>修改时间</span>
                        <span v-if="activeSortArrow('mtime')" class="opacity-80 ml-0.5">{{ activeSortArrow('mtime') }}</span>
                      </button>
                    </div>
                  </div>

                  <div
                    v-if="loading || (isRecursiveListingActive && searchLoading)"
                    class="absolute inset-0 bg-white/70 dark:bg-gray-900/70 z-10 flex items-center justify-center"
                  >
                    <div class="flex flex-col items-center space-y-2">
                      <div class="w-8 h-8 border-4 border-primary border-t-transparent rounded-full animate-spin" />
                      <span class="text-xs text-gray-400 font-medium">正在读取文件系统...</span>
                    </div>
                  </div>

                  <div v-if="viewMode === 'list'" class="flex-1 overflow-y-auto custom-scrollbar p-1 pb-16 min-h-0" @contextmenu="handleListContextMenu" @scroll.passive="closeActionMenu">
                    <div v-if="displayItems.length === 0 && !searchLoading" class="h-full flex flex-col items-center justify-center text-gray-400 py-12 px-4">
                      <span class="text-4xl mb-2">{{ isRecursiveListingActive || isSearchActive ? '🔍' : '📂' }}</span>
                      <span class="text-xs font-bold">{{ displayEmptyHint }}</span>
                      <div v-if="currentPathWritable && !isRecursiveListingActive && !isSearchActive" class="mt-4 flex flex-wrap items-center justify-center gap-2">
                        <button type="button" class="px-3 py-1.5 rounded-lg text-[10px] font-bold bg-primary text-white" @click="createDialog = { kind: 'file', parentPath: currentPath, name: '未命名.md' }">新建文件</button>
                        <button type="button" class="px-3 py-1.5 rounded-lg text-[10px] font-bold border border-gray-200 dark:border-gray-700" @click="createDialog = { kind: 'dir', parentPath: currentPath, name: '新建文件夹' }">新建文件夹</button>
                        <button type="button" class="px-3 py-1.5 rounded-lg text-[10px] font-bold border border-gray-200 dark:border-gray-700" @click="openUploadPicker()">上传文件</button>
                      </div>
                    </div>

                    <div v-else class="space-y-0.5">
                      <div
                        v-for="item in paginatedDisplayItems"
                        :key="item.path"
                        class="grid grid-cols-12 gap-2 px-3 py-2 rounded-lg cursor-pointer transition-all duration-150 select-none group items-center"
                        :class="[
                          selectedItem?.path === item.path || selectedPaths.has(item.path) || highlightedPath === item.path
                            ? 'bg-primary/10 dark:bg-primary/20 ring-1 ring-primary/20'
                            : 'hover:bg-gray-100/50 dark:hover:bg-gray-800/40',
                        ]"
                        @click="multiSelectMode ? toggleMultiSelect(item) : handleRowClick(item)"
                        @dblclick="handleDoubleClick(item)"
                        @contextmenu="handleItemContextMenu($event, item)"
                        @touchstart.passive="handleTouchStart($event, item)"
                        @touchend="handleTouchEnd"
                        @touchmove="handleTouchEnd"
                      >
                        <div v-if="multiSelectMode" class="col-span-1 flex items-center">
                          <input type="checkbox" class="rounded border-gray-300 text-primary" :checked="selectedPaths.has(item.path)" @click.stop="toggleMultiSelect(item)">
                        </div>
                        <template v-for="visual in [getRowVisual(item)]" :key="item.path + '-visual'">
                          <div
                            class="flex items-start gap-2.5 min-w-0"
                            :class="multiSelectMode ? 'col-span-6 sm:col-span-5' : 'col-span-7 sm:col-span-6'"
                          >
                            <div
                              class="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 ring-1 text-sm mt-0.5"
                              :class="[visual.iconBg, visual.iconRing]"
                            >
                              {{ visual.icon }}
                            </div>
                            <div class="min-w-0 flex-1">
                              <div
                                class="text-xs font-medium text-gray-700 dark:text-gray-200 break-all leading-snug"
                                :class="selectedItem?.path === item.path ? 'text-primary font-bold' : ''"
                                :title="resolveItemDisplayName(item)"
                              >
                                {{ resolveItemDisplayName(item) }}
                              </div>
                              <span
                                v-if="item.is_user_workspace"
                                class="inline-flex mt-1 px-1.5 py-0.5 rounded-md text-[9px] font-bold bg-primary/10 text-primary ring-1 ring-primary/20"
                                title="当前登录用户的 AI 会话工作目录"
                              >
                                用户工作目录
                              </span>
                              <span
                                v-else-if="isPublicItem(item)"
                                class="inline-flex items-center gap-1 mt-1 px-1.5 py-0.5 rounded-md text-[9px] font-bold bg-amber-50 dark:bg-amber-500/10 text-amber-700 dark:text-amber-300 ring-1 ring-amber-200/60 dark:ring-amber-500/20"
                                :title="isAdminScope ? '公共目录（管理员可编辑）' : '公共只读目录或资源，禁止修改或删除'"
                              >
                                <span>公共目录</span>
                                <span v-if="!isAdminScope" class="text-[8px] opacity-75 font-normal">· 只读</span>
                              </span>
                              <span
                                v-if="isSessionDirItem(item)"
                                class="inline-flex sm:hidden mt-1 px-1.5 py-0.5 rounded-md text-[9px] font-bold bg-sky-50 dark:bg-sky-500/10 text-sky-700 dark:text-sky-300 ring-1 ring-sky-200/60 dark:ring-sky-500/20"
                              >
                                会话目录
                              </span>
                              <div
                                v-if="isRecursiveListingActive && getItemLocationHint(item.path)"
                                class="text-[9px] text-gray-400 dark:text-gray-500 mt-0.5 truncate font-mono"
                                :title="item.path"
                              >
                                📂 {{ getItemLocationHint(item.path) }}
                              </div>
                            </div>
                          </div>
                          <div class="col-span-2 hidden sm:flex items-center">
                            <span
                              class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold whitespace-nowrap"
                              :class="[visual.badgeBg, visual.badgeText]"
                            >
                              {{ visual.label }}
                            </span>
                          </div>
                        </template>
                        <div class="col-span-5 sm:col-span-4 flex items-center justify-end gap-1.5">
                          <div class="text-right text-[10px] font-mono text-gray-400 dark:text-gray-500 leading-tight whitespace-nowrap">
                            <div>{{ item.is_dir ? '—' : formatSize(item.size) }}</div>
                            <div class="text-[9px] text-gray-400/80 dark:text-gray-500/80">{{ formatTime(item.mtime) }}</div>
                          </div>
                          <button
                            v-if="!multiSelectMode && (shouldShowRowActions(item) || !isMobile)"
                            type="button"
                            class="relative p-1 rounded-md border shadow-sm transition-colors before:absolute before:-inset-1.5 before:content-[''] focus:outline-none focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-primary/40"
                            :class="[
                              isRowActionMenuOpen(item)
                                ? 'border-primary/60 bg-primary/10 text-primary opacity-100'
                                : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-500 dark:text-gray-400 hover:border-primary/50 hover:bg-primary/5 hover:text-primary',
                              shouldShowRowActions(item)
                                ? 'opacity-100'
                                : 'opacity-40 group-hover:opacity-100 group-focus-within:opacity-100',
                            ]"
                            aria-haspopup="menu"
                            aria-label="更多操作"
                            :aria-expanded="isRowActionMenuOpen(item) ? 'true' : 'false'"
                            :title="`${resolveItemDisplayName(item)} 的更多操作`"
                            @mousedown.stop
                            @click.stop="toggleRowActionMenu($event, item)"
                          >
                            <EllipsisVerticalIcon class="h-4 w-4" aria-hidden="true" />
                          </button>
                        </div>
                      </div>
                      <div v-if="hasMoreListItems" class="py-3 text-center">
                        <button type="button" class="text-[10px] font-bold text-primary hover:opacity-80" @click="loadMoreListItems">
                          加载更多 ({{ paginatedDisplayItems.length }}/{{ sortedDisplayItems.length }})
                        </button>
                      </div>
                    </div>
                  </div>

                  <div v-else class="flex-1 overflow-y-auto custom-scrollbar p-2 pb-16 min-h-0" @contextmenu="handleListContextMenu" @scroll.passive="closeActionMenu">
                    <div v-if="displayItems.length === 0 && !searchLoading" class="h-full flex flex-col items-center justify-center text-gray-400 py-12 px-4">
                      <span class="text-4xl mb-2">{{ isRecursiveListingActive || isSearchActive ? '🔍' : '📂' }}</span>
                      <span class="text-xs font-bold">{{ displayEmptyHint }}</span>
                    </div>
                    <div v-else class="grid grid-cols-2 gap-2 sm:grid-cols-3">
                      <div
                        v-for="item in paginatedDisplayItems"
                        :key="item.path"
                        class="group min-w-0 cursor-pointer rounded-xl border border-gray-100 bg-white p-2.5 transition-all hover:border-primary/30 hover:bg-primary/5 dark:border-gray-800 dark:bg-gray-900/40"
                        :class="selectedItem?.path === item.path || selectedPaths.has(item.path) || highlightedPath === item.path ? 'border-primary/40 bg-primary/10 ring-1 ring-primary/20' : ''"
                        @click="multiSelectMode ? toggleMultiSelect(item) : handleRowClick(item)"
                        @dblclick="handleDoubleClick(item)"
                        @contextmenu="handleItemContextMenu($event, item)"
                        @touchstart.passive="handleTouchStart($event, item)"
                        @touchend="handleTouchEnd"
                        @touchmove="handleTouchEnd"
                      >
                        <div class="flex items-start justify-between gap-1">
                          <div
                            class="flex h-10 w-10 items-center justify-center rounded-lg text-lg"
                            :class="[getRowVisual(item).iconBg, getRowVisual(item).iconRing]"
                          >
                            {{ getRowVisual(item).icon }}
                          </div>
                          <input
                            v-if="multiSelectMode"
                            type="checkbox"
                            class="mt-1 rounded border-gray-300 text-primary"
                            :checked="selectedPaths.has(item.path)"
                            @click.stop="toggleMultiSelect(item)"
                          >
                        </div>
                        <div class="mt-2 truncate text-xs font-bold text-gray-700 dark:text-gray-200" :title="resolveItemDisplayName(item)">
                          {{ resolveItemDisplayName(item) }}
                        </div>
                        <div class="mt-1 flex items-center justify-between gap-1 text-[9px] text-gray-400">
                          <span class="truncate">{{ item.is_dir ? '文件夹' : formatSize(item.size) }}</span>
                          <span v-if="isRecursiveListingActive" class="max-w-[55%] truncate" :title="item.path">{{ getItemLocationHint(item.path) }}</span>
                        </div>
                      </div>
                    </div>
                    <div v-if="hasMoreListItems" class="py-3 text-center">
                      <button type="button" class="text-[10px] font-bold text-primary hover:opacity-80" @click="loadMoreListItems">
                        加载更多 ({{ paginatedDisplayItems.length }}/{{ sortedDisplayItems.length }})
                      </button>
                    </div>
                  </div>

                  <div class="absolute bottom-3 right-3 z-20 flex flex-col items-end gap-2 pointer-events-none">
                    <!-- 多选操作条：「全选」常驻（没选任何项时也要能全选），删除/挂载按可用性出现 -->
                    <div
                      v-if="multiSelectMode"
                      class="pointer-events-auto flex items-center gap-1 rounded-full border border-gray-200 bg-white/95 px-1.5 py-1 shadow-lg ring-1 ring-black/5 dark:border-gray-700 dark:bg-gray-900/95"
                    >
                      <button
                        type="button"
                        class="rounded-full px-2.5 py-1.5 text-[11px] font-bold text-gray-600 transition-colors hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 disabled:opacity-40 dark:text-gray-300 dark:hover:bg-gray-800"
                        :disabled="batchSelectablePaths.length === 0"
                        :title="batchSelectAllState === 'all' ? '取消全选当前列表' : '全选当前列表'"
                        @click="toggleSelectAllItems"
                      >
                        {{ batchSelectAllState === 'all' ? '取消全选' : '全选' }}
                      </button>
                      <span class="h-4 w-px bg-gray-200 dark:bg-gray-700" aria-hidden="true" />
                      <button
                        v-if="!isTrashView"
                        type="button"
                        class="inline-flex items-center gap-1 rounded-full border border-red-200 bg-red-50 px-2.5 py-1.5 text-[11px] font-bold text-red-600 transition-colors hover:bg-red-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-400/40 disabled:cursor-not-allowed disabled:opacity-40 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-400 dark:hover:bg-red-500/20"
                        :disabled="batchDeletePlan.deletable.length === 0 || batchDeleteSubmitting"
                        :title="batchDeletePlan.deletable.length === 0 ? '请选择可删除的项目' : '批量删除所选项目'"
                        @click="requestBatchDelete"
                      >
                        <TrashIcon class="h-3.5 w-3.5" aria-hidden="true" />
                        批量删除 ({{ batchDeletePlan.deletable.length }})
                      </button>
                      <button
                        v-if="batchSelectedItems.length > 0"
                        type="button"
                        class="inline-flex items-center gap-1.5 rounded-full border border-primary/20 bg-primary px-3 py-1.5 text-[11px] font-bold text-white shadow-primary/20 transition-all hover:brightness-105 active:scale-[0.98] focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
                        @click="mountSelectedBatch"
                      >
                        批量挂载 ({{ batchSelectedItems.length }})
                      </button>
                    </div>
                    <button
                      type="button"
                      class="pointer-events-auto flex h-11 w-11 items-center justify-center rounded-full border shadow-lg transition-all active:scale-95"
                      :class="multiSelectMode
                        ? 'border-primary bg-primary text-white shadow-primary/25'
                        : 'border-gray-200 bg-white text-gray-600 hover:border-primary/30 hover:text-primary dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300 dark:shadow-black/30'"
                      :title="multiSelectMode ? '取消多选' : '多选'"
                      :aria-label="multiSelectMode ? '取消多选' : '多选'"
                      @click="toggleMultiSelectMode"
                    >
                      <XMarkIcon v-if="multiSelectMode" class="h-5 w-5" aria-hidden="true" />
                      <CheckCircleIcon v-else class="h-5 w-5" aria-hidden="true" />
                    </button>
                  </div>

                  <!-- 上传队列面板：传输中 / 服务端写入中 / 失败重试都在这里可见，
                       避免上传大文件时界面完全静默（用户无法区分「在传」和「卡死」） -->
                  <!-- 淡出交给 transition：自动清除时先播完 200ms 再移除 DOM，
                       否则面板「啪」地消失，最后一条状态来不及被看见 -->
                  <transition
                    enter-active-class="transition-opacity ease-out duration-150"
                    enter-from-class="opacity-0"
                    enter-to-class="opacity-100"
                    leave-active-class="transition-opacity ease-in duration-200"
                    leave-from-class="opacity-100"
                    leave-to-class="opacity-0"
                  >
                  <div
                    v-if="uploadSnapshot.tasks.length > 0"
                    class="absolute bottom-5 left-3 z-30 w-[min(22rem,calc(100%-1.5rem))] rounded-xl border border-gray-200 bg-white/95 shadow-lg ring-1 ring-black/5 backdrop-blur dark:border-gray-700 dark:bg-gray-900/95"
                    role="region"
                    aria-label="上传队列"
                  >
                    <div class="flex items-center justify-between gap-2 border-b border-gray-100 px-3 py-2 dark:border-gray-800">
                      <div class="flex min-w-0 items-center gap-2">
                        <span class="text-[11px] font-bold text-gray-700 dark:text-gray-200">
                          上传 {{ uploadSnapshot.aggregate.done }}/{{ uploadSnapshot.aggregate.total }}
                        </span>
                        <span class="truncate text-[10px] text-gray-400">
                          {{ formatBytes(uploadSnapshot.aggregate.loadedBytes) }} / {{ formatBytes(uploadSnapshot.aggregate.totalBytes) }}
                        </span>
                      </div>
                      <div class="flex items-center gap-1">
                        <button
                          type="button"
                          class="rounded px-1.5 py-0.5 text-[10px] text-gray-500 transition-colors hover:bg-gray-100 dark:hover:bg-gray-800"
                          @click="uploadPanelCollapsed = !uploadPanelCollapsed"
                        >{{ uploadPanelCollapsed ? '展开' : '收起' }}</button>
                        <button
                          v-if="!uploadSnapshot.aggregate.active"
                          type="button"
                          class="rounded px-1.5 py-0.5 text-[10px] text-gray-500 transition-colors hover:bg-gray-100 dark:hover:bg-gray-800"
                          @click="clearFinishedUploads()"
                        >清除已完成</button>
                      </div>
                    </div>

                    <div v-if="!uploadPanelCollapsed" class="max-h-56 space-y-2 overflow-y-auto px-3 py-2">
                      <div v-for="task in uploadSnapshot.tasks" :key="task.id" class="space-y-1">
                        <div class="flex items-center justify-between gap-2">
                          <span class="min-w-0 flex-1 truncate text-[11px] font-medium text-gray-700 dark:text-gray-200" :title="task.name">
                            {{ task.name }}
                          </span>
                          <button
                            v-if="uploadTaskActive(task.status)"
                            type="button"
                            class="shrink-0 rounded px-1.5 py-0.5 text-[10px] text-gray-400 transition-colors hover:text-red-500"
                            @click="cancelWorkspaceUpload(task.id)"
                          >取消</button>
                          <button
                            v-else-if="task.status === 'failed' || task.status === 'canceled'"
                            type="button"
                            class="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold text-primary hover:underline"
                            @click="retryWorkspaceUpload(task.id)"
                          >重试</button>
                        </div>

                        <div class="h-1.5 w-full overflow-hidden rounded-full bg-gray-100 dark:bg-gray-800">
                          <div
                            class="h-full rounded-full transition-all"
                            :class="task.status === 'failed'
                              ? 'bg-red-400'
                              : task.status === 'canceled'
                                ? 'bg-gray-300 dark:bg-gray-600'
                                : task.status === 'committing'
                                  ? 'animate-pulse bg-primary/60'
                                  : 'bg-primary'"
                            :style="{ width: `${task.percent}%` }"
                          />
                        </div>

                        <div class="flex items-center justify-between gap-2 text-[10px]">
                          <span :class="task.status === 'failed' ? 'text-red-500' : 'text-gray-500 dark:text-gray-400'">
                            {{ uploadTaskStatusText(task.status) }}
                          </span>
                          <span class="truncate text-gray-400">{{ uploadTaskHint(task) }}</span>
                        </div>

                        <!-- 默认保留原名，只有重名才会改名：变了就必须显式说明，否则用户会以为传错了文件 -->
                        <div
                          v-if="task.status === 'done' && task.storedName && task.storedName !== task.name"
                          class="truncate text-[10px] text-gray-400"
                          :title="task.storedName"
                        >
                          已存为 {{ task.storedName }}（同名已存在，自动加后缀）
                        </div>
                      </div>
                    </div>
                  </div>
                  </transition>
                </div>
              </div>
            </div>
          </div>
        </transition>
      </div>
    </div>
  </teleport>

  <input ref="uploadInputRef" type="file" multiple class="hidden" @change="handleUploadFiles">

  <Teleport to="body">
    <div
      v-if="quickNavOpen"
      ref="quickNavMenuRef"
      class="fixed z-[130] max-w-[calc(100vw-2rem)] rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-xl ring-1 ring-black/5 py-1 text-xs"
      :style="{ top: `${quickNavMenuPos.top}px`, left: `${quickNavMenuPos.left}px`, width: `${QUICK_NAV_MENU_WIDTH}px` }"
      role="menu"
      @click.stop
    >
      <div class="px-3 py-1.5 text-[9px] font-bold text-gray-400 uppercase tracking-wide border-b border-gray-100 dark:border-gray-800 mb-0.5">
        快捷入口
      </div>
      <button
        v-for="link in quickNavLinks"
        :key="link.key"
        type="button"
        role="menuitem"
        class="w-full px-3 py-2 text-left flex items-center gap-2 transition-colors focus:outline-none"
        :class="link.disabled
          ? 'opacity-40 cursor-not-allowed text-gray-400 dark:text-gray-500'
          : isQuickLinkActive(link.path)
            ? 'bg-primary/5 text-primary font-bold'
            : 'text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800'"
        :title="link.disabled ? link.disabledTitle : link.label"
        :aria-disabled="link.disabled ? 'true' : undefined"
        @click="handleQuickNavClick(link)"
      >
        <component :is="link.icon" class="h-5 w-5 shrink-0" aria-hidden="true" />
        <span class="truncate flex-1">{{ link.label }}</span>
        <span
          v-if="isQuickLinkActive(link.path) && !link.disabled"
          class="text-[9px] font-bold text-primary/70"
        >当前</span>
      </button>
      <div class="my-1 border-t border-gray-100 dark:border-gray-800" />
      <button
        type="button"
        role="menuitem"
        class="w-full px-3 py-2 text-left flex items-center gap-2 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors focus:outline-none"
        @click="openRecentFilesFromQuickNav"
      >
        <ClockIcon class="h-5 w-5 shrink-0" aria-hidden="true" />
        <span class="truncate flex-1">最近文件</span>
        <svg class="w-3 h-3 text-gray-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 5l7 7-7 7" />
        </svg>
      </button>
      <button
        v-if="trashNavLink"
        type="button"
        role="menuitem"
        class="w-full px-3 py-2 text-left flex items-center gap-2 transition-colors focus:outline-none"
        :class="isTrashView
          ? 'bg-primary/5 text-primary font-bold'
          : 'text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800'"
        @click="handleQuickNavClick(trashNavLink)"
      >
        <component :is="trashNavLink.icon" class="h-5 w-5 shrink-0" aria-hidden="true" />
        <span class="truncate flex-1">{{ trashNavLink.label }}</span>
        <span v-if="isTrashView" class="text-[9px] font-bold text-primary/70">当前</span>
      </button>
    </div>
  </Teleport>

  <Teleport to="body">
    <div
      v-if="recentFilesOpen"
      ref="recentFilesMenuRef"
      class="fixed z-[130] w-72 max-w-[calc(100vw-2rem)] max-h-60 overflow-y-auto rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-xl ring-1 ring-black/5 py-1 text-xs"
      :style="{ top: `${recentFilesMenuPos.top}px`, left: `${recentFilesMenuPos.left}px` }"
      @click.stop
    >
      <div class="px-3 py-1.5 text-[9px] font-bold text-gray-400 uppercase tracking-wide border-b border-gray-100 dark:border-gray-800 mb-0.5 flex items-center justify-between gap-2">
        <span>最近打开 / 添加的文件</span>
        <span v-if="recentFiles.length" class="normal-case font-medium text-gray-400/80">{{ recentFiles.length }}/{{ MAX_RECENT }}</span>
      </div>
      <template v-if="recentFiles.length">
        <div
          v-for="recent in recentFiles"
          :key="recent.path"
          class="flex items-start gap-0.5 px-1.5 py-1 border-b border-gray-50 dark:border-gray-800/60 last:border-b-0 group hover:bg-gray-50 dark:hover:bg-gray-800/80 transition-colors"
        >
          <button
            type="button"
            class="flex-1 min-w-0 px-1.5 py-1 text-left rounded-md focus:outline-none"
            @click="openRecentFile(recent)"
          >
            <div class="flex items-start gap-2 min-w-0">
              <span class="text-sm shrink-0 mt-0.5">{{ getItemVisual({ name: recent.name, is_dir: false }).icon }}</span>
              <div class="min-w-0 flex-1">
                <div class="text-[11px] font-bold text-gray-700 dark:text-gray-200 truncate" :title="recent.name">
                  {{ recent.name }}
                </div>
                <div class="text-[9px] text-gray-400 dark:text-gray-500 truncate mt-0.5" :title="recent.path">
                  {{ formatRecentFileLocation(recent.path) }}
                </div>
                <div class="text-[9px] text-gray-400/80 dark:text-gray-500/80 mt-0.5">
                  {{ formatTime(recent.mtime) }}
                </div>
              </div>
            </div>
          </button>
          <button
            type="button"
            class="shrink-0 p-1.5 mt-0.5 rounded-md text-gray-300 dark:text-gray-600 hover:text-red-500 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 opacity-60 group-hover:opacity-100 transition-all focus:outline-none"
            title="从最近列表移除"
            aria-label="从最近列表移除"
            @click.stop="removeRecentFile(recent.path)"
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div class="px-3 py-2 border-t border-gray-100 dark:border-gray-800">
          <button
            type="button"
            class="w-full text-[10px] font-bold text-red-500 dark:text-red-400 hover:text-red-600 dark:hover:text-red-300 transition-colors focus:outline-none"
            @click="clearRecentFiles"
          >
            清空记录
          </button>
        </div>
      </template>
      <div v-else class="px-3 py-5 text-center text-[10px] text-gray-400 leading-relaxed">
        暂无最近文件<br>
        <span class="text-[9px] text-gray-400/70">预览或挂载文件后会出现在这里</span>
      </div>
    </div>
  </Teleport>

  <WorkspaceActionMenu
    :open="!!actionMenu"
    :entries="actionMenuEntries"
    :anchor="actionMenu?.anchor ?? null"
    @select="runWorkspaceAction"
    @close="closeActionMenu"
  />

  <Teleport to="body">
    <div
      v-if="createDialog"
      class="fixed inset-0 z-[131] flex items-center justify-center p-4 bg-black/30 backdrop-blur-[1px]"
      @click.self="cancelCreateEntry"
    >
      <div
        class="w-full max-w-sm rounded-2xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-2xl p-4"
        @click.stop
      >
        <h3 class="text-sm font-bold text-gray-800 dark:text-gray-100 mb-3">
          {{ createDialog.kind === 'file' ? '新建文件' : '新建文件夹' }}
        </h3>
        <input
          v-model="createDialog.name"
          type="text"
          class="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-950 focus:ring-2 focus:ring-primary/30 focus:border-primary focus:outline-none"
          :placeholder="createDialog.kind === 'file' ? '例如 notes.md' : '文件夹名称'"
          autofocus
        >
        <p class="mt-2 text-[10px] text-gray-400 leading-relaxed">
          仅可在本人工作目录内创建；文件支持常见文本类型（如 .md、.txt、.json）。
        </p>
        <div class="mt-4 flex justify-end gap-2">
          <button
            type="button"
            class="px-3 py-1.5 text-xs font-bold text-gray-500 hover:text-gray-700 dark:hover:text-gray-200 rounded-lg focus:outline-none"
            :disabled="createSubmitting"
            @click="cancelCreateEntry"
          >
            取消
          </button>
          <button
            type="button"
            class="px-3 py-1.5 text-xs font-bold text-white bg-primary hover:bg-primary/90 rounded-lg focus:outline-none disabled:opacity-60"
            :disabled="createSubmitting"
            @click="submitCreateEntry"
          >
            {{ createSubmitting ? '创建中…' : '创建' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>

  <Teleport to="body">
    <div v-if="renameDialog" class="fixed inset-0 z-[131] flex items-center justify-center p-4 bg-black/30" @click.self="renameDialog = null">
      <div class="w-full max-w-sm rounded-2xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-2xl p-4" @click.stop>
        <h3 class="text-sm font-bold mb-3">重命名</h3>
        <input v-model="renameDialog.name" type="text" class="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-950 focus:outline-none focus:ring-2 focus:ring-primary/30">
        <div class="mt-4 flex justify-end gap-2">
          <button type="button" class="px-3 py-1.5 text-xs font-bold text-gray-500" @click="renameDialog = null">取消</button>
          <button type="button" class="px-3 py-1.5 text-xs font-bold text-white bg-primary rounded-lg" @click="submitRename">确定</button>
        </div>
      </div>
    </div>
  </Teleport>

  <Teleport to="body">
    <div v-if="deleteTarget" class="fixed inset-0 z-[131] flex items-center justify-center p-4 bg-black/30" @click.self="deleteTarget = null">
      <div class="w-full max-w-sm rounded-2xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-2xl p-4" @click.stop>
        <h3 class="text-sm font-bold mb-2" :class="deleteTarget.isPublic ? 'text-red-600' : ''">{{ deleteTarget.isPublic ? '永久删除？' : '移入回收站？' }}</h3>
        <p class="text-xs text-gray-500 mb-1">{{ deleteTarget.name }}</p>
        <p v-if="deleteTarget.isPublic" class="text-xs text-red-500 mb-4">⚠️ 该文件属于公共目录，将被<strong>永久删除</strong>，不可恢复。</p>
        <p v-else class="text-xs text-gray-400 mb-4">将被移入回收站，可在回收站中恢复。</p>
        <div class="flex justify-end gap-2">
          <button type="button" class="px-3 py-1.5 text-xs font-bold text-gray-500" @click="deleteTarget = null">取消</button>
          <button type="button" class="px-3 py-1.5 text-xs font-bold text-white bg-red-500 rounded-lg" @click="submitDelete">{{ deleteTarget.isPublic ? '永久删除' : '删除' }}</button>
        </div>
      </div>
    </div>
  </Teleport>

  <Teleport to="body">
    <div v-if="purgeTarget" class="fixed inset-0 z-[131] flex items-center justify-center p-4 bg-black/30" @click.self="purgeTarget = null">
      <div class="w-full max-w-sm rounded-2xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-2xl p-4" @click.stop>
        <h3 class="text-sm font-bold mb-2 text-red-600">永久删除？</h3>
        <p class="text-xs text-gray-500 mb-4">{{ formatTrashItemName(purgeTarget.name) }} — 删除后无法恢复。</p>
        <div class="flex justify-end gap-2">
          <button type="button" class="px-3 py-1.5 text-xs font-bold text-gray-500" @click="purgeTarget = null">取消</button>
          <button type="button" class="px-3 py-1.5 text-xs font-bold text-white bg-red-500 rounded-lg" @click="submitPurge">永久删除</button>
        </div>
      </div>
    </div>
  </Teleport>

  <Teleport to="body">
    <div v-if="emptyTrashConfirm" class="fixed inset-0 z-[131] flex items-center justify-center p-4 bg-black/30" @click.self="emptyTrashConfirm = false">
      <div class="w-full max-w-sm rounded-2xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-2xl p-4" @click.stop>
        <h3 class="text-sm font-bold mb-2 text-red-600">清空回收站？</h3>
        <p class="text-xs text-gray-500 mb-4">将永久删除回收站内的全部文件和文件夹，此操作无法撤销。</p>
        <div class="flex justify-end gap-2">
          <button type="button" class="px-3 py-1.5 text-xs font-bold text-gray-500" :disabled="emptyTrashSubmitting" @click="emptyTrashConfirm = false">取消</button>
          <button type="button" class="px-3 py-1.5 text-xs font-bold text-white bg-red-500 rounded-lg disabled:opacity-60" :disabled="emptyTrashSubmitting" @click="submitEmptyTrash">
            {{ emptyTrashSubmitting ? '清空中…' : '确认清空' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- 解压目标选择：与「保存到目录」共用同一个目录树，语义靠这几项文案区分 -->
  <WorkspaceDirectorySaveDialog
    :visible="!!extractDialog"
    :conversation-id="conversationId"
    :default-filename="extractDialog?.defaultName || ''"
    :default-path="extractDialog?.parentPath || null"
    title="解压到目录"
    subtitle="压缩包只读；解压目标必须是你可以写入的目录"
    name-label="子文件夹名"
    submit-label="开始解压"
    :submitting="extractSubmitting"
    allow-empty-name
    @close="closeExtractDialog"
    @save="submitExtract"
  />

  <!-- 批量删除二次确认：复用仓库通用 ConfirmModal，列出待删项与跳过项，
       避免「一次点击删掉一批」且用户记不清自己勾了什么 -->
  <ConfirmModal
    v-if="batchDeleteConfirm"
    :title="batchDeletePermanentCount > 0 ? '确认删除选中项？' : '移入回收站？'"
    :message="batchDeleteMessage"
    :details="batchDeleteDetails"
    details-label="将删除以下项目"
    confirm-text="确认删除"
    :loading="batchDeleteSubmitting"
    @confirm="submitBatchDelete"
    @cancel="batchDeleteConfirm = false"
  />
</template>

<style scoped>
.workspace-drawer-scroll {
  -webkit-overflow-scrolling: touch;
  overscroll-behavior: contain;
}
.custom-scrollbar::-webkit-scrollbar {
  width: 4px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background: rgba(156, 163, 175, 0.25);
  border-radius: 4px;
}
.no-scrollbar::-webkit-scrollbar {
  display: none;
}
.no-scrollbar {
  -ms-overflow-style: none;
  scrollbar-width: none;
}
</style>
