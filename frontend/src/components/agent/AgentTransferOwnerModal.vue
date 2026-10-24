<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { AIAgent } from '../../api/agent'
import { agentSystemApi } from '../../api/agent'

const props = defineProps<{
  agent: AIAgent | null
  show: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'done', dropped: Array<{ name: string; reason_label?: string }>): void
}>()

interface CandidateUser {
  user_name: string
  real_name?: string | null
}

const search = ref('')
const candidates = ref<CandidateUser[]>([])
const selected = ref<CandidateUser | null>(null)
const reason = ref('')
const loadingUsers = ref(false)
const submitting = ref(false)
const errorMessage = ref('')

const currentOwner = computed(() => (props.agent?.created_by || '').trim())

const canSubmit = computed(
  () =>
    !!selected.value &&
    selected.value.user_name !== currentOwner.value &&
    !submitting.value,
)

const reset = () => {
  search.value = ''
  candidates.value = []
  selected.value = null
  reason.value = ''
  errorMessage.value = ''
  submitting.value = false
}

let searchTimer: ReturnType<typeof setTimeout> | null = null

const onSearchInput = () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    void loadUsers()
  }, 250)
}

watch(
  () => props.show,
  (visible) => {
    if (visible) {
      reset()
      void loadUsers()
    }
  },
)

const loadUsers = async () => {
  loadingUsers.value = true
  errorMessage.value = ''
  try {
    const res = await agentSystemApi.transferOwnerCandidates({
      search: search.value.trim() || undefined,
      limit: 50,
    })
    // 后端只返回启用账号，前端不再二次过滤（过去在界面上过滤，反而把
    // 「接口失败」和「确实没人」显示成同一句「没有匹配的启用用户」）
    candidates.value = (res.data || []).map((item) => ({
      user_name: item.user_name,
      real_name: item.real_name ?? null,
    }))
  } catch (error: any) {
    candidates.value = []
    errorMessage.value =
      error.response?.data?.detail || '候选人加载失败，请稍后重试'
  } finally {
    loadingUsers.value = false
  }
}

const choose = (user: CandidateUser) => {
  selected.value = user
  errorMessage.value = ''
}

const submit = async () => {
  if (!props.agent || !selected.value || submitting.value) return
  if (selected.value.user_name === currentOwner.value) {
    errorMessage.value = '该用户已经是这个智能体的权属人'
    return
  }

  submitting.value = true
  errorMessage.value = ''
  try {
    const res = await agentSystemApi.transferOwner(props.agent.id, {
      new_owner: selected.value.user_name,
      reason: reason.value.trim() || undefined,
    })
    // 后端按新权属人的视角体检过工具：换人之后可能有引用他用不了，
    // 这份清单必须转达，否则新同事接手时只会看到「配置都在、就是跑不起来」
    emit('done', (res.data?.dropped_tool_refs || []) as Array<{ name: string; reason_label?: string }>)
  } catch (error: any) {
    errorMessage.value =
      error.response?.data?.detail ||
      error.response?.data?.message ||
      '变更失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="show && agent"
      class="fixed inset-0 z-[100] flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-sm"
      @click.self="emit('close')"
    >
      <div class="flex w-full max-w-lg flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
        <div class="border-b border-gray-100 px-6 py-4">
          <h3 class="text-base font-bold text-gray-900">变更权属人</h3>
          <p class="mt-1 truncate text-xs text-gray-500">
            {{ agent.display_name }}
            <span class="text-gray-400">· 当前权属人 {{ currentOwner || '未指定' }}</span>
          </p>
        </div>

        <div class="space-y-4 px-6 py-5">
          <div class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-800">
            转交后，新权属人可以编辑配置、创建版本；系统级智能体的发布仍需管理员审核。
            变更会记入发布历史，并给新权属人发送站内信。
          </div>

          <div>
            <label class="mb-1.5 block text-xs font-bold text-gray-800">新的权属人</label>
            <div class="flex gap-2">
              <input
                v-model="search"
                type="search"
                placeholder="搜索用户名或姓名"
                class="min-w-0 flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                @input="onSearchInput"
                @keyup.enter="loadUsers"
              />
              <button
                type="button"
                class="shrink-0 rounded-lg border border-gray-300 px-3 py-2 text-xs font-bold text-gray-700 transition-colors hover:bg-gray-50 cursor-pointer disabled:opacity-50"
                :disabled="loadingUsers"
                @click="loadUsers"
              >
                {{ loadingUsers ? '搜索中…' : '搜索' }}
              </button>
            </div>

            <div class="mt-2 max-h-56 overflow-y-auto rounded-lg border border-gray-200">
              <button
                v-for="user in candidates"
                :key="user.user_name"
                type="button"
                class="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs transition-colors hover:bg-gray-50 cursor-pointer"
                :class="selected?.user_name === user.user_name ? 'bg-blue-50' : ''"
                @click="choose(user)"
              >
                <span class="min-w-0">
                  <span class="block truncate font-bold text-gray-800">{{ user.real_name || user.user_name }}</span>
                  <span class="block truncate text-gray-500">{{ user.user_name }}</span>
                </span>
                <span
                  v-if="user.user_name === currentOwner"
                  class="shrink-0 rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-bold text-gray-500"
                >
                  当前权属人
                </span>
                <svg
                  v-else-if="selected?.user_name === user.user_name"
                  class="h-4 w-4 shrink-0 text-blue-600"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7" />
                </svg>
              </button>
              <p v-if="!candidates.length" class="px-3 py-6 text-center text-xs text-gray-400">
                {{ loadingUsers ? '加载中…' : (errorMessage || '没有匹配的启用用户') }}
              </p>
            </div>
          </div>

          <div>
            <label class="mb-1.5 block text-xs font-bold text-gray-800">
              变更原因 <span class="font-normal text-gray-400">（可选，会记入发布历史）</span>
            </label>
            <textarea
              v-model="reason"
              rows="2"
              maxlength="200"
              placeholder="例如：原负责人已离职，转交给运维组"
              class="w-full resize-none rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            ></textarea>
          </div>

          <p v-if="errorMessage" class="text-xs font-medium text-red-600">{{ errorMessage }}</p>
        </div>

        <div class="flex items-center justify-end gap-2 border-t border-gray-100 bg-gray-50 px-6 py-3">
          <button
            type="button"
            class="rounded-lg px-4 py-2 text-xs font-bold text-gray-600 transition-colors hover:bg-gray-200 cursor-pointer"
            @click="emit('close')"
          >
            取消
          </button>
          <button
            type="button"
            class="rounded-lg bg-blue-600 px-4 py-2 text-xs font-bold text-white transition-colors hover:bg-blue-700 cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="!canSubmit"
            @click="submit"
          >
            {{ submitting ? '变更中…' : '确认变更' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
