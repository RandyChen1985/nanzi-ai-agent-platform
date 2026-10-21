<script setup lang="ts">
/**
 * 分享弹窗：只负责「选人 + 附言 + 发送」。
 *
 * 历史记录统一在「我的分享」里看，弹窗只给一个入口链接——两处都维护列表
 * 必然出现状态不同步，这里刻意只保留发送能力。
 */
import { computed, onUnmounted, ref, watch } from 'vue'
import axios from '@/utils/axios'
import { useToast } from '@/composables/useToast'

interface ShareSkill {
  id: string
  name: string
  share_count?: number | null
}

interface TargetUser {
  user_id: number
  user_name: string
  real_name?: string | null
  department?: string | null
}

const props = defineProps<{
  visible: boolean
  skill: ShareSkill | null
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'sent', payload: { created?: unknown[]; refreshed?: unknown[] }): void
  (e: 'view-sent'): void
}>()

const { showToast } = useToast()
const keyword = ref('')
const searching = ref(false)
const results = ref<TargetUser[]>([])
const selected = ref<TargetUser[]>([])
const message = ref('')
const sending = ref(false)
const searchError = ref('')
let timer: ReturnType<typeof setTimeout> | null = null
// 搜索请求序号：慢请求晚到时不覆盖快请求的结果
let searchSeq = 0

const handleKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Escape') emit('close')
}

const canSend = computed(() => selected.value.length > 0 && !sending.value)
const sharedCount = computed(() => Number(props.skill?.share_count || 0))

function reset() {
  keyword.value = ''
  results.value = []
  selected.value = []
  message.value = ''
  searchError.value = ''
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
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
  timer = setTimeout(() => runSearch(text), 300)
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
    const response = await axios.get('/api/portal/skills/personal/share-targets', {
      params: { keyword: text },
    })
    if (seq !== searchSeq) return
    const items = (response.data?.data || []) as TargetUser[]
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

function toggle(user: TargetUser) {
  const index = selected.value.findIndex((item) => item.user_id === user.user_id)
  if (index >= 0) {
    selected.value.splice(index, 1)
    return
  }
  selected.value.push(user)
  results.value = results.value.filter((item) => item.user_id !== user.user_id)
}

function remove(user: TargetUser) {
  selected.value = selected.value.filter((item) => item.user_id !== user.user_id)
}

function displayName(user: TargetUser): string {
  return user.real_name || user.user_name
}

async function submit() {
  if (!props.skill || !canSend.value) return
  sending.value = true
  try {
    const response = await axios.post(
      `/api/portal/skills/personal/${props.skill.id}/shares`,
      {
        target_user_ids: selected.value.map((user) => user.user_id),
        message: message.value.trim() || undefined,
      },
    )
    const data = response.data?.data || {}
    const count = (data.created?.length || 0) + (data.refreshed?.length || 0)
    showToast(`已分享给 ${count} 位同事`, 'success')
    emit('sent', data)
    emit('close')
  } catch (error: any) {
    showToast(error.response?.data?.detail || '分享技能失败', 'error')
  } finally {
    sending.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="visible && skill"
      class="fixed inset-0 z-[13000] flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
      @click.self="emit('close')"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="skill-share-title"
        class="flex max-h-[90vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-gray-100 bg-white shadow-2xl dark:border-gray-800 dark:bg-gray-900"
      >
        <div class="flex items-start justify-between gap-3 border-b border-gray-100 px-5 py-4 dark:border-gray-800">
          <div class="min-w-0">
            <h2 id="skill-share-title" class="text-base font-bold text-gray-900 dark:text-gray-100">分享技能给同事</h2>
            <p class="mt-0.5 truncate text-xs text-gray-500 dark:text-gray-400">
              {{ skill.name }} <span class="font-mono text-[10px] text-gray-400 dark:text-gray-500">（{{ skill.id }}）</span>
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
                class="w-full rounded-lg border border-gray-300 bg-white py-2 pl-9 pr-3 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-900"
              />
            </div>

            <div v-if="selected.length" class="mt-2 flex flex-wrap gap-1.5">
              <span
                v-for="user in selected"
                :key="user.user_id"
                class="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 dark:bg-blue-900/40 dark:text-blue-300"
              >
                {{ displayName(user) }}
                <button type="button" class="text-blue-400 hover:text-blue-600 dark:hover:text-blue-400" title="移除" @click="remove(user)">
                  <svg class="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </span>
            </div>

            <div v-if="keyword.trim()" class="mt-2 max-h-44 overflow-y-auto rounded-lg border border-gray-200 dark:border-gray-700">
              <div v-if="searching" class="px-3 py-3 text-xs text-gray-400 dark:text-gray-500">搜索中...</div>
              <div v-else-if="searchError" class="px-3 py-3 text-xs text-red-500">{{ searchError }}</div>
              <div v-else-if="!results.length" class="px-3 py-3 text-xs text-gray-400 dark:text-gray-500">没有匹配的用户</div>
              <template v-else>
                <button
                  v-for="user in results"
                  :key="user.user_id"
                  type="button"
                  class="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-gray-50 dark:hover:bg-gray-800"
                  @click="toggle(user)"
                >
                  <span class="min-w-0">
                    <span class="font-medium text-gray-800 dark:text-gray-200">{{ displayName(user) }}</span>
                    <span class="ml-1 text-xs text-gray-400 dark:text-gray-500">@{{ user.user_name }}</span>
                  </span>
                  <span class="shrink-0 text-[11px] text-gray-400 dark:text-gray-500">{{ user.department || '' }}</span>
                </button>
              </template>
            </div>
          </div>

          <div>
            <label class="mb-1.5 block text-xs font-semibold text-gray-700 dark:text-gray-300">
              附言 <span class="font-normal text-gray-400 dark:text-gray-500">（可选）</span>
            </label>
            <textarea
              v-model="message"
              maxlength="200"
              rows="3"
              placeholder="说一句用途，比如：这个技能用来生成周报，你可以试试"
              class="w-full resize-none rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-900"
            />
            <div class="mt-1 text-right text-[11px] text-gray-400 dark:text-gray-500">{{ message.length }}/200</div>
          </div>
        </div>

        <div class="flex items-center justify-between gap-3 border-t border-gray-100 bg-gray-50/60 px-5 py-3 dark:border-gray-800 dark:bg-gray-800/60">
          <button
            type="button"
            class="inline-flex items-center gap-1 text-xs font-medium text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"
            @click="emit('view-sent')"
          >
            已分享给 {{ sharedCount }} 人，查看我的分享 →
          </button>
          <div class="flex items-center gap-2">
            <button
              type="button"
              class="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs text-gray-600 transition-colors hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300 dark:hover:bg-gray-800"
              @click="emit('close')"
            >
              取消
            </button>
            <button
              type="button"
              class="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
              :disabled="!canSend"
              @click="submit"
            >
              {{ sending ? '发送中...' : '发送分享' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>
