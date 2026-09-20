<template>
  <div class="h-full w-full flex flex-col items-center justify-center gap-2 text-center px-4">
    <svg class="h-10 w-10 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
      <path
        stroke-linecap="round"
        stroke-linejoin="round"
        stroke-width="1.5"
        d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
      />
    </svg>
    <p class="text-sm font-medium text-gray-500">{{ title }}</p>
    <p v-if="description" class="text-xs text-gray-400 max-w-xs">{{ description }}</p>
    <button
      type="button"
      class="mt-1 px-3 py-1.5 rounded-lg border border-gray-200 text-xs font-bold text-gray-600 hover:bg-gray-50 active:scale-95 transition-all"
      @click="goToKnowledgeBase"
    >
      去知识库管理
    </button>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'

defineProps<{
  title: string
  description?: string
}>()

const router = useRouter()
const goToKnowledgeBase = () => router.push('/dashboard/knowledge-bases')
</script>
