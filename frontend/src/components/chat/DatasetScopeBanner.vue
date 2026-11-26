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
