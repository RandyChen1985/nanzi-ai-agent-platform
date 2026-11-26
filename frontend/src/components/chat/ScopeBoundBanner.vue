<script setup lang="ts">
/**
 * 「仅检索已绑定的知识库」状态提示。
 *
 * 这是智能体级的范围策略（engine_config.scope_gate = bound_only），用户在当前会话里
 * 无论选库还是挂附件都不会改变检索范围。如果不提示，用户会以为「我选了库却没有效果」，
 * 所以做成输入框上方的常驻说明，而不是错误发生后才解释。
 */
import { computed } from 'vue'

const props = defineProps<{
  agentName?: string
  boundCount?: number
}>()

const emit = defineEmits<{
  (e: 'dismiss'): void
}>()

const title = '仅检索已绑定的知识库'
const detail = computed(() => {
  const name = String(props.agentName || '').trim()
  const count = Number(props.boundCount || 0)
  const scope = count > 0 ? `已绑定的 ${count} 个知识库` : '智能体绑定的知识库'
  const owner = name ? `当前智能体「${name}」` : '当前智能体'
  return `${owner}已设置只检索${scope}，在输入框或知识库中心选择其他知识库不会生效：这类检索会被直接拒绝并提示范围无效。`
})
</script>

<template>
  <div
    role="status"
    class="mb-2 flex items-start justify-between gap-2 rounded-xl border border-primary/20 bg-primary/5 px-3 py-2 text-xs text-primary-dark dark:border-primary/30 dark:bg-primary-dark/40 dark:text-primary-bright"
  >
    <div class="flex min-w-0 items-start gap-2">
      <span class="shrink-0 font-semibold">{{ title }}</span>
      <span class="break-words text-primary-dark/90 dark:text-primary-hover">{{ detail }}</span>
    </div>
    <button
      type="button"
      class="shrink-0 rounded px-1 text-primary/90 hover:text-primary-dark dark:text-primary-hover dark:hover:text-primary-hover"
      title="隐藏提示"
      @click="emit('dismiss')"
    >
      ×
    </button>
  </div>
</template>
