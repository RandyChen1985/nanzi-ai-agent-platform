<script setup lang="ts">
import { ref, computed, onMounted, watch, onUnmounted } from 'vue'
import Modal from '@/components/Modal.vue'
import axios from '@/utils/axios'
import { useToast } from '@/composables/useToast'
import {
  resolveFileTypeVisual,
  FILE_TYPE_LEGEND,
  getCategoryStyle,
} from '@/utils/fileTypeVisual'

const props = defineProps<{
  show: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'select', payload: { type: 'local_file' | 'local_dir'; path: string; name: string; size: number; ext: string }): void
}>()

const activeTab = ref<'file' | 'directory'>('file')
const currentPath = ref<string>('')
const parentPath = ref<string | null>(null)
const isRoot = ref<boolean>(true)
const items = ref<any[]>([])
const loading = ref<boolean>(false)
const baseDir = ref<string>('')

const { showToast } = useToast()

// 选中的项 (针对文件 Tab 选择文件，针对目录 Tab 选择子目录)
const selectedItem = ref<any | null>(null)
const searchQuery = ref('')
const includeSubdirs = ref(true)
const searchLoading = ref(false)
const recursiveSearchResults = ref<any[]>([])
const searchTruncated = ref(false)
let searchDebounceTimer: ReturnType<typeof setTimeout> | null = null

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

onUnmounted(resetSearchState)

