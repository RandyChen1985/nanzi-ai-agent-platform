<script setup lang="ts">
/**
 * 收到的分享：待处理 / 全部两个筛选，接受即复制到我的个人技能。
 */
import { onMounted, ref } from 'vue'
import axios from '@/utils/axios'
import { useToast } from '@/composables/useToast'
import ConfirmModal from '@/components/ConfirmModal.vue'
import { shareStatusMeta } from '@/utils/skillShare'

interface ReceivedShare {
  id: string
  source_skill_id: string
  source_user_id: number
  source_user_name?: string | null
  source_real_name?: string | null
  name: string
  description?: string
  message?: string | null
  status: string
  file_count?: number
  total_size?: number
  created_at?: string | null
  responded_at?: string | null
  accepted_skill_id?: string | null
}

const emit = defineEmits<{
  (e: 'updated'): void
  (e: 'go-mine', skillId: string): void
}>()

const { showToast } = useToast()
const items = ref<ReceivedShare[]>([])
const loading = ref(false)
const error = ref('')
const filter = ref<'PENDING' | 'all'>('PENDING')
const pendingCount = ref(0)
const actingId = ref('')
const confirmState = ref<{ show: boolean; title: string; message: string; type: 'danger' | 'primary' | 'warning'; onConfirm: () => void }>({
  show: false,
  title: '',
  message: '',
  type: 'danger',
  onConfirm: () => {},
})

function sourceName(item: ReceivedShare): string {
  return item.source_real_name || item.source_user_name || String(item.source_user_id)
}

function formatDateTime(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('zh-CN', { hour12: false })
}

