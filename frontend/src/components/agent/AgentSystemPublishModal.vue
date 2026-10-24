<script setup lang="ts">
/**
 * 发布为系统级 / 申请发布为系统级。
 *
 * 两个入口共用同一个弹窗，只是文案与调用的接口不同：
 * - publish：持审核权限的人直接设状态，立即生效；
 * - apply：所有者提交申请，提交即快照并冻结内容，等管理员审核。
 *
 * 刻意**没有版本选择**：发布只改 is_system，运行时用哪个版本由既有的
 * 「发布版本」流程决定，这里不做第二次决定。
 */
import { computed, ref, watch } from 'vue'
import type { AIAgent } from '../../api/agent'
import { agentSystemApi } from '../../api/agent'
import { useToast } from '@/composables/useToast'

const props = defineProps<{
  isOpen: boolean
  agent: AIAgent | null
  mode: 'publish' | 'apply'
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'done'): void
}>()

const { showToast } = useToast()
const reason = ref('')
const submitting = ref(false)
const errorMessage = ref('')

const isPublish = computed(() => props.mode === 'publish')
const title = computed(() => (isPublish.value ? '发布为系统级' : '申请发布为系统级'))
const reasonLabel = computed(() => (isPublish.value ? '发布说明' : '申请理由'))

watch(
  () => props.isOpen,
  (open) => {
    if (open) {
      reason.value = ''
      errorMessage.value = ''
      submitting.value = false
    }
  },
)

const submit = async () => {
  if (!props.agent || submitting.value) return
  submitting.value = true
  errorMessage.value = ''
  const note = reason.value.trim() || undefined
  try {
    if (isPublish.value) {
      await agentSystemApi.publish(props.agent.id, { reason: note })
      showToast('已发布为系统级', 'success')
    } else {
      await agentSystemApi.submitRequest(props.agent.id, { comment: note })
      showToast('已提交发布申请，内容已冻结', 'success')
    }
    emit('done')
    emit('close')
  } catch (error: any) {
    errorMessage.value = error?.response?.data?.detail || '操作失败，请稍后重试'
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
          <h3 class="text-base font-semibold text-slate-900">{{ title }}</h3>
          <button
            class="ml-auto rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
            @click="emit('close')"
          >
            ✕
          </button>
        </div>

        <div class="space-y-3 px-5 pb-4">
          <div class="rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-xs leading-relaxed text-blue-900">
            <template v-if="isPublish">
              <strong>「{{ agent?.display_name }}」将立即成为平台系统级智能体</strong><br />
              它会进入主助手的自动委派候选与路由，被授权的用户可直接使用。
            </template>
            <template v-else>
              <strong>提交后内容将被冻结</strong><br />
              审核期间不能编辑、建版本或发布。如需继续开发，请先「复制智能体」另开一条。
            </template>
          </div>

          <div
            v-if="isPublish"
            class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-900"
          >
            ⚠ 发布后该智能体对所有者变为<strong>只读</strong>（只能复制另开一条继续开发），
            直到下架为止。系统会自动把所有者加入可用名单。
          </div>

          <div class="space-y-1.5">
            <label class="text-xs font-semibold text-slate-700">
              {{ reasonLabel }}<span class="ml-1 font-normal text-slate-400">可选</span>
            </label>
            <textarea
              v-model="reason"
              rows="3"
              maxlength="500"
              class="w-full resize-y rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/10"
              :placeholder="
                isPublish
                  ? '例如：面向全公司数据组，替代人工清洗流程'
                  : '例如：团队每周都要手写周报，这个智能体已稳定使用 2 个月'
              "
            ></textarea>
            <p class="text-[11px] text-slate-400">
              会记录到发布历史{{ isPublish ? '' : '，审核人会看到这段说明' }}。
            </p>
          </div>

          <div class="space-y-1">
            <p class="text-xs font-semibold text-slate-700">版本状态</p>
            <p class="text-[11px] leading-relaxed text-slate-500">
              发布为系统级<strong class="text-slate-700">不会改变任何版本状态</strong>，
              运行时使用该智能体当前已发布的版本。
            </p>
          </div>

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
            class="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white hover:bg-primary-hover disabled:opacity-50"
            :disabled="submitting"
            @click="submit"
          >
            {{ submitting ? '提交中…' : isPublish ? '确认发布' : '提交申请' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
