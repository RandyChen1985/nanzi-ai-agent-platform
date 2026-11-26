<script setup lang="ts">
import { computed, ref } from 'vue'

interface FileNode {
  name: string
  path: string
  is_dir: boolean
  size?: number
  modified_at?: number
  children?: FileNode[]
}

const props = defineProps<{
  treeData: FileNode[]
  selectedPath?: string
  selectedDirectoryPath?: string
  sortBy?: 'name' | 'time'
  sortOrder?: 'asc' | 'desc'
  /**
   * 只读：平台技能在没有 `element:skills:admin` 时只能浏览。
   * 只读时不渲染行内删除按钮，也不派发右键菜单事件（右键菜单全是写入口）。
   */
  readonly?: boolean
}>()

const emit = defineEmits<{
  (e: 'select-file', path: string): void
  (e: 'select-directory', path: string): void
  (e: 'delete-file', path: string): void
  (e: 'context-menu', data: { event: MouseEvent, node: FileNode }): void
}>()

const collapsedDirs = ref<Record<string, boolean>>({})

const sortNodes = (nodes: FileNode[]): FileNode[] => {
  const sortBy = props.sortBy || 'name'
  const sortOrder = props.sortOrder || 'asc'
  const dir = sortOrder === 'asc' ? 1 : -1

  const sorted = [...nodes].sort((a, b) => {
    if (a.is_dir !== b.is_dir) {
      return a.is_dir ? -1 : 1
    }
    if (sortBy === 'time') {
      return ((a.modified_at || 0) - (b.modified_at || 0)) * dir
    }
    return a.name.localeCompare(b.name, 'zh-CN') * dir
  })

  return sorted.map((node) => {
    if (node.is_dir && node.children?.length) {
      return { ...node, children: sortNodes(node.children) }
    }
    return node
  })
}

const displayTree = computed(() => sortNodes(props.treeData))

const toggleDir = (path: string) => {
  collapsedDirs.value[path] = !collapsedDirs.value[path]
}

const selectAndToggleDir = (path: string) => {
  emit('select-directory', path)
  toggleDir(path)
}

const isCollapsed = (path: string) => {
  return collapsedDirs.value[path]
}

const formatSize = (bytes?: number) => {
  if (bytes === undefined) return ''
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i]
}

