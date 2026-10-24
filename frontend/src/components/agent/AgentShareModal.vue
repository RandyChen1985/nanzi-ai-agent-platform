<script setup lang="ts">
/**
 * 分享智能体：把自己的智能体**发一份副本**给同事。
 *
 * 语义要点（界面必须说清楚，否则容易被理解成「授权共用」）：
 * - 对方接受后得到的是他自己的副本，需要重新命名并走向导确认；
 * - 源智能体不受影响，也不会因为分享变成共用；
 * - 对方无权使用的工具 / 技能会在复制时自动剔除。
 *
 * 系统级智能体不提供分享入口（父组件不渲染本弹窗），后端也会拒绝。
 */
import { computed, onUnmounted, ref, watch } from 'vue'
import type { AIAgent } from '../../api/agent'
import { agentSystemApi } from '../../api/agent'

const props = defineProps<{
  agent: AIAgent | null
  show: boolean
  /** 候选用户（启用中的同事，来自分享专用候选接口） */
  candidates: Array<{ user_name: string; real_name?: string | null }>
  loadingCandidates?: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'done', count: number): void
  (e: 'search', keyword: string): void
}>()

/** 当前登录人：和智能体中心一样读 localStorage 缓存，避免引入额外的 store 依赖 */
const currentUserName = (): string => {
  try {
    const cached = localStorage.getItem('user_info')
    if (!cached) return ''
    return String(JSON.parse(cached)?.user_name || '').trim()
  } catch {
    return ''
  }
}

const search = ref('')
const picked = ref<Array<{ user_name: string; real_name?: string | null }>>([])
const message = ref('')
const submitting = ref(false)
const errorMessage = ref('')
/** 候选人搜索/加载的失败，与提交失败分开显示，避免底部和空态重复同一句话 */
const searchError = computed(() =>
  props.loadingCandidates ? '' : errorMessage.value.includes('分享失败') ? '' : errorMessage.value,
)

const MAX_RECIPIENTS = 20

const available = computed(() => {
  const currentOwner = (props.agent?.created_by || '').trim()
  // 也要排除当前登录人：管理员代分享别人的智能体时，能把自己选进去，
  // 提交后后端才报「不能分享给自己」
  const currentUser = currentUserName()
  const pickedNames = new Set(picked.value.map((item) => item.user_name))
  return (props.candidates || []).filter(
    (item) =>
      item.user_name !== currentOwner &&
      item.user_name !== currentUser &&
      !pickedNames.has(item.user_name),
  )
})

const canSubmit = computed(
  () => picked.value.length > 0 && !submitting.value && picked.value.length <= MAX_RECIPIENTS,
)

const reset = () => {
  search.value = ''
  picked.value = []
  message.value = ''
  errorMessage.value = ''
  submitting.value = false
}

watch(
  () => props.show,
  (visible) => {
    if (!visible && searchTimer) {
      clearTimeout(searchTimer)
      searchTimer = null
    }
    if (visible) reset()
  },
)

onUnmounted(() => {
  if (searchTimer) clearTimeout(searchTimer)
})

let searchTimer: ReturnType<typeof setTimeout> | null = null
const onSearchInput = () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => emit('search', search.value.trim()), 250)
}

const add = (user: { user_name: string; real_name?: string | null }) => {
  if (picked.value.length >= MAX_RECIPIENTS) return
  picked.value = [...picked.value, user]
  search.value = ''
  emit('search', '')
}

const remove = (userName: string) => {
  picked.value = picked.value.filter((item) => item.user_name !== userName)
}

const submit = async () => {
  if (!props.agent || !canSubmit.value) return
  submitting.value = true
  errorMessage.value = ''
  try {
    const res = await agentSystemApi.shareAgent(props.agent.id, {
      recipient_user_names: picked.value.map((item) => item.user_name),
      message: message.value.trim() || undefined,
    })
    emit('done', res.data.created)
  } catch (e: any) {
    errorMessage.value = e?.response?.data?.detail || '分享失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="fixed inset-0 z-[120] flex items-center justify-center p-4">
      <div class="absolute inset-0 bg-black/40" @click="emit('close')"></div>

      <div class="relative w-full max-w-lg rounded-xl bg-white shadow-xl">
        <div class="flex items-center justify-between border-b border-gray-100 px-5 py-4">
          <div>
            <h3 class="text-base font-semibold text-gray-900">分享智能体</h3>
            <p class="mt-0.5 text-xs text-gray-500">
              {{ agent?.display_name || agent?.name }}
            </p>
          </div>
          <button
            type="button"
            class="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
            @click="emit('close')"
          >
            <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div class="space-y-4 px-5 py-4">
          <div class="rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-xs leading-relaxed text-blue-900">
            <strong>对方接受后会得到一份属于他自己的副本</strong><br />
            需要重新命名并走向导确认配置；你的这个智能体不受影响，也不会变成共用。<br />
            对方无权使用的工具与技能会在复制时自动剔除。
          </div>

          <div>
            <label class="mb-1.5 block text-xs font-medium text-gray-700">
              接收人 <span class="text-gray-400">（最多 {{ MAX_RECIPIENTS }} 人）</span>
            </label>

            <div v-if="picked.length" class="mb-2 flex flex-wrap gap-1.5">
              <span
                v-for="user in picked"
                :key="user.user_name"
                class="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-1 text-xs text-primary"
              >
                {{ user.real_name || user.user_name }}
                <button
                  type="button"
                  class="text-primary/60 hover:text-primary"
                  @click="remove(user.user_name)"
                >
                  ×
                </button>
              </span>
            </div>

            <input
              v-model="search"
              type="search"
              placeholder="搜索用户名或姓名"
              class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              @input="onSearchInput"
            />

            <div class="mt-1.5 max-h-40 overflow-y-auto rounded-lg border border-gray-200">
              <div v-if="loadingCandidates" class="px-3 py-4 text-center text-xs text-gray-400">
                正在加载…
              </div>
              <div
                v-else-if="!available.length"
                class="px-3 py-4 text-center text-xs text-gray-400"
              >
                {{ searchError || '没有匹配的启用用户' }}
              </div>
              <button
                v-for="user in available"
                :key="user.user_name"
                type="button"
                class="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-gray-50"
                @click="add(user)"
              >
                <span class="text-gray-800">{{ user.real_name || user.user_name }}</span>
                <span class="text-xs text-gray-400">{{ user.user_name }}</span>
              </button>
            </div>
          </div>

          <div>
            <label class="mb-1.5 block text-xs font-medium text-gray-700">
              留言 <span class="text-gray-400">（可选）</span>
            </label>
            <textarea
              v-model="message"
              rows="2"
              maxlength="500"
              placeholder="说明一下这个智能体是做什么的"
              class="w-full resize-none rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            ></textarea>
          </div>

          <p v-if="errorMessage" class="text-xs text-red-600">{{ errorMessage }}</p>
        </div>

        <div class="flex items-center justify-end gap-2 border-t border-gray-100 px-5 py-3">
          <button
            type="button"
            class="rounded-lg border border-gray-300 px-3.5 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
            @click="emit('close')"
          >
            取消
          </button>
          <button
            type="button"
            :disabled="!canSubmit"
            class="rounded-lg bg-primary px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-primary-dark disabled:cursor-not-allowed disabled:opacity-50"
            @click="submit"
          >
            {{ submitting ? '分享中…' : `分享给 ${picked.length} 人` }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
