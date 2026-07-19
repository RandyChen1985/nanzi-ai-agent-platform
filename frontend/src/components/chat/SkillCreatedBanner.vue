<script setup lang="ts">
import type { SkillCreatedInfo } from '../../utils/skillCreated'
import { personalSkillsEditUrl } from '../../utils/skillCreated'

const props = defineProps<{
  info: SkillCreatedInfo
}>()

const emit = defineEmits<{
  (e: 'mount'): void
  (e: 'dismiss'): void
}>()

const openEditor = () => {
  const url = personalSkillsEditUrl(props.info.skill_id)
  window.open(url, '_blank', 'noopener,noreferrer')
}
</script>

<template>
  <div
    class="mb-2 flex flex-wrap items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50/90 px-3 py-2 text-xs text-emerald-900 shadow-sm dark:border-emerald-500/30 dark:bg-emerald-950/40 dark:text-emerald-100"
    role="status"
  >
    <span class="font-semibold">已创建个人技能「{{ info.name }}」</span>
    <span class="text-emerald-700/80 dark:text-emerald-200/70 font-mono">{{ info.skill_id }}</span>
    <div class="ml-auto flex items-center gap-2">
      <button
        type="button"
        class="rounded-lg bg-white px-2.5 py-1 font-medium text-emerald-700 shadow-sm ring-1 ring-emerald-200 hover:bg-emerald-50 dark:bg-emerald-900 dark:text-emerald-100 dark:ring-emerald-700"
        @click="openEditor"
      >
        去编辑
      </button>
      <button
        type="button"
        class="rounded-lg bg-emerald-600 px-2.5 py-1 font-medium text-white hover:bg-emerald-700"
        @click="emit('mount')"
      >
        立即挂载
      </button>
      <button
        type="button"
        class="rounded-lg px-2 py-1 text-emerald-700/70 hover:bg-emerald-100/80 dark:text-emerald-200/70 dark:hover:bg-emerald-900/60"
        aria-label="关闭"
        @click="emit('dismiss')"
      >
        ✕
      </button>
    </div>
  </div>
</template>
