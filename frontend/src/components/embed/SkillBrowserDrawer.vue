<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import axios from '@/utils/axios'
import { useToast } from '@/composables/useToast'
import { renderMarkdownPreview } from '@/utils/markdown'

const modelValue = defineModel<boolean>({ default: false })
const keepOpenOnSelect = defineModel<boolean>('keepOpenOnSelect', { default: false })
const pinned = defineModel<boolean>('pinned', { default: false })

const props = withDefaults(
  defineProps<{
    pinnedDockClass?: string
    /** 已挂载到输入框的技能 ID */
    attachedSkillIds?: string[]
  }>(),
  { pinnedDockClass: 'right-0', attachedSkillIds: () => [] },
)

const emit = defineEmits<{
  (e: 'select', skill: SkillItem): void
}>()

const { showToast } = useToast()

interface SkillItem {
  id: string
  name: string
  description?: string
  path?: string
}

const skillsList = ref<SkillItem[]>([])
const isLoadingSkillsList = ref(false)
const skillSearchQuery = ref('')
const showSkillPreviewModal = ref(false)
const previewLoading = ref(false)
const previewSkill = ref<SkillItem | null>(null)
const previewMarkdown = ref('')

const isMobile = ref(false)
let mobileMq: MediaQueryList | null = null
const syncMobile = () => {
  isMobile.value = !!mobileMq?.matches
}

const attachedIdSet = computed(() => new Set(props.attachedSkillIds))

const filteredSkillsList = computed(() => {
  const query = skillSearchQuery.value.trim().toLowerCase()
  if (!query) return skillsList.value
  return skillsList.value.filter((s) =>
    s.name?.toLowerCase().includes(query) ||
    s.id?.toLowerCase().includes(query) ||
    s.description?.toLowerCase().includes(query) ||
    s.path?.toLowerCase().includes(query),
  )
})

const renderedPreviewHtml = computed(() => renderMarkdownPreview(previewMarkdown.value || ''))

const loadSkillsList = async () => {
  skillsList.value = []
  isLoadingSkillsList.value = true
  try {
    const res = await axios.get('/api/portal/skills')
    if (res.data?.status === 'success') {
      skillsList.value = res.data.data || []
    }
  } catch (err) {
    console.error('加载技能列表失败:', err)
    showToast('加载技能列表失败', 'error')
  } finally {
    isLoadingSkillsList.value = false
  }
}

const mountSkill = (skill: SkillItem) => {
  if (attachedIdSet.value.has(skill.id)) {
    showToast('该技能已挂载，请勿重复挂载', 'warning')
    return
  }
  emit('select', skill)
  showToast('已挂载至输入框', 'success')
  if (!keepOpenOnSelect.value) {
    modelValue.value = false
  }
}

const openSkillPreview = async (skill: SkillItem) => {
  previewSkill.value = skill
  previewMarkdown.value = ''
  showSkillPreviewModal.value = true
  previewLoading.value = true
  try {
    const res = await axios.get(`/api/portal/skills/${skill.id}/preview`)
    if (res.data?.status === 'success') {
      const data = res.data.data || {}
      previewMarkdown.value = data.skill_md_content || '（SKILL.md 为空）'
      if (data.name) {
        previewSkill.value = { ...skill, name: data.name, description: data.description || skill.description }
      }
    }
  } catch (err: any) {
    showToast(err.response?.data?.detail || '加载 SKILL.md 失败', 'error')
    showSkillPreviewModal.value = false
  } finally {
    previewLoading.value = false
  }
}

const copyPreviewMarkdown = async () => {
  if (!previewMarkdown.value) return
  try {
    await navigator.clipboard.writeText(previewMarkdown.value)
    showToast('已复制 SKILL.md 内容', 'success')
  } catch {
    showToast('复制失败', 'error')
  }
}

