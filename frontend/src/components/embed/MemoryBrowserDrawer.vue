<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import axios from '@/utils/axios'
import { useToast } from '@/composables/useToast'

const modelValue = defineModel<boolean>({ default: false })
const keepOpenOnSelect = defineModel<boolean>('keepOpenOnSelect', { default: false })
const pinned = defineModel<boolean>('pinned', { default: false })

const props = withDefaults(
  defineProps<{
    pinnedDockClass?: string
    /** 已挂载到输入框的记忆会话 ID，逗号分隔 */
    attachedConversationIds?: string
  }>(),
  { pinnedDockClass: 'right-0', attachedConversationIds: '' },
)

const emit = defineEmits<{
  (e: 'mount', memory: MemorySummary): void
  (e: 'cleared', payload: { conversationIds: string[]; all?: boolean }): void
}>()

const { showToast } = useToast()

interface MemorySummary {
  conversation_id: string
  summary: string
  last_active?: number
}

const memoryList = ref<MemorySummary[]>([])
const isLoadingMemoryList = ref(false)
const memorySearchQuery = ref('')
const showMemoryDetailModal = ref(false)
const selectedMemoryDetail = ref<MemorySummary | null>(null)
const memoryToDelete = ref<MemorySummary | null>(null)
const showClearAllConfirm = ref(false)
const clearingMemory = ref(false)
const clearingAllMemory = ref(false)

const isMobile = ref(false)
let mobileMq: MediaQueryList | null = null
const syncMobile = () => {
  isMobile.value = !!mobileMq?.matches
}

const attachedIdSet = computed(() => new Set(
  props.attachedConversationIds
    .split(',')
    .map((id) => id.trim())
    .filter(Boolean),
))

const attachedCount = computed(() => attachedIdSet.value.size)

const filteredMemoryList = computed(() => {
  const query = memorySearchQuery.value.trim().toLowerCase()
  if (!query) return memoryList.value
  return memoryList.value.filter((m) =>
    (m.summary || '').toLowerCase().includes(query) ||
    (m.conversation_id || '').toLowerCase().includes(query),
  )
})

const loadMemoryList = async () => {
  memoryList.value = []
  isLoadingMemoryList.value = true
  try {
    const res = await axios.get('/api/portal/memory/my/summaries', { params: { limit: 50 } })
    if (res.data?.status === 'success') {
      memoryList.value = (res.data.data || []).filter((m: MemorySummary) => m.summary)
    }
  } catch (err) {
    console.error('加载记忆列表失败:', err)
  } finally {
    isLoadingMemoryList.value = false
  }
}

const mountMemory = (memory: MemorySummary) => {
  if (attachedIdSet.value.has(memory.conversation_id)) {
    showToast('该记忆已挂载，请勿重复挂载', 'warning')
    return
  }
  emit('mount', memory)
  showToast('已挂载至输入框', 'success')
  if (!keepOpenOnSelect.value) {
    modelValue.value = false
  }
}

const openMemoryDetail = (memory: MemorySummary) => {
  selectedMemoryDetail.value = memory
  showMemoryDetailModal.value = true
}

const mountMemoryFromDetail = () => {
  if (!selectedMemoryDetail.value) return
  mountMemory(selectedMemoryDetail.value)
  if (!keepOpenOnSelect.value) {
    showMemoryDetailModal.value = false
  }
}

const copyMemoryDetailText = async () => {
  const text = selectedMemoryDetail.value?.summary
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    /* ignore */
  }
}

const confirmDeleteMemory = (memory: MemorySummary) => {
  memoryToDelete.value = memory
}

const executeDeleteMemory = async () => {
  const target = memoryToDelete.value
  if (!target) return
  const cid = target.conversation_id
  memoryToDelete.value = null
  clearingMemory.value = true
  try {
    const response = await axios.delete(`/api/portal/memory/my/summaries/${cid}`)
    if (response.data?.status === 'success') {
      showToast('会话记忆及历史聊天记录已清除', 'success')
      memoryList.value = memoryList.value.filter((m) => m.conversation_id !== cid)
      if (showMemoryDetailModal.value && selectedMemoryDetail.value?.conversation_id === cid) {
        showMemoryDetailModal.value = false
        selectedMemoryDetail.value = null
      }
      emit('cleared', { conversationIds: [cid] })
    }
  } catch (err: any) {
    showToast(err.response?.data?.detail || '清除会话记忆失败', 'error')
  } finally {
    clearingMemory.value = false
  }
}

const confirmClearAllMemory = () => {
  showClearAllConfirm.value = true
}

