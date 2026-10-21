<script setup lang="ts">
/**
 * 分享个人 MCP：搜索同事、多选、附言，并显式选择是否携带认证凭证。
 *
 * 与技能分享的差别就在凭证：MCP 没有 Token 基本连不上，所以默认携带；
 * 但携带等于把对方的访问权交出去，弹窗必须把这件事说清楚，也允许关掉。
 */
import { computed, onUnmounted, ref, watch } from 'vue'
import axios from '@/utils/axios'
import { useToast } from '@/composables/useToast'

interface ShareTarget {
  user_id: number
  user_name: string
  real_name: string | null
  department: string | null
}

const props = defineProps<{
  visible: boolean
  server: { id: string; server_name: string; sse_url: string } | null
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'sent'): void
}>()

const MAX_MESSAGE = 200
const { showToast } = useToast()

const keyword = ref('')
const results = ref<ShareTarget[]>([])
const selected = ref<ShareTarget[]>([])
const searching = ref(false)
const searchError = ref('')
const message = ref('')
const includeCredentials = ref(true)
const submitting = ref(false)
let timer: ReturnType<typeof setTimeout> | null = null
let searchSeq = 0

const canSubmit = computed(() => selected.value.length > 0 && !submitting.value)
const remaining = computed(() => MAX_MESSAGE - message.value.length)

/**
 * 地址里是否内嵌了访问令牌。
 *
 * ModelScope 这类托管网关把 key 编进路径（.../e260f2cad7f443/mcp），地址本身就是凭据；
 * 此时「取消勾选携带凭证」并不能保护对方，必须提前告诉分享人。
 */
const urlEmbedsCredential = computed(() => {
  const url = String(props.server?.sse_url || '')
  if (!url) return false
  const withoutOrigin = url.replace(/^[a-z][a-z0-9+.-]*:\/\/[^/]+/i, '')
  const [pathPart, queryPart = ''] = withoutOrigin.split('?')
  const looksSecret = (segment: string) =>
    /^[0-9a-f]{12,}$/i.test(segment) || /^[A-Za-z0-9_-]{24,}$/.test(segment)
  if (pathPart.split('/').filter(Boolean).some(looksSecret)) return true
  const params = new URLSearchParams(queryPart)
  for (const key of ['token', 'access_token', 'key', 'apikey', 'api_key', 'sig', 'signature']) {
    if (params.get(key)) return true
  }
  return false
})

function reset() {
  keyword.value = ''
  results.value = []
  selected.value = []
  searchError.value = ''
  message.value = ''
  includeCredentials.value = true
  submitting.value = false
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
}

const handleKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Escape') emit('close')
}

watch(() => props.visible, (visible) => {
  if (visible) {
    reset()
    window.addEventListener('keydown', handleKeydown)
  } else {
    window.removeEventListener('keydown', handleKeydown)
  }
})

watch(keyword, (value) => {
  if (timer) clearTimeout(timer)
  const text = value.trim()
  if (!text) {
    results.value = []
    return
  }
  timer = setTimeout(() => void runSearch(text), 300)
})

onUnmounted(() => {
  if (timer) clearTimeout(timer)
  window.removeEventListener('keydown', handleKeydown)
})

async function runSearch(text: string) {
  const seq = ++searchSeq
  searching.value = true
  searchError.value = ''
  try {
    const response = await axios.get('/api/portal/mcp/share-targets', {
      params: { keyword: text },
    })
    if (seq !== searchSeq) return
    const items = (response.data?.data || []) as ShareTarget[]
    results.value = items.filter(
      (user) => !selected.value.some((item) => item.user_id === user.user_id),
    )
  } catch (error: any) {
    if (seq !== searchSeq) return
    searchError.value = error.response?.data?.detail || '搜索用户失败'
  } finally {
    if (seq === searchSeq) searching.value = false
  }
}

function toggle(user: ShareTarget) {
  const index = selected.value.findIndex((item) => item.user_id === user.user_id)
  if (index >= 0) {
    selected.value.splice(index, 1)
    return
  }
  selected.value.push(user)
  results.value = results.value.filter((item) => item.user_id !== user.user_id)
}

function remove(user: ShareTarget) {
  selected.value = selected.value.filter((item) => item.user_id !== user.user_id)
}

function displayName(user: ShareTarget): string {
  return user.real_name || user.user_name
}

