<script setup lang="ts">
/**
 * 申请访问弹窗。
 *
 * 理由选填：强制填写只会得到「工作需要」四个字，反而更没用；
 * 但给几个常见理由的快捷按钮，比空输入框更容易得到有效信息。
 *
 * 提交成功后由父组件刷新列表，卡片状态就地变成「审批中」——
 * 不要关掉弹窗就完事，用户会怀疑到底提交成功没有。
 */
import { ref, watch } from 'vue'

import { applyResourceAccess, resourceSquareErrorMessage } from '@/api/resourceSquare'

const props = defineProps<{
  visible: boolean
  resourceType: 'dataset' | 'metadata'
  resourceId: string
  resourceName: string
  resourceMeta?: string
}>()

const emit = defineEmits<{
  (event: 'close'): void
  (event: 'submitted', payload: { id: number }): void
}>()

const reason = ref('')
const submitting = ref(false)
const error = ref('')

const quickReasons = [
  '季度对账需要核对历史口径',
  '搭建经营分析看板需要该数据',
  '新接手业务，需要补齐背景资料',
]

watch(
  () => props.visible,
  (open) => {
    if (open) {
      reason.value = ''
      error.value = ''
      submitting.value = false
    }
  },
)

async function submit() {
  if (submitting.value) return
  submitting.value = true
  error.value = ''
  try {
    const result = await applyResourceAccess({
      resource_type: props.resourceType,
      resource_id: props.resourceId,
      reason: reason.value.trim() || undefined,
    })
    emit('submitted', { id: result.id })
    emit('close')
  } catch (err) {
    error.value = resourceSquareErrorMessage(err, '提交申请失败')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div
    v-if="visible"
    class="fixed inset-0 z-[80] flex items-start justify-center overflow-auto bg-slate-900/50 p-4 pt-16"
    @click.self="emit('close')"
  >
    <div
      class="w-full max-w-lg rounded-2xl border border-gray-100 bg-white shadow-2xl dark:border-gray-700 dark:bg-gray-800"
    >
      <div class="flex items-start justify-between gap-3 px-5 pt-5">
        <div>
          <h3 class="text-base font-semibold text-gray-900 dark:text-gray-100">申请访问</h3>
          <p class="mt-1 text-xs text-gray-500">提交后由管理员审批，通过时自动给你加上访问授权。</p>
        </div>
        <button
          type="button"
          class="shrink-0 text-xs font-medium text-gray-400 hover:text-gray-600"
          @click="emit('close')"
        >
          关闭
        </button>
      </div>

      <div class="px-5 pt-4">
        <div class="rounded-xl border border-gray-100 bg-gray-50 px-3 py-2.5 dark:border-gray-700 dark:bg-gray-900/40">
          <p class="text-sm font-semibold text-gray-900 dark:text-gray-100">{{ resourceName }}</p>
          <p class="mt-0.5 text-xs text-gray-500">
            {{ resourceMeta || (resourceType === 'metadata' ? '数据集 · 只读权限' : '知识库 · 申请通过后可检索') }}
          </p>
        </div>

        <label class="mt-4 block text-xs font-semibold text-gray-700 dark:text-gray-300">
          申请理由（可选，写清楚更容易通过）
        </label>
        <textarea
          v-model="reason"
          rows="3"
          class="mt-1.5 w-full resize-y rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/10 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-100"
          placeholder="例如：季度对账需要查 2024 年的报销制度"
        />
        <div class="mt-2 flex flex-wrap gap-1.5">
          <button
            v-for="item in quickReasons"
            :key="item"
            type="button"
            class="rounded-full border border-gray-200 px-2 py-0.5 text-[11px] text-gray-500 hover:border-primary hover:text-primary dark:border-gray-600 dark:text-gray-400"
            @click="reason = item"
          >
            {{ item }}
          </button>
        </div>
        <p class="mt-2 text-[11px] leading-5 text-gray-400">
          提交后管理员会在「用户管理 → 待审核」处理；进度可在个人中心「我的申请」查看。同一条申请正在等待审批时不能重复提交。
        </p>
        <p v-if="error" class="mt-2 text-xs text-red-500">{{ error }}</p>
      </div>

      <div class="mt-4 flex justify-end gap-2 border-t border-gray-100 px-5 py-3 dark:border-gray-700">
        <button
          type="button"
          class="rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300"
          @click="emit('close')"
        >
          取消
        </button>
        <button
          type="button"
          class="rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-dark disabled:opacity-60"
          :disabled="submitting"
          @click="submit"
        >
          {{ submitting ? '提交中…' : '提交申请' }}
        </button>
      </div>
    </div>
  </div>
</template>
