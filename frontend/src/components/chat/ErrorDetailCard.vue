<script setup lang="ts">
import { MagnifyingGlassIcon } from "@heroicons/vue/24/outline";
import { ref } from "vue";
import { copyToClipboard } from "@/utils/clipboard";
import type { StreamErrorAIStatus } from "@/utils/streamErrorPresentation";

withDefaults(
  defineProps<{
    rawError: string;
    aiStatus?: StreamErrorAIStatus;
  }>(),
  {
    aiStatus: "disabled",
  },
);

const copied = ref(false);
const copyFailed = ref(false);

const handleCopy = async (rawError: string) => {
  const success = await copyToClipboard(rawError);
  copied.value = success;
  copyFailed.value = !success;
  window.setTimeout(() => {
    copied.value = false;
    copyFailed.value = false;
  }, 1800);
};
</script>

<template>
  <details
    v-if="rawError"
    class="mt-3 rounded-lg border border-gray-200/80 bg-gray-50/80 text-xs text-gray-500 dark:border-gray-700 dark:bg-gray-800/70 dark:text-gray-400"
  >
    <summary class="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2 font-medium transition-colors hover:text-primary focus:outline-none [&::-webkit-details-marker]:hidden">
      <span class="inline-flex items-center gap-1.5">
        <MagnifyingGlassIcon class="h-3.5 w-3.5 shrink-0 text-gray-400" aria-hidden="true" />
        查看技术详情
      </span>
      <span class="text-[10px] text-gray-400 dark:text-gray-500">可复制</span>
    </summary>
    <div class="border-t border-gray-200/80 px-3 pb-3 pt-2 dark:border-gray-700">
      <div class="mb-2 flex items-center justify-between gap-3">
        <span class="text-[10px] text-gray-400 dark:text-gray-500">已脱敏的原始错误</span>
        <button
          type="button"
          class="rounded-md border border-gray-200 bg-white px-2 py-1 text-[10px] font-semibold text-gray-500 transition-colors hover:border-primary/40 hover:text-primary dark:border-gray-600 dark:bg-gray-900 dark:text-gray-300"
          @click.prevent.stop="handleCopy(rawError)"
        >
          {{ copied ? '已复制' : copyFailed ? '复制失败' : '复制' }}
        </button>
      </div>
      <pre class="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md bg-gray-900/5 p-2 font-mono text-[11px] leading-relaxed text-gray-600 dark:bg-black/20 dark:text-gray-300">{{ rawError }}</pre>
    </div>
  </details>
</template>
