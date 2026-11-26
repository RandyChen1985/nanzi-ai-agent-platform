<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  projectName: string
  resourceCount: number
  datasetCount?: number
  knowledgeBaseCount?: number
  skillCount?: number
  mcpCount?: number
}>()

defineEmits<{
  (event: 'manage'): void
}>()

const summaryParts = computed(() => {
  const parts: string[] = []
  const datasets = Number(props.datasetCount || 0)
  const kbs = Number(props.knowledgeBaseCount || 0)
  const skills = Number(props.skillCount || 0)
  const mcps = Number(props.mcpCount || 0)
  if (datasets > 0) parts.push(`数据集 ${datasets}`)
  if (kbs > 0) parts.push(`知识库 ${kbs}`)
  if (skills > 0) parts.push(`技能 ${skills} 个`)
  if (mcps > 0) parts.push(`MCP ${mcps} 个`)
  return parts
})
</script>

<template>
  <div class="flex-shrink-0 px-4 py-2 flex items-center gap-2 text-[11px] text-gray-500 dark:text-gray-400 border-b border-gray-100 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-800/20">
    <div class="flex items-center gap-2 shrink-0 min-w-0 flex-1">
      <span class="font-bold shrink-0">{{ projectName ? `${projectName}` : '会话固定资源' }}</span>
      <button
        type="button"
        class="px-2 py-1 rounded-full border border-gray-200 hover:border-primary hover:text-primary dark:border-gray-700 dark:hover:border-primary shrink-0"
        @click="$emit('manage')"
      >
        管理会话资源
      </button>
      <span v-if="resourceCount === 0" class="text-gray-400 truncate">未挂载，按默认权限自动使用</span>
      <button
        v-else
        type="button"
        class="min-w-0 truncate text-left text-gray-600 dark:text-gray-300 hover:text-primary"
        title="点击查看并管理已挂载资源详情"
        @click="$emit('manage')"
      >
        {{ summaryParts.join(' | ') }}
      </button>
    </div>
  </div>
</template>
