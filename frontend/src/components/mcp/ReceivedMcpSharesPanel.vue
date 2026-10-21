<script setup lang="ts">
/**
 * 收到的 MCP 分享：待处理 / 全部两个筛选，接受即复制一份到「我的 MCP」。
 */
import { onMounted, ref } from 'vue'
import axios from '@/utils/axios'
import { useToast } from '@/composables/useToast'
import ConfirmModal from '@/components/ConfirmModal.vue'
import McpShareAcceptWizard from './McpShareAcceptWizard.vue'
import { shareStatusMeta } from '@/utils/mcpShare'
import type { McpShareItem } from '@/utils/mcpShare'

const emit = defineEmits<{
  (e: 'updated'): void
  (e: 'go-mine', serverId: string): void
}>()

const { showToast } = useToast()
const items = ref<McpShareItem[]>([])
const loading = ref(false)
const error = ref('')
const filter = ref<'PENDING' | 'all'>('PENDING')
const pendingCount = ref(0)
const actingId = ref('')
const wizardShare = ref<McpShareItem | null>(null)
const confirmState = ref<{ show: boolean; title: string; message: string; type: 'danger' | 'primary' | 'warning'; onConfirm: () => void }>({
  show: false,
  title: '',
  message: '',
  type: 'danger',
  onConfirm: () => {},
})

async function load(target: 'PENDING' | 'all' = filter.value) {
  filter.value = target
  loading.value = true
  error.value = ''
  try {
    const response = await axios.get('/api/portal/mcp/shares/received', {
      params: { status: target },
    })
    const data = response.data?.data || {}
    items.value = (data.items || []) as McpShareItem[]
    pendingCount.value = Number(data.pending_count || 0)
  } catch (err: any) {
    error.value = err.response?.data?.detail || '加载收到的分享失败'
  } finally {
    loading.value = false
  }
}

/** 接受 / 自定义安装都会走到这里：刷新列表与角标，并跳到新装的服务 */
async function afterInstalled(serverId: string) {
  // 停在「待处理」时刚接受的卡片会立刻消失，切到「全部」让它以已接受态留一会儿
  await load('all')
  emit('updated')
  if (serverId) emit('go-mine', serverId)
}

async function accept(item: McpShareItem) {
  actingId.value = item.id
  try {
    const response = await axios.post(`/api/portal/mcp/shares/received/${item.id}/accept`)
    const data = response.data?.data || {}
    showToast(`已安装为 ${data.server?.server_name || data.accepted_server_id}`, 'success')
    await afterInstalled(String(data.accepted_server_id || ''))
  } catch (err: any) {
    showToast(err.response?.data?.detail || '接受分享失败', 'error')
  } finally {
    actingId.value = ''
  }
}

