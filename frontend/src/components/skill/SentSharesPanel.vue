<script setup lang="ts">
/**
 * 我的分享：跨技能汇总我发出的全部分享，按状态筛选，待接受的可撤回。
 */
import { onMounted, ref } from 'vue'
import axios from '@/utils/axios'
import { useToast } from '@/composables/useToast'
import ConfirmModal from '@/components/ConfirmModal.vue'
import { SHARE_STATUS_META, shareStatusMeta, type SkillShareStatus } from '@/utils/skillShare'

interface SentShare {
  id: string
  source_skill_id: string
  name: string
  description?: string
  message?: string | null
  status: SkillShareStatus
  target_user_id: number
  target_user_name?: string | null
  target_real_name?: string | null
  created_at?: string | null
  responded_at?: string | null
  accepted_skill_id?: string | null
}

const emit = defineEmits<{
  (e: 'updated'): void
  (e: 'open-skill', skillId: string): void
}>()

const { showToast } = useToast()
const items = ref<SentShare[]>([])
const loading = ref(false)
const error = ref('')
const filter = ref<'' | SkillShareStatus>('')
const pendingCount = ref(0)
const total = ref(0)
const statusCounts = ref<Record<SkillShareStatus, number>>({
  PENDING: 0,
  ACCEPTED: 0,
  REJECTED: 0,
  WITHDRAWN: 0,
})
const actingId = ref('')
const confirmState = ref<{ show: boolean; title: string; message: string; onConfirm: () => void }>({
  show: false,
  title: '',
  message: '',
  onConfirm: () => {},
})

const filters: Array<{ key: '' | SkillShareStatus; label: string }> = [
  { key: '', label: '全部' },
  { key: 'PENDING', label: SHARE_STATUS_META.PENDING.label },
  { key: 'ACCEPTED', label: SHARE_STATUS_META.ACCEPTED.label },
  { key: 'REJECTED', label: SHARE_STATUS_META.REJECTED.label },
  { key: 'WITHDRAWN', label: SHARE_STATUS_META.WITHDRAWN.label },
]

function targetName(item: SentShare): string {
  return item.target_real_name || item.target_user_name || String(item.target_user_id)
}

function formatDateTime(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('zh-CN', { hour12: false })
}

function countFor(key: '' | SkillShareStatus): number {
  if (!key) return total.value
  return statusCounts.value[key] || 0
}

async function load(status: '' | SkillShareStatus = filter.value) {
  filter.value = status
  loading.value = true
  error.value = ''
  try {
    const response = await axios.get('/api/portal/skills/personal/shares/sent', {
      params: status ? { status } : {},
    })
    const data = response.data?.data || {}
    items.value = (data.items || []) as SentShare[]
    total.value = Number(data.total || 0)
    pendingCount.value = Number(data.pending_count || 0)
    statusCounts.value = {
      PENDING: 0,
      ACCEPTED: 0,
      REJECTED: 0,
      WITHDRAWN: 0,
      ...(data.status_counts || {}),
    }
  } catch (err: any) {
    error.value = err.response?.data?.detail || '加载我的分享失败'
  } finally {
    loading.value = false
  }
}

function requestWithdraw(item: SentShare) {
  confirmState.value = {
    show: true,
    title: '撤回技能分享',
    message: `确定撤回分享给「${targetName(item)}」的技能「${item.name}」吗？对方将无法再接受，分享内容会被回收。`,
    onConfirm: async () => {
      confirmState.value.show = false
      actingId.value = item.id
      try {
        await axios.delete(`/api/portal/skills/personal/shares/sent/${item.id}`)
        showToast('已撤回该分享', 'success')
        await load()
        emit('updated')
      } catch (err: any) {
        showToast(err.response?.data?.detail || '撤回分享失败', 'error')
      } finally {
        actingId.value = ''
      }
    },
  }
}