const executeClearAllMemory = async () => {
  showClearAllConfirm.value = false
  clearingAllMemory.value = true
  try {
    const response = await axios.delete('/api/portal/memory/my/session-memory')
    if (response.data?.status === 'success') {
      showToast('全部会话记忆已清除', 'success')
      memoryList.value = []
      showMemoryDetailModal.value = false
      selectedMemoryDetail.value = null
      emit('cleared', { conversationIds: [], all: true })
    }
  } catch (err: any) {
    showToast(err.response?.data?.detail || '清除全部会话记忆失败', 'error')
  } finally {
    clearingAllMemory.value = false
  }
}

const setMobileBodyScrollLock = (locked: boolean) => {
  if (!isMobile.value) return
  document.body.style.overflow = locked ? 'hidden' : ''
}

const closeDrawer = () => {
  modelValue.value = false
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

watch(
  modelValue,
  (open) => {
    setMobileBodyScrollLock(!!open)
    if (open) {
      memorySearchQuery.value = ''
      void loadMemoryList()
    } else {
      showMemoryDetailModal.value = false
      selectedMemoryDetail.value = null
    }
  },
)

const onGlobalKeydown = (event: KeyboardEvent) => {
  if (event.key !== 'Escape' || !modelValue.value) return
  if (memoryToDelete.value) {
    memoryToDelete.value = null
    return
  }
  if (showClearAllConfirm.value) {
    showClearAllConfirm.value = false
    return
  }
  if (showMemoryDetailModal.value) {
    showMemoryDetailModal.value = false
    return
  }
  if (!pinned.value) closeDrawer()
}

onMounted(() => {
  mobileMq = window.matchMedia('(max-width: 639px)')
  syncMobile()
  if (isMobile.value && pinned.value) {
    pinned.value = false
  }
  mobileMq.addEventListener('change', syncMobile)
  document.addEventListener('keydown', onGlobalKeydown)
})

onUnmounted(() => {
  mobileMq?.removeEventListener('change', syncMobile)
  document.removeEventListener('keydown', onGlobalKeydown)
  setMobileBodyScrollLock(false)
})
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
                <span class="text-base flex-shrink-0" aria-hidden="true">🧠</span>
                <span class="truncate">选择记忆记录</span>
                <span
                  v-if="attachedCount > 0"
                  class="px-1.5 py-0.5 rounded-full text-[10px] font-bold text-primary bg-primary/10"
                >
                  已挂 {{ attachedCount }}
                </span>
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
                  title="开启后挂载记忆不会关闭侧栏，可连续选择"
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
                  aria-label="关闭记忆浏览"
                  @click="closeDrawer"
                >
                  <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            </div>

            <div class="flex-1 overflow-y-auto overscroll-y-contain p-3 sm:p-4 bg-white dark:bg-gray-900/60 min-h-0 touch-pan-y flex flex-col">
              <div class="flex flex-col gap-2 mb-3 shrink-0">
                <div class="relative">
                  <span class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                    <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                    </svg>
                  </span>
                  <input
                    v-model="memorySearchQuery"
                    type="text"
                    placeholder="搜索记忆内容..."
                    class="w-full pl-9 pr-4 py-2 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg focus:ring-2 focus:ring-primary focus:outline-none text-xs transition-all"
                  />
                </div>
                <button
                  v-if="!isLoadingMemoryList && memoryList.length > 0"
                  type="button"
                  class="w-full px-3 py-1.5 border border-red-200 dark:border-red-500/30 text-red-600 dark:text-red-400 rounded-lg text-[11px] font-medium hover:bg-red-50 dark:hover:bg-red-500/10 active:scale-[0.99] transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                  :disabled="clearingAllMemory || clearingMemory"
                  @click="confirmClearAllMemory"
                >
                  {{ clearingAllMemory ? '清除中…' : '一键清除所有记忆' }}
                </button>
              </div>

              <div class="flex-1 min-h-0 overflow-y-auto space-y-2 custom-scrollbar">
                <div v-if="isLoadingMemoryList" class="flex flex-col items-center justify-center py-10 opacity-50">
                  <div class="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
                  <span class="text-[10px] font-bold text-gray-400 mt-2 uppercase tracking-widest">加载中...</span>
                </div>

                <div v-else-if="filteredMemoryList.length === 0" class="text-center py-12">
                  <span class="text-2xl opacity-40">🧠</span>
                  <p class="text-xs text-gray-400 mt-2 font-bold">暂无可用的记忆记录</p>
                  <p class="text-[10px] text-gray-400/70 mt-1">与 AI 对话后系统会自动生成记忆摘要</p>
                </div>

                <div
                  v-for="memory in filteredMemoryList"
                  :key="memory.conversation_id"
                  class="group p-3 border rounded-xl cursor-pointer transition-all flex items-start space-x-3"
                  :class="attachedIdSet.has(memory.conversation_id)
                    ? 'bg-gray-50 dark:bg-gray-800/80 border-gray-200 dark:border-gray-700 opacity-80'
                    : 'bg-white dark:bg-gray-800 border-gray-150 dark:border-gray-700/60 hover:border-primary/30 hover:shadow-sm'"
                  @dblclick="mountMemory(memory)"
                >
                  <div
                    class="w-8 h-8 rounded-lg flex items-center justify-center text-sm flex-shrink-0"
                    :class="attachedIdSet.has(memory.conversation_id)
                      ? 'bg-gray-200 dark:bg-gray-700 text-gray-500'
                      : 'bg-primary/10 dark:bg-primary/20 text-primary'"
                  >
                    🧠
                  </div>
                  <div class="flex-1 min-w-0">
                    <div class="flex items-center justify-between mb-1">
                      <span class="text-[10px] font-mono text-gray-400 dark:text-gray-500 uppercase tracking-wider flex items-center">
                        {{ memory.last_active ? new Date(memory.last_active * 1000).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' }) : '未知日期' }}
                        <span v-if="memory.last_active" class="ml-2 text-gray-300 dark:text-gray-600 font-mono">
                          {{ new Date(memory.last_active * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) }}
                        </span>
                      </span>
                      <div class="flex items-center gap-2 flex-shrink-0">
                        <button
                          type="button"
                          class="text-[10px] text-primary hover:text-primary-dark hover:underline flex items-center space-x-0.5"
                          @click.stop="openMemoryDetail(memory)"
                        >
                          <span>详情</span>
                          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/></svg>
                        </button>
                        <button
                          type="button"
                          class="text-[10px] font-medium text-red-600 hover:text-red-800 hover:underline transition-all disabled:opacity-50"
                          :disabled="clearingMemory || clearingAllMemory"
                          @click.stop="confirmDeleteMemory(memory)"
                        >
                          清除记忆
                        </button>
                      </div>
                    </div>
                    <p class="text-xs text-gray-700 dark:text-gray-200 leading-relaxed line-clamp-3">{{ memory.summary }}</p>
                    <div class="mt-2.5 flex items-center justify-end">
                      <div class="flex-shrink-0">
                        <span
                          v-if="attachedIdSet.has(memory.conversation_id)"
                          class="text-[9px] font-bold text-gray-400 dark:text-gray-500 flex items-center gap-0.5 select-none"
                        >
                          已加载
                        </span>
                        <button
                          v-else
                          type="button"
                          class="px-2 py-0.5 text-[9px] font-medium bg-green-500 hover:bg-green-600 text-white rounded transition-all active:scale-95 flex items-center space-x-0.5 shadow-sm"
                          @click.stop="mountMemory(memory)"
                        >
                          <span>加载记忆</span>
                          <svg class="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4"/></svg>
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div class="shrink-0 p-3 sm:p-4 border-t border-gray-150 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-800/20 text-center">
              <span class="text-[10px] text-gray-400 font-bold">双击卡片或点击「加载记忆」按钮可引入至输入框</span>
            </div>
          </div>
        </transition>
      </div>
    </div>

    <div
      v-if="showMemoryDetailModal && selectedMemoryDetail"
      class="fixed inset-0 z-[131] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      @click.self="showMemoryDetailModal = false"
    >
      <div class="bg-white/95 dark:bg-gray-800/95 border border-gray-200/50 dark:border-gray-700/50 rounded-2xl shadow-2xl w-full max-w-md flex flex-col overflow-hidden">
        <div class="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center bg-gray-50/50 dark:bg-gray-800/50 flex-shrink-0">
          <div class="flex items-center space-x-2">
            <span class="text-lg">🧠</span>
            <h3 class="text-base font-bold text-gray-800 dark:text-gray-100">记忆详情</h3>
          </div>
          <button type="button" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors" @click="showMemoryDetailModal = false">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg>
          </button>
        </div>
        <div class="p-5 flex-1 overflow-y-auto max-h-[50vh] bg-white dark:bg-gray-800">
          <div class="text-[10px] font-mono text-gray-400 dark:text-gray-500 uppercase tracking-wider mb-2">
            生成时间：{{ selectedMemoryDetail.last_active ? new Date(selectedMemoryDetail.last_active * 1000).toLocaleString('zh-CN') : '未知时间' }}
          </div>
          <div class="text-xs text-gray-500 dark:text-gray-400 font-mono mb-4 truncate select-all">
            会话ID：{{ selectedMemoryDetail.conversation_id }}
          </div>
          <div class="p-4 bg-gray-50 dark:bg-gray-900 rounded-xl border border-gray-100 dark:border-gray-800 text-sm text-gray-700 dark:text-gray-200 leading-relaxed whitespace-pre-wrap select-text">
            {{ selectedMemoryDetail.summary }}
          </div>
        </div>
        <div class="px-5 py-3 bg-gray-50/80 dark:bg-gray-800/80 border-t border-gray-100 dark:border-gray-700 flex justify-between items-center flex-shrink-0 gap-2">
          <button
            type="button"
            class="px-3 py-1.5 text-xs text-red-600 hover:text-red-800 hover:underline font-medium disabled:opacity-50"
            :disabled="clearingMemory || clearingAllMemory"
            @click="confirmDeleteMemory(selectedMemoryDetail)"
          >
            清除记忆
          </button>
          <div class="flex items-center gap-2 ml-auto">
            <button
              type="button"
              class="px-3 py-1.5 text-xs text-gray-600 bg-white dark:bg-gray-700 dark:text-gray-200 border border-gray-200 dark:border-gray-600 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-600 transition-colors font-medium"
              @click="copyMemoryDetailText"
            >
              复制内容
            </button>
            <button
              type="button"
              class="px-3.5 py-1.5 text-xs text-white rounded-lg transition-all font-medium disabled:opacity-50"
              :class="selectedMemoryDetail && attachedIdSet.has(selectedMemoryDetail.conversation_id) ? 'bg-gray-400 cursor-not-allowed' : 'bg-primary hover:bg-primary-dark'"
              :style="selectedMemoryDetail && !attachedIdSet.has(selectedMemoryDetail.conversation_id) ? { backgroundColor: 'var(--primary-color, #1677ff)' } : {}"
              :disabled="!!selectedMemoryDetail && attachedIdSet.has(selectedMemoryDetail.conversation_id)"
              @click="mountMemoryFromDetail"
            >
              {{ selectedMemoryDetail && attachedIdSet.has(selectedMemoryDetail.conversation_id) ? '已挂载' : '挂载至输入框' }}
            </button>
            <button type="button" class="px-3.5 py-1.5 text-xs text-gray-500 bg-gray-100 dark:bg-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors font-medium" @click="showMemoryDetailModal = false">
              关闭
            </button>
          </div>
        </div>
      </div>
    </div>

    <div
      v-if="memoryToDelete"
      class="fixed inset-0 z-[131] flex items-center justify-center p-4 bg-black/30 backdrop-blur-[1px]"
      @click.self="memoryToDelete = null"
    >
      <div class="w-full max-w-sm rounded-2xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-2xl p-4" @click.stop>
        <h3 class="text-sm font-bold mb-2 text-red-600 dark:text-red-400">清除会话记忆</h3>
        <p class="text-xs text-gray-500 dark:text-gray-400 mb-4 leading-relaxed">
          确定清除该会话的记忆摘要及 Redis 中的历史聊天记录？清除后，该会话的历史事实将不会自动注入未来的会话上下文。此操作无法撤销。
        </p>
        <div class="flex justify-end gap-2">
          <button type="button" class="px-3 py-1.5 text-xs font-bold text-gray-500" :disabled="clearingMemory" @click="memoryToDelete = null">取消</button>
          <button type="button" class="px-3 py-1.5 text-xs font-bold text-white bg-red-500 rounded-lg disabled:opacity-60" :disabled="clearingMemory" @click="executeDeleteMemory">
            {{ clearingMemory ? '清除中…' : '确认清除' }}
          </button>
        </div>
      </div>
    </div>

    <div
      v-if="showClearAllConfirm"
      class="fixed inset-0 z-[131] flex items-center justify-center p-4 bg-black/30 backdrop-blur-[1px]"
      @click.self="showClearAllConfirm = false"
    >
      <div class="w-full max-w-sm rounded-2xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-2xl p-4" @click.stop>
        <h3 class="text-sm font-bold mb-2 text-red-600 dark:text-red-400">一键清除所有记忆</h3>
        <p class="text-xs text-gray-500 dark:text-gray-400 mb-4 leading-relaxed">
          确定清除全部会话记忆？将删除所有会话摘要、每日摘要及 Redis 中的历史聊天记录。清除后，这些历史事实将不再自动注入未来的会话上下文。此操作无法撤销。
        </p>
        <div class="flex justify-end gap-2">
          <button type="button" class="px-3 py-1.5 text-xs font-bold text-gray-500" :disabled="clearingAllMemory" @click="showClearAllConfirm = false">取消</button>
          <button type="button" class="px-3 py-1.5 text-xs font-bold text-white bg-red-500 rounded-lg disabled:opacity-60" :disabled="clearingAllMemory" @click="executeClearAllMemory">
            {{ clearingAllMemory ? '清除中…' : '确认清除' }}
          </button>
        </div>
      </div>
    </div>
  </teleport>
</template>
