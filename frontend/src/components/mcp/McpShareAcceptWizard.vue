<script setup lang="ts">
/**
 * 安装 MCP 分享的向导弹层。
 *
 * 一键「接受并安装」对熟人分享够用，但接收人常常想改一下落地名或地址
 * （比如自己已有同名服务、或分享方的地址要走内网网关），所以额外给一条
 * 可编辑的安装路径；默认值全部来自分享快照，不改就是原样安装。
 */
import { computed, onUnmounted, ref, watch } from 'vue'
import axios from '@/utils/axios'
import { useToast } from '@/composables/useToast'
import { useUser } from '@/composables/useUser'
import {
  buildMcpServerNamePrefix,
  composeMcpServerName,
  stripMcpServerNamePrefix,
} from '@/utils/mcpServerName'
import type { McpShareItem } from '@/utils/mcpShare'

const props = defineProps<{
  visible: boolean
  share: McpShareItem | null
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'installed', serverId: string): void
}>()

const { userInfo } = useUser()
const { showToast } = useToast()

const myUserName = computed(() => String(userInfo.value?.user_name || ''))
const namePrefix = computed(() => buildMcpServerNamePrefix('personal', myUserName.value))

const nameSuffix = ref('')
const remark = ref('')
const sseUrl = ref('')
const submitting = ref(false)

const reset = () => {
  const share = props.share
  // 后端算好的默认落地名优先；老分享没有这个字段时退回源名（strip 会剥掉任意私有前缀）
  const suggested = share?.suggested_server_name || share?.server_name || ''
  nameSuffix.value = suggested
    ? stripMcpServerNamePrefix(suggested, 'personal', myUserName.value)
    : ''
  remark.value = share?.remark || ''
  sseUrl.value = share?.sse_url || ''
}

const fullName = computed(() =>
  composeMcpServerName('personal', myUserName.value, nameSuffix.value),
)
const urlValid = computed(() => /^https?:\/\//i.test(sseUrl.value.trim()))
const canSubmit = computed(() => Boolean(fullName.value) && urlValid.value && !submitting.value)

const handleKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Escape' && !submitting.value) emit('close')
}

watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      reset()
      window.addEventListener('keydown', handleKeydown)
    } else {
      window.removeEventListener('keydown', handleKeydown)
    }
  },
)

onUnmounted(() => window.removeEventListener('keydown', handleKeydown))

async function submit() {
  if (!props.share || !canSubmit.value) return
  submitting.value = true
  try {
    const response = await axios.post(
      `/api/portal/mcp/shares/received/${props.share.id}/accept`,
      {
        server_name: fullName.value,
        remark: remark.value,
        sse_url: sseUrl.value.trim(),
      },
    )
    const data = response.data?.data || {}
    showToast(`已安装为 ${data.server?.server_name || fullName.value}`, 'success')
    emit('installed', String(data.accepted_server_id || ''))
    emit('close')
  } catch (error: any) {
    showToast(error.response?.data?.detail || '安装 MCP 失败', 'error')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="visible && share"
      class="fixed inset-0 z-[13000] flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
      @click.self="!submitting && emit('close')"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="mcp-accept-title"
        class="flex max-h-[90vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-gray-100 bg-white shadow-2xl dark:border-gray-800 dark:bg-gray-900"
      >
        <div class="flex items-start justify-between gap-3 border-b border-gray-100 px-5 py-4 dark:border-gray-800">
          <div class="min-w-0">
            <h2 id="mcp-accept-title" class="text-base font-bold text-gray-900 dark:text-gray-100">自定义安装</h2>
            <p class="mt-0.5 truncate text-xs text-gray-500 dark:text-gray-400">
              来自 {{ share.source_real_name || share.source_user_name || share.source_user_id }} 的分享「{{ share.server_name }}」
            </p>
          </div>
          <button
            type="button"
            class="rounded-lg p-1 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 dark:text-gray-500 dark:hover:bg-gray-700"
            title="关闭"
            @click="emit('close')"
          >
            <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div class="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
          <div>
            <label class="mb-1.5 block text-xs font-semibold text-gray-700 dark:text-gray-300">服务名</label>
            <div class="flex items-stretch overflow-hidden rounded-lg border border-gray-300 bg-white shadow-sm focus-within:ring-2 focus-within:ring-emerald-500 dark:border-gray-600 dark:bg-gray-900">
              <span class="flex max-w-[60%] shrink-0 items-center truncate border-r border-gray-200 bg-gray-50 px-2.5 text-[11px] font-mono text-gray-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-400">
                {{ namePrefix }}
              </span>
              <input
                v-model="nameSuffix"
                type="text"
                placeholder="hcp"
                class="h-9 min-w-0 flex-1 border-0 bg-transparent px-2.5 text-sm text-gray-800 placeholder:text-gray-400 focus:outline-none dark:text-gray-100"
              />
            </div>
            <p class="mt-1 text-[11px] text-gray-400 dark:text-gray-500">
              全名：
              <span class="font-mono text-gray-600 dark:text-gray-300">{{ fullName || '—' }}</span>
              <span v-if="!nameSuffix.trim()" class="ml-1 text-amber-600 dark:text-amber-400">请填写后缀</span>
            </p>
          </div>

          <div>
            <label class="mb-1.5 block text-xs font-semibold text-gray-700 dark:text-gray-300">备注</label>
            <textarea
              v-model="remark"
              rows="2"
              placeholder="给这条服务写一句说明，方便以后认出来"
              class="w-full resize-none rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
            ></textarea>
          </div>

          <div>
            <label class="mb-1.5 block text-xs font-semibold text-gray-700 dark:text-gray-300">SSE 地址</label>
            <input
              v-model="sseUrl"
              type="url"
              placeholder="https://example.com/mcp"
              class="h-9 w-full rounded-lg border border-gray-300 bg-white px-3 font-mono text-xs text-gray-800 shadow-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
            />
            <p class="mt-1 text-[11px]" :class="urlValid ? 'text-gray-400 dark:text-gray-500' : 'text-amber-600 dark:text-amber-400'">
              {{ urlValid ? '接受后可再改；凭证沿用分享内容。' : '需以 http:// 或 https:// 开头' }}
            </p>
          </div>

          <p
            v-if="!share.include_credentials"
            class="rounded-lg bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-700 dark:bg-amber-900/30 dark:text-amber-300"
          >
            这条分享未携带认证凭证，安装后需要到「我的 MCP」里补 Token 才能连接。
          </p>
        </div>

        <div class="flex items-center justify-end gap-2 border-t border-gray-100 px-5 py-3 dark:border-gray-800">
          <button
            type="button"
            class="rounded-lg px-3 py-1.5 text-sm text-gray-600 transition-colors hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
            @click="emit('close')"
          >
            取消
          </button>
          <button
            type="button"
            :disabled="!canSubmit"
            class="rounded-lg bg-emerald-600 px-3.5 py-1.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
            @click="submit"
          >
            {{ submitting ? '安装中…' : '安装' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
