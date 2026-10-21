<script setup lang="ts">
/**
 * 我发出的 MCP 分享：按状态筛选、查看接收人与处理结果、撤回未处理的邀请。
 */
import { computed, onMounted, ref } from 'vue'
import axios from '@/utils/axios'
import { useToast } from '@/composables/useToast'
import ConfirmModal from '@/components/ConfirmModal.vue'
import { SHARE_STATUS_META, shareStatusMeta } from '@/utils/mcpShare'
import type { McpShareItem } from '@/utils/mcpShare'

const emit = defineEmits<{
  (e: 'updated'): void
  (e: 'open-mine'): void
}>()

const { showToast } = useToast()
const items = ref<McpShareItem[]>([])
const loading = ref(false)
const error = ref('')
const filter = ref<string>('all')
const statusCounts = ref<Record<string, number>>({ PENDING: 0, ACCEPTED: 0, REJECTED: 0, WITHDRAWN: 0 })
const total = ref(0)
const withdrawId = ref('')
const confirmState = ref<{ show: boolean; onConfirm: () => void }>({ show: false, onConfirm: () => {} })

const filters = computed(() => [
  { key: 'all', label: '全部', count: total.value },
  ...Object.entries(SHARE_STATUS_META).map(([key, meta]) => ({
    key,
    label: meta.label,
    count: statusCounts.value[key] || 0,
  })),
])

async function load(target: string = filter.value) {
  filter.value = target
  loading.value = true
  error.value = ''
  try {
    const response = await axios.get('/api/portal/mcp/shares/sent', {
      params: target === 'all' ? {} : { status: target },
    })
    const data = response.data?.data || {}
    items.value = (data.items || []) as McpShareItem[]
    total.value = Number(data.total || 0)
    statusCounts.value = { ...statusCounts.value, ...(data.status_counts || {}) }
  } catch (err: any) {
    error.value = err.response?.data?.detail || '加载我的分享失败'
  } finally {
    loading.value = false
  }
}

function requestWithdraw(item: McpShareItem) {
  confirmState.value = {
    show: true,
    onConfirm: async () => {
      confirmState.value.show = false
      withdrawId.value = item.id
      try {
        await axios.delete(`/api/portal/mcp/shares/sent/${item.id}`)
        showToast('已撤回', 'success')
        await load()
        emit('updated')
      } catch (err: any) {
        showToast(err.response?.data?.detail || '撤回失败', 'error')
      } finally {
        withdrawId.value = ''
      }
    },
  }
}

function formatTime(value: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString()
}

onMounted(() => void load())
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col gap-3">
    <div class="flex flex-wrap items-center gap-1.5">
      <button
        v-for="item in filters"
        :key="item.key"
        type="button"
        class="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs transition-colors"
        :class="filter === item.key
          ? 'border-emerald-600 bg-emerald-600 text-white'
          : 'border-gray-200 bg-white text-gray-600 hover:border-emerald-200 hover:text-emerald-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300 dark:hover:border-emerald-800 dark:hover:text-emerald-400'"
        @click="load(item.key)"
      >
        {{ item.label }}
        <span class="tabular-nums opacity-70">{{ item.count }}</span>
      </button>
    </div>

    <div v-if="loading" class="py-10 text-center text-sm text-gray-400 dark:text-gray-500">加载我的分享…</div>
    <div v-else-if="error" class="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-600 dark:border-red-900 dark:bg-red-900/20 dark:text-red-300">
      {{ error }}
    </div>
    <div v-else-if="!items.length" class="rounded-2xl border border-dashed border-gray-200 py-12 text-center text-sm text-gray-400 dark:border-gray-700 dark:text-gray-500">
      还没有分享过 MCP
    </div>
    <div v-else class="min-h-0 flex-1 overflow-y-auto rounded-2xl border border-gray-100 bg-white dark:border-gray-800 dark:bg-gray-900">
      <div
        v-for="item in items"
        :key="item.id"
        class="flex flex-wrap items-start justify-between gap-3 border-b border-gray-50 px-4 py-3 last:border-b-0 dark:border-gray-800"
      >
        <div class="min-w-0 flex-1">
          <div class="flex min-w-0 flex-wrap items-center gap-2">
            <button
              type="button"
              class="truncate text-sm font-bold text-gray-900 transition-colors hover:text-emerald-600 dark:text-gray-100 dark:hover:text-emerald-400"
              @click="emit('open-mine')"
            >
              {{ item.server_name }}
            </button>
            <span
              class="rounded-full px-1.5 py-0.5 text-[10px] font-semibold"
              :class="shareStatusMeta(item.status).badgeClass"
            >{{ shareStatusMeta(item.status).label }}</span>
            <span
              v-if="!item.include_credentials"
              class="rounded-full bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700 dark:bg-amber-900/40 dark:text-amber-300"
            >未携带凭证</span>
          </div>
          <p class="mt-1 text-[11px] text-gray-500 dark:text-gray-400">
            分享给 <span class="font-medium text-gray-700 dark:text-gray-300">{{ item.target_real_name || item.target_user_name || item.target_user_id }}</span>
            <span class="mx-1">·</span>{{ formatTime(item.created_at) }}
            <template v-if="item.responded_at">
              <span class="mx-1">·</span>处理于 {{ formatTime(item.responded_at) }}
            </template>
          </p>
          <p v-if="item.message" class="mt-1 line-clamp-1 text-xs text-gray-500 dark:text-gray-400">{{ item.message }}</p>
        </div>
        <button
          v-if="item.status === 'PENDING'"
          type="button"
          :disabled="withdrawId === item.id"
          class="shrink-0 rounded-lg border border-gray-200 px-2.5 py-1 text-xs text-gray-600 transition-colors hover:border-red-300 hover:text-red-600 disabled:opacity-50 dark:border-gray-700 dark:text-gray-300 dark:hover:border-red-800 dark:hover:text-red-400"
          @click="requestWithdraw(item)"
        >
          {{ withdrawId === item.id ? '撤回中…' : '撤回' }}
        </button>
      </div>
    </div>

    <ConfirmModal
      v-if="confirmState.show"
      title="撤回 MCP 分享"
      message="撤回后对方将无法再接受这条分享，已接受的不受影响。"
      type="warning"
      @confirm="confirmState.onConfirm()"
      @cancel="confirmState.show = false"
    />
  </div>
</template>