onMounted(() => {
  load('')
})
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div class="flex flex-wrap items-center gap-1 rounded-lg border border-gray-200 bg-gray-100/60 p-0.5 dark:border-gray-700 dark:bg-gray-800/60">
        <button
          v-for="item in filters"
          :key="item.key || 'all'"
          type="button"
          class="rounded-md px-2.5 py-1.5 text-xs font-medium transition-all"
          :class="filter === item.key ? 'bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200'"
          @click="load(item.key)"
        >
          {{ item.label }}
          <span v-if="countFor(item.key)" class="ml-1 tabular-nums text-gray-400 dark:text-gray-500">{{ countFor(item.key) }}</span>
        </button>
      </div>
      <button
        type="button"
        class="rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-xs text-gray-600 transition-colors hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300 dark:hover:bg-gray-800"
        :disabled="loading"
        @click="load()"
      >
        刷新
      </button>
    </div>

    <div v-if="loading && !items.length" class="space-y-3">
      <div v-for="n in 3" :key="n" class="h-16 animate-pulse rounded-xl bg-gray-100 dark:bg-gray-800" />
    </div>

    <div v-else-if="error" class="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700 dark:border-amber-800 dark:bg-amber-900/30 dark:text-amber-300">
      {{ error }}
      <button type="button" class="ml-2 font-semibold underline" @click="load()">重试</button>
    </div>

    <div
      v-else-if="!items.length"
      class="flex min-h-[280px] flex-col items-center justify-center rounded-2xl border border-dashed border-gray-200 bg-white px-6 text-center dark:border-gray-700 dark:bg-gray-900"
    >
      <svg class="h-12 w-12 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M12 20V8m0 0l4 4m-4-4l-4 4" />
      </svg>
      <p class="mt-3 text-sm font-semibold text-gray-500 dark:text-gray-400">
        {{ filter ? '该状态下没有分享记录' : '还没有分享过技能' }}
      </p>
      <p class="mt-1 text-xs text-gray-400 dark:text-gray-500">在「我的技能」卡片上点分享按钮，就能发给指定同事。</p>
    </div>

    <div v-else class="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-900">
      <div class="overflow-x-auto">
        <table class="min-w-full divide-y divide-gray-200 text-left text-sm dark:divide-gray-700">
          <thead class="bg-gray-50/70 text-[11px] font-bold uppercase tracking-wider text-gray-500 dark:bg-gray-800/70 dark:text-gray-400">
            <tr>
              <th class="px-4 py-3">技能</th>
              <th class="px-4 py-3">接收人</th>
              <th class="px-4 py-3">附言</th>
              <th class="px-4 py-3 text-center">状态</th>
              <th class="px-4 py-3 text-center">分享时间</th>
              <th class="px-4 py-3 text-center">操作</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-gray-100 dark:divide-gray-800">
            <tr v-for="item in items" :key="item.id" class="transition-colors hover:bg-blue-50/20 dark:hover:bg-blue-900/20">
              <td class="px-4 py-3">
                <button
                  type="button"
                  class="text-left font-semibold text-gray-900 hover:text-blue-600 dark:text-gray-100 dark:hover:text-blue-400"
                  :title="`定位到我的技能：${item.source_skill_id}`"
                  @click="emit('open-skill', item.source_skill_id)"
                >
                  {{ item.name }}
                </button>
                <div class="font-mono text-[10px] text-gray-400 dark:text-gray-500">{{ item.source_skill_id }}</div>
              </td>
              <td class="px-4 py-3">
                <div class="text-gray-800 dark:text-gray-200">{{ targetName(item) }}</div>
                <div class="text-[10px] text-gray-400 dark:text-gray-500">@{{ item.target_user_name }}</div>
              </td>
              <td class="max-w-[16rem] px-4 py-3 text-xs text-gray-500 dark:text-gray-400">
                <span class="line-clamp-2" :title="item.message || ''">{{ item.message || '—' }}</span>
              </td>
              <td class="px-4 py-3 text-center">
                <span
                  class="rounded-full px-1.5 py-0.5 text-[10px] font-semibold"
                  :class="shareStatusMeta(item.status).badgeClass"
                >{{ shareStatusMeta(item.status).label }}</span>
                <div v-if="item.accepted_skill_id" class="mt-1 font-mono text-[10px] text-emerald-600">
                  → {{ item.accepted_skill_id }}
                </div>
              </td>
              <td class="px-4 py-3 text-center text-xs text-gray-500 dark:text-gray-400">{{ formatDateTime(item.created_at) }}</td>
              <td class="px-4 py-3 text-center">
                <button
                  v-if="item.status === 'PENDING'"
                  type="button"
                  class="rounded-lg border border-red-200 px-2.5 py-1 text-xs font-semibold text-red-600 transition-colors hover:bg-red-50 disabled:opacity-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-900/30"
                  :disabled="actingId === item.id"
                  @click="requestWithdraw(item)"
                >
                  {{ actingId === item.id ? '处理中...' : '撤回' }}
                </button>
                <span v-else class="text-xs text-gray-400 dark:text-gray-500">{{ formatDateTime(item.responded_at) }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <ConfirmModal
      v-if="confirmState.show"
      :title="confirmState.title"
      :message="confirmState.message"
      type="warning"
      confirm-text="撤回分享"
      cancel-text="取消"
      @confirm="confirmState.onConfirm()"
      @cancel="confirmState.show = false"
    />
  </div>
</template>
