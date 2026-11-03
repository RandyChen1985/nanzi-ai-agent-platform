<script setup lang="ts">
/**
 * 「这个智能体是团主专家」的输入框上方说明条。
 *
 * 用户选中的是团主专家时，他其实在跟「一个团」对话：主专家会把子任务派给已发布版本
 * 调度范围里的子专家。这件事在页头和列表里只有一个小徽标，进对话后很容易忘，于是
 * 在输入框上方补一条可关闭的说明——骨架与「仅检索已绑定的知识库」一致，只是换成
 * 团主专家的青绿色（与顶部 👥 TEAM 徽标同色系）。
 *
 * 为什么不用警示色：这是身份说明，不是异常。用黄色/红色会让人以为团队模式出了问题。
 */
import { computed } from 'vue'

const props = defineProps<{
  teamName?: string
  /** null = 未配置调度范围（全部可委派的都能调），0 = 显式清空，两者文案不同 */
  memberCount?: number | null
}>()

const emit = defineEmits<{
  (e: 'dismiss'): void
}>()

const title = computed(() => {
  const name = String(props.teamName || '').trim()
  return `${name || '专家团'} · 团队模式`
})

const detail = computed(() => {
  const count = props.memberCount
  if (count === null || count === undefined) {
    return '主专家会按需调度可委派的子专家；调度范围还没限定，可从全部可委派专家里选'
  }
  if (count <= 0) {
    return '这个团还没有子专家，主专家会自己处理全部问题'
  }
  return `主专家会按分工调度 ${count} 位子专家；想指定某位，在输入框里 @ 他`
})
</script>

<template>
  <div
    role="status"
    class="mb-2 flex items-start justify-between gap-2 rounded-xl border border-teal-200 bg-teal-50/90 px-3 py-2 text-xs text-teal-900 dark:border-teal-700/40 dark:bg-teal-950/40 dark:text-teal-100"
  >
    <div class="flex min-w-0 flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
      <span class="shrink-0 font-semibold text-teal-800 dark:text-teal-200">👥 {{ title }}</span>
      <span class="min-w-0 break-words text-teal-700/90 dark:text-teal-200/80">{{ detail }}</span>
    </div>
    <button
      type="button"
      class="shrink-0 rounded px-1 text-teal-600/90 hover:text-teal-700 dark:text-teal-300/80 dark:hover:text-teal-200"
      title="隐藏提示"
      @click="emit('dismiss')"
    >
      ×
    </button>
  </div>
</template>
