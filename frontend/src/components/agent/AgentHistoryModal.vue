<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import Modal from '../Modal.vue'
import { agentApi, type AIAgent, type AgentExecutionHistory } from '../../api/agent'
import { useToast } from '@/composables/useToast'
import { useUser } from '@/composables/useUser'
import { renderMarkdown } from '@/utils/markdown'
import { copyToClipboard } from '@/utils/clipboard'

const props = defineProps<{
  show: boolean
  agent: AIAgent | null
}>()

const emit = defineEmits<{
  close: []
  'update:show': [value: boolean]
}>()

const { showToast } = useToast()
const { isAdmin } = useUser()

/**
 * 管理员看跨用户的执行历史，普通用户只看自己的。
 *
 * 该弹窗按 `agent_id` 过滤、没有用户名维度：此前 admin 是靠 `/chat/history` 的 admin
 * 旁路顺带看到所有用户的执行记录。旁路删除后必须显式切到审计端点（`require_admin`），
 * 否则 admin 的智能体排障视角会静默退化为「只看自己跑过的那些」。
 */
const historyApi = computed(() =>
  isAdmin.value ? agentApi.getAdminChatHistory : agentApi.getChatHistory,
)

const executions = ref<AgentExecutionHistory[]>([])
const loading = ref(false)
const page = ref(1)
const pageSize = 10
const keyword = ref('')
const hasMore = ref(true)
const selectedId = ref<number | string | null>(null)
/** AI 回复展示：默认渲染 Markdown，可切到源码 */
const replyViewMode = ref<'render' | 'source'>('render')

const selectedItem = computed(
  () => executions.value.find((e) => e.id === selectedId.value) || null,
)

const title = computed(
  () => `对话历史 - ${props.agent?.display_name || props.agent?.name || ''}`,
)

const replyHtml = computed(() => {
  const text = selectedItem.value?.summary
  if (!text) return ''
  try {
    return renderMarkdown(text)
  } catch (e) {
    console.error('Markdown render failed', e)
    return `<p class="text-red-500">Markdown 渲染失败</p>`
  }
})

const truncateQuery = (text?: string, max = 72) => {
  const value = (text || '').replace(/\s+/g, ' ').trim()
  if (!value) return '(空提问)'
  return value.length > max ? `${value.slice(0, max)}…` : value
}

