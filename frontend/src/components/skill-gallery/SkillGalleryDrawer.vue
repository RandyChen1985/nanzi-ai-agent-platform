<script setup lang="ts">
/**
 * 技能详情抽屉。
 *
 * 布局对齐 skills.sh 的技能页：标题 + 标签徽标、SUMMARY、能力要点、说明正文、
 * INSTALLATION（Prompt / Command 双页签）、来源元数据。
 *
 * 三处刻意的克制：
 * 1. 正文是**我们自己撰写的介绍**，不内嵌第三方 SKILL.md 原文——那些材料多数
 *    是 Proprietary 许可，复制到服务之外属于违规。来源用外链给出，用户自己去看。
 * 2. 「安装」是复制提示词，不是下载安装包。提示词里已包含非交互参数与失败兜底，
 *    用户不需要理解 npx 的细节。
 * 3. 管理员多一个「安装到平台」入口，普通用户看不到（后端权限才是真正闸门，
 *    这里只是不给无权限的人一个必然失败的按钮）。
 */
import { computed, ref, watch } from 'vue'

import { fetchSkillGalleryDetail } from '@/api/skillGallery'
import { useToast } from '@/composables/useToast'
import { copyToClipboard } from '@/utils/clipboard'
import type { SkillGalleryDetail } from '@/types/skillGallery'

const props = defineProps<{
  open: boolean
  skillId: string
  canInstallPlatform: boolean
}>()

const emit = defineEmits<{ close: [] }>()

const { showToast } = useToast()

const detail = ref<SkillGalleryDetail | null>(null)
const loading = ref(false)
const error = ref('')
const installTab = ref<'prompt' | 'command'>('prompt')

const installText = computed(() => {
  if (!detail.value) return ''
  return installTab.value === 'prompt' ? detail.value.install_prompt : detail.value.install_command
})

const introHtml = computed(() => renderMarkdown(detail.value?.intro || ''))

/** 已安装徽标文案：只说明「库里已有同名技能」，不声称是最新版本。 */
const installedText = computed(() => {
  const scope = detail.value?.installed
  if (scope === 'personal') return '已在你的个人技能库'
  if (scope === 'global') return '平台技能库已收录'
  return ''
})

/**
 * 极简 Markdown 渲染：只支持标题、列表、粗体、行内代码。
 *
 * 不引第三方解析器的原因：介绍正文是我们自己按固定几种语法写的，
 * 引一个解析器要多一份 XSS 面（v-html）与构建体积，收益不成正比。
 * 所有文本先做 HTML 转义再拼标签，正文里出现 `<script>` 也只会被当成文字。
 */
function renderMarkdown(source: string): string {
  if (!source) return ''
  const escaped = source
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')

  const blocks = escaped.split(/\n{2,}/)
  return blocks
    .map((block) => {
      const lines = block.split('\n')
      if (/^### /.test(lines[0] || '')) {
        return `<h3>${lines[0].slice(4)}</h3>${renderParagraph(lines.slice(1))}`
      }
      if (/^## /.test(lines[0] || '')) {
        return `<h3>${lines[0].slice(3)}</h3>${renderParagraph(lines.slice(1))}`
      }
      if (/^# /.test(lines[0] || '')) {
        return `<h2>${lines[0].slice(2)}</h2>${renderParagraph(lines.slice(1))}`
      }
      if (lines.every((line) => /^[-*] /.test(line))) {
        const items = lines.map((line) => `<li>${inline(line.slice(2))}</li>`).join('')
        return `<ul>${items}</ul>`
      }
      return renderParagraph(lines)
    })
    .join('')
}

function inline(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
}

function renderParagraph(lines: string[]): string {
  const kept = lines.filter((line) => line.trim())
  if (!kept.length) return ''
  return `<p>${kept.map((line) => inline(line)).join('<br>')}</p>`
}

