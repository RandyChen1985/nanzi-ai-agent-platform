<script setup lang="ts">
import { ref, watch } from 'vue'
import axios from '@/utils/axios'

type DirectoryItem = {
  name: string
  path: string
  is_dir: boolean
  is_user_workspace?: boolean
}

const props = withDefaults(
  defineProps<{
    visible: boolean
    conversationId?: string | null
    defaultFilename: string
    /** 标题文案：保存文件与解压共用同一个目录树，靠这几项区分语义。 */
    title?: string
    subtitle?: string
    nameLabel?: string
    submitLabel?: string
    /** 打开时优先定位到的目录；不在本人工作区内时回退到工作区根。 */
    defaultPath?: string | null
    /** 请求进行中：禁用提交并改文案，避免重复点击。 */
    submitting?: boolean
    /** 解压场景允许留空 = 直接解到当前目录。 */
    allowEmptyName?: boolean
  }>(),
  {
    conversationId: null,
    title: '保存到目录',
    subtitle: '仅可保存到当前用户的 AI 工作区',
    nameLabel: '文件名',
    submitLabel: '保存',
    defaultPath: null,
    submitting: false,
    allowEmptyName: false,
  },
)

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'save', payload: { parentPath: string; name: string }): void
}>()

const loading = ref(false)
const errorMessage = ref('')
const currentPath = ref('')
const userWorkspaceRoot = ref('')
const parentPath = ref<string | null>(null)
const directories = ref<DirectoryItem[]>([])
const filename = ref('')

const normalizePath = (path: string) => String(path || '').replace(/\\/g, '/').replace(/\/+$/, '')

const loadDirectory = async (path: string) => {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await axios.get('/api/v1/chat/fs/list', { params: { path } })
    const data = response.data?.data
    if (!data?.current_path || !data?.user_workspace_root) {
      throw new Error('无法解析当前用户的工作目录')
    }
    const root = normalizePath(data.user_workspace_root)
    const resolvedPath = normalizePath(data.current_path)
    if (resolvedPath !== root && !resolvedPath.startsWith(`${root}/`)) {
      throw new Error('目录不在当前用户工作区内')
    }
    userWorkspaceRoot.value = root
    currentPath.value = resolvedPath
    parentPath.value = resolvedPath === root ? null : normalizePath(data.parent_path || root)
    directories.value = (data.items || []).filter((item: DirectoryItem) => item.is_dir && item.name !== '.trash')
  } catch (error: any) {
    errorMessage.value = error?.response?.data?.detail || error?.message || '读取目录失败'
    directories.value = []
  } finally {
    loading.value = false
  }
}

const loadRoot = async () => {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await axios.get('/api/v1/chat/fs/list')
    const data = response.data?.data
    const root = normalizePath(data?.user_workspace_root || '')
    const rootItem = (data?.items || []).find((item: DirectoryItem) => item.is_user_workspace)
    const resolvedRoot = root || normalizePath(rootItem?.path || '')
    if (!resolvedRoot) throw new Error('当前用户没有可用的工作目录')
    filename.value = props.defaultFilename
    const preferred = normalizePath(props.defaultPath || '')
    if (preferred && (preferred === resolvedRoot || preferred.startsWith(`${resolvedRoot}/`))) {
      await loadDirectory(preferred)
    } else {
      // 公共只读目录、会话目录等不在本人工作区内：回退到根目录，由用户自己下钻
      await loadDirectory(resolvedRoot)
    }
  } catch (error: any) {
    errorMessage.value = error?.response?.data?.detail || error?.message || '读取用户工作目录失败'
    loading.value = false
  }
}

const goUp = () => {
  if (parentPath.value && userWorkspaceRoot.value) {
    void loadDirectory(parentPath.value)
  }
}

const selectDirectory = (directory: DirectoryItem) => {
  if (directory.is_dir) void loadDirectory(directory.path)
}

const submit = () => {
  if (props.submitting) return
  const name = filename.value.trim()
  if (!name && !props.allowEmptyName) {
    errorMessage.value = `请输入${props.nameLabel}`
    return
  }
  if (name && (name === '.' || name === '..' || /[\\/]/.test(name))) {
    errorMessage.value = `${props.nameLabel}不能包含路径分隔符`
    return
  }
  if (!currentPath.value) return
  emit('save', { parentPath: currentPath.value, name })
}

watch(
  () => props.visible,
  (visible) => {
    if (visible) void loadRoot()
  },
)
</script>

<template>
  <Teleport to="body">
  <div
    v-if="visible"
    class="fixed inset-0 z-[360] flex items-center justify-center bg-black/35 p-4"
    @click.self="emit('close')"
  >
    <div class="w-full max-w-lg rounded-2xl bg-white p-5 shadow-2xl dark:bg-gray-800">
      <div class="flex items-center justify-between">
        <div>
          <h3 class="text-sm font-bold text-gray-900 dark:text-gray-100">{{ title }}</h3>
          <p class="mt-1 text-[11px] text-gray-500 dark:text-gray-400">{{ subtitle }}</p>
        </div>
        <button type="button" class="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700" @click="emit('close')">✕</button>
      </div>

      <div class="mt-4 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-700 dark:bg-gray-900/50">
        <div class="flex items-center justify-between gap-2">
          <span class="text-xs font-semibold text-gray-700 dark:text-gray-200">选择目录</span>
          <button
            type="button"
            class="text-[11px] text-primary disabled:cursor-default disabled:text-gray-400"
            :disabled="!parentPath || loading"
            @click="goUp"
          >上一级</button>
        </div>
        <div class="mt-1 truncate font-mono text-[11px] text-gray-500 dark:text-gray-400" :title="currentPath">
          {{ currentPath || '加载中...' }}
        </div>
      </div>

      <div class="mt-3 max-h-40 overflow-y-auto rounded-lg border border-gray-200 dark:border-gray-700">
        <div v-if="loading" class="px-3 py-5 text-center text-xs text-gray-400">正在读取目录…</div>
        <div v-else-if="errorMessage" class="px-3 py-5 text-center text-xs text-rose-500">{{ errorMessage }}</div>
        <div v-else-if="!directories.length" class="px-3 py-5 text-center text-xs text-gray-400">当前目录没有子目录，可直接选择这里</div>
        <button
          v-for="directory in directories"
          :key="directory.path"
          type="button"
          class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-gray-700 hover:bg-primary/5 dark:text-gray-200 dark:hover:bg-primary/10"
          @click="selectDirectory(directory)"
        >
          <span>📁</span><span class="truncate">{{ directory.name }}</span>
        </button>
      </div>

      <label class="mt-4 block text-xs font-semibold text-gray-700 dark:text-gray-200">
        {{ nameLabel }}
        <input
          v-model="filename"
          type="text"
          class="mt-1 w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs outline-none focus:border-primary dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
          :placeholder="allowEmptyName ? '留空则直接解压到当前目录' : ''"
          @keyup.enter="submit"
        />
      </label>

      <div class="mt-5 flex justify-end gap-2">
        <button type="button" class="rounded-lg px-3 py-2 text-xs text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700" @click="emit('close')">取消</button>
        <button type="button" class="rounded-lg bg-primary px-4 py-2 text-xs font-bold text-white hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50" :disabled="loading || !currentPath || submitting" @click="submit">{{ submitting ? '处理中…' : submitLabel }}</button>
      </div>
    </div>
  </div>
  </Teleport>
</template>