function requestReject(item: McpShareItem) {
  confirmState.value = {
    show: true,
    title: '拒绝 MCP 分享',
    message: `确定拒绝「${item.server_name}」吗？分享人收到的状态会变成「已拒绝」。`,
    type: 'warning',
    onConfirm: async () => {
      confirmState.value.show = false
      actingId.value = item.id
      try {
        await axios.post(`/api/portal/mcp/shares/received/${item.id}/reject`)
        showToast('已拒绝', 'success')
        await load()
        emit('updated')
      } catch (err: any) {
        showToast(err.response?.data?.detail || '拒绝失败', 'error')
      } finally {
        actingId.value = ''
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
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div class="flex items-center gap-1 rounded-lg bg-gray-100 p-0.5 dark:bg-gray-800">
        <button
          type="button"
          class="rounded-md px-3 py-1 text-xs font-medium transition-colors"
          :class="filter === 'PENDING' ? 'bg-white text-emerald-600 shadow-sm dark:bg-gray-800 dark:text-emerald-400' : 'text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200'"
          @click="load('PENDING')"
        >
          待处理<span v-if="pendingCount" class="ml-1 font-semibold">{{ pendingCount }}</span>
        </button>
        <button
          type="button"
          class="rounded-md px-3 py-1 text-xs font-medium transition-colors"
          :class="filter === 'all' ? 'bg-white text-emerald-600 shadow-sm dark:bg-gray-800 dark:text-emerald-400' : 'text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200'"
          @click="load('all')"
        >
          全部
        </button>
      </div>
      <button
        type="button"
        class="text-xs text-gray-400 transition-colors hover:text-emerald-600 dark:text-gray-500 dark:hover:text-emerald-400"
        @click="load()"
      >
        刷新
      </button>
    </div>

    <div v-if="loading" class="py-10 text-center text-sm text-gray-400 dark:text-gray-500">加载收到的分享…</div>
    <div v-else-if="error" class="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-600 dark:border-red-900 dark:bg-red-900/20 dark:text-red-300">
      {{ error }}
    </div>
    <div v-else-if="!items.length" class="rounded-2xl border border-dashed border-gray-200 py-12 text-center text-sm text-gray-400 dark:border-gray-700 dark:text-gray-500">
      暂时没有收到 MCP 分享
    </div>
    <div v-else class="min-h-0 flex-1 space-y-2 overflow-y-auto">
      <div
        v-for="item in items"
        :key="item.id"
        class="rounded-2xl border border-gray-100 bg-white p-4 shadow-sm transition-colors dark:border-gray-800 dark:bg-gray-900"
      >
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="flex min-w-0 flex-wrap items-center gap-2">
              <h3 class="truncate text-sm font-bold text-gray-900 dark:text-gray-100">{{ item.server_name }}</h3>
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
              来自 <span class="font-medium text-gray-700 dark:text-gray-300">{{ item.source_real_name || item.source_user_name || item.source_user_id }}</span>
              <span class="mx-1">·</span>{{ formatTime(item.created_at) }}
            </p>
            <p v-if="item.remark" class="mt-1.5 line-clamp-2 text-xs text-gray-600 dark:text-gray-300">{{ item.remark }}</p>
            <p class="mt-1.5 truncate font-mono text-[10px] text-gray-400 dark:text-gray-500">{{ item.sse_url }}</p>
            <p v-if="item.message" class="mt-2 rounded-lg bg-gray-50 px-2.5 py-1.5 text-xs text-gray-600 dark:bg-gray-800/60 dark:text-gray-300">
              {{ item.message }}
            </p>
          </div>
          <div class="flex shrink-0 items-center gap-1.5">
            <template v-if="item.status === 'PENDING'">
              <button
                type="button"
                :disabled="actingId === item.id"
                class="rounded-lg border border-emerald-200 px-2.5 py-1 text-xs font-medium text-emerald-700 transition-colors hover:bg-emerald-50 disabled:opacity-50 dark:border-emerald-800 dark:text-emerald-300 dark:hover:bg-emerald-900/30"
                @click="requestReject(item)"
              >
                拒绝
              </button>
              <button
                type="button"
                :disabled="actingId === item.id"
                class="rounded-lg border border-gray-200 px-2.5 py-1 text-xs text-gray-600 transition-colors hover:border-emerald-300 hover:text-emerald-700 disabled:opacity-50 dark:border-gray-700 dark:text-gray-300 dark:hover:border-emerald-700 dark:hover:text-emerald-300"
                title="安装前可以改服务名、备注和地址"
                @click="wizardShare = item"
              >
                自定义安装
              </button>
              <button
                type="button"
                :disabled="actingId === item.id"
                class="rounded-lg bg-emerald-600 px-2.5 py-1 text-xs font-medium text-white transition-colors hover:bg-emerald-700 disabled:opacity-50"
                title="按分享内容直接安装到我的 MCP"
                @click="accept(item)"
              >
                {{ actingId === item.id ? '安装中…' : '接受并安装' }}
              </button>
            </template>
            <button
              v-else-if="item.status === 'ACCEPTED' && item.accepted_server_id"
              type="button"
              class="rounded-lg border border-gray-200 px-2.5 py-1 text-xs text-gray-600 transition-colors hover:border-emerald-300 hover:text-emerald-700 dark:border-gray-700 dark:text-gray-300 dark:hover:border-emerald-700 dark:hover:text-emerald-300"
              @click="emit('go-mine', item.accepted_server_id || item.source_server_id)"
            >
              去我的 MCP 看看
            </button>
          </div>
        </div>
      </div>
    </div>

    <McpShareAcceptWizard
      :visible="!!wizardShare"
      :share="wizardShare"
      @installed="afterInstalled"
      @close="wizardShare = null"
    />

    <ConfirmModal
      v-if="confirmState.show"
      :title="confirmState.title"
      :message="confirmState.message"
      :type="confirmState.type"
      @confirm="confirmState.onConfirm()"
      @cancel="confirmState.show = false"
    />
  </div>
</template>