async function submit() {
  if (!props.server || !canSubmit.value) return
  submitting.value = true
  try {
    const response = await axios.post(`/api/portal/mcp/servers/${props.server.id}/shares`, {
      target_user_ids: selected.value.map((item) => item.user_id),
      message: message.value.trim() || null,
      include_credentials: includeCredentials.value,
    })
    const data = response.data?.data || {}
    const count = (data.created?.length || 0) + (data.refreshed?.length || 0)
    showToast(`已分享给 ${count} 人`, 'success')
    selected.value = []
    emit('sent')
    emit('close')
  } catch (error: any) {
    showToast(error.response?.data?.detail || '分享 MCP 失败', 'error')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="visible && server"
      class="fixed inset-0 z-[13000] flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
      @click.self="emit('close')"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="mcp-share-title"
        class="flex max-h-[90vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-gray-100 bg-white shadow-2xl dark:border-gray-800 dark:bg-gray-900"
      >
        <div class="flex items-start justify-between gap-3 border-b border-gray-100 px-5 py-4 dark:border-gray-800">
          <div class="min-w-0">
            <h2 id="mcp-share-title" class="text-base font-bold text-gray-900 dark:text-gray-100">分享 MCP 给同事</h2>
            <p class="mt-0.5 truncate text-xs text-gray-500 dark:text-gray-400">
              {{ server.server_name }}
              <span class="font-mono text-[10px] text-gray-400 dark:text-gray-500">（{{ server.sse_url }}）</span>
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
            <label class="mb-1.5 block text-xs font-semibold text-gray-700 dark:text-gray-300">接收人</label>
            <div class="relative">
              <span class="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3">
                <svg class="h-4 w-4 text-gray-400 dark:text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
              </span>
              <input
                v-model="keyword"
                type="search"
                placeholder="搜索姓名或用户名"
                class="w-full rounded-lg border border-gray-300 bg-white py-2 pl-9 pr-3 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
              />
            </div>

            <div v-if="selected.length" class="mt-2 flex flex-wrap gap-1.5">
              <span
                v-for="user in selected"
                :key="user.user_id"
                class="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300"
              >
                {{ displayName(user) }}
                <button type="button" class="text-emerald-400 hover:text-emerald-600 dark:hover:text-emerald-400" title="移除" @click="remove(user)">
                  <svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </span>
            </div>

            <div
              v-if="searching || results.length || searchError"
              class="mt-2 max-h-44 overflow-y-auto rounded-lg border border-gray-200 dark:border-gray-700"
            >
              <p v-if="searching" class="px-3 py-2 text-xs text-gray-400 dark:text-gray-500">搜索中…</p>
              <p v-else-if="searchError" class="px-3 py-2 text-xs text-red-600 dark:text-red-400">{{ searchError }}</p>
              <p v-else-if="!results.length" class="px-3 py-2 text-xs text-gray-400 dark:text-gray-500">没有匹配的同事</p>
              <button
                v-for="user in results"
                :key="user.user_id"
                type="button"
                class="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm text-gray-700 transition-colors hover:bg-emerald-50 dark:text-gray-200 dark:hover:bg-emerald-900/30"
                @click="toggle(user)"
              >
                <span class="min-w-0 truncate">
                  <span class="font-medium text-gray-800 dark:text-gray-200">{{ user.real_name || user.user_name }}</span>
                  <span
                    v-if="user.real_name && user.user_name && user.real_name !== user.user_name"
                    class="ml-1 text-[11px] text-gray-400 dark:text-gray-500"
                  >@{{ user.user_name }}</span>
                </span>
                <span class="shrink-0 text-[11px] text-gray-400 dark:text-gray-500">
                  {{ user.department || '' }}
                </span>
              </button>
            </div>
          </div>

          <div class="rounded-xl border border-gray-200 p-3 dark:border-gray-700">
            <label class="flex cursor-pointer items-start gap-2.5">
              <input
                v-model="includeCredentials"
                type="checkbox"
                class="mt-0.5 h-4 w-4 shrink-0 rounded border-gray-300 text-emerald-600 focus:ring-emerald-500 dark:border-gray-600 dark:bg-gray-800"
              />
              <span class="min-w-0">
                <span class="block text-xs font-semibold text-gray-700 dark:text-gray-200">携带认证凭证</span>
                <span class="mt-0.5 block text-[11px] leading-relaxed text-gray-500 dark:text-gray-400">
                  <template v-if="includeCredentials">
                    对方接受后会同时拿到该服务的 Authorization / 固定 Token / 用户身份签名私钥，可凭此直接访问该 MCP，请只分享给可信同事。
                  </template>
                  <template v-else>
                    对方只得到连接配置与工具清单，需要在「我的 MCP」里自行填写 Token 才能连接。
                  </template>
                </span>
              </span>
            </label>
            <p
              v-if="urlEmbedsCredential"
              class="mt-2 flex items-start gap-1.5 rounded-lg bg-amber-50 px-2.5 py-2 text-[11px] leading-relaxed text-amber-700 dark:bg-amber-900/30 dark:text-amber-300"
            >
              <svg class="mt-0.5 h-3.5 w-3.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
              </svg>
              <span>
                这个地址本身含访问令牌（托管网关常把 key 编在路径里），
                <strong>取消勾选「携带认证凭证」也无法隐藏它</strong>；请只分享给可信同事。
              </span>
            </p>
          </div>

          <div>
            <label class="mb-1.5 flex items-center justify-between text-xs font-semibold text-gray-700 dark:text-gray-300">
              <span>附言（可选）</span>
              <span class="font-normal text-gray-400 dark:text-gray-500">{{ remaining }}</span>
            </label>
            <textarea
              v-model="message"
              :maxlength="MAX_MESSAGE"
              rows="3"
              placeholder="比如：这个 MCP 我正在用，工具挺全的"
              class="w-full resize-none rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
            ></textarea>
          </div>
        </div>

        <div class="flex items-center justify-between gap-3 border-t border-gray-100 px-5 py-3 dark:border-gray-800">
          <span class="text-[11px] text-gray-400 dark:text-gray-500">
            对方接受后会安装到他的「我的 MCP」
          </span>
          <div class="flex items-center gap-2">
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
              {{ submitting ? '分享中…' : '分享' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>
