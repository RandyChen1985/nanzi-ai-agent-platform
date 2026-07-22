<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import axios from '@/utils/axios'
import { useToast } from '@/composables/useToast'

export interface SkillItem {
  id: string
  name: string
  description?: string
  path?: string
  scope?: 'global' | 'personal'
}

const props = withDefaults(
  defineProps<{
    /** 已挂载到输入框的技能 ID */
    attachedSkillIds?: string[]
    /**
     * 当前会话有效智能体 ID。有值时按该智能体已发布版本的 skills_custom / skills
     * 过滤「平台技能」列表；个人技能始终全量展示。
     */
    agentId?: string | null
    /** 窄屏级联浮层宽度 */
    compact?: boolean
    /** 移动端底部抽屉内铺满宽度 */
    fullWidth?: boolean
  }>(),
  { attachedSkillIds: () => [], agentId: null, compact: false, fullWidth: false },
)

const emit = defineEmits<{
  (e: 'select', skill: SkillItem): void
}>()

const { showToast } = useToast()

const skillsList = ref<SkillItem[]>([])
const personalSkillsList = ref<SkillItem[]>([])
const activeScope = ref<'global' | 'personal'>('global')
const isLoadingSkillsList = ref(false)
const skillSearchQuery = ref('')
/** 当前智能体是否开启了自定义公共 Skills */
const skillsCustom = ref(false)
const allowedGlobalSkillIds = ref<string[] | null>(null)
const loadedOnce = ref(false)

const attachedIdSet = computed(() => new Set(props.attachedSkillIds))

const currentScopeSkills = computed(() =>
  activeScope.value === 'global' ? skillsList.value : personalSkillsList.value,
)

const filteredSkillsList = computed(() => {
  const query = skillSearchQuery.value.trim().toLowerCase()
  if (!query) return currentScopeSkills.value
  return currentScopeSkills.value.filter(
    (s) =>
      s.name?.toLowerCase().includes(query) ||
      s.id?.toLowerCase().includes(query) ||
      s.description?.toLowerCase().includes(query) ||
      s.path?.toLowerCase().includes(query),
  )
})

const skillInitial = (skill: SkillItem) => {
  const raw = (skill.name || skill.id || 'S').trim()
  return raw.charAt(0).toUpperCase()
}

const skillAccentClass = (skill: SkillItem, index: number) => {
  if (attachedIdSet.value.has(skill.id)) {
    return 'bg-gray-200 dark:bg-gray-700 text-gray-500'
  }
  if (skill.scope === 'personal') {
    return 'bg-emerald-500 text-white'
  }
  const accents = [
    'bg-teal-500 text-white',
    'bg-lime-500 text-white',
    'bg-violet-500 text-white',
    'bg-green-500 text-white',
    'bg-sky-500 text-white',
  ]
  return accents[index % accents.length]
}

const resolveAgentSkillFilter = async (agentId: string | null | undefined) => {
  skillsCustom.value = false
  allowedGlobalSkillIds.value = null
  const id = String(agentId || '').trim()
  if (!id) return
  try {
    const res = await axios.get(`/api/portal/agents/${encodeURIComponent(id)}/active-config`)
    const cfg = res.data || {}
    if (cfg.skills_custom) {
      skillsCustom.value = true
      allowedGlobalSkillIds.value = Array.isArray(cfg.skills)
        ? cfg.skills.map((s: any) => String(s || '').trim()).filter(Boolean)
        : []
    }
  } catch (err) {
    console.warn('加载智能体 Skills 配置失败，回退为全量公共技能', err)
  }
}

