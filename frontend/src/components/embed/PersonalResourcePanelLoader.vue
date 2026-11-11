<script setup lang="ts">
/**
 * 「我的资源」弹层里各 Tab 面板的异步加载占位。
 *
 * 面板组件是按 Tab 懒加载的（DataPortalHome / SkillsManagement / TaskCenter … 都不小），
 * 首次切到某个 Tab 时模块还在下载。没有占位时内容区高度为 0，弹窗会先缩成一条再被撑开。
 * 这里用「撑满内容区 + 居中转圈」的占位，配合弹窗固定高度，切 Tab 时只看到加载态、不见尺寸跳动。
 */
withDefaults(defineProps<{ label?: string }>(), { label: '加载中…' })
</script>

<template>
  <div
    class="flex h-full w-full flex-col items-center justify-center gap-3 py-12"
    role="status"
    aria-live="polite"
  >
    <span class="h-7 w-7 animate-spin rounded-full border-2 border-primary/25 border-t-primary" />
    <p class="text-xs font-bold text-gray-400 dark:text-gray-500">{{ label }}</p>
  </div>
</template>
