<script setup lang="ts">
/**
 * 下架系统级智能体。
 *
 * 三件事必须让操作者看见：影响多少人、要不要通知、为什么下架。
 * - 人数口径是「实际能用的人」（用户直配 ∪ 角色成员），不是授权行数；
 * - 通知默认勾选，取消时明确告诉他会发生什么（用户不知道就不能用）；
 * - 下架原因必填，事件表会连同受影响人数、是否通知一起留痕。
 */
import { ref, watch } from 'vue'
import type { AIAgent, AgentSystemUnpublishPreview } from '../../api/agent'
import { agentSystemApi } from '../../api/agent'
import { useToast } from '@/composables/useToast'

const props = defineProps<{
  isOpen: boolean
  agent: AIAgent | null
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'done'): void
}>()

const { showToast } = useToast()
const reason = ref('')
const notify = ref(true)
const submitting = ref(false)
const errorMessage = ref('')
const preview = ref<AgentSystemUnpublishPreview | null>(null)
const loadingPreview = ref(false)

const loadPreview = async () => {
  if (!props.agent) return
  loadingPreview.value = true
  try {
    const res = await agentSystemApi.previewUnpublish(props.agent.id)
    preview.value = res.data
    // 没有任何使用者时通知开关没有意义，直接关掉
    if (!res.data.affected_user_count) notify.value = false
  } catch (error: any) {
    errorMessage.value = error?.response?.data?.detail || '加载受影响用户失败'
  } finally {
    loadingPreview.value = false
  }
}

watch(
  () => props.isOpen,
  (open) => {
    if (!open) return
    reason.value = ''
    notify.value = true
    errorMessage.value = ''
    preview.value = null
    submitting.value = false
    void loadPreview()
  },
)

const submit = async () => {
  if (!props.agent || submitting.value) return
  if (!reason.value.trim()) {
    errorMessage.value = '下架原因不能为空'
    return
  }
  submitting.value = true
  errorMessage.value = ''
  try {
    const res = await agentSystemApi.unpublish(props.agent.id, {
      reason: reason.value.trim(),
      notify: notify.value,
    })
    const notified = res.data.notified_user_count
    showToast(
      notify.value && notified
        ? `已下架，并通知了 ${notified} 位用户`
        : '已下架',
      'success',
    )
    emit('done')
    emit('close')
  } catch (error: any) {
    errorMessage.value = error?.response?.data?.detail || '下架失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="isOpen"
      class="fixed inset-0 z-[100] flex items-center justify-center bg-slate-900/40 p-4"
      @click.self="emit('close')"
    >
      <div class="w-full max-w-lg rounded-xl bg-white shadow-2xl">
        <div class="flex items-center gap-2 px-5 pb-2 pt-4">
          <h3 class="text-base font-semibold text-slate-900">
            下架「{{ agent?.display_name }}」
          </h3>
          <button
            class="ml-auto rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
            @click="emit('close')"
          >
            ✕
          </button>
        </div>

        <div class="space-y-3 px-5 pb-4">
          <div class="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs leading-relaxed text-red-800">
            ⚠ 下架后它<strong>不再是系统级</strong>：将从自动委派、路由与所有使用者的对话入口消失，
            智能体归还给所有者并可继续编辑。授权记录会保留，重新上架后自动恢复。
          </div>

          <div class="space-y-1.5">
            <p class="text-xs font-semibold text-slate-700">
              <template v-if="loadingPreview">正在统计受影响用户…</template>
              <template v-else-if="preview">
                已分配给 {{ preview.affected_user_count }} 位用户
              </template>
              <template v-else>受影响用户</template>
            </p>

            <div v-if="preview && preview.sample_user_names.length" class="flex flex-wrap gap-1.5">
              <span
                v-for="name in preview.sample_user_names"
                :key="name"
                class="rounded-md border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] text-slate-600"
              >
                @{{ name }}
              </span>
              <span
                v-if="preview.affected_user_count > preview.sample_user_names.length"
                class="px-1 text-[11px] text-slate-400"
              >
                …共 {{ preview.affected_user_count }} 人
              </span>
            </div>
            <p v-else-if="preview && !preview.affected_user_count" class="text-[11px] text-slate-400">
              目前还没有被分配给任何人。
            </p>
            <p class="text-[11px] text-slate-400">
              含通过角色继承权限的成员。
            </p>
          </div>

          <div class="space-y-1.5">
            <label class="text-xs font-semibold text-slate-700">
              下架原因 <span class="text-red-500">*</span>
            </label>
            <textarea
              v-model="reason"
              rows="3"
              maxlength="500"
              class="w-full resize-y rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/10"
              placeholder="例如：业务已迁移到「合同智能审核 2.0」"
            ></textarea>
          </div>

          <label
            v-if="preview && preview.affected_user_count > 0"
            class="flex cursor-pointer items-start gap-2"
          >
            <input v-model="notify" type="checkbox" class="mt-0.5 h-4 w-4 accent-primary" />
            <span class="text-xs">
              <span class="font-semibold text-slate-700">
                通知这 {{ preview.affected_user_count }} 位用户
              </span>
              <span v-if="notify" class="mt-0.5 block text-[11px] text-slate-400">
                下架后他们会在站内信收到：「智能体「{{ agent?.display_name }}」已下架，
                你暂时无法使用」。
              </span>
              <span v-else class="mt-0.5 block text-[11px] text-amber-600">
                取消后用户不会收到任何通知，可能不知道无法使用的原因。该选择会被记录，
                重新上架时也不会通知「已恢复」。
              </span>
            </span>
          </label>

          <p v-if="errorMessage" class="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">
            {{ errorMessage }}
          </p>
        </div>

        <div class="flex justify-end gap-2 border-t border-slate-100 bg-slate-50 px-5 py-3">
          <button
            class="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm text-slate-700 hover:bg-slate-100"
            @click="emit('close')"
          >
            取消
          </button>
          <button
            class="rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-50"
            :disabled="submitting || loadingPreview"
            @click="submit"
          >
            {{ submitting ? '下架中…' : '确认下架' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