const formatModifiedAt = (ts?: number) => {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const getFileIcon = (name: string, isDir: boolean) => {
  if (isDir) return 'folder'
  const ext = name.split('.').pop()?.toLowerCase()
  if (ext === 'md') return 'md'
  if (ext === 'py') return 'py'
  if (ext === 'js' || ext === 'ts') return 'js'
  if (ext === 'json') return 'json'
  if (ext === 'sh') return 'sh'
  return 'file'
}
</script>

<template>
  <ul class="space-y-1 pl-1 select-none">
    <li v-for="node in displayTree" :key="node.path" class="text-sm">
      <!-- 文件夹节点 -->
      <div v-if="node.is_dir">
        <div 
          @click="selectAndToggleDir(node.path)"
          @contextmenu.prevent="props.readonly ? undefined : emit('context-menu', { event: $event, node })"
          class="flex items-center justify-between py-1.5 px-2 rounded-lg cursor-pointer group transition-all"
          :class="selectedDirectoryPath === node.path ? 'bg-amber-50 ring-1 ring-amber-200' : 'hover:bg-gray-100'"
        >
          <div class="flex items-center space-x-2 text-gray-700 min-w-0">
            <svg 
              class="w-3.5 h-3.5 text-gray-400 transition-transform duration-200"
              :class="isCollapsed(node.path) ? '' : 'rotate-90'"
              fill="none" 
              stroke="currentColor" 
              viewBox="0 0 24 24"
            >
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 5l7 7-7 7" />
            </svg>
            
            <svg class="w-4 h-4 text-amber-500 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
              <path fill-rule="evenodd" d="M2 6a2 2 0 012-2h4l2 2h4a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" clip-rule="evenodd" />
            </svg>
            <span class="font-medium truncate">{{ node.name }}</span>
            <span v-if="formatModifiedAt(node.modified_at)" class="text-[10px] text-gray-400 font-normal shrink-0">
              {{ formatModifiedAt(node.modified_at) }}
            </span>
          </div>
          
          <button 
            v-if="!props.readonly"
            @click.stop="emit('delete-file', node.path)"
            class="opacity-0 group-hover:opacity-100 p-1 text-red-500 hover:bg-red-50 rounded transition-all flex-shrink-0"
            title="删除文件夹"
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
          </button>
        </div>
        
        <transition name="fade">
          <div v-show="!isCollapsed(node.path)" class="pl-4 border-l border-gray-200 ml-3.5 mt-0.5 space-y-0.5">
            <SkillFileTree 
              :tree-data="node.children || []" 
              :selected-path="selectedPath"
              :selected-directory-path="selectedDirectoryPath"
              :sort-by="sortBy"
              :sort-order="sortOrder"
              :readonly="readonly"
              @select-file="(path) => emit('select-file', path)"
              @select-directory="(path) => emit('select-directory', path)"
              @delete-file="(path) => emit('delete-file', path)"
              @context-menu="(data) => emit('context-menu', data)"
            />
          </div>
        </transition>
      </div>

      <!-- 文件节点 -->
      <div v-else>
        <div 
          @click="emit('select-file', node.path)"
          @contextmenu.prevent="props.readonly ? undefined : emit('context-menu', { event: $event, node })"
          class="flex items-center justify-between py-1.5 px-2 rounded-lg cursor-pointer group transition-all"
          :class="[
            selectedPath === node.path 
              ? 'bg-primary/5 text-primary font-semibold border-l-2 border-primary pl-1.5' 
              : 'hover:bg-gray-100 text-gray-600'
          ]"
        >
          <div class="flex items-center space-x-2 min-w-0">
            <svg v-if="getFileIcon(node.name, false) === 'md'" class="w-4 h-4 text-emerald-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            <svg v-else-if="getFileIcon(node.name, false) === 'py'" class="w-4 h-4 text-primary flex-shrink-0" fill="currentColor" viewBox="0 0 24 24">
              <path d="M11.93 2.02c-1.39 0-2.65.11-3.66.27-2.63.4-3.15 1.57-3.15 3.51v1.94h6.91V8.3H5.12c-1.95 0-3.11.53-3.51 3.16-.43 2.76-.41 4.54.02 7.15.35 2.18 1.48 3.13 3.49 3.13h1.94v-2.73c0-2 1.15-3.41 3.13-3.41h3.76c1.92 0 3.13-1.12 3.13-3.13V5.64c0-1.93-1.07-3.15-3.13-3.5-1-.16-2.27-.27-3.66-.27v.15zm-3.08 2.21c.54 0 .97.43.97.98a.98.98 0 01-.97.97.98.98 0 01-.98-.97c0-.55.44-.98.98-.98zm6.7 5.7c-1.92 0-3.13 1.11-3.13 3.12v2.73H5.51v.57c0 1.93 1.07 3.15 3.13 3.5 1 .16 2.27.27 3.66.27 1.39 0 2.65-.11 3.66-.27 2.63-.4 3.15-1.57 3.15-3.51v-1.94h-6.91v-.57h6.91c1.95 0 3.11-.53 3.51-3.16.43-2.76.41-4.54-.02-7.15-.35-2.18-1.48-3.13-3.49-3.13h-1.94v2.73c0 2-1.15 3.41-3.13 3.41h-3.76zm3.08 5.7c.54 0 .97.43.97.98a.98.98 0 01-.97.97.98.98 0 01-.98-.97c0-.55.44-.98.98-.98z"/>
            </svg>
            <svg v-else-if="getFileIcon(node.name, false) === 'js'" class="w-4 h-4 text-yellow-500 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
              <path fill-rule="evenodd" d="M4 4a2 2 0 012-2h4.586A1 1 0 0112 2.586L15.414 6A1 1 0 0116 6.586V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4zm2 6a1 1 0 011-1h6a1 1 0 110 2H7a1 1 0 01-1-1zm1 3a1 1 0 100 2h6a1 1 0 100-2H7z" clip-rule="evenodd" />
            </svg>
            <svg v-else-if="getFileIcon(node.name, false) === 'json'" class="w-4 h-4 text-primary flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
            </svg>
            <svg v-else-if="getFileIcon(node.name, false) === 'sh'" class="w-4 h-4 text-teal-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
            </svg>
            <svg v-else class="w-4 h-4 text-gray-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
            </svg>
            
            <span class="truncate" :title="node.name">{{ node.name }}</span>
            <span class="text-[10px] text-gray-400 font-normal">({{ formatSize(node.size) }})</span>
            <span v-if="formatModifiedAt(node.modified_at)" class="text-[10px] text-gray-400 font-normal shrink-0">
              {{ formatModifiedAt(node.modified_at) }}
            </span>
          </div>
          
          <button 
            v-if="!props.readonly && node.name.toLowerCase() !== 'skill.md'"
            @click.stop="emit('delete-file', node.path)"
            class="opacity-0 group-hover:opacity-100 p-1 text-red-500 hover:bg-red-50 rounded transition-all flex-shrink-0"
            title="删除文件"
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
          </button>
        </div>
      </div>
    </li>
  </ul>
</template>

<style scoped>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