async function loadDetail(skillId: string) {
  if (!skillId) return
  loading.value = true
  error.value = ''
  try {
    detail.value = await fetchSkillGalleryDetail(skillId)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '读取技能详情失败'
    detail.value = null
  } finally {
    loading.value = false
  }
}

async function copy(text: string, successText: string) {
  if (!text) {
    showToast('没有可复制的内容', 'warning')
    return
  }
  const copied = await copyToClipboard(text)
  showToast(copied ? successText : '复制失败，请手动选中复制', copied ? 'success' : 'error')
}

function copyPersonal() {
  if (!detail.value) return
  copy(detail.value.install_prompt, '已复制「安装到个人技能库」提示词')
}

function copyPlatform() {
  if (!detail.value) return
  const text = detail.value.install_prompt_global || detail.value.install_prompt
  copy(text, '已复制「安装到平台技能库」提示词')
}

watch(
  () => [props.open, props.skillId] as const,
  ([open, skillId]) => {
    if (!open) return
    installTab.value = 'prompt'
    // 同一个技能反复打开时不必重拉，但切到别的技能要重新取
    if (detail.value?.id !== skillId) loadDetail(skillId)
  },
  { immediate: true },
)
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-40 bg-gray-900/30"
      aria-hidden="true"
      @click="emit('close')"
    />
    <aside
      v-if="open"
      class="fixed right-0 top-0 z-50 flex h-full w-[min(560px,94vw)] flex-col bg-white shadow-2xl dark:bg-gray-900"
      role="dialog"
      aria-modal="true"
      :aria-label="detail ? `${detail.name} 详情` : '技能详情'"
    >
      <div class="relative border-b border-gray-100 px-5 pb-3.5 pt-4 dark:border-gray-800">
        <button
          type="button"
          class="absolute right-4 top-3.5 rounded p-1 text-xl leading-none text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
          aria-label="关闭"
          @click="emit('close')"
        >
          ×
        </button>
        <div v-if="detail" class="flex items-start gap-3 pr-8">
          <div
            class="flex h-11 w-11 flex-none items-center justify-center rounded-xl border border-gray-100 bg-gray-50 text-xl dark:border-gray-700 dark:bg-gray-800"
            aria-hidden="true"
          >
            {{ detail.icon }}
          </div>
          <div class="min-w-0">
            <h2 class="text-lg font-semibold text-gray-900 dark:text-gray-100">{{ detail.name }}</h2>
            <p class="mt-0.5 truncate text-[11.5px] text-gray-400">
              {{ detail.id }} · v{{ detail.version }} · {{ detail.author }}
            </p>
            <div class="mt-1.5 flex flex-wrap gap-1">
              <span
                v-if="detail.official"
                class="rounded-md bg-primary/10 px-1.5 py-0.5 text-[10.5px] font-semibold text-primary"
              >
                官方
              </span>
              <span
                class="rounded-md bg-gray-100 px-1.5 py-0.5 text-[10.5px] font-semibold text-gray-600 dark:bg-gray-700 dark:text-gray-300"
              >
                {{ detail.category }}
              </span>
              <span
                v-for="tag in detail.tags"
                :key="tag"
                class="rounded-md bg-gray-100 px-1.5 py-0.5 text-[10.5px] font-semibold text-gray-600 dark:bg-gray-700 dark:text-gray-300"
              >
                {{ tag }}
              </span>
              <span
                v-if="detail.installed"
                class="rounded-md bg-emerald-50 px-1.5 py-0.5 text-[10.5px] font-semibold text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300"
              >
                {{ installedText }}
              </span>
            </div>
          </div>
        </div>
        <h2 v-else class="pr-8 text-lg font-semibold text-gray-900 dark:text-gray-100">技能详情</h2>
      </div>

      <div class="flex-1 overflow-auto px-5 py-4">
        <p v-if="loading" class="py-8 text-center text-sm text-gray-400">正在加载详情…</p>
        <p v-else-if="error" class="py-8 text-center text-sm text-red-600">{{ error }}</p>

        <template v-else-if="detail">
          <h3 class="mb-1.5 text-[11px] font-bold uppercase tracking-wider text-gray-400">Summary</h3>
          <p class="text-[13px] leading-relaxed text-gray-700 dark:text-gray-300">
            {{ detail.summary }}
          </p>

          <template v-if="detail.highlights.length">
            <h3 class="mb-1.5 mt-5 text-[11px] font-bold uppercase tracking-wider text-gray-400">
              这个技能能做什么
            </h3>
            <ul class="list-disc space-y-1 pl-5 text-[13px] leading-relaxed text-gray-700 dark:text-gray-300">
              <li v-for="(item, index) in detail.highlights" :key="index">{{ item }}</li>
            </ul>
          </template>

          <template v-if="introHtml">
            <h3 class="mb-1.5 mt-5 text-[11px] font-bold uppercase tracking-wider text-gray-400">
              技能说明
            </h3>
            <!-- 正文先做 HTML 转义再拼标签，见 renderMarkdown 注释 -->
            <!-- eslint-disable-next-line vue/no-v-html -->
            <div class="skill-gallery-intro" v-html="introHtml" />
          </template>

          <h3 class="mb-1.5 mt-5 text-[11px] font-bold uppercase tracking-wider text-gray-400">
        安装
          </h3>
          <div class="overflow-hidden rounded-xl border border-gray-200 dark:border-gray-700">
            <div
              class="flex items-center gap-1 border-b border-gray-200 bg-gray-50 px-2 py-1.5 dark:border-gray-700 dark:bg-gray-800"
            >
              <span class="mr-auto pl-1 text-[10.5px] font-bold uppercase tracking-wider text-gray-400">
                Installation
              </span>
              <button
                type="button"
                class="rounded-md px-2.5 py-1 text-xs font-semibold"
                :class="
                  installTab === 'prompt'
                    ? 'bg-white text-gray-900 shadow-sm dark:bg-gray-900 dark:text-gray-100'
                    : 'text-gray-500'
                "
                @click="installTab = 'prompt'"
              >
                Prompt
              </button>
              <button
                type="button"
                class="rounded-md px-2.5 py-1 text-xs font-semibold"
                :class="
                  installTab === 'command'
                    ? 'bg-white text-gray-900 shadow-sm dark:bg-gray-900 dark:text-gray-100'
                    : 'text-gray-500'
                "
                @click="installTab = 'command'"
              >
                Command
              </button>
            </div>
            <div class="relative px-3 py-3 pr-11">
              <pre
                class="whitespace-pre-wrap break-words font-mono text-[12px] leading-relaxed text-gray-800 dark:text-gray-200"
              >{{ installText }}</pre>
              <button
                type="button"
                class="absolute right-2 top-2 flex h-7 w-7 items-center justify-center rounded-md border border-gray-200 bg-white text-gray-500 hover:border-primary/40 hover:bg-primary/10 hover:text-primary dark:border-gray-600 dark:bg-gray-900"
                title="复制"
                aria-label="复制"
                @click="copy(installText, '已复制')"
              >
                ⧉
              </button>
            </div>
          </div>

          <p
            class="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2 text-[11.5px] leading-relaxed text-amber-800 dark:border-amber-900/40 dark:bg-amber-900/20 dark:text-amber-300"
          >
            <b>必须非交互执行</b>：命令里两个 <code class="rounded bg-amber-100 px-1 dark:bg-amber-900/40">-y</code>
            缺一不可（跳过 npx 装包确认 + 跳过 skills 安装确认），否则沙箱里会停在等待输入、把会话拖成僵尸进程。
            统一装到 <code class="rounded bg-amber-100 px-1 dark:bg-amber-900/40">/workspace/.skill-tmp</code>，
            路径可预测，导入完成后该目录可直接删除。
          </p>

          <h3 class="mb-1.5 mt-5 text-[11px] font-bold uppercase tracking-wider text-gray-400">来源</h3>
          <dl class="grid grid-cols-[76px_1fr] gap-x-3 gap-y-1.5 text-[12.5px]">
            <dt class="text-gray-400">作者</dt>
            <dd class="text-gray-700 dark:text-gray-300">{{ detail.author }}</dd>
            <dt class="text-gray-400">仓库</dt>
            <dd class="break-all">
              <a
                v-if="detail.repository_url"
                :href="detail.repository_url"
                target="_blank"
                rel="noreferrer noopener"
                class="text-primary hover:underline"
              >
                {{ detail.repository || detail.repository_url }}
              </a>
              <span v-else class="text-gray-700 dark:text-gray-300">{{ detail.repository || '—' }}</span>
            </dd>
            <dt class="text-gray-400">介绍来源</dt>
            <dd class="break-all">
              <a
                v-if="detail.source_url"
                :href="detail.source_url"
                target="_blank"
                rel="noreferrer noopener"
                class="text-primary hover:underline"
              >
                {{ detail.source_url }}
              </a>
              <span v-else class="text-gray-700 dark:text-gray-300">—</span>
            </dd>
            <dt class="text-gray-400">许可</dt>
            <dd class="text-gray-700 dark:text-gray-300">
              {{ detail.license || '—' }}
              <span class="block text-[11px] text-gray-400">仅作索引介绍，未复制正文</span>
            </dd>
            <dt class="text-gray-400">版本</dt>
            <dd class="text-gray-700 dark:text-gray-300">{{ detail.version || '—' }}</dd>
          </dl>
        </template>
      </div>

      <div
        class="flex flex-wrap justify-end gap-2 border-t border-gray-100 bg-gray-50 px-5 py-3 dark:border-gray-800 dark:bg-gray-800"
      >
        <button
          v-if="canInstallPlatform"
          type="button"
          class="rounded-lg border border-gray-200 bg-white px-3.5 py-2 text-xs font-semibold text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-200"
          @click="copyPlatform"
        >
          复制提示词 · 安装到平台
        </button>
        <button
          type="button"
          class="rounded-lg bg-primary px-3.5 py-2 text-xs font-semibold text-white hover:bg-primary-hover"
          @click="copyPersonal"
        >
          {{ detail?.installed ? '复制提示词 · 重新安装到个人' : '复制提示词 · 安装到个人' }}
        </button>
      </div>
    </aside>
  </Teleport>
