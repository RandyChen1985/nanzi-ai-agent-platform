<script setup lang="ts">
/**
 * 发布历史：申请与事件合并成一条时间线。
 *
 * 只对管理员和该智能体的所有者开放，没有跨智能体的全局视图——这个页面的
 * 用途是「这个智能体为什么被上架/下架」，而不是审计全平台。
 */
import { ref, watch } from 'vue'
import type { AIAgent, AgentSystemHistoryEntry } from '../../api/agent'
import { agentSystemApi } from '../../api/agent'

const props = defineProps<{
  isOpen: boolean
  agent: AIAgent | null
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

const entries = ref<AgentSystemHistoryEntry[]>([])
const loading = ref(false)
const errorMessage = ref('')
const expanded = ref<string>('')

const formatTime = (value?: string | null) => {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString('zh-CN', { hour12: false })
}

const eventTone = (entry: AgentSystemHistoryEntry) => {
  if (entry.kind === 'request') {
    if (entry.status === 'APPROVED') return { dot: 'bg-emerald-500', label: '申请已通过' }
    if (entry.status === 'REJECTED') return { dot: 'bg-red-500', label: '申请被驳回' }
    if (entry.status === 'CANCELLED') return { dot: 'bg-slate-400', label: '申请人撤回' }
    return { dot: 'bg-amber-500', label: '提交发布申请' }
  }
  const map: Record<string, { dot: string; label: string }> = {
    PUBLISH: { dot: 'bg-emerald-500', label: '发布为系统级' },
    UNPUBLISH: { dot: 'bg-red-500', label: '下架（取消系统级）' },
    META_UPDATE: { dot: 'bg-blue-500', label: '修改元数据' },
    TOGGLE_ENABLED: { dot: 'bg-slate-500', label: '启用状态变更' },
    BASELINE_PUBLISH: { dot: 'bg-indigo-500', label: '发布新基线版本' },
    TRANSFER_OWNER: { dot: 'bg-violet-500', label: '变更权属人' },
  SHARE_SEND: { dot: 'bg-sky-500', label: '分享给同事' },
  SHARE_ACCEPT: { dot: 'bg-sky-500', label: '分享被接受' },
  SHARE_REJECT: { dot: 'bg-gray-400', label: '分享被拒绝' },
  SHARE_REVOKE: { dot: 'bg-amber-500', label: '分享已作废' },
  }
  return map[entry.action || ''] || { dot: 'bg-slate-400', label: entry.action || '操作' }
}

const toggle = (key: string) => {
  expanded.value = expanded.value === key ? '' : key
}

const load = async () => {
  if (!props.agent) return
  loading.value = true
  errorMessage.value = ''
  try {
    const res = await agentSystemApi.history(props.agent.id, { page_size: 100 })
    entries.value = res.data.items || []
  } catch (error: any) {
    entries.value = []
    errorMessage.value = error?.response?.data?.detail || '加载发布历史失败'
  } finally {
    loading.value = false
  }
}

watch(
  () => props.isOpen,
  (open) => {
    if (!open) return
    entries.value = []
    expanded.value = ''
    void load()
  },
)
</script>

<template>
  <Teleport to="body">
    <div v-if="isOpen" class="fixed inset-0 z-[100] flex justify-end bg-slate-900/40">
      <div class="flex h-full w-full max-w-xl flex-col bg-white shadow-2xl">
        <div class="flex items-center gap-2 border-b border-slate-200 px-5 py-3">
          <div>
            <h2 class="text-base font-semibold text-slate-900">发布历史</h2>
            <p class="mt-0.5 text-xs text-slate-500">{{ agent?.display_name }}</p>
          </div>
          <button
            class="ml-auto rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
            @click="emit('close')"
          >
            ✕
          </button>
        </div>

        <div class="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <p v-if="loading" class="text-xs text-slate-400">加载中…</p>
          <p v-else-if="errorMessage" class="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">
            {{ errorMessage }}
          </p>
          <p v-else-if="!entries.length" class="text-xs leading-relaxed text-slate-400">
            还没有发布记录。该智能体被设为系统级后，这里会记录每次申请、审核、上架、下架与配置变更。
          </p>

          <ol v-else class="relative space-y-4 border-l border-slate-200 pl-5">
            <li v-for="entry in entries" :key="`${entry.kind}-${entry.id}`" class="relative">
              <span
                class="absolute -left-[23px] top-1.5 h-2.5 w-2.5 rounded-full ring-2 ring-white"
                :class="eventTone(entry).dot"
              ></span>

              <div class="flex flex-wrap items-baseline gap-2">
                <p class="text-sm font-semibold text-slate-800">{{ eventTone(entry).label }}</p>
                <p class="text-[11px] text-slate-400">
                  {{ formatTime(entry.at) }}
                  <span
                    v-if="entry.kind === 'request' && entry.reviewed_at && entry.requested_at"
                    class="text-slate-300"
                  >（{{ formatTime(entry.requested_at) }} 提交）</span>
                </p>
              </div>

              <p class="mt-0.5 text-xs text-slate-500">
                {{ entry.actor_name || '系统' }}
                <template v-if="entry.version_number"> · 版本 v{{ entry.version_number }}</template>
                <template v-if="entry.affected_user_count !== null && entry.affected_user_count !== undefined">
                  · 影响 {{ entry.affected_user_count }} 人
                </template>
                <template v-if="entry.notified !== null && entry.notified !== undefined">
                  · {{ entry.notified ? '已发站内信' : '未通知用户' }}
                </template>
              </p>

              <p
                v-if="entry.reason"
                class="mt-1 rounded-md border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs leading-relaxed text-slate-600"
              >
                {{ entry.reason }}
              </p>
              <p
                v-if="entry.comment"
                class="mt-1 rounded-md border border-blue-100 bg-blue-50 px-2.5 py-1.5 text-xs leading-relaxed text-blue-900"
              >
                申请理由：{{ entry.comment }}
              </p>
              <p
                v-if="entry.review_comment"
                class="mt-1 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs leading-relaxed text-slate-600"
              >
                审核意见：{{ entry.review_comment }}
              </p>

              <button
                v-if="entry.snapshot"
                class="mt-1 text-[11px] font-medium text-primary hover:underline"
                @click="toggle(`${entry.kind}-${entry.id}`)"
              >
                {{ expanded === `${entry.kind}-${entry.id}` ? '收起内容快照' : '查看内容快照' }}
              </button>

              <div
                v-if="entry.snapshot && expanded === `${entry.kind}-${entry.id}`"
                class="mt-2 space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3"
              >
                <p class="text-[11px] font-semibold text-slate-600">
                  元数据：{{ entry.snapshot.agent?.display_name }}（{{ entry.snapshot.agent?.name }}）
                </p>
                <p v-if="entry.snapshot.version" class="text-[11px] text-slate-600">
                  版本 v{{ entry.snapshot.version.version_number }} · 模型
                  {{ entry.snapshot.version.model_name || '—' }}
                </p>
                <p
                  v-if="entry.snapshot.version"
                  class="text-[11px] leading-relaxed text-slate-500"
                >
                  工具：{{ (entry.snapshot.version.tools || []).join('、') || '未绑定工具' }}
                </p>
                <pre
                  v-if="entry.snapshot.version?.system_prompt"
                  class="max-h-56 overflow-auto whitespace-pre-wrap rounded-md border border-slate-200 bg-white p-2 font-mono text-[11px] leading-relaxed text-slate-600"
                  >{{ entry.snapshot.version.system_prompt }}</pre
                >
              </div>
            </li>
          </ol>
        </div>
      </div>
    </div>
  </Teleport>
</template>
