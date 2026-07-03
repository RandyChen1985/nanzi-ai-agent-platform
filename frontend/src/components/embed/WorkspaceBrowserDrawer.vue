<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import axios from '@/utils/axios'
import { useToast } from '@/composables/useToast'
import {
  resolveFileTypeVisual,
} from '@/utils/fileTypeVisual'
import { canPreviewWorkspaceFile } from '@/utils/workspaceFilePreview'

const modelValue = defineModel<boolean>({ default: false })
const keepOpenOnSelect = defineModel<boolean>('keepOpenOnSelect', { default: false })
const pinned = defineModel<boolean>('pinned', { default: false })

const props = withDefaults(
  defineProps<{
    /** 与数据门户等同侧钉住时的水平偏移（如 right-[28rem]） */
    pinnedDockClass?: string
  }>(),
  { pinnedDockClass: 'right-0' },
)

const emit = defineEmits<{
  (e: 'select', payload: { type: 'local_file' | 'local_dir'; path: string; name: string; size: number; ext: string }): void
  (e: 'preview', payload: { path: string; name: string }): void
}>()

const activeTab = ref<'file' | 'directory'>('file')
const currentPath = ref<string>('')
const parentPath = ref<string | null>(null)
const isRoot = ref<boolean>(true)
const scope = ref<'admin_all' | 'user_scoped'>('user_scoped')
const items = ref<any[]>([])
const loading = ref<boolean>(false)
const baseDir = ref<string>('')

const { showToast } = useToast()
const selectedItem = ref<any | null>(null)
const searchQuery = ref('')
const includeSubdirs = ref(true)
const searchLoading = ref(false)
const recursiveSearchResults = ref<any[]>([])
const searchTruncated = ref(false)
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
  if (searchDebounceTimer) {
    clearTimeout(searchDebounceTimer)
    searchDebounceTimer = null
  }
}

const fetchRecursiveSearch = async () => {
  const q = searchQuery.value.trim()
  if (!q || !includeSubdirs.value) {
    recursiveSearchResults.value = []
    searchTruncated.value = false
    return
  }

  searchLoading.value = true
  try {
    const res = await axios.get('/api/v1/chat/fs/search', {
      params: {
        q,
        path: currentPath.value || undefined,
        max_results: 80,
      },
    })
    if (res.data?.data) {
      recursiveSearchResults.value = res.data.data.items || []
      searchTruncated.value = !!res.data.data.truncated
    }
  } catch (error) {
    console.error('Failed to search files:', error)
    recursiveSearchResults.value = []
    showToast('搜索失败，请稍后重试', 'error')
  } finally {
    searchLoading.value = false
  }
}

watch(searchQuery, () => {
  selectedItem.value = null
  if (searchDebounceTimer) clearTimeout(searchDebounceTimer)

  const q = searchQuery.value.trim()
  if (!q) {
    recursiveSearchResults.value = []
    searchTruncated.value = false
    return
  }

  if (includeSubdirs.value) {
    searchDebounceTimer = setTimeout(fetchRecursiveSearch, 300)
  } else {
    recursiveSearchResults.value = []
    searchTruncated.value = false
  }
})

watch(includeSubdirs, () => {
  selectedItem.value = null
  if (searchQuery.value.trim() && includeSubdirs.value) {
    if (searchDebounceTimer) clearTimeout(searchDebounceTimer)
    searchDebounceTimer = setTimeout(fetchRecursiveSearch, 200)
  } else {
    recursiveSearchResults.value = []
    searchTruncated.value = false
  }
})

const filteredCurrentItems = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return items.value
  return items.value.filter((item) => item.name.toLowerCase().includes(q))
})

const isSearchActive = computed(() => searchQuery.value.trim().length > 0)

const displayItems = computed(() => {
  if (!isSearchActive.value) return items.value
  if (includeSubdirs.value) return recursiveSearchResults.value
  return filteredCurrentItems.value
})