</template>

<style scoped>
/*
 * 介绍正文走 v-html，Tailwind 的 preflight 会把 h2/h3/ul 全重置掉，
 * 这里按正文的实际层级补回样式。
 */
.skill-gallery-intro {
  font-size: 13px;
  line-height: 1.8;
  color: rgb(55 65 81);
}
.skill-gallery-intro :deep(h2) {
  margin: 0 0 6px;
  font-size: 14px;
  font-weight: 600;
  color: rgb(17 24 39);
}
.skill-gallery-intro :deep(h3) {
  margin: 14px 0 6px;
  font-size: 13.5px;
  font-weight: 600;
  color: rgb(17 24 39);
}
.skill-gallery-intro :deep(p) {
  margin: 7px 0;
}
.skill-gallery-intro :deep(ul) {
  margin: 7px 0;
  padding-left: 18px;
  list-style: disc;
}
.skill-gallery-intro :deep(li) {
  margin: 3px 0;
}
.skill-gallery-intro :deep(strong) {
  font-weight: 600;
}
.skill-gallery-intro :deep(code) {
  border-radius: 4px;
  background: rgb(243 244 246);
  padding: 1px 5px;
  font-size: 12px;
}
.dark .skill-gallery-intro {
  color: rgb(209 213 219);
}
.dark .skill-gallery-intro :deep(h2),
.dark .skill-gallery-intro :deep(h3) {
  color: rgb(243 244 246);
}
.dark .skill-gallery-intro :deep(code) {
  background: rgb(55 65 81);
}
</style>