const loadSkillsList = async () => {
  skillsList.value = []
  personalSkillsList.value = []
  isLoadingSkillsList.value = true
  try {
    await resolveAgentSkillFilter(props.agentId)
    const [globalRes, personalRes] = await Promise.allSettled([
      axios.get('/api/portal/skills'),
      axios.get('/api/portal/skills/personal'),
    ])
    if (globalRes.status === 'fulfilled' && globalRes.value.data?.status === 'success') {
      let list = (globalRes.value.data.data || [])
        .map((s: any) => ({ ...s, scope: 'global' as const }))
        .filter((s: any) => s.enabled !== 'false')
      if (skillsCustom.value && allowedGlobalSkillIds.value) {
        const allow = new Set(allowedGlobalSkillIds.value)
        list = list.filter((s: SkillItem) => allow.has(s.id))
      }
      skillsList.value = list
    }
    if (personalRes.status === 'fulfilled' && personalRes.value.data?.status === 'success') {
      personalSkillsList.value = (personalRes.value.data.data || [])
        .map((s: any) => ({ ...s, scope: 'personal' as const }))
        .filter((s: any) => s.enabled !== 'false')
    }
    loadedOnce.value = true
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
}

watch(
  () => props.agentId,
  () => {
    if (loadedOnce.value) void loadSkillsList()
  },
)

defineExpose({
  reload: loadSkillsList,
  resetSearch: () => {
    skillSearchQuery.value = ''
  },
})

void loadSkillsList()
</script>

<template>
  <div
    class="flex flex-col bg-white dark:bg-gray-800 overflow-hidden border border-gray-200 dark:border-gray-700"
    :class="fullWidth
      ? 'w-full max-h-[min(70vh,28rem)] rounded-none border-x-0 border-b-0 shadow-none'
      : compact
        ? 'w-[min(20rem,calc(100vw-1.5rem))] max-h-[min(60vh,24rem)] rounded-xl shadow-xl'
        : 'w-80 max-h-[min(60vh,24rem)] rounded-xl shadow-xl'"
    role="menu"
    aria-label="技能中心"
  >
    <div class="p-2.5 pb-1.5 shrink-0 space-y-2">
      <div class="relative">
        <span class="absolute inset-y-0 left-0 pl-2.5 flex items-center pointer-events-none">
          <svg class="h-3.5 w-3.5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
        </span>
        <input
          v-model="skillSearchQuery"
          type="search"
          placeholder="搜索技能"
          class="w-full pl-8 pr-3 py-1.5 bg-gray-100 dark:bg-gray-900/80 border-0 rounded-lg focus:ring-2 focus:ring-primary/40 focus:outline-none text-xs text-gray-800 dark:text-gray-100 placeholder:text-gray-400"
          @click.stop
          @keydown.stop
        />
      </div>

      <div class="flex items-center gap-1 rounded-lg bg-gray-50 dark:bg-gray-900/50 p-0.5">
        <button
          type="button"
          class="flex-1 py-1 text-center text-[11px] font-semibold rounded-md transition-colors"
          :class="activeScope === 'global'
            ? 'bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 shadow-sm'
            : 'text-gray-500 hover:text-gray-700 dark:text-gray-400'"
          @click.stop="activeScope = 'global'"
        >
          平台
          <span class="ml-0.5 text-[9px] font-normal text-gray-400">{{ skillsList.length }}</span>
        </button>
        <button
          type="button"
          class="flex-1 py-1 text-center text-[11px] font-semibold rounded-md transition-colors"
          :class="activeScope === 'personal'
            ? 'bg-white dark:bg-gray-700 text-emerald-700 dark:text-emerald-300 shadow-sm'
            : 'text-gray-500 hover:text-gray-700 dark:text-gray-400'"
          @click.stop="activeScope = 'personal'"
        >
          我的
          <span class="ml-0.5 text-[9px] font-normal text-gray-400">{{ personalSkillsList.length }}</span>
        </button>
      </div>

      <p
        v-if="skillsCustom && activeScope === 'global'"
        class="px-1 text-[10px] text-amber-700 dark:text-amber-300 leading-snug"
      >
        当前智能体仅展示已配置的 {{ skillsList.length }} 个公共技能
      </p>
    </div>

    <div class="flex-1 min-h-0 max-h-64 overflow-y-auto overscroll-y-contain px-1.5 pb-1 custom-scrollbar">
      <div v-if="isLoadingSkillsList" class="flex flex-col items-center justify-center py-8 opacity-50">
        <div class="w-5 h-5 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        <span class="text-[10px] font-medium text-gray-400 mt-2">加载中...</span>
      </div>

      <div v-else-if="filteredSkillsList.length === 0" class="text-center py-8 px-3">
        <p class="text-xs text-gray-400 font-medium">未发现可用技能</p>
        <p class="text-[10px] text-gray-400/80 mt-1.5 leading-relaxed">
          <template v-if="skillsCustom && activeScope === 'global'">
            可切换到「我的」查看个人技能
          </template>
          <template v-else-if="activeScope === 'personal'">
            前往
            <a
              class="text-emerald-600 hover:underline font-semibold"
              href="/dashboard/personal?tab=skills"
              target="_blank"
              rel="noopener noreferrer"
              @click.stop
            >个人中心 · 我的技能</a>
            新建 / 导入
          </template>
          <template v-else>
            可切换到「我的」，或前往个人中心创建个人技能
          </template>
        </p>
      </div>

      <button
        v-for="(skill, index) in filteredSkillsList"
        :key="skill.id"
        type="button"
        class="w-full flex items-start gap-2.5 px-2 py-2 rounded-lg text-left transition-colors"
        :class="attachedIdSet.has(skill.id)
          ? 'opacity-60 cursor-default'
          : 'hover:bg-gray-100 dark:hover:bg-gray-700/70 cursor-pointer'"
        :disabled="attachedIdSet.has(skill.id)"
        @click.stop="mountSkill(skill)"
      >
        <div
          class="w-7 h-7 rounded-full flex items-center justify-center text-[11px] font-bold flex-shrink-0 mt-0.5"
          :class="skillAccentClass(skill, index)"
        >
          {{ skillInitial(skill) }}
        </div>
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-1.5 min-w-0">
            <span class="text-xs font-semibold text-gray-900 dark:text-gray-100 truncate">
              {{ skill.name || skill.id }}
            </span>
            <span
              v-if="skill.scope === 'personal'"
              class="shrink-0 px-1 py-px text-[8px] font-semibold rounded bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400"
            >个人</span>
            <span
              v-if="attachedIdSet.has(skill.id)"
              class="shrink-0 text-[9px] text-gray-400 font-medium"
            >已挂载</span>
          </div>
          <p class="text-[10px] text-gray-400 dark:text-gray-500 mt-0.5 truncate leading-snug">
            {{ skill.description || '暂无描述' }}
          </p>
        </div>
      </button>
    </div>

    <!-- 桌面端保留「管理技能」；移动端底部抽屉不展示 -->
    <div v-if="!fullWidth" class="shrink-0 border-t border-gray-100 dark:border-gray-700/80 py-1">
      <a
        href="/dashboard/personal?tab=skills"
        target="_blank"
        rel="noopener noreferrer"
        class="w-full flex items-center gap-2.5 px-3 py-2 text-xs text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700/70 transition-colors"
        @click.stop
      >
        <svg class="w-4 h-4 text-gray-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.75">
          <path stroke-linecap="round" stroke-linejoin="round" d="M11.42 15.17 17.25 21A2.652 2.652 0 0 0 21 17.25l-5.877-5.877M11.42 15.17l2.496-3.03c.317-.384.74-.626 1.208-.766M11.42 15.17l-4.655 5.653a2.548 2.548 0 1 1-3.586-3.586l6.837-5.63m5.108-.233c.55-.164 1.163-.188 1.743-.14a4.5 4.5 0 0 0 4.486-6.336l-3.276 3.277a3.004 3.004 0 0 1-2.25-2.25l3.276-3.276a4.5 4.5 0 0 0-6.336 4.486c.091 1.076-.071 2.264-.904 2.95l-.102.085m-1.745 1.437L5.909 7.5H4.5L2.25 3.75l1.5-1.5L7.5 4.5v1.409l4.26 4.26m-1.745 1.437 1.745-1.437m6.615 8.206L15.75 15.75M4.867 19.125h.008v.008h-.008v-.008Z" />
        </svg>
        <span class="font-medium">管理技能</span>
      </a>
    </div>
  </div>
</template>