const formatListTime = (iso?: string) => {
  if (!iso) return '-'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '-'
  const diffMs = Date.now() - date.getTime()
  const dayMs = 24 * 60 * 60 * 1000
  if (diffMs >= 0 && diffMs < dayMs) {
    return date.toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
  }
  return date.toLocaleString('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const close = () => {
  emit('update:show', false)
  emit('close')
}

const fetchHistory = async (loadMore = false) => {
  if (!props.agent) return
  loading.value = true
  try {
    const res = await historyApi.value({
      agent_id: props.agent.id,
      page: page.value,
      page_size: pageSize,
      keyword: keyword.value || undefined,
    })
    const items = res.data.data.items || []
    if (loadMore) {
      executions.value = [...executions.value, ...items]
    } else {
      executions.value = items
      selectedId.value = items[0]?.id ?? null
    }
    hasMore.value = items.length === pageSize
    if (
      selectedId.value != null &&
      !executions.value.some((e) => e.id === selectedId.value)
    ) {
      selectedId.value = executions.value[0]?.id ?? null
    }
  } catch (error) {
    console.error('Failed to fetch executions', error)
    showToast('获取历史记录失败', 'error')
  } finally {
    loading.value = false
  }
}

const resetAndLoad = async () => {
  page.value = 1
  keyword.value = ''
  hasMore.value = true
  executions.value = []
  selectedId.value = null
  await fetchHistory()
}

const handleSearch = () => {
  page.value = 1
  hasMore.value = true
  fetchHistory()
}

const loadMore = () => {
  if (!hasMore.value || loading.value) return
  page.value++
  fetchHistory(true)
}

const copyText = async (text: string, label = '内容') => {
  if (!text) {
    showToast('内容为空', 'warning')
    return
  }
  const ok = await copyToClipboard(text)
  if (ok) {
    showToast(`${label}已复制`, 'success')
  } else {
    showToast('复制失败，请手动复制', 'error')
  }
}

watch(
  () => [props.show, props.agent?.id] as const,
  ([open]) => {
    if (open && props.agent) {
      replyViewMode.value = 'render'
      void resetAndLoad()
    }
  },
)

watch(selectedId, () => {
  replyViewMode.value = 'render'
})
</script>

<template>
  <Modal
    v-if="show"
    :title="title"
    size="max-w-6xl"
    @close="close"
  >
    <div class="-m-6 flex flex-col h-[min(78vh,740px)]">
      <div class="shrink-0 px-5 py-3 border-b border-gray-100 bg-white flex items-center gap-2">
        <div class="relative flex-1 min-w-0">
          <span class="absolute inset-y-0 left-0 pl-3 flex items-center text-gray-400 pointer-events-none">
            <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
          </span>
          <input
            v-model="keyword"
            type="search"
            placeholder="搜索对话内容关键词..."
            class="block w-full pl-10 pr-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none bg-gray-50/80"
            @keyup.enter="handleSearch"
          />
        </div>
        <button
          type="button"
          class="px-4 py-2 bg-primary text-white text-sm rounded-lg hover:bg-primary-dark transition-colors font-medium shrink-0"
          @click="handleSearch"
        >
          搜索
        </button>
      </div>

      <div class="flex-1 min-h-0 flex flex-col sm:flex-row">
        <aside class="sm:w-[340px] sm:max-w-[38%] shrink-0 border-b sm:border-b-0 sm:border-r border-gray-100 bg-gray-50/60 flex flex-col min-h-0 max-h-[38%] sm:max-h-none">
          <div class="px-4 py-2.5 text-[11px] font-bold text-gray-400 uppercase tracking-wide border-b border-gray-100/80 flex items-center justify-between shrink-0">
            <span>会话列表</span>
            <span class="font-mono font-medium normal-case tracking-normal text-gray-400">{{ executions.length }} 条</span>
          </div>
          <div class="flex-1 overflow-y-auto custom-scrollbar min-h-0">
            <div v-if="loading && page === 1" class="py-16 text-center text-gray-400 text-sm">
              加载中...
            </div>
            <div v-else-if="!loading && executions.length === 0" class="py-16 px-6 text-center">
              <div class="text-2xl opacity-40 mb-2">💬</div>
              <p class="text-sm text-gray-400 font-medium">暂无对话记录</p>
              <p class="text-[11px] text-gray-400 mt-1">试试调整关键词或稍后再查看</p>
            </div>
            <button
              v-for="exec in executions"
              :key="exec.id"
              type="button"
              class="w-full text-left px-3.5 py-3 border-b border-gray-100/80 transition-colors"
              :class="selectedId === exec.id
                ? 'bg-white border-l-2 border-l-primary shadow-[inset_0_0_0_1px_rgba(0,0,0,0.02)]'
                : 'hover:bg-white/80 border-l-2 border-l-transparent'"
              @click="selectedId = exec.id"
            >
              <div class="flex items-start justify-between gap-2 mb-1.5">
                <div class="flex items-center gap-1.5 min-w-0">
                  <span
                    class="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold shrink-0"
                    :class="selectedId === exec.id ? 'bg-primary/10 text-primary' : 'bg-gray-200/80 text-gray-500'"
                  >
                    {{ exec.username ? exec.username.charAt(0).toUpperCase() : 'U' }}
                  </span>
                  <span class="text-[11px] font-semibold text-gray-700 truncate">{{ exec.username || 'Unknown' }}</span>
                </div>
                <span class="text-[10px] font-mono text-gray-400 whitespace-nowrap shrink-0">{{ formatListTime(exec.created_at) }}</span>
              </div>
              <p
                class="text-[13px] leading-snug line-clamp-2"
                :class="selectedId === exec.id ? 'text-gray-900 font-medium' : 'text-gray-600'"
              >
                {{ truncateQuery(exec.query) }}
              </p>
              <div class="mt-2 flex items-center gap-1.5 flex-wrap">
                <span
                  class="inline-flex px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wide border"
                  :class="exec.status === 'success'
                    ? 'bg-emerald-50 text-emerald-700 border-emerald-100'
                    : 'bg-red-50 text-red-600 border-red-100'"
                >{{ exec.status }}</span>
                <span
                  v-if="exec.agent_version"
                  class="inline-flex px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 text-[9px] font-bold border border-gray-200"
                >{{ exec.agent_version }}</span>
                <span
                  v-if="exec.execution_time_ms"
                  class="text-[10px] font-mono text-gray-400"
                >{{ (exec.execution_time_ms / 1000).toFixed(1) }}s</span>
              </div>
            </button>
          </div>
          <div v-if="hasMore" class="shrink-0 p-2.5 border-t border-gray-100 bg-white/80">
            <button
              type="button"
              class="w-full py-2 rounded-lg text-xs font-medium text-gray-500 hover:text-primary hover:bg-primary/5 border border-gray-200 transition-all disabled:opacity-50"
              :disabled="loading"
              @click="loadMore"
            >
              {{ loading ? '加载中...' : '加载更多' }}
            </button>
          </div>
        </aside>

        <section class="flex-1 min-w-0 min-h-0 flex flex-col bg-white">
          <template v-if="selectedItem">
            <div class="shrink-0 px-5 py-3 border-b border-gray-100 flex flex-wrap items-center gap-2 justify-between">
              <div class="flex items-center gap-2 min-w-0 flex-wrap">
                <span class="text-xs font-mono text-gray-500">{{ new Date(selectedItem.created_at).toLocaleString() }}</span>
                <span
                  v-if="selectedItem.agent_version"
                  class="inline-flex px-2 py-0.5 rounded bg-gray-100 text-gray-600 text-[10px] font-bold border border-gray-200"
                >{{ selectedItem.agent_version }}</span>
                <span
                  v-if="selectedItem.model_id"
                  class="inline-flex px-2 py-0.5 rounded bg-slate-50 text-slate-600 text-[10px] font-bold border border-slate-200 truncate max-w-[180px]"
                  :title="selectedItem.model_id"
                >{{ selectedItem.model_id }}</span>
                <span
                  class="inline-flex px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide border"
                  :class="selectedItem.status === 'success'
                    ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                    : 'bg-red-50 text-red-700 border-red-200'"
                >{{ selectedItem.status }}</span>
              </div>
              <div class="text-[11px] font-mono text-gray-400 shrink-0">
                {{
                  selectedItem.execution_time_ms
                    ? (selectedItem.execution_time_ms / 1000).toFixed(2) + 's'
                    : '-'
                }}
              </div>
            </div>

            <div class="flex-1 overflow-y-auto custom-scrollbar min-h-0 p-5 space-y-4">
              <div class="rounded-xl border border-gray-200 bg-gray-50/80 p-4 relative group">
                <div class="flex items-center justify-between gap-2 mb-2">
                  <div class="flex items-center gap-2">
                    <span class="w-6 h-6 rounded-full bg-blue-100 text-blue-600 flex items-center justify-center text-[10px] font-bold">
                      {{ selectedItem.username ? selectedItem.username.charAt(0).toUpperCase() : 'U' }}
                    </span>
                    <span class="text-xs font-bold text-gray-500">用户提问</span>
                    <span class="text-[11px] text-gray-400">{{ selectedItem.username || 'Unknown' }}</span>
                  </div>
                  <button
                    type="button"
                    class="text-[11px] text-gray-400 hover:text-primary font-medium opacity-70 group-hover:opacity-100 transition-opacity"
                    @click="copyText(selectedItem.query || '', '用户提问')"
                  >复制</button>
                </div>
                <p class="text-sm text-gray-800 whitespace-pre-wrap break-words leading-relaxed">{{ selectedItem.query || '(空)' }}</p>
              </div>

              <div class="rounded-xl border border-blue-100 bg-blue-50/40 p-4 relative group">
                <div class="flex items-center justify-between gap-2 mb-2">
                  <div class="flex items-center gap-2 min-w-0">
                    <span class="text-xs font-bold text-primary/70">AI 回复</span>
                    <div class="inline-flex rounded-md border border-blue-100 bg-white/80 p-0.5 text-[11px]">
                      <button
                        type="button"
                        class="px-2 py-0.5 rounded transition-colors"
                        :class="replyViewMode === 'render'
                          ? 'bg-primary text-white font-medium shadow-sm'
                          : 'text-gray-500 hover:text-primary'"
                        @click="replyViewMode = 'render'"
                      >渲染</button>
                      <button
                        type="button"
                        class="px-2 py-0.5 rounded transition-colors"
                        :class="replyViewMode === 'source'
                          ? 'bg-primary text-white font-medium shadow-sm'
                          : 'text-gray-500 hover:text-primary'"
                        @click="replyViewMode = 'source'"
                      >源码</button>
                    </div>
                  </div>
                  <button
                    type="button"
                    class="text-[11px] text-primary/50 hover:text-primary font-medium opacity-70 group-hover:opacity-100 transition-opacity shrink-0"
                    @click="copyText(selectedItem.summary || '', '回复内容')"
                  >复制</button>
                </div>
                <div
                  v-if="!selectedItem.summary"
                  class="text-sm text-gray-500"
                >(无响应内容)</div>
                <div
                  v-else-if="replyViewMode === 'render'"
                  class="markdown-body prose prose-sm max-w-none text-gray-800 break-words"
                  v-html="replyHtml"
                />
                <pre
                  v-else
                  class="text-sm text-gray-800 whitespace-pre-wrap break-words leading-relaxed font-mono bg-white/60 border border-blue-100/80 rounded-lg p-3 overflow-x-auto"
                >{{ selectedItem.summary }}</pre>
              </div>
            </div>
          </template>

          <div v-else class="flex-1 flex flex-col items-center justify-center text-gray-400 px-6">
            <div class="w-12 h-12 rounded-2xl bg-gray-50 border border-gray-100 flex items-center justify-center mb-3">
              <svg class="w-6 h-6 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
              </svg>
            </div>
            <p class="text-sm font-medium">从左侧选择一条会话</p>
            <p class="text-[11px] mt-1 text-center">列表只展示摘要，完整内容在这里阅读</p>
          </div>
        </section>
      </div>

      <div class="shrink-0 px-5 py-3 border-t border-gray-100 bg-gray-50/40 flex justify-end">
        <button
          type="button"
          class="px-4 py-2 bg-white border border-gray-200 rounded-lg text-gray-700 hover:bg-gray-50 font-medium text-sm"
          @click="close"
        >
          关闭
        </button>
      </div>
    </div>
  </Modal>
</template>

<style scoped>
.markdown-body {
  font-size: 14px;
  line-height: 1.65;
  overflow-wrap: break-word;
}
.markdown-body :deep(p) {
  margin-bottom: 0.85em;
}
.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  font-weight: 700;
  color: #111827;
  margin-top: 1.1em;
  margin-bottom: 0.5em;
  line-height: 1.35;
}
.markdown-body :deep(h1) { font-size: 1.35em; }
.markdown-body :deep(h2) { font-size: 1.2em; }
.markdown-body :deep(h3) { font-size: 1.05em; }
.markdown-body :deep(h1:first-child),
.markdown-body :deep(h2:first-child),
.markdown-body :deep(h3:first-child) {
  margin-top: 0;
}
.markdown-body :deep(ul) {
  list-style-type: disc;
  padding-left: 1.4em;
  margin-bottom: 0.85em;
}
.markdown-body :deep(ol) {
  list-style-type: decimal;
  padding-left: 1.4em;
  margin-bottom: 0.85em;
}
.markdown-body :deep(li) {
  margin-bottom: 0.35em;
}
.markdown-body :deep(strong) {
  font-weight: 700;
  color: #111827;
}
.markdown-body :deep(a) {
  color: #2563eb;
  text-decoration: underline;
}
.markdown-body :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  background-color: rgba(175, 184, 193, 0.22);
  padding: 0.15em 0.35em;
  border-radius: 4px;
  font-size: 85%;
  color: #b91c1c;
}
.markdown-body :deep(pre) {
  margin: 0.85em 0;
  padding: 0.85em 1em;
  background-color: #1e293b;
  border-radius: 8px;
  overflow: auto;
}
.markdown-body :deep(pre code) {
  background: transparent;
  color: #e2e8f0;
  padding: 0;
  font-size: 12.5px;
}
.markdown-body :deep(blockquote) {
  border-left: 3px solid #93c5fd;
  padding-left: 0.85em;
  color: #4b5563;
  margin: 0.85em 0;
}
.markdown-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.85em 0;
  font-size: 13px;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid #e5e7eb;
  padding: 0.4em 0.6em;
}
.markdown-body :deep(th) {
  background: #f8fafc;
  font-weight: 600;
}
</style>