const mountSkillFromPreview = () => {
  if (!previewSkill.value) return
  mountSkill(previewSkill.value)
  if (!keepOpenOnSelect.value) {
    showSkillPreviewModal.value = false
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

watch(modelValue, (open) => {
  setMobileBodyScrollLock(!!open)
  if (open) {
    skillSearchQuery.value = ''
    void loadSkillsList()
  } else {
    showSkillPreviewModal.value = false
    previewSkill.value = null
    previewMarkdown.value = ''
  }
})

const onGlobalKeydown = (event: KeyboardEvent) => {
  if (event.key !== 'Escape' || !modelValue.value) return
  if (showSkillPreviewModal.value) {
    showSkillPreviewModal.value = false
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
                <span class="text-base flex-shrink-0" aria-hidden="true">⚙️</span>
                <span class="truncate">选择技能工作流</span>
                <span
                  v-if="attachedSkillIds.length > 0"
                  class="px-1.5 py-0.5 rounded-full text-[10px] font-bold text-primary bg-primary/10"
                >
                  已挂 {{ attachedSkillIds.length }}
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
                  title="开启后挂载技能不会关闭侧栏，可连续选择"
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
                  aria-label="关闭技能浏览"
                  @click="closeDrawer"
                >
                  <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.2" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            </div>

            <div class="flex-1 overflow-y-auto overscroll-y-contain p-3 sm:p-4 bg-white dark:bg-gray-900/60 min-h-0 touch-pan-y flex flex-col">
              <div class="relative mb-3 shrink-0">
                <span class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                  <svg class="h-4 w-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                  </svg>
                </span>
                <input
                  v-model="skillSearchQuery"
                  type="text"
                  placeholder="搜索技能名称、标识或目录..."
                  class="w-full pl-9 pr-4 py-2 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg focus:ring-2 focus:ring-primary focus:outline-none text-xs transition-all"
                />
              </div>

              <div class="flex-1 min-h-0 overflow-y-auto space-y-2 custom-scrollbar">
                <div v-if="isLoadingSkillsList" class="flex flex-col items-center justify-center py-10 opacity-50">
                  <div class="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
                  <span class="text-[10px] font-bold text-gray-400 mt-2 uppercase tracking-widest">加载中...</span>
                </div>

                <div v-else-if="filteredSkillsList.length === 0" class="text-center py-12">
                  <span class="text-2xl opacity-40">⚙️</span>
                  <p class="text-xs text-gray-400 mt-2 font-bold">未发现可用的智能体技能</p>
                  <p class="text-[10px] text-gray-400/70 mt-1">您可以前往系统控制台「技能管理」页面创建</p>
                </div>

                <div
                  v-for="skill in filteredSkillsList"
                  :key="skill.id"
                  class="group p-3 border rounded-xl cursor-pointer transition-all flex items-start space-x-3"
                  :class="attachedIdSet.has(skill.id)
                    ? 'bg-gray-50 dark:bg-gray-800/80 border-gray-200 dark:border-gray-700 opacity-80'
                    : 'bg-white dark:bg-gray-800 border-gray-150 dark:border-gray-700/60 hover:border-primary/40 hover:shadow-md'"
                  @dblclick="mountSkill(skill)"
                >
                  <div
                    class="w-8 h-8 rounded-lg flex items-center justify-center text-sm flex-shrink-0 transition-transform font-mono"
                    :class="attachedIdSet.has(skill.id)
                      ? 'bg-gray-200 dark:bg-gray-700 text-gray-500'
                      : 'bg-primary/10 dark:bg-primary/20 text-primary group-hover:scale-105'"
                  >
                    ⚙️
                  </div>
                  <div class="flex-1 min-w-0">
                    <div class="flex items-center justify-between gap-2">
                      <span
                        class="text-xs font-bold truncate pr-2 transition-colors"
                        :class="attachedIdSet.has(skill.id)
                          ? 'text-gray-500 dark:text-gray-400'
                          : 'text-gray-800 dark:text-gray-100 group-hover:text-primary'"
                      >
                        {{ skill.name }}
                      </span>
                      <div class="flex items-center gap-2 flex-shrink-0">
                        <button
                          type="button"
                          class="text-[10px] text-primary hover:text-primary-dark hover:underline flex items-center space-x-0.5"
                          @click.stop="openSkillPreview(skill)"
                        >
                          <span>详情</span>
                          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/></svg>
                        </button>
                        <span v-if="!attachedIdSet.has(skill.id)" class="text-[9px] font-mono text-gray-400 shrink-0 select-all uppercase">ID: {{ skill.id }}</span>
                      </div>
                    </div>
                    <p class="text-[10px] text-gray-500 dark:text-gray-400 mt-1 line-clamp-2">{{ skill.description || '暂无描述信息' }}</p>
                    <div class="mt-2.5 flex items-center justify-between gap-4">
                      <div
                        v-if="skill.path"
                        class="flex items-center gap-1 text-[9px] font-mono text-gray-400 dark:text-gray-500 min-w-0"
                        :title="skill.path"
                      >
                        <svg class="w-3 h-3 shrink-0 opacity-70" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                        </svg>
                        <span class="truncate">{{ skill.path }}</span>
                      </div>
                      <div v-else />
                      <div class="flex-shrink-0">
                        <span
                          v-if="attachedIdSet.has(skill.id)"
                          class="text-[9px] font-bold text-gray-400 dark:text-gray-500 flex items-center gap-0.5 select-none"
                        >
                          已加载
                        </span>
                        <button
                          v-else
                          type="button"
                          class="px-2 py-0.5 text-[9px] font-medium bg-green-500 hover:bg-green-600 text-white rounded transition-all active:scale-95 flex items-center space-x-0.5 shadow-sm"
                          @click.stop="mountSkill(skill)"
                        >
                          <span>加载技能</span>
                          <svg class="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4"/></svg>
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div class="shrink-0 p-3 sm:p-4 border-t border-gray-150 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-800/20 text-center">
              <span class="text-[10px] text-gray-400 font-bold">双击卡片或点击「加载技能」按钮可引入至输入框</span>
            </div>
          </div>
        </transition>
      </div>
    </div>

    <div
      v-if="showSkillPreviewModal && previewSkill"
      class="fixed inset-0 z-[131] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      @click.self="showSkillPreviewModal = false"
    >
      <div class="bg-white/95 dark:bg-gray-800/95 border border-gray-200/50 dark:border-gray-700/50 rounded-2xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col overflow-hidden">
        <div class="px-5 py-4 border-b border-gray-100 dark:border-gray-700 flex justify-between items-center bg-gray-50/50 dark:bg-gray-800/50 flex-shrink-0 gap-3">
          <div class="min-w-0">
            <div class="flex items-center gap-2">
              <span class="text-lg">⚙️</span>
              <h3 class="text-base font-bold text-gray-800 dark:text-gray-100 truncate">{{ previewSkill.name }}</h3>
            </div>
            <p class="text-[10px] font-mono text-gray-400 mt-1 truncate">SKILL.md · {{ previewSkill.id }}</p>
          </div>
          <button type="button" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors shrink-0" @click="showSkillPreviewModal = false">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" /></svg>
          </button>
        </div>
        <div class="flex-1 overflow-y-auto min-h-0 bg-white dark:bg-gray-900">
          <div v-if="previewLoading" class="flex flex-col items-center justify-center py-16 opacity-60">
            <div class="w-7 h-7 border-2 border-primary border-t-transparent rounded-full animate-spin" />
            <span class="text-[10px] font-bold text-gray-400 mt-3 uppercase tracking-widest">加载 SKILL.md...</span>
          </div>
          <div
            v-else
            class="p-5 sm:p-6 text-sm text-gray-700 dark:text-gray-200 leading-relaxed select-text markdown-body"
            v-html="renderedPreviewHtml"
          />
        </div>
        <div class="px-5 py-3 bg-gray-50/80 dark:bg-gray-800/80 border-t border-gray-100 dark:border-gray-700 flex justify-between items-center flex-shrink-0 gap-2">
          <button
            type="button"
            class="px-3 py-1.5 text-xs text-gray-600 bg-white dark:bg-gray-700 dark:text-gray-200 border border-gray-200 dark:border-gray-600 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-600 transition-colors font-medium disabled:opacity-50"
            :disabled="previewLoading"
            @click="copyPreviewMarkdown"
          >
            复制内容
          </button>
          <div class="flex items-center gap-2 ml-auto">
            <button
              type="button"
              class="px-3.5 py-1.5 text-xs text-white rounded-lg transition-all font-medium disabled:opacity-50"
              :class="attachedIdSet.has(previewSkill.id) ? 'bg-gray-400 cursor-not-allowed' : 'bg-primary hover:bg-primary-dark'"
              :style="!attachedIdSet.has(previewSkill.id) ? { backgroundColor: 'var(--primary-color, #1677ff)' } : {}"
              :disabled="attachedIdSet.has(previewSkill.id) || previewLoading"
              @click="mountSkillFromPreview"
            >
              {{ attachedIdSet.has(previewSkill.id) ? '已挂载' : '挂载至输入框' }}
            </button>
            <button type="button" class="px-3.5 py-1.5 text-xs text-gray-500 bg-gray-100 dark:bg-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors font-medium" @click="showSkillPreviewModal = false">
              关闭
            </button>
          </div>
        </div>
      </div>
    </div>
  </teleport>
</template>

<style scoped>
.markdown-body :deep(p) {
  margin: 0 0 0.75em;
}
.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
  font-weight: 700;
  margin: 1em 0 0.5em;
  line-height: 1.3;
}
.markdown-body :deep(h1) { font-size: 1.35em; }
.markdown-body :deep(h2) { font-size: 1.15em; }
.markdown-body :deep(h3) { font-size: 1.05em; }
.markdown-body :deep(code) {
  font-family: ui-monospace, monospace;
  font-size: 0.9em;
  background: rgba(0, 0, 0, 0.06);
  padding: 0.1em 0.35em;
  border-radius: 0.25rem;
}
.markdown-body :deep(pre) {
  background: #1e293b;
  color: #e2e8f0;
  padding: 0.75rem 1rem;
  border-radius: 0.5rem;
  overflow-x: auto;
  margin: 0.75em 0;
}
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin: 0.5em 0;
  padding-left: 1.25em;
}
.markdown-body :deep(blockquote) {
  border-left: 3px solid #cbd5e1;
  padding-left: 0.75em;
  color: #64748b;
  margin: 0.75em 0;
}
.markdown-body :deep(a) {
  color: var(--primary-color, #1677ff);
  text-decoration: underline;
}
</style>