function formatSize(bytes?: number): string {
  const size = Number(bytes || 0)
  if (size <= 0) return '0 B'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

async function load(status: 'PENDING' | 'all' = filter.value) {
  filter.value = status
  loading.value = true
  error.value = ''
  try {
    const response = await axios.get('/api/portal/skills/personal/shares/received', {
      params: { status },
    })
    items.value = (response.data?.data?.items || []) as ReceivedShare[]
    pendingCount.value = Number(response.data?.data?.pending_count || 0)
  } catch (err: any) {
    error.value = err.response?.data?.detail || '加载收到的分享失败'
  } finally {
    loading.value = false
  }
}

async function accept(item: ReceivedShare) {
  actingId.value = item.id
  try {
    const response = await axios.post(`/api/portal/skills/personal/shares/received/${item.id}/accept`)
    const acceptedId = response.data?.data?.accepted_skill_id || item.source_skill_id
    showToast(`已安装为 ${acceptedId}`, 'success')
    // 停「待处理」时刚接受的卡片会立刻消失，切到「全部」让它以「已接受」态留一会儿
    await load('all')
    emit('updated')
  } catch (err: any) {
    showToast(err.response?.data?.detail || '接受分享失败', 'error')
  } finally {
    actingId.value = ''
  }
}

function requestReject(item: ReceivedShare) {
  confirmState.value = {
    show: true,
    title: '拒绝技能分享',
    message: `确定拒绝来自「${sourceName(item)}」的技能「${item.name}」吗？拒绝后该分享内容会被回收，需要对方重新分享。`,
    type: 'danger',
    onConfirm: async () => {
      confirmState.value.show = false
      actingId.value = item.id
      try {
        await axios.post(`/api/portal/skills/personal/shares/received/${item.id}/reject`)
        showToast('已拒绝该分享', 'success')
        await load()
        emit('updated')
      } catch (err: any) {
        showToast(err.response?.data?.detail || '拒绝分享失败', 'error')
      } finally {
        actingId.value = ''
      }
    },
  }
}

onMounted(() => {
  load('PENDING')
})
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div class="flex items-center gap-1 rounded-lg border border-gray-200 bg-gray-100/60 p-0.5 dark:border-gray-700 dark:bg-gray-800/60">
        <button
          type="button"
          class="rounded-md px-3 py-1.5 text-xs font-medium transition-all"
          :class="filter === 'PENDING' ? 'bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200'"
          @click="load('PENDING')"
        >
          待处理<span v-if="pendingCount" class="ml-1 tabular-nums text-amber-600">{{ pendingCount }}</span>
        </button>
        <button
          type="button"
          class="rounded-md px-3 py-1.5 text-xs font-medium transition-all"
          :class="filter === 'all' ? 'bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200'"
          @click="load('all')"
        >
          全部
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
      <div v-for="n in 2" :key="n" class="h-28 animate-pulse rounded-2xl bg-gray-100 dark:bg-gray-800" />
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
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M12 4v12m0 0l-4-4m4 4l4-4" />
      </svg>
      <p class="mt-3 text-sm font-semibold text-gray-500 dark:text-gray-400">
        {{ filter === 'PENDING' ? '暂时没有待接受的分享' : '还没有收到过分享' }}
      </p>
      <p class="mt-1 text-xs text-gray-400 dark:text-gray-500">同事把技能分享给你后，会出现在这里。</p>
    </div>

    <div v-else class="space-y-3">
      <article
        v-for="item in items"
        :key="item.id"
        class="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm transition-colors hover:border-blue-200 dark:border-gray-700 dark:bg-gray-900"
      >
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h3 class="truncate font-bold text-gray-900 dark:text-gray-100">{{ item.name }}</h3>
              <span
                class="rounded-full px-1.5 py-0.5 text-[10px] font-semibold"
                :class="shareStatusMeta(item.status).badgeClass"
              >{{ shareStatusMeta(item.status).label }}</span>
            </div>
            <p class="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
              {{ sourceName(item) }} 分享
              <span class="font-mono text-[10px] text-gray-400 dark:text-gray-500">（{{ item.source_skill_id }}）</span>
            </p>
          </div>
          <div class="shrink-0 text-right text-[11px] text-gray-400 dark:text-gray-500">
            <div>{{ item.file_count ?? 0 }} 个文件 · {{ formatSize(item.total_size) }}</div>
            <div>{{ formatDateTime(item.created_at) }}</div>
          </div>
        </div>

        <p class="mt-3 line-clamp-2 text-sm text-gray-600 dark:text-gray-300">{{ item.description || '暂无描述' }}</p>
        <blockquote
          v-if="item.message"
          class="mt-2 rounded-lg border-l-2 border-blue-200 bg-blue-50/60 px-3 py-1.5 text-xs text-blue-800 dark:border-blue-800 dark:bg-blue-900/30 dark:text-blue-200"
        >
          {{ item.message }}
        </blockquote>

        <div class="mt-3 flex items-center justify-end gap-2">
          <template v-if="item.status === 'PENDING'">
            <button
              type="button"
              class="rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-gray-600 transition-colors hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800"
              :disabled="actingId === item.id"
              @click="requestReject(item)"
            >
              拒绝
            </button>
            <button
              type="button"
              class="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-emerald-700 disabled:opacity-50"
              :disabled="actingId === item.id"
              @click="accept(item)"
            >
              {{ actingId === item.id ? '处理中...' : '接受并安装到我的技能' }}
            </button>
          </template>
          <button
            v-else-if="item.accepted_skill_id"
            type="button"
            class="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-700 transition-colors hover:bg-emerald-100 dark:border-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300 dark:hover:bg-emerald-900/40"
            @click="emit('go-mine', item.accepted_skill_id || item.source_skill_id)"
          >
            去我的技能看看（{{ item.accepted_skill_id }}）
          </button>
        </div>
      </article>
    </div>

    <ConfirmModal
      v-if="confirmState.show"
      :title="confirmState.title"
      :message="confirmState.message"
      :type="confirmState.type"
      confirm-text="拒绝分享"
      cancel-text="取消"
      @confirm="confirmState.onConfirm()"
      @cancel="confirmState.show = false"
    />
  </div>
</template>
