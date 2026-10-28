<script setup lang="ts">
/**
 * 技能广场：只读目录。
 *
 * 卡片与详情只做介绍——第三方技能多为 Proprietary 许可（`anthropics/skills` 明令
 * 禁止把材料复制到服务之外），所以这里不内嵌 SKILL.md 正文，正文用外链指向来源。
 * 「安装」也不是下载文件，而是复制一段提示词给会话里的智能体：它在沙箱执行
 * `npx skills add ... -y -y`（两个 -y 缺一不可，否则挂起等输入）后，再用
 * `import_skill_from_sandbox` 收进技能库。
 */
import { computed, onMounted, ref } from 'vue'

import SkillGalleryDrawer from './SkillGalleryDrawer.vue'
import { fetchSkillGallery } from '@/api/skillGallery'
import { useToast } from '@/composables/useToast'
import { useUser } from '@/composables/useUser'
import { copyToClipboard } from '@/utils/clipboard'
import type { SkillGalleryCard } from '@/types/skillGallery'

const { showToast } = useToast()
const { hasPermission } = useUser()
// 管理员才有的入口：安装到平台全局技能库。判定口径与技能管理工作台一致。
const canInstallPlatform = computed(() => hasPermission('element:skills:admin'))

/**
 * 外部技能社区。广场只放我们精选（并撰写过介绍）的那几个，全量列表交给 ModelScope——
 * 那边有上千个技能，我们既审不过来也不打算复制它们的正文。
 */
const EXTERNAL_SKILL_HUB_URL = 'https://modelscope.cn/skills'

const skills = ref<SkillGalleryCard[]>([])
const categories = ref<string[]>([])
const sourceNote = ref('')
const loading = ref(false)
const error = ref('')

const keyword = ref('')
const activeCategory = ref('全部')
const officialOnly = ref(false)

const activeSkillId = ref('')
const drawerOpen = ref(false)

const categoryChips = computed(() => ['全部', ...categories.value])

/** 已安装徽标文案。两份都有时后端已归一成 personal，这里只管显示。 */
function installedLabel(scope: string | null): string {
  if (scope === 'personal') return '已在你的技能库'
  if (scope === 'global') return '平台已收录'
  return ''
}

const visibleSkills = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  return skills.value.filter((skill) => {
    if (activeCategory.value !== '全部' && skill.category !== activeCategory.value) return false
    if (officialOnly.value && !skill.official) return false
    if (!query) return true
    const haystack = [skill.name, skill.summary, skill.id, ...(skill.tags || [])]
      .join(' ')
      .toLowerCase()
    return haystack.includes(query)
  })
})

async function reloadAll() {
  loading.value = true
  error.value = ''
  try {
    const data = await fetchSkillGallery()
    skills.value = data.skills
    categories.value = data.categories
    sourceNote.value = data.source_note
  } catch (err) {
    error.value = err instanceof Error ? err.message : '读取技能广场失败'
  } finally {
    loading.value = false
  }
}

function openDetail(skill: SkillGalleryCard) {
  activeSkillId.value = skill.id
  drawerOpen.value = true
}

/**
 * 卡片上的快捷复制：直接给个人技能库那份提示词。
 *
 * 列表接口已经把 install_prompt 一起返回了，所以这里不需要再请求详情——
 * 点一下就能复制走，是这条路径存在的主要理由。
 */
async function copyPrompt(skill: SkillGalleryCard) {
  const text = skill.install_prompt || skill.install_command
  if (!text) {
    showToast('这个技能没有配置安装提示词', 'warning')
    return
  }
  const copied = await copyToClipboard(text)
  showToast(
    copied ? '已复制安装提示词，粘贴到对话里即可' : '复制失败，请在详情里手动选中复制',
    copied ? 'success' : 'error',
  )
}

function closeDetail() {
  drawerOpen.value = false
}

defineExpose({ reloadAll })

onMounted(reloadAll)
</script>

