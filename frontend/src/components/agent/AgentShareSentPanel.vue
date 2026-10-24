<script setup lang="ts">
/**
 * 我分享的：我发出去的所有智能体副本邀请。
 *
 * 分享人必须能查到「我发出去的东西现在什么状态」——对方一直没接受、
 * 或者发错人了，都要能看见、能撤回。这跟技能 / MCP 分享是同一个诉求。
 */
import { computed, onMounted, ref } from 'vue'
import type { AgentShareRecord } from '../../api/agent'
import { agentSystemApi } from '../../api/agent'
import { useToast } from '../../composables/useToast'

const emit = defineEmits<{
  (e: 'updated'): void
}>()

const { showToast } = useToast()

const items = ref<AgentShareRecord[]>([])
const loading = ref(false)
const errorMessage = ref('')
const actingId = ref<string | null>(null)

const pendingCount = computed(() => items.value.filter((item) => item.status === 'PENDING').length)

const load = async () => {
  loading.value = true
  errorMessage.value = ''
  try {
    const res = await agentSystemApi.listSentShares()
    items.value = res.data || []
  } catch (e: any) {
    errorMessage.value = e?.response?.data?.detail || '加载失败，请稍后重试'
    items.value = []
  } finally {
    loading.value = false
  }
}

onMounted(load)

/** 搜索词来自父级搜索框 */
const props = withDefaults(defineProps<{ keyword?: string }>(), { keyword: '' })

const visible = computed(() => {
  const kw = (props.keyword || '').trim().toLowerCase()
  if (!kw) return items.value
  return items.value.filter((item) =>
    [item.agent_name, item.agent_display_name, item.recipient_user_name]
      .filter(Boolean)
      .some((field) => String(field).toLowerCase().includes(kw)),
  )
})

const statusMeta = (status: string): { label: string; className: string } => {
  switch (status) {
    case 'PENDING':
      return { label: '等待对方接受', className: 'bg-amber-50 text-amber-700 border-amber-200' }
    case 'ACCEPTED':
      return { label: '对方已接受', className: 'bg-emerald-50 text-emerald-700 border-emerald-200' }
    case 'REJECTED':
      return { label: '对方已拒绝', className: 'bg-gray-50 text-gray-600 border-gray-200' }
    case 'EXPIRED':
      return { label: '已过期', className: 'bg-gray-50 text-gray-500 border-gray-200' }
    case 'REVOKED':
      return { label: '已作废', className: 'bg-rose-50 text-rose-700 border-rose-200' }
    case 'WITHDRAWN':
      return { label: '你已撤回', className: 'bg-gray-50 text-gray-600 border-gray-200' }
    default:
      return { label: status, className: 'bg-gray-50 text-gray-600 border-gray-200' }
  }
}

function formatDateTime(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('zh-CN', { hour12: false })
}

const withdraw = async (record: AgentShareRecord) => {
  actingId.value = record.id
  try {
    await agentSystemApi.withdrawShare(record.id)
    showToast('已撤回该分享', 'success')
    await load()
    emit('updated')
  } catch (e: any) {
    showToast(e?.response?.data?.detail || '撤回失败', 'error')
  } finally {
    actingId.value = null
  }
}
</script>

<template>
  <div class="space-y-3">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <p class="text-xs text-gray-500">
        共 {{ items.length }} 条，其中 <span class="font-semibold text-gray-700">{{ pendingCount }}</span> 条等待对方接受
      </p>
      <button type="button" class="text-xs font-medium text-gray-500 hover:text-primary" @click="load">
        刷新
      </button>
    </div>

    <div class="rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-xs leading-relaxed text-blue-900">
      分享出去的是<strong>一份副本</strong>：对方接受后会得到属于他自己的智能体，需要重新命名并走向导确认。
      你这一份不会被改动，也不会变成共用。
    </div>

    <div v-if="loading" class="rounded-xl border border-gray-200 bg-white py-12 text-center text-sm text-gray-400">
      正在加载…
    </div>
    <div v-else-if="errorMessage" class="rounded-xl border border-red-100 bg-white py-12 text-center text-sm text-red-600">
      {{ errorMessage }}
    </div>
    <div v-else-if="!items.length" class="rounded-xl border border-gray-200 bg-white py-12 text-center text-sm text-gray-400">
      你还没有分享过智能体
    </div>

    <div v-else class="space-y-2.5">
      <div
        v-for="record in visible"
        :key="record.id"
        class="rounded-xl border border-gray-200 bg-white p-4 transition-colors hover:border-gray-300"
      >
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <p class="truncate text-sm font-semibold text-gray-900">
                {{ record.agent_display_name || record.agent_name }}
              </p>
              <span
                class="rounded border px-1.5 py-0.5 text-[11px] font-medium"
                :class="statusMeta(record.status).className"
              >
                {{ statusMeta(record.status).label }}
              </span>
            </div>
            <p class="mt-1 text-xs text-gray-500">
              分享给 <span class="font-medium text-gray-700">{{ record.recipient_user_name || '同事' }}</span>
              · {{ formatDateTime(record.created_at) }}
              <span v-if="record.status === 'PENDING' && record.expires_at" class="text-amber-600">
                · {{ formatDateTime(record.expires_at) }} 前有效
              </span>
            </p>
            <p v-if="record.summary?.description" class="mt-1 text-xs text-gray-600">
              {{ record.summary.description }}
            </p>
            <div class="mt-1.5 flex flex-wrap items-center gap-1.5">
              <span
                v-if="record.summary?.model_name"
                class="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-600"
              >
                模型 {{ record.summary.model_name }}
              </span>
              <span
                v-if="record.summary?.tool_count"
                class="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-600"
              >
                {{ record.summary.tool_count }} 个工具
              </span>
              <span
                v-if="record.summary?.source_version_number"
                class="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-600"
              >
                源版本 v{{ record.summary.source_version_number }}
              </span>
            </div>
            <p
              v-if="record.share_message"
              class="mt-2 rounded-lg bg-gray-50 px-2.5 py-1.5 text-xs text-gray-600"
            >
              {{ record.share_message }}
            </p>
            <p v-if="record.status === 'REVOKED' && record.share_reason" class="mt-1.5 text-xs text-rose-600">
              {{ record.share_reason }}
            </p>
          </div>

          <div class="flex shrink-0 gap-1.5">
            <button
              v-if="record.status === 'PENDING'"
              type="button"
              :disabled="actingId === record.id"
              class="rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-50"
              @click="withdraw(record)"
            >
              撤回
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