// 载入目录列表
const fetchDirectory = async (pathUrl: string = '') => {
  loading.value = true
  selectedItem.value = null
  resetSearchState()
  try {
    const res = await axios.get('/api/v1/chat/fs/list', {
      params: { path: pathUrl }
    })
    if (res.data && res.data.data) {
      const data = res.data.data
      currentPath.value = data.current_path
      parentPath.value = data.parent_path
      isRoot.value = data.is_root
      items.value = data.items
      
      // 记录根目录的绝对路径，用于面包屑做截断
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

// 首次挂载时加载默认根目录
onMounted(() => {
  fetchDirectory()
})

// 当 Tab 切换时清空已选项，且如果选目录 Tab，默认选中当前目录
watch(activeTab, () => {
  selectedItem.value = null
})

// 格式化文件大小
const formatSize = (bytes: number) => {
  if (bytes === 0) return '-'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i]
}

// 格式化时间戳
const formatTime = (timestamp: number) => {
  const date = new Date(timestamp * 1000)
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

// 获取文件后缀
const getFileExt = (filename: string) => {
  const parts = filename.split('.')
  return parts.length > 1 ? '.' + parts.pop()?.toLowerCase() : ''
}

const getItemVisual = (item: { name: string; is_dir: boolean }) =>
  resolveFileTypeVisual(item.name, item.is_dir)

const legendItems = FILE_TYPE_LEGEND.map((category) => ({
  category,
  ...getCategoryStyle(category),
}))

// 极客感面包屑计算属性
const breadcrumbs = computed(() => {
  const base = baseDir.value
  const current = currentPath.value
  if (!current) return []
  
  const parts = current.split('/').filter(Boolean)
  const crumbs: Array<{ name: string; path: string; isBase: boolean }> = []
  let runningPath = ''
  
  for (const part of parts) {
    runningPath += '/' + part
    // 只有在 baseDir 自身或其子目录之下才在面板里友好展示，绝不展示父级目录
    if (runningPath.startsWith(base)) {
      const isBase = runningPath === base
      crumbs.push({
        name: isBase ? '📁 root' : part,
        path: runningPath,
        isBase
      })
    }
  }
  
  // 确保如果 baseDir 为空或没有匹配上，至少有个当前目录
  if (crumbs.length === 0) {
    crumbs.push({ name: '📁 root', path: base || current, isBase: true })
  }
  
  return crumbs
})

// 点击面包屑
const clickBreadcrumb = (path: string) => {
  fetchDirectory(path)
}

// 双击行：若是文件夹，下钻进入
const handleDoubleClick = (item: any) => {
  if (item.is_dir) {
    fetchDirectory(item.path)
  }
}

const navigateToItemDirectory = (item: any) => {
  if (item.is_dir) {
    fetchDirectory(item.path)
    return
  }
  const parent = item.path.split('/').slice(0, -1).join('/')
  if (parent) fetchDirectory(parent)
}

// 单击行：选中该项
const handleSelectRow = (item: any) => {
  if (activeTab.value === 'file') {
    if (!item.is_dir) {
      selectedItem.value = item
    } else {
      // 在文件 Tab 点击文件夹不下钻，只双击下钻
      selectedItem.value = null
    }
  } else {
    // 目录 Tab
    if (item.is_dir) {
      selectedItem.value = item
    } else {
      selectedItem.value = null
    }
  }
}

// 确认选择并挂载
const handleConfirm = () => {
  // 1. 如果在选择目录 Tab 且用户没有高亮点击某子目录，但点击了确认，则默认直接挂载“当前浏览的目录”
  if (activeTab.value === 'directory' && !selectedItem.value) {
    const dirName = currentPath.value.split('/').filter(Boolean).pop() || 'data'
    emit('select', {
      type: 'local_dir',
      path: currentPath.value,
      name: dirName,
      size: 0,
      ext: ''
    })
    emit('close')
    return
  }

  // 2. 正常高亮选中确认
  if (!selectedItem.value) return

  const item = selectedItem.value
  if (activeTab.value === 'file') {
    emit('select', {
      type: 'local_file',
      path: item.path,
      name: item.name,
      size: item.size,
      ext: getFileExt(item.name)
    })
  } else {
    emit('select', {
      type: 'local_dir',
      path: item.path,
      name: item.name,
      size: 0,
      ext: ''
    })
  }
  emit('close')
}
</script>

<template>
  <Modal :show="show" title="服务器文件浏览器" size="max-w-2xl" @close="emit('close')">
    <div class="flex flex-col h-[500px]">
      <!-- 1. 双 Tab 页切换 -->
      <div class="flex border-b border-gray-100 dark:border-gray-800 mb-3 bg-gray-50/50 dark:bg-gray-900/30 p-1 rounded-xl">
        <button 
          @click="activeTab = 'file'" 
          class="flex-1 py-2 text-xs font-black rounded-lg transition-all duration-200 flex items-center justify-center space-x-1.5 focus:outline-none"
          :class="activeTab === 'file' ? 'bg-white dark:bg-gray-800 text-primary shadow-sm ring-1 ring-black/5' : 'text-gray-500 hover:text-gray-900 dark:hover:text-gray-100'"
        >
          <span>📄</span>
          <span>挂载系统文件</span>
        </button>
        <button 
          @click="activeTab = 'directory'" 
          class="flex-1 py-2 text-xs font-black rounded-lg transition-all duration-200 flex items-center justify-center space-x-1.5 focus:outline-none"
          :class="activeTab === 'directory' ? 'bg-white dark:bg-gray-800 text-primary shadow-sm ring-1 ring-black/5' : 'text-gray-500 hover:text-gray-900 dark:hover:text-gray-100'"
        >
          <span>📁</span>
          <span>挂载系统目录</span>
        </button>
      </div>

      <!-- 2. 面包屑路径导航栏 -->
      <div class="flex items-center space-x-2 px-2 py-1.5 bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-gray-100 dark:border-gray-800 mb-2 overflow-x-auto no-scrollbar">
        <!-- 后退按钮 -->
        <button 
          @click="parentPath && fetchDirectory(parentPath)" 
          :disabled="isRoot || loading" 
          class="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-500 disabled:opacity-30 disabled:hover:bg-transparent transition-colors"
          title="返回上一级"
        >
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15 19l-7-7 7-7" />
          </svg>
        </button>

        <span class="text-gray-300 dark:text-gray-700">|</span>

        <!-- 面包屑流 -->
        <div class="flex items-center space-x-1 whitespace-nowrap text-xs">
          <template v-for="(crumb, idx) in breadcrumbs" :key="idx">
            <span v-if="idx > 0" class="text-gray-400 dark:text-gray-600">/</span>
            <button 
              @click="clickBreadcrumb(crumb.path)"
              :disabled="loading"
              class="font-medium hover:text-primary transition-colors focus:outline-none"
              :class="idx === breadcrumbs.length - 1 ? 'text-gray-800 dark:text-gray-200 font-bold' : 'text-gray-400 dark:text-gray-500'"
            >
              {{ crumb.name }}
            </button>
          </template>
        </div>
      </div>

      <!-- 搜索栏 -->
      <div class="flex items-center gap-2 mb-2">
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
            @click="clearSearch"
            class="absolute inset-y-0 right-0 pr-2.5 flex items-center text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
            title="清除搜索"
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <button
          type="button"
          @click="includeSubdirs = !includeSubdirs"
          class="flex-shrink-0 px-2.5 py-2 rounded-lg text-[10px] font-bold border transition-all focus:outline-none"
          :class="includeSubdirs
            ? 'bg-primary/10 border-primary/30 text-primary'
            : 'bg-gray-50 dark:bg-gray-800 border-gray-200 dark:border-gray-700 text-gray-500'"
        >
          含子目录
        </button>
      </div>
      <div v-if="isSearchActive" class="flex items-center justify-between px-1 mb-2 text-[10px] text-gray-400">
        <span>
          <template v-if="includeSubdirs && searchLoading">正在搜索...</template>
          <template v-else>找到 {{ displayItems.length }} 项</template>
          <span v-if="searchTruncated" class="text-amber-600 dark:text-amber-400 ml-1">（结果已截断）</span>
        </span>
        <span v-if="includeSubdirs" class="font-mono truncate max-w-[50%]">{{ currentPath }}</span>
        <span v-else>仅当前目录</span>
      </div>

      <!-- 类型图例 -->
      <div class="flex flex-wrap items-center gap-1.5 px-1 mb-3">
        <span class="text-[10px] font-black text-gray-400 tracking-wider mr-0.5">类型</span>
        <span
          v-for="item in legendItems"
          :key="item.category"
          class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[9px] font-bold"
          :class="[item.badgeBg, item.badgeText]"
        >
          <span class="text-[10px] leading-none">{{ item.icon }}</span>
          {{ item.label }}
        </span>
      </div>

      <!-- 3. 文件及文件夹网格列表 -->
      <div class="flex-1 border border-gray-100 dark:border-gray-800 rounded-xl overflow-hidden flex flex-col relative bg-gray-50/10">
        <!-- 表头 -->
        <div class="grid grid-cols-12 gap-2 px-4 py-2 border-b border-gray-100 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-900/20 text-[10px] font-black text-gray-400 tracking-wider">
          <div class="col-span-6 sm:col-span-5">名称</div>
          <div class="col-span-2 hidden sm:block">类型</div>
          <div class="col-span-2 sm:col-span-2 text-right">大小</div>
          <div class="col-span-4 sm:col-span-3 text-right">修改时间</div>
        </div>

        <!-- 载入层 -->
        <div v-if="loading || (isSearchActive && includeSubdirs && searchLoading)" class="absolute inset-0 bg-white/70 dark:bg-gray-900/70 z-10 flex items-center justify-center">
          <div class="flex flex-col items-center space-y-2">
            <div class="w-8 h-8 border-4 border-primary border-t-transparent rounded-full animate-spin"></div>
            <span class="text-xs text-gray-400 font-medium">正在读取文件系统...</span>
          </div>
        </div>

        <!-- 数据体 -->
        <div class="flex-1 overflow-y-auto custom-scrollbar p-1">
          <div v-if="displayItems.length === 0 && !searchLoading" class="h-full flex flex-col items-center justify-center text-gray-400 py-12">
            <span class="text-4xl mb-2">{{ isSearchActive ? '🔍' : '📂' }}</span>
            <span class="text-xs font-bold">{{ displayEmptyHint }}</span>
          </div>
          
          <div v-else class="space-y-0.5">
            <div 
              v-for="item in displayItems" 
              :key="item.path"
              @click="handleSelectRow(item)"
              @dblclick="isSearchActive && includeSubdirs ? navigateToItemDirectory(item) : handleDoubleClick(item)"
              class="grid grid-cols-12 gap-2 px-3 py-2 rounded-lg cursor-pointer transition-all duration-150 select-none group items-center"
              :class="[
                selectedItem?.path === item.path 
                  ? 'bg-primary/10 dark:bg-primary/20 ring-1 ring-primary/20' 
                  : 'hover:bg-gray-100/50 dark:hover:bg-gray-800/40',
                (activeTab === 'file' && item.is_dir) || (activeTab === 'directory' && !item.is_dir)
                  ? 'opacity-70 group-hover:opacity-100'
                  : ''
              ]"
            >
              <template v-for="visual in [getItemVisual(item)]" :key="item.path + '-visual'">
                <!-- 图标 & 名称 -->
                <div class="col-span-6 sm:col-span-5 flex items-center gap-2.5 min-w-0">
                  <div
                    class="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 ring-1 text-sm"
                    :class="[visual.iconBg, visual.iconRing]"
                  >
                    {{ visual.icon }}
                  </div>
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-1.5 min-w-0">
                      <span 
                        class="text-xs truncate font-medium text-gray-700 dark:text-gray-200"
                        :class="selectedItem?.path === item.path ? 'text-primary font-bold' : ''"
                      >
                        {{ item.name }}
                      </span>
                      <!-- 移动端类型标签 -->
                      <span
                        class="sm:hidden flex-shrink-0 px-1.5 py-0.5 rounded text-[9px] font-bold"
                        :class="[visual.badgeBg, visual.badgeText]"
                      >
                        {{ visual.label }}
                      </span>
                    </div>
                    <div
                      v-if="visual.ext && !item.is_dir"
                      class="text-[9px] font-mono text-gray-400 dark:text-gray-500 mt-0.5 uppercase"
                    >
                      .{{ visual.ext }}
                    </div>
                    <div
                      v-if="isSearchActive && includeSubdirs && getItemLocationHint(item.path)"
                      class="text-[9px] text-gray-400 dark:text-gray-500 mt-0.5 truncate font-mono"
                      :title="item.path"
                    >
                      📂 {{ getItemLocationHint(item.path) }}
                    </div>
                  </div>
                </div>

                <!-- 类型（桌面端） -->
                <div class="col-span-2 hidden sm:flex items-center">
                  <span
                    class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold whitespace-nowrap"
                    :class="[visual.badgeBg, visual.badgeText]"
                  >
                    {{ visual.label }}
                  </span>
                </div>
              </template>
              
              <!-- 大小 -->
              <div class="col-span-2 text-right text-[10px] font-mono text-gray-400 dark:text-gray-500">
                {{ item.is_dir ? '—' : formatSize(item.size) }}
              </div>
              
              <!-- 修改时间 -->
              <div class="col-span-4 sm:col-span-3 text-right text-[10px] font-mono text-gray-400 dark:text-gray-500">
                {{ formatTime(item.mtime) }}
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 4. 底部操作栏 -->
      <div class="mt-4 pt-3 border-t border-gray-100 dark:border-gray-800 flex items-center justify-between">
        <!-- 选中态显示 -->
        <div class="flex-1 min-w-0 pr-4">
          <div class="text-[10px] font-black text-gray-400 uppercase tracking-widest">当前已选择</div>
          <div class="text-xs truncate font-bold text-gray-700 dark:text-gray-200 mt-0.5">
            <template v-if="selectedItem">
              <span class="inline-flex items-center gap-1.5">
                <span
                  class="inline-flex w-5 h-5 rounded items-center justify-center text-[10px] ring-1 flex-shrink-0"
                  :class="[getItemVisual(selectedItem).iconBg, getItemVisual(selectedItem).iconRing]"
                >
                  {{ getItemVisual(selectedItem).icon }}
                </span>
                <span
                  class="px-1.5 py-0.5 rounded text-[9px] font-bold flex-shrink-0"
                  :class="[getItemVisual(selectedItem).badgeBg, getItemVisual(selectedItem).badgeText]"
                >
                  {{ getItemVisual(selectedItem).label }}
                </span>
                <span class="truncate text-gray-500 dark:text-gray-400 font-mono font-normal">{{ selectedItem.path }}</span>
              </span>
            </template>
            <template v-else-if="activeTab === 'directory'">
              <span class="text-primary">📁</span> {{ currentPath }} <span class="text-[10px] font-bold text-primary px-1.5 py-0.5 bg-primary/10 rounded ml-1.5">当前浏览</span>
            </template>
            <template v-else>
              <span class="text-gray-400">请双击文件夹下钻，单击选中目标文件...</span>
            </template>
          </div>
        </div>

        <!-- 按钮组 -->
        <div class="flex items-center space-x-2 flex-shrink-0">
          <button 
            @click="emit('close')"
            class="px-4 py-2 text-xs font-bold text-gray-500 hover:text-gray-700 bg-gray-100 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700 rounded-xl transition-all"
          >
            取消
          </button>
          <button 
            @click="handleConfirm"
            :disabled="!selectedItem && activeTab === 'file'"
            class="px-4 py-2 text-xs font-bold text-white bg-primary hover:bg-primary-hover disabled:opacity-40 disabled:hover:bg-primary rounded-xl shadow-lg shadow-primary/20 hover:shadow-primary/30 transition-all focus:outline-none"
            :style="{ backgroundColor: 'var(--primary-color, #1677ff)' }"
          >
            确认挂载
          </button>
        </div>
      </div>
    </div>
  </Modal>
</template>

<style scoped>
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
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background: rgba(156, 163, 175, 0.45);
}
.no-scrollbar::-webkit-scrollbar {
  display: none;
}
.no-scrollbar {
  -ms-overflow-style: none;
  scrollbar-width: none;
}
</style>