<template>
  <section class="space-y-3">
    <!-- 标题由工作台的 tab 承担：这里再来一个「技能广场」就是同一句话写两遍 -->
    <div class="flex flex-wrap items-center justify-between gap-3">
      <p class="text-xs text-gray-500 dark:text-gray-400">
        精选可直接抄用的智能体技能，复制提示词到对话里即可安装。
        <!-- 广场里只有我们精选的那几个，想看全量往外走：模型社区那边有更长的列表 -->
        <a
          :href="EXTERNAL_SKILL_HUB_URL"
          target="_blank"
          rel="noreferrer noopener"
          class="ml-1 font-semibold text-primary hover:underline"
          aria-label="更多技能（在新窗口打开 ModelScope 技能广场）"
        >
          更多技能 ↗
        </a>
      </p>
      <div class="flex flex-wrap gap-2 text-[11.5px] font-semibold">
        <span class="rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 dark:border-gray-700 dark:bg-gray-800">
          精选 <b class="text-primary">{{ visibleSkills.length }}</b>
        </span>
        <span class="rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 dark:border-gray-700 dark:bg-gray-800">
          分类 <b class="text-primary">{{ categories.length }}</b>
        </span>
      </div>
    </div>

    <div
      class="flex flex-wrap items-center gap-2 rounded-xl border border-gray-100 bg-white p-3 dark:border-gray-700 dark:bg-gray-800"
    >
      <input
        v-model="keyword"
        type="search"
        class="min-w-[200px] flex-1 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
        placeholder="搜索技能名 / 用途 / 标签，例如 PDF、表格、MCP"
      />
      <div class="flex flex-wrap gap-1.5">
        <button
          v-for="category in categoryChips"
          :key="category"
          type="button"
          class="rounded-full border px-3 py-1 text-xs font-medium transition-colors"
          :class="
            activeCategory === category
              ? 'border-primary bg-primary text-white'
              : 'border-gray-200 bg-white text-gray-600 hover:border-primary/40 hover:text-primary dark:border-gray-600 dark:bg-gray-900 dark:text-gray-300'
          "
          @click="activeCategory = category"
        >
          {{ category }}
        </button>
      </div>
      <label class="flex items-center gap-1.5 text-xs text-gray-600 dark:text-gray-300">
        <input v-model="officialOnly" type="checkbox" class="accent-primary" />
        仅看官方
      </label>
    </div>

    <div
      v-if="error"
      class="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900/40 dark:bg-red-900/20 dark:text-red-300"
    >
      {{ error }}
    </div>

    <div v-if="loading && !skills.length" class="py-10 text-center text-sm text-gray-400">
      正在加载技能广场…
    </div>

    <div v-else-if="!visibleSkills.length" class="py-10 text-center text-sm text-gray-400">
      没有匹配的技能，换个关键词试试
    </div>

    <div v-else class="grid grid-cols-1 gap-3.5 sm:grid-cols-2 xl:grid-cols-3">
      <article
        v-for="skill in visibleSkills"
        :key="skill.id"
        class="flex cursor-pointer flex-col rounded-2xl border border-gray-100 bg-white p-4 transition-all hover:-translate-y-px hover:border-primary/30 hover:shadow-md dark:border-gray-700 dark:bg-gray-800"
        @click="openDetail(skill)"
      >
        <div class="flex items-start gap-3">
          <div
            class="flex h-10 w-10 flex-none items-center justify-center rounded-xl border border-gray-100 bg-gray-50 text-lg dark:border-gray-700 dark:bg-gray-900"
            aria-hidden="true"
          >
            {{ skill.icon }}
          </div>
          <div class="min-w-0 flex-1">
            <h3 class="truncate text-sm font-semibold text-gray-900 dark:text-gray-100">
              {{ skill.name }}
            </h3>
            <p class="mt-0.5 truncate text-[11.5px] text-gray-400">
              {{ skill.author }} · v{{ skill.version }}
            </p>
          </div>
        </div>

        <div class="mt-2 flex flex-wrap gap-1">
          <span
            v-if="skill.official"
            class="rounded-md bg-primary/10 px-1.5 py-0.5 text-[10.5px] font-semibold text-primary"
          >
            官方
          </span>
          <span
            class="rounded-md bg-gray-100 px-1.5 py-0.5 text-[10.5px] font-semibold text-gray-600 dark:bg-gray-700 dark:text-gray-300"
          >
            {{ skill.category }}
          </span>
          <span
            v-if="skill.installed"
            class="rounded-md bg-emerald-50 px-1.5 py-0.5 text-[10.5px] font-semibold text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300"
          >
            {{ installedLabel(skill.installed) }}
          </span>
        </div>

        <p class="mt-2.5 line-clamp-3 text-[12.5px] leading-relaxed text-gray-600 dark:text-gray-300">
          {{ skill.summary }}
        </p>

        <div class="mt-2.5 flex flex-wrap gap-1">
          <span
            v-for="tag in skill.tags"
            :key="tag"
            class="rounded-md border border-gray-100 bg-gray-50 px-1.5 py-0.5 text-[10.5px] text-gray-500 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-400"
          >
            {{ tag }}
          </span>
        </div>

        <div
          class="mt-auto flex items-center justify-between gap-2 border-t border-dashed border-gray-100 pt-3 dark:border-gray-700"
        >
          <button
            type="button"
            class="text-xs font-semibold text-primary hover:text-primary-hover"
            @click.stop="openDetail(skill)"
          >
            查看详情 →
          </button>
          <button
            type="button"
            class="rounded-lg bg-primary/10 px-3 py-1.5 text-xs font-semibold text-primary hover:bg-primary/15"
            @click.stop="copyPrompt(skill)"
          >
            {{ skill.installed ? '重新复制提示词' : '复制安装提示词' }}
          </button>
        </div>
      </article>
    </div>

    <p v-if="sourceNote" class="pt-1 text-[11px] leading-relaxed text-gray-400">
      {{ sourceNote }}
    </p>

    <SkillGalleryDrawer
      :open="drawerOpen"
      :skill-id="activeSkillId"
      :can-install-platform="canInstallPlatform"
      @close="closeDetail"
    />
  </section>
</template>
