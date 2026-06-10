<script setup lang="ts">
import { computed } from "vue";
import { parseKnowledgeToolLog } from "@/utils/knowledgeToolLog";

const props = defineProps<{
  details: string;
}>();

const kbLog = computed(() => parseKnowledgeToolLog(props.details));
</script>

<template>
  <div v-if="kbLog" class="space-y-2 mb-1">
    <div
      v-if="kbLog.emptyMessage"
      class="text-xs text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-900/20 border border-amber-100 dark:border-amber-800/40 rounded p-2"
    >
      {{ kbLog.emptyMessage }}
    </div>
    <template v-else>
      <pre
        v-if="kbLog.citations.length === 0 && kbLog.summary"
        class="font-mono text-[10px] text-gray-600 dark:text-gray-300 whitespace-pre-wrap break-all leading-relaxed"
      >{{ kbLog.summary }}</pre>
      <template v-else>
        <p
          v-if="kbLog.summary"
          class="text-[10px] text-gray-500 dark:text-gray-400 whitespace-pre-wrap line-clamp-4"
        >{{ kbLog.summary }}</p>
        <div
          v-for="(ref, idx) in kbLog.citations"
          :key="idx"
          class="p-2 bg-blue-50/50 dark:bg-blue-900/20 rounded border border-blue-100/80 dark:border-blue-800/40 text-xs"
        >
          <div class="font-bold text-blue-700 dark:text-blue-300 mb-1 flex justify-between items-center gap-2">
            <div class="flex items-center space-x-1 min-w-0">
              <svg class="w-3 h-3 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
              <span class="truncate">{{ ref.doc_name || ref.document_name || "Unknown Document" }}</span>
            </div>
            <span v-if="ref.similarity" class="shrink-0 bg-white dark:bg-gray-800 px-1.5 py-0.5 rounded text-[10px] text-blue-600 dark:text-blue-300 font-medium border border-blue-100 dark:border-blue-800">
              {{ (ref.similarity * 100).toFixed(0) }}%
            </span>
          </div>
          <div class="text-gray-600 dark:text-gray-300 leading-relaxed whitespace-pre-wrap">{{ ref.content }}</div>
        </div>
      </template>
    </template>
  </div>
</template>
