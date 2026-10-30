<script setup lang="ts">
/**
 * 「当前智能体绑定了数据集」提示。
 *
 * 数据集绑定收窄的是**可见范围**（智能体绑定的集合 ∩ 用户自己的数据权限），
 * 不是「超出即拒绝」的硬隔离，所以文案只做信息告知，不承诺强制拦截。
 * 措辞上避免用「限定 / 仅限」，那会让人以为绑了就等于授权。
 */
import { computed } from 'vue'

const props = defineProps<{
  agentName?: string
  boundCount?: number
}>()

const emit = defineEmits<{
  (e: 'dismiss'): void
}>()

const title = '当前智能体已绑定数据集'

const detail = computed(() => {
  const name = String(props.agentName || '').trim()
  const count = Number(props.boundCount || 0)
  const scope = count > 0 ? `已绑定的 ${count} 个数据集` : '智能体绑定的数据集'
  const owner = name ? `当前智能体「${name}」` : '当前智能体'
  return `${owner}绑定了${scope}，查询范围不会超出这些数据集；具体能查到哪些表，仍取决于你自身的数据权限。`
})
</script>

<template>
  <div
    role="status"
    class="mb-2 flex items-start justify-between gap-2 rounded-xl border border-blue-200 bg-blue-50/90 px-3 py-2 text-xs text-blue-900 dark:border-blue-500/30 dark:bg-blue-950/40 dark:text-blue-100"
  >
    <div class="flex min-w-0 items-start gap-2">
      <span class="shrink-0 font-semibold">📊 {{ title }}</span>
      <span class="break-words text-blue-800/90 dark:text-blue-200/80">{{ detail }}</span>
    </div>
    <button
      type="button"
      class="shrink-0 rounded px-1 text-blue-600/90 hover:text-blue-700 dark:text-blue-300/80 dark:hover:text-blue-200"
      title="隐藏提示"
      @click="emit('dismiss')"
    >
      ×
    </button>
  </div>
</template>