const displayEmptyHint = computed(() => {
  if (!isSearchActive.value) return '暂无子文件或子目录'
  if (includeSubdirs.value && searchLoading.value) return ''
  return '未找到匹配的文件或目录'
})

const getItemLocationHint = (itemPath: string) => {
  const parent = itemPath.split('/').slice(0, -1).join('/')
  if (!parent || parent === currentPath.value) return ''
  const base = baseDir.value || currentPath.value
  const rel = parent.startsWith(base) ? parent.slice(base.length).replace(/^\//, '') : parent
  return rel || parent.split('/').pop() || ''
}

const fetchDirectory = async (pathUrl: string = '') => {
  loading.value = true
  selectedItem.value = null
  resetSearchState()
  try {
    const res = await axios.get('/api/v1/chat/fs/list', {
      params: { path: pathUrl },
    })
    if (res.data?.data) {
      const data = res.data.data
      currentPath.value = data.current_path
      parentPath.value = data.parent_path
      isRoot.value = data.is_root
      scope.value = data.scope === 'admin_all' ? 'admin_all' : 'user_scoped'
      items.value = data.items
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
      fetchDirectory()
    }
  },
  { immediate: true },
)

watch(activeTab, () => {
  selectedItem.value = null
})

onMounted(() => {
  mobileMq = window.matchMedia('(max-width: 639px)')
  syncMobile()
  if (isMobile.value && pinned.value) {
    pinned.value = false
  }
  mobileMq.addEventListener('change', syncMobile)
})

onUnmounted(() => {
  mobileMq?.removeEventListener('change', syncMobile)
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

const scopedHomeCrumbName = () => '🏠 工作空间'
const adminHomeCrumbName = () => '📁 root'
const formatBaseCrumb = (currentScope: 'admin_all' | 'user_scoped') =>
  currentScope === 'user_scoped' ? scopedHomeCrumbName() : adminHomeCrumbName()

const breadcrumbs = computed(() => {
  if (isRoot.value && scope.value === 'user_scoped') {
    return [{ name: scopedHomeCrumbName(), path: '', isBase: true }]
  }

  const base = baseDir.value
  const current = currentPath.value
  if (!current) return []

  const parts = current.split('/').filter(Boolean)
  const crumbs: Array<{ name: string; path: string; isBase: boolean }> = []
  let runningPath = ''

  for (const part of parts) {
    runningPath += `/${part}`
    if (runningPath.startsWith(base)) {
      const isBase = runningPath === base
      crumbs.push({
        name: isBase ? formatBaseCrumb(scope.value) : part,
        path: isBase && scope.value === 'user_scoped' ? '' : runningPath,
        isBase,
      })
    }
  }

  if (crumbs.length === 0) {
    crumbs.push({
      name: formatBaseCrumb(scope.value),
      path: scope.value === 'user_scoped' ? '' : (base || current),
      isBase: true,
    })
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
  if (activeTab.value === 'file' && canPreviewWorkspaceFile(item.name)) {
    previewItem(item)
  }
}

const previewItem = (item: { path: string; name: string }) => {
  if (!canPreviewWorkspaceFile(item.name)) {
    showToast('不支持预览该类型的文件', 'error')
    return
  }
  emit('preview', { path: item.path, name: item.name })
}

const canPreviewSelected = computed(() => {
  if (!selectedItem.value || selectedItem.value.is_dir) return false
  return canPreviewWorkspaceFile(selectedItem.value.name)
})

const handlePreviewSelected = () => {
  if (!selectedItem.value) return
  previewItem(selectedItem.value)
}

const handleSelectRow = (item: any) => {
  if (activeTab.value === 'file') {
    selectedItem.value = item.is_dir ? null : item
  } else {
    selectedItem.value = item.is_dir ? item : null
  }
}

const handleRowClick = (item: any) => {
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

const handleConfirm = () => {
  if (activeTab.value === 'directory' && !selectedItem.value) {
    const dirName = currentPath.value.split('/').filter(Boolean).pop() || 'data'
    finishSelect({
      type: 'local_dir',
      path: currentPath.value,
      name: dirName,
      size: 0,
      ext: '',
    })
    return
  }

  if (!selectedItem.value) return

  const item = selectedItem.value
  if (activeTab.value === 'file') {
    finishSelect({
      type: 'local_file',
      path: item.path,
      name: item.name,
      size: item.size,
      ext: getFileExt(item.name),
    })
  } else {
    finishSelect({
      type: 'local_dir',
      path: item.path,
      name: item.name,
      size: 0,
      ext: '',
    })
  }
}

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
    : `fixed inset-y-0 ${props.pinnedDockClass} max-w-full flex pointer-events-none`
})

const closeDrawer = () => {
  modelValue.value = false
}
</script>

<template>
  <teleport to="body">
    <div
      v-show="modelValue"
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
              isMobile
                ? 'w-full max-w-none rounded-t-2xl border-t border-gray-200 dark:border-gray-800 h-full max-h-full'
                : 'w-screen max-w-[min(100vw,28rem)] h-full border-l border-gray-200 dark:border-gray-800',
            ]"
          >
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
                <span class="text-base flex-shrink-0" aria-hidden="true">💻</span>
                <span class="truncate">浏览工作空间</span>
                <span
                  v-if="pinned"
                  class="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wide text-blue-600 bg-blue-50 border border-blue-100 dark:text-blue-300 dark:bg-blue-500/10 dark:border-blue-500/20"
                >
                  已钉住
                </span>
              </span>
              <div class="flex items-center gap-2 flex-shrink-0">
                <label
                  class="hidden sm:flex items-center gap-1.5 text-[10px] text-gray-500 dark:text-gray-400 cursor-pointer select-none whitespace-nowrap"
                  title="开启后确认挂载不会关闭侧栏，可连续选择文件"
                >
                  <input
                    v-model="keepOpenOnSelect"
                    type="checkbox"
                    class="rounded border-gray-300 text-primary focus:ring-primary/30"
                  />
                  挂载后保持
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
                <div class="flex border-b border-gray-100 dark:border-gray-800 mb-3 bg-gray-50/50 dark:bg-gray-900/30 p-1 rounded-xl shrink-0">
                  <button
                    class="flex-1 py-2 text-xs font-black rounded-lg transition-all duration-200 flex items-center justify-center space-x-1.5 focus:outline-none"
                    :class="activeTab === 'file' ? 'bg-white dark:bg-gray-800 text-primary shadow-sm ring-1 ring-black/5' : 'text-gray-500 hover:text-gray-900 dark:hover:text-gray-100'"
                    @click="activeTab = 'file'"
                  >
                    <span>📄</span>
                    <span>挂载系统文件</span>
                  </button>
                  <button
                    class="flex-1 py-2 text-xs font-black rounded-lg transition-all duration-200 flex items-center justify-center space-x-1.5 focus:outline-none"
                    :class="activeTab === 'directory' ? 'bg-white dark:bg-gray-800 text-primary shadow-sm ring-1 ring-black/5' : 'text-gray-500 hover:text-gray-900 dark:hover:text-gray-100'"
                    @click="activeTab = 'directory'"
                  >
                    <span>📁</span>
                    <span>挂载系统目录</span>
                  </button>
                </div>

                <div class="flex items-center space-x-2 px-2 py-1.5 bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-gray-100 dark:border-gray-800 mb-2 overflow-x-auto no-scrollbar shrink-0">
                  <button
                    class="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-500 disabled:opacity-30 disabled:hover:bg-transparent transition-colors"
                    :disabled="isRoot || loading"
                    title="返回上一级"
                    @click="goParent"
                  >
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15 19l-7-7 7-7" />
                    </svg>
                  </button>
                  <span class="text-gray-300 dark:text-gray-700">|</span>
                  <div class="flex items-center space-x-1 whitespace-nowrap text-xs">
                    <template v-for="(crumb, idx) in breadcrumbs" :key="idx">
                      <span v-if="idx > 0" class="text-gray-400 dark:text-gray-600">/</span>
                      <button
                        class="font-medium hover:text-primary transition-colors focus:outline-none"
                        :class="idx === breadcrumbs.length - 1 ? 'text-gray-800 dark:text-gray-200 font-bold' : 'text-gray-400 dark:text-gray-500'"
                        :disabled="loading"
                        @click="clickBreadcrumb(crumb.path)"
                      >
                        {{ crumb.name }}
                      </button>
                    </template>
                  </div>
                </div>

                <div class="flex items-center gap-2 mb-2 shrink-0">
                  <div class="relative flex-1">
                    <span class="absolute inset-y-0 left-0 pl-2.5 flex items-center pointer-events-none text-gray-400">
                      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                      </svg>
                    </span>
                    <input
                      v-model="searchQuery"
                      type="text"
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
                    class="flex-shrink-0 px-2.5 py-2 rounded-lg text-[10px] font-bold border transition-all focus:outline-none"
                    :class="includeSubdirs ? 'bg-primary/10 border-primary/30 text-primary' : 'bg-gray-50 dark:bg-gray-800 border-gray-200 dark:border-gray-700 text-gray-500'"
                    @click="includeSubdirs = !includeSubdirs"
                  >
                    含子目录
                  </button>
                </div>

                <div v-if="isSearchActive" class="flex items-center justify-between px-1 mb-2 text-[10px] text-gray-400 shrink-0">
                  <span>
                    <template v-if="includeSubdirs && searchLoading">正在搜索...</template>
                    <template v-else>找到 {{ displayItems.length }} 项</template>
                    <span v-if="searchTruncated" class="text-amber-600 dark:text-amber-400 ml-1">（结果已截断）</span>
                  </span>
                  <span v-if="includeSubdirs" class="font-mono truncate max-w-[50%]">{{ currentPath }}</span>
                  <span v-else>仅当前目录</span>
                </div>

                <div class="flex-1 min-h-[240px] border border-gray-100 dark:border-gray-800 rounded-xl overflow-hidden flex flex-col relative bg-gray-50/10">
                  <div class="grid grid-cols-12 gap-2 px-4 py-2 border-b border-gray-100 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-900/20 text-[10px] font-black text-gray-400 tracking-wider shrink-0">
                    <div class="col-span-7 sm:col-span-6">名称</div>
                    <div class="col-span-2 hidden sm:block">类型</div>
                    <div class="col-span-5 sm:col-span-4 text-right leading-tight">
                      <span>大小</span>
                      <span class="block text-[9px] font-normal text-gray-300 dark:text-gray-600">修改时间</span>
                    </div>
                  </div>

                  <div
                    v-if="loading || (isSearchActive && includeSubdirs && searchLoading)"
                    class="absolute inset-0 bg-white/70 dark:bg-gray-900/70 z-10 flex items-center justify-center"
                  >
                    <div class="flex flex-col items-center space-y-2">
                      <div class="w-8 h-8 border-4 border-primary border-t-transparent rounded-full animate-spin" />
                      <span class="text-xs text-gray-400 font-medium">正在读取文件系统...</span>
                    </div>
                  </div>

                  <div class="flex-1 overflow-y-auto custom-scrollbar p-1 min-h-0">
                    <div v-if="displayItems.length === 0 && !searchLoading" class="h-full flex flex-col items-center justify-center text-gray-400 py-12">
                      <span class="text-4xl mb-2">{{ isSearchActive ? '🔍' : '📂' }}</span>
                      <span class="text-xs font-bold">{{ displayEmptyHint }}</span>
                    </div>

                    <div v-else class="space-y-0.5">
                      <div
                        v-for="item in displayItems"
                        :key="item.path"
                        class="grid grid-cols-12 gap-2 px-3 py-2 rounded-lg cursor-pointer transition-all duration-150 select-none group items-center"
                        :class="[
                          selectedItem?.path === item.path
                            ? 'bg-primary/10 dark:bg-primary/20 ring-1 ring-primary/20'
                            : 'hover:bg-gray-100/50 dark:hover:bg-gray-800/40',
                          (activeTab === 'file' && item.is_dir) || (activeTab === 'directory' && !item.is_dir)
                            ? 'opacity-70 group-hover:opacity-100'
                            : '',
                        ]"
                        @click="handleRowClick(item)"
                        @dblclick="handleDoubleClick(item)"
                      >
                        <template v-for="visual in [getItemVisual(item)]" :key="item.path + '-visual'">
                          <div class="col-span-7 sm:col-span-6 flex items-start gap-2.5 min-w-0">
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
                                :title="item.name"
                              >
                                {{ item.name }}
                              </div>
                              <span
                                v-if="item.is_user_workspace"
                                class="inline-flex mt-1 px-1.5 py-0.5 rounded-md text-[9px] font-bold bg-primary/10 text-primary ring-1 ring-primary/20"
                                title="当前登录用户的 AI 会话工作目录"
                              >
                                用户工作目录
                              </span>
                              <div
                                v-if="isSearchActive && includeSubdirs && getItemLocationHint(item.path)"
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
                        <div class="col-span-5 sm:col-span-4 text-right text-[10px] font-mono text-gray-400 dark:text-gray-500 leading-tight">
                          <div>{{ item.is_dir ? '—' : formatSize(item.size) }}</div>
                          <div class="text-[9px] text-gray-400/80 dark:text-gray-500/80">{{ formatTime(item.mtime) }}</div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div class="shrink-0 px-4 py-3 border-t border-gray-100 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-800/20">
              <div class="text-[10px] font-black text-gray-400 uppercase tracking-widest mb-1">当前已选择</div>
              <div class="text-xs truncate font-bold text-gray-700 dark:text-gray-200 mb-3 min-h-[1.25rem]">
                <template v-if="selectedItem">
                  <span class="text-gray-500 dark:text-gray-400 font-mono font-normal">{{ selectedItem.path }}</span>
                </template>
                <template v-else-if="activeTab === 'directory'">
                  <span class="text-primary">📁</span> {{ currentPath }}
                </template>
                <template v-else>
                  <span class="text-gray-400 font-normal">
                    {{ isMobile ? '点按文件夹进入，点按文件选中后可预览或挂载' : '单击选中，双击文件夹下钻、双击文件可预览...' }}
                  </span>
                </template>
              </div>
              <div class="flex items-center justify-end gap-2">
                <button
                  type="button"
                  class="px-4 py-2 text-xs font-bold text-gray-500 hover:text-gray-700 bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700 rounded-xl transition-all"
                  @click="closeDrawer"
                >
                  取消
                </button>
                <button
                  v-if="activeTab === 'file'"
                  type="button"
                  class="px-4 py-2 text-xs font-bold text-primary bg-primary/10 hover:bg-primary/15 disabled:opacity-40 rounded-xl transition-all focus:outline-none"
                  :disabled="!canPreviewSelected"
                  title="在左侧画布中预览选中文件"
                  @click="handlePreviewSelected"
                >
                  预览
                </button>
                <button
                  type="button"
                  class="px-4 py-2 text-xs font-bold text-white bg-primary hover:bg-primary-hover disabled:opacity-40 disabled:hover:bg-primary rounded-xl shadow-lg shadow-primary/20 transition-all focus:outline-none"
                  :disabled="!selectedItem && activeTab === 'file'"
                  :style="{ backgroundColor: 'var(--primary-color, #1677ff)' }"
                  @click="handleConfirm"
                >
                  确认挂载
                </button>
              </div>
            </div>
          </div>
        </transition>
      </div>
    </div>
  </teleport>
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
